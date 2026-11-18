"""RCA2A-009 regression tests: supervisor GPU-PASS validation on the DML path.

The amdgpu path requires an ep_device_selected (LUID-pinned) event; the DML
path must instead accept the always-emitted gpu_identity event (physical DXGI
Radeon adapter) — never an amdgpu-only probe event, and never a silent pass
when identity is missing."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runner.rca2a_main import _validate_gpu_pass


def _events(with_identity: bool) -> list[dict]:
    evs = [
        {"kind": "providers_available", "providers": ["DmlExecutionProvider",
                                                     "CPUExecutionProvider"]},
        {"kind": "session_created", "providers": ["DmlExecutionProvider",
                                                  "CPUExecutionProvider"]},
    ]
    if with_identity:
        evs.insert(1, {"kind": "gpu_identity", "vendor_id": 0x1002,
                       "device_id": 0x744C, "name": "AMD Radeon RX 7900 XTX"})
    return evs


def test_dml_pass_with_physical_identity(tmp_path):
    # Positive control must prove real numerical parity and a profiled GPU node.
    np.savez(tmp_path / "outputs_first.npz",
             output=np.array([1.0], dtype=np.float32))
    (tmp_path / "ort_profile_sess0.json").write_text(
        json.dumps([{"cat": "Node", "args": {
            "provider": "DmlExecutionProvider"}}]), encoding="utf-8")
    case = {"reference_outputs": {"output": [1.0]}}
    v = _validate_gpu_pass(tmp_path, case, _events(True), 0, "dml", True)
    assert v["verdict"] == "GPU_PASS_VALIDATED", v["checks"]
    names = {c["check"] for c in v["checks"]}
    assert "gpu_identity_event" in names and "physical_radeon_adapter" in names


def test_dml_fail_closed_without_identity(tmp_path):
    v = _validate_gpu_pass(tmp_path, {}, _events(False), 0, "dml", False)
    assert v["verdict"] == "GPU_PASS_INVALIDATED"
    assert any(c["check"] == "gpu_identity_event" and not c["ok"]
               for c in v["checks"])


def test_dml_fail_closed_on_non_amd_adapter(tmp_path):
    evs = _events(True)
    evs[1] = {"kind": "gpu_identity", "vendor_id": 0x8086, "device_id": 0xFFFF}
    v = _validate_gpu_pass(tmp_path, {}, evs, 0, "dml", False)
    assert v["verdict"] == "GPU_PASS_INVALIDATED"
    assert any(c["check"] == "physical_radeon_adapter" and not c["ok"]
               for c in v["checks"])


def test_amdgpu_path_still_requires_ep_device_selected(tmp_path):
    evs = _events(True)  # gpu_identity present but no ep_device_selected
    v = _validate_gpu_pass(tmp_path, {}, evs, 0, "amdgpu", False)
    assert v["verdict"] == "GPU_PASS_INVALIDATED"
    assert any(c["check"] == "ep_device_probe_event" and not c["ok"]
               for c in v["checks"])
