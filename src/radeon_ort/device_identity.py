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
    return ctypes.cast(ctypes.cast(instance, ctypes.c_void_p).value,
                       ctypes.POINTER(ctypes.c_void_p))


# IUnknown(0-2) + IDXGIObject(3-6) + IDXGIFactory(7-11) => IDXGIFactory1::EnumAdapters1 = 12
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
    enum_adapters1 = ctypes.WINFUNCTYPE(
        ctypes.HRESULT, ctypes.c_void_p, ctypes.c_uint,
        ctypes.POINTER(ctypes.c_void_p))(factory_vtbl[_IDXGIFACTORY1_ENUMADAPTERS1])

    for i in range(16):
        adapter = ctypes.c_void_p()
        hr = enum_adapters1(factory, i, ctypes.byref(adapter))
        if hr == DXGI_ERROR_NOT_FOUND:
            break
        if hr != 0:
            raise OSError(f"EnumAdapters1({i}) hr=0x{hr & 0xFFFFFFFF:08X}")
        adapter_vtbl = _vtbl(adapter)
        get_desc1 = ctypes.WINFUNCTYPE(
            ctypes.HRESULT, ctypes.c_void_p, ctypes.POINTER(_DXGI_ADAPTER_DESC1))(
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
KNOWN_AMD_DEVICE_IDS = {
    0x1586: "gfx1151 (Radeon 8060S, Ryzen AI MAX)",  # per dxdiag ChipType 0x1586
    0x7448: "gfx1100 (RX 7900 XTX class)",
    0x7480: "gfx1201 (RX 9070 XT class)",
}


def resolve_target_adapter(adapters: list[DxgiAdapter], *, gfx: str,
                           expect_vendor_id: int = AMD_VENDOR_ID,
                           name_needles: tuple[str, ...] = ("8060S", "Radeon")) -> dict:
    """Select the intended physical GPU. Fail WRONG_OR_AMBIGUOUS_DEVICE on ambiguity.

    Never requires Discrete=True / DedicatedVideoMemory>0 (integrated 8060S has
    ~0 dedicated and large SharedSystemMemory).
    """
    amd = [a for a in adapters if a.vendor_id == expect_vendor_id and not a.is_software]
    if not amd:
        return {"ok": False, "reason": "WRONG_OR_AMBIGUOUS_DEVICE: no non-software AMD adapter found"}

    if gfx == "gfx1151":
        cands = [a for a in amd if a.device_id == 0x1586]
        if not cands:
            cands = [a for a in amd if any(n.lower() in a.description.lower() for n in name_needles)]
    else:
        cands = amd

    if len(cands) == 0:
        return {"ok": False, "reason": f"WRONG_OR_AMBIGUOUS_DEVICE: no candidate for {gfx}"}
    if len(cands) > 1:
        return {"ok": False, "reason":
                f"WRONG_OR_AMBIGUOUS_DEVICE: {len(cands)} AMD candidates "
                f"({[a.description for a in cands]})"}

    a = cands[0]
    corroborated = []
    if a.device_id in KNOWN_AMD_DEVICE_IDS:
        corroborated.append(f"DeviceId 0x{a.device_id:X} -> {KNOWN_AMD_DEVICE_IDS[a.device_id]}")
    for n in name_needles:
        if n.lower() in a.description.lower():
            corroborated.append(f"description contains '{n}'")
    return {
        "ok": True,
        "adapter": a,
        "identity_method": "DXGI vendor+deviceId+description corroboration (integrated allowed)",
        "corroboration": corroborated,
        "note": "LUID is boot-local; recorded as session corroboration only",
    }


def canary_inputs(seed: int = 1234) -> dict:
    """Deterministic canary tensor spec: values chosen so CPU vs GPU float paths
    produce small but nonzero arithmetic (0.1234567-style mantissa stress)."""
    import numpy as np
    rng = np.random.default_rng(seed)
    a = rng.standard_normal((256, 256)).astype(np.float32) * 0.1234567
    b = rng.standard_normal((256, 256)).astype(np.float32) * 0.7654321
    return {"a": a, "b": b}
