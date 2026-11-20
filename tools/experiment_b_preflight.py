"""Experiment B: read-only preflight snapshot collector (GROK/B_GFX1151_AMDGPU_EP_ROOT_CAUSE).

Read-only. Writes a single machine-readable snapshot; it never installs, registers
or starts a GPU session (the quick DML canary is a separate child process invoked
by the caller, not by this module).

Usage:
    python tools/experiment_b_preflight.py --out reports/experiment_b/PRE_EXPERIMENT_SNAPSHOT.json
        --run-id B_GFX1151_AMDGPU_<UTC> --gfx gfx1151
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def run(cmd: list[str], timeout: int = 180) -> dict:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           cwd=str(REPO), encoding="utf-8", errors="replace", check=False)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "stdout": ""}
    return {"ok": p.returncode == 0, "rc": p.returncode,
            "stdout": p.stdout or "", "stderr": (p.stderr or "")[:2000]}


PS_PREAMBLE = ("$ProgressPreference='SilentlyContinue';"
               "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
               "$OutputEncoding=[System.Text.Encoding]::UTF8;")


def ps(script: str, timeout: int = 180) -> dict:
    """Run a probe script; prefer the engine that actually returns data.

    Windows PowerShell 5.1 denies a bare Get-AppxPackage to a non-admin user on
    this host while pwsh 7 enumerates the current user's packages, so the engine
    is recorded instead of assumed.
    """
    last = None
    for exe in ("pwsh", "powershell"):
        r = run([exe, "-NoProfile", "-NonInteractive", "-Command", PS_PREAMBLE + script], timeout)
        r["engine"] = exe
        if (r.get("stdout") or "").strip():
            return r
        last = r
    return last or {"ok": False, "error": "no powershell", "stdout": "", "engine": None}


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_state() -> dict:
    def g(*args: str) -> str:
        r = run(["git", *args], timeout=120)
        return (r.get("stdout") or "").strip()
    head = g("rev-parse", "HEAD")
    porcelain = g("status", "--porcelain=v1", "--untracked-files=all")
    harness = g("status", "--porcelain=v1", "--untracked-files=all", "--",
                "runner", "src/radeon_ort", "configs", "schemas", "pyproject.toml")
    return {
        "head_sha": head,
        "branch": g("rev-parse", "--abbrev-ref", "HEAD"),
        "describe": g("describe", "--always", "--dirty"),
        "remote": g("remote", "get-url", "origin"),
        "log_head5": g("log", "--oneline", "-5").splitlines(),
        "worktree_clean": porcelain == "",
        "dirty_paths": [ln for ln in porcelain.splitlines() if ln][:50],
        "harness_scope_clean": harness == "",
        "harness_dirty_paths": [ln for ln in harness.splitlines() if ln][:50],
        "tracked_files": int(g("ls-files").count("\n") + 1 if g("ls-files") else 0),
    }


def os_info() -> dict:
    info = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "python_executable_basename": Path(sys.executable).name,
    }
    script = r"""
