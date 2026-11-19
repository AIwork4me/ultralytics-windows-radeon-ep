"""Real Windows host fingerprint collection (WMI via PowerShell, redacted output)."""

from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
from pathlib import Path

PS_SCRIPT = r"""
$ErrorActionPreference='SilentlyContinue'
$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
[PSCustomObject]@{
  os_edition       = $cv.EditionID
  display_version  = $cv.DisplayVersion
  build            = $cv.CurrentBuild
  ubr              = [int]$cv.UBR
} | ConvertTo-Json -Compress
$cs = Get-CimInstance Win32_ComputerSystem
$proc = Get-CimInstance Win32_Processor | Select-Object -First 1
[PSCustomObject]@{
  cpu_name = $proc.Name
  ram_gb   = [math]::Round($cs.TotalPhysicalMemory/1GB,1)
  model    = $cs.Model
} | ConvertTo-Json -Compress
Get-CimInstance Win32_VideoController | ForEach-Object {
  [PSCustomObject]@{
    name           = $_.Name
    driver_version = $_.DriverVersion
    pnp_device_id  = $_.PNPDeviceID
    status         = $_.Status
    adapter_ram    = $_.AdapterRAM
  } | ConvertTo-Json -Compress
}
Get-CimInstance Win32_PnPSignedDriver | Where-Object { $_.DeviceClass -eq 'DISPLAY' } | ForEach-Object {
  [PSCustomObject]@{
    device_name  = $_.DeviceName
    drv          = $_.DriverVersion
    drv_date     = ($_.DriverDate -as [string])
  } | ConvertTo-Json -Compress
}
Get-AppxPackage | Where-Object { $_.Name -match 'WindowsAppRuntime' } | Select-Object -ExpandProperty Version
# A non-terminating Appx/cmdlet error can leave a non-zero process exit code even
# though the fingerprint was emitted completely (observed on a clean 25H2 host).
# Success is judged by parseable output, not by this trailing exit code.
exit 0
"""


def _run_ps() -> str:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", PS_SCRIPT],
        capture_output=True, text=True, timeout=120,
    )
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    json_lines = [ln for ln in lines if ln.lstrip().startswith("{")]
    if r.returncode != 0 and len(json_lines) < 2:
        raise RuntimeError(f"powershell probe failed rc={r.returncode}: {r.stderr[:500]}")
    if r.returncode != 0:
        print(f"[host_probe] WARNING: powershell rc={r.returncode} but {len(json_lines)} "
              f"JSON records were emitted; continuing", file=sys.stderr)
    return r.stdout


def redact_pnp_id(pnp: str | None) -> str | None:
    """Keep the public hardware identification prefix only.

    A full PnP *instance* id embeds the machine-local device instance path
    (e.g. the trailing \\4&85C8329&0&0041), which is host-identifying. The
    vendor/device/subsystem/revision prefix is public hardware data.
    """
    if not pnp:
        return pnp
    text = str(pnp)
    head, sep, tail = text.rpartition("\\")
    if sep and re.match(r"^[0-9A-Fa-f]+&", tail):
        return head
    return text


