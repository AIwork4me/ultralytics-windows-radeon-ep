"""Real RCA2A receipt and parity gate tests. CPU only; no ORT/GPU."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

from runner.rca2a_main import _validate_gpu_pass
from runner.rca2a_g4_stats import soak_gate, SOAK_MIN_EFFECTIVE_SECONDS
from radeon_ort.rca2a_schema import validate_receipt

E = Path(__file__).resolve().parents[2] / "reports/gfx1100/rca2a/RCA2A_20261118T1140Z"


def rec(name):
    return json.loads((E / "receipts" / (name + ".json")).read_text(encoding="utf-8"))


def valid(r, plan=None):
    return validate_receipt(r, plan=plan)["verdict"] == "RECEIPT_VALID"


@pytest.mark.parametrize("name", [
    "E0_BASELINE_yolo26n_rep0_pub2",
    "E4_MXR_CACHE_yolo26n_rep1_pub2",
    "E4_MXR_FRC_yolo26n_rep0_pub2",
    "E2_SKIP_BENCH_rtdetr-l_rep0_pub2",
    "SOAK_MXR_CACHE_yolo26n_rep1_pub2",
    "SOAK_MXR_CACHE_rtdetr-l_rep1_pub2",
])
def test_real_published_receipt_passes(name):
    assert valid(rec(name))


def test_published_dml_control_env_evidence_is_incomplete():
    # Historical DML GPU calculation was profiled, but its published receipt
    # declares %CACHE% without an echoed value. Never forge the env evidence.
    r = rec("E8_DML_yolo26n_rep3_pub2")
    assert r["gpu_validation"]["verdict"] == "GPU_PASS_VALIDATED"
    assert validate_receipt(r)["verdict"] == "RECEIPT_INVALID_ENV_NOT_ECHOED"


def test_frozen_plan_binds_model_and_input_bytes():
    r = rec("E0_BASELINE_yolo26n_rep0_pub2")
    plan = json.loads((E / "PUBCONF_PLAN_PUBLIC.json").read_text(encoding="utf-8"))
    assert valid(r, plan)
    for field in ("onnx_sha256", "input_sha256"):
        forged = copy.deepcopy(r)
        forged[field] = "f" * 64
        assert not valid(forged, plan)


@pytest.mark.parametrize("name", [
    "ep_device_probe_event", "luid_match_basis",
    "amdgpu_provider_present", "output_names_match",
    "supervisor_independent_parity", "profile_ep_nodes_positive",
])
def test_deleting_real_gpu_evidence_fails(name):
    r = rec("E4_MXR_CACHE_yolo26n_rep1_pub2")
    r["gpu_validation"]["checks"] = [
        c for c in r["gpu_validation"]["checks"] if c["check"] != name]
    assert not valid(r)


def test_flat_forged_pass_and_nan_diagnostics_fail():
    r = rec("E4_MXR_CACHE_yolo26n_rep1_pub2")
    r["gpu_validation"] = {
        "verdict": "GPU_PASS_VALIDATED",
        "profile_ep_nodes_positive": True,
        "supervisor_independent_parity": True}
    assert not valid(r)
    r = rec("E4_MXR_CACHE_yolo26n_rep1_pub2")
    r["gpu_validation"]["supervisor_parity_max_abs"] = float("nan")
    assert not valid(r)


def test_missing_session_create_phase_fails():
    r = rec("E4_MXR_CACHE_yolo26n_rep1_pub2")
    r["timeline"] = [
        t for t in r["timeline"] if t["phase"] != "SESSION_CREATE_BEGIN"]
    assert not valid(r)


def events():
    return [
        {"kind": "ep_device_selected", "selection_basis": "LUID match gfx1100"},
        {"kind": "session_created",
         "providers": ["AMDGPUExecutionProvider", "CPUExecutionProvider"]},
    ]


def check(tmp_path, ref, cand, *, profile=True):
    if cand is not None:
        np.savez(tmp_path / "outputs_first.npz", **cand)
    (tmp_path / "ort_profile_sess0.json").write_text(
        json.dumps([{"cat": "Node", "args": {
            "provider": "AMDGPUExecutionProvider"}}]), encoding="utf-8")
    return _validate_gpu_pass(
        tmp_path, {"reference_outputs": ref, "atol": 5e-3, "rtol": 5e-3},
        events(), 0, "amdgpu", profile)


def test_small_error_is_valid_per_element(tmp_path):
    ref = {"large": np.array([1000.], dtype=np.float32),
           "small": np.array([0.], dtype=np.float32)}
    cand = {"large": ref["large"], "small": np.array([0.004], dtype=np.float32)}
    assert check(tmp_path, ref, cand)["verdict"] == "GPU_PASS_VALIDATED"


def test_large_tensor_cannot_mask_wrong_small_output(tmp_path):
    ref = {"large": np.array([1000.], dtype=np.float32),
           "small": np.array([0.], dtype=np.float32)}
    cand = {"large": ref["large"], "small": np.array([1.], dtype=np.float32)}
    assert check(tmp_path, ref, cand)["verdict"] == "GPU_PASS_INVALIDATED"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_gpu_outputs_fail(tmp_path, bad):
    ref = {"x": np.array([0.], dtype=np.float32)}
    cand = {"x": np.array([bad], dtype=np.float32)}
    assert check(tmp_path, ref, cand)["verdict"] == "GPU_PASS_INVALIDATED"


def test_empty_reference_and_disabled_profiling_fail(tmp_path):
    cand = {"x": np.array([1.], dtype=np.float32)}
    assert check(tmp_path, {}, cand)["verdict"] == "GPU_PASS_INVALIDATED"
    assert check(tmp_path, {"x": cand["x"]}, cand, profile=False)["verdict"] == \
        "GPU_PASS_INVALIDATED"


def test_missing_named_or_zero_size_output_fails(tmp_path):
    ref = {"a": np.array([0.], dtype=np.float32),
           "b": np.array([0.], dtype=np.float32)}
    assert check(tmp_path, ref, {"a": ref["a"]})["verdict"] == "GPU_PASS_INVALIDATED"
    assert check(tmp_path, {"a": ref["a"]}, {
        "a": np.array([], dtype=np.float32)})["verdict"] == "GPU_PASS_INVALIDATED"


@pytest.mark.parametrize("duration,expected", [
    (869, False), (899, False), (900, True), (901, True),
])
def test_soak_boundary(duration, expected):
    r = rec("SOAK_MXR_CACHE_yolo26n_rep1_pub2")
    r["phases"]["steady_effective_s"] = float(duration)
    assert soak_gate(r) is expected
    assert SOAK_MIN_EFFECTIVE_SECONDS == 900.0


def test_soak_needs_gpu_proof_and_recreate():
    r = rec("SOAK_MXR_CACHE_yolo26n_rep1_pub2")
    assert soak_gate(r)
    r["gpu_validation"]["verdict"] = "GPU_PASS_INVALIDATED"
    assert not soak_gate(r)
    r = rec("SOAK_MXR_CACHE_yolo26n_rep1_pub2")
    r["phases"]["recreates"] = []
    assert not soak_gate(r)
