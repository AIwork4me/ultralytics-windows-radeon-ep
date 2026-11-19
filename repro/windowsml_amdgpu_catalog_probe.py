#!/usr/bin/env python
"""Standalone, READ-ONLY minimal reproduction: Windows ML EP catalog on an AMD GPU.

Purpose
-------
Answer one question on a clean machine with no repository checkout:

    "On this host, does the Windows ML 2.x ExecutionProviderCatalog list any
     execution provider, and specifically AMDGPUExecutionProvider?"

Nothing is installed, registered, downloaded or written outside the optional
--out file. The script imports only the Python standard library plus the
official ``windowsml`` package (and ``onnxruntime`` only to read its version if
it happens to be installed). It never imports this repository.

Usage
-----
    python windowsml_amdgpu_catalog_probe.py
    python windowsml_amdgpu_catalog_probe.py --out probe.json --gfx gfx1151

The machine-readable block is printed between the markers below so it can be
piped into another tool:

    ===WINML_AMDGPU_CATALOG_PROBE_JSON_BEGIN===
    {...}
    ===WINML_AMDGPU_CATALOG_PROBE_JSON_END===

Exit codes
----------
    0  the catalog listed at least one provider
    3  the catalog API succeeded but listed ZERO providers (BLOCKED_EXTERNAL)
    4  the official ``windowsml`` package is not installed in this interpreter
    5  unexpected error while probing the catalog
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import platform
import sys
import uuid
from ctypes import wintypes
from datetime import UTC, datetime

PROBE_SCHEMA = "winml_amdgpu_catalog_probe/v1"
BEGIN = "===WINML_AMDGPU_CATALOG_PROBE_JSON_BEGIN==="
END = "===WINML_AMDGPU_CATALOG_PROBE_JSON_END==="

# DXGI / COM interface ids (documented Windows SDK GUIDs).
IID_IDXGIFACTORY1 = "{770aae78-f26f-4dba-a829-253c83d1b387}"
IID_IDXGIDEVICE = "{54ec77fa-1377-44e6-8c32-88fd5f44c84c}"

# ReadyState values from windowsml.EpReadyState (Ready=0, NotReady=1, NotPresent=2).
READY_STATE_NAMES = {0: "Ready", 1: "NotReady", 2: "NotPresent"}

_APPX_KEY = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Appx\AppxAllUserStore"
             r"\Applications")


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class DXGI_ADAPTER_DESC1(ctypes.Structure):
    _fields_ = [("Description", wintypes.WCHAR * 128),
                ("VendorId", wintypes.UINT),
                ("DeviceId", wintypes.UINT),
                ("SubSysId", wintypes.UINT),
                ("Revision", wintypes.UINT),
                ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t),
                ("SharedSystemMemory", ctypes.c_size_t),
                ("AdapterLuid", LUID),
                ("Flags", wintypes.UINT)]


def _guid(text: str) -> GUID:
    return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


def _com(obj: ctypes.c_void_p, index: int, restype, *argtypes):
    """Call COM method #index of obj (vtable slot), typed with WINFUNCTYPE."""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(vtable[index])


def _hresult(code: int) -> str:
    return f"0x{code & 0xFFFFFFFF:08X}"


def _decode_umd_version(value: int) -> str:
    """Decode IDXGIAdapter::CheckInterfaceSupport's LARGE_INTEGER UMD version.

    Layout: high 32 bits = (major << 16 | minor), low 32 bits = (build << 16 | revision),
    which is the same 4-part number dxdiag reports as the display driver version.
    """
    high = (value >> 32) & 0xFFFFFFFF
    low = value & 0xFFFFFFFF
    return f"{high >> 16}.{high & 0xFFFF}.{low >> 16}.{low & 0xFFFF}"


def dxgi_adapters() -> dict:
    """Enumerate adapters through DXGI with ctypes only (no pywin32)."""
    out: dict = {"available": False, "adapters": [], "errors": []}
    try:
        dxgi = ctypes.WinDLL("dxgi.dll")
    except OSError as e:
        out["errors"].append({"stage": "load_dxgi", "message": str(e)})
        return out
    try:
        create = dxgi.CreateDXGIFactory1
    except AttributeError as e:
        out["errors"].append({"stage": "resolve_CreateDXGIFactory1", "message": str(e)})
        return out
    iid = _guid(IID_IDXGIFACTORY1)
    factory = ctypes.c_void_p()
    create.argtypes = [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    create.restype = ctypes.c_long
    hr = create(ctypes.byref(iid), ctypes.byref(factory))
    if hr != 0 or not factory:
        out["errors"].append({"stage": "CreateDXGIFactory1", "hresult": _hresult(hr)})
        return out
    out["available"] = True
    enum = _com(factory, 12, ctypes.c_long, wintypes.UINT,
                ctypes.POINTER(ctypes.c_void_p))  # IDXGIFactory1::EnumAdapters1
    index = 0
    while True:
        adapter = ctypes.c_void_p()
        hr = enum(factory, index, ctypes.byref(adapter))
        if hr != 0 or not adapter:
            break
        desc = DXGI_ADAPTER_DESC1()
        get_desc = _com(adapter, 10, ctypes.c_long,
                        ctypes.POINTER(DXGI_ADAPTER_DESC1))  # IDXGIAdapter1::GetDesc1
        if get_desc(adapter, ctypes.byref(desc)) == 0:
            row = {
                "index": index,
                "description": desc.Description,
                "vendor_id": f"0x{desc.VendorId:04X}",
                "device_id": f"0x{desc.DeviceId:04X}",
                "subsys_id": f"0x{desc.SubSysId:08X}",
                "revision": desc.Revision,
                "is_software": bool(desc.Flags & 2),
                "driver_version": None,
            }
            umd = ctypes.c_longlong()
            check = _com(adapter, 9, ctypes.c_long, ctypes.POINTER(GUID),
                         ctypes.POINTER(ctypes.c_longlong))  # CheckInterfaceSupport
            if check(adapter, ctypes.byref(_guid(IID_IDXGIDEVICE)),
                     ctypes.byref(umd)) == 0:
                row["driver_version"] = _decode_umd_version(umd.value)
            out["adapters"].append(row)
        release = _com(adapter, 2, wintypes.ULONG)  # IUnknown::Release
        release(adapter)
        index += 1
    release_factory = _com(factory, 2, wintypes.ULONG)
    release_factory(factory)
    return out


def windows_build() -> dict:
    """OS build from sys.getwindowsversion plus UBR/DisplayVersion from the registry."""
    out: dict = {"platform": platform.platform(), "major": None, "minor": None,
                 "build": None, "ubr": None, "display_version": None}
    try:
        info = sys.getwindowsversion()
    except AttributeError as e:  # not Windows
        out["platform"] = f"not-windows ({e})"
        return out
    out.update({"major": info.major, "minor": info.minor, "build": info.build})
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
            for field, name in (("ubr", "UBR"), ("display_version", "DisplayVersion")):
                try:
                    out[field] = winreg.QueryValueEx(key, name)[0]
                except OSError:
                    pass
    except OSError:
        pass
    return out


def ep_package_versions() -> dict:
    """Read-only inventory of the WinML / EP AppX packages (registry, no WMI)."""
    out: dict = {"windows_ml_shared_runtime": None, "ep_packages": [], "error": None}
    seen: dict[str, str] = {}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _APPX_KEY) as key:
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
    except OSError as e:
        out["error"] = str(e)
        return out
    for name, version in sorted(seen.items()):
        if name.startswith("WindowsWorkload.WinMLShared"):
            out["windows_ml_shared_runtime"] = f"{name} {version}"
        elif "EP" in name or "ExecutionProvider" in name:
            out["ep_packages"].append(f"{name} {version}")
    return out


