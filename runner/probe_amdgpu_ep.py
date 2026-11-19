"""python -m runner.probe_amdgpu_ep - READ-ONLY AMDGPU EP provisioning probe.

Nothing is installed, registered system-wide, or changed. The probe separates
five distinct states (R3-C) instead of one ambiguous BLOCKED_EP label:

  NOT_INSTALLED   no official windowsml bindings AND no AMD GPU EP artifact
  CATALOG_EMPTY   the official catalog API answered successfully but offered
                  zero providers (the OS has no catalog entry for this device)
  EP_NOT_READY    the catalog offers an AMD GPU EP (or an EP DLL exists) but it
                  is not Ready / not registered in this Python ORT process
  EP_REGISTERED   this ORT process sees an AMDGPU EP device/provider
  GPU_EXECUTED    a canary session attributed >0 profile nodes to AMDGPU and 0
                  to CPU (only attempted with --canary-onnx; never assumed)

API diagnostics and EP readiness are recorded in separate fields so a broken
binding cannot be reported as a healthy-but-empty catalog (and vice versa).

Exit codes: 0 = EP_REGISTERED or GPU_EXECUTED, 2 = blocked/not installed.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
from pathlib import Path

from radeon_ort import windows_ml_ep_catalog
from radeon_ort.receipts import utc_now_iso

NOT_INSTALLED = "NOT_INSTALLED"
CATALOG_EMPTY = "CATALOG_EMPTY"
EP_NOT_READY = "EP_NOT_READY"
EP_REGISTERED = "EP_REGISTERED"
GPU_EXECUTED = "GPU_EXECUTED"

OK_STATES = (EP_REGISTERED, GPU_EXECUTED)
EXIT_OK = 0
EXIT_BLOCKED = 2

# Documented (Microsoft Learn, checked 2026-11-19) AMD EP requirements. These
# are recorded as references, never as a proven driver-compatibility verdict:
# the Windows WDDM driver version (32.0.x.y) and the AMD software/Adrenalin
# numbering do not share a scale.
EP_REFERENCE = {
    "MIGraphXExecutionProvider": {
        "role": "AMD GPU EP offered by the Windows ML 1.8/2.x catalog",
        "documented_requirement": "GPU with driver version 25.10.13.09 (exactly)",
        "source": "https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/supported-execution-providers",
    },
    "VitisAIExecutionProvider": {
        "role": "AMD NPU EP (not an AMD GPU EP)",
        "documented_requirement": "Adrenalin 25.6.3..25.9.1 with NPU driver 32.00.0203.280..297",
        "source": "https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/supported-execution-providers",
    },
    "AMDGPUExecutionProvider": {
        "role": "AMD GPU EP referenced by the mission/R3-D",
        "documented_requirement": "NOT listed in the live catalog table on 2026-11-19",
        "source": "https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/supported-execution-providers",
    },
}

_APPX_STORES = (
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Appx\AppxAllUserStore\Applications"),
    ("HKCU", (r"Software\Classes\Local Settings\Software\Microsoft"
              r"\Windows\CurrentVersion\AppModel\Repository\Packages")),
)
_DISPLAY_CLASS = (r"SYSTEM\CurrentControlSet\Control\Class"
                  r"\{4d36e968-e325-11ce-bfc1-08002be10318}")


def _appx_packages() -> list[dict]:
    """Installed AppX/MSIX packages from the registry (encoding-safe, read-only)."""
    import winreg

    seen: dict[str, str] = {}
    for hive_name, path in _APPX_STORES:
        hive = winreg.HKEY_LOCAL_MACHINE if hive_name == "HKLM" else winreg.HKEY_CURRENT_USER
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue
        index = 0
        while True:
            try:
                full = winreg.EnumKey(key, index)
            except OSError:
                break
            index += 1
            parts = full.split("_")
            if len(parts) >= 2 and parts[1][:1].isdigit():
                name, version = parts[0], parts[1]
                if version > seen.get(name, ""):
                    seen[name] = version
    return [{"Name": n, "Version": v} for n, v in sorted(seen.items())]


def _display_adapters() -> list[dict]:
    """Display adapters + driver versions from the display class registry key."""
    import winreg

    out: list[dict] = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS)
    except OSError:
        return out
    index = 0
    while True:
        try:
            sub = winreg.EnumKey(root, index)
        except OSError:
            break
        index += 1
        if not sub.isdigit():
            continue
        try:
            key = winreg.OpenKey(root, sub)
            desc = str(winreg.QueryValueEx(key, "DriverDesc")[0])
            ver = str(winreg.QueryValueEx(key, "DriverVersion")[0])
            out.append({"Name": desc, "DriverVersion": ver})
        except OSError:
            continue
    return out


def system_state() -> dict:
    """OS / GPU / AppX inventory, read-only and independent of console code pages."""
    import platform

    version = platform.version()
    parts = version.split(".")
    build = parts[2] if len(parts) > 2 else ""
    return {
        "source": "registry+platform",
        "os": {"version": version, "build": build,
               "caption": platform.platform()},
        "video": _display_adapters(),
        "packages": _appx_packages(),
    }


def _official_catalog() -> dict:
    """Official windowsml (>=2.5.96) flat C API probe, with raw HRESULTs."""
    out: dict = {"engine": "windowsml_flat_c", "installed": False,
                 "version": None, "api_ok": False, "providers": [],
                 "errors": []}
    try:
        import importlib.metadata as md

        import windowsml

        out["installed"] = True
        try:
            out["version"] = md.version("windowsml")
        except Exception:  # noqa: BLE001
            out["version"] = getattr(windowsml, "__version__", "unknown")
    except ImportError as e:
        out["errors"].append({"stage": "import_windowsml", "message": str(e)})
        return out
    except Exception as e:  # noqa: BLE001
        out["errors"].append({"stage": "import_windowsml", "message":
                              f"{type(e).__name__}: {e}"})
        return out

    try:
        with windowsml.EpCatalog() as cat:
            for p in cat.find_all_providers():
                state = getattr(p.ready_state, "value", p.ready_state)
                cert = getattr(p.certification, "value", p.certification)
                out["providers"].append({
                    "name": p.name, "version": p.version,
                    "ready_state": int(state),
                    "ready_state_name": str(getattr(p.ready_state, "name", state)),
                    "certification": int(cert),
                    "library_path": p.library_path or "",
                    "package_family_name": p.package_family_name or "",
                })
        out["api_ok"] = True
    except Exception as e:  # noqa: BLE001
        code = getattr(e, "winerror", None)
        out["errors"].append({"stage": "catalog_enumerate",
                              "hresult": (f"0x{code & 0xFFFFFFFF:08X}"
                                          if isinstance(code, int) else None),
                              "message": f"{type(e).__name__}: {e}"})
    out["raw"] = _raw_catalog_hresults()
    return out


def _raw_catalog_hresults() -> dict:
    """Direct ctypes call so an HRESULT cannot be swallowed by a wrapper."""
    raw: dict = {}
    try:
        from windowsml import _bindings as B
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": f"{type(e).__name__}: {e}"}
    raw["available"] = True
    handle = B.EpCatalogHandle()
    try:
        hr = int(B.WinMLEpCatalogCreate(ctypes.byref(handle)))
        raw["create_hresult"] = f"0x{hr & 0xFFFFFFFF:08X}"
    except OSError as e:
        raw["create_hresult"] = (f"0x{e.winerror & 0xFFFFFFFF:08X}"
                                 if getattr(e, "winerror", None) else str(e))
        return raw
    seen: list[int] = []

    @B.EpEnumCallback
    def _cb(ep, info, _ctx):
        seen.append(1)
        return True

    try:
        hr = int(B.WinMLEpCatalogEnumProviders(handle, _cb, None))
        raw["enum_hresult"] = f"0x{hr & 0xFFFFFFFF:08X}"
    except OSError as e:
        raw["enum_hresult"] = (f"0x{e.winerror & 0xFFFFFFFF:08X}"
                               if getattr(e, "winerror", None) else str(e))
    finally:
        raw["provider_count"] = len(seen)
        try:
            B.WinMLEpCatalogRelease(handle)
        except Exception as e:  # noqa: BLE001
            raw["release_error"] = str(e)
    return raw


def _legacy_projection() -> dict:
    """Legacy wasdk 2.3.0 projection probe (kept for comparability only)."""
    out: dict = {"engine": "wasdk_projection", "installed": False,
                 "api_ok": False, "providers": [], "errors": []}
    try:
        import importlib
        import importlib.metadata as md

        mod = importlib.import_module(windows_ml_ep_catalog.PROJECTION_MODULE)
        out["installed"] = True
        try:
            out["version"] = md.version(
                "wasdk-Microsoft.Windows.AI.MachineLearning")
        except Exception:  # noqa: BLE001
            out["version"] = "unknown"
    except Exception as e:  # noqa: BLE001
        out["errors"].append({"stage": "import_projection",
                              "message": f"{type(e).__name__}: {e}"})
        return out
    try:
        # The bootstrap object is a context manager: entering it creates the
        # package-identity context the projection needs, and leaving it shuts
        # the context down explicitly (no GC warning, no leaked identity).
        with windows_ml_ep_catalog.bootstrap_winml("2.3"):
            cat = mod.ExecutionProviderCatalog.get_default()
            for p in cat.find_all_providers():
                out["providers"].append({
                    "name": p.name, "ready_state": int(p.ready_state),
                    "certification": int(p.certification),
                    "library_path": p.library_path or "",
                })
        out["api_ok"] = True
    except Exception as e:  # noqa: BLE001
        code = getattr(e, "winerror", None)
        out["errors"].append({"stage": "catalog_enumerate",
                              "hresult": (f"0x{code & 0xFFFFFFFF:08X}"
                                          if isinstance(code, int) else None),
                              "message": f"{type(e).__name__}: {e}"})
    return out


def _ort_state(dlls: list[dict]) -> dict:
    out: dict = {"importable": False, "version": None, "providers": [],
                 "ep_devices": [], "amd_devices": [], "registration": None}
    try:
        import onnxruntime as ort
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    out["importable"] = True
    out["version"] = getattr(ort, "__version__", "unknown")
    try:
        out["providers"] = list(ort.get_available_providers())
    except Exception as e:  # noqa: BLE001
        out["error"] = f"get_available_providers: {e}"
    try:
        for d in ort.get_ep_devices():
            row = {"ep_name": d.ep_name, "ep_vendor": getattr(d, "ep_vendor", None)}
            out["ep_devices"].append(row)
            if "amd" in str(row["ep_name"]).lower():
                out["amd_devices"].append(row)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"get_ep_devices: {e}"
    if not out["amd_devices"] and dlls and hasattr(
            ort, "register_execution_provider_library"):
        for dll in dlls[:1]:
            try:
                ort.register_execution_provider_library("AMDGPU", dll["path"])
                out["registration"] = {"attempted": True, "ok": True,
                                       "library_sha256": dll.get("sha256")}
            except Exception as e:  # noqa: BLE001
                out["registration"] = {"attempted": True, "ok": False,
                                       "error": f"{type(e).__name__}: {e}"}
        try:
            out["amd_devices"] = [
                {"ep_name": d.ep_name} for d in ort.get_ep_devices()
                if "amd" in str(d.ep_name).lower()]
        except Exception as e:  # noqa: BLE001
            out["post_registration_error"] = str(e)
    return out


def classify(*, official: dict, legacy: dict, ort: dict, dlls: list[dict],
             canary: dict | None = None) -> str:
    """Single, ordered state machine over the observed evidence."""
    if canary and canary.get("executed_full"):
        return GPU_EXECUTED
    if ort.get("amd_devices"):
        return EP_REGISTERED
    providers = (official.get("providers") or []) + (legacy.get("providers") or [])
    amd = [p for p in providers if "amd" in str(p.get("name", "")).lower()]
    if amd:
        return EP_NOT_READY  # offered by a catalog but not registered here
    if dlls:
        return EP_NOT_READY  # artifact present but this ORT cannot use it
    if official.get("api_ok") or legacy.get("api_ok"):
        return CATALOG_EMPTY
    return NOT_INSTALLED


def system_dependencies(state: dict, official: dict, ort: dict) -> list[dict]:
    packages = state.get("packages") or []
    if isinstance(packages, dict):
        packages = [packages]
    packages = [p for p in packages if isinstance(p, dict)]
    names = {str(p.get("Name")): str(p.get("Version")) for p in packages}
    os_info = state.get("os") or {}
    build = str(os_info.get("build") or os_info.get("BuildNumber") or "")
    return [
        {"name": "windows_11_24h2_or_later", "required": "build >= 26100",
         "observed": build or "unknown",
         "status": "OK" if build.isdigit() and int(build) >= 26100 else "UNKNOWN"},
        {"name": "windows_ml_shared_runtime", "required": "WindowsWorkload.WinMLShared",
         "observed": next((f"{n} {v}" for n, v in names.items()
                           if n.startswith("WindowsWorkload.WinMLShared")), "missing"),
         "status": "OK" if any(n.startswith("WindowsWorkload.WinMLShared")
                               for n in names) else "MISSING"},
        {"name": "windows_app_runtime", "required": "1.8 or 2.x",
         "observed": next((f"{n} {v}" for n, v in names.items()
                           if n.startswith("Microsoft.WindowsAppRuntime")), "missing"),
         "status": "OK" if any(n.startswith("Microsoft.WindowsAppRuntime")
                               for n in names) else "MISSING"},
        {"name": "python_windowsml", "required": ">= 2.5.96 (official C API)",
         "observed": official.get("version") if official.get("installed")
                     else "not installed",
         "status": "OK" if official.get("installed") else "MISSING"},
        {"name": "onnxruntime_windowsml", "required": "importable in this venv",
         "observed": ort.get("version") if ort.get("importable") else "not importable",
         "status": "OK" if ort.get("importable") else "MISSING"},
        {"name": "amd_gpu_ep_package_or_dll", "required": "AMD GPU EP artifact",
         "observed": f"{len(state.get('amd_ep_packages') or [])} package(s)",
         "status": "OK" if (state.get("amd_ep_packages") or state.get("dlls"))
                   else "MISSING"},
    ]


def recommended_actions(state: str) -> list[str]:
    if state == GPU_EXECUTED:
        return ["record the run contract and receipts for this run_id"]
    if state == EP_REGISTERED:
        return ["run the AMDGPU canary (--canary-onnx) and then runner.run_matrix"]
    if state == EP_NOT_READY:
        return ["call provider.ensure_ready_async().wait() for the AMD GPU EP",
                "re-run this probe and confirm ort.get_ep_devices() lists AMDGPU"]
    if state == CATALOG_EMPTY:
        return ["no Windows ML EP is offered for this device/OS state",
                "confirm the device is serviced (Windows Update optional preview)",
                "do not install an EP from an untrusted source",
                "record BLOCKED_EXTERNAL with this probe as evidence"]
    return ["install the official windowsml Python bindings (pip install windowsml)",
            "install onnxruntime-windowsml in the same interpreter",
            "re-run the probe before attempting any EP registration"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--gfx", default=None,
                    help="expected gfx target (recorded, not assumed)")
    ap.add_argument("--canary-onnx", default=None,
                    help="optional ONNX for an in-process AMDGPU canary; the "
                         "default probe never creates a session")
    a = ap.parse_args(argv)

    state = system_state()
    official = _official_catalog()
    legacy = _legacy_projection()
    dlls = windows_ml_ep_catalog.scan_amdgpu_ep_dll().get("dlls") or []
    ort = _ort_state(dlls)
    canary = None
    if a.canary_onnx:
        canary = _run_canary(a.canary_onnx)
    ep_state = classify(official=official, legacy=legacy, ort=ort, dlls=dlls,
                        canary=canary)

    packages = state.get("packages") or []
    if isinstance(packages, dict):
        packages = [packages]
    packages = [p for p in packages if isinstance(p, dict)]
    amd_ep_packages = [p for p in packages
                       if "EP" in str(p.get("Name", ""))
                       and "AMD" in str(p.get("Name", "")).upper()
                       and "VitisAI" not in str(p.get("Name", ""))]
    state["amd_ep_packages"] = amd_ep_packages
    state["dlls"] = dlls

    error_codes = []
    for engine in (official, legacy):
        for err in engine.get("errors") or []:
            error_codes.append({"engine": engine.get("engine"), **err})

    report = {
        "schema_version": 2,
        "captured_utc": utc_now_iso(),
        "scope": "READ_ONLY (no install / no driver change / no MSIX change)",
        "gfx_requested": a.gfx,
        "state": ep_state,
        "verdict": "OK" if ep_state in OK_STATES else "BLOCKED_EP",
        "blocked_status": (None if ep_state in OK_STATES else
                           ("BLOCKED_EP_NOT_INSTALLED" if ep_state == NOT_INSTALLED
                            else "BLOCKED_EXTERNAL")),
        "api_diagnostic": {"official": official, "legacy_projection": legacy},
        "ep_readiness": {
            "catalog_providers": official.get("providers") or [],
            "legacy_projection_providers": legacy.get("providers") or [],
            "amd_gpu_ep_dlls": dlls,
            "amd_gpu_ep_packages": amd_ep_packages,
            "ort_providers": ort.get("providers"),
            "ort_ep_devices": ort.get("ep_devices"),
            "ort_registration": ort.get("registration"),
            "canary": canary,
        },
        "system": state,
        "system_dependencies": system_dependencies(state, official, ort),
        "error_codes": error_codes,
        "ep_reference": EP_REFERENCE,
        "recommended_actions": recommended_actions(ep_state),
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(json.dumps({"state": ep_state, "verdict": report["verdict"],
                      "providers": len(report["ep_readiness"]["catalog_providers"]),
                      "amd_devices": len(ort.get("amd_devices") or []),
                      "out": str(out)}, indent=2))
    return EXIT_OK if ep_state in OK_STATES else EXIT_BLOCKED


def _run_canary(onnx_path: str) -> dict:
    """Minimal AMDGPU canary: profiler node attribution, never a PASS claim."""
    result: dict = {"onnx": Path(onnx_path).name, "executed_full": False}
    try:
        import numpy as np
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.enable_profiling = True
        so.profile_file_prefix = "canary_amdgpu"
        sess = ort.InferenceSession(onnx_path, so, providers=["AMDGPUExecutionProvider"])
        inp = sess.get_inputs()[0]
        shape = [d if isinstance(d, int) else 1 for d in inp.shape]
        tensor = np.zeros(shape, dtype=np.float32)
        sess.run(None, {inp.name: tensor})
        prof = sess.end_profiling()
        counts: dict[str, int] = {}
        for ev in json.loads(Path(prof).read_text(encoding="utf-8")):
            if ev.get("cat") in ("Node", "Op"):
                key = str(ev.get("args", {}).get("provider", "") or ev.get("name", ""))
                counts[key] = counts.get(key, 0) + 1
        gpu = sum(v for k, v in counts.items() if "AMD" in k.upper())
        cpu = sum(v for k, v in counts.items() if "CPU" in k.upper())
        result.update({"profiled": True, "amd_nodes": gpu, "cpu_nodes": cpu,
                       "nodes": counts,
                       "executed_full": gpu > 0 and cpu == 0})
    except Exception as e:  # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {e}"
    return result


if __name__ == "__main__":
    sys.exit(main())
