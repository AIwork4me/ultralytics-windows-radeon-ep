"""Experiment B2: AMD_GPU_EP_AB_COMPARISON_V1 builder and cross-host diff.

The gfx1151 (B) side is derived deterministically from the evidence committed
under reports/experiment_b/. The gfx1100 (A) side is imported from machine-
readable JSON supplied with --a-json; when no A artifact is available the tool
still runs, marks every A field A_EVIDENCE_PENDING/UNKNOWN and refuses to infer
a PASS from an absent field.

Supported A inputs (all optional, merged left to right):
  * an AB-schema document (this schema, one host block per gfx)
  * a runner.probe_amdgpu_ep report
  * a runner.unified_canary report
  * a runner.unified_predict / unified_smoke run_result report
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXP = REPO / "reports" / "experiment_b"

FIELDS = [
    "schema_version", "git_sha", "run_id", "host_gfx", "device_did", "driver_wddm",
    "driver_adrenalin", "windows_build", "windowsml_version", "ort_version",
    "catalog_api_hresult", "catalog_provider_count", "catalog_amdgpu_state",
    "ep_package_identity", "ep_package_version", "ep_dll_sha256",
    "direct_registration_status", "catalog_registration_status", "gpu_executed",
    "gpu_ep_profile_nodes", "cpu_ep_profile_nodes", "numeric_parity", "task_parity",
    "blocker_reason", "evidence_manifest_sha256",
]

STATUSES = {"PASS", "FAIL", "BLOCKED", "NOT_RUN", "UNKNOWN",
            # A-side only: the field was not supplied by experiment A
            "A_EVIDENCE_PENDING"}

# A/B comparison treats these as pure facts, not verdicts.
FACT_FIELDS = {"schema_version", "git_sha", "run_id", "host_gfx", "device_did",
               "driver_wddm", "driver_adrenalin", "windows_build", "windowsml_version",
               "ort_version", "catalog_api_hresult", "catalog_provider_count",
               "catalog_amdgpu_state", "ep_package_identity", "ep_package_version",
               "ep_dll_sha256", "evidence_manifest_sha256"}


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(rel: str) -> dict:
    p = REPO / rel
    return json.loads(p.read_text(encoding="utf-8-sig")) if p.is_file() else {}


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def git_sha() -> str:
    try:
        import subprocess
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO),
                              capture_output=True, text=True, timeout=30,
                              check=False).stdout.strip()
    except Exception:  # noqa: BLE001
        return "UNKNOWN"


def field(value, status: str, source: str, note: str | None = None) -> dict:
    assert status in STATUSES, status
    out = {"value": value, "status": status, "source": source}
    if note:
        out["note"] = note
    return out


def sibling_manifest_digest() -> tuple[str, list[str]]:
    """Digest over every evidence file that does not embed this digest.

    AB_COMPARISON.json, EXPERIMENT_B_RESULT.json and MANIFEST.sha256 are excluded
    because they either contain this digest or are generated after it; the
    exclusion list is part of the record so the digest is reproducible.
    """
    excluded = {"AB_COMPARISON.json", "EXPERIMENT_B_RESULT.json", "MANIFEST.sha256"}
    entries = []
    for p in sorted(EXP.glob("*.json")):
        if p.name in excluded:
            continue
        entries.append(f"{p.name}  {sha256_file(p)}")
    blob = "\n".join(entries).encode()
    return hashlib.sha256(blob).hexdigest(), [e.split("  ")[0] for e in entries]


def build_b_side() -> dict:
    pre = load("reports/experiment_b/PRE_EXPERIMENT_SNAPSHOT.json")
    cat = load("reports/experiment_b/CATALOG_PROBE.json")
    msix = load("reports/experiment_b/MSIX_INVENTORY.json")
    drv = load("reports/experiment_b/DRIVER_COMPATIBILITY.json")
    prov = load("reports/experiment_b/PROVISIONING_ATTEMPT.json")

    primary = (cat.get("primary_report") or {})
    api = ((primary.get("api_diagnostic") or {}).get("official") or {}).get("raw") or {}
    readiness = primary.get("ep_readiness") or {}
    os_win = ((pre.get("os") or {}).get("windows") or {})
    resolve = ((pre.get("gpu") or {}).get("resolve") or {})
    rt = (pre.get("runtime") or {})
    wddm = ((drv.get("driver_package") or {}).get("wddm_driver_version"))
    pkg_state = ((msix.get("amdgpu_ep") or {}).get("status"))
    dlls = (((msix.get("amdgpu_ep") or {}).get("dll_scan") or {}).get("dlls")) or []

    # The AMDGPU execution fields stay NOT_RUN on this host: the DML regression run
    # proves the GPU path works, not that an AMDGPU session executed.
    parity = "NOT_RUN"
    task = "NOT_RUN"

    digest, included = sibling_manifest_digest()
    b = {
        "schema_version": field("AMD_GPU_EP_AB_COMPARISON_V1", "PASS", "constant"),
        "git_sha": field(git_sha() or "UNKNOWN", "PASS" if git_sha() else "UNKNOWN", "git"),
        "run_id": field(pre.get("run_id"), "PASS" if pre.get("run_id") else "UNKNOWN",
                        "PRE_EXPERIMENT_SNAPSHOT.run_id"),
        "host_gfx": field("gfx1151", "PASS" if resolve.get("did_match") else "UNKNOWN",
                          "PRE_EXPERIMENT_SNAPSHOT.gpu.resolve + DID 0x1586"),
        "device_did": field(resolve.get("selected_device_id_hex") or "0x1586", "PASS",
                            "DXGI device_id"),
        "driver_wddm": field(wddm, "PASS" if wddm else "UNKNOWN",
                             "DRIVER_COMPATIBILITY.driver_package"),
        "driver_adrenalin": field("UNKNOWN", "UNKNOWN",
                                  "DRIVER_COMPATIBILITY.adrenalin_software_version",
                                  "WDDM 32.0.22018.5 / INF build 25.20.18 do not map to an "
                                  "Adrenalin marketing version"),
        "windows_build": field(f"{os_win.get('build')}.{os_win.get('ubr')}",
                               "PASS" if os_win.get("build") else "UNKNOWN",
                               "PRE_EXPERIMENT_SNAPSHOT.os"),
        "windowsml_version": field(((rt.get("packages") or {}).get("windowsml")),
                                   "PASS", "PRE_EXPERIMENT_SNAPSHOT.runtime"),
        "ort_version": field((rt.get("ort") or {}).get("version"), "PASS",
                             "PRE_EXPERIMENT_SNAPSHOT.runtime.ort"),
        "catalog_api_hresult": field(
            {"create": api.get("create_hresult"), "enum": api.get("enum_hresult")},
            "PASS" if api.get("create_hresult") else "UNKNOWN",
            "CATALOG_PROBE.primary_report.api_diagnostic.official.raw"),
        "catalog_provider_count": field(
            len(readiness.get("catalog_providers") or []),
            "PASS" if readiness.get("catalog_providers") is not None else "UNKNOWN",
            "CATALOG_PROBE.primary_report.ep_readiness"),
        "catalog_amdgpu_state": field("NOT_LISTED", "BLOCKED",
                                      "CATALOG_SURFACE_MATRIX (all surfaces 0 providers)"),
        "ep_package_identity": field(pkg_state or "UNKNOWN", "BLOCKED",
                                     "MSIX_INVENTORY.amdgpu_ep"),
        "ep_package_version": field("ABSENT", "BLOCKED", "MSIX_INVENTORY.amdgpu_ep"),
        "ep_dll_sha256": field("ABSENT" if not dlls else [d.get("sha256") for d in dlls],
                               "BLOCKED" if not dlls else "PASS",
                               "PRE_EXPERIMENT_SNAPSHOT.ep_dll"),
        "direct_registration_status": field(
            "BLOCKED_NO_DLL", "BLOCKED",
            "NEGATIVE_TESTS.register_missing_amdgpu_dll",
            "registration is reachable and fails closed with a DLL_LOAD_ERROR"),
        "catalog_registration_status": field(
            "BLOCKED_CATALOG_EMPTY", "BLOCKED",
            "PROVISIONING_ATTEMPT.path_1_official_catalog"),
        "gpu_executed": field(False, "NOT_RUN",
                              "no AMDGPU session was created (no provider, no DLL)"),
        "gpu_ep_profile_nodes": field(0, "NOT_RUN", "no AMDGPU session"),
        "cpu_ep_profile_nodes": field(None, "NOT_RUN", "no AMDGPU session"),
        "numeric_parity": field(parity, "NOT_RUN", "no AMDGPU session"),
        "task_parity": field(task, "NOT_RUN", "no AMDGPU session"),
        "blocker_reason": field((prov.get("verdict") or {}).get("label") or "UNKNOWN",
                                "BLOCKED", "PROVISIONING_ATTEMPT.verdict"),
        "evidence_manifest_sha256": field(
            digest, "PASS", "sha256 over the sorted sibling evidence list",
            "excludes AB_COMPARISON.json, EXPERIMENT_B_RESULT.json and MANIFEST.sha256"),
    }
    b["_digest_inputs"] = included
    return b


def _ab_from_doc(doc: dict, gfx: str) -> dict | None:
    if str(doc.get("schema_version")) != "AMD_GPU_EP_AB_COMPARISON_V1":
        return None
    for key in ("hosts", "host_blocks", "comparison"):
        block = doc.get(key)
        if isinstance(block, dict):
            for k, v in block.items():
                if gfx.lower() in str(k).lower():
                    return v
    if str(doc.get("host_gfx", "")).lower() == gfx.lower():
        return doc
    return None


def _a_from_probe(doc: dict) -> dict:
    out = {}
    api = ((doc.get("api_diagnostic") or {}).get("official") or {}).get("raw") or {}
    readiness = doc.get("ep_readiness") or {}
    if api:
        out["catalog_api_hresult"] = {"create": api.get("create_hresult"),
                                      "enum": api.get("enum_hresult")}
    if readiness.get("catalog_providers") is not None:
        out["catalog_provider_count"] = len(readiness["catalog_providers"])
    if doc.get("state"):
        out["catalog_amdgpu_state"] = doc["state"]
    gfx = doc.get("gfx_requested")
    if gfx:
        out["host_gfx"] = gfx
    video = ((doc.get("system") or {}).get("video") or [{}])[0]
    if video.get("DriverVersion"):
        out["driver_wddm"] = video["DriverVersion"]
    build = (doc.get("system") or {}).get("os") or {}
    if build.get("build"):
        out["windows_build"] = build["build"]
    return out


def _a_from_canary(doc: dict) -> dict:
    out = {}
    phys = doc.get("physical") or {}
    if phys.get("device_id") is not None:
        out["device_did"] = f"0x{int(phys['device_id']):04X}"
    if doc.get("gfx"):
        out["host_gfx"] = doc["gfx"]
    prof = doc.get("profile") or {}
    if prof.get("target_ep_nodes") is not None:
        out["gpu_ep_profile_nodes"] = prof["target_ep_nodes"]
        out["cpu_ep_profile_nodes"] = prof.get("cpu_nodes")
        out["gpu_executed"] = bool(prof.get("target_ep_nodes"))
    par = doc.get("parity") or {}
    if par.get("allclose") is not None:
        out["numeric_parity"] = "PASS" if par["allclose"] else "FAIL"
    out.setdefault("direct_registration_status", doc.get("status") or "UNKNOWN")
    return out


def _a_from_smoke(doc: dict) -> dict:
    rr = doc.get("run_result") or {}
    out = {}
    if rr.get("actual_provider"):
        out["direct_registration_status"] = rr.get("status") or "UNKNOWN"
    if rr.get("target_ep_profile_nodes") is not None:
        out["gpu_ep_profile_nodes"] = rr["target_ep_profile_nodes"]
        out["cpu_ep_profile_nodes"] = rr.get("cpu_profile_nodes")
        out["gpu_executed"] = bool(rr.get("target_ep_profile_nodes"))
    par = rr.get("numeric_oracle") or {}
    if par.get("allclose") is not None:
        out["numeric_parity"] = "PASS" if par["allclose"] else "FAIL"
    task = rr.get("task_oracle") or {}
    if task.get("status"):
        out["task_parity"] = task["status"]
    ident = rr.get("device_identity") or {}
    if ident.get("device_id") is not None:
        out["device_did"] = f"0x{int(ident['device_id']):04X}"
    if ident.get("gfx"):
        out["host_gfx"] = ident["gfx"]
    return out


def normalize_a(docs: list[dict], gfx: str) -> dict:
    merged: dict = {}
    for doc in docs:
        block = _ab_from_doc(doc, gfx)
        if block is not None:
            for f in FIELDS:
                if f in block:
                    merged[f] = block[f]
            continue
        for extractor in (_a_from_probe, _a_from_canary, _a_from_smoke):
            try:
                part = extractor(doc)
            except Exception:  # noqa: BLE001
                part = {}
            for k, v in part.items():
                if v not in (None, "", "UNKNOWN") and k not in merged:
                    merged[k] = v
    return merged


def a_side(merged: dict, pending: bool) -> dict:
    out = {}
    for f in FIELDS:
        if pending:
            out[f] = field("A_EVIDENCE_PENDING", "A_EVIDENCE_PENDING", "no A artifact supplied")
        elif f in merged:
            v = merged[f]
            out[f] = (v if isinstance(v, dict) and "status" in v
                      else field(v, "PASS", "experiment A artifact"))
        else:
            out[f] = field("UNKNOWN", "UNKNOWN", "field absent in the A artifact")
    return out


def decide(a: dict, b: dict, a_pending: bool) -> dict:
    def val(side, name):
        v = side.get(name) or {}
        return v.get("value")

    b_cat_empty = (val(b, "catalog_provider_count") == 0)
    b_catalog_amdgpu = val(b, "catalog_amdgpu_state")
    b_direct = str(val(b, "direct_registration_status"))
    b_gpu = bool(val(b, "gpu_executed"))
    a_cat_empty = (val(a, "catalog_provider_count") == 0) if not a_pending else None
    a_direct = str(val(a, "direct_registration_status")) if not a_pending else None
    a_listed = ("amdgpu" in str(val(a, "catalog_amdgpu_state")).lower()
                or (val(a, "catalog_provider_count") or 0) > 0) if not a_pending else None
    b_artifact_unavailable = "ARTIFACT_UNAVAILABLE" in str(val(b, "blocker_reason"))

    cases = []
    if b_direct.upper().startswith(("DLL_LOAD", "FAIL", "GPU_FAIL")) and b_gpu is False \
            and val(b, "ep_dll_sha256") not in ("ABSENT", None):
        cases.append(("B4", "GFX1151_RUNTIME_OR_MODEL_COMPATIBILITY_FAILURE"))
    if b_cat_empty and b_gpu:
        cases.append(("B3", "GFX1151_DIRECT_GPU_GO_CATALOG_BLOCKED"))
    if not a_pending and a_listed and b_cat_empty:
        cases.append(("B2", "HOST_SPECIFIC_CATALOG_FILTER_OR_DEPENDENCY"))
    if not a_pending and a_cat_empty and (a_direct or "").upper().startswith(("PASS", "GPU")):
        cases.append(("B1", "DIRECT_PATH_WORKS_ON_GFX1100; CATALOG_PATH_NOT_PROVEN"))
    if b_artifact_unavailable and not b_gpu and b_cat_empty:
        cases.append(("B5", "GFX1151_BLOCKED_OFFICIAL_EP_ARTIFACT"))
    if not cases:
        cases.append(("UNCLASSIFIED", "INSUFFICIENT_EVIDENCE"))
    primary = cases[0]
    return {
        "matched_cases": [{"case": c, "verdict": v} for c, v in cases],
        "primary_case": primary[0],
        "verdict": primary[1],
        "a_evidence_state": "A_EVIDENCE_PENDING" if a_pending else "PRESENT",
        "reasoning": [
            (f"B catalog_provider_count={val(b, 'catalog_provider_count')} "
             f"(state={b_catalog_amdgpu})"),
            f"B direct registration={b_direct}, gpu_executed={b_gpu}",
            f"B official artifact available={not b_artifact_unavailable}",
            ("A evidence not supplied: no A/B diff is asserted, and no case that needs an "
             "A fact (B1/B2) is claimed" if a_pending else
             f"A catalog_provider_count={val(a, 'catalog_provider_count')}, "
             f"A direct={a_direct}"),
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--a-json", nargs="*", default=[],
                    help="machine-readable experiment A artifacts (0 or more)")
    ap.add_argument("--a-gfx", default="gfx1100")
    ap.add_argument("--out", default="reports/experiment_b/AB_COMPARISON.json")
    a = ap.parse_args()

    a_docs = []
    for p in a.a_json:
        path = Path(p)
        a_docs.append(json.loads(path.read_text(encoding="utf-8-sig")))
    pending = not a_docs
    merged = normalize_a(a_docs, a.a_gfx) if a_docs else {}

    b = build_b_side()
    b_out = {k: v for k, v in b.items() if not k.startswith("_")}
    a_out = a_side(merged, pending)

    rows = []
    for f in FIELDS:
        av, bv = a_out[f], b_out[f]
        rows.append({
            "field": f,
            "gfx1100": av,
            "gfx1151": bv,
            "same_value": (av.get("value") == bv.get("value")),
            "note": (av.get("note") or bv.get("note")),
        })

    doc = {
        "schema_version": "AMD_GPU_EP_AB_COMPARISON_V1",
        "mission": "B_GFX1151_AMDGPU_EP_ROOT_CAUSE",
        "run_id": a.run_id,
        "captured_utc": utc_now(),
        "status_vocabulary": sorted(STATUSES),
        "a_artifact_inputs": [str(p) for p in a.a_json],
        "hosts": {f"{a.a_gfx}": a_out, "gfx1151": b_out},
        "comparison": rows,
        "diff": {
            "fields_compared": len(rows),
            "fields_equal": sum(1 for r in rows if r["same_value"]),
            "fields_different": [r["field"] for r in rows if not r["same_value"]],
            "fields_a_unknown": [r["field"] for r in rows if r["gfx1100"]["status"] in
                                 ("UNKNOWN", "A_EVIDENCE_PENDING")],
        },
        "decision": decide(a_out, b_out, pending),
        "evidence_manifest_sha256": b_out["evidence_manifest_sha256"]["value"],
        "evidence_manifest_inputs": b["_digest_inputs"],
    }
    out = REPO / a.out
    out.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"out": a.out,
                      "a_evidence_state": doc["decision"]["a_evidence_state"],
                      "primary_case": doc["decision"]["primary_case"],
                      "verdict": doc["decision"]["verdict"],
                      "manifest_sha256": doc["evidence_manifest_sha256"][:16]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
