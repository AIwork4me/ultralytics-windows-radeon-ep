"""python -m runner.probe_amdgpu_ep — READ-ONLY AMDGPU EP provisioning probe.

Collects, without installing anything:

  * OS / GPU / driver state,
  * installed WinML / WindowsAppRuntime / AMD EP AppX packages,
  * Python projections (legacy wasdk and official windowsml) + ORT providers,
  * EP catalog result from every available Python surface,
  * AMD GPU EP DLL discovery.

Writes a JSON report and prints a fail-closed verdict. No system component,
driver, MSIX or Python package is installed or changed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from radeon_ort import windows_ml_ep_catalog
from radeon_ort.receipts import utc_now_iso

PS = r"""
$ErrorActionPreference='SilentlyContinue'
$vc = Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,DriverDate
$vc | ConvertTo-Json -Depth 4 -Compress
$pkg = Get-AppxPackage | Where-Object { $_.Name -match 'WinML|WindowsWorkload|MachineLearning|AMD' } |
       Select-Object Name,Version,PackageFullName
$pkg | ConvertTo-Json -Depth 4 -Compress
"""


def _ps_state() -> dict:
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", PS],
                           capture_output=True, text=True, timeout=120)
        lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
        video = json.loads(lines[0]) if lines else []
        if isinstance(video, dict):
            video = [video]
        packages = []
        for ln in lines[1:]:
            if not ln.lstrip().startswith(("[", "{")):
                continue
            obj = json.loads(ln)
            packages = obj if isinstance(obj, list) else [obj]
            break
        if not packages:
            return {"video": video, "packages": [],
                    "packages_note": "Get-AppxPackage returned 0 rows from this "
                                     "non-interactive powershell context; the "
                                     "interactive host package table is recorded "
                                     "in docs/AMDGPU_EP_PROVISIONING.md"}
        return {"video": video, "packages": packages}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def _python_packages() -> dict:
    import importlib.metadata as md
    out = {}
    for name in ("windowsml", "wasdk-Microsoft.Windows.AI.MachineLearning",
                 "wasdk-Microsoft.Windows.ApplicationModel.DynamicDependency.Bootstrap",
                 "onnxruntime-windowsml"):
        try:
            out[name] = md.version(name)
        except Exception:  # noqa: BLE001
            out[name] = "not-installed"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--gfx", default="gfx1151")
    a = ap.parse_args()

    state = _ps_state()
    ep = windows_ml_ep_catalog.catalog_probe()
    data = ep.data
    packages = state.get("packages") or []
    if isinstance(packages, dict):
        packages = [packages]
    video = state.get("video") or []
    if isinstance(video, dict):
        video = [video]

    amd_gpu_pkg = [p for p in packages
                   if "AMD" in (p.get("Name") or "")
                   and ("EP" in (p.get("Name") or "") or "WinML" in (p.get("Name") or ""))
                   and "VitisAI" not in (p.get("Name") or "")]
    report = {
        "schema_version": 1,
        "captured_utc": utc_now_iso(),
        "scope": "READ_ONLY (no install / no driver change / no MSIX change)",
        "gfx": a.gfx,
        "host": state,
        "python_packages": _python_packages(),
        "ort": {
            "providers": data.get("ort_providers"),
            "ep_devices": data.get("ep_devices"),
        },
        "catalog": {
            "engines": (data.get("api") or {}).get("catalog_probes"),
            "wasdk_projection_providers": (data.get("api") or {}).get("catalog_providers"),
            "windowsml_flat_c": (data.get("api") or {}).get("windowsml"),
            "counts": data.get("catalog_provider_count"),
        },
        "amd_gpu_ep": {
            "dlls": data.get("amdgpu_dlls"),
            "register_attempt": data.get("register_attempt"),
            "provider_registered": data.get("provider_registered"),
        },
        "amd_gpu_ep_packages": amd_gpu_pkg,
        "verdict": "BLOCKED_EP",
        "verdict_reasons": [],
    }
    reasons = report["verdict_reasons"]
    if not data.get("amdgpu_dlls"):
        reasons.append("amdgpu-ep.dll not found in site-packages or WindowsApps")
    if not amd_gpu_pkg:
        reasons.append("no AMD GPU EP AppX/MSIX package installed")
    counts = data.get("catalog_provider_count") or {}
    if not any(counts.values()):
        reasons.append("all EP catalog engines returned zero providers")
    if "windowsml" in report["python_packages"] and \
            report["python_packages"]["windowsml"] == "not-installed":
        reasons.append("official windowsml>=2.5.96 Python bindings are not installed")
    if not reasons:
        report["verdict"] = "EP_AVAILABLE_NEEDS_SESSION_TEST"

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "reasons": reasons,
                      "out": str(out)}, indent=2))
    return 0 if report["verdict"] == "BLOCKED_EP" else 1


if __name__ == "__main__":
    sys.exit(main())
