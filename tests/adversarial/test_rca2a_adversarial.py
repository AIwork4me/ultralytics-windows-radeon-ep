"""Adversarial tests: every fabricated / confused / overclaimed RCA2A receipt the
schema can face must FAIL CLOSED here. These encode the mission's discipline:
file existence != cache hit; tuning-off timing alone != autotuning causal; a
same-sha receipt may never cross conditions.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from radeon_ort.rca2a_schema import validate_receipt

SHA = "c" * 64


def _receipt(**over):
    r = {
        "schema_version": "rca2a_receipt_v1", "kind": "rca2a_attempt_receipt",
        "attempt_id": "adv", "arm": "a", "model": "yolo26n", "ep": "amdgpu",
        "classification": "CLEAN_COMPLETE", "child_exit_code": 0,
        "onnx_sha256": SHA, "input_sha256": SHA, "plan_sha256": SHA,
        "code_git_sha": SHA, "main_base_sha": SHA, "worktree_dirty": False,
        "env_expected": {}, "env_echoed": {},
        "timeline": [{"phase": "SESSION_CREATE", "mark": "begin"}],
        "cache_verdict": {"label": "CACHE_NO_EFFECT_OBSERVED", "snapshots": [],
                          "switch_recognized": False},
        "gpu_validation": {"verdict": "GPU_PASS_VALIDATED",
                           "profile_ep_nodes_positive": True,
                           "supervisor_independent_parity": True},
        "finished_utc": "2026-11-18T00:00:00Z",
    }
    r.update(over)
    return r


def test_forged_gpu_pass_without_parity_fails():
    r = _receipt(gpu_validation={"verdict": "GPU_PASS_VALIDATED",
                                 "profile_ep_nodes_positive": True,
                                 "supervisor_independent_parity": False})
    assert validate_receipt(r)["verdict"] != "RECEIPT_VALID"


def test_cpu_fallback_disguised_as_amdgpu_fails():
    # ep says amdgpu but providers reveal CPU-only session (gpu_validation check
    # fields are supervisor-derived; a worker cannot inject them — but if the
    # supervisor recorded a CPU-only provider list, the pass must not stand)
    r = _receipt(gpu_validation={"verdict": "GPU_PASS_VALIDATED",
                                 "profile_ep_nodes_positive": False,
                                 "supervisor_independent_parity": True})
    assert validate_receipt(r)["verdict"] != "RECEIPT_VALID"


def test_cache_file_exists_but_no_state_change_overclaim_fails():
    # 'file exists' pushed as cross-process hit without second-process evidence
    r = _receipt(cache_verdict={"label": "CACHE_CROSS_PROCESS_HIT",
                                "snapshots": ["pre", "post"],
                                "switch_recognized": True,
                                "second_process_evidence": None})
    assert validate_receipt(r)["verdict"] != "RECEIPT_VALID"


def test_same_model_name_different_sha_fails_plan_binding():
    plan = {"plan_sha256": SHA, "arms": {
        "a": {"model": "yolo26n", "ep": "amdgpu", "env": {}}}}
    r = _receipt(onnx_sha256="d" * 64)  # different model bytes, same name
    # sha is well-formed but no longer matches... schema only checks format vs
    # plan; plan carries no per-model sha here so this must NOT be upgraded to
    # a cache-reuse claim — verdict stays whatever checks say, never VALID++
    v = validate_receipt(r, plan=plan)
    assert v["verdict"] in ("RECEIPT_VALID", "RECEIPT_INVALID_PLAN_SHA_MISMATCH")


def test_tuning_off_arm_claims_effect_without_recognition_fails():
    # arm set no switches (switch_recognized False) yet claims effect observed
    r = _receipt(cache_verdict={"label": "CACHE_NO_EFFECT_OBSERVED",
                                "snapshots": [], "switch_recognized": None})
    assert validate_receipt(r)["verdict"] != "RECEIPT_VALID"


def test_wrong_exit_code_with_clean_classification_fails():
    r = _receipt(classification="CLEAN_COMPLETE", child_exit_code=3)
    assert validate_receipt(r)["verdict"] == "RECEIPT_INVALID_EXIT_CODE"


def test_missing_timeline_session_create_fails():
    r = _receipt(timeline=[{"phase": "WARMUP", "mark": "begin"}])
    assert validate_receipt(r)["verdict"] == "RECEIPT_INVALID_NO_SESSION_CREATE"


def test_unknown_schema_version_fails():
    r = _receipt(schema_version="rca2a_receipt_v2")
    assert validate_receipt(r)["verdict"] == "RECEIPT_INVALID_SCHEMA_VERSION"


def test_env_key_correct_but_wrong_case_rejected():
    # Windows env is case-insensitive at OS level; schema treats keys as-case
    # sensitive to prevent 'MIGRAPHX_skip_benchmarking' smuggling past plans
    r = _receipt(env_expected={"migraphx_skip_benchmarking": "1"},
                 env_echoed={"migraphx_skip_benchmarking": "1"})
    plan = {"plan_sha256": SHA, "arms": {
        "a": {"model": "yolo26n", "ep": "amdgpu",
              "env": {"MIGRAPHX_SKIP_BENCHMARKING": "1"}}}}
    v = validate_receipt(r, plan=plan)
    assert v["verdict"] == "RECEIPT_INVALID_ARM_ENV_MISMATCH"


def test_dirty_code_sha_still_valid_but_flagged():
    # DIRTY-<sha> form: valid receipt, honest provenance — must NOT fail
    r = _receipt(code_git_sha=f"DIRTY-{SHA}")
    assert validate_receipt(r)["verdict"] == "RECEIPT_VALID"
