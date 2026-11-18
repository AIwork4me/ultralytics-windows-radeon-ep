"""Unit tests for RCA2A schema / cache key / metrics (pure CPU, no GPU needed)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from radeon_ort.rca2a_cache_key import (compute_cache_key, describe_invalidation,
                                        input_signature, normalize_flags)
from radeon_ort.rca2a_metrics import (bootstrap_ci, paired_improvement, percentile,
                                      steady_regression_verdict, summarize)
from radeon_ort.rca2a_schema import validate_receipt

SHA = "a" * 64
SHA2 = "b" * 64


def _base_receipt(**over):
    r = {
        "schema_version": "rca2a_receipt_v1", "kind": "rca2a_attempt_receipt",
        "attempt_id": "t1", "arm": "baseline", "model": "yolo26n", "ep": "amdgpu",
        "classification": "CLEAN_COMPLETE", "child_exit_code": 0,
        "onnx_sha256": SHA, "input_sha256": SHA, "plan_sha256": SHA,
        "code_git_sha": SHA, "main_base_sha": SHA, "worktree_dirty": False,
        "env_expected": {}, "env_echoed": {},
        "timeline": [{"phase": "EP_DISCOVERY", "mark": "begin"},
                     {"phase": "SESSION_CREATE", "mark": "begin"}],
        "cache_verdict": {"label": "CACHE_NO_EFFECT_OBSERVED", "snapshots": [],
                          "switch_recognized": False},
        "gpu_validation": {"verdict": "GPU_PASS_VALIDATED",
                           "profile_ep_node_count": 2,
                           "supervisor_parity_max_abs": 0.0,
                           "checks": [{"check": name, "ok": True} for name in (
                               "ep_device_probe_event", "luid_match_basis",
                               "amdgpu_provider_present", "output_names_match",
                               "supervisor_independent_parity",
                               "profile_ep_nodes_positive")]},
        "finished_utc": "2026-11-18T00:00:00Z",
    }
    r.update(over)
    return r


class TestSchema:
    def test_valid_minimal(self):
        v = validate_receipt(_base_receipt())
        assert v["verdict"] == "RECEIPT_VALID", v

    def test_incomplete_sha_rejected(self):
        r = _base_receipt(onnx_sha256="deadbeef")
        assert validate_receipt(r)["verdict"] == "RECEIPT_INVALID_SHA_ONNX_SHA256"

    def test_env_not_echoed_rejected(self):
        r = _base_receipt(env_expected={"MIGRAPHX_SKIP_BENCHMARKING": "1"},
                          env_echoed={})
        assert validate_receipt(r)["verdict"] == "RECEIPT_INVALID_ENV_NOT_ECHOED"

    def test_env_value_mismatch_rejected(self):
        r = _base_receipt(env_expected={"A": "1"}, env_echoed={"A": "2"})
        assert validate_receipt(r)["verdict"] == "RECEIPT_INVALID_ENV_NOT_ECHOED"

    def test_undeclared_env_key_rejected(self):
        # a knob the plan never declared cannot sneak into the echo
        r = _base_receipt(env_expected={}, env_echoed={"WEIRD_KNOB": "1"})
        assert validate_receipt(r)["verdict"] == "RECEIPT_INVALID_ENV_UNDECLARED_KEYS"

    def test_same_sha_cross_condition_mixing_rejected(self):
        # same plan sha but receipt claims an arm whose env differs from the plan
        plan = {"plan_sha256": SHA, "arms": {
            "baseline": {"models": ["yolo26n"], "ep": "amdgpu", "env": {}},
            "skip": {"models": ["yolo26n"], "ep": "amdgpu",
                     "env": {"MIGRAPHX_SKIP_BENCHMARKING": "1"}}},
                "cases": {"yolo26n": {"onnx_sha256": SHA, "input_sha256": SHA}}}
        r = _base_receipt(arm="skip", env_expected={})  # claims skip, no env
        assert validate_receipt(r, plan=plan)["verdict"] == \
            "RECEIPT_INVALID_ARM_ENV_MISMATCH"

    def test_timeline_order_enforced(self):
        r = _base_receipt(timeline=[{"phase": "STEADY_RUN", "mark": "begin"},
                                    {"phase": "EP_DISCOVERY", "mark": "begin"}])
        assert validate_receipt(r)["verdict"] == "RECEIPT_INVALID_TIMELINE_ORDER"

    def test_cache_hit_claim_without_evidence_rejected(self):
        # both orderings fail closed: snapshots<2 fires first, evidence check second
        r = _base_receipt(cache_verdict={"label": "CACHE_CROSS_PROCESS_HIT",
                                         "snapshots": [], "switch_recognized": True,
                                         "second_process_evidence": None})
        v = validate_receipt(r)["verdict"]
        assert v in ("RECEIPT_INVALID_CACHE_CLAIM_WITHOUT_SNAPSHOTS",
                     "RECEIPT_INVALID_CROSS_PROCESS_CLAIM_WITHOUT_EVIDENCE")
        r2 = _base_receipt(cache_verdict={"label": "CACHE_CROSS_PROCESS_HIT",
                                          "snapshots": ["a", "b"],
                                          "switch_recognized": True,
                                          "second_process_evidence": None})
        assert validate_receipt(r2)["verdict"] == \
            "RECEIPT_INVALID_CROSS_PROCESS_CLAIM_WITHOUT_EVIDENCE"

    def test_cache_changed_claim_needs_two_snapshots(self):
        r = _base_receipt(cache_verdict={"label": "CACHE_FILE_STATE_CHANGED",
                                         "snapshots": ["pre_session"],
                                         "switch_recognized": True})
        assert validate_receipt(r)["verdict"] == \
            "RECEIPT_INVALID_CACHE_CLAIM_WITHOUT_SNAPSHOTS"

    def test_gpu_pass_without_profile_nodes_rejected(self):
        gv = _base_receipt()["gpu_validation"]
        gv["profile_ep_node_count"] = 0
        gv["checks"] = [dict(c, ok=False) if c["check"] == "profile_ep_nodes_positive"
                        else c for c in gv["checks"]]
        r = _base_receipt(gpu_validation=gv)
        assert validate_receipt(r)["verdict"] == \
            "RECEIPT_INVALID_GPU_PASS_WITHOUT_PROFILE_NODES"

    def test_missing_runtime_env_reject_when_expected(self):
        # tuning-off arm must not be presented as tuning-on
        r = _base_receipt(env_expected={"ORT_MIGRAPHX_EXHAUSTIVE_TUNE": "1"},
                          env_echoed={"ORT_MIGRAPHX_EXHAUSTIVE_TUNE": "1"},
                          arm="baseline")
        plan = {"plan_sha256": SHA, "arms": {
            "baseline": {"models": ["yolo26n"], "ep": "amdgpu", "env": {}}},
                "cases": {"yolo26n": {"onnx_sha256": SHA, "input_sha256": SHA}}}
        assert validate_receipt(r, plan=plan)["verdict"] == \
            "RECEIPT_INVALID_ARM_ENV_MISMATCH"


class TestCacheKey:
    def _fields(self, **over):
        f = {"model_sha256": SHA, "input_signature": "images:float32:[1,3,640,640]",
             "ep_dll_sha256": SHA, "mgx_dll_sha256": SHA, "driver_version": "32.0.21030.2001",
             "gpu_arch": "gfx1100", "precision": "fp32", "ep_version": "1.8.65.0",
             "ort_version": "1.25.2", "effective_flags": {"a": "1"}}
        f.update(over)
        return f

    def test_deterministic(self):
        assert compute_cache_key(**self._fields()) == compute_cache_key(**self._fields())

    def test_arch_change_invalidates(self):
        k1 = compute_cache_key(**self._fields())
        k2 = compute_cache_key(**self._fields(gpu_arch="gfx1151"))
        assert k1 != k2

    def test_flag_order_irrelevant(self):
        k1 = compute_cache_key(**self._fields(effective_flags={"a": "1", "b": "2"}))
        k2 = compute_cache_key(**self._fields(effective_flags={"b": "2", "a": "1"}))
        assert k1 == k2

    def test_flag_value_change_invalidates(self):
        k1 = compute_cache_key(**self._fields())
        k2 = compute_cache_key(**self._fields(effective_flags={"a": "2"}))
        assert k1 != k2

    def test_missing_field_fails_closed(self):
        with pytest.raises(KeyError):
            compute_cache_key(**{k: v for k, v in self._fields().items()
                                 if k != "driver_version"})

    def test_bad_arch_rejected(self):
        with pytest.raises(ValueError):
            compute_cache_key(**self._fields(gpu_arch="cuda1234"))

    def test_invalidation_describes_changed_field(self):
        old = self._fields()
        new = self._fields(model_sha256=SHA2)
        d = describe_invalidation(compute_cache_key(**old), compute_cache_key(**new),
                                  old, new)
        assert d["changed_fields"].keys() == {"model_sha256"}
        assert not d["keys_equal"]

    def test_normalize_flags_drops_none(self):
        assert normalize_flags({"a": None, "b": 1}) == {"b": "1"}

    def test_input_signature(self):
        import numpy as np
        sig = input_signature({"images": np.zeros((1, 3, 8, 8), np.float32)})
        assert sig == "images:float32:[1, 3, 8, 8]"


class TestMetrics:
    def test_percentile_simple(self):
        assert percentile([1, 2, 3, 4, 5], 0.5) == 3

    def test_summarize_fields(self):
        s = summarize([1.0] * 10)
        assert s["n"] == 10 and s["p50"] == 1.0

    def test_bootstrap_ci_deterministic(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        a = bootstrap_ci(xs)
        b = bootstrap_ci(xs)
        assert a["ci95"] == b["ci95"]

    def test_bootstrap_low_power_flag(self):
        assert bootstrap_ci([1.0, 2.0])["low_power"] is True

    def test_paired_improvement_positive_when_faster(self):
        # halving latency = 50% improvement (candidate 5 vs baseline 10)
        r = paired_improvement([10.0] * 6, [5.0] * 6)
        assert r["median_improvement_pct"] == 50.0

    def test_steady_regression_gate(self):
        ok = steady_regression_verdict({"p50": 10, "p95": 20}, {"p50": 10, "p95": 20})
        assert ok["verdict"] == "STEADY_NOT_REGRESSED"
        bad = steady_regression_verdict({"p50": 10, "p95": 20}, {"p50": 12, "p95": 20})
        assert bad["verdict"] == "STEADY_REGRESSED"
        edge = steady_regression_verdict({"p50": 10, "p95": 20}, {"p50": 10.4, "p95": 21})
        assert edge["verdict"] == "STEADY_NOT_REGRESSED"  # <=5% allowed
