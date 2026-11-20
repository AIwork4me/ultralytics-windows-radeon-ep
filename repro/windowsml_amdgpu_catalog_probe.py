"""Experiment A1 — official Windows ML 2.x Catalog read-only probe.

Mission A_GFX1100_CATALOG_VS_DIRECT_REGISTRATION (step 4).

Runs under the ISOLATED user-level venv that carries the verified official
`windowsml==2.5.96` wheel (Microsoft-signed interop DLL; wheel sha256 and
signature are recorded by the inventory step). Read-only by construction:
this script never calls ensure_ready / ensure_ready_async / try_register /
WinMLEpEnsureReady* — an assertion guards the import surface.

Records per run:
  - windowsml package + interop DLL identity (versions, sha256)
  - WinMLEpCatalogCreate HRESULT (raw flat-C call, not the swallowing wrapper)
  - WinMLEpCatalogEnumProviders HRESULT + provider count
  - every provider: name / version / ready_state (int + API-enum name) /
    certification / package_family_name / library_path (redacted) /
    package_root_path (redacted), with per-property error capture
  - AMDGPUExecutionProvider presence (CATALOG_AMDGPU_LISTED)
  - stable digest (sha256 over the canonical, redacted provider list)

Usage (windowsml venv python):
  python -I repro/windowsml_amdgpu_catalog_probe.py --gfx gfx1100 \
      --out reports/experiment_a/CATALOG_PROBE.json --runs 3
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from radeon_ort.redaction import redact_path  # noqa: E402

MOUNT_STATES = [
    "EP_PACKAGE_INSTALLED", "EP_DLL_PRESENT", "DIRECT_EP_REGISTERED",
    "DIRECT_AMDGPU_GPU_EXECUTED", "CATALOG_AMDGPU_LISTED",
    "CATALOG_AMDGPU_READY", "CATALOG_AMDGPU_REGISTERED",
    "CATALOG_AMDGPU_GPU_EXECUTED",
]


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hr(v) -> str:
    return f"0x{v & 0xFFFFFFFF:08X}"


FORBIDDEN_CALLS = (".ensure_ready(", ".ensure_ready_async(", ".try_register(")


def assert_this_script_is_readonly() -> None:
    """Guard: this file itself must contain no catalog-mutating call sites."""
    body = Path(__file__).read_text(encoding="utf-8").split('"""', 2)[2]
    body = "".join(ln for ln in body.splitlines(keepends=True)
                   if not ln.strip().startswith('".' ) and "FORBIDDEN_CALLS" not in ln)
    for banned in FORBIDDEN_CALLS:
        assert banned not in body, f"read-only guarantee violated: {banned}"


def _dll_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_prop(fn) -> object:
    try:
        v = fn()
        return v
    except Exception as e:  # noqa: BLE001 — error IS the evidence
        return {"error": f"{type(e).__name__}: {e}"}


def probe_once(interop_sha: str) -> dict:
    import windowsml
    from windowsml import _bindings as B
    from windowsml import ExecutionProvider

    run: dict = {"sampled_utc": _utcnow(),
                 "windowsml_version": getattr(windowsml, "__version__", "unknown"),
                 "interop_dll_sha256": interop_sha}

    handle = B.EpCatalogHandle()
    hr_create = B.WinMLEpCatalogCreate(ctypes.byref(handle))
    run["winml_ep_catalog_create_hresult"] = _hr(hr_create)
    if hr_create != 0:
        run["error"] = "WinMLEpCatalogCreate failed"
        run["provider_count"] = 0
        run["providers"] = []
        return run

    raw_handles = []

    @B.EpEnumCallback
    def _cb(ep, info_ptr, ctx):
        raw_handles.append(ep)
        return True

    hr_enum = B.WinMLEpCatalogEnumProviders(handle, _cb, None)
    run["winml_ep_catalog_enum_providers_hresult"] = _hr(hr_enum)

    providers = []
    for h in raw_handles:
        ep = ExecutionProvider(h)
        rec = {
            "name": _read_prop(lambda: ep.name),
            "version": _read_prop(lambda: ep.version),
            "ready_state": _read_prop(lambda: int(ep.ready_state)),
            "ready_state_name": _read_prop(lambda: ep.ready_state.name),
            "certification": _read_prop(lambda: int(ep.certification)),
            "certification_name": _read_prop(lambda: ep.certification.name),
            "package_family_name": _read_prop(lambda: ep.package_family_name),
            "library_path_redacted": _read_prop(
                lambda: redact_path(ep.library_path) if ep.library_path else ""),
            "package_root_path_redacted": _read_prop(
                lambda: redact_path(ep.package_root_path) if ep.package_root_path else ""),
        }
        providers.append(rec)
    run["providers"] = providers
    run["provider_count"] = len(providers)
    B.WinMLEpCatalogRelease(handle)

    amd = [p for p in providers if p["name"] == "AMDGPUExecutionProvider"]
    run["amdgpu_listed"] = bool(amd)
    run["amdgpu_entry"] = amd[0] if amd else None
    if not providers:
        run["catalog_empty"] = True
    return run


