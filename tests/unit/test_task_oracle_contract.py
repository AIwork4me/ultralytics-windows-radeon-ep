"""R7-A: task-oracle contract - the blocked paths must never become a PASS.

The oracle itself needs torch+ultralytics, so these tests drive the *contract*:
what predict() does when the reference is missing, incompatible, or not
requested. The real oracle run on hardware is covered by the R7 evidence under
reports/mission_r7/.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from radeon_ort import task_oracle_runner as tor
from radeon_ort import unified_runtime as ur

FAKE = "tests.fixtures.fake_ep_worker"


def _inputs():
    return {"images": np.zeros((1, 3, 8, 8), dtype=np.float32)}


def _predict(tmp_path, *, model_name="yolo26n.raw_b1_fp32_static.onnx", **kw):
    m = tmp_path / model_name
    m.write_bytes(b"onnx-bytes")
    args = {"policy": "prefer_amdgpu_allow_dml", "target_gfx": "gfx1151",
                "work_dir": tmp_path / "work", "run_id": "RUN_ORACLE",
                "worker_module": FAKE, "timeout_s": 30}
    args.update(kw)
    return ur.predict(model_path=m, inputs=_inputs(), **args)


def test_verify_task_false_is_numeric_parity_only(tmp_path):
    r = _predict(tmp_path, task="detect", verify_task=False)
    assert r["gpu_full"] is True
    assert r["task_verified"] is False
    assert r["task_parity"]["status"] == "NOT_RUN"
    assert "verify_task=False" in r["task_parity"]["reason"]


def test_no_task_at_all_is_not_requested(tmp_path):
    r = _predict(tmp_path)
    assert r["task_parity"]["status"] == "NOT_REQUESTED"


def test_verify_task_requires_a_task(tmp_path):
    with pytest.raises(ur.ContractError, match="requires task"):
        _predict(tmp_path, verify_task=True, pt_reference="x.pt")


def test_verify_task_requires_a_reference(tmp_path):
    with pytest.raises(ur.ContractError, match="pt_reference"):
        _predict(tmp_path, verify_task=True, task="detect")


def test_unknown_task_is_a_contract_error(tmp_path):
    with pytest.raises(ur.ContractError, match="unsupported task"):
        _predict(tmp_path, verify_task=True, task="not-a-task",
                 pt_reference="x.pt")


def test_missing_reference_file_blocks_without_faking_a_pass(tmp_path):
    r = _predict(tmp_path, task="detect", verify_task=True,
                 pt_reference=tmp_path / "nope.pt")
    assert r["gpu_full"] is True                     # the GPU really ran
    assert r["task_verified"] is False               # but the task is unverified
    assert r["task_parity"]["status"] == tor.BLOCKED
    assert "TASK_ORACLE_REFERENCE_MISSING" in r["task_parity"]["reason_code"]
    # the stub worker is not evidence-grade, so the verdict is UNVERIFIED - but
    # never INVALID and never VALID: the GPU/CPU split is still coherent
    assert ur.verify_run_result(r, work_dir=tmp_path / "work")["verdict"] == "RUNRESULT_UNVERIFIED"


def test_catalog_task_mismatch_blocks(tmp_path):
    pt = tmp_path / "yolo26n.pt"
    pt.write_bytes(b"pt-bytes")
    # pass a real interpreter so the *catalog* check is the one that fires: on a
    # CPU-only CI runner there is no .venv-export, and the interpreter
    # precondition would otherwise mask the task-binding check.
    r = _predict(tmp_path, task="segment", verify_task=True, pt_reference=pt,
                 export_python=sys.executable)
    assert r["task_verified"] is False
    assert r["task_parity"]["status"] == tor.BLOCKED
    assert "TASK_ORACLE_MODEL_TASK_MISMATCH" in r["task_parity"]["reason_code"]


def test_missing_candidate_outputs_blocks(tmp_path):
    pt = tmp_path / "yolo26n.pt"
    pt.write_bytes(b"pt-bytes")
    r = _predict(tmp_path, task="detect", verify_task=True, pt_reference=pt,
                 worker_case_extra={"fake_mode": {"cpu": "ok", "dml": "no_outputs",
                                                  "amdgpu": "no_outputs"}})
    assert r["gpu_full"] is False
    assert r["task_verified"] is False
    assert r["task_parity"]["status"] == tor.BLOCKED


def test_missing_interpreter_blocks(tmp_path):
    pt = tmp_path / "yolo26n.pt"
    pt.write_bytes(b"pt-bytes")
    r = _predict(tmp_path, task="detect", verify_task=True, pt_reference=pt,
                 export_python=tmp_path / "no-python.exe")
    assert r["task_verified"] is False
    assert "TASK_ORACLE_INTERPRETER_MISSING" in r["task_parity"]["reason_code"]


def test_oracle_block_does_not_change_gpu_full(tmp_path):
    r = _predict(tmp_path, task="detect", verify_task=True,
                 pt_reference=tmp_path / "nope.pt")
    assert r["readiness"]["gpu_full"] is True
    assert r["readiness"]["task_verified"] is False
    assert r["readiness"]["product_ready"] is False


def test_unsupported_task_name_in_oracle_module():
    with pytest.raises(Exception):
        tor._tensor_npy({}, Path("x.npy"))


def test_oracle_binding_records_every_input():
    spec = tor.resolve_task_spec(
        onnx_path="yolo26n.raw_b1_fp32_static.onnx", task="detect",
        repo_root=Path(__file__).resolve().parents[2])
    assert spec["catalog_match"] is True
    assert spec["catalog_task"] == "detect"
    assert spec["imgsz"] == 640
    assert spec["catalog_sha256"]


def test_oracle_binding_unknown_onnx_is_not_silently_assumed():
    spec = tor.resolve_task_spec(onnx_path="mystery.onnx", task="detect",
                                 repo_root=Path(__file__).resolve().parents[2])
    assert spec["catalog_match"] is False
    assert spec["imgsz"] == 640