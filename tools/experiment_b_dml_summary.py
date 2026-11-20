"""Experiment B7: assemble the DirectML safety-regression verdict.

Reads the run directory produced by runner.run_matrix (never re-derives a PASS
from a label), re-runs the strict receipt verifier, and checks the specific
properties the mission requires: target-EP nodes > 0, CPU nodes = 0, numeric
parity, task oracle, Job Object reclamation, and no receipt reuse.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXP = REPO / "reports" / "experiment_b"
sys.path.insert(0, str(REPO / "src"))


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig")) if p.is_file() else {}


def verify(run_dir: Path) -> dict:
    p = subprocess.run([sys.executable, "-m", "runner.verify_receipts",
                        "--run-dir", str(run_dir)],
                       cwd=str(REPO), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600, check=False)
    out = p.stdout or ""
    verdict = None
    for ln in out.splitlines():
        if "verdict:" in ln:
            verdict = ln.split("verdict:", 1)[1].strip()
    from radeon_ort.redaction import redact_text
    return {"exit_code": p.returncode, "verdict": verdict,
            "stdout_tail": redact_text(out[-1200:]),
            "stderr_tail": redact_text((p.stderr or "")[-400:])}


def summarize_tier(run_id: str) -> dict:
    """Per-receipt summary of a full Tier-A run (no PASS is inferred from a label)."""
    run_dir = REPO / "reports" / "gfx1151" / run_id
    cases = []
    for p in sorted((run_dir / "receipts").glob("receipt_*.json")):
        rec = load(p)
        status = rec.get("status") or {}
        counts = (rec.get("session") or {}).get("provider_node_counts") or {}
        parity = rec.get("parity") or {}
        task = rec.get("task_parity") or {}
        cases.append({
            "case": rec.get("case_id"),
            "dml_status": status.get("dml"),
            "task_oracle": status.get("task_oracle"),
            "dml_nodes": int(counts.get("DmlExecutionProvider") or 0),
            "cpu_nodes": int(counts.get("CPUExecutionProvider") or 0),
            "parity_allclose": parity.get("all_outputs_allclose"),
            "task_passed": task.get("passed"),
            "receipt": p.relative_to(REPO).as_posix(),
            "receipt_sha256": sha256(p),
        })
    ok = bool(cases) and all(
        c["dml_status"] == "PASS_GPU_EXECUTED_FULL" and c["dml_nodes"] > 0
        and c["cpu_nodes"] == 0 and c["parity_allclose"] and c["task_passed"]
        for c in cases)
    return {"run_id": run_id, "run_dir": f"reports/gfx1151/{run_id}",
            "case_count": len(cases), "cases": cases, "all_ok": ok,
            "matrix_exit": load(run_dir / "MATRIX_EXIT.json"),
            "manifest_entries": len((load(run_dir / "manifest.sha256").get("entries")) or []),
            "verifier": verify_clean(run_dir)}


def git_ignored(run_dir: Path, rel_paths: list[str]) -> set[str]:
    if not rel_paths:
        return set()
    p = subprocess.run(["git", "check-ignore", "--stdin"], cwd=str(run_dir),
                       input="\n".join(rel_paths), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", check=False)
    return {ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()}


def verify_clean(run_dir: Path) -> dict:
    """Verify the run as a fresh checkout sees it (git-ignored payloads absent)."""
    import shutil
    import tempfile
    rel = sorted(p.relative_to(run_dir).as_posix()
                 for p in run_dir.rglob("*") if p.is_file())
    skip = git_ignored(run_dir, rel)
    tmp_root = Path(tempfile.mkdtemp(prefix="expb_verify_"))
    # the verifier requires run_dir.name == receipt.run_id, so the copy keeps the name
    tmp = tmp_root / run_dir.name
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        for r in rel:
            if r in skip:
                continue
            dst = tmp / r
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(run_dir / r, dst)
        res = verify(tmp)
        res["layout"] = "fresh_checkout_layout"
        res["excluded_git_ignored_files"] = len(skip)
        return res
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--dml-run-id", required=True)
    ap.add_argument("--prior-attempt-run-id", default="")
    ap.add_argument("--tier-run-id", default="",
                    help="optional full Tier-A DML run to summarise alongside")
    ap.add_argument("--out", default="reports/experiment_b/DML_SAFETY_REGRESSION.json")
    a = ap.parse_args()

    run_dir = REPO / "reports" / "gfx1151" / a.dml_run_id
    receipt_path = run_dir / "receipts" / "receipt_yolo26n__detect__dml.json"
    receipt = load(receipt_path)
    contract = load(run_dir / "RUN_CONTRACT.json")
    matrix_exit = load(run_dir / "MATRIX_EXIT.json")
    pipeline_exit = load(run_dir / "PIPELINE_EXIT.json")
    manifest = load(run_dir / "manifest.sha256")
    canary = load(EXP / "_quick" / "dml_canary_quick.json")
    job = load(EXP / "_raw" / "JOB_OBJECT_RECLAIM.json")

    status = receipt.get("status") or {}
    session = receipt.get("session") or {}
    counts = session.get("provider_node_counts") or {}
    parity = receipt.get("parity") or {}
    task = receipt.get("task_parity") or {}
    verdicts = (receipt.get("verdicts") or {}).get("dml") or {}
    prov = receipt.get("provenance") or {}
    binding = receipt.get("device_binding") or {}

    dml_nodes = int(counts.get("DmlExecutionProvider") or 0)
    cpu_nodes = int(counts.get("CPUExecutionProvider") or 0)

    prior = {}
    if a.prior_attempt_run_id:
        prior_dir = REPO / "reports" / "gfx1151" / a.prior_attempt_run_id
        prior_receipt = load(prior_dir / "receipts" / "receipt_yolo26n__detect__dml.json")
        prior = {"run_id": a.prior_attempt_run_id,
                 "status": (prior_receipt.get("status") or {}),
                 "receipt_sha256": (sha256(prior_dir / "receipts" /
                                           "receipt_yolo26n__detect__dml.json")
                                    if (prior_dir / "receipts").is_dir() else None),
                 "note": ("first attempt: execution PASS but the PT task oracle was "
                          "TASK_ORACLE_BLOCKED because torch was missing from the "
                          "interpreter the matrix passed as --export-python; preserved "
                          "as failure evidence and corrected in the run below")}

    tier = summarize_tier(a.tier_run_id) if a.tier_run_id else {}

    def single_case_checks() -> list:
        return [
            {"check": "yolo26n_detect_ran_on_dml",
             "ok": status.get("dml") == "PASS_GPU_EXECUTED_FULL",
             "observed": status.get("dml")},
            {"check": "gpu_full_dml_requested_and_bound",
             "ok": session.get("ep") == "DML" and verdicts.get("execution") == "PASS_FULL",
             "observed": {"session_ep": session.get("ep"),
                          "verdict_execution": verdicts.get("execution")}},
            {"check": "target_ep_nodes_positive", "ok": dml_nodes > 0,
             "observed": dml_nodes},
            {"check": "cpu_profile_nodes_zero", "ok": cpu_nodes == 0,
             "observed": cpu_nodes},
            {"check": "numeric_parity_by_name",
             "ok": bool(parity.get("all_outputs_allclose"))
                   and bool(parity.get("by_name_matching")),
             "observed": {"all_outputs_allclose": parity.get("all_outputs_allclose"),
                          "by_name_matching": parity.get("by_name_matching"),
                          "outputs": [{"name": o.get("name"),
                                       "allclose": o.get("allclose"),
                                       "max_abs_diff": o.get("max_abs_diff"),
                                       "threshold": o.get("threshold_used")}
                                      for o in (parity.get("outputs") or [])]}},
            {"check": "task_oracle_pass",
             "ok": status.get("task_oracle") == "PASS_TASK" and bool(task.get("passed")),
             "observed": {"status": status.get("task_oracle"),
                          "passed": task.get("passed"),
                          "metrics": task.get("metrics")}},
            {"check": "device_identity_verified_gfx1151",
             "ok": (receipt.get("host") or {}).get("status") == "VERIFIED_GFX1151"
                   or status.get("host") == "VERIFIED_GFX1151",
             "observed": {"status_host": status.get("host"),
                          "device_id": (receipt.get("host") or {}).get("device_id"),
                          "luid": (receipt.get("host") or {}).get("device_luid"),
                          "identity_method": (receipt.get("host") or {}).get("identity_method")}},
            {"check": "manifest_integrity",
             "ok": bool(manifest.get("entries"))
                   and verifier_main.get("verdict") == "ALL_OK",
             "observed": {"manifest_entries": len(manifest.get("entries") or []),
                          "verifier": verifier_main}},
            {"check": "job_object_reclaim",
             "ok": bool(job.get("all_passed")),
             "observed": {"source": "reports/experiment_b/_raw/JOB_OBJECT_RECLAIM.json",
                          "passed": job.get("passed"), "total": job.get("total")}},
            {"check": "receipt_not_reused",
             "ok": receipt.get("run_id") == a.dml_run_id
                   and receipt.get("rerun_of") is None,
             "observed": {"run_id": receipt.get("run_id"),
                          "rerun_of": receipt.get("rerun_of"),
                          "created_utc": receipt.get("created_utc")}},
            {"check": "contract_clean_worktree",
             "ok": prov.get("worktree_dirty") is False,
             "observed": {"git_head": prov.get("git_head_sha"),
                          "harness_sha256": prov.get("harness_sha256"),
                          "worktree_dirty": prov.get("worktree_dirty")}},
        ]

    verifier_main = verify_clean(run_dir)
    checks = single_case_checks()
    if tier:
        checks.insert(0, {
            "check": "tier_a_dml_matrix_all_pass",
            "ok": bool(tier.get("all_ok")),
            "observed": {"case_count": tier.get("case_count"),
                         "cases": [{"case": c["case"], "dml": c["dml_status"],
                                    "task": c["task_oracle"], "nodes": c["dml_nodes"],
                                    "cpu_nodes": c["cpu_nodes"]}
                                   for c in tier.get("cases") or []],
                         "verifier": (tier.get("verifier") or {}).get("verdict")}},
        )

    all_ok = all(c["ok"] for c in checks)
    doc = {
        "schema": "experiment_b_dml_safety_regression_v1",
        "mission": "B_GFX1151_AMDGPU_EP_ROOT_CAUSE",
        "run_id": a.run_id,
        "captured_utc": utc_now(),
        "purpose": ("prove that the AMDGPU investigation did not damage the working "
                    "DirectML path on this host"),
        "dml_run": {
            "run_id": a.dml_run_id,
            "run_dir": f"reports/gfx1151/{a.dml_run_id}",
            "case": "yolo26n__detect__dml",
            "receipt": f"reports/gfx1151/{a.dml_run_id}/receipts/receipt_yolo26n__detect__dml.json",
            "receipt_sha256": sha256(receipt_path),
            "manifest_sha256": sha256(run_dir / "manifest.sha256"),
            "matrix_exit": matrix_exit,
            "pipeline_exit": pipeline_exit,
            "contract_sha256": contract.get("contract_sha256"),
            "contract_git_head": prov.get("git_head_sha"),
        },
        "quick_canary_preflight": canary,
        "prior_attempt": prior,
        "extended_tier_a_matrix": tier,
        "checks": checks,
        "all_ok": all_ok,
        "verdict": "DML_PATH_HEALTHY" if all_ok else "DML_REGRESSION_DETECTED",
        "evidence_summary": {
            "providers_requested": session.get("requested_providers"),
            "provider_node_counts": counts,
            "graph_node_total": session.get("graph_node_total"),
            "fallback_signals": {"stdout": session.get("stdout_fallback_signals"),
                                 "stderr": session.get("stderr_fallback_signals")},
            "device_binding_state": binding.get("state"),
            "numeric_parity": verdicts.get("numeric_parity"),
            "task_parity": verdicts.get("task_parity"),
            "backend_routing": verdicts.get("backend_routing"),
            "note": ("backend_routing BACKEND_UNVERIFIED and benchmark NOT_MEASURED are the "
                     "documented limits of a fused single-node DML profile on this host; they "
                     "are not regressions"),
        },
        "scope_note": ("one Tier-A case (YOLO26n detect) was re-verified end-to-end in this "
                       "mission; the historical 9/9 Tier-A DML matrix is referenced, not "
                       "re-claimed"),
    }
    out = REPO / a.out
    out.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    for c in checks:
        print(f"{'OK  ' if c['ok'] else 'FAIL'} {c['check']}")
    print(json.dumps({"out": a.out, "verdict": doc["verdict"]}))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
