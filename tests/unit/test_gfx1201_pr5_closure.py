"""PR #5 must be independently importable, fail closed and preserve old runs.

CPU-only tests; GPU claims are deliberately NOT made by these fixtures.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from runner.unified_canary import compare_named_outputs
from runner import freeze_gfx1201_evidence as freeze
from radeon_ort.device_identity import resolve_target_adapter, DxgiAdapter

def _gpu(did, desc="AMD Radeon RX 7900 XTX", luid=123):
    return DxgiAdapter(index=0, description=desc, vendor_id=0x1002,
                       device_id=did, sub_sys_id=0, revision=0,
                       dedicated_video_mb=24560, dedicated_system_mb=0,
                       shared_sys_mb=0, luid_high=0, luid_low=luid)

def test_pr5_standalone_imports_without_pr6_module():
    import runner.unified_canary as uc
    import runner.freeze_gfx1201_evidence as fr
    assert callable(uc.main) and callable(fr.main)
    assert not hasattr(fr, "build_chain")
    assert "unified_identity" not in Path(uc.__file__).read_text(encoding="utf-8")
    assert "unified_identity" not in Path(fr.__file__).read_text(encoding="utf-8")

def test_same_output_high_scale_does_not_mask_small_error():
    cpu = {"y": np.array([1000.0, 0.0], dtype=np.float32)}
    gpu = {"y": np.array([1000.0, 1.0], dtype=np.float32)}
    r = compare_named_outputs(cpu, gpu)
    assert r["allclose"] is False and "parity_failed" in r["reason"]

def test_per_element_positive_and_discrete_exact():
    cpu = {"z": np.array([0.0, 0.1], np.float32),
           "labels": np.array([1, 2], np.int64)}
    gpu = {"z": np.array([0.004, 0.1005], np.float32),
           "labels": np.array([1, 2], np.int64)}
    assert compare_named_outputs(cpu, gpu)["allclose"]
    gpu["labels"][0] = 3
    assert not compare_named_outputs(cpu, gpu)["allclose"]

@pytest.mark.parametrize("invalid", [
    np.array([np.nan], np.float32),
    np.array([np.inf], np.float32),
    np.array([], np.float32),
])
def test_invalid_output_never_passes(invalid):
    assert not compare_named_outputs({"z": np.array([0.0], np.float32)},
                                     {"z": invalid})["allclose"]

def test_wrong_output_name_and_empty_oracle_never_pass():
    z = np.array([0.], np.float32)
    assert not compare_named_outputs({"a": z}, {"b": z})["allclose"]
    assert not compare_named_outputs({}, {"a": z})["allclose"]

def test_gfx1201_wrong_host_still_blocks():
    result = resolve_target_adapter([_gpu(0x744C)], gfx="gfx1201")
    assert not result["ok"] and "744C" in result["reason"]

def test_new_freezer_generates_real_csv_without_overwrite(tmp_path, monkeypatch):
    target = tmp_path / "new-run"
    target.mkdir()
    monkeypatch.setattr(freeze, "RUN", target)
    csv = "task,ep,status\nclassify,amdgpu,NOT_RUN\n"
    freeze.w("MODEL_TASK_MATRIX.csv", csv)
    assert (target / "MODEL_TASK_MATRIX.csv").read_text(encoding="utf-8") == csv
    with pytest.raises(FileExistsError):
        freeze.w("MODEL_TASK_MATRIX.csv", csv)

def test_freezer_refuses_historical_run_id(tmp_path, monkeypatch, capsys):
    root = tmp_path / "reports"
    existing = root / "GFX1201_DIAG_20261118T2359Z"
    existing.mkdir(parents=True)
    (existing / "historical.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(freeze, "EVIDENCE_BASE", root)
    monkeypatch.setattr(sys, "argv", ["freeze",
                                     "--run-id", "GFX1201_DIAG_20261118T2359Z"])
    assert freeze.main() == 65
    assert "BLOCKED_IMMUTABLE_RUN" in capsys.readouterr().err
    assert (existing / "historical.json").read_text() == "{}"

def test_freezer_blocks_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(freeze, "EVIDENCE_BASE", tmp_path)
    monkeypatch.setattr(sys, "argv", ["freeze", "--run-id", "../bad"])
    with pytest.raises(ValueError):
        freeze.main()
