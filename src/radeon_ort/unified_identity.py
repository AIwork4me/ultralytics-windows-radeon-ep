"""unified_identity — the four-class identity chain for the unified runtime.

Classes (never interchangeable, see docs/unified/ARCHITECTURE.md §3):
  1. physical GPU      — PnP + DXGI VID/DID/description + boot-local LUID
  2. catalog EP entry  — Windows ML 2.x EpName/version/ReadyState/library_path
  3. ORT alias         — library registration name inside THIS Python process
  4. OrtEpDevice       — EP-device candidate (metadata LUID/Discrete), EP-internal index

binding confidence labels:
  PHYSICAL_BINDING_CONFIRMED
  EP_DEVICE_SELECTED + PHYSICAL_GPU_CORROBORATED; BINDING_UNPROVEN
  UNPROVEN
Fail-closed: any missing link degrades or blocks; never guesses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CONFIRMED = "PHYSICAL_BINDING_CONFIRMED"
CORROBORATED_UNPROVEN = "EP_DEVICE_SELECTED+PHYSICAL_GPU_CORROBORATED;BINDING_UNPROVEN"
UNPROVEN = "UNPROVEN"


@dataclass
class IdentityChain:
    physical: dict[str, Any] | None = None      # class 1 evidence
    catalog: dict[str, Any] | None = None       # class 2 evidence (may be absent on BYO)
    ort_alias: str | None = None                # class 3
    ep_device: dict[str, Any] | None = None     # class 4
    failures: list[str] = field(default_factory=list)

    def confidence(self) -> str:
        """Derive the binding-confidence label from present evidence."""
        if self.physical is None:
            return UNPROVEN
        if self.ep_device is None:
            return UNPROVEN
        ep_luid = str((self.ep_device.get("metadata") or {}).get("LUID", ""))
        phys_luid = str(self.physical.get("luid_key", ""))
        # a full chain requires: physical LUID derived + EP-device LUID equal
        if phys_luid and ep_luid and ep_luid == phys_luid:
            return CONFIRMED
        return CORROBORATED_UNPROVEN


def resolve_physical(gfx: str, adapters: list | None = None) -> dict[str, Any]:
    """Class 1: resolve the physical adapter for a gfx arch (B0 DID pinning).

    Returns {"ok": True, "adapter":..., "luid_key":..., ...} or
    {"ok": False, "reason": ...} — never a silent any-AMD pick.
    """
    from .device_identity import enum_dxgi_adapters, resolve_target_adapter

    adapters = adapters if adapters is not None else enum_dxgi_adapters()
    res = resolve_target_adapter(adapters, gfx=gfx)
    if not res.get("ok"):
        return {"ok": False, "reason": res.get("reason", "resolve failed")}
    a = res["adapter"]
    from .rca2a_ep import physical_luid_key

    luid = physical_luid_key(a)
    return {
        "ok": True,
        "adapter": a,
        "gfx": gfx,
        "description": a.description,
        "vendor_id": a.vendor_id,
        "device_id": a.device_id,
        "luid_key": luid,
        "luid_str": a.luid_str,
        "luid_valid": bool(res.get("luid_valid", luid is not None)),
        "dxgi_index": a.index,  # per-API ordinal; NOT transferable to other APIs
        "did_match": bool(res.get("did_match")),
        "corroboration": res.get("corroboration", []),
        "dedup_note": res.get("dedup_note"),
    }


def resolve_catalog_readonly(bootstrap_version: str | None = "2.3") -> dict[str, Any]:
    """Class 2: read-only Windows ML catalog discovery (no installs, ever)."""
    from .windows_ml_ep_catalog import probe_windows_ml_api

    caps = probe_windows_ml_api(bootstrap_version=bootstrap_version)
    caps.pop("_bootstrap_ctx", None)
    return caps


def resolve_ep_device(target_luid: int | str | None, ort_module=None) -> dict[str, Any]:
    """Class 4: pick the AMDGPU EP device pinned to the physical LUID."""
    from .rca2a_ep import select_amdgpu_ep_device

    dev, sel = select_amdgpu_ep_device(target_luid)
    if dev is None:
        return {"ok": False, "selection": sel}
    from .rca2a_ep import _ep_device_metadata

    md = _ep_device_metadata(dev)
    luid_ok = str(md.get("LUID", "")) == str(target_luid) if target_luid is not None else None
    return {"ok": True, "metadata": md, "selection_basis": sel.get("basis"),
            "luid_match": luid_ok}


def build_chain(gfx: str, *, catalog: dict | None = None,
                ort_module=None) -> IdentityChain:
    """Assemble the full chain; failures are recorded, never guessed over."""
    chain = IdentityChain()
    phys = resolve_physical(gfx)
    if phys.get("ok"):
        chain.physical = phys
    else:
        chain.failures.append(f"physical:{phys.get('reason')}")
        return chain
    if catalog is not None:
        chain.catalog = catalog
    ep = resolve_ep_device(phys.get("luid_key"))
    if ep.get("ok"):
        chain.ep_device = ep
        if ep.get("luid_match") is False:
            chain.failures.append("ep_device_luid_mismatch")
    else:
        chain.failures.append("ep_device:" + str(ep.get("selection")))
    return chain