def collect_host(gpu_name_needle: str | None = None, gfx: str | None = None,
                 gpu_device_ids: tuple[int, ...] | None = None) -> dict:
    """Collect and structure the host fingerprint.

    Target selection is DID-first when ``gpu_device_ids`` is supplied (the r1
    path does this per gfx target). A generic name needle is only a fallback and
    is never used to claim identity on its own. ``gfx`` is recorded as given, or
    "unknown" — the caller performs the DXGI DID-pinned attestation.
    """
    raw = _run_ps()
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    os_info = json.loads(lines[0])
    cpu_info = json.loads(lines[1])
    gpus = []
    idx = 2
    # video-controller records carry "name"; signed-driver records carry
    # "device_name". They are two consecutive JSON streams, not one.
    while idx < len(lines) and lines[idx].startswith("{"):
        obj = json.loads(lines[idx])
        if "name" not in obj:
            break
        gpus.append(obj)
        idx += 1
    drivers = []
    while idx < len(lines) and lines[idx].startswith("{"):
        obj = json.loads(lines[idx])
        if "device_name" not in obj:
            break
        drivers.append(obj)
        idx += 1
    runtimes = [ln for ln in lines[idx:] if ln and ln[0].isdigit()]

    def _pnp_parts(g: dict) -> tuple[str | None, int | None]:
        pnp = (g.get("pnp_device_id") or "").upper()
        m_ven = re.search(r"VEN_([0-9A-F]{4})", pnp)
        m_dev = re.search(r"DEV_([0-9A-F]{4})", pnp)
        return (f"0x{m_ven.group(1)}" if m_ven else None,
                int(m_dev.group(1), 16) if m_dev else None)

    target = None
    if gpu_device_ids:
        wanted = {int(d) for d in gpu_device_ids}
        target = next((g for g in gpus if (_pnp_parts(g)[1] in wanted)), None)
    if target is None and gpu_name_needle:
        needle = gpu_name_needle.lower()
        target = next((g for g in gpus if needle in (g.get("name") or "").lower()), None)

    def _with_ids(g: dict) -> dict:
        ven, did = _pnp_parts(g)
        arm = g.get("adapter_ram")
        return {**g, "pnp_device_id": redact_pnp_id(g.get("pnp_device_id")),
                "vendor_id": ven, "device_id": (f"0x{did:04X}" if did is not None else None),
                "adapter_ram_bytes": arm,
                "wmi_adapter_ram_saturated": (
                    isinstance(arm, int) and arm >= 0xFFF00000)}

    if target is None:
        return {
            "schema_version": 1,
            "captured_utc": __import__("radeon_ort.receipts", fromlist=["utc_now_iso"]).utc_now_iso(),
            "host_id": "unknown",
            "os": os_info, "cpu": cpu_info,
            "gpu_target": {"name": None, "gfx": gfx or "unknown", "verified": False,
                           "identity_method": "target-not-found",
                           "driver_version": "unknown"},
            "other_adapters": [_with_ids(g) for g in gpus],
            "windows_app_runtime_versions": sorted(set(runtimes)),
            "python": {"version": sys.version.split()[0], "implementation": platform.python_implementation(),
                       "executable_basename": Path(sys.executable).name},
            "tooling": {},
            "_gpu_list": gpus,
        }
    needle_for_driver = (target.get("name") or "").lower()
    drv_meta = next((d for d in drivers
                     if needle_for_driver and needle_for_driver in (d.get("device_name") or "").lower()), {})
    ven, did = _pnp_parts(target)
    arm = target.get("adapter_ram")
    host = {
        "schema_version": 1,
        "captured_utc": __import__("radeon_ort.receipts", fromlist=["utc_now_iso"]).utc_now_iso(),
        "os": os_info,
        "cpu": cpu_info,
        "gpu_target": {
            "name": target["name"],
            "gfx": gfx or "unknown",
            "vendor_id": ven,
            "device_id": (f"0x{did:04X}" if did is not None else None),
            "driver_version": target.get("driver_version"),
            "driver_date": (drv_meta.get("drv_date") or "").split(" ")[0] or None,
            "pnp_device_id": redact_pnp_id(target.get("pnp_device_id")),
            "status": target.get("status"),
            "adapter_ram_bytes": arm,
            "wmi_adapter_ram_saturated": (
                isinstance(arm, int) and arm >= 0xFFF00000),
            "verified": False,  # set True only by device_identity with LUID+canary
            "identity_method": "wmi-did-match (pending DXGI cross-check)",
        },
        "other_adapters": [_with_ids(g) for g in gpus if g is not target],
        "windows_app_runtime_versions": sorted(set(runtimes)),
        "python": {"version": sys.version.split()[0], "implementation": platform.python_implementation(),
                   "executable_basename": Path(sys.executable).name},
        "tooling": {},
        "_gpu_list": gpus,
    }
    host["host_id"] = host_id_from_host(host)
    return host


def host_id_from_host(host: dict) -> str:
    """Stable-but-non-identifying host id: CPU name + public PnP hardware id."""
    import hashlib
    key = ((host.get("cpu") or {}).get("cpu_name") or "?") + "|" + \
          ((host.get("gpu_target") or {}).get("pnp_device_id") or "?")
    return "host_" + hashlib.sha256(key.encode()).hexdigest()[:12]


def host_id_stable(gfx: str = "gfx1151") -> str:
    """Backwards-compatible stable host id helper (DID-pinned target)."""
    from .device_identity import target_device_ids
    h = collect_host(gpu_name_needle=None, gfx=gfx,
                     gpu_device_ids=target_device_ids(gfx))
    return h.get("host_id", "unknown")