$os = Get-CimInstance Win32_OperatingSystem
$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
[pscustomobject]@{
  caption = $os.Caption
  version = $os.Version
  build = $os.BuildNumber
  ubr = $cv.UBR
  display_version = $cv.DisplayVersion
  product_name = $cv.ProductName
  edition = $cv.EditionID
  install_date = ($os.InstallDate -as [string])
  last_boot = ($os.LastBootUpTime -as [string])
  architecture = $os.OSArchitecture
  locale = (Get-Culture).Name
  ps_version = $PSVersionTable.PSVersion.ToString()
} | ConvertTo-Json -Compress
"""
    r = ps(script)
    out = {"windows": None, "probe": {"ok": bool(r.get("ok"))}}
    for ln in (r.get("stdout") or "").splitlines():
        if ln.strip().startswith("{"):
            try:
                out["windows"] = json.loads(ln)
            except json.JSONDecodeError:
                pass
    return {**info, **out}


def gpu_block(gfx: str) -> dict:
    from radeon_ort.device_identity import enum_dxgi_adapters, resolve_target_adapter, target_device_ids
    from radeon_ort.host_probe import collect_host

    dids = target_device_ids(gfx)
    host = collect_host(gfx=gfx, gpu_device_ids=dids)
    host.pop("_gpu_list", None)
    adapters = enum_dxgi_adapters()
    res = resolve_target_adapter(adapters, gfx=gfx)
    dxgi = []
    for a in adapters:
        dxgi.append({
            "index": a.index, "description": a.description,
            "vendor_id": a.vendor_id,
            "vendor_id_hex": f"0x{a.vendor_id:04X}",
            "device_id": a.device_id,
            "device_id_hex": f"0x{a.device_id:04X}",
            "sub_sys_id": a.sub_sys_id, "revision": a.revision,
            "luid_high": a.luid_high, "luid_low": a.luid_low,
            "luid_str": a.luid_str, "is_software": a.is_software,
            "dedicated_video_mb": a.dedicated_video_mb,
            "shared_sys_mb": a.shared_sys_mb,
        })
    pick = res.get("adapter") if isinstance(res, dict) else None
    return {
        "gfx_requested": gfx,
        "expected_device_ids": [f"0x{d:04X}" for d in dids],
        "host_fingerprint": host,
        "dxgi_adapters": dxgi,
        "resolve": {
            "ok": bool(res.get("ok")),
            "did_match": bool(res.get("did_match")),
            "luid_valid": bool(res.get("luid_valid")),
            "reason": res.get("reason"),
            "selected_index": getattr(pick, "index", None),
            "selected_description": getattr(pick, "description", None),
            "selected_luid": getattr(pick, "luid_str", None),
            "selected_device_id_hex": (f"0x{pick.device_id:04X}" if pick is not None else None),
        },
    }


def driver_block() -> dict:
    script = r"""
