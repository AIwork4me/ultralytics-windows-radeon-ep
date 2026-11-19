"""Contract-aware resume / threshold snapshot regression tests (P0-B/P0-D).

These tests never touch a GPU: they freeze the contract semantics that forbid
reusing a receipt after code/model/input/EP/threshold/device changes.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers_receipts import make_receipt_v2  # noqa: E402
from radeon_ort import contract as c  # noqa: E402

TH = {"numeric_parity": {"fp32": {"atol": 5e-3, "rtol": 5e-3}},
      "task_oracle": {"detection": {"iou_thres": 0.5, "min_matches": 1}}}
CODE = {"git_head_sha": "a" * 40, "worktree_dirty": False,
        "harness_sha256": "b" * 64}
DEV = {"gfx": "gfx1151", "host_id": "host_test", "vendor_id": "0x1002",
       "device_id": "0x1586", "physical_gpu_name": "AMD Radeon 8060S Graphics",
       "luid": "0x0:0x11746", "identity_method": "test", "did_match": True}
MODELS = [{"checkpoint": "yolo26n.pt", "task": "detect", "imgsz": 640,
           "pt_sha256": "1" * 64, "onnx_sha256": "2" * 64,
           "asset_available": True, "gfx": "gfx1151"}]
IMAGES = {"primary": {"name": "bus.jpg", "sha256": "3" * 64}}
OPTIONS = {"benchmark": False, "with_negative": False}


def _contract(tmp_path: Path, **kw):
    args = dict(run_id="RUN_TEST", gfx="gfx1151", eps=["amdgpu", "dml"],
                models=MODELS, cases=["yolo26n__detect"], thresholds=TH,
                code=CODE, device_identity=DEV, images=IMAGES, options=OPTIONS,
                created_utc="2026-11-19T00:00:00Z")
    args.update(kw)
    return c.build_run_contract(**args)


def _host():
    return {"gfx": "gfx1151", "host_id": "host_test", "vendor_id": "0x1002",
            "device_id": "0x1586", "verified_device_identity": True,
            "device_luid": "0x0:0x11746"}


def test_threshold_snapshot_is_immutable_and_resume_rejects_changed_config(tmp_path):
    run = tmp_path / "RUN_TEST"
    snap, sha = c.ensure_thresholds(run, TH, resume=False)
    assert snap == TH and c.thresholds_snapshot_path(run).is_file()
    assert c.thresholds_sha256(snap) == sha

    changed = copy.deepcopy(TH)
    changed["numeric_parity"]["fp32"]["atol"] = 0.9
    with pytest.raises(c.ContractError, match="THRESHOLDS_CONFIG_CHANGED"):
        c.ensure_thresholds(run, changed, resume=True)


def test_resume_without_snapshot_is_refused(tmp_path):
    run = tmp_path / "RUN_TEST"
    run.mkdir()
    with pytest.raises(c.ContractError, match="missing"):
        c.ensure_thresholds(run, TH, resume=True)


def test_run_contract_tamper_detected(tmp_path):
    run = tmp_path / "RUN_TEST"
    run.mkdir()
    contract = _contract(tmp_path)
    c.write_run_contract(run, contract)
    loaded = c.load_run_contract(run)
    assert loaded["contract_sha256"] == contract["contract_sha256"]

    path = run / c.RUN_CONTRACT_NAME
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["thresholds"]["numeric_parity"]["fp32"]["atol"] = 0.9
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(c.ContractError, match="TAMPERED"):
        c.load_run_contract(run)


def test_legacy_run_without_contract_cannot_resume(tmp_path):
    run = tmp_path / "RUN_TEST"
    run.mkdir()
    with pytest.raises(c.ContractError, match="legacy"):
        c.load_run_contract(run)


def test_verify_run_contract_rejects_changed_code_and_thresholds(tmp_path, monkeypatch):
    run = tmp_path / "RUN_TEST"
    run.mkdir()
    contract = _contract(tmp_path)
    monkeypatch.setattr(c, "code_state", lambda root: dict(CODE))
    assert c.verify_run_contract(
        contract, repo_root=tmp_path, run_dir=run, thresholds=TH,
        host_block=_host(), models=["yolo26n.pt"], eps=["amdgpu", "dml"],
        images=IMAGES, options=OPTIONS) == []

    changed_th = copy.deepcopy(TH)
    changed_th["numeric_parity"]["fp32"]["atol"] = 0.9
    problems = c.verify_run_contract(
        contract, repo_root=tmp_path, run_dir=run, thresholds=changed_th,
        host_block=_host(), models=["yolo26n.pt"], eps=["amdgpu", "dml"],
        images=IMAGES, options=OPTIONS)
    assert any("THRESHOLDS" in p for p in problems)

    bad_host = dict(_host(), device_id="0x7550")
    problems = c.verify_run_contract(
        contract, repo_root=tmp_path, run_dir=run, thresholds=TH,
        host_block=bad_host, models=["yolo26n.pt"], eps=["amdgpu", "dml"],
        images=IMAGES, options=OPTIONS)
    assert any("DEVICE_IDENTITY_CHANGED" in p for p in problems)


def test_receipt_contract_matches_then_rejects_tampered_model_and_input():
    contract = _contract(Path("."))
    receipt = make_receipt_v2(ep="dml", ep_nodes=1)
    receipt["case_id"] = "yolo26n__detect__dml"
    receipt["run_id"] = "RUN_TEST"
    receipt["contract"] = c.build_receipt_contract(
        contract, ep="dml", model=receipt["model"], input_block=receipt["input"])
    assert c.validate_receipt_contract(receipt, contract) == []

    bad = copy.deepcopy(receipt)
    bad["model"]["onnx_sha256"] = "f" * 64
    assert any("MODEL_ONNX" in p for p in c.validate_receipt_contract(bad, contract))

    bad2 = copy.deepcopy(receipt)
    bad2["input"]["tensor_sha256"] = {"images": "e" * 64}
    assert any("INPUT_TENSOR" in p for p in c.validate_receipt_contract(bad2, contract))


def test_receipt_without_contract_is_rejected_for_contract_run():
    contract = _contract(Path("."))
    receipt = make_receipt_v2(ep="dml", ep_nodes=1)
    problems = c.validate_receipt_contract(receipt, contract)
    assert any("CONTRACT_MISSING" in p for p in problems)


def test_run_matrix_audit_is_strict_validation_driven(tmp_path):
    from runner.run_matrix import _audit

    run = tmp_path / "RUN_TEST"
    (run / "receipts").mkdir(parents=True)
    contract = _contract(tmp_path, eps=["dml"])
    c.write_thresholds_snapshot(run, TH)
    c.write_run_contract(run, contract)

    receipt = make_receipt_v2(ep="dml", ep_nodes=1)
    receipt["case_id"] = "yolo26n__detect__dml"
    receipt["run_id"] = "RUN_TEST"
    receipt["contract"] = c.build_receipt_contract(
        contract, ep="dml", model=receipt["model"], input_block=receipt["input"])
    (run / "receipts" / "receipt_yolo26n__detect__dml.json").write_text(
        json.dumps(receipt), encoding="utf-8")

    rc, audit = _audit(run, contract, run_id="RUN_TEST")
    assert rc == 0, audit
    assert audit["counts"]["PASS_FULL"] == 1


def test_run_matrix_audit_blocks_when_second_ep_receipt_missing(tmp_path):
    from runner.run_matrix import _audit

    run = tmp_path / "RUN_TEST"
    (run / "receipts").mkdir(parents=True)
    contract = _contract(tmp_path, eps=["amdgpu", "dml"])
    c.write_thresholds_snapshot(run, TH)
    c.write_run_contract(run, contract)

    receipt = make_receipt_v2(ep="dml", ep_nodes=1)
    receipt["case_id"] = "yolo26n__detect__dml"
    receipt["run_id"] = "RUN_TEST"
    receipt["contract"] = c.build_receipt_contract(
        contract, ep="dml", model=receipt["model"], input_block=receipt["input"])
    (run / "receipts" / "receipt_yolo26n__detect__dml.json").write_text(
        json.dumps(receipt), encoding="utf-8")

    rc, audit = _audit(run, contract, run_id="RUN_TEST")
    assert rc == 2, audit
    assert audit["counts"]["PASS_FULL"] == 1
    assert audit["expected"] == 2


def test_run_matrix_audit_rejects_forged_status_label(tmp_path):
    from runner.run_matrix import _audit

    run = tmp_path / "RUN_TEST"
    (run / "receipts").mkdir(parents=True)
    contract = _contract(tmp_path, eps=["dml"])
    c.write_thresholds_snapshot(run, TH)
    c.write_run_contract(run, contract)

    receipt = make_receipt_v2(ep="dml", ep_nodes=1)
    receipt["case_id"] = "yolo26n__detect__dml"
    receipt["run_id"] = "RUN_TEST"
    receipt["contract"] = c.build_receipt_contract(
        contract, ep="dml", model=receipt["model"], input_block=receipt["input"])
    # forge: PASS label but zero DML nodes
    receipt["session"]["provider_node_counts"] = {"DmlExecutionProvider": 0}
    receipt["device_binding"]["final_profile"]["provider_node_counts"] = {
        "DmlExecutionProvider": 0}
    receipt["status"]["dml"] = "PASS_GPU_EXECUTED_FULL"
    (run / "receipts" / "receipt_yolo26n__detect__dml.json").write_text(
        json.dumps(receipt), encoding="utf-8")

    rc, audit = _audit(run, contract, run_id="RUN_TEST")
    assert rc == 1, audit
    assert any("strict validation failed" in p for p in audit["problems"])
