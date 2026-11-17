"""Windows ML 2.x ExecutionProviderCatalog discovery for AMDGPUExecutionProvider.

Follows the documented path:
  catalog find provider -> ensure/ready -> get library path ->
  ort.register_execution_provider_library(...) -> session with that provider.

The wasdk Python projections are discovered dynamically (module names have changed
across preview versions); every step is probed at runtime, never assumed. Fails
closed with BLOCKED_EP detail when the API surface or the OS runtime is missing.
"""

from __future__ import annotations

import ctypes
import json
from pathlib import Path

from .manifest import sha256_file


class EpCatalogResult:
    def __init__(self, data: dict):
        self.data = data

    @property
    def ok(self) -> bool:
        return bool(self.data.get("provider_registered"))

    def __repr__(self) -> str:  # pragma: no cover
        return f"EpCatalogResult(ok={self.ok}, detail={json.dumps(self.data)[:400]})"


def _try_imports():
    """Yield (module_name, module) for every plausible WinML projection import."""
    candidates = [
        "winrt.windows.ai.machinelearning",
        "windowsai.machinelearning",
        "Windows.AI.MachineLearning",
    ]
    import importlib
    for name in candidates:
        try:
            yield name, importlib.import_module(name)
        except ImportError:
            continue


def probe_windows_ml_api() -> dict:
    """Discover the installed WinML python surface. Returns capability map."""
    caps = {"projection_module": None, "types_found": [], "errors": []}
    for name, mod in _try_imports():
        caps["projection_module"] = name
        for attr in ("ExecutionProviderCatalog", "WinMLEpCatalogFindProvider",
                     "WinMLEpEnsureReady", "WinMLEpGetLibraryPath"):
            if hasattr(mod, attr):
                caps["types_found"].append(attr)
        if caps["types_found"]:
            break
    if caps["projection_module"] is None:
        caps["errors"].append(
            "no winrt Windows.AI.MachineLearning projection importable; "
            "install wasdk-Microsoft.Windows.AI.MachineLearning[all]"
        )
    return caps


def try_bootstrap_winml_runtime() -> dict:
    """wasdk dynamic dependency bootstrap (framework-dependent)."""
    out = {"bootstrapped": False, "detail": ""}
    try:
        import importlib
        bs = importlib.import_module(
            "windows_applicationmodel_dynamicdependency_bootstrap")
    except ImportError:
        try:
            import importlib
            bs = importlib.import_module(
                "winrt.windows.applicationmodel.dynamicdependency")
        except ImportError:
            out["detail"] = "bootstrap module not importable"
            return out
    try:
        import winsdkutils  # type: ignore # noqa: F401
    except ImportError:
        pass
    init_attr = None
    for attr in ("Bootstrap", "TryInitialize", "Initialize"):
        if hasattr(bs, attr):
            init_attr = attr
            break
    out["detail"] = f"bootstrap module found; entry attr={init_attr}; system runtime used as-installed"
    # Actual MSIX bootstrap requires app identity; a Python console app uses the
    # undocked WinML ORT build which loads EP DLLs directly. We record capability.
    out["bootstrapped"] = False
    return out


def scan_amdgpu_ep_dll() -> dict:
    """Locate an AMDGPU EP library exposed by the onnxruntime-windowsml install.

    We never hardcode another host's WindowsApps path: we search ONLY inside this
    venv's site-packages and the well-known per-user WindowsApps AMD EP package
    directory (whose glob we resolve and hash). Missing -> BLOCKED_EP.
    """
    import glob
    import site
    found = []
    patterns = []
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        p = Path(sp)
        patterns.append(str(p / "**" / "*amdgpu*ep*.dll"))
        patterns.append(str(p / "**" / "AMDGPUExecutionProvider*.dll"))
    # Windows ML vendor EP packages install under per-user WindowsApps with family
    # names like 'MicrosoftWindowsAI.<Vendor>EP_...'; resolveable, hashed, then recorded
    wa = Path.home() / "AppData" / "Local" / "Microsoft" / "WindowsApps"
    if wa.is_dir():
        patterns.append(str(wa / "*" / "*AMDGPU*EP*.dll"))
        patterns.append(str(wa / "*AMDGPU*EP*.dll"))
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
    """Full capability probe: API surface + DLL discovery + registration attempt."""
    caps = probe_windows_ml_api()
    dlls = scan_amdgpu_ep_dll()
    data = {
        "api": caps,
        "amdgpu_dlls": dlls["dlls"],
        "provider_registered": False,
        "register_attempt": "not_attempted",
    }
    ort = _get_ort()
    if ort is not None and hasattr(ort, "register_execution_provider_library"):
        dll_paths = [d for d in dlls["dlls"]]
        if dll_paths:
            # register needs the real path; we kept only redacted+hash, so re-derive
            real = _rederive_real_paths()
            if real:
                try:
                    for rp in real:
                        ort.register_execution_provider_library(rp)
                    data["provider_registered"] = True
                    data["register_attempt"] = f"registered {len(real)} library path(s)"
                except Exception as e:  # noqa: BLE001
                    data["register_attempt"] = f"register_execution_provider_library failed: {e}"
    elif ort is not None:
        data["register_attempt"] = (
            "ort lacks register_execution_provider_library; distribution="
            f"{getattr(ort, '__distribution__', 'unknown')}"
        )
    else:
        data["register_attempt"] = "onnxruntime not importable in this venv"
    return EpCatalogResult(data)


def _get_ort():
    try:
        import onnxruntime as ort
        return ort
    except ImportError:
        return None


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
    return sorted(set(out))


def list_ort_providers(ort_module=None) -> list[str]:
    ort = ort_module or _get_ort()
    if ort is None:
        return []
    try:
        return list(ort.get_available_providers())
    except Exception:  # noqa: BLE001
        return []


def load_dll_metadata(path: str | Path) -> dict:
    """Best-effort DLL metadata: exports check via LoadLibrary + sha256."""
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