def library_versions() -> dict:
    out: dict = {"windowsml_python": None, "onnxruntime": None}
    try:
        import importlib.metadata as md
        out["windowsml_python"] = md.version("windowsml")
    except Exception:  # noqa: BLE001 - package metadata is best-effort only
        try:
            import windowsml  # noqa: F401
            out["windowsml_python"] = "installed (version metadata unavailable)"
        except Exception as e:  # noqa: BLE001
            out["windowsml_python"] = None
            out["windowsml_error"] = f"{type(e).__name__}: {e}"
    try:
        import onnxruntime as ort
        out["onnxruntime"] = getattr(ort, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        out["onnxruntime"] = None
    return out


def _redact(text: str) -> str:
    """Drop any absolute user-profile path so the output is safe to paste."""
    lowered = text.lower()
    marker = "\\users\\"
    pos = lowered.find(marker)
    if pos == -1:
        return text
    tail = text[pos + len(marker):]
    rest = tail.split("\\", 1)
    prefix = "\\Users\\<redacted>"
    return text[:pos] + prefix + (("\\" + rest[1]) if len(rest) > 1 else "")


def describe_provider(provider) -> dict:
    state = int(getattr(provider.ready_state, "value", provider.ready_state))
    cert = int(getattr(provider.certification, "value", provider.certification))
    return {
        "name": getattr(provider, "name", None),
        "version": getattr(provider, "version", None),
        "ready_state": state,
        "ready_state_name": READY_STATE_NAMES.get(state, "Unknown"),
        "certification": cert,
        "library_path": _redact(getattr(provider, "library_path", "") or ""),
        "package_family_name": getattr(provider, "package_family_name", "") or "",
        "package_root_path": _redact(getattr(provider, "package_root_path", "") or ""),
    }


def raw_hresults() -> dict:
    """Direct ctypes calls so an HRESULT cannot be swallowed by the Python wrapper."""
    out: dict = {"available": False}
    try:
        from windowsml import _bindings as bindings
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    out["available"] = True
    handle = bindings.EpCatalogHandle()
    try:
        out["create_hresult"] = _hresult(int(bindings.WinMLEpCatalogCreate(ctypes.byref(handle))))
    except OSError as e:
        out["create_hresult"] = _hresult(e.winerror) if getattr(e, "winerror", None) else str(e)
        return out
    seen: list[int] = []

    @bindings.EpEnumCallback
    def _callback(_ep, _info, _ctx):
        seen.append(1)
        return True

    try:
        out["enum_hresult"] = _hresult(
            int(bindings.WinMLEpCatalogEnumProviders(handle, _callback, None)))
    except OSError as e:
        out["enum_hresult"] = _hresult(e.winerror) if getattr(e, "winerror", None) else str(e)
    finally:
        out["enumerated_count"] = len(seen)
        try:
            bindings.WinMLEpCatalogRelease(handle)
        except Exception as e:  # noqa: BLE001
            out["release_error"] = str(e)
    return out


def catalog_probe() -> dict:
    """Call the official catalog API and record raw HRESULTs where reachable."""
    out: dict = {"api_ok": False, "provider_count": 0, "providers": [],
                 "error": None, "raw_hresults": {"available": False}}
    try:
        import windowsml
    except Exception as e:  # noqa: BLE001
        out["error"] = f"import windowsml: {type(e).__name__}: {e}"
        return out
    try:
        with windowsml.EpCatalog() as catalog:
            providers = catalog.find_all_providers()
            out["providers"] = [describe_provider(p) for p in providers]
            out["provider_count"] = len(out["providers"])
        out["api_ok"] = True
    except Exception as e:  # noqa: BLE001
        code = getattr(e, "winerror", None)
        out["error"] = {"stage": "catalog_enumerate",
                        "hresult": _hresult(code) if isinstance(code, int) else None,
                        "message": f"{type(e).__name__}: {e}"}
    out["raw_hresults"] = raw_hresults()
    return out


def checks(report: dict) -> list[dict]:
    """Per-item checks: each is independently PASS / FAIL / UNKNOWN."""
    rows: list[dict] = []

    def add(name: str, status: str, detail) -> None:
        rows.append({"check": name, "status": status, "detail": detail})

    acquire = report["versions"]["windowsml_python"]
    add("official_windowsml_package", "PASS" if acquire else "FAIL",
        acquire or "pip install windowsml==2.5.96")
    build = report["host"]["windows_build"]["build"]
    add("windows_11_24h2_or_later",
        "PASS" if isinstance(build, int) and build >= 26100 else "UNKNOWN",
        f"build={build} (Windows ML 2.x EPs need 24H2 / build 26100+)")
    amd = [a for a in report["host"]["gpu"]["adapters"]
           if a["vendor_id"] == "0x1002" and not a["is_software"]]
    add("amd_gpu_adapter_present", "PASS" if amd else "FAIL",
        f"{len(amd)} AMD adapter(s)")
    raw = report["catalog"]["raw_hresults"]
    add("catalog_create_hresult",
        "PASS" if raw.get("create_hresult") == "0x00000000" else "UNKNOWN",
        raw.get("create_hresult"))
    add("catalog_enum_hresult",
        "PASS" if raw.get("enum_hresult") == "0x00000000" else "UNKNOWN",
        raw.get("enum_hresult"))
    add("catalog_provider_count",
        "PASS" if report["catalog"]["provider_count"] else "FAIL",
        report["catalog"]["provider_count"])
    names = [p["name"] for p in report["catalog"]["providers"]]
    add("amdgpu_provider_listed", "PASS" if "AMDGPUExecutionProvider" in names else "FAIL",
        names or "catalog is empty")
    return rows


def observation(report: dict) -> dict:
    """Everything except the capture timestamp: stable across idempotent re-runs."""
    return {k: v for k, v in report.items() if k not in ("captured_utc", "stable_digest")}


def build_report(gfx: str | None) -> dict:
    gpu = dxgi_adapters()
    report = {
        "schema": PROBE_SCHEMA,
        "captured_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "read_only": True,
        "gfx_requested": gfx,
        "host": {"windows_build": windows_build(), "gpu": gpu},
        "versions": {**library_versions(), **ep_package_versions()},
        "catalog": catalog_probe(),
    }
    report["checks"] = checks(report)
    report["blocked_status"] = (None if report["catalog"]["provider_count"]
                               else "BLOCKED_EXTERNAL")
    blob = json.dumps(observation(report), sort_keys=True, ensure_ascii=False)
    report["stable_digest"] = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=None, help="also write the JSON report to this path")
    ap.add_argument("--gfx", default=None, help="expected gfx target, recorded only")
    args = ap.parse_args(argv)

    report = build_report(args.gfx)
    body = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)
    print(BEGIN)
    print(body)
    print(END)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body + "\n")

    assert "radeon_ort" not in sys.modules, "this reproduction must not import the repository"
    if not report["versions"]["windowsml_python"]:
        return 4
    if not report["catalog"]["api_ok"]:
        return 5
    return 0 if report["catalog"]["provider_count"] else 3


if __name__ == "__main__":
    sys.exit(main())
