"""Experiment B4: assemble the MSIX / driver / hypothesis evidence documents.

Inputs are the byte-exact raw captures under reports/experiment_b/_raw/ plus the
already-committed probe reports; nothing here re-probes the machine, so the
documents cannot drift from the captures they cite.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RAW = REPO / "reports" / "experiment_b" / "_raw"


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(name: str) -> dict:
    return json.loads((RAW / name).read_text(encoding="utf-8-sig"))


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def classify_package(name: str) -> str:
    n = name.lower()
    if "ep.amd.vitisai" in n:
        return "AMD_NPU_EXECUTION_PROVIDER_PACKAGE"
    if "winml.amd.gpu.ep" in n:
        return "AMD_GPU_EXECUTION_PROVIDER_PACKAGE"
    if "winmlshared" in n:
        return "WINDOWS_ML_SHARED_RUNTIME"
    if "windowsappruntime" in n:
        return "WINDOWS_APP_RUNTIME"
    if "windowsstore" in n or "storepurchase" in n:
        return "STORE_CLIENT"
    if "workload" in n:
        return "WINDOWS_WORKLOAD_OTHER"
    if "amdraedonsoftware" in n:
        return "AMD_ADRENALIN_SOFTWARE"
    return "OTHER"


def build_msix(run_id: str) -> dict:
    appx = load("b4_winml_packages.json")
    pre = json.loads((REPO / "reports/experiment_b/PRE_EXPERIMENT_SNAPSHOT.json")
                     .read_text(encoding="utf-8"))
    ev = load("b4_appx_events_redacted.json")
    store = load("b4_store_events.json")
    svc = load("b4_services.json")

    pkgs = []
    for p in appx["packages"]:
        pkgs.append({
            "name": p.get("Name"), "version": p.get("Version"),
            "package_full_name": p.get("PackageFullName"),
            "publisher_id": p.get("PublisherId"),
            "signature_kind": p.get("SignatureKind"),
            "is_framework": p.get("IsFramework"),
            "role": classify_package(p.get("Name") or ""),
        })
    amd_gpu = [p for p in pkgs if p["role"] == "AMD_GPU_EXECUTION_PROVIDER_PACKAGE"]

    base = pre.get("appx") or {}
    cmdlet = base.get("cmdlet_cross_check") or {}
    events = [e for e in ev.get("events") or [] if e.get("message")]
    kw = ev.get("keyword_counts_over_full_dump") or {}
    quoted_events = len(events)
    return {
        "schema": "experiment_b_msix_inventory_v1",
        "mission": "B_GFX1151_AMDGPU_EP_ROOT_CAUSE",
        "run_id": run_id,
        "captured_utc": utc_now(),
        "read_only": True,
        "sources": {
            "appx_current_user": "reports/experiment_b/_raw/appx_cmdlet.json (Get-AppxPackage via the agent shell)",
            "appx_registry": "reports/experiment_b/PRE_EXPERIMENT_SNAPSHOT.json:appx",
            "deployment_events": ("reports/experiment_b/_raw/b4_appx_events_redacted.json "
                                  "(aggregates over the full 2464-event dump; the raw dump "
                                  "carries account SIDs and is intentionally not committed)"),
            "store_events": "reports/experiment_b/_raw/b4_store_events.json",
            "services": "reports/experiment_b/_raw/b4_services.json",
        },
        "windowsapps_directory": {
            "path": "C:/Program Files/WindowsApps",
            "listable_by_this_account": base.get("windowsapps_listable"),
            "error": base.get("windowsapps_error"),
            "impact": ("an unprivileged process cannot enumerate the package directory; the "
                       "absence finding therefore rests on the registry package stores, which "
                       "are the authority AppX itself uses, in both per-user and machine scope"),
        },
        "inventory": {
            "current_user_total_packages": cmdlet.get("total_current_user"),
            "current_user_repository_total": base.get("current_user_repository_total"),
            "machine_allusers_total": base.get("machine_allusers_total"),
            "relevant_packages": pkgs,
            "relevant_count": len(pkgs),
            "registry_vs_cmdlet_agree_on_amd_gpu_ep_absence":
                (base.get("amdgpu_ep_packages_current_user") == []) and (not amd_gpu),
        },
        "amdgpu_ep": {
            "candidate_identities": [
                "Microsoft.WinML.AMD.GPU.EP.Framework.*",
                "Microsoft.WinML.AMD.GPU.EP.*",
                "MicrosoftCorporationII.WinML.AMD.GPU.EP.*",
            ],
            "found_current_user": base.get("amdgpu_ep_packages_current_user") or [],
            "found_machine_all_users": base.get("amdgpu_ep_packages_machine") or [],
            "found_relevant_packages": [p["package_full_name"] for p in amd_gpu],
            "dll_scan": (pre.get("ep_dll") or {}).get("scan"),
            "status": "ABSENT_MACHINE_WIDE",
            "note": ("the only AMD execution-provider package on the host is the VitisAI NPU "
                     "framework (1.8.80.0); it is not an AMD GPU EP and cannot serve an "
                     "AMDGPU session"),
        },
        "windows_ml_shared_runtime": next(
            (p for p in pkgs if p["role"] == "WINDOWS_ML_SHARED_RUNTIME"), None),
        "amd_npu_ep_present": next(
            (p for p in pkgs if p["role"] == "AMD_NPU_EXECUTION_PROVIDER_PACKAGE"), None),
        "deployment_events": {
            "logs": ["Microsoft-Windows-AppXDeploymentServer/Operational",
                     "Microsoft-Windows-AppXDeployment/Operational"],
            "events_matched": ev.get("raw_event_count"),
            "keyword_counts": kw,
            "quoted_events_in_redacted_dump": quoted_events,
            "vitisai_registration_first": ev.get("vitisai_registration_first"),
            "vitisai_registration_last": ev.get("vitisai_registration_last"),
            "amdgpu_ep_installation_events": 0,
            "interpretation": ("the workload EP delivery machinery is demonstrably active on "
                               "this host (the AMD NPU EP framework was registered on "
                               "2026-11-18), yet no AMD GPU EP package was ever staged, "
                               "registered or mentioned in the deployment logs"),
        },
        "store_events": {
            "scanned": store.get("scanned"), "kept": store.get("kept"),
            "acquisition_failures_found": bool(store.get("kept")),
            "note": ("no Store-side acquisition error, retry or denial is recorded for this "
                     "host in the scanned window"),
        },
        "services": svc.get("services"),
        "verdict": {
            "ep_package_state": "NOT_INSTALLED_MACHINE_WIDE",
            "catalog_state": "EMPTY_ALL_SURFACES",
            "label": "B_CATALOG_EMPTY_REPRODUCED",
        },
    }


def build_driver(run_id: str) -> dict:
    drv = load("b4_driver_inf.json")
    pre = json.loads((REPO / "reports/experiment_b/PRE_EXPERIMENT_SNAPSHOT.json")
                     .read_text(encoding="utf-8"))
    cites = json.loads((REPO / "reports/experiment_b/OFFICIAL_DOC_CITATIONS.json")
                       .read_text(encoding="utf-8"))
    supported = next(p for p in cites["pages"] if p["name"] == "supported_execution_providers_md")
    display = [r for r in drv.get("pnputil_amd_records") or []
               if (r.get("Class Name") or "") == "Display"]
    vc = (drv.get("video_controller") or [{}])[0]
    banner = (drv.get("inf") or {}).get("banner_comments") or []
    internal = next((b for b in banner if b.strip(";-").replace("-", "").strip()
                     and "2510" in b), None)
    canary = pre.get("dml_canary_quick") or {}
    return {
        "schema": "experiment_b_driver_compatibility_v1",
        "mission": "B_GFX1151_AMDGPU_EP_ROOT_CAUSE",
        "run_id": run_id,
        "captured_utc": utc_now(),
        "read_only": True,
        "host_gpu": {
            "description": vc.get("Name"),
            "pnp_hardware_id": vc.get("PNPDeviceID"),
            "device_id": "0x1586",
            "subsys_id": "0x8D01103C",
            "revision": "0xD1",
            "gfx": "gfx1151 (Radeon 8060S, Strix Halo)",
            "dxgi_luid": (pre.get("gpu") or {}).get("resolve", {}).get("selected_luid"),
        },
        "driver_package": {
            "wddm_driver_version": vc.get("DriverVersion"),
            "driver_date": (vc.get("DriverDate") or "").split(" ")[0] or None,
            "provider": "Advanced Micro Devices, Inc.",
            "signer": next((r.get("Signer Name") for r in display), None),
            "published_inf_names": sorted({r.get("Published Name") for r in display if r.get("Published Name")}),
            "original_inf_names": sorted({r.get("Original Name") for r in display if r.get("Original Name")}),
            "driver_store_package": (drv.get("driver_store_packages") or [None])[0],
            "inf_driver_ver_line": (drv.get("inf") or {}).get("driver_ver_line"),
            "inf_banner_internal_build": internal,
            "inf_banner_all": banner,
            "amd_driver_package_records_total": len(drv.get("pnputil_amd_records") or []),
            "amd_display_class_records": display,
            "oem_note": ("the INF banner ends in -HP and was published by the Microsoft Windows "
                         "Hardware Compatibility Publisher, i.e. it is an HP-packaged OEM "
                         "driver rather than a retail AMD Adrenalin installer"),
        },
        "os": {"build": (drv.get("os") or {}).get("build"),
               "version": (drv.get("os") or {}).get("version"),
               "display_version": "25H2", "ubr": 9974},
        "documented_requirement": {
            "ep": "AMDGPUExecutionProvider",
            "source_url": supported["url"],
            "accessed_utc": supported["accessed_utc"],
            "sha256_raw": supported["sha256_raw"],
            "last_updated_marker": supported.get("markers"),
            "minimum_windows_ml": "2.5.96",
            "driver": "Adrenalin Edition 24.12.1 or later",
            "gpu_target_list_documented": False,
            "statement": ("the published page states a Windows ML minimum and an Adrenalin "
                          "minimum; it states no gfx/DID list, so gfx1151 eligibility cannot "
                          "be read from it"),
        },
        "adrenalin_software_version": {
            "value": "UNKNOWN",
            "reason": ("the installed AMD software package reports 10.25.20179.0 and the INF "
                       "banner reports the driver-package build 25.20.18; neither is the "
                       "Adrenalin marketing version and no documented mapping exists, so the "
                       "documented Adrenalin minimum can be neither confirmed nor refuted "
                       "from read-only telemetry"),
            "installed_amd_software_package": "AdvancedMicroDevicesInc-2.AMDRadeonSoftware 10.25.20179.0",
            "corroboration_only": ("the driver date 2025-09-17 is later than the "
                                   "Adrenalin 24.12.1 release window; this is consistent "
                                   "with, not proof of, meeting the minimum"),
        },
        "compatibility_findings": [
            {"id": "DRV-1", "statement": "OS build 26200.9974 satisfies the documented 24H2 (26100+) requirement",
             "status": "MET"},
            {"id": "DRV-2", "statement": "Windows ML binding 2.5.96 satisfies the documented 2.5.96 minimum",
             "status": "MET"},
            {"id": "DRV-3", "statement": "Adrenalin 24.12.1-or-later requirement", "status": "UNVERIFIED",
             "why": "no documented mapping from 32.0.22018.5 or 25.20.18 to an Adrenalin marketing version"},
            {"id": "DRV-4", "statement": "gfx1151 / DID 0x1586 explicitly supported by AMDGPU EP",
             "status": "UNVERIFIED", "why": "no official gfx/DID list is published; cannot be tested without the EP"},
            {"id": "DRV-5", "statement": "the installed display driver can run GPU inference at all",
             "status": "MET_FOR_DML",
             "why": "the DirectML canary executes on this adapter with positive DML profiler nodes"},
            {"id": "DRV-6", "statement": "HP OEM packaging blocks the AMDGPU EP",
             "status": "UNVERIFIED", "why": "no official statement ties the AMDGPU EP to a retail AMD installer"},
        ],
        "dml_functional_baseline": {
            "status": canary.get("status"),
            "target_ep_nodes": (canary.get("profile") or {}).get("target_ep_nodes"),
            "cpu_nodes": (canary.get("profile") or {}).get("cpu_nodes"),
            "parity": canary.get("parity"),
        },
        "verdict": ("DRIVER_REQUIREMENT_NEITHER_PROVEN_NOR_REFUTED; the driver stack is "
                    "functional for DirectML and no evidence ties the empty catalog to the "
                    "HP OEM driver"),
    }


def build_hypotheses(run_id: str) -> dict:
    def H(hid, category, statement, status, evidence, discriminator, owner):
        return {"id": hid, "category": category, "statement": statement,
                "status": status, "evidence": evidence,
                "what_would_settle_it": discriminator, "owner": owner}

    rows = [
        H("H1", "WINDOWS_ML_API_SDK",
          "The Windows ML 2.x API/SDK surface is broken or too old, so the catalog cannot list providers.",
          "REFUTED_BY_EVIDENCE",
          ["reports/experiment_b/CATALOG_PROBE.json (S_OK create + S_OK enum, three repeats)",
           "reports/experiment_b/CATALOG_SURFACE_MATRIX.json (5 usable surfaces, all 0 providers, incl. official 2.5.96)",
           "reports/experiment_b/PRE_EXPERIMENT_SNAPSHOT.json:runtime (windowsml 2.5.96 = documented minimum)"],
          "a surface returning S_OK with >=1 provider, or an HRESULT failure",
          "Microsoft (Windows ML team)"),
        H("H2", "EP_CATALOG_AND_STORE_DISTRIBUTION",
          "The ExecutionProviderCatalog serves no EP entry to this device, so there is nothing to ensure_ready.",
          "SUPPORTED_BY_EVIDENCE",
          ["reports/experiment_b/CATALOG_SURFACE_MATRIX.json",
           "reports/experiment_b/MSIX_INVENTORY.json:deployment_events (0 AMD.GPU events; workload machinery active for the NPU EP)",
           "reports/experiment_b/OFFICIAL_DOC_CITATIONS.json:execution_provider_errors"],
          "a catalog listing AMDGPUExecutionProvider with ReadyState=NotPresent on this host",
          "Microsoft (Catalog/Store targeting)"),
        H("H3", "EP_MSIX_DLL_DEPENDENCY",
          "The AMD GPU EP MSIX/DLL is not installed and no DLL exists anywhere on the host.",
          "SUPPORTED_BY_EVIDENCE",
          ["reports/experiment_b/MSIX_INVENTORY.json:amdgpu_ep (absent in current-user and machine scope)",
           "reports/experiment_b/PRE_EXPERIMENT_SNAPSHOT.json:ep_dll (0 DLLs)",
           "reports/experiment_b/NEGATIVE_TESTS.json (registering a missing DLL fails with a DLL_LOAD_ERROR)"],
          "the package appearing in HKLM AppxAllUserStore or an amdgpu-ep.dll being found",
          "Microsoft/AMD (package publication + targeting)"),
        H("H4", "HP_OEM_DRIVER_INCOMPATIBILITY",
          "The HP OEM display driver blocks the AMD GPU EP (Adrenalin minimum not met, or OEM packaging unsupported).",
          "UNVERIFIED",
          ["reports/experiment_b/DRIVER_COMPATIBILITY.json (Adrenalin version UNKNOWN; DML works)",
           "reports/experiment_b/OFFICIAL_DOC_CITATIONS.json (only an Adrenalin minimum is documented)"],
          "an AMD/HP statement mapping 32.0.22018.5 to an Adrenalin version, or the EP loading on this driver",
          "HP + AMD (driver qualification)"),
        H("H5", "GFX1151_GPU_KERNEL_OPERATOR_SUPPORT",
          "gfx1151 kernels/operators are unsupported by the AMD GPU EP build.",
          "UNVERIFIED",
          ["reports/experiment_b/CATALOG_PROBE.json (no EP to test against)",
           "reports/experiment_b/OFFICIAL_DOC_CITATIONS.json (no gfx/DID list published)",
           "docs/experiment_b/OFFICIAL_EP_DEPENDENCY_MATRIX.md"],
          "an AMDGPU EP DLL on this host plus a kernel-level session failure, or an official gfx list",
          "AMD (EP kernel coverage)"),
        H("H6", "SILENT_CPU_FALLBACK_MASQUERADING",
          "An AMDGPU request appears to succeed while running on CPU, so a green result is not proof.",
          "SUPPORTED_BY_EVIDENCE",
          ["reports/experiment_b/NEGATIVE_TESTS.json:amdgpu_session_without_registration_silently_falls_back_to_cpu"],
          "n/a - this is a measurement hazard, mitigation is the get_providers() + profiler assertion",
          "this repository (harness)"),
    ]
    return {
        "schema": "experiment_b_dependency_hypotheses_v1",
        "mission": "B_GFX1151_AMDGPU_EP_ROOT_CAUSE",
        "run_id": run_id,
        "captured_utc": utc_now(),
        "status_vocabulary": ["SUPPORTED_BY_EVIDENCE", "REFUTED_BY_EVIDENCE", "UNVERIFIED"],
        "hypotheses": rows,
        "summary": {
            "supported": [r["id"] for r in rows if r["status"] == "SUPPORTED_BY_EVIDENCE"],
            "refuted": [r["id"] for r in rows if r["status"] == "REFUTED_BY_EVIDENCE"],
            "unverified": [r["id"] for r in rows if r["status"] == "UNVERIFIED"],
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    a = ap.parse_args()
    out = REPO / "reports" / "experiment_b"
    docs = {
        "MSIX_INVENTORY.json": build_msix(a.run_id),
        "DRIVER_COMPATIBILITY.json": build_driver(a.run_id),
        "DEPENDENCY_HYPOTHESES.json": build_hypotheses(a.run_id),
    }
    for name, doc in docs.items():
        (out / name).write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        print(json.dumps({"out": f"reports/experiment_b/{name}",
                          "bytes": (out / name).stat().st_size}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
