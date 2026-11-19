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


def make_receipt_v2(*, ep="amdgpu", exec_status="PASS_GPU_EXECUTED_FULL",
                    ep_nodes=1, cpu_nodes=0, worker_rc=0, outputs=("output0",),
                    dtype="float32", shape=(1, 3), parity_present=True,
                    parity_allclose=True, finite=True, by_name=True,
                    binding_state="EP_DEVICE_SELECTED", probe_nodes=1,
                    attribution="fused_single_node", task_parity_passed=None,
                    task_kind="TASK_PARITY", thresholds_declared=None,
                    errors=None, verified=True,
                    rca_ids=None, backend_route=None, model_sha=None,
                    input_present=True, contract=None) -> dict:
    """Structurally-valid v2 receipt. An *honest* one is self-consistent because
    verdicts/status are derived from the same evidence the builder emits."""
    from radeon_ort.receipts import (build_receipt_v2, derive_verdicts,
                                     combine_v2_to_status)

    ep_name = "AMDGPU" if ep == "amdgpu" else "DML"
    ort_key = "AMDGPU" if ep == "amdgpu" else "DmlExecutionProvider"
    exp = [{"name": n, "dtype": dtype, "shape": list(shape)} for n in outputs]
    outs = []
    for n in outputs:
        outs.append({
            "name": n, "shape": list(shape), "dtype": dtype, "finite": finite,
            "max_abs_diff": 1e-6, "mean_abs_diff": 1e-7, "max_rel_diff": 1e-6,
            "p99_abs_diff": 1e-6, "allclose": parity_allclose, "threshold_used": 5e-3,
            "candidate_sha256": _hex(10, n), "ref_sha256": _hex(11, n),
        })
    parity = {
        "reference": "CPU_ORT", "outputs": outs,
        "all_outputs_allclose": parity_allclose and finite,
        "by_name_matching": by_name,
        "thresholds": {"atol": 5e-3, "rtol": 5e-3, "source": "configs/thresholds.yaml"},
    } if parity_present else None

    counts = {ort_key: ep_nodes}
    if cpu_nodes:
        counts["CPUExecutionProvider"] = cpu_nodes
    db = {
        "ep": ep_name, "state": binding_state, "selected_candidate_index": 1,
        "candidates_tried": 2,
        "selected_ep_device": {"ep_vendor": "AMD", "ep_metadata": {"version": "0.1.0"},
                               "ep_options": {}},
        "probe": {"amdgpu_nodes": probe_nodes, "cpu_nodes": 0, "profile_sha256": _hex(5)},
        "final_profile": {"provider_node_counts": dict(counts), "profile_sha256": _hex(6)},
        "attribution": attribution,
        "dxgi_corroboration": {"luid": "0x0:0x11746", "vendor": "0x1002", "did": "0x1586"},
    }
    session_block = {
        "ep": ep_name, "requested_providers": [ep_name],
        "providers_after_create": [ep_name],
        "ort_version": "1.25.2", "ort_distribution": "onnxruntime-windowsml",
        "provider_node_counts": counts,
        "graph_node_total": ep_nodes + cpu_nodes,
        "profile_available": True, "profile_sha256": _hex(4),
        "profile_fused_event_present": ep_nodes > 0,
        "routing_notes": "", "stderr_fallback_signals": [], "stdout_fallback_signals": [],
    }
    if backend_route:
        session_block["backend_route_evidence"] = backend_route

    task_block = None
    tp_block = None
    task_status = "NOT_RUN"
    if task_kind == "PT_APP":
        task_block = {"oracle_kind": "PT_APP", "metrics": {"pt_boxes": 5},
                      "passed": True, "summary": "smoke"}
        task_status = "PT_APP_SMOKE_ONLY"
    elif task_parity_passed is not None:
        tp_block = {
            "oracle_kind": "TASK_PARITY", "task": "detect",
            "image_sha256": _hex(7), "input_id": _hex(3),
            "ref_source": "PT_ULTRALYTICS", "cand_source": f"{ep_name}_POST",
            "ref_fingerprint_sha256": _hex(8), "cand_fingerprint_sha256": _hex(9),
            "metrics": {"matched": 4, "iou_mean": 0.97, "ref_entities": 4},
            "thresholds_declared": thresholds_declared or {"iou_thres": 0.5},
            "thresholds_source": "configs/thresholds.yaml",
            "passed": task_parity_passed,
            "negative_control": None, "not_implemented_reason": None,
        }
        task_status = "PASS_TASK" if task_parity_passed else "FAIL_TASK"

    st = ReceiptStatus(
        model_asset="AVAILABLE", export="PASS_ONNX_EXPORT",
        cpu_oracle="PASS_CPU_REFERENCE", amdgpu="NOT_RUN", dml="NOT_RUN",
        task_oracle=task_status, benchmark="NOT_MEASURED",
        host="VERIFIED_GFX1151" if verified else "BLOCKED_ENV",
    )
    setattr(st, ep, exec_status)

    host_block = {
        "host_id": "host_test", "physical_gpu_name": "AMD Radeon 8060S Graphics",
        "gfx": "gfx1151", "device_luid": "0x0:0x11746",
        "dxgi_evidence": {"vendor_id": "0x1002", "device_id": "0x1586"},
        "pnp_evidence": {"pnp_device_id": "PCI\\VEN_1002&DEV_1586"},
        "os_build": "26200.9974", "driver_version": "32.0.22032.6002",
        "driver_date": "2025-12-15", "wddm_version": "3.2",
        "verified_device_identity": verified, "identity_method": "test",
    }
    model_block = {
        "family": "YOLO26", "task": "detect", "checkpoint": "yolo26n.pt",
        "size": "n", "pt_sha256": _hex(1), "onnx_sha256": model_sha or _hex(2),
        "onnx_path_redacted": "%USERPROFILE%/models/onnx/x.onnx",
        "export": {"imgsz": 640, "opset": 17, "dynamic": False, "batch": 1,
                   "nms_mode": "raw", "quantize": "fp32", "args": {},
                   "ultralytics_version": "8.4.215"},
        "input_names": ["images"], "output_names": list(outputs),
        "inputs_sha256": _hex(3),
    }
    input_block = {
        "input_id": _hex(12), "kind": "real_image", "image_sha256": _hex(7),
        "tensor_sha256": {"images": _hex(13)},
        "preprocess": {"method": "letterbox-ultralytics", "params": {"imgsz": 640},
                       "pt_equivalence": "same decoded image, AR-preserving resize+pad",
                       "tensor_dtype": "float32", "tensor_shape": [1, 3, 640, 640]},
        "npy_path_redacted": None,
        "generation": {"script": "radeon_ort.input_contract", "canonical_config": "{}"},
    }
    if not input_present:
        input_block = {"input_id": _hex(12), "kind": "real_image", "tensor_sha256": {}}

    r = build_receipt_v2(
        run_id="RUN_TEST2", case_id="CASE_TEST2", status=st, verdicts={}, host=host_block,
        model=model_block, input_block=input_block, expected_outputs=exp,
        worker={"exit_code": worker_rc, "pid": 4242, "parent_pid": 4241, "python": "3.13"},
        session=session_block, device_binding=db, parity=parity,
        task=task_block, task_parity=tp_block, benchmark_data=None,
        errors=errors or [],
        rca_issue_ids=(rca_ids if rca_ids is not None
                       else (["AMDGPU-RCA-003"] if errors else [])),
        provenance={"script_head_sha": "abc123", "command": "pytest", "seed": 1,
                    "python_version": "3.13", "ultralytics_version": "8.4.215",
                    "raw_log_path_redacted": None, "raw_log_sha256": None,
                    "env_fingerprint": {}},
        contract=contract,
    )
    # derive verdicts from evidence so honest receipts are self-consistent
    v = derive_verdicts(r)
    for e in ("amdgpu", "dml"):
        if r["status"][e] != "NOT_RUN" and r["status"][e] not in (
                "BLOCKED_EP", "BLOCKED_IDENTITY"):
            r["status"][e] = combine_v2_to_status(e, v[e])
    r["verdicts"] = v
    if task_kind == "PT_APP":
        r["status"]["task_oracle"] = "PT_APP_SMOKE_ONLY"
    return r
