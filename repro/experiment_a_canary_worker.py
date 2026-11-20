"""Experiment A canary worker — one EP attempt in one fresh process.

Used by A3 (direct MSIX DLL registration) and A4 (catalog-provided library
path, if any). Mission A_GFX1100_CATALOG_VS_DIRECT_REGISTRATION.

Runs under the MISSION venv (onnxruntime-windowsml build with
register_execution_provider_library). Fail-closed: any error exits non-zero
with a JSON error receipt; a CPU session can never be reported as GPU.

Usage:
  python -I repro/experiment_a_canary_worker.py \
      --onnx <path> --npy <path> --dll <path> --alias AMDGPUExecutionProvider \
      --luid 72346 --gfx gfx1100 --out-npz out.npz --out-json worker.json \
      [--bootstrap byo_msix|catalog] [--bootstrap-note "..."]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

TARGET_EP_KEYS = ("AMDGPU", "AMDGPUExecutionProvider")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx", required=True)
    ap.add_argument("--npy", required=True)
    ap.add_argument("--dll", required=True)
    ap.add_argument("--alias", default="AMDGPUExecutionProvider")
    ap.add_argument("--luid", default=None, help="decimal LUID key of physical adapter")
    ap.add_argument("--gfx", default="gfx1100")
    ap.add_argument("--out-npz", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--bootstrap", default="byo_msix",
                    choices=("byo_msix", "catalog"))
    ap.add_argument("--bootstrap-note", default="")
    a = ap.parse_args()

    receipt: dict = {"worker": "experiment_a_canary_worker",
                     "bootstrap": a.bootstrap, "bootstrap_note": a.bootstrap_note,
                     "alias": a.alias, "gfx": a.gfx}
    try:
        import numpy as np
        import onnxruntime as ort

        from radeon_ort import device_identity, profiler_audit, rca2a_ep

        onnx_p, npy_p, dll_p = Path(a.onnx), Path(a.npy), Path(a.dll)
        receipt["onnx_sha256"] = sha256_file(onnx_p)
        receipt["dll_sha256"] = sha256_file(dll_p)
        receipt["dll_name"] = dll_p.name

        # 1) register the DLL in THIS process (direct, explicit)
        ort.register_execution_provider_library(a.alias, str(dll_p))
        ep_devices = [d for d in ort.get_ep_devices()
                      if d.ep_name == a.alias]
        receipt["ep_devices_after_register"] = [
            {"ep_name": d.ep_name, "ep_vendor": d.ep_vendor,
             "ep_metadata": dict(d.ep_metadata or {}),
             "device_metadata": rca2a_ep._ep_device_metadata(d)}
            for d in ep_devices]
        receipt["direct_ep_registered"] = bool(ep_devices)

        # 2) physical identity: DXGI DID-pinned resolution + EP-device LUID match
        adapters = device_identity.enum_dxgi_adapters()
        res = device_identity.resolve_target_adapter(adapters, gfx=a.gfx)
        if not res.get("ok"):
            raise RuntimeError(f"device identity failed: {res.get('reason')}")
        adapter = res["adapter"]
        luid_key = rca2a_ep.physical_luid_key(adapter)
        receipt["device_identity"] = {
            "ok": True, "gfx": a.gfx,
            "description": adapter.description,
            "vendor_id": f"0x{adapter.vendor_id:04X}",
            "device_id": f"0x{adapter.device_id:04X}",
            "luid_str": adapter.luid_str, "luid_key": luid_key,
            "identity_method": res.get("identity_method"),
        }
        want_luid = int(a.luid) if a.luid else luid_key
        matching = [d for d in ep_devices
                    if str(rca2a_ep._ep_device_metadata(d).get("LUID", ""))
                    == str(want_luid)]
        receipt["ep_device_luid_match"] = {
            "wanted": str(want_luid),
            "matches": len(matching),
            "ep_device_luids": [str(rca2a_ep._ep_device_metadata(d).get("LUID", ""))
                                for d in ep_devices],
            "verified_gfx1100": (
                adapter.device_id == 0x744C and len(matching) >= 1),
        }

        # 3) LUID-pinned AMDGPU-only session (never CPU)
        x = np.load(npy_p)
        sess = rca2a_ep.create_amdgpu_session(
            str(onnx_p), enable_profiling=True,
            profile_prefix=str(Path(a.out_npz).parent / "amdgpu_profile"),
            target_luid=want_luid)
        receipt["session_providers_metadata_only"] = sess.get_providers()

        # 4) fixed real input, run, capture outputs
        feeds = {sess.get_inputs()[0].name: np.ascontiguousarray(x, dtype=np.float32)}
        sess.run(None, feeds)  # warm (compile/tune)
        outs = sess.run(None, feeds)
        out_names = [o.name for o in sess.get_outputs()]
        np.savez(a.out_npz, **{n: v for n, v in zip(out_names, outs)})

        # 5) profiler audit
        prof = sess.end_profiling()
        audit = profiler_audit.audit_profile(prof)
        counts = audit["provider_node_counts"]
        receipt["profile"] = {
            "provider_node_counts": counts,
            "op_samples": audit["op_samples"][:12],
            "total_events": audit["total_events"],
        }
        target_nodes = sum(counts.get(k, 0) for k in TARGET_EP_KEYS)
        cpu_nodes = counts.get("CPU", 0) + counts.get("cpu", 0)
        receipt["target_ep_profile_nodes"] = target_nodes
        receipt["cpu_profile_nodes"] = cpu_nodes

        receipt["status"] = "GPU_RUN_COMPLETE"
        Path(a.out_json).write_text(
            json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
        return 0
    except Exception as e:  # noqa: BLE001 — failure receipt, never a fake PASS
        receipt["status"] = "WORKER_FAILED"
        receipt["error"] = f"{type(e).__name__}: {e}"
        receipt["traceback_tail"] = traceback.format_exc()[-1500:]
        Path(a.out_json).write_text(
            json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
        return 3


if __name__ == "__main__":
    sys.exit(main())
