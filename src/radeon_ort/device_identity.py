"""gfx1151-specific device identity: DXGI adapter enumeration -> LUID -> PnP cross-check.

Uses raw ctypes against dxgi.dll (no pywin32 dependency). Implements the mission rule:
the Radeon 8060S is an *integrated* GPU — we must NOT require Discrete=True,
dedicated VRAM > 0, or DXGI index == any other ordinal. Identity is established by
(vendor_id, device_id, PnP cross-check, description text) plus a per-session live
canary executed by the caller on the selected device.

LUID is boot-local: receipts record it as corroboration, never as permanent identity.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from dataclasses import dataclass, field

# --- DXGI COM plumbing via ctypes -------------------------------------------------

DXGI_ERROR_NOT_FOUND = 0x887A0002


@dataclass
class DxgiAdapter:
    index: int
    description: str
    vendor_id: int
    device_id: int
    sub_sys_id: int
    revision: int
    dedicated_video_mb: int
    dedicated_system_mb: int
    shared_sys_mb: int
    luid_high: int
    luid_low: int
    is_software: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def luid_str(self) -> str:
        return f"0x{self.luid_high & 0xFFFFFFFF:08X}:0x{self.luid_low & 0xFFFFFFFF:08X}"


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


IID_IDXGIFactory1 = _GUID(
    0x770AAE78, 0xF26F, 0x4DBA, (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87)
)


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_ulong), ("HighPart", wt.LONG)]


class _DXGI_ADAPTER_DESC1(ctypes.Structure):
    _fields_ = [
        ("Description", ctypes.c_wchar * 128),
        ("VendorId", ctypes.c_uint),
        ("DeviceId", ctypes.c_uint),
        ("SubSysId", ctypes.c_uint),
        ("Revision", ctypes.c_uint),
        ("DedicatedVideoMemory", ctypes.c_size_t),
        ("DedicatedSystemMemory", ctypes.c_size_t),
        ("SharedSystemMemory", ctypes.c_size_t),
        ("AdapterLuid", _LUID),
        ("Flags", ctypes.c_uint),
    ]


def _vtbl(instance):
    """Dereference a COM interface pointer to its vtable (one level of indirection)."""
    iface = ctypes.cast(instance, ctypes.c_void_p).value
    if not iface:
        raise OSError("null COM interface pointer")
    vtbl_ptr = ctypes.cast(iface, ctypes.POINTER(ctypes.c_void_p))[0]
    return ctypes.cast(vtbl_ptr, ctypes.POINTER(ctypes.c_void_p))


# Empirically verified on this host (see tests): IDXGIFactory1::EnumAdapters1 = vtable slot 12
_IDXGIFACTORY1_ENUMADAPTERS1 = 12
# IUnknown(0-2) + IDXGIObject(3-6) + IDXGIAdapter(7-9) => IDXGIAdapter1::GetDesc1 = 10
_IDXGIADAPTER1_GETDESC1 = 10


def enum_dxgi_adapters() -> list[DxgiAdapter]:
    """Enumerate physical adapters via IDXGIFactory1::EnumAdapters1."""
    dxgi = ctypes.WinDLL("dxgi.dll")
    factory = ctypes.c_void_p()
    hr = dxgi.CreateDXGIFactory1(
        ctypes.byref(IID_IDXGIFactory1), ctypes.byref(factory))
    if hr != 0:
        raise OSError(f"CreateDXGIFactory1 failed hr=0x{hr & 0xFFFFFFFF:08X}")

    adapters: list[DxgiAdapter] = []
    factory_vtbl = _vtbl(factory)
    # restype c_long (not HRESULT: ctypes auto-raises OSError on failing HRESULTs,
    # which would prevent us from handling DXGI_ERROR_NOT_FOUND as end-of-list)
    enum_adapters1 = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_void_p, ctypes.c_uint,
        ctypes.POINTER(ctypes.c_void_p))(factory_vtbl[_IDXGIFACTORY1_ENUMADAPTERS1])

    for i in range(16):
        adapter = ctypes.c_void_p()
        hr = enum_adapters1(factory, i, ctypes.byref(adapter))
        if hr & 0xFFFFFFFF == DXGI_ERROR_NOT_FOUND:  # HRESULT is signed here
            break
        if hr != 0:
            raise OSError(f"EnumAdapters1({i}) hr=0x{hr & 0xFFFFFFFF:08X}")
        adapter_vtbl = _vtbl(adapter)
        get_desc1 = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(_DXGI_ADAPTER_DESC1))(
            adapter_vtbl[_IDXGIADAPTER1_GETDESC1])
        desc = _DXGI_ADAPTER_DESC1()
        hr = get_desc1(adapter, ctypes.byref(desc))
        if hr != 0:
            raise OSError(f"GetDesc1({i}) hr=0x{hr & 0xFFFFFFFF:08X}")
        adapters.append(DxgiAdapter(
            index=i,
            description=desc.Description,
            vendor_id=desc.VendorId,
            device_id=desc.DeviceId,
            sub_sys_id=desc.SubSysId,
            revision=desc.Revision,
            dedicated_video_mb=int(desc.DedicatedVideoMemory // (1024 * 1024)),
            dedicated_system_mb=int(desc.DedicatedSystemMemory // (1024 * 1024)),
            shared_sys_mb=int(desc.SharedSystemMemory // (1024 * 1024)),
            luid_high=desc.AdapterLuid.HighPart,
            luid_low=desc.AdapterLuid.LowPart,
            is_software=bool(desc.Flags & 2),
        ))
    return adapters


AMD_VENDOR_ID = 0x1002

# Known AMD APU/discrete DeviceIds used ONLY as corroboration, never sole proof.
# Sources: pci.ids database (1002:7550 = Navi 48 [Radeon RX 9070/9070 XT/9070
# GRE]; 1002:7448/744C = Navi 31; 1002:1586 = gfx1151 per dxdiag on the
# reference host). 0x7480 was previously mislabeled gfx1201 — it is NOT a
# Navi 48 ID and has been removed (red test: test_7480_is_not_labeled_gfx1201).
# 0x7551 is a reported Navi 48 variant, hardware-unverified on any reference
# host; a table entry alone NEVER grants PHYSICAL_BINDING_CONFIRMED.
KNOWN_AMD_DEVICE_IDS = {
    0x1586: "gfx1151 (Radeon 8060S, Ryzen AI MAX)",  # per dxdiag ChipType 0x1586
    0x7448: "gfx1100 (Navi 31, RX 7900 XT class)",
    0x744C: "gfx1100 (Navi 31, RX 7900 XTX)",        # live-verified 2026-11-18 host PNP
    0x7550: "gfx1201 (Navi 48, RX 9070/9070 XT/9070 GRE)",  # pci.ids
    0x7551: "gfx1201 (Navi 48 variant, hardware-unverified)",
}

# Deterministic per-gfx DeviceId pinning: the requested gfx arch decides which
# DIDs are acceptable. A host whose AMD adapter is NOT in the requested set
# fails visibly instead of being silently selected (B0 fix).
GFX_DEVICE_IDS = {
    "gfx1100": {0x7448, 0x744C},
    "gfx1151": {0x1586},
    "gfx1201": {0x7550, 0x7551},
}

# Reverse map used ONLY to infer a requested gfx when the caller omits --gfx.
# An ambiguous DID (none here) or an uncatalogued AMD adapter fails closed; the
# harness never guesses a gfx from a generic "Radeon" name.
GFX_BY_DEVICE_ID = {did: gfx for gfx, dids in GFX_DEVICE_IDS.items() for did in dids}


def target_device_ids(gfx: str) -> tuple[int, ...]:
    """DIDs accepted for a gfx target, sorted for deterministic contracts."""
    return tuple(sorted(GFX_DEVICE_IDS.get(gfx, ())))


def gfx_for_device_id(device_id: int) -> str | None:
    return GFX_BY_DEVICE_ID.get(int(device_id))


def infer_gfx_from_adapters(adapters: list["DxgiAdapter"],
                            expect_vendor_id: int = AMD_VENDOR_ID) -> dict:
    """Infer the gfx target from live DXGI adapters, failing closed.

    Used only when the operator omits --gfx. More than one distinct supported
    gfx among the physical AMD adapters is ambiguous: never pick one silently.
    """
    found = []
    for a in adapters:
        if a.vendor_id != expect_vendor_id or a.is_software:
            continue
        gfx = gfx_for_device_id(a.device_id)
        found.append((a.device_id, gfx, a.description))
    if not found:
        return {"ok": False, "reason":
                "UNSUPPORTED_GFX_TARGET: no non-software AMD adapter with a "
                "catalogued gfx DID; pass --gfx explicitly after verifying hardware"}
    gfxs = sorted({f[1] for f in found if f[1]})
    if not gfxs:
        return {"ok": False, "reason":
                f"UNSUPPORTED_GFX_TARGET: AMD adapter DID(s) "
                f"{sorted(hex(f[0]) for f in found)} are not gfx-catalogued"}
    if len(gfxs) > 1:
        return {"ok": False, "reason":
                f"AMBIGUOUS_GFX_TARGET: multiple supported gfx present {gfxs}; "
                "pass --gfx explicitly"}
    return {"ok": True, "gfx": gfxs[0],
            "reason": f"inferred from DXGI DID 0x{found[0][0]:04X}"}


def memory_provenance(adapter: "DxgiAdapter",
                      wmi_adapter_ram_bytes: int | None = None) -> dict:
    """Audit the two independent GPU-memory sources without conflating them.

    DXGI AdapterDesc1 reports driver/BIOS memory *segments*. On an integrated
    APU, DedicatedVideoMemory is a carve-out segment (not soldered VRAM) and
    SharedSystemMemory is system RAM the GPU may use. WMI Win32_VideoController
    AdapterRAM is a uint32 byte count and saturates near 4 GiB on large-memory
    GPUs, so it must never be presented as physical VRAM.

    This function only labels the sources/units; it performs no measurement and
    does not turn any of them into a temperature/power/BW metric.
    """
    wmi_saturated = (wmi_adapter_ram_bytes is not None
                     and int(wmi_adapter_ram_bytes) >= 0xFFF00000)
    return {
        "schema_version": 1,
        "sources": {
            "dedicated_video_mb": {
                "source": "DXGI_ADAPTER_DESC1.DedicatedVideoMemory // 1048576",
                "unit": "MiB (rounded down)",
                "meaning": "driver/BIOS-reported dedicated segment; on an integrated "
                           "APU this is a carve-out, NOT independent physical VRAM",
            },
            "dedicated_system_mb": {
                "source": "DXGI_ADAPTER_DESC1.DedicatedSystemMemory // 1048576",
                "unit": "MiB (rounded down)",
                "meaning": "system memory dedicated to the adapter segment",
            },
            "shared_system_mb": {
                "source": "DXGI_ADAPTER_DESC1.SharedSystemMemory // 1048576",
                "unit": "MiB (rounded down)",
                "meaning": "system RAM the GPU is allowed to share; not VRAM",
            },
            "wmi_adapter_ram_bytes": {
                "source": "WMI Win32_VideoController.AdapterRAM",
                "unit": "bytes",
                "meaning": "uint32 field; saturates near 4 GiB and is unreliable "
                           "for >4 GiB devices; do NOT use as VRAM capacity",
            },
        },
        "values": {
            "dedicated_video_mb": adapter.dedicated_video_mb,
            "dedicated_system_mb": adapter.dedicated_system_mb,
            "shared_system_mb": adapter.shared_sys_mb,
            "wmi_adapter_ram_bytes": wmi_adapter_ram_bytes,
        },
        "wmi_adapter_ram_saturated": wmi_saturated,
        "measurement_status": "DXGI_STATIC_ENUMERATION_ONLY; runtime VRAM usage, "
                              "bandwidth, temperature and power are NOT_MEASURED",
    }

# gfx-arch-SPECIFIC name needles for the legacy pre-DID fallback. The generic
# 'Radeon' needle is deliberately excluded: it matches every AMD card and would
# let e.g. a gfx1151 request silently select a 7900 XTX (live-discovered hole).
GFX_FALLBACK_NEEDLES = {
    "gfx1151": ("8060s",),
}


def _describe_found(adapters: list[DxgiAdapter]) -> str:
    parts = []
    for a in adapters:
        known = KNOWN_AMD_DEVICE_IDS.get(a.device_id, "unknown/uncatalogued DID")
        parts.append(f"0x{a.device_id:X} ({known}) {a.description!r}")
    return "; ".join(parts)


def resolve_target_adapter(adapters: list[DxgiAdapter], *, gfx: str,
                           expect_vendor_id: int = AMD_VENDOR_ID,
                           name_needles: tuple[str, ...] = (),
                           allow_unverified_name_fallback: bool = False) -> dict:
    """Select the intended physical GPU. Fail WRONG_OR_AMBIGUOUS_DEVICE on ambiguity.

    Never requires Discrete=True / DedicatedVideoMemory>0 (integrated 8060S has
    ~0 dedicated and large SharedSystemMemory).

    B0 contract: selection is DID-pinned per gfx target. Requesting gfx1201 on
    a host whose only AMD adapter is gfx1100 FAILS with a reason naming what
    was found — never a silent any-AMD pick. The legacy gfx1151 name-only
    fallback is *opt-in for diagnostics*, never the default identity path.
    Name-only selection is tagged did_match=False and cannot certify GPU FULL.
    """
    amd = [a for a in adapters if a.vendor_id == expect_vendor_id and not a.is_software]
    if not amd:
        return {"ok": False, "reason": "WRONG_OR_AMBIGUOUS_DEVICE: no non-software AMD adapter found"}

    dids = GFX_DEVICE_IDS.get(gfx)
    if dids is None:
        return {"ok": False,
                "reason": f"UNSUPPORTED_GFX_TARGET: {gfx}; supported: "
                          f"{sorted(GFX_DEVICE_IDS)}"}

    cands = [a for a in amd if a.device_id in dids]
    did_match = bool(cands)
    if not cands and allow_unverified_name_fallback and gfx in GFX_FALLBACK_NEEDLES:
        # legacy name-only corroboration, arch-SPECIFIC needles only
        needles = GFX_FALLBACK_NEEDLES[gfx]
        cands = [a for a in amd
                 if any(n in a.description.lower() for n in needles)]
    if not cands:
        return {"ok": False,
                "reason": f"WRONG_OR_AMBIGUOUS_DEVICE: no adapter with {gfx} "
                          f"DeviceId(s) {sorted(hex(d) for d in dids)}; found: "
                          f"{_describe_found(amd)}"}
    dup_note = None

    if len(cands) == 0:
        return {"ok": False, "reason": f"WRONG_OR_AMBIGUOUS_DEVICE: no candidate for {gfx}"}
    if len(cands) > 1:
        # An integrated Radeon can be enumerated multiple times (e.g. compute +
        # display enumeration, LUID differs per instance). Multiple entries with
        # IDENTICAL vendor+deviceId+description denote the same physical silicon;
        # keep the lowest index and record every observed LUID as corroboration.
        uniq = {(a.vendor_id, a.device_id, a.description): None for a in cands}
        if len(uniq) == 1:
            dup_note = (f"same physical {gfx} enumerated {len(cands)}x "
                        f"(LUIDs: {[a.luid_str for a in cands]}); selected index "
                        f"{min(a.index for a in cands)}")
            cands = [min(cands, key=lambda a: a.index)]
        else:
            return {"ok": False, "reason":
                    f"WRONG_OR_AMBIGUOUS_DEVICE: {len(cands)} distinct AMD candidates "
                    f"({[a.description for a in cands]})"}

    a = cands[0]
    corroborated = []
    if a.device_id in KNOWN_AMD_DEVICE_IDS:
        corroborated.append(f"DeviceId 0x{a.device_id:X} -> {KNOWN_AMD_DEVICE_IDS[a.device_id]}")
    for n in name_needles:
        if n.lower() in a.description.lower():
            corroborated.append(f"description contains '{n}'")
    result = {
        "ok": True,
        "adapter": a,
        "did_match": did_match,
        "identity_method": "DXGI vendor+deviceId per-gfx pinning (integrated allowed)",
        "corroboration": corroborated,
        "note": "LUID is boot-local; recorded as session corroboration only",
        "luid_valid": not (a.luid_high == 0 and a.luid_low == 0),
    }
    if not did_match:
        result["warning"] = ("name-needle fallback used (DID not in target set); "
                             "binding confidence reduced — canary required")
    if dup_note is not None:
        result["dedup_note"] = dup_note
    return result


def canary_inputs(seed: int = 1234) -> dict:
    """Deterministic canary tensor spec: values chosen so CPU vs GPU float paths
    produce small but nonzero arithmetic (0.1234567-style mantissa stress)."""
    import numpy as np
    rng = np.random.default_rng(seed)
    a = rng.standard_normal((256, 256)).astype(np.float32) * 0.1234567
    b = rng.standard_normal((256, 256)).astype(np.float32) * 0.7654321
    return {"a": a, "b": b}
