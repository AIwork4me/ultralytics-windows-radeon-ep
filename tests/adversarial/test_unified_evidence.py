"""R7-D adversarial: the verifier must not believe the producer.

Each case starts from a genuine, worker-produced RunResult (the stub worker is
the same contract as the real one) and then attacks exactly one field. The
expected verdict is always INVALID - a forged or inconsistent claim can never
survive an independent re-check.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from radeon_ort import unified_runtime as ur

FAKE = "tests.fixtures.fake_ep_worker"


def _inputs():
    return {"images": np.zeros((1, 3, 8, 8), dtype=np.float32)}


@pytest.fixture()
def good(tmp_path):
    m = tmp_path / "m.onnx"
    m.write_bytes(b"onnx-bytes")
    work = tmp_path / "work"
    r = ur.predict(model_path=m, inputs=_inputs(),
                   policy="prefer_amdgpu_allow_dml", target_gfx="gfx1151",
                   work_dir=work, run_id="RUN_EV", worker_module=FAKE,
                   task="detect", verify_task=False, timeout_s=30)
    return r, work, m


def _verify(r, work, *, model=None, inputs=None, deep=False):
    return ur.verify_run_result(r, model_path=model, inputs=inputs,
                                work_dir=work, deep=deep)


def test_baseline_result_is_valid(good):
    r, work, m = good
    assert r["runtime_status"] == ur.GPU_FULL_DML
    assert r["gpu_full"] is True and r["task_verified"] is False
    assert _verify(r, work)["verdict"] == "RUNRESULT_VALID"
    assert _verify(r, work, model=m, inputs=_inputs(), deep=True)["verdict"] == "RUNRESULT_VALID"


def test_task_verified_without_pass_is_invalid(good):
    r, work, _ = good
    r["task_verified"] = True          # producer asserts it
    v = _verify(r, work)
    assert v["verdict"] == "RUNRESULT_INVALID"
    assert "task_flag_consistent_with_status" in v["failed"]


def test_pass_task_status_without_flag_is_invalid(good):
    r, work, _ = good
    r["task_parity"] = {"status": "PASS_TASK", "passed": True, "metrics": {"m": 1},
                        "thresholds": {"t": 1}, "binding": {"onnx_sha256": "x",
                                                            "pt_reference_sha256": "y",
                                                            "input_tensor_sha256": "z"}}
    v = _verify(r, work)
    assert v["verdict"] == "RUNRESULT_INVALID"
    assert "task_pass_matches_flag" in v["failed"]


def test_pass_task_without_metrics_is_invalid(good):
    r, work, _ = good
    r["task_parity"] = {"status": "PASS_TASK", "passed": True, "metrics": {},
                        "thresholds": {"t": 1},
                        "binding": {"onnx_sha256": "x", "pt_reference_sha256": "y",
                                    "input_tensor_sha256": "z"}}
    r["task_verified"] = True
    assert _verify(r, work)["verdict"] == "RUNRESULT_INVALID"


def test_pass_task_without_binding_is_invalid(good):
    r, work, _ = good
    r["task_parity"] = {"status": "PASS_TASK", "passed": True, "metrics": {"m": 1},
                        "thresholds": {"t": 1}, "binding": {}}
    r["task_verified"] = True
    v = _verify(r, work)
    assert v["verdict"] == "RUNRESULT_INVALID"
    assert "task_pass_binds_onnx" in v["failed"]


def test_blocked_oracle_cannot_claim_verified(good):
    r, work, _ = good
    r["task_parity"] = {"status": "TASK_ORACLE_BLOCKED", "passed": False,
                        "reason_code": "TASK_ORACLE_REFERENCE_MISSING"}
    r["task_verified"] = True
    assert _verify(r, work)["verdict"] == "RUNRESULT_INVALID"


def test_unknown_task_status_is_invalid(good):
    r, work, _ = good
    r["task_parity"] = {"status": "PROBABLY_FINE", "passed": True}
    assert _verify(r, work)["verdict"] == "RUNRESULT_INVALID"


def test_arbitrary_binding_label_is_invalid(good):
    r, work, _ = good
    r["device_binding_confidence"] = "TRUST_ME"
    assert _verify(r, work)["verdict"] == "RUNRESULT_INVALID"


def test_tampered_receipt_file_is_detected(good):
    r, work, _ = good
    path = work / r["receipt"]["path"]
    data = json.loads(path.read_text(encoding="utf-8"))
    data["runtime_status"] = "GPU_FULL"
    path.write_text(json.dumps(data), encoding="utf-8")
    v = _verify(r, work)
    assert v["verdict"] == "RUNRESULT_INVALID"
    assert "receipt_file_sha_matches" in v["failed"]


def test_edited_receipt_body_is_detected(good):
    r, work, _ = good
    path = work / r["receipt"]["path"]
    data = json.loads(path.read_text(encoding="utf-8"))
    data["target_ep_profile_nodes"] = 99
    # rewrite keeping the declared file sha so only the body check can catch it
    text = json.dumps(data, sort_keys=True)
    path.write_bytes((text + chr(10)).encode("utf-8"))
    import hashlib
    r["receipt"]["file_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    v = _verify(r, work)
    assert v["verdict"] == "RUNRESULT_INVALID"
    assert "receipt_body_sha_matches" in v["failed"]


def test_missing_receipt_file_is_invalid(good):
    r, work, _ = good
    (work / r["receipt"]["path"]).unlink()
    assert _verify(r, work)["verdict"] == "RUNRESULT_INVALID"


def test_attempt_with_survivors_is_invalid(good):
    r, work, _ = good
    for a in r["attempts"]:
        if a["ep"] == r["actual_ep"]:
            a["process"]["survivors"] = [1234]
    v = _verify(r, work)
    assert v["verdict"] == "RUNRESULT_INVALID"
    assert any("survivors" in f for f in v["failed"])


def test_adopted_attempt_without_outputs_hash_is_invalid(good):
    r, work, _ = good
    for a in r["attempts"]:
        if a["ep"] == r["actual_ep"]:
            a["outputs_sha256"] = None
    v = _verify(r, work)
    assert v["verdict"] == "RUNRESULT_INVALID"
    assert "adopted_outputs_hashed" in v["failed"]


def test_mismatched_fragment_is_invalid(good):
    r, work, _ = good
    for a in r["attempts"]:
        if a["ep"] == r["actual_ep"]:
            a["fragment_matches_attempt"] = False
    assert _verify(r, work)["verdict"] == "RUNRESULT_INVALID"


def test_broken_input_binding_is_invalid(good):
    r, work, _ = good
    for a in r["attempts"]:
        if a["ep"] == r["actual_ep"]:
            a["input_binding"] = {"ok": False}
    v = _verify(r, work)
    assert v["verdict"] == "RUNRESULT_INVALID"
    assert "adopted_input_binding" in v["failed"]


def test_forged_adopted_attempt_id_is_invalid(good):
    r, work, _ = good
    r["adopted_attempt_id"] = "A999-forged"
    v = _verify(r, work)
    assert v["verdict"] == "RUNRESULT_INVALID"
    assert "adopted_attempt_in_trace" in v["failed"]


def test_product_ready_without_gpu_is_invalid(good):
    r, work, _ = good
    r["gpu_full"] = False
    r["runtime_status"] = ur.BLOCKED
    r["readiness"] = {"gpu_full": False, "task_verified": False,
                      "evidence_verified": True, "product_ready": True}
    v = _verify(r, work)
    assert v["verdict"] == "RUNRESULT_INVALID"
    assert "readiness_product_needs_both" in v["failed"]


def test_cpu_business_result_needs_consent(good):
    r, work, _ = good
    r["actual_ep"] = "cpu"
    r["runtime_status"] = ur.CPU_AUTHORIZED
    r["cpu_user_authorized"] = False
    r["gpu_full"] = False
    assert _verify(r, work)["verdict"] == "RUNRESULT_INVALID"


def test_deep_verification_without_raw_evidence_is_unverified(good):
    r, work, _ = good
    v = _verify(r, work, deep=True)
    assert v["verdict"] == "RUNRESULT_UNVERIFIED"
    assert "model_sha_recomputed" in v["unverified"]


def test_model_sha_mismatch_is_invalid(good, tmp_path):
    r, work, _ = good
    other = tmp_path / "other.onnx"
    other.write_bytes(b"a different model")
    v = _verify(r, work, model=other)
    assert v["verdict"] == "RUNRESULT_INVALID"
    assert "model_sha_recomputed" in v["failed"]


def test_input_sha_mismatch_is_invalid(good):
    r, work, _ = good
    v = _verify(r, work, inputs={"images": np.ones((1, 3, 8, 8), dtype=np.float32)})
    assert v["verdict"] == "RUNRESULT_INVALID"
    assert "input_sha_recomputed" in v["failed"]


def test_zero_target_nodes_is_invalid(good):
    r, work, _ = good
    r["target_ep_profile_nodes"] = 0
    assert _verify(r, work)["verdict"] == "RUNRESULT_INVALID"


def test_cpu_nodes_present_is_invalid(good):
    r, work, _ = good
    r["cpu_profile_nodes"] = 3
    assert _verify(r, work)["verdict"] == "RUNRESULT_INVALID"
