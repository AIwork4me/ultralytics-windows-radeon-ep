"""Regression tests for the clean-host P0 audit findings.

These are fail-closed tests: the harness must REFUSE to bless empty or
unverifiable evidence, and device identifiers must be redacted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


def test_redact_pnp_id_keeps_public_prefix_only():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from radeon_ort.host_probe import redact_pnp_id
    raw = "PCI\\VEN_1002&DEV_1586&SUBSYS_8D01103C&REV_D1\\4&85C8329&0&0041"
    assert redact_pnp_id(raw) == "PCI\\VEN_1002&DEV_1586&SUBSYS_8D01103C&REV_D1"
    # A public-only id must be preserved.
    assert redact_pnp_id("PCI\\VEN_1002&DEV_1586") == "PCI\\VEN_1002&DEV_1586"


def test_verify_receipts_rejects_zero_receipts(tmp_path, capsys):
    from runner import verify_receipts
    run_dir = tmp_path / "empty_run"
    run_dir.mkdir()
    old = sys.argv
    try:
        sys.argv = ["verify_receipts", "--run-dir", str(run_dir)]
        rc = verify_receipts.main()
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    assert rc == 1
    assert "zero receipts" in out
    assert "ALL_OK" not in out


def test_verify_receipts_rejects_missing_manifest(tmp_path, capsys):
    from runner import verify_receipts
    run_dir = tmp_path / "run"
    (run_dir / "receipts").mkdir(parents=True)
    # A structurally invalid receipt is enough: both problems must be reported.
    (run_dir / "receipts" / "receipt_x.json").write_text("{}", encoding="utf-8")
    old = sys.argv
    try:
        sys.argv = ["verify_receipts", "--run-dir", str(run_dir)]
        rc = verify_receipts.main()
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    assert rc == 1
    assert "manifest.sha256 absent" in out


def test_finalize_run_rejects_zero_receipts(tmp_path, capsys):
    from runner import finalize_run
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    old = sys.argv
    try:
        sys.argv = ["finalize_run", "--run-dir", str(run_dir)]
        rc = finalize_run.main()
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    assert rc == 1
    assert "zero receipts" in out


def test_blocked_receipt_without_expected_outputs_validates():
    """A BLOCKED_EP receipt has no session outputs by definition; it must still
    validate as an honest non-pass (previously the v2 validator demanded an output
    manifest even for blocked cases, forcing forged/partial evidence)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from radeon_ort.receipts import (ReceiptStatus, build_receipt_v2,
                                     combine_v2_to_status, derive_verdicts,
                                     validate_receipt_strict)
    st = ReceiptStatus(model_asset="AVAILABLE", export="PASS_ONNX_EXPORT",
                       cpu_oracle="PASS_CPU_REFERENCE", amdgpu="BLOCKED_EP",
                       dml="NOT_RUN", task_oracle="NOT_RUN", benchmark="NOT_MEASURED",
                       host="VERIFIED_GFX1151")
    receipt = build_receipt_v2(
        run_id="run-123456", case_id="c__amdgpu", status=st, verdicts={},
        host={"host_id": "host_test", "physical_gpu_name": "AMD Radeon 8060S Graphics",
              "gfx": "gfx1151", "os_build": "26200.1", "driver_version": "test",
              "verified_device_identity": True},
        model={"family": "YOLO", "checkpoint": "m.pt", "task": "detect",
               "pt_sha256": "b" * 64, "onnx_sha256": "a" * 64,
               "onnx_path_redacted": "models/onnx/m.onnx", "output_names": [],
               "export": {"imgsz": 640, "nms_mode": "raw", "quantize": "fp32",
                          "batch": 1, "dynamic": False}},
        input_block={"input_id": "0" * 64, "kind": "deterministic_synthetic",
                     "tensor_sha256": {"images": "1" * 64}},
        expected_outputs=[], worker={"exit_code": 34}, session=None,
        device_binding=None, parity=None, task=None, task_parity=None,
        benchmark_data=None, errors=[{"stage": "AMDGPU", "type": "RUNTIME",
                                      "message": "BLOCKED_EP"}],
        rca_issue_ids=["AMDGPU-RCA-001"],
        provenance={"script_head_sha": "t", "command": "t", "seed": 0})
    verdicts = derive_verdicts(receipt)
    receipt["verdicts"] = verdicts
    receipt["status"]["amdgpu"] = combine_v2_to_status("amdgpu", verdicts["amdgpu"])
    v = validate_receipt_strict(receipt)
    assert v["ok"], v["problems"]
    assert combine_v2_to_status("amdgpu", verdicts["amdgpu"]) == "BLOCKED_EP"
