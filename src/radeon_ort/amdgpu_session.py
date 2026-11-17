"""AMDGPU EP session creation (Windows ML 2.x / onnxruntime-windowsml).

Live-verified mechanics on gfx1151 (Ryzen AI MAX, dual DXGI enumeration):

1. Register once per process:
     ort.register_execution_provider_library(
         "AMDGPU", r"...WindowsApps\\MicrosoftCorporationII.WinML.AMD.GPU.EP...\\ExecutionProvider\\amdgpu-ep.dll")
   The registered provider name is "AMDGPU" (NOT "AMDGPUExecutionProvider").
   A wrong name makes ORT's python wrapper SILENTLY rebuild a CPU-only session
   (observed live; only the profiler catches it).

2. The 8060S appears as TWO OrtEpDevices (matching its two DXGI enumerations).
   On this host dev[0] fails with HIP 101 (invalid device ordinal) and dev[1]
   executes on the GPU (profiler: {'AMDGPU': n}). We therefore try devices in
   order and ACCEPT ONLY a session whose probe-run profile shows positive
   AMDGPU node count — fail-closed selection, never a blind first-device
   fallback.

3. provider_options on the classic path (providers=["AMDGPU"]) are rejected by
   this EP build ("Unknown provider option"), so the ep-device route above is
   the supported mechanism.
"""

from __future__ import annotations

from pathlib import Path


def register_amdgpu_ep_library(dll_path: str | Path) -> str:
    """Register the AMD GPU EP library. Returns the provider name used."""
    import onnxruntime as ort
    ort.register_execution_provider_library("AMDGPU", str(dll_path))
    return "AMDGPU"


def amdgpu_ep_devices():
    """All registered AMDGPU OrtEpDevices (len==2 on dual-enumerated gfx1151)."""
    import onnxruntime as ort
    return [d for d in ort.get_ep_devices() if d.ep_name == "AMDGPU"]


def create_amdgpu_session(onnx_path: str | Path, *, enable_profiling: bool = True,
                          profile_prefix: str = "amdgpu_profile",
                          feeds_for_validation: dict | None = None,
                          device_candidates: list | None = None):
    """Create an AMDGPU-EP session bound to a working ep-device.

    Tries each candidate OrtEpDevice in order; a candidate is accepted only
    after a probe run succeeds AND the profiler attributes compute to AMDGPU
    (when feeds_for_validation is provided). If every candidate fails, raises
    the last error — never falls back to a CPU session.
    """
    import onnxruntime as ort

    devices = device_candidates if device_candidates is not None else amdgpu_ep_devices()
    if not devices:
        raise RuntimeError("BLOCKED_EP: no registered AMDGPU ep-device; register amdgpu-ep.dll first")

    last_err: Exception | None = None
    for dev in devices:
        so = ort.SessionOptions()
        # probe session ALWAYS profiles: the fail-closed device probe requires
        # profiler evidence of AMDGPU compute (cannot be disabled)
        so.enable_profiling = True
        so.profile_file_prefix = f"{profile_prefix}_probe"
        so.log_severity_level = 3
        so.add_provider_for_devices([dev], {})
        try:
            sess = ort.InferenceSession(str(onnx_path), sess_options=so)
            if feeds_for_validation is None:
                return sess
            probe = _probe_session(sess, feeds_for_validation, profile_prefix)
            if probe["amdgpu_nodes"] > 0:
                # rebuild with the final (non-probe) profile prefix
                so2 = ort.SessionOptions()
                if enable_profiling:
                    so2.enable_profiling = True
                    so2.profile_file_prefix = profile_prefix
                so2.log_severity_level = 3
                so2.add_provider_for_devices([dev], {})
                final = ort.InferenceSession(str(onnx_path), sess_options=so2)
                return final
            last_err = RuntimeError(
                f"ep-device executed with {probe['amdgpu_nodes']} AMDGPU profile nodes (no GPU compute)")
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
    raise RuntimeError(f"AMDGPU_FAILED: all {len(devices)} ep-device candidate(s) failed; "
                       f"last error: {last_err}")


def _probe_session(sess, feeds: dict, profile_prefix: str) -> dict:
    import numpy as np
    from . import profiler_audit
    f32 = {k: np.asarray(v, dtype=np.float32) for k, v in feeds.items()}
    sess.run(None, f32)
    prof = sess.end_profiling()
    counts = profiler_audit.audit_profile(prof)["provider_node_counts"]
    return {"amdgpu_nodes": counts.get("AMDGPU", 0), "counts": counts, "profile": prof}


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
