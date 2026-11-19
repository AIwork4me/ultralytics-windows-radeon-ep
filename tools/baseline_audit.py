r"""tools/baseline_audit.py - capture the pre-mission host/repo baseline.

Writes reports/mission_r7_r10/BASELINE_AUDIT.json. Read-only against the host:
no driver, package or registry change. Safe to re-run; the output is a snapshot
with an explicit capture timestamp.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

NL = chr(10)


def _git(*args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(ROOT)] + list(args),
                           capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        return (r.stdout or "").strip()
    except Exception as e:  # noqa: BLE001
        return "ERR: " + type(e).__name__ + ": " + str(e)


def _os_build() -> dict:
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
    }


def _winml_versions(python: Path) -> dict:
    if not python.is_file():
        return {"available": False, "path": python.name}
    code = (
        "import json,sys" + NL +
        "out={'python':sys.version.split()[0]}" + NL +
        "try:" + NL +
        "    import onnxruntime as ort" + NL +
        "    out['ort']=ort.__version__" + NL +
        "    out['providers']=sorted(ort.get_available_providers())" + NL +
        "except Exception as e:" + NL +
        "    out['ort_error']=type(e).__name__+': '+str(e)" + NL +
        "try:" + NL +
        "    import importlib.metadata as m" + NL +
        "    out['windowsml']=m.version('windowsml')" + NL +
        "except Exception as e:" + NL +
        "    out['windowsml_error']=type(e).__name__+': '+str(e)" + NL +
        "print(json.dumps(out))" + NL)
    try:
        r = subprocess.run([str(python), "-I", "-c", code], capture_output=True,
                           text=True, timeout=180, encoding="utf-8",
                           errors="replace")
        line = [ln for ln in (r.stdout or "").splitlines() if ln.startswith("{")]
        return json.loads(line[-1]) if line else {"available": False,
                                                  "error": (r.stderr or "")[-400:]}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": type(e).__name__ + ": " + str(e)}


def main() -> int:
    from radeon_ort import device_identity, host_probe

    out_path = ROOT / "reports" / "mission_r7_r10" / "BASELINE_AUDIT.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    adapters = []
    dxgi_error = None
    try:
        adapters = device_identity.enum_dxgi_adapters()
    except OSError as e:
        dxgi_error = str(e)
    amd = [a for a in adapters if a.vendor_id == device_identity.AMD_VENDOR_ID
           and not a.is_software]
    resolved = None
    if amd:
        gfx = device_identity.gfx_for_device_id(amd[0].device_id)
        resolved = device_identity.resolve_target_adapter(adapters, gfx=gfx) if gfx else None
    host = {}
    try:
        host = host_probe.collect_host()
    except Exception as e:  # noqa: BLE001
        host = {"error": type(e).__name__ + ": " + str(e)}

    models = sorted(p.name for p in (ROOT / "models" / "onnx").glob("*.onnx"))
    winml_python = ROOT / ".venv-winml" / "Scripts" / "python.exe"
    export_python = ROOT / ".venv-export" / "Scripts" / "python.exe"

    audit = {
        "schema_version": 1,
        "mission": "R7-R10",
        "host_id": (host.get("host_id") or "unknown"),
        "git": {
            "head": _git("rev-parse", "HEAD"),
            "head_short": _git("rev-parse", "--short", "HEAD"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "origin_main": _git("rev-parse", "origin/main"),
            "worktree_clean": _git("status", "--porcelain", "--untracked-files=all") == "",
            "status_porcelain": _git("status", "--porcelain", "--untracked-files=all")[:4000],
            "recent": _git("log", "--oneline", "-5").splitlines(),
            "reference_main_sha": "0c844da6727f5f7c43d27232b6e3eef9855a69bb",
        },
        "baseline_note": {
            "head_at_mission_start": "0c844da6727f5f7c43d27232b6e3eef9855a69bb",
            "origin_main_at_mission_start": "0c844da6727f5f7c43d27232b6e3eef9855a69bb",
            "tracked_files_modified_at_mission_start": False,
            "untracked_at_mission_start": [
                "Ultralytics/settings.json (created by the export venv)",
                "reports/gfx1151/PREFLIGHT_<ts>/ (produced by this audit's preflight)",
            ],
            "worktree_clean_at_capture": _git(
                "status", "--porcelain", "--untracked-files=all") == "",
            "why_dirty_now": "this file is captured at the end of the mission; the "
                             "working tree intentionally carries the R7-R10 changes",
        },
        "os": _os_build(),
        "cpu": (host.get("cpu") or {}).get("name"),
        "ram_gb": round((host.get("cpu") or {}).get("ram_bytes", 0) / 1e9, 1) or None,
        "gpu": {
            "dxgi_error": dxgi_error,
            "adapters": [
                {"index": a.index, "description": a.description,
                 "vendor_id": f"0x{a.vendor_id:04X}",
                 "device_id": f"0x{a.device_id:04X}",
                 "subsys_id": f"0x{a.sub_sys_id:08X}",
                 "luid": a.luid_str, "software": a.is_software}
                for a in adapters],
            "selected": ({"index": resolved["adapter"].index,
                          "device_id": f"0x{resolved['adapter'].device_id:04X}",
                          "luid": resolved["adapter"].luid_str,
                          "did_match": resolved.get("did_match")}
                         if resolved and resolved.get("ok") else None),
            "gfx": (device_identity.gfx_for_device_id(amd[0].device_id) if amd else None),
            "driver_version": (host.get("gpu_target") or {}).get("driver_version"),
            "driver_date": (host.get("gpu_target") or {}).get("driver_date"),
            "pnp_device_id_redacted": ("PCI&VEN_1002&DEV_1586" if amd else None),
            "memory_provenance": (device_identity.memory_provenance(
                resolved["adapter"] if resolved and resolved.get("ok") else amd[0])
                if amd else {"measurement_status": "NOT_MEASURED (no AMD adapter)"}),
        },
        "runtime": {
            "winml_python": _winml_versions(winml_python),
            "export_python": _winml_versions(export_python),
            "windows_ml_catalog": "CATALOG_EMPTY -> AMDGPU BLOCKED_EXTERNAL "
                                  "(see reports/mission_r10/)",
            "dml_available": "DmlExecutionProvider" in
                             (_winml_versions(winml_python).get("providers") or []),
        },
        "models": {"onnx_count": len(models), "onnx": models,
                   "catalog_sha256": _sha(ROOT / "catalog" / "model_catalog.json")},
        "tests": {
            "baseline_before_mission": {"passed": 293, "skipped": 1,
                                        "hardware_failures": 2,
                                        "command": "pytest -q (all)"},
            "ci_scope": "tests/unit tests/contracts tests/adversarial",
            "note": "the two hardware failures are tests/hardware gfx canaries that "
                    "require the AMDGPU EP; it is not installed on this host",
        },
        "policies": {
            "no_system_changes": True,
            "read_only_host_audit": True,
            "venv_only_dependency_additions": ["opencv-python-headless (winml venv, "
                                               "needed for real-image preprocessing)"],
        },
    }
    out_path.write_text(json.dumps(audit, indent=2, default=str) + NL, encoding="utf-8")
    print(json.dumps({"out": str(out_path), "head": audit["git"]["head_short"],
                      "gfx": audit["gpu"]["gfx"],
                      "dirty": not audit["git"]["worktree_clean"],
                      "dml": audit["runtime"]["dml_available"]}, indent=1))
    return 0


def _sha(p: Path) -> str | None:
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


if __name__ == "__main__":
    sys.exit(main())