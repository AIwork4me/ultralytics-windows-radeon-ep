"""Shared test helper: synthesize structurally-valid receipts for adversarial tests."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from radeon_ort.receipts import build_receipt, ReceiptStatus  # noqa: E402


def _hex(n: int, seed: str = "0") -> str:
    import hashlib
    return hashlib.sha256(f"{seed}{n}".encode()).hexdigest()


def make_receipt(*, amdgpu="PASS_GPU_EXECUTED_FULL", dml="NOT_RUN", cpu_nodes=0,
                 amdgpu_nodes=5, verified=True, parity_allclose=True, finite=True,
                 outputs=("y0",), by_name=True, task_passed=None, ep="AMDGPU",
                 errors=None, parity_present=True) -> dict:
    st = ReceiptStatus(
        model_asset="AVAILABLE", export="PASS_ONNX_EXPORT",
        cpu_oracle="PASS_CPU_REFERENCE", amdgpu=amdgpu, dml=dml,
        task_oracle="PASS_TASK" if task_passed is None else ("PASS_TASK" if task_passed else "FAIL_TASK"),
        benchmark="NOT_MEASURED",
        host="VERIFIED_GFX1151" if verified else "BLOCKED_ENV",
    )
    outs = []
    for i, name in enumerate(outputs):
        outs.append({
            "name": name, "shape": [1, 3], "dtype": "float32", "finite": finite,
            "max_abs_diff": 1e-6, "mean_abs_diff": 1e-7, "max_rel_diff": 1e-6,
            "p99_abs_diff": 1e-6, "allclose": parity_allclose, "threshold_used": 5e-3,
        })
    parity = {
        "reference": "CPU_ORT", "outputs": outs,
        "all_outputs_allclose": parity_allclose and finite,
        "by_name_matching": by_name,
    } if parity_present else None
    host_block = {
        "host_id": "host_test", "physical_gpu_name": "AMD Radeon 8060S Graphics",
        "gfx": "gfx1151", "device_luid": "0x0:0x1234",
        "dxgi_evidence": {"vendor_id": "0x1002", "device_id": "0x1586"},
        "pnp_evidence": {"pnp_device_id": "PCI\\VEN_1002&DEV_1586"},
        "os_build": "26200.9974", "driver_version": "32.0.22032.6002",
        "driver_date": "2025-12-15", "wddm_version": "3.2",
        "verified_device_identity": verified, "identity_method": "test",
    }
    model_block = {
        "family": "YOLO26", "task": "detect", "checkpoint": "yolo26n-detect.pt",
        "size": "n", "pt_sha256": _hex(1), "onnx_sha256": _hex(2),
        "onnx_path_redacted": "%USERPROFILE%/models/onnx/x.onnx",
        "export": {"imgsz": 640, "opset": 17, "dynamic": False, "batch": 1,
                   "nms_mode": "raw", "quantize": "fp32", "args": {},
                   "ultralytics_version": "8.3.280"},
        "input_names": ["images"], "output_names": list(outputs),
        "inputs_sha256": _hex(3),
    }
    session_block = {
        "ep": ep, "requested_providers": ["AMDGPUExecutionProvider"],
        "providers_after_create": ["AMDGPUExecutionProvider"],
        "ep_library_path_redacted": None, "ep_library_sha256": None, "ep_msix_package": None,
        "ort_version": "1.25.2", "ort_distribution": "onnxruntime-windowsml",
        "provider_node_counts": {"AMDGPU": amdgpu_nodes, "CPU": cpu_nodes},
        "graph_node_total": amdgpu_nodes + cpu_nodes,
        "profile_available": True, "profile_sha256": _hex(4),
        "profile_fused_event_present": amdgpu_nodes > 0,
        "routing_notes": "", "stderr_fallback_signals": [],
    }
    task_block = None if task_passed is None else {
        "oracle_kind": "PT_APP", "metrics": {"precision": 1.0}, "passed": task_passed,
        "summary": "test",
    }
    return build_receipt(
        run_id="RUN_TEST", case_id="CASE_TEST", status=st, host=host_block,
        model=model_block, session=session_block, parity=parity, task=task_block,
        errors=errors or [],
        provenance={"script_head_sha": "abc123", "command": "pytest",
                    "seed": 1, "python_version": "3.13", "ultralytics_version": "8.3.280",
                    "raw_log_path_redacted": None,
                    "raw_log_sha256": None, "env_fingerprint": {}},
    )


def clone(r: dict) -> dict:
    return copy.deepcopy(r)