$vc = Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,DriverDate,DriverProviderName,InfFilename,Status,PNPDeviceID,AdapterRAM
$pnp = Get-CimInstance Win32_PnPSignedDriver | Where-Object { $_.DeviceClass -eq 'DISPLAY' } | Select-Object DeviceName,DriverVersion,DriverDate,DriverProviderName,InfName,IsSigned,Signer
$rows = @()
foreach ($v in $vc) { $rows += [pscustomobject]@{ kind='video_controller'; data=$v } }
foreach ($p in $pnp) { $rows += [pscustomobject]@{ kind='signed_driver'; data=$p } }
$rows | ConvertTo-Json -Compress -Depth 4
"""
    r = ps(script)
    recs = []
    for ln in (r.get("stdout") or "").splitlines():
        s = ln.strip()
        if s.startswith(("[", "{")):
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                continue
            recs.extend(obj if isinstance(obj, list) else [obj])
    return {"records": recs, "probe_ok": bool(r.get("ok"))}


def _reg_enum(root: int, path: str) -> list[str]:
    import winreg
    try:
        k = winreg.OpenKey(root, path)
    except OSError:
        return []
    out, i = [], 0
    while True:
        try:
            out.append(winreg.EnumKey(k, i))
        except OSError:
            break
        i += 1
    return out


def _parse_pkgfullname(pfn: str) -> dict:
    parts = pfn.split("_")
    return {
        "name": parts[0] if parts else pfn,
        "version": parts[1] if len(parts) > 1 else None,
        "arch": parts[2] if len(parts) > 2 else None,
        "publisher_id": parts[3] if len(parts) > 3 else None,
        "package_full_name": pfn,
    }


APPX_RE = re.compile(r"AMD|WinML|WindowsAppRuntime|MachineLearning|VitisAI|Store",
                     re.IGNORECASE)
AMDGPU_EP_RE = re.compile(r"WinML\.AMD\.GPU\.EP", re.IGNORECASE)


def appx_registry_block(raw_appx_json: str = "") -> dict:
    """Package inventory from the AppX registry stores (readable without elevation).

    A bare Get-AppxPackage enumeration is refused with E_ACCESSDENIED when the
    PowerShell child is spawned from Python with redirected stdio on this build,
    so the registry stores are used as the authority; the cmdlet inventory is
    ingested separately when the agent shell captured it.
    """
    import winreg
    repo_cur = _reg_enum(
        winreg.HKEY_CURRENT_USER,
        "Software\\Classes\\Local Settings\\Software\\Microsoft\\Windows"
        "\\CurrentVersion\\AppModel\\Repository\\Packages")
    allusers = _reg_enum(
        winreg.HKEY_LOCAL_MACHINE,
        "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Appx"
        "\\AppxAllUserStore\\Applications")
    inbox = _reg_enum(
        winreg.HKEY_LOCAL_MACHINE,
        "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Appx"
        "\\AppxAllUserStore\\InboxApplications")
    cur = [{**_parse_pkgfullname(p), "source": "HKCU_AppModel_Repository"}
           for p in repo_cur if APPX_RE.search(p)]
    mach = [{**_parse_pkgfullname(p), "source": "HKLM_AppxAllUserStore_Applications"}
            for p in allusers if APPX_RE.search(p)]
    out = {
        "method": "registry-stores",
        "current_user_repository_total": len(repo_cur),
        "machine_allusers_total": len(allusers),
        "inbox_total": len(inbox),
        "current_user": sorted(cur, key=lambda x: x["name"]),
        "machine_allusers": sorted(mach, key=lambda x: x["name"]),
        "amdgpu_ep_packages_current_user": sorted(
            p["package_full_name"] for p in cur
            if AMDGPU_EP_RE.search(p["package_full_name"])),
        "amdgpu_ep_packages_machine": sorted(
            p["package_full_name"] for p in mach
            if AMDGPU_EP_RE.search(p["package_full_name"])),
        "cmdlet_cross_check": None,
        "cmdlet_note": ("Get-AppxPackage is unusable from a python-spawned child on "
                        "this host; the cmdlet inventory is ingested from "
                        "reports/experiment_b/_raw/appx_cmdlet.json when present"),
    }
    if raw_appx_json:
        p = Path(raw_appx_json)
        if p.is_file():
            try:
                out["cmdlet_cross_check"] = json.loads(
                    p.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError as exc:
                out["cmdlet_cross_check"] = {"error": str(exc)}
    return out


def ep_dll_block() -> dict:
    from radeon_ort.windows_ml_ep_catalog import scan_amdgpu_ep_dll
    out = scan_amdgpu_ep_dll()
    # an unprivileged user cannot list C:\Program Files\WindowsApps; record that
    wa = Path(r"C:\Program Files\WindowsApps")
    try:
        listable = wa.is_dir() and any(True for _ in wa.iterdir())
        err = None
    except OSError as exc:
        listable, err = False, f"{type(exc).__name__}: {exc.strerror or exc}"
    return {"scan": out, "windowsapps_listable": bool(listable),
            "windowsapps_error": err,
            "windowsapps_path_note": "C:/Program Files/WindowsApps (system path, not user data)"}


def runtime_block() -> dict:
    import importlib.metadata as md
    want = ["onnxruntime-windowsml", "onnxruntime", "windowsml", "numpy", "onnx",
            "wasdk-Microsoft.Windows.AI.MachineLearning",
            "wasdk-Microsoft.Windows.ApplicationModel.DynamicDependency.Bootstrap",
            "wasdk-Microsoft.Windows.Applications", "pytest", "ruff", "ultralytics"]
    pkgs = {}
    for name in want:
        try:
            pkgs[name] = md.version(name)
        except md.PackageNotFoundError:
            pkgs[name] = None
    out = {"packages": pkgs, "ort": None}
    try:
        import onnxruntime as ort
        out["ort"] = {
            "version": ort.__version__,
            "available_providers": list(ort.get_available_providers()),
            "ep_devices": [],
            "has_register_api": hasattr(ort, "register_execution_provider_library"),
            "module_file_basename": Path(ort.__file__).name,
        }
        try:
            for d in ort.get_ep_devices():
                out["ort"]["ep_devices"].append({
                    "ep_name": d.ep_name, "ep_vendor": d.ep_vendor,
                    "ep_options": dict(d.ep_options or {}),
                    "ep_metadata": dict(d.ep_metadata or {}),
                })
        except Exception as exc:  # noqa: BLE001
            out["ort"]["ep_devices_error"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001
        out["ort_error"] = f"{type(exc).__name__}: {exc}"
    return out


def catalog_quick() -> dict:
    from radeon_ort.windows_ml_ep_catalog import probe_windows_ml_api
    caps = probe_windows_ml_api()
    caps.pop("_bootstrap_ctx", None)
    return caps


def store_block() -> dict:
    script = r"""
