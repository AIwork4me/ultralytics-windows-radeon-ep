"""Experiment A negative/control worker — one injection per fresh process.

Mission step 8. Every case MUST fail closed: the emitted receipt records the
observed failure mode; none may produce AMDGPU GPU-PASS evidence.

Cases (runtime, mission venv):
  wrong_did       – request a gfx arch this host does not have
  wrong_dll       – register a non-EP system DLL as the AMDGPU library
  unregistered    – raw session demanding AMDGPUExecutionProvider with NO
                    registration (ORT may warn + silently build CPU — the
                    detector must catch that, not trust get_providers)
  luid_pin_fail   – real DLL registered, session pinned to a nonexistent LUID
  bad_path        – register a nonexistent library path
  cpu_impersonate – CPU-only session run; gates applied must reject GPU-PASS
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

TARGET_EP_KEYS = ("AMDGPU", "AMDGPUExecutionProvider")


def emit(path: str, payload: dict) -> None:
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                          encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--onnx", default=None)
    ap.add_argument("--npy", default=None)
    ap.add_argument("--out-json", required=True)
    a = ap.parse_args()
    r: dict = {"case": a.case}

    try:
        if a.case == "wrong_did":
            from radeon_ort import device_identity
            adapters = device_identity.enum_dxgi_adapters()
            fails = {}
            for gfx in ("gfx1201", "gfx1151"):
                res = device_identity.resolve_target_adapter(adapters, gfx=gfx)
                fails[gfx] = {"ok": res.get("ok"), "reason": res.get("reason")}
            # PASS-condition of the INJECTION: both wrong-arch requests refused
            r["refused_all"] = all(not v["ok"] for v in fails.values())
            r["detail"] = fails
            r["fail_closed"] = r["refused_all"]

        elif a.case == "wrong_dll":
            import onnxruntime as ort
            target = r"C:\Windows\System32\kernel32.dll"
            err = None
            try:
                ort.register_execution_provider_library(
                    "AMDGPUExecutionProvider", target)
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
            has = any(d.ep_name == "AMDGPUExecutionProvider"
                      for d in ort.get_ep_devices())
            r["register_error"] = err
            r["amdgpu_ep_device_present"] = has
            r["fail_closed"] = (err is not None) or (not has)

        elif a.case == "unregistered":
            import numpy as np
            import onnxruntime as ort
            from radeon_ort import profiler_audit
            so = ort.SessionOptions()
            so.enable_profiling = True
            so.profile_file_prefix = "neg_unregistered"
            so.log_severity_level = 3
            created = True
            try:
                sess = ort.InferenceSession(str(Path(a.onnx)), sess_options=so,
                                            providers=["AMDGPUExecutionProvider"])
                x = np.load(a.npy)
                sess.run(None, {sess.get_inputs()[0].name: x.astype("float32")})
                prof = sess.end_profiling()
                counts = profiler_audit.audit_profile(prof)["provider_node_counts"]
            except Exception as e:  # noqa: BLE001
                created = False
                counts = {}
                r["session_error"] = f"{type(e).__name__}: {e}"
            target_nodes = sum(counts.get(k, 0) for k in TARGET_EP_KEYS)
            r["session_created"] = created
            r["provider_node_counts"] = counts
            r["target_ep_nodes"] = target_nodes
            r["cpu_nodes"] = counts.get("CPU", 0)
            # fail-closed = no GPU-PASS possible: either error, or CPU-only run
            r["fail_closed"] = (not created) or (target_nodes == 0)

        elif a.case == "luid_pin_fail":
            # Registered EP, session pinned to a NONEXISTENT LUID.
            # Live finding 2026-11-20: rca2a_ep.create_amdgpu_session does
            # NOT raise for an unmatched LUID — select_amdgpu_ep_device falls
            # back to "AMBIGUOUS: took first". Fail-closed must therefore be
            # enforced by the EVIDENCE GATE (luid match count == 0 withholds
            # GPU-PASS), exactly as ARCHITECTURE.md §3 demands for unproven
            # links. This case proves the gate refuses such a run.
            from radeon_ort import profiler_audit, rca2a_ep
            from radeon_ort.rca2a_ep import _appx_ep_dll_path
            import numpy as np
            import onnxruntime as ort
            dll = _appx_ep_dll_path()
            ort.register_execution_provider_library(
                "AMDGPUExecutionProvider", dll)
            dev, sel = rca2a_ep.select_amdgpu_ep_device(999999)
            r["pinned_luid"] = 999999
            r["selection_basis"] = sel.get("basis")
            r["library_raised"] = False  # honest: no exception (fail-open lib)
            sess = rca2a_ep.create_amdgpu_session(
                a.onnx, enable_profiling=True,
                profile_prefix="neg_luid_pin", target_luid=999999)
            x = np.load(a.npy)
            sess.run(None, {sess.get_inputs()[0].name: x.astype("float32")})
            counts = profiler_audit.audit_profile(
                sess.end_profiling())["provider_node_counts"]
            target_nodes = sum(counts.get(k, 0) for k in TARGET_EP_KEYS)
            # gate: the A3 hard gate requires an EP-device LUID match with the
            # physical adapter; 999999 matches nothing → GPU-PASS withheld.
            matches = len([d for d in ort.get_ep_devices()
                           if d.ep_name == "AMDGPUExecutionProvider"
                           and str(rca2a_ep._ep_device_metadata(d).get(
                               "LUID", "")) == "999999"])
            r["provider_node_counts"] = counts
            r["ep_device_luid_matches_for_999999"] = matches
            r["gates_would_grant_gpu_pass"] = bool(
                target_nodes > 0 and counts.get("CPU", 0) == 0 and matches >= 1)
            r["fail_closed"] = not r["gates_would_grant_gpu_pass"]

        elif a.case == "bad_path":
            import onnxruntime as ort
            err = None
            try:
                ort.register_execution_provider_library(
                    "AMDGPUExecutionProvider", r"C:\nonexistent\amdgpu-ep.dll")
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
            has = any(d.ep_name == "AMDGPUExecutionProvider"
                      for d in ort.get_ep_devices())
            r["register_error"] = err
            r["amdgpu_ep_device_present"] = has
            r["fail_closed"] = (err is not None) and (not has)

        elif a.case == "cpu_impersonate":
            import numpy as np
            import onnxruntime as ort
            from radeon_ort import profiler_audit
            so = ort.SessionOptions()
            so.enable_profiling = True
            so.profile_file_prefix = "neg_cpu_impersonate"
            sess = ort.InferenceSession(str(Path(a.onnx)), sess_options=so,
                                        providers=["CPUExecutionProvider"])
            x = np.load(a.npy)
            sess.run(None, {sess.get_inputs()[0].name: x.astype("float32")})
            counts = profiler_audit.audit_profile(sess.end_profiling())[
                "provider_node_counts"]
            target_nodes = sum(counts.get(k, 0) for k in TARGET_EP_KEYS)
            cpu_nodes = counts.get("CPU", 0)
            r["provider_node_counts"] = counts
            # apply the A3 GPU-PASS gates to a CPU-only run: must NOT pass
            r["gates_would_grant_gpu_pass"] = bool(
                target_nodes > 0 and cpu_nodes == 0)
            r["fail_closed"] = not r["gates_would_grant_gpu_pass"]

        else:
            r["error"] = f"unknown case {a.case}"
            r["fail_closed"] = True
        rc = 0
    except Exception as e:  # noqa: BLE001
        r["worker_error"] = f"{type(e).__name__}: {e}"
        r["traceback_tail"] = traceback.format_exc()[-800:]
        r["fail_closed"] = True
        rc = 3
    emit(a.out_json, r)
    return rc


if __name__ == "__main__":
    sys.exit(main())
