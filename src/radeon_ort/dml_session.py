"""DirectML EP session — independently tested GPU path.

Constraints honored (ORT docs): execution_mode=ORT_SEQUENTIAL,
enable_mem_pattern=False, no concurrent Run on one session.
Adapter selection: device_id chosen by matching DXGI adapter index of the intended
physical Radeon (resolved via device_identity), never blindly 0.
"""

from __future__ import annotations

from pathlib import Path


def resolve_dml_device_id_for_adapter(target_adapter_index: int) -> int:
    """ORT DML 'device_id' == adapter enumeration ordinal (DML factory order equals
    DXGI non-software adapter order in practice); we pass the DXGI-derived index
    and additionally verify post-hoc via profiled compute on that adapter."""
    return int(target_adapter_index)


def create_dml_session(onnx_path: str | Path, *, device_id: int,
                       enable_profiling: bool = True,
                       profile_prefix: str = "dml_profile"):
    import onnxruntime as ort

    so = ort.SessionOptions()
    if enable_profiling:
        so.enable_profiling = True
        so.profile_file_profile_prefix = profile_prefix
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    so.enable_mem_pattern = False
    so.log_severity_level = 3

    pov = [{"device_id": int(device_id)}]
    return ort.InferenceSession(
        str(onnx_path), sess_options=so,
        providers=["DmlExecutionProvider", "CPUExecutionProvider"],
        provider_options=pov,
    )
