"""AMDGPU EP session creation (Windows ML 2.x / onnxruntime-windowsml).

Strict mode: provider list is ['AMDGPUExecutionProvider'] ONLY. If the EP cannot
be created, InferenceSession construction raises — that is the intended
fail-closed behaviour (FAIL_RUNTIME / BLOCKED_EP), never a CPU session.
"""

from __future__ import annotations

from pathlib import Path


def create_amdgpu_session(onnx_path: str | Path, *, enable_profiling: bool = True,
                          profile_prefix: str = "amdgpu_profile",
                          strict_no_cpu: bool = True,
                          provider_options: dict | None = None):
    """Create an AMDGPU-EP-only InferenceSession. Raises on any failure."""
    import onnxruntime as ort

    so = ort.SessionOptions()
    if enable_profiling:
        so.enable_profiling = True
        so.profile_file_profile_prefix = profile_prefix
    so.log_severity_level = 3

    providers = ["AMDGPUExecutionProvider"]
    if not strict_no_cpu:
        providers.append("CPUExecutionProvider")

    opts = provider_options or {}
    try:
        return ort.InferenceSession(str(onnx_path), sess_options=so, providers=providers,
                                    provider_options=[opts] if opts else None)
    except Exception:
        if opts:
            # retry without provider options in case option keys unsupported
            return ort.InferenceSession(str(onnx_path), sess_options=so, providers=providers)
        raise


def canary_model_path(tmp_dir: str | Path) -> Path:
    """Write a tiny 2-input MatMul+Add ONNX used as the live device canary."""
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    tmp = Path(tmp_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    p = tmp / "canary_amdgpu.onnx"
    a = helper.make_tensor_value_info("a", TensorProto.FLOAT, [256, 256])
    b = helper.make_tensor_value_info("b", TensorProto.FLOAT, [256, 256])
    y = helper.make_tensor_value_info("y", TensorProto.FLOAT, [256, 256])
    w_init = numpy_helper.from_array(
        (np.arange(256 * 256, dtype=np.float32) / 65536.0).reshape(256, 256), "w")
    node1 = helper.make_node("MatMul", ["a", "w"], ["mw"], name="canary_matmul")
    node2 = helper.make_node("Add", ["mw", "b"], ["y"], name="canary_add")
    graph = helper.make_graph([node1, node2], "canary", [a, b], [y], [w_init])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 9
    onnx.checker.check_model(model)
    onnx.save(model, str(p))
    return p
