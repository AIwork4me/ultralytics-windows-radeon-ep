"""Unified runtime policy + evidence gates (R6-B).

All tests are CPU-only: they drive the policy engine with an injected fake
executor, so every refusal rule is deterministic and hardware-independent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from radeon_ort import unified_runtime as ur


def _parity(**over):
    out = {"name": "out0", "shape": [1, 3], "dtype": "float32",
           "allclose": True, "finite": True, "max_abs_diff": 1e-6}
    out.update(over)
    return {"by_name_matching": True, "all_outputs_allclose": True,
            "outputs": [out]}


def _attempt(ep: str, **over) -> ur.Attempt:
    base = {
        "ep": ep,
        "ok": True,
        "status": "FULL",
        "provider": ("AMDGPUExecutionProvider" if ep == "amdgpu"
                     else "DmlExecutionProvider"),
        "provider_node_counts": ({"AMDGPU": 5} if ep == "amdgpu"
                                 else {"DmlExecutionProvider": 5}),
        "target_nodes": 5,
        "cpu_nodes": 0,
        "parity": _parity(),
        "binding_confidence": (ur.CONFIRMED if ep == "amdgpu"
                               else ur.ORDINAL_MAPPING),
        "provider_list": (["AMDGPUExecutionProvider"] if ep == "amdgpu"
                          else ["DmlExecutionProvider"]),
        "outputs": {"out0": np.zeros((1, 3), dtype=np.float32)},
    }
    base.update(over)
    return ur.Attempt(**base)


class FakeExecutor:
    def __init__(self, behaviour: dict):
        self.behaviour = behaviour
        self.calls: list[str] = []

    def __call__(self, *, ep, model_path, inputs, reference=None):
        self.calls.append(ep)
        b = self.behaviour[ep]
        if callable(b):
            return b()
        return b


@pytest.fixture()
def model(tmp_path: Path) -> Path:
    p = tmp_path / "model.onnx"
    p.write_bytes(b"not-a-real-onnx")
    return p


def _inputs():
    return {"images": np.zeros((1, 3, 8, 8), dtype=np.float32)}


def _predict(model, executor, tmp_path, **kw):
    args = {"policy": "require_amdgpu", "target_gfx": "gfx1151",
            "work_dir": tmp_path, "executor": executor, "run_id": "RUN_TEST"}
    args.update(kw)
    return ur.predict(model_path=model, inputs=_inputs(), **args)


def test_require_amdgpu_success_reports_full_evidence(model, tmp_path):
    ex = FakeExecutor({"amdgpu": _attempt("amdgpu")})
    r = _predict(model, ex, tmp_path)
    assert r["runtime_status"] == ur.GPU_FULL
    assert r["gpu_full"] is True and r["task_verified"] is False
    assert r["attempted_eps"] == ["amdgpu"]
    assert r["device_binding_confidence"] == ur.CONFIRMED
    for f in ur.REQUIRED_RESULT_FIELDS:
        assert f in r, f
    assert len(r["receipt_sha256"]) == 64
    assert ur.verify_run_result(r)["verdict"] == "RUNRESULT_VALID"


def test_require_amdgpu_never_falls_back_to_dml(model, tmp_path):
    ex = FakeExecutor({"amdgpu": ur.Attempt(ep="amdgpu", status="BLOCKED",
                                            error="BLOCKED_EP")})
    r = _predict(model, ex, tmp_path)
    assert r["runtime_status"] == ur.BLOCKED
    assert r["attempted_eps"] == ["amdgpu"]
    assert "dml" not in ex.calls


def test_prefer_policy_falls_back_to_dml_and_discloses_reason(model, tmp_path):
    ex = FakeExecutor({"amdgpu": ur.Attempt(ep="amdgpu", status="BLOCKED",
                                            error="BLOCKED_EP"),
                       "dml": _attempt("dml")})
    r = _predict(model, ex, tmp_path, policy="prefer_amdgpu_allow_dml")
    assert r["runtime_status"] == ur.GPU_FULL_DML
    assert r["actual_ep"] == "dml"
    assert r["attempted_eps"] == ["amdgpu", "dml"]
    assert "AMDGPU_FAILED" in r["fallback_reason"]
    assert r["device_binding_confidence"] == ur.ORDINAL_MAPPING


def test_gpu_first_without_consent_never_runs_cpu(model, tmp_path):
    ex = FakeExecutor({"amdgpu": ur.Attempt(ep="amdgpu", error="boom"),
                       "dml": ur.Attempt(ep="dml", error="nope")})
    r = _predict(model, ex, tmp_path, policy="gpu_first_allow_cpu")
    assert r["runtime_status"] == ur.BLOCKED
    assert r["attempted_eps"] == ["amdgpu", "dml"]
    assert "CPU_NOT_AUTHORIZED" in r["fallback_reason"]


def test_gpu_first_with_consent_labels_cpu_honestly(model, tmp_path):
    ex = FakeExecutor({"amdgpu": ur.Attempt(ep="amdgpu", error="boom"),
                       "dml": ur.Attempt(ep="dml", error="nope"),
                       "cpu": ur.Attempt(ep="cpu", ok=True, status="FULL",
                                         provider="CPUExecutionProvider",
                                         provider_list=["CPUExecutionProvider"],
                                         binding_confidence="CPU_HOST",
                                         target_nodes=0, cpu_nodes=9)})
    r = _predict(model, ex, tmp_path, policy="gpu_first_allow_cpu", allow_cpu=True)
    assert r["runtime_status"] == ur.CPU_AUTHORIZED
    assert r["gpu_full"] is False
    assert r["cpu_user_authorized"] is True
    assert ex.calls[-1] == "cpu"


def test_consent_flags_are_not_additive(model, tmp_path):
    ex = FakeExecutor({})
    with pytest.raises(ur.ContractError):
        _predict(model, ex, tmp_path, policy="require_amdgpu", allow_cpu=True)
    with pytest.raises(ur.ContractError):
        _predict(model, ex, tmp_path, policy="cpu_opt_in", allow_cpu=False)


def test_unsupported_gfx_is_refused(model, tmp_path):
    with pytest.raises(ur.ContractError, match="UNSUPPORTED_GFX_TARGET"):
        _predict(model, FakeExecutor({}), tmp_path, target_gfx="gfx9999")


def test_cpu_nodes_disqualify_gpu_full(model, tmp_path):
    ex = FakeExecutor({"amdgpu": _attempt("amdgpu", cpu_nodes=2),
                       "dml": _attempt("dml")})
    r = _predict(model, ex, tmp_path, policy="prefer_amdgpu_allow_dml")
    assert r["runtime_status"] == ur.GPU_FULL_DML
    assert "CPU_NODES_PRESENT" in r["fallback_reason"]


def test_zero_target_nodes_is_refused(model, tmp_path):
    ex = FakeExecutor({"amdgpu": _attempt("amdgpu", target_nodes=0),
                       "dml": _attempt("dml", target_nodes=0)})
    r = _predict(model, ex, tmp_path, policy="prefer_amdgpu_allow_dml")
    assert r["runtime_status"] == ur.BLOCKED
    assert "PROFILE_ZERO_NODES" in r["fallback_reason"]


def test_non_finite_parity_is_refused(model, tmp_path):
    ex = FakeExecutor({"amdgpu": _attempt("amdgpu", parity=_parity(finite=False)),
                       "dml": _attempt("dml", parity=_parity(finite=False))})
    r = _predict(model, ex, tmp_path, policy="prefer_amdgpu_allow_dml")
    assert r["runtime_status"] == ur.BLOCKED
    assert "PARITY_NON_FINITE" in r["fallback_reason"]


def test_empty_output_is_refused(model, tmp_path):
    ex = FakeExecutor({"amdgpu": _attempt("amdgpu", parity=_parity(shape=[1, 0]))})
    r = _predict(model, ex, tmp_path)
    assert r["runtime_status"] != ur.GPU_FULL
    assert "PARITY_EMPTY_OUTPUT" in r["fallback_reason"]


def test_arbitrary_binding_label_cannot_pass(model, tmp_path):
    ex = FakeExecutor({"amdgpu": _attempt("amdgpu",
                                          binding_confidence="TRUST_ME_BRO")})
    r = _predict(model, ex, tmp_path)
    assert r["runtime_status"] != ur.GPU_FULL
    assert "DEVICE_BINDING" in r["fallback_reason"]


def test_silent_cpu_session_is_refused(model, tmp_path):
    ex = FakeExecutor({"amdgpu": _attempt("amdgpu", provider="CPUExecutionProvider")})
    r = _predict(model, ex, tmp_path)
    assert r["runtime_status"] != ur.GPU_FULL
    assert "SILENT_CPU_FALLBACK" in r["fallback_reason"]


def test_input_fingerprint_binds_name_dtype_shape_and_bytes():
    a = {"images": np.zeros((1, 3, 4, 4), dtype=np.float32)}
    b = {"images": np.zeros((1, 3, 4, 4), dtype=np.float64)}
    c = {"images": np.zeros((1, 3, 4, 5), dtype=np.float32)}
    d = {"pixels": np.zeros((1, 3, 4, 4), dtype=np.float32)}
    e = {"images": np.ones((1, 3, 4, 4), dtype=np.float32)}
    shas = {ur.input_fingerprint(x)["combined_sha256"] for x in (a, b, c, d, e)}
    assert len(shas) == 5


def test_verifier_rejects_tampered_result(model, tmp_path):
    ex = FakeExecutor({"amdgpu": _attempt("amdgpu")})
    r = _predict(model, ex, tmp_path)
    tampered = dict(r, target_ep_profile_nodes=0)
    assert ur.verify_run_result(tampered)["verdict"] == "RUNRESULT_INVALID"


def test_attempt_trace_records_first_error(model, tmp_path):
    ex = FakeExecutor({"amdgpu": ur.Attempt(ep="amdgpu", status="BLOCKED",
                                            error="BLOCKED_EP: no catalog entry")})
    r = _predict(model, ex, tmp_path)
    assert r["attempts"][0]["error"] == "BLOCKED_EP: no catalog entry"
    assert r["attempts"][0]["ep"] == "amdgpu"
