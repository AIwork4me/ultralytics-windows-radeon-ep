"""AMDGPU EP session creation (Windows ML 2.x / onnxruntime-windowsml).

Live-verified mechanics on gfx1151 (Ryzen AI MAX, dual DXGI enumeration):

1. Register once per process:
     ort.register_execution_provider_library(
         "AMDGPU", r"...WindowsApps\\MicrosoftCorporationII.WinML.AMD.GPU.EP...\\ExecutionProvider\\amdgpu-ep.dll")
   The registered provider name is "AMDGPU" (NOT "AMDGPUExecutionProvider").
   A wrong name makes ORT's python wrapper SILENTLY rebuild a CPU-only session
   (observed live; only the profiler catches it).

2. The 8060S appears as TWO AMDGPU OrtEpDevices among 5 total ep-devices (live
   2026-11-17: global indices 3 and 4). Candidate[0] deterministically fails
   session-create with HIP 101 (invalid device ordinal, GPU=0; see
   AMDGPU-RCA-003) and candidate[1] executes on the GPU. We try devices in
   order and ACCEPT ONLY a session whose probe-run profile shows positive
   AMDGPU node count — fail-closed selection, never a blind first-device
   fallback.

3. provider_options on the classic path (providers=["AMDGPU"]) are rejected by
   this EP build ("Unknown provider option"), so the ep-device route above is
   the supported mechanism.

R1-C contract: create_amdgpu_session returns an AmdgpuSessionResult carrying
the session AND a validated selection attestation:
  - every candidate's metadata + outcome (exception text incl. HIP code),
  - the chosen ep-device (candidate index, vendor, metadata, options),
  - probe evidence (AMDGPU node count from the probe profile),
  - FINAL-session revalidation: after rebuilding the final session it runs the
    same feeds once with profiling ON and re-derives provider attribution —
    a probe success can never certify a final session that fell back,
  - attribution honesty: 1 fused EP node is labeled fused_single_node (graph
    fusion evidence), NOT per-source-node execution proof,
  - phase timings (ns clock) for the R1-F session-creation audit.

EP_DEVICE_SELECTED vs PHYSICAL_GPU_IDENTITY_CORROBORATED: the ep-device API
exposes no LUID/ordinal on this build (identical metadata for both candidates,
see AMDGPU-RCA-004), so physical corroboration is established by the CALLER
from DXGI/PnP + canary evidence and recorded as dxgi_corroboration; the
attestation itself only proves which ep-device executed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path


def register_amdgpu_ep_library(dll_path: str | Path) -> str:
    """Register the AMD GPU EP library. Returns the provider name used."""
    import onnxruntime as ort
    ort.register_execution_provider_library("AMDGPU", str(dll_path))
    return "AMDGPU"


def ensure_amdgpu_registered() -> str:
    """Register the EP library if no AMDGPU ep-device exists yet.

    Returns the DLL path used, or raises RuntimeError (BLOCKED_EP) when the
    library cannot be found in site-packages or WindowsApps.
    """
    from radeon_ort import windows_ml_ep_catalog
    if amdgpu_ep_devices():
        return "already-registered"
    dlls = windows_ml_ep_catalog._rederive_real_paths()
    if not dlls:
        raise RuntimeError(
            "BLOCKED_EP: amdgpu-ep.dll not found in site-packages or WindowsApps")
    register_amdgpu_ep_library(dlls[-1])
    return dlls[-1]


def amdgpu_ep_devices():
    """All registered AMDGPU OrtEpDevices (len==2 on dual-enumerated gfx1151)."""
    import onnxruntime as ort
    return [d for d in ort.get_ep_devices() if d.ep_name == "AMDGPU"]


def _dev_metadata(dev) -> dict:
    return {
        "ep_name": getattr(dev, "ep_name", None),
        "ep_vendor": getattr(dev, "ep_vendor", None),
        "ep_options": dict(getattr(dev, "ep_options", None) or {}),
        "ep_metadata": dict(getattr(dev, "ep_metadata", None) or {}),
    }


def _hip_code_from_error(exc: BaseException) -> str | None:
    text = f"{type(exc).__name__}: {exc}"
    if "HIP failure" in text or "hip" in text.lower():
        import re
        m = re.search(r"HIP failure (\d+)", text)
        if m:
            return m.group(1)
    return None


@dataclass
class AmdgpuSessionResult:
    session: object
    attestation: dict = field(default_factory=dict)

    # passthrough so existing callers can treat it as the session where handy
    def run(self, *a, **kw):
        return self.session.run(*a, **kw)

    def get_providers(self):
        return self.session.get_providers()

    def get_outputs(self):
        return self.session.get_outputs()

    def get_inputs(self):
        return self.session.get_inputs()

    def end_profiling(self):
        return self.session.end_profiling()


def create_amdgpu_session(onnx_path: str | Path, *, enable_profiling: bool = True,
                          profile_prefix: str = "amdgpu_profile",
                          feeds_for_validation: dict | None = None,
                          device_candidates: list | None = None,
                          revalidate_final: bool = True) -> AmdgpuSessionResult:
    """Create an AMDGPU-EP session bound to a working ep-device + attestation.

    Tries each candidate OrtEpDevice in order; a candidate is accepted only
    after a probe run succeeds AND the profiler attributes compute to AMDGPU
    (when feeds_for_validation is provided). The final rebuilt session is
    revalidated the same way. If every candidate fails, raises the last error —
    never falls back to a CPU session.
    """
    import onnxruntime as ort

    t = {"start": time.perf_counter_ns()}
    devices = device_candidates if device_candidates is not None else amdgpu_ep_devices()
    if not devices:
        raise RuntimeError("BLOCKED_EP: no registered AMDGPU ep-device; register amdgpu-ep.dll first")
    all_devices = ort.get_ep_devices()
    global_indices = {id(d): i for i, d in enumerate(all_devices)}

    attestation: dict = {
        "ep": "AMDGPU",
        "candidates_tried": 0,
        "candidate_log": [],
        "state": "IDENTITY_BINDING_UNPROVEN",
        "selected_candidate_index": None,
        "selected_ep_device": None,
        "probe": {"amdgpu_nodes": 0, "cpu_nodes": 0, "profile": None},
        "final_profile": None,
        "attribution": "none",
        "model_compiled_twice": False,
        "phase_timings_ns": None,
    }

    last_err: Exception | None = None
    for cand_idx, dev in enumerate(devices):
        attestation["candidates_tried"] += 1
        entry = {"candidate_index": cand_idx,
                 "global_ep_device_index": global_indices.get(id(dev)),
                 "metadata": _dev_metadata(dev), "outcome": None, "hip_code": None}
        attestation["candidate_log"].append(entry)
        so = ort.SessionOptions()
        # probe session ALWAYS profiles: the fail-closed device probe requires
        # profiler evidence of AMDGPU compute (cannot be disabled)
        so.enable_profiling = True
        so.profile_file_prefix = f"{profile_prefix}_probe_c{cand_idx}"
        so.log_severity_level = 3
        so.add_provider_for_devices([dev], {})
        try:
            t_c = time.perf_counter_ns()
            sess = ort.InferenceSession(str(onnx_path), sess_options=so)
            entry["session_create_ms"] = (time.perf_counter_ns() - t_c) / 1e6
            if feeds_for_validation is None:
                # no probe evidence possible; caller opted out (canary-only path)
                attestation["selected_candidate_index"] = cand_idx
                attestation["selected_ep_device"] = _dev_metadata(dev)
                attestation["state"] = "EP_DEVICE_SELECTED_PROBELESS"
                attestation["model_compiled_twice"] = False
                attestation["phase_timings_ns"] = _phases(t, time.perf_counter_ns())
                return AmdgpuSessionResult(sess, attestation)
            probe = _probe_session(sess, feeds_for_validation, f"{profile_prefix}_probe_c{cand_idx}")
            entry["probe_counts"] = probe["counts"]
            if probe["amdgpu_nodes"] > 0:
                # rebuild with the final (non-probe) profile prefix
                t_r = time.perf_counter_ns()
                so2 = ort.SessionOptions()
                if enable_profiling:
                    so2.enable_profiling = True
                    so2.profile_file_prefix = profile_prefix
                so2.log_severity_level = 3
                so2.add_provider_for_devices([dev], {})
                final = ort.InferenceSession(str(onnx_path), sess_options=so2)
                attestation["rebuild_ms"] = (time.perf_counter_ns() - t_r) / 1e6
                attestation["model_compiled_twice"] = True

                attestation["probe"] = {"amdgpu_nodes": probe["amdgpu_nodes"],
                                        "cpu_nodes": probe["counts"].get("CPU", 0),
                                        "profile": probe["profile"]}
                # FINAL revalidation: run once with profiling and re-derive
                # attribution — a probe win can never certify a silent fallback.
                if revalidate_final and enable_profiling:
                    rev = _probe_session(final, feeds_for_validation, profile_prefix)
                    final_counts = rev["counts"]
                    if rev["amdgpu_nodes"] <= 0:
                        raise RuntimeError(
                            "AMDGPU_FAILED: final session fell back after probe "
                            f"(final profile AMDGPU nodes={rev['amdgpu_nodes']}, "
                            f"counts={final_counts})")
                else:
                    final_counts = probe["counts"]
                amd = final_counts.get("AMDGPU", 0)
                attestation["final_profile"] = {
                    "provider_node_counts": dict(final_counts),
                    "amdgpu_nodes": amd,
                }
                attestation["attribution"] = (
                    "fused_single_node" if amd == 1 else
                    "multi_node" if amd > 1 else "none")
                attestation["selected_candidate_index"] = cand_idx
                attestation["selected_ep_device"] = _dev_metadata(dev)
                attestation["state"] = "EP_DEVICE_SELECTED"
                attestation["phase_timings_ns"] = _phases(t, time.perf_counter_ns())
                return AmdgpuSessionResult(final, attestation)
            entry["outcome"] = (f"probe executed with {probe['amdgpu_nodes']} AMDGPU "
                                f"profile nodes (no GPU compute)")
            last_err = RuntimeError(entry["outcome"])
        except Exception as e:  # noqa: BLE001
            entry["outcome"] = f"{type(e).__name__}: {e}"[:500]
            entry["hip_code"] = _hip_code_from_error(e)
            last_err = e
            continue
    attestation["phase_timings_ns"] = _phases(t, time.perf_counter_ns())
    raise RuntimeError(f"AMDGPU_FAILED: all {len(devices)} ep-device candidate(s) failed; "
                       f"last error: {last_err}")


def _phases(t0: dict, t_end: int) -> dict:
    return {"total_ms": (t_end - t0["start"]) / 1e6}


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
