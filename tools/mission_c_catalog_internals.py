"""Mission C: direct flat-C catalog probe (read-only).

Exercises the *documented* flat-C API from the official `windowsml` wheel
(WinMLEpCatalogCreate / EnumProviders / FindProvider) plus the *undocumented*
WinMLEpCatalogCreateWithMode export that is present in the same Microsoft-signed
binary but absent from the published WinMLEpCatalog.h header.

No session is created, no EP is registered, nothing is installed.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys


def main() -> int:
    import windowsml
    from windowsml import _bindings as b

    dll_path = os.path.join(os.path.dirname(os.path.abspath(windowsml.__file__)), "lib",
                            "Microsoft.Windows.AI.MachineLearning.dll")
    dll = b._dll

    out: dict = {
        "dll_path": dll_path,
        "exports_checked": {},
        "create": None,
        "enum": None,
        "find_provider": None,
        "create_with_mode": [],
    }

    for name in ("WinMLEpCatalogCreate", "WinMLEpCatalogEnumProviders",
                 "WinMLEpCatalogFindProvider", "WinMLEpCatalogCreateWithMode",
                 "WinMLEpCatalogRegister"):
        try:
            getattr(dll, name)
            out["exports_checked"][name] = True
        except AttributeError:
            out["exports_checked"][name] = False

    # --- baseline: documented create + enumerate ---
    handle = b.EpCatalogHandle()
    hr = b.WinMLEpCatalogCreate(ctypes.byref(handle))
    out["create"] = {"hresult": f"0x{hr & 0xFFFFFFFF:08X}"}

    providers: list[dict] = []

    @b.EpEnumCallback
    def _cb(ep, info_ptr, ctx):
        i = info_ptr.contents
        providers.append({
            "name": (i.name or b"").decode("utf-8", "replace"),
            "version": (i.version or b"").decode("utf-8", "replace"),
            "packageFamilyName": (i.packageFamilyName or b"").decode("utf-8", "replace"),
            "libraryPath": (i.libraryPath or b"").decode("utf-8", "replace"),
            "packageRootPath": (i.packageRootPath or b"").decode("utf-8", "replace"),
            "readyState": int(i.readyState),
            "certification": int(i.certification),
        })
        return True

    if hr == 0:
        ehr = b.WinMLEpCatalogEnumProviders(handle, _cb, None)
        out["enum"] = {"hresult": f"0x{ehr & 0xFFFFFFFF:08X}", "provider_count": len(providers),
                       "providers": providers}

        # --- direct lookup of AMDGPU, bypassing enumeration ---
        finds = []
        for pfn in (None, "Microsoft.WinML.AMD.GPU.EP.Framework.2_8wekyb3d8bbwe",
                    "MicrosoftCorporationII.WinML.AMD.GPU.EP.1.8_8wekyb3d8bbwe"):
            ep = b.EpHandle()
            entry: dict = {"packageFamilyName": pfn}
            try:
                fhr = b.WinMLEpCatalogFindProvider(
                    handle, b"AMDGPUExecutionProvider",
                    pfn.encode() if pfn else None, ctypes.byref(ep))
                entry["hresult"] = f"0x{fhr & 0xFFFFFFFF:08X}"
                entry["found"] = bool(fhr == 0 and ep)
            except OSError as exc:
                code = exc.winerror if exc.winerror is not None else 0
                entry["hresult"] = f"0x{code & 0xFFFFFFFF:08X}"
                entry["found"] = False
                entry["winerror_text"] = str(exc)
            if entry["found"]:
                try:
                    st = ctypes.c_int()
                    b.WinMLEpGetReadyState(ep, ctypes.byref(st))
                    entry["readyState"] = int(st.value)
                except Exception as exc:  # noqa: BLE001
                    entry["readyState_error"] = repr(exc)
            finds.append(entry)
        out["find_provider"] = finds

    # --- undocumented: CreateWithMode(mode, &handle) ---
    try:
        fn = dll.WinMLEpCatalogCreateWithMode
    except AttributeError:
        out["create_with_mode"] = [{"error": "export_absent"}]
    else:
        fn.argtypes = [ctypes.c_int, ctypes.POINTER(b.EpCatalogHandle)]
        fn.restype = ctypes.HRESULT
        for mode in range(6):
            h = b.EpCatalogHandle()
            rec: dict = {"mode": mode}
            try:
                mhr = fn(mode, ctypes.byref(h))
                rec["hresult"] = f"0x{mhr & 0xFFFFFFFF:08X}"
            except OSError as exc:
                code = exc.winerror if exc.winerror is not None else 0
                rec["hresult"] = f"0x{code & 0xFFFFFFFF:08X}"
                rec["exception"] = str(exc)
                out["create_with_mode"].append(rec)
                continue
            rec["provider_count"] = None
            if mhr == 0 and h:
                got: list[str] = []

                @b.EpEnumCallback
                def _cb2(ep, info_ptr, ctx, _got=got):
                    i = info_ptr.contents
                    _got.append((i.name or b"").decode("utf-8", "replace"))
                    return True

                try:
                    ehr2 = b.WinMLEpCatalogEnumProviders(h, _cb2, None)
                    rec["enum_hresult"] = f"0x{ehr2 & 0xFFFFFFFF:08X}"
                    rec["provider_count"] = len(got)
                    rec["providers"] = got
                except OSError as exc:
                    rec["enum_exception"] = repr(exc)
                try:
                    b.WinMLEpCatalogRelease(h)
                except Exception:  # noqa: BLE001
                    pass
            out["create_with_mode"].append(rec)

    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
