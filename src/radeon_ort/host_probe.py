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


def collect_host(gpu_name_needle: str = "8060S", gfx: str = "gfx1151") -> dict:
    """Collect and structure the host fingerprint. gpu_name_needle selects the target."""
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

    needle = gpu_name_needle.lower()
    target = next((g for g in gpus if needle in (g.get("name") or "").lower()), None)
    if target is None:
        return {
            "schema_version": 1,
            "captured_utc": __import__("radeon_ort.receipts", fromlist=["utc_now_iso"]).utc_now_iso(),
            "os": os_info, "cpu": cpu_info,
            "gpu_target": {"name": None, "gfx": gfx, "verified": False,
                           "identity_method": "name-needle-not-found",
                           "driver_version": "unknown"},
            "other_adapters": gpus,
            "windows_app_runtime_versions": sorted(set(runtimes)),
            "python": {"version": sys.version.split()[0], "implementation": platform.python_implementation(),
                       "executable_basename": Path(sys.executable).name},
            "tooling": {},
            "_gpu_list": gpus,
        }
    drv_meta = next((d for d in drivers
                     if needle in (d.get("device_name") or "").lower()), {})
    return {
        "schema_version": 1,
        "captured_utc": __import__("radeon_ort.receipts", fromlist=["utc_now_iso"]).utc_now_iso(),
        "os": os_info,
        "cpu": cpu_info,
        "gpu_target": {
            "name": target["name"],
            "gfx": gfx,
            "driver_version": target.get("driver_version"),
            "driver_date": (drv_meta.get("drv_date") or "").split(" ")[0] or None,
            "pnp_device_id": redact_pnp_id(target.get("pnp_device_id")),
            "status": target.get("status"),
            "verified": False,  # set True only by device_identity with LUID+canary
            "identity_method": "wmi-name-match (pending DXGI cross-check)",
        },
        "other_adapters": [{**g, "pnp_device_id": redact_pnp_id(g.get("pnp_device_id"))}
                           for g in gpus if g is not target],
        "windows_app_runtime_versions": sorted(set(runtimes)),
        "python": {"version": sys.version.split()[0], "implementation": platform.python_implementation(),
                   "executable_basename": Path(sys.executable).name},
        "tooling": {},
        "_gpu_list": gpus,
    }


def host_id_stable() -> str:
    """Stable-but-non-identifying host id: hash of CPU name + GPU pnp id, truncated."""
    import hashlib
    h = collect_host()
    key = (h["cpu"].get("cpu_name") or "?") + "|" + (h["gpu_target"].get("pnp_device_id") or "?")
    return "host_" + hashlib.sha256(key.encode()).hexdigest()[:12]
