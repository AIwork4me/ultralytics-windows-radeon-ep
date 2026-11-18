"""Freeze M2 evidence bundle for the gfx1201 diagnostic run (run_id fixed).

Collects: HOST_FINGERPRINT, EP_INSTALL_AND_PROBE (catalog + MSIX + ORT EPs),
DEVICE_IDENTITY (DXGI+PNP+resolution incl. gfx1201 BLOCKED receipt),
GPU_CANARY (amdgpu + dml results from unified_canary run), MODEL_TASK_MATRIX
(honest NOT_RUN row), manifest. Redaction: no hostnames/user paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE_BASE = Path(__file__).resolve().parents[1] / "reports/gfx1201"
RUN = None  # set only after immutable run_id validation
SRC = Path(__file__).resolve().parents[1] / "reports/gfx1100/unified_canary_20261118T2359Z"


def w(name: str, obj) -> None:
    if RUN is None:
        raise RuntimeError("run directory has not been initialized")
    RUN.mkdir(parents=True, exist_ok=True)
    target = RUN / name
    if target.exists():
        raise FileExistsError(f"IMMUTABLE_RECEIPT: {name} already exists")
    if name.endswith(".csv"):
        if not isinstance(obj, str):
            raise TypeError("CSV content must be a string")
        target.write_text(obj, encoding="utf-8", newline="")
    else:
        target.write_text(json.dumps(obj, indent=1, default=str, ensure_ascii=False),
                          encoding="utf-8", newline="")
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
    global RUN
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True, help="new immutable GFX1201_DIAG_* run")
    ap.add_argument("--canary-run-dir", default=str(SRC),
                    help="matching locally generated gfx1100/gfx1201 canary receipts")
    args = ap.parse_args()
    if not re.fullmatch(r"GFX1201_DIAG_[0-9]{8}T[0-9]{4}Z(?:_[a-zA-Z0-9-]+)?",
                        args.run_id):
        raise ValueError("invalid run ID; use a new GFX1201_DIAG_<UTC> name")
    RUN = EVIDENCE_BASE / args.run_id
    if RUN.exists() and any(RUN.iterdir()):
        print(f"BLOCKED_IMMUTABLE_RUN: {RUN} exists; use a new --run-id",
              file=sys.stderr)
        return 65
    canary_root = Path(args.canary_run_dir)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    vc = ps("Get-CimInstance Win32_VideoController | Select-Object Name,PNPDeviceID,"
            "DriverVersion | ConvertTo-Json -Compress")
    build = ps("(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion').DisplayVersion"
               " + ' build ' + (Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion').CurrentBuildNumber")
    appx = ps("(Get-AppxPackage Microsoft.WinML.AMD.GPU.EP.Framework.2).Version")
    appx_loc = ps("(Get-AppxPackage Microsoft.WinML.AMD.GPU.EP.Framework.2).InstallLocation")
    # Never publish hardware-instance suffixes, full PnP IDs, or host-specific IDs.
    video = json.loads(vc) if vc.startswith(("[", "{")) else []
    if isinstance(video, dict):
        video = [video]
    video = [
        {"Name": item.get("Name"), "DriverVersion": item.get("DriverVersion"),
         "PnP_VID_DID": (
             re.search(r"VEN_[0-9A-F]{4}&DEV_[0-9A-F]{4}",
                       item.get("PNPDeviceID", ""), flags=re.IGNORECASE).group(0)
             if re.search(r"VEN_[0-9A-F]{4}&DEV_[0-9A-F]{4}",
                          item.get("PNPDeviceID", ""), flags=re.IGNORECASE)
             else "NON_PCI_OR_REDACTED")}
        for item in video if isinstance(item, dict)]
    # Derive actual hardware from live PnP; never hardcode this host's gfx1100.
    from radeon_ort.device_identity import GFX_DEVICE_IDS
    observed_dids = set()
    for item in video:
        m = re.search(r"DEV_([0-9A-Fa-f]{4})", item["PnP_VID_DID"])
        if "VEN_1002" in item["PnP_VID_DID"].upper() and m:
            observed_dids.add(int(m.group(1), 16))
    observed_gfx = next((gfx for gfx in ("gfx1201", "gfx1100", "gfx1151")
                         if observed_dids & ids),
                        None)
    has_1201 = observed_gfx == "gfx1201"
    w("HOST_FINGERPRINT.json", {
        "captured_utc": now,
        "os": build,
        "video_controllers_redacted": video,
        "amd_ep_msix_version": appx,
        "mission_premise_check": {
            "expected_by_mission": "RX 9070 XT (gfx1201, DEV_7550/7551)",
            "actual_amd_gfx": observed_gfx or "NOT_DETECTED",
            "observed_amd_device_ids": sorted(hex(did) for did in observed_dids),
            "conclusion": ("GFX1201_PRESENT_TASKS_AND_SOAK_PENDING"
                           if has_1201 else "NO_GFX1201_HARDWARE_ON_THIS_HOST"),
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
    from radeon_ort.rca2a_ep import _ep_device_metadata, physical_luid_key, select_amdgpu_ep_device
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
    # Audit current observed GPU only; a run on gfx1100 cannot provide
    # physical gfx1201 evidence. No PR #6 module is required to execute this.
    found_gfx = next((gfx for gfx in ("gfx1201", "gfx1100", "gfx1151")
                      if resolve_target_adapter(adapters, gfx=gfx).get("did_match")),
                     None)
    d["host_matched_gfx"] = found_gfx
    if found_gfx:
        chosen = resolve_target_adapter(adapters, gfx=found_gfx)
        adapter = chosen["adapter"]
        luid = physical_luid_key(adapter)
        try:
            dev, sel = select_amdgpu_ep_device(luid)
            md = _ep_device_metadata(dev) if dev is not None else {}
            confirmed = (luid is not None and chosen.get("luid_valid")
                         and str(md.get("LUID", "")) == str(luid))
            d["ep_device_binding"] = {
                "confidence": "PHYSICAL_BINDING_CONFIRMED" if confirmed else "UNPROVEN",
                "luid_match": bool(confirmed),
                "metadata": md, "selection_basis": sel.get("basis")}
        except Exception as exc:  # noqa: BLE001 — report failure without an invalid PASS
            d["ep_device_binding"] = {
                "confidence": "UNPROVEN",
                "reason": f"EP-device probe unavailable: {type(exc).__name__}"}
    w("DEVICE_IDENTITY.json", d)

    can = {}
    for name, key in (("AMDGPU_CANARY.json", "amdgpu"), ("DML_CANARY.json", "dml"),
                      ("GFX1201_BLOCKED.json", "gfx1201_request")):
        p = canary_root / name
        if p.is_file():
            can[key] = json.loads(p.read_text(encoding="utf-8"))
    # Bind copied canaries to actual host architecture; no recycled gfx1100
    # PASS may ever be re-labelled as gfx1201.
    for key in ("amdgpu", "dml"):
        if key in can and can[key].get("gfx") != found_gfx:
            raise RuntimeError(
                "CANARY_GFX_MISMATCH: refusing cross-arch evidence copy")
    if "gfx1201_request" in can and found_gfx == "gfx1201":
        raise RuntimeError(
            "STALE_BLOCKED_RECEIPT: supply a fresh gfx1201 canary, not old gfx1100 error")
    w("GPU_CANARY.json", {
        "captured_utc": now,
        "hardware_gfx": found_gfx or "NOT_DETECTED",
        "label": ("GFX1201_CANARY_ONLY_TASKS_AND_SOAK_NOT_RUN"
                  if found_gfx == "gfx1201"
                  else "CODE_PATH_VALIDATION_ONLY_NOT_GFX1201_EVIDENCE"),
        **can})

    reason = ("PENDING_REAL_GFX1201_TASK_RECEIPTS" if found_gfx == "gfx1201"
              else "NO_GFX1201_HARDWARE_ON_HOST")
    rows = ["task,requested_gfx,executed_ep,status,reason"]
    for task in ("detect", "segment", "classify", "pose", "obb", "semantic", "depth"):
        for ep in ("amdgpu", "dml"):
            rows.append(f"{task},gfx1201,{ep},NOT_RUN,{reason}")
    for ep in ("amdgpu", "dml"):
        if ep in can:
            status = can[ep].get("status", "UNVERIFIED")
            rows.append(f"canary_tiny_conv,{found_gfx},{ep},{status},canary_only")
    w("MODEL_TASK_MATRIX.csv", "\n".join(rows) + "\n")

    files = sorted(p for p in RUN.iterdir() if p.is_file() and p.name != "manifest.sha256")
    manifest = "\n".join(f"{sha(p)}  {p.name}" for p in files) + "\n"
    (RUN / "manifest.sha256").write_text(manifest, encoding="utf-8", newline="")
    print("froze", len(files), "files + manifest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
