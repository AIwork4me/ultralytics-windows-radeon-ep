"""Windows ML 2.x ExecutionProviderCatalog discovery + AMD GPU EP registration.

LIVE-VERIFIED on gfx1151 / WindowsAppRuntime 2.6.5 / onnxruntime-windowsml
1.25.2 (2026-11-17); every claim below was observed, not assumed:

- The wasdk 2.3.0 python projection lives at `winui3.microsoft.windows.ai.machinelearning`.
- `ExecutionProviderCatalog.get_default()` REQUIRES the Windows App SDK bootstrap
  context (`winui3...dynamicdependency.bootstrap.initialize()`); without it the
  call fails with WinError 0x80073CF4 "process has no package identity".
- The catalog lists MIGraphXExecutionProvider / VitisAIExecutionProvider /
  WebGpuExecutionProvider (the 1.8-era list). AMDGPUExecutionProvider (WinML
  >= 2.5.96) is NOT listed by this projection surface.
- `ensure_ready_async()` on MIGraphXExecutionProvider failed with HRESULT
  0x80073B0C ("Failed to ensure provider ready").
- The physical AMD GPU EP MSIX is nevertheless installed machine-wide:
  `C:\\Program Files\\WindowsApps\\MicrosoftCorporationII.WinML.AMD.GPU.EP.1.8_1.8.65.0_x64__8wekyb3d8bbwe\\ExecutionProvider\\amdgpu-ep.dll`
  (bundling hip-backend/directx-backend + custom_kernels_gfx1151.dll +
  hipblaslt gfx1151 kernels). We resolve it by glob (never hardcode another
  host's path), hash it, and register it via the documented
  `ort.register_execution_provider_library("AMDGPU", path)` — the registered
  provider name is "AMDGPU".
"""

from __future__ import annotations

import ctypes
import json
from pathlib import Path

from .manifest import sha256_file

PROJECTION_MODULE = "winui3.microsoft.windows.ai.machinelearning"
BOOTSTRAP_MODULE = "winui3.microsoft.windows.applicationmodel.dynamicdependency.bootstrap"


class EpCatalogResult:
    def __init__(self, data: dict):
        self.data = data

    @property
    def ok(self) -> bool:
        return bool(self.data.get("provider_registered"))

    def __repr__(self) -> str:  # pragma: no cover
        return f"EpCatalogResult(ok={self.ok}, detail={json.dumps(self.data)[:400]})"


def bootstrap_winml(product_version: str = "2.3"):
    """Initialize the Windows App SDK dynamic dependency (keep the returned ctx alive)."""
    import importlib
    bs = importlib.import_module(BOOTSTRAP_MODULE)
    return bs.initialize(version=product_version)


def probe_windows_ml_api(bootstrap_version: str | None = "2.3") -> dict:
    """Discover the installed WinML python surface (with bootstrap when possible)."""
    caps = {"projection_module": None, "types_found": [], "errors": [],
            "bootstrap": None, "catalog_providers": None}
    try:
        import importlib
        mod = importlib.import_module(PROJECTION_MODULE)
        caps["projection_module"] = PROJECTION_MODULE
        for attr in ("ExecutionProviderCatalog", "ExecutionProvider",
                     "ExecutionProviderReadyResultState"):
            if hasattr(mod, attr):
                caps["types_found"].append(attr)
    except ImportError as e:
        caps["errors"].append(f"projection {PROJECTION_MODULE} not importable: {e}")
        return caps

    if bootstrap_version:
        try:
            ctx = bootstrap_winml(bootstrap_version)
            caps["bootstrap"] = f"initialized (version={bootstrap_version})"
            cat = mod.ExecutionProviderCatalog.get_default()
            provs = cat.find_all_providers()
            caps["catalog_providers"] = [
                {"name": p.name, "ready_state": int(p.ready_state),
                 "certification": int(p.certification),
                 "library_path": p.library_path or "", "package_id": p.package_id or ""}
                for p in provs
            ]
            caps["_bootstrap_ctx"] = ctx  # caller must keep alive while using catalog
        except Exception as e:  # noqa: BLE001
            caps["bootstrap"] = f"failed: {type(e).__name__}: {e}"
    return caps


def _machine_windowsapps_patterns() -> list[str]:
    mwa = Path(r"C:\Program Files\WindowsApps")
    if not mwa.is_dir():
        return []
    return [
        str(mwa / "*WinML*AMD*GPU*EP*" / "ExecutionProvider" / "amdgpu-ep.dll"),
    ]


