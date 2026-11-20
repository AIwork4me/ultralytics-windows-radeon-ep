"""Experiment A2 — installed MSIX + DLL provenance inventory (strictly read-only).

Mission A_GFX1100_CATALOG_VS_DIRECT_REGISTRATION (step 5).

Read-only interfaces only: Get-AppxPackage queries, manifest read,
Get-AuthenticodeSignature, file hashes, PE import-table parse (pure python),
ORT/EP version introspection. No WindowsApps writes, no installs, no
ensure_ready. Every path in the OUTPUT is redacted; raw paths stay on host.

Usage (any python with stdlib only):
  python -I repro/experiment_a_inventory.py --gfx gfx1100 \
      --out reports/experiment_a/EP_PACKAGE_INVENTORY.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from radeon_ort.redaction import redact_path  # noqa: E402

EP_PKG_NAME = "Microsoft.WinML.AMD.GPU.EP.Framework.2"


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ps(cmd: str, timeout: int = 60) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return (r.stdout or "").strip()


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pe_imports(path: Path) -> list[str]:
    """Minimal PE import-table reader (dll names only). Pure stdlib, read-only."""
    data = path.read_bytes()
    if data[:2] != b"MZ":
        return []
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        return []
    opt_off = e_lfanew + 24
    magic = struct.unpack_from("<H", data, opt_off)[0]
    if magic == 0x20B:      # PE32+
        dd_off = opt_off + 112
    elif magic == 0x10B:    # PE32
        dd_off = opt_off + 96
    else:
        return []
    import_rva, import_sz = struct.unpack_from("<II", data, dd_off + 8)
    if not import_rva:
        return []
    # section table to translate RVA->file offset
    nsec = struct.unpack_from("<H", data, e_lfanew + 6)[0]
    sec_off = opt_off + struct.unpack_from("<H", data, e_lfanew + 20)[0]
    secs = []
    for i in range(nsec):
        s = sec_off + 40 * i
        vsize, vaddr, rsize, roff = struct.unpack_from("<IIII", data, s + 8)
        secs.append((vaddr, max(vsize, rsize), roff))

    def rva2off(rva: int) -> int | None:
        for vaddr, size, roff in secs:
            if vaddr <= rva < vaddr + size:
                return roff + (rva - vaddr)
        return None

    names: list[str] = []
    off = rva2off(import_rva)
    if off is None:
        return []
    # IMAGE_IMPORT_DESCRIPTOR: OriginalFirstThunk(0) TimeDateStamp(4)
    # ForwarderChain(8) Name(12) FirstThunk(16), 20 bytes each
    while True:
        oft_rva, _ts, _fc, name_rva, _ft = struct.unpack_from("<IIIII", data, off)
        if oft_rva == 0 and name_rva == 0:
            break
        n_off = rva2off(name_rva)
        if n_off:
            end = data.index(b"\0", n_off)
            names.append(data[n_off:end].decode("ascii", "replace"))
        off += 20
    return names


def appx_inventory() -> dict:
    out: dict = {"query": EP_PKG_NAME}
    fields = ("Name", "Version", "PackageFamilyName", "Publisher", "PublisherId",
              "SignatureKind", "IsFramework", "IsResourcePackage", "NonRemovable")
    packages = []
    # NB (live-observed 2026-11-20): -Name infix wildcards ('*WinML*AMD*')
    # silently return nothing, and FullName is empty in -NoProfile sessions;
    # enumerate by Name then query fields per exact name.
    names = ps("Get-AppxPackage -Name '*WinML*' | ForEach-Object { $_.Name }").splitlines()
    amd_names = [l.strip() for l in names if l.strip() and "AMD" in l.upper()]
    for pkg_name in amd_names:
        rec = {"queried_name": pkg_name}
        for f in fields:
            rec[f] = ps(f"(Get-AppxPackage -Name '{pkg_name}').{f}")
        packages.append(rec)
    if not packages:
        v = ps(f"(Get-AppxPackage {EP_PKG_NAME}).Version")
        packages.append({"Name": EP_PKG_NAME, "Version": v or "NOT_FOUND"})
    out["packages_matching_winml_amd"] = packages
    out["package_count"] = len(packages)
    out["all_versions_installed"] = sorted({p.get("Version", "") for p in packages})
    deps = ps(f"(Get-AppxPackage {EP_PKG_NAME}).Dependencies | ForEach-Object {{ $_.Name + ' ' + $_.Version }}")
    out["dependencies"] = [l for l in deps.splitlines() if l.strip()]
    out["windows_app_runtime_versions"] = [
        l for l in ps("Get-AppxPackage | Where-Object { $_.Name -match 'WindowsAppRuntime' } "
                      "| ForEach-Object { $_.Name + ' ' + $_.Version }").splitlines() if l.strip()]
    return out


def file_inventory(install_loc: str) -> dict:
    rec: dict = {"install_location_redacted": redact_path(install_loc)}
    ep_dir = Path(install_loc) / "ExecutionProvider"
    if not ep_dir.is_dir():
        rec["error"] = "ExecutionProvider dir not listable (ACL or missing)"
        return rec
    files = []
    for p in sorted(ep_dir.iterdir()):
        if not p.is_file():
            continue
        entry: dict = {"name": p.name, "bytes": p.stat().st_size,
                       "sha256": _sha256(p)}
        sig = ps(f"(Get-AuthenticodeSignature '{p}').Status.ToString()")
        signer = ps(f"(Get-AuthenticodeSignature '{p}').SignerCertificate.Subject")
        entry["authenticode_status"] = sig or "UNKNOWN"
        entry["signer"] = signer or ""
        vi = ps(f"(Get-Item '{p}').VersionInfo | Out-String").replace("\r\n", " | ")
        entry["version_info"] = re.sub(r"\s+", " ", vi).strip()[:300]
        files.append(entry)
    rec["files"] = files
    rec["file_count"] = len(files)
    # arch/kernel targeting evidence
    kernels = [f["name"] for f in files if re.search(r"gfx\d+|rdna|navi", f["name"], re.I)]
    rec["arch_targeting_files"] = kernels
    # migraphx colocated?
    rec["migraphx_colocated"] = any(f["name"].lower().startswith("migraphx") for f in files)
    # manifest
    mani = Path(install_loc) / "AppxManifest.xml"
    if mani.is_file():
        try:
            rec["manifest_excerpt"] = {
                "identity": re.search(
                    r"<Identity[^>]*/>", mani.read_text(encoding="utf-8", errors="replace")
                ).group(0) if re.search(r"<Identity[^>]*/>", mani.read_text(
                    encoding="utf-8", errors="replace")) else "NOT_PARSED"}
        except OSError:
            rec["manifest_excerpt"] = "READ_FAIL"
    return rec


def dll_registration_entry(dll: Path) -> dict:
    """ORT-facing facts about amdgpu-ep.dll + its PE imports."""
    rec: dict = {"dll_name": dll.name, "sha256": _sha256(dll), "bytes": dll.stat().st_size}
    try:
        rec["pe_imports"] = sorted(set(pe_imports(dll)))
    except Exception as e:  # noqa: BLE001
        rec["pe_imports_error"] = f"{type(e).__name__}: {e}"
    # exported entry points (GetProcAddress names) — parse export table
    return rec


def ort_compat() -> dict:
    code = (
        "import onnxruntime as ort, json;"
        "print(json.dumps({'ort_version': ort.__version__,"
        " 'has_register_execution_provider_library': hasattr(ort,'register_execution_provider_library'),"
        " 'has_get_ep_devices': hasattr(ort,'get_ep_devices'),"
        " 'available_providers': ort.get_available_providers()}))"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        return {"error": r.stderr[-400:] or "no output"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gfx", default="gfx1100")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    inv: dict = {
        "mission": "A_GFX1100_CATALOG_VS_DIRECT_REGISTRATION",
        "experiment": "A2_INSTALLED_MSIX_AND_DLL_PROVENANCE",
        "gfx": a.gfx,
        "captured_utc": _utcnow(),
        "readonly": "Get-AppxPackage / signature / hash / PE parse only",
        "install_channel": "UNKNOWN unless proven below (no guessing)",
    }
    inv["appx"] = appx_inventory()
    loc = ps(f"(Get-AppxPackage {EP_PKG_NAME}).InstallLocation")
    if loc:
        inv["ep_package_files"] = file_inventory(loc)
        dll = Path(loc) / "ExecutionProvider" / "amdgpu-ep.dll"
        if dll.is_file():
            inv["amdgpu_ep_dll"] = dll_registration_entry(dll)
    else:
        inv["ep_package_files"] = {"error": "InstallLocation empty (package absent?)"}
    inv["ort_compat"] = ort_compat()

    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(inv, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[A2] wrote {p} ({inv['appx'].get('package_count')} packages, "
          f"{inv.get('ep_package_files', {}).get('file_count', 0)} EP files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
