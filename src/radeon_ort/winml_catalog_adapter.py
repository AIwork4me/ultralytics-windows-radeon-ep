"""winml_catalog_adapter — Catalog-first EP acquisition state machine (v2.x).

Flow (docs/unified/ARCHITECTURE.md §4):
  discover (read-only) -> select target -> [ensure_ready ONLY with explicit
  installation authorization] -> library_path -> register in THIS Python ORT
  -> verify via get_ep_devices (Catalog Ready alone proves nothing).

All states are data, so every transition is unit-testable and mockable without
touching the system. No installs are ever performed by this module unless the
caller passes install_authorized=True AND a real installer callable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

BLOCKED_INSTALL = "BLOCKED_INSTALL_AUTHORIZATION"
CERTIFIED_ALIAS = "AMDGPUExecutionProvider"   # Catalog EpName / official alias
LEGACY_ALIAS = "AMDGPU"                       # historical manual-registration alias


@dataclass
class CatalogDecision:
    state: str                                   # discovery/registered/blocked/failed
    detail: dict[str, Any] = field(default_factory=dict)


def discover(bootstrap_version: str | None = "2.3") -> dict[str, Any]:
    """Read-only: catalog providers + ready states + DLL scan. Never installs."""
    from .windows_ml_ep_catalog import probe_windows_ml_api, scan_amdgpu_ep_dll

    caps = probe_windows_ml_api(bootstrap_version=bootstrap_version)
    caps.pop("_bootstrap_ctx", None)
    caps["amdgpu_dlls"] = scan_amdgpu_ep_dll()["dlls"]
    return caps


def ensure_ready(ep_name: str, *, install_authorized: bool,
                 installer: Callable[[str], dict] | None = None) -> CatalogDecision:
    """ensure_ready gate. Without explicit authorization this is BLOCKED —
    the only honest answer when a system-level install would be required.
    `installer` is injected for tests (mock installation); never call system
    package managers here."""
    if not install_authorized:
        return CatalogDecision(BLOCKED_INSTALL,
                               {"ep_name": ep_name,
                                "note": "read-only discovery only; installation "
                                        "requires owner authorization"})
    if installer is None:
        return CatalogDecision(BLOCKED_INSTALL,
                               {"ep_name": ep_name,
                                "note": "authorized but no installer provided "
                                        "(no system mutation from library code)"})
    return CatalogDecision("ensure_attempted", installer(ep_name))


def register_in_process(ort_module, library_paths: list[str],
                        alias: str = CERTIFIED_ALIAS) -> dict[str, Any]:
    """Register the EP library in THIS interpreter and verify it is really there.

    Catalog/WinRT registration elsewhere does NOT count: verification is
    ort.get_ep_devices() in this process (Architecture §4 Python caveat)."""
    out: dict[str, Any] = {"registered": False, "alias": alias,
                           "tried_paths": len(library_paths), "errors": []}
    if ort_module is None or not hasattr(ort_module, "register_execution_provider_library"):
        out["errors"].append("ORT lacks register_execution_provider_library")
        return out
    for rp in library_paths:
        try:
            try:
                ort_module.register_execution_provider_library(alias, rp)
            except TypeError:  # older signature without alias
                ort_module.register_execution_provider_library(rp)
            out["registered"] = _ep_present(ort_module, alias)
            if out["registered"]:
                out["path_used"] = rp
                break
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"{type(e).__name__}: {e}")
    return out


def _ep_present(ort_module, alias: str) -> bool:
    try:
        devices = ort_module.get_ep_devices()
    except Exception:  # noqa: BLE001
        return False
    names = {getattr(d, "ep_name", "") for d in devices}
    return alias in names or LEGACY_ALIAS in names