def scan_amdgpu_ep_dll() -> dict:
    """Locate the AMD GPU EP library (venv site-packages OR WindowsApps).

    Never hardcodes a foreign host path; resolves by glob, hashes, records."""
    import glob
    import site
    found = []
    patterns = []
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        p = Path(sp)
        patterns.append(str(p / "**" / "*amdgpu*ep*.dll"))
        patterns.append(str(p / "**" / "AMDGPUExecutionProvider*.dll"))
    wa = Path.home() / "AppData" / "Local" / "Microsoft" / "WindowsApps"
    if wa.is_dir():
        patterns.append(str(wa / "*" / "*AMDGPU*EP*.dll"))
        patterns.append(str(wa / "*AMDGPU*EP*.dll"))
    patterns.extend(_machine_windowsapps_patterns())
    for pat in patterns:
        for hit in glob.glob(pat, recursive=True):
            hit_p = Path(hit)
            try:
                found.append({
                    "path_redacted": _redact(hit_p),
                    "sha256": sha256_file(hit_p),
                    "bytes": hit_p.stat().st_size,
                })
            except OSError:
                continue
    return {"dlls": found, "searched_pattern_kinds": sorted({Path(p).name for p in patterns})}


def _redact(p: Path) -> str:
    from .redaction import redact_path
    return redact_path(p)


def catalog_probe() -> EpCatalogResult:
    """Full capability probe: API surface + catalog + DLL discovery + registration."""
    caps = probe_windows_ml_api()
    ctx = caps.pop("_bootstrap_ctx", None)  # keep bootstrap alive for registration below
    dlls = scan_amdgpu_ep_dll()
    data = {
        "api": caps,
        "amdgpu_dlls": dlls["dlls"],
        "provider_registered": False,
        "register_attempt": "not_attempted",
    }
    ort = _get_ort()
    real = _rederive_real_paths()
    if ort is not None and hasattr(ort, "register_execution_provider_library"):
        if real:
            try:
                for rp in real:
                    ort.register_execution_provider_library("AMDGPU", rp)
                data["provider_registered"] = True
                data["register_attempt"] = (
                    f"registered {len(real)} library path(s) as 'AMDGPU'")
            except Exception as e:  # noqa: BLE001
                data["register_attempt"] = f"register_execution_provider_library failed: {e}"
        else:
            data["register_attempt"] = "amdgpu-ep.dll not found (BLOCKED_EP)"
    elif ort is not None:
        data["register_attempt"] = "ort lacks register_execution_provider_library"
    else:
        data["register_attempt"] = "onnxruntime not importable in this venv"
    del ctx
    return EpCatalogResult(data)


def _get_ort():
    try:
        import onnxruntime as ort
        return ort
    except ImportError:
        return None


def _appx_ep_dll_fallback() -> list[str]:
    """RCA1-proven Get-AppxPackage resolver (globs of C:\\Program Files\\
    WindowsApps are ACL-blocked for normal processes and silently return
    nothing — live-observed again 2026-11-20 on the preflight path)."""
    import subprocess
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-AppxPackage Microsoft.WinML.AMD.GPU.EP.Framework.2).InstallLocation"],
            capture_output=True, text=True, timeout=30).stdout.strip()
        if out:
            p = Path(out) / "ExecutionProvider" / "amdgpu-ep.dll"
            if p.is_file():
                return [str(p)]
    except Exception:  # noqa: BLE001
        return []
    return []


def _rederive_real_paths() -> list[str]:
    import glob
    import site
    out = []
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        for pat in ("**/*amdgpu*ep*.dll", "**/AMDGPUExecutionProvider*.dll"):
            out.extend(glob.glob(str(Path(sp) / pat), recursive=True))
    wa = Path.home() / "AppData" / "Local" / "Microsoft" / "WindowsApps"
    if wa.is_dir():
        out.extend(glob.glob(str(wa / "*" / "*AMDGPU*EP*.dll")))
        out.extend(glob.glob(str(wa / "*AMDGPU*EP*.dll")))
    for pat in _machine_windowsapps_patterns():
        out.extend(glob.glob(pat))
    out.extend(_appx_ep_dll_fallback())
    # dedupe; the machine-wide vendor EP package is the canonical real path
    return sorted(set(out))


def list_ort_providers(ort_module=None) -> list[str]:
    ort = ort_module or _get_ort()
    if ort is None:
        return []
    try:
        return list(ort.get_available_providers())
    except Exception:  # noqa: BLE001
        return []


def list_ort_ep_devices(ort_module=None) -> list[dict]:
    ort = ort_module or _get_ort()
    if ort is None or not hasattr(ort, "get_ep_devices"):
        return []
    out = []
    for d in ort.get_ep_devices():
        out.append({
            "ep_name": d.ep_name, "ep_vendor": d.ep_vendor,
            "ep_options": dict(d.ep_options or {}), "ep_metadata": dict(d.ep_metadata or {}),
        })
    return out


def load_dll_metadata(path: str | Path) -> dict:
    """Best-effort DLL metadata: LoadLibrary probe + sha256."""
    p = Path(path)
    meta = {"path_redacted": _redact(p), "sha256": sha256_file(p), "bytes": p.stat().st_size,
            "loadable": False, "load_error": None}
    try:
        h = ctypes.WinDLL(str(p))
        meta["loadable"] = True
        meta["_handle"] = h._handle
    except OSError as e:
        meta["load_error"] = str(e)
    return meta
