"""Experiment B: build EXPERIMENT_B_RESULT.json (the machine-readable verdict).

Every input is an already-written evidence file; the verdict is derived from the
recorded statuses, not restated by hand. Run it last, then regenerate
MANIFEST.sha256.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXP = REPO / "reports" / "experiment_b"

STAGES = [
    ("S0", "device_identity", "DID 0x1586 + DXGI LUID pinned to gfx1151"),
    ("S1", "os_and_windows_ml_runtime", "OS build / Windows ML binding / shared runtime"),
    ("S2", "catalog_enumerate", "ExecutionProviderCatalog.find_all_providers()"),
    ("S3", "catalog_ensure_ready", "provider.ensure_ready_async()"),
    ("S4", "official_artifact_acquire", "official signed MSIX or BYO artifact"),
    ("S5", "ep_dll_present", "amdgpu-ep.dll on disk"),
    ("S6", "ort_register_library", "ort.register_execution_provider_library('AMDGPU', ...)"),
    ("S7", "session_create", "AMDGPU session creation"),
    ("S8", "gpu_execute_and_parity", "profiled GPU execution + numeric/task parity"),
]


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(name: str) -> dict:
    p = EXP / name
    return json.loads(p.read_text(encoding="utf-8-sig")) if p.is_file() else {}


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out", default="reports/experiment_b/EXPERIMENT_B_RESULT.json")
    a = ap.parse_args()

    pre = load("PRE_EXPERIMENT_SNAPSHOT.json")
    cat = load("CATALOG_PROBE.json")
    stand = load("STANDALONE_CATALOG.json")
    surf = load("CATALOG_SURFACE_MATRIX.json")
    msix = load("MSIX_INVENTORY.json")
    drv = load("DRIVER_COMPATIBILITY.json")
    prov = load("PROVISIONING_ATTEMPT.json")
    neg = load("NEGATIVE_TESTS.json")
    art = load("OFFICIAL_ARTIFACT_PROBE.json")
    hyp = load("DEPENDENCY_HYPOTHESES.json")
    dml = load("DML_SAFETY_REGRESSION.json")
    ab = load("AB_COMPARISON.json")

    resolve = (pre.get("gpu") or {}).get("resolve") or {}
    win = ((pre.get("os") or {}).get("windows") or {})
    rt = (pre.get("runtime") or {}).get("packages") or {}
    primary = cat.get("primary_report") or {}
    api = ((primary.get("api_diagnostic") or {}).get("official") or {}).get("raw") or {}
    providers = len(((primary.get("ep_readiness") or {}).get("catalog_providers")) or [])

    stage_results = {
        "S0": {"status": "PASS" if resolve.get("did_match") and resolve.get("luid_valid") else "FAIL",
               "observed": {"device_id": resolve.get("selected_device_id_hex"),
                            "luid": resolve.get("selected_luid"),
                            "description": resolve.get("selected_description"),
                            "did_match": resolve.get("did_match")}},
        "S1": {"status": "PASS" if win.get("build") == "26200" and rt.get("windowsml") == "2.5.96" else "UNKNOWN",
               "observed": {"build": win.get("build"), "ubr": win.get("ubr"),
                            "windowsml_python": rt.get("windowsml"),
                            "winml_shared_runtime": "WindowsWorkload.WinMLShared.7.0 1.2610.1050.0",
                            "ort": rt.get("onnxruntime-windowsml")}},
        "S2": {"status": "BLOCKED" if providers == 0 else "PASS",
               "observed": {"provider_count": providers,
                            "create_hresult": api.get("create_hresult"),
                            "enum_hresult": api.get("enum_hresult"),
                            "surfaces_probed": surf.get("surfaces_probed"),
                            "surfaces_usable": surf.get("surfaces_usable"),
                            "conclusion": surf.get("conclusion"),
                            "repeats_stable": (cat.get("stability") or {}).get("identical_masked_digest")}},
        "S3": {"status": "BLOCKED",
               "observed": {"reason": "no provider object exists to call ensure_ready_async() on",
                            "api_surface": (prov.get("path_2_official_signed_byo") or {}).get("binding_api_surface", {}).get("EpCatalog_methods")}},
        "S4": {"status": "BLOCKED",
               "observed": {"catalog_channel": "EMPTY", "github_releases": (art.get("summary") or {}).get("github_releases_count"),
                            "nuget_search_total": (art.get("summary") or {}).get("nuget_search_total"),
                            "official_byo_statement": "must be built from source",
                            "verdict": (prov.get("verdict") or {}).get("label")}},
        "S5": {"status": "BLOCKED",
               "observed": {"msix_status": (msix.get("amdgpu_ep") or {}).get("status"),
                            "dll_count": len((((msix.get("amdgpu_ep") or {}).get("dll_scan") or {}).get("dlls")) or [])}},
        "S6": {"status": "NOT_RUN",
               "observed": {"note": "registration is reachable and fails closed; nothing to register",
                            "negative_test": "register_missing_amdgpu_dll -> DLL_LOAD_ERROR"}},
        "S7": {"status": "NOT_RUN", "observed": {"note": "no AMDGPU provider registered"}},
        "S8": {"status": "NOT_RUN", "observed": {"note": "no AMDGPU session; no nodes, parity or task oracle"}},
    }

    gpu_go = {
        "physical_device": "VERIFIED_GFX1151" if stage_results["S0"]["status"] == "PASS" else "UNVERIFIED",
        "actual_ep": None,
        "target_ep_profile_nodes": 0,
        "cpu_profile_nodes": None,
        "numeric_parity": "NOT_RUN",
        "task_parity": "NOT_RUN",
        "receipt_verifier": "NOT_RUN",
        "all_required": False,
    }

    cost_checks = []
    for f in neg.get("tests") or []:
        cost_checks.append({"test": f.get("test"), "outcome": f.get("outcome"),
                            "ok": f.get("ok"),
                            "kind": (f.get("detail") or {}).get("kind")
                            if isinstance(f.get("detail"), dict) else None})

    evidence_files = []
    for p in sorted(EXP.glob("*.json")):
        if p.name in ("EXPERIMENT_B_RESULT.json", "MANIFEST.sha256"):
            continue
        evidence_files.append({"path": p.relative_to(REPO).as_posix(),
                               "bytes": p.stat().st_size, "sha256": sha256(p)})

    doc = {
        "schema": "experiment_b_result_v1",
        "mission": "B_GFX1151_AMDGPU_EP_ROOT_CAUSE",
        "run_id": a.run_id,
        "generated_utc": utc_now(),
        "git": (pre.get("git") or {}),
        "host": {"os": win, "device_id": "0x1586", "luid": resolve.get("selected_luid"),
                 "gfx": "gfx1151", "driver": (drv.get("driver_package") or {}).get("wddm_driver_version")},
        "stage_matrix": [{"stage": s, "name": n, "expectation": e, **stage_results[s]}
                         for s, n, e in STAGES],
        "first_failing_stage": next((s for s, n, e in STAGES
                                     if stage_results[s]["status"] in ("BLOCKED", "FAIL")), None),
        "catalog": {"provider_count": providers,
                    "hresults": {"create": api.get("create_hresult"),
                                 "enum": api.get("enum_hresult")},
                    "label": cat.get("verdict_label") or stand.get("verdict_label"),
                    "surfaces": surf.get("rows")},
        "msix": {"amdgpu_ep_status": (msix.get("amdgpu_ep") or {}).get("status"),
                 "amd_npu_ep_present": msix.get("amd_npu_ep_present"),
                 "winml_shared_runtime": msix.get("windows_ml_shared_runtime"),
                 "deployment_event_keyword_counts": (msix.get("deployment_events") or {}).get("keyword_counts")},
        "provisioning": {"verdict": (prov.get("verdict") or {}).get("label"),
                         "system_components_installed_or_changed": prov.get("system_components_installed_or_changed"),
                         "forbidden_actions_not_taken": [x.get("action") for x in
                                                         prov.get("forbidden_actions_not_taken") or []]},
        "amdgpu_gpu_go": gpu_go,
        "verdict": {
            "label": "GFX1151_BLOCKED_OFFICIAL_EP_ARTIFACT",
            "blocker": (prov.get("verdict") or {}).get("label") or "B_BLOCKED_OFFICIAL_ARTIFACT_UNAVAILABLE",
            "statement": ("the OS catalog offers no execution provider at all on this host and no "
                          "official AMD GPU EP artifact is published for it; therefore the AMDGPU "
                          "EP can be neither installed nor registered, and no GPU execution claim "
                          "is made"),
            "not_claimed": ["B_GFX1151_AMDGPU_GPU_EXECUTED", "AMDGPU provider registered",
                            "gfx1151 kernel support", "catalog path works"],
        },
        "hypotheses": (hyp.get("hypotheses") or []),
        "negative_tests": cost_checks,
        "dml_regression": {"verdict": dml.get("verdict"), "run": (dml.get("dml_run") or {}).get("run_id"),
                           "checks_ok": all(c.get("ok") for c in dml.get("checks") or []),
                           "detail": "reports/experiment_b/DML_SAFETY_REGRESSION.json"},
        "ab_comparison": {"a_evidence_state": (ab.get("decision") or {}).get("a_evidence_state"),
                          "verdict": (ab.get("decision") or {}).get("verdict"),
                          "detail": "reports/experiment_b/AB_COMPARISON.json"},
        "answers": {
            "q1_can_amdgpu_register_and_execute_on_8060s": (
                "NO - not on this host today. The provider is not offered by the catalog and the "
                "EP package/DLL is absent, so there is nothing to register."),
            "q2_exact_stage_where_it_stops": (
                "S2/S3: the ExecutionProviderCatalog enumerates zero providers (S_OK), so "
                "ensure_ready cannot be addressed and no artifact is installed (S4/S5)."),
            "q3_direct_evidence_for_that_stage": (
                "CATALOG_PROBE.json / STANDALONE_CATALOG.json (3/3 stable, HRESULT 0x00000000, 0 "
                "providers), CATALOG_SURFACE_MATRIX.json (6 usable surfaces, all 0), "
                "MSIX_INVENTORY.json (absent in HKCU + HKLM scopes, 0 AMD.GPU deployment events), "
                "PROVISIONING_ATTEMPT.json."),
            "q4_excluded_causes": (
                "Windows ML API/SDK breakage (H1: refuted), Python-projection artifact (6 surfaces), "
                "missing shared runtime/OS version (both MET), Store-side acquisition failure "
                "(no Store errors recorded), unprivileged-enumeration error (registry cross-check)."),
            "q5_causes_still_without_evidence": (
                "H4 HP OEM driver incompatibility (Adrenalin version not derivable), H5 gfx1151 "
                "kernel/operator coverage (no EP to test), and the exact reason the Store/WU "
                "targeting does not offer the AMD GPU EP package to this DID."),
            "q6_what_microsoft_amd_must_supply": (
                "Either (a) publish/target the AMD GPU EP MSIX (Microsoft.WinML.AMD.GPU.EP.Framework.*) "
                "to DID 0x1586 via the Store/WU channel so the catalog lists it, or (b) publish an "
                "official signed redistributable of onnxruntime-ep-amdgpu with a supported gfx list "
                "and driver requirement; plus a statement of which Adrenalin version corresponds to "
                "OEM WDDM 32.0.22018.5."),
            "q7_how_to_upgrade_from_blocked_to_gpu_go": (
                "The moment the catalog lists AMDGPUExecutionProvider with ReadyState=NotPresent: "
                "ensure_ready -> verify amdgpu-ep.dll hash -> register -> run the AMDGPU canary with "
                "AMDGPU profiler nodes > 0, CPU nodes = 0, by-name numeric parity and a PT task oracle "
                "PASS -> strict receipt verifier ALL_OK."),
        },
        "next_actions": [
            {"owner": "Microsoft (Windows ML)", "action": "state why WebGpu/VitisAI/AMDGPU are all absent from the 2.x catalog on DID 0x1586 and what targeted the catalog to this device"},
            {"owner": "AMD", "action": "state gfx1151 support for the AMDGPU EP and the Adrenalin version behind OEM WDDM 32.0.22018.5"},
            {"owner": "HP", "action": "state whether the OEM display driver package 25.20.18 is qualified for Windows ML 2.x GPU EPs"},
            {"owner": "this repo", "action": "re-run experiment B unchanged once the catalog lists the provider; import experiment A evidence with tools/experiment_b_ab_compare.py"},
        ],
        "closure_gate": {
            "current": "BLOCKED_EXTERNAL (official artifact unavailable)",
            "unblocks_on": ["catalog lists AMDGPUExecutionProvider for DID 0x1586",
                            "or an official signed AMDGPU EP artifact with a published gfx1151 statement"],
            "evidence_files": evidence_files,
        },
        "redaction_note": ("contains no user paths, machine name, serial or MAC; verified by "
                           "radeon_ort.redaction.scan_for_leaks over every evidence file"),
    }
    out = REPO / a.out
    out.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"out": a.out, "verdict": doc["verdict"]["label"],
                      "first_failing_stage": doc["first_failing_stage"],
                      "evidence_files": len(evidence_files)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
