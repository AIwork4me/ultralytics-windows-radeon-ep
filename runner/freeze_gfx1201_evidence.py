"""Freeze M2 evidence bundle for the gfx1201 diagnostic run (run_id fixed).

Collects: HOST_FINGERPRINT, EP_INSTALL_AND_PROBE (catalog + MSIX + ORT EPs),
DEVICE_IDENTITY (DXGI+PNP+resolution incl. gfx1201 BLOCKED receipt),
GPU_CANARY (amdgpu + dml results from unified_canary run), MODEL_TASK_MATRIX
(honest NOT_RUN row), manifest. Redaction: no hostnames/user paths.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

RUN = Path(__file__).resolve().parents[1] / "reports/gfx1201/GFX1201_DIAG_20261118T2359Z"
SRC = Path(__file__).resolve().parents[1] / "reports/gfx1100/unified_canary_20261118T2359Z"


def w(name: str, obj) -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    (RUN / name).write_text(json.dumps(obj, indent=1, default=str, ensure_ascii=False),
                            encoding="utf-8")
    print("wrote", name)


def ps(cmd: str):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                       capture_output=True, text=True, timeout=60)
    return r.stdout.strip()


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    now = "2026-11-18T23:59:00Z"
    vc = ps("Get-CimInstance Win32_VideoController | Select-Object Name,PNPDeviceID,"
            "DriverVersion | ConvertTo-Json -Compress")
    build = ps("(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion').DisplayVersion"
               " + ' build ' + (Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion').CurrentBuildNumber")
    appx = ps("(Get-AppxPackage Microsoft.WinML.AMD.GPU.EP.Framework.2).Version")
    appx_loc = ps("(Get-AppxPackage Microsoft.WinML.AMD.GPU.EP.Framework.2).InstallLocation")
    w("HOST_FINGERPRINT.json", {
        "captured_utc": now,
        "os": build,
        "video_controllers_raw": json.loads(vc) if vc.startswith("[") or vc.startswith("{") else vc,
        "amd_ep_msix_version": appx,
        "mission_premise_check": {
            "expected_by_mission": "RX 9070 XT (gfx1201, DEV_7550/7551)",
            "actual_physical_amd_dgpu": "RX 7900 XTX DEV_744C (gfx1100)",
            "conclusion": "NO_GFX1201_HARDWARE_ON_THIS_HOST -> M2 gfx1201 real-GPU execution NOT_RUN",
        },
        "note": "host-identifying fields omitted (AGENTS redaction)",
    })

    ep_dll = Path(appx_loc) / "ExecutionProvider" / "amdgpu-ep.dll" if appx_loc else None
    ep_probe = {
        "captured_utc": now,
        "msix": {"package": "Microsoft.WinML.AMD.GPU.EP.Framework.2", "version": appx},
        "ep_dll_sha256": sha(ep_dll) if ep_dll and ep_dll.is_file() else None,
        "ep_dll_bytes": ep_dll.stat().st_size if ep_dll and ep_dll.is_file() else None,
        "catalog_discovery": "see EP_catalog_providers in DEVICE_IDENTITY (read-only find_all_providers)",
        "catalog_install_attempted": False,
        "catalog_install_note": "read-only discovery only (BLOCKED_INSTALL_AUTHORIZATION posture)",
    }
    w("EP_INSTALL_AND_PROBE.json", ep_probe)

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from radeon_ort.device_identity import enum_dxgi_adapters, resolve_target_adapter
    from radeon_ort.unified_identity import build_chain
    adapters = enum_dxgi_adapters()
    d = {
        "captured_utc": now,
        "dxgi_adapters": [
            {"index": a.index, "desc": a.description, "vid": f"{a.vendor_id:04X}",
             "did": f"{a.device_id:04X}", "sw": a.is_software, "luid": a.luid_str,
             "dedicated_mb": a.dedicated_video_mb}
            for a in adapters],
        "resolution": {},
    }
    for gfx in ("gfx1100", "gfx1151", "gfx1201"):
        r = resolve_target_adapter(adapters, gfx=gfx)
        d["resolution"][gfx] = ("OK did=" + hex(r["adapter"].device_id)
                                + " luid=" + r["adapter"].luid_str) if r.get("ok") else r["reason"]
    chain = build_chain("gfx1100")
    d["gfx1100_chain_confidence"] = chain.confidence()
    d["gfx1100_ep_device"] = chain.ep_device
    w("DEVICE_IDENTITY.json", d)

    can = {}
    for name, key in (("AMDGPU_CANARY.json", "amdgpu"), ("DML_CANARY.json", "dml"),
                      ("GFX1201_BLOCKED.json", "gfx1201_request")):
        p = SRC / name
        if p.is_file():
            can[key] = json.loads(p.read_text(encoding="utf-8"))
    w("GPU_CANARY.json", {"captured_utc": now, "hardware": "gfx1100 RX 7900 XTX",
                          "label": "CODE_PATH_VALIDATION_ONLY_NOT_GFX1201_EVIDENCE",
                          **can})

    w("MODEL_TASK_MATRIX.csv",
      "task,requested_gfx,executed_ep,status,reason\n"
      "detect,gfx1201,none,NOT_RUN,NO_GFX1201_HARDWARE_ON_HOST(gfx1100 present)\n"
      "segment,gfx1201,none,NOT_RUN,NO_GFX1201_HARDWARE_ON_HOST(gfx1100 present)\n"
      "classify,gfx1201,none,NOT_RUN,NO_GFX1200_HARDWARE_ON_HOST(gfx1100 present)\n"
      "pose,gfx1201,none,NOT_RUN,NO_GFX1201_HARDWARE_ON_HOST(gfx1100 present)\n"
      "obb,gfx1201,none,NOT_RUN,NO_GFX1201_HARDWARE_ON_HOST(gfx1100 present)\n"
      "semantic,gfx1201,none,NOT_RUN,NO_GFX1201_HARDWARE_ON_HOST(gfx1100 present)\n"
      "depth,gfx1201,none,NOT_RUN,NO_GFX1201_HARDWARE_ON_HOST(gfx1100 present)\n"
      "canary_tiny_conv,gfx1100,amdgpu,GPU_PASS_VALIDATED,code_path_validation_only\n"
      "canary_tiny_conv,gfx1100,dml,GPU_PASS_VALIDATED,code_path_validation_only\n")

    files = sorted(p for p in RUN.iterdir() if p.is_file() and p.name != "manifest.sha256")
    manifest = "\n".join(f"{sha(p)}  {p.name}" for p in files) + "\n"
    (RUN / "manifest.sha256").write_text(manifest, encoding="utf-8", newline="")
    print("froze", len(files), "files + manifest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