$out = [ordered]@{}
$out.store_pkg = (Get-AppxPackage -Name Microsoft.WindowsStore | Select-Object Name,Version | ConvertTo-Json -Compress)
$keys = @()
foreach ($k in 'HKLM:\SOFTWARE\Policies\Microsoft\WindowsStore','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsStore\WindowsUpdate') {
  if (Test-Path $k) { $keys += [pscustomobject]@{ path=$k; values=(Get-ItemProperty $k | Select-Object * -ExcludeProperty PS* | ConvertTo-Json -Compress) } }
}
$out.policies = $keys
$reboot = @()
foreach ($k in 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\PackagesPending') {
  if (Test-Path $k) { $reboot += $k }
}
$out.reboot_pending_keys = $reboot
$out.proxy = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue | Select-Object ProxyEnable,ProxyServer,AutoConfigURL | ConvertTo-Json -Compress)
$out.wu_service = (Get-Service wuauserv,DoSvc,AppXSvc,ClipSVC,InstallService -ErrorAction SilentlyContinue | Select-Object Name,Status,StartType | ConvertTo-Json -Compress)
$out | ConvertTo-Json -Compress -Depth 5
"""
    r = ps(script)
    txt = (r.get("stdout") or "").strip()
    try:
        data = json.loads(txt) if txt.startswith("{") else {"raw": txt[:2000]}
    except json.JSONDecodeError:
        data = {"raw": txt[:2000]}
    return {"probe_ok": bool(r.get("ok")), "data": data}


def r10_evidence(graph_root: Path) -> dict:
    d = REPO / "reports" / "mission_r10"
    files = []
    if d.is_dir():
        for p in sorted(d.rglob("*")):
            if p.is_file():
                files.append({"path": p.relative_to(REPO).as_posix(),
                              "bytes": p.stat().st_size, "sha256": sha256_file(p)})
    return {"dir": "reports/mission_r10", "file_count": len(files), "files": files}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--gfx", default="gfx1151")
    ap.add_argument("--appx-cmdlet-json", default="",
                    help="optional Get-AppxPackage JSON captured by the agent shell")
    ap.add_argument("--dml-canary-json", default="",
                    help="path to a DML canary JSON produced by a child process")
    a = ap.parse_args()

    snap: dict = {
        "schema": "experiment_b_preflight_v1",
        "mission": "B_GFX1151_AMDGPU_EP_ROOT_CAUSE",
        "run_id": a.run_id,
        "captured_utc": utc_now(),
        "captured_local": local_now(),
        "host_os_timezone": datetime.now().astimezone().tzname(),
        "gfx": a.gfx,
        "read_only": True,
    }
    blocks = {
        "git": git_state,
        "os": os_info,
        "gpu": lambda: gpu_block(a.gfx),
        "driver": driver_block,
        "appx": lambda: appx_registry_block(a.appx_cmdlet_json),
        "ep_dll": ep_dll_block,
        "runtime": runtime_block,
        "catalog_preflight": catalog_quick,
        "store": store_block,
        "r10_evidence": lambda: r10_evidence(REPO),
    }
    for name, fn in blocks.items():
        try:
            snap[name] = fn()
        except Exception as exc:  # noqa: BLE001 — a failed block is evidence, not a crash
            snap[name] = {"error": f"{type(exc).__name__}: {exc}"}

    if a.dml_canary_json:
        p = Path(a.dml_canary_json)
        if p.is_file():
            try:
                snap["dml_canary_quick"] = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                snap["dml_canary_quick"] = {"error": str(exc)}
        else:
            snap["dml_canary_quick"] = {"error": "missing", "path": a.dml_canary_json}
    else:
        snap["dml_canary_quick"] = {"status": "NOT_RUN"}

    from radeon_ort.redaction import redact_text, scan_for_leaks
    body = redact_text(json.dumps(snap, indent=1, sort_keys=False))
    leaks = scan_for_leaks(body)
    snap["redaction_scan"] = {"ok": not leaks, "leaks": leaks}
    body = redact_text(json.dumps(snap, indent=1, sort_keys=False))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body + "\n", encoding="utf-8")
    print(json.dumps({"out": out.as_posix(), "bytes": len(body),
                      "leaks": leaks, "blocks": sorted(snap)}, indent=1))
    return 0 if not leaks else 3


if __name__ == "__main__":
    raise SystemExit(main())