def canonical_digest(run: dict) -> str:
    canon = json.dumps(
        [{"name": p["name"], "ready_state": p.get("ready_state"),
          "certification": p.get("certification"),
          "library_path_redacted": p.get("library_path_redacted")}
         for p in run.get("providers", [])],
        sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gfx", default="gfx1100")
    ap.add_argument("--out", required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--interval-s", type=float, default=1.0)
    a = ap.parse_args()
    assert_this_script_is_readonly()

    import time
    import windowsml
    interop = Path(windowsml.__file__).parent / "lib" / "Microsoft.Windows.AI.MachineLearning.dll"
    interop_sha = _dll_sha256(interop)

    runs = []
    for i in range(a.runs):
        runs.append(probe_once(interop_sha))
        if i < a.runs - 1:
            time.sleep(a.interval_s)

    digests = [canonical_digest(r) for r in runs]
    amd_listed = [bool(r.get("amdgpu_listed")) for r in runs]
    amd_ready = []
    for r in runs:
        e = r.get("amdgpu_entry")
        if e is None:
            amd_ready.append(None)
        else:
            rs = e.get("ready_state")
            amd_ready.append(rs == 0)  # EpReadyState.Ready == 0

    out = {
        "mission": "A_GFX1100_CATALOG_VS_DIRECT_REGISTRATION",
        "experiment": "A1_OFFICIAL_CATALOG_READ_ONLY_PROBE",
        "gfx": a.gfx,
        "probe": "windowsml (official PyPI package, flat-C WinML EP Catalog API)",
        "readonly_guarantee": ("no ensure_ready/ensure_ready_async/try_register "
                               "call sites in this script; catalog handles released"),
        "state_ladder_reference": MOUNT_STATES,
        "runs": runs,
        "digests_per_run": digests,
        "digest_stable_across_runs": len(set(digests)) == 1,
        "amdgpu_listed_all_runs": all(amd_listed) if amd_listed else False,
        "amdgpu_listed_any_run": any(amd_listed),
        "amdgpu_ready_state_interpretation": {
            "0": "Ready", "1": "NotReady", "2": "NotPresent",
            "source": "windowsml._bindings.EpReadyState (Microsoft-signed interop)"},
        "amdgpu_ready_all_runs": all(x is True for x in amd_ready) if amd_ready else False,
        "captured_utc": _utcnow(),
    }
    if all(r.get("provider_count") == 0 for r in runs):
        out["classification"] = "CATALOG_EMPTY"
    elif out["amdgpu_listed_all_runs"] and not out["amdgpu_ready_all_runs"]:
        out["classification"] = "CATALOG_AMDGPU_LISTED_NOT_READY"
    elif out["amdgpu_listed_all_runs"] and out["amdgpu_ready_all_runs"]:
        out["classification"] = "CATALOG_AMDGPU_LISTED_READY"
    else:
        out["classification"] = "CATALOG_AMDGPU_NOT_LISTED"

    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: out[k] for k in (
        "classification", "digest_stable_across_runs", "amdgpu_listed_all_runs",
        "amdgpu_ready_all_runs")}, indent=1))
    print(f"[A1] wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
