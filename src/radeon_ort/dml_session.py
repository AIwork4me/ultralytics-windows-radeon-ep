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
                       profile_prefix: str = "dml_profile",
                       sess_options_override=None):
    import onnxruntime as ort

    # RCA2A-008: rca2a_worker passes supervisor-prepared SessionOptions
    # (profiling prefix / graph-opt level); when given, the override wins over
    # the enable_profiling/profile_prefix pair, and the DML-mandated flags
    # below are still enforced on it.
    if sess_options_override is not None:
        so = sess_options_override
    else:
        so = ort.SessionOptions()
        if enable_profiling:
            so.enable_profiling = True
            so.profile_file_prefix = profile_prefix
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    so.enable_mem_pattern = False
    so.log_severity_level = 3

    pov = [{"device_id": int(device_id)}, {}]
    sess = ort.InferenceSession(
        str(onnx_path), sess_options=so,
        providers=["DmlExecutionProvider", "CPUExecutionProvider"],
        provider_options=pov,
    )
    # ORT's python wrapper SILENTLY retries CPU when the DML provider fails to
    # initialize (e.g. an invalid device_id). Never return such a session as a
    # DML session: the caller would then measure CPU and label it GPU.
    getter = getattr(sess, "get_providers", None)
    if callable(getter):
        providers = list(getter())
        if "DmlExecutionProvider" not in providers:
            raise RuntimeError(
                f"DML_FAILED: wrapper fell back to CPU; providers={providers}, "
                f"requested device_id={device_id}")
    return sess
