"""Exit-code propagation (R3-A): PASS=0, failure/tamper=1, blocked=2.

The producer (r1_pipeline) and the coordinator (run_matrix) are both covered:
the coordinator must never collapse a classified refusal into a generic 1.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers_receipts import make_receipt_v2

from radeon_ort import contract as c
from runner import run_matrix
from runner.r1_pipeline import classify_exit

TH = {"numeric_parity": {"fp32": {"atol": 5e-3, "rtol": 5e-3}},
      "task_oracle": {"detection": {"iou_thres": 0.5, "min_matches": 1}}}
ENV = {"python_executable_name": "python.exe", "python_version": "3.12.10",
       "os_build": "10.0.26200", "ort_version": "1.25.2",
       "ort_distribution": "onnxruntime-windowsml",
       "ort_providers": ["CPUExecutionProvider", "DmlExecutionProvider"],
       "windowsml_version": "2.5.96", "probe_error": None}
DEV = {"gfx": "gfx1151", "host_id": "host_test", "vendor_id": "0x1002",
       "device_id": "0x1586", "physical_gpu_name": "AMD Radeon 8060S Graphics",
       "luid": "0x0:0x11746", "identity_method": "test", "did_match": True}
MODELS = [{"checkpoint": "yolo26n.pt", "task": "detect", "imgsz": 640,
           "pt_sha256": "1" * 64, "onnx_sha256": "2" * 64,
           "asset_available": True, "gfx": "gfx1151"}]
HOST = {"gfx": "gfx1151", "host_id": "host_test", "vendor_id": "0x1002",
        "device_id": "0x1586", "verified_device_identity": True,
        "device_luid": "0x0:0x11746"}


def test_classify_exit_matches_documented_contract():
    assert classify_exit(0, 0, ["x"]) == (1, "EVIDENCE_INVALID")
    assert classify_exit(3, 1, []) == (1, "CASE_FAILED")
    assert classify_exit(0, 0, []) == (2, "NO_GPU_PASS")
    assert classify_exit(4, 0, []) == (0, "OK")


def test_run_pipeline_preserves_exit_code_and_reason(tmp_path):
    cmd = [sys.executable, "-c",
           "import sys; print('[r1] CONTRACT FAIL: thresholds changed'); sys.exit(2)"]
    log = tmp_path / "logs" / "r1.log"
    rc, reasons = run_matrix._run_pipeline(cmd, log)
    assert rc == 2
    assert any("CONTRACT FAIL" in r for r in reasons)
    assert "thresholds changed" in log.read_text(encoding="utf-8")
    assert run_matrix.pipeline_classification(rc, reasons) == "CONTRACT_REFUSED"


def test_run_pipeline_reports_crash_exit(tmp_path):
    cmd = [sys.executable, "-c", "import sys; sys.exit(7)"]
    rc, _ = run_matrix._run_pipeline(cmd, tmp_path / "r1.log")
    assert rc == 7
    assert run_matrix.pipeline_classification(rc, []) == "FAILURE"


def _catalog(tmp_path: Path) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"entries": {
        "yolo26n.pt": {"checkpoint": "yolo26n.pt", "task": "detect",
                       "tier": "A", "eligibility": "eligible",
                       "imgsz_default": 640}}}), encoding="utf-8")
    return path


def _write_run(out_dir: Path, *, receipt: dict | None, eps=("dml",)):
    out_dir.mkdir(parents=True, exist_ok=True)
    contract = c.build_run_contract(
        run_id=out_dir.name, gfx="gfx1151", eps=list(eps), models=MODELS,
        cases=["yolo26n__detect"], thresholds=TH,
        code={"git_head_sha": "a" * 40, "worktree_dirty": False,
              "harness_sha256": "b" * 64},
        device_identity=DEV, images={}, options={"benchmark": False,
                                                 "with_negative": False},
        environment=ENV, created_utc="2026-11-19T00:00:00Z")
    c.write_thresholds_snapshot(out_dir, TH)
    c.write_run_contract(out_dir, contract)
    if receipt is not None:
        rec = make_receipt_v2(ep="dml", ep_nodes=1)
        rec["case_id"] = "yolo26n__detect__dml"
        rec["run_id"] = out_dir.name
        rec["contract"] = c.build_receipt_contract(
            contract, ep="dml", model=rec["model"], input_block=rec["input"])
        (out_dir / "receipts").mkdir(exist_ok=True)
        (out_dir / "receipts" / "receipt_yolo26n__detect__dml.json").write_text(
            json.dumps(receipt if receipt is not rec else rec), encoding="utf-8")


def _argv(tmp_path: Path, catalog: Path) -> list[str]:
    return ["--gfx", "gfx1151", "--ep", "dml", "--run-id", "RUN_TEST",
            "--reports-root", str(tmp_path / "reports"),
            "--catalog", str(catalog), "--models-root", str(tmp_path / "models")]


def test_main_returns_zero_for_strict_pass(tmp_path, monkeypatch):
    catalog = _catalog(tmp_path)
    out_dir = tmp_path / "reports" / "gfx1151" / "RUN_TEST"
    monkeypatch.setattr(run_matrix, "_resolve_host", lambda *a, **k: dict(HOST))

    def fake(cmd, log_path):
        _write_run(out_dir, receipt=None)
        return 0, []

    monkeypatch.setattr(run_matrix, "_run_pipeline", fake)
    assert run_matrix.main(_argv(tmp_path, catalog)) == 2  # no receipt => blocked

    monkeypatch.setattr(run_matrix, "_run_pipeline", lambda cmd, log: (0, []))
    # rewrite the same run with an honest DML receipt
    for p in (out_dir / "receipts").glob("*.json"):
        p.unlink()
    contract = c.load_run_contract(out_dir)
    rec = make_receipt_v2(ep="dml", ep_nodes=1)
    rec["case_id"] = "yolo26n__detect__dml"
    rec["run_id"] = "RUN_TEST"
    rec["contract"] = c.build_receipt_contract(
        contract, ep="dml", model=rec["model"], input_block=rec["input"])
    (out_dir / "receipts").mkdir(exist_ok=True)
    (out_dir / "receipts" / "receipt_yolo26n__detect__dml.json").write_text(
        json.dumps(rec), encoding="utf-8")
    assert run_matrix.main(_argv(tmp_path, catalog) + ["--resume"]) == 0
    record = json.loads((out_dir / "MATRIX_EXIT.json").read_text(encoding="utf-8"))
    assert record["matrix_exit_code"] == 0
    assert record["pipeline_exit_code"] == 0


def test_main_propagates_classified_block(tmp_path, monkeypatch):
    catalog = _catalog(tmp_path)
    monkeypatch.setattr(run_matrix, "_resolve_host", lambda *a, **k: dict(HOST))
    monkeypatch.setattr(run_matrix, "_run_pipeline", lambda cmd, log: (
        2, ["[r1] CONTRACT FAIL: THRESHOLDS_CONFIG_CHANGED"]))
    assert run_matrix.main(_argv(tmp_path, catalog)) == 2
    record = json.loads((tmp_path / "reports" / "gfx1151" / "RUN_TEST"
                         / "MATRIX_EXIT.json").read_text(encoding="utf-8"))
    assert record["pipeline_exit_code"] == 2
    assert record["classification"] == "CONTRACT_REFUSED"
    assert record["matrix_exit_code"] == 2


def test_main_maps_real_failure_to_one(tmp_path, monkeypatch):
    catalog = _catalog(tmp_path)
    monkeypatch.setattr(run_matrix, "_resolve_host", lambda *a, **k: dict(HOST))
    monkeypatch.setattr(run_matrix, "_run_pipeline", lambda cmd, log: (
        1, ["[r1] VALIDATION PROBLEMS yolo26n/dml: forged"]))
    assert run_matrix.main(_argv(tmp_path, catalog)) == 1


def test_main_rejects_unverified_device(tmp_path, monkeypatch):
    catalog = _catalog(tmp_path)
    monkeypatch.setattr(run_matrix, "_resolve_host", lambda *a, **k: dict(
        HOST, verified_device_identity=False, identity_method="WRONG_DEVICE"))
    assert run_matrix.main(_argv(tmp_path, catalog)) == 2
