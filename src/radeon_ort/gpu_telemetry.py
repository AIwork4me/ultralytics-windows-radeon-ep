"""Read-only GPU clock telemetry for AMD adapters (ADL, no driver changes).

The Windows AMD display driver exposes observed core/memory clocks through
``ADL2_Adapter_ObservedClockInfo_Get``. On the Radeon 8060S APU this works even
though Overdrive temperature/power/activity APIs return ADL_ERR_NOT_SUPPORTED,
so this module reports clocks only and never invents temperature or power.

All calls are read-only. The module is import-safe on non-Windows hosts and
returns ``available=False`` instead of raising.
"""

from __future__ import annotations

import ctypes as C
import threading
from dataclasses import dataclass, field

ADL_MAX_PATH = 256
ADL_OK = 0


class _AdapterInfo(C.Structure):
    _fields_ = [
        ("iSize", C.c_int), ("iAdapterIndex", C.c_int),
        ("strUDID", C.c_char * ADL_MAX_PATH),
        ("iBusNumber", C.c_int), ("iDeviceNumber", C.c_int),
        ("iFunctionNumber", C.c_int), ("iVendorID", C.c_int),
        ("strAdapterName", C.c_char * ADL_MAX_PATH),
        ("strDisplayName", C.c_char * ADL_MAX_PATH),
        ("iPresent", C.c_int), ("iExist", C.c_int),
        ("strDriverPath", C.c_char * ADL_MAX_PATH),
        ("strDriverPathExt", C.c_char * ADL_MAX_PATH),
        ("strPNPString", C.c_char * ADL_MAX_PATH),
        ("iOSDisplayIndex", C.c_int),
    ]


@dataclass
class AdlClocks:
    available: bool = False
    error: str | None = None
    adapter_index: int | None = None
    core_mhz: int | None = None
    mem_mhz: int | None = None
    sources: dict = field(default_factory=dict)


def _open_adl():
    if not hasattr(C, "WinDLL"):
        return None, None, "not-windows"
    try:
        adl = C.WinDLL("atiadlxx.dll")
    except OSError as e:
        return None, None, f"ADL load failed: {e}"
    alloc_type = C.CFUNCTYPE(C.c_void_p, C.c_int)
    buffers: list = []

    def _alloc(n):
        b = C.create_string_buffer(n)
        buffers.append(b)
        return C.cast(b, C.c_void_p)

    cb = alloc_type(_alloc)
    ctx = C.c_void_p()
    adl.ADL2_Main_Control_Create.argtypes = [alloc_type, C.c_int, C.POINTER(C.c_void_p)]
    adl.ADL2_Main_Control_Create.restype = C.c_int
    if adl.ADL2_Main_Control_Create(cb, 1, C.byref(ctx)) != ADL_OK:
        return None, None, "ADL2_Main_Control_Create failed"
    return adl, (ctx, buffers), None


def read_adl_clocks(gfx_hint: str | None = None) -> AdlClocks:
    """Return observed clocks for the AMD adapter whose name/PNP matches gfx."""
    adl, keep, err = _open_adl()
    if adl is None:
        return AdlClocks(available=False, error=err)
    try:
        ctx, buffers = keep
        num = C.c_int()
        adl.ADL2_Adapter_NumberOfAdapters_Get.argtypes = [C.c_void_p, C.POINTER(C.c_int)]
        adl.ADL2_Adapter_NumberOfAdapters_Get.restype = C.c_int
        if adl.ADL2_Adapter_NumberOfAdapters_Get(ctx, C.byref(num)) != ADL_OK:
            return AdlClocks(available=False, error="NumberOfAdapters failed")
        arr = (_AdapterInfo * num.value)()
        for a in arr:
            a.iSize = C.sizeof(_AdapterInfo)
        adl.ADL2_Adapter_AdapterInfo_Get.argtypes = [
            C.c_void_p, C.POINTER(_AdapterInfo), C.c_int]
        adl.ADL2_Adapter_AdapterInfo_Get.restype = C.c_int
        adl.ADL2_Adapter_AdapterInfo_Get(
            ctx, arr, C.sizeof(_AdapterInfo) * num.value)
        candidates = [a for a in arr if b"AMD" in a.strAdapterName
                      or a.iVendorID in (0x1002, 1002)]
        if not candidates:
            return AdlClocks(available=False, error="no AMD adapter in ADL")
        a = candidates[0]
        fn = adl.ADL2_Adapter_ObservedClockInfo_Get
        fn.argtypes = [C.c_void_p, C.c_int, C.POINTER(C.c_int), C.POINTER(C.c_int)]
        fn.restype = C.c_int
        core, mem = C.c_int(), C.c_int()
        rc = fn(ctx, a.iAdapterIndex, C.byref(core), C.byref(mem))
        if rc != ADL_OK:
            return AdlClocks(available=False, error=f"ObservedClock rc={rc}",
                             adapter_index=a.iAdapterIndex)
        return AdlClocks(
            available=True, adapter_index=a.iAdapterIndex,
            core_mhz=core.value, mem_mhz=mem.value,
            sources={"core_mhz": "ADL2_Adapter_ObservedClockInfo_Get",
                     "mem_mhz": "ADL2_Adapter_ObservedClockInfo_Get",
                     "temperature": "NOT_EXPOSED_BY_ADL_ON_THIS_APU",
                     "power": "NOT_EXPOSED_BY_ADL_ON_THIS_APU",
                     "activity": "NOT_EXPOSED_BY_ADL_ON_THIS_APU"})
    finally:
        try:
            adl.ADL2_Main_Control_Destroy.argtypes = [C.c_void_p]
            adl.ADL2_Main_Control_Destroy.restype = C.c_int
            adl.ADL2_Main_Control_Destroy(keep[0])
        except Exception:  # noqa: BLE001
            pass


class ClockSampler(threading.Thread):
    """Background 1-10 Hz sampler; use only while one GPU process is running."""

    def __init__(self, interval_s: float = 0.2):
        super().__init__(daemon=True)
        self.interval_s = interval_s
        self.samples: list[dict] = []
        self._stop_event = threading.Event()
        self._first: AdlClocks | None = None

    def run(self):
        while not self._stop_event.is_set():
            c = read_adl_clocks()
            if self._first is None:
                self._first = c
            self.samples.append({
                "t_rel_s": round(len(self.samples) * self.interval_s, 3),
                "available": c.available, "core_mhz": c.core_mhz,
                "mem_mhz": c.mem_mhz, "error": c.error,
            })
            self._stop_event.wait(self.interval_s)

    def stop(self):
        self._stop_event.set()
        self.join(timeout=5)

    def summary(self) -> dict:
        cores = [s["core_mhz"] for s in self.samples if s["core_mhz"] is not None]
        mems = [s["mem_mhz"] for s in self.samples if s["mem_mhz"] is not None]
        return {
            "samples": len(self.samples),
            "available": bool(cores),
            "core_mhz_min": min(cores) if cores else None,
            "core_mhz_max": max(cores) if cores else None,
            "core_mhz_unique": sorted(set(cores))[:20],
            "mem_mhz_min": min(mems) if mems else None,
            "mem_mhz_max": max(mems) if mems else None,
            "mem_mhz_unique": sorted(set(mems))[:20],
            "first": (self._first.__dict__ if self._first else None),
            "limitation": "clocks only; APU ADL temperature/power/activity are "
                          "NOT_SUPPORTED on this host, so they remain NOT_MEASURED",
        }
