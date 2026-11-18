"""R1 freeze tooling: matrices, device bindings, provenance, manifest.

Run (winml venv, from repo root):
  PYTHONPATH=src python -I tools/r1_finalize.py reports/gfx1151/R1_20261117T215114Z

Generates under the run dir:
  R1_CASE_MATRIX.csv        one row per receipt (exec/parity/task per EP)
  R1_TASK_PARITY_MATRIX.csv one row per model x task with per-EP task metrics
  R1_LEDGER.json            machine-readable summary incl denominators
  R1_DEVICE_BINDINGS.json   per-receipt selected ep-device attestations
  R1_ADVERSARIAL_CI.md      test/CI summary skeleton (numbers filled by caller)
  manifest.sha256           evidence manifest (deterministic)
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from radeon_ort.manifest import write_manifest  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def main(run_dir_s: str) -> int:
    run_dir = Path(run_dir_s)
    receipts = sorted((run_dir / "receipts").glob("receipt_*.json"))
    rows, task_rows, bindings = [], [], {}
    per_ep_exec = {"amdgpu": {}, "dml": {}}
    for rp in receipts:
        r = json.loads(rp.read_text(encoding="utf-8"))
        ep = "amdgpu" if r["status"]["amdgpu"] != "NOT_RUN" else "dml"
        model = r["model"]["checkpoint"]
        task = r["model"]["task"]
        tp = r.get("task_parity") or {}
        m = tp.get("metrics", {})
        exec_s = r["status"][ep]
        per_ep_exec[ep].setdefault(exec_s, 0)
        per_ep_exec[ep][exec_s] += 1
        rows.append({
            "case": r["case_id"], "model": model, "task": task, "ep": ep.upper(),
            "execution": exec_s,
            "numeric_parity": r.get("verdicts", {}).get(ep, {}).get("numeric_parity", ""),
            "task_oracle": r["status"]["task_oracle"],
            "binding": (r.get("device_binding") or {}).get("state", ""),
            "attribution": (r.get("device_binding") or {}).get("attribution", ""),
            "input_id": r["input"]["input_id"][:16],
            "onnx_sha256": (r["model"].get("onnx_sha256") or "")[:16],
            "worker_rc": r["worker"]["exit_code"],
            "rca_ids": ";".join(r.get("rca_issue_ids", [])),
            "errors": len(r.get("errors", [])),
        })
        if ep == "amdgpu":  # one task row per case, EP columns
            task_rows.append({
                "model": model, "task": task,
                "image_sha256": (r["input"].get("image_sha256") or "")[:16],
                "amdgpu_exec": exec_s,
                "amdgpu_task": r["status"]["task_oracle"],
                "dml_task": "",  # filled below from the DML receipt
                "ref_entities": m.get("ref_entities", ""),
                "matched": m.get("matched", ""), "precision": m.get("precision", ""),
                "recall": m.get("recall", ""), "iou_mean": m.get("iou_mean", ""),
                "mask_iou_mean": m.get("mask_iou_mean", ""),
                "oks_mean": m.get("oks_mean", ""),
                "rotated_iou_mean": m.get("rotated_iou_mean", ""),
                "angle_absdiff_mean_deg": m.get("angle_absdiff_mean_deg", ""),
                "pixel_agreement": m.get("pixel_agreement", ""),
                "miou": m.get("miou", ""),
                "mae": m.get("mae", ""), "rel_mae": m.get("rel_mae", ""),
                "top1_match": m.get("top1_match", ""),
                "score_drift": m.get("score_drift", ""),
                "negative_control_discriminating":
                    bool((tp.get("negative_control") or {}).get("discriminating")),
            })
        db = r.get("device_binding") or {}
        bindings[r["case_id"]] = {
            "ep": db.get("ep"), "state": db.get("state"),
            "selected_candidate_index": db.get("selected_candidate_index"),
            "candidates_tried": db.get("candidates_tried"),
            "probe_amdgpu_nodes": (db.get("probe") or {}).get("amdgpu_nodes"),
            "final_counts": (db.get("final_profile") or {}).get("provider_node_counts"),
            "attribution": db.get("attribution"),
            "dxgi_corroboration": db.get("dxgi_corroboration"),
            "compiled_twice": db.get("model_compiled_twice"),
        }
    # fill DML task verdicts into task rows
    dml_task = {}
    for rp in receipts:
        r = json.loads(rp.read_text(encoding="utf-8"))
        if r["status"]["dml"] != "NOT_RUN":
            dml_task[(r["model"]["checkpoint"], r["model"]["task"])] = r["status"]["task_oracle"]
    for tr in task_rows:
        tr["dml_task"] = dml_task.get((tr["model"], tr["task"]), "")

    def write_csv(path: Path, rows: list[dict]):
        if not rows:
            return
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    write_csv(run_dir / "R1_CASE_MATRIX.csv", rows)
    write_csv(run_dir / "R1_TASK_PARITY_MATRIX.csv", task_rows)
    (run_dir / "R1_DEVICE_BINDINGS.json").write_text(
        json.dumps(bindings, indent=2, sort_keys=True), encoding="utf-8")

    n_amd = sum(1 for r in rows if r["ep"] == "AMDGPU")
    n_dml = sum(1 for r in rows if r["ep"] == "DML")
    ledger = {
        "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "receipts": len(receipts),
        "gpu_cases": {"amdgpu": n_amd, "dml": n_dml, "total": n_amd + n_dml},
        "exec_counts": per_ep_exec,
        "task_pass": sum(1 for r in rows if r["task_oracle"] == "PASS_TASK"),
        "task_blocked": sum(1 for r in rows if r["task_oracle"] in ("TASK_ORACLE_BLOCKED", "TASK_PARITY_NOT_IMPLEMENTED")),
        "task_fail": sum(1 for r in rows if r["task_oracle"] == "FAIL_TASK"),
        "negative_controls_discriminating": sum(
            1 for tr in task_rows if tr["negative_control_discriminating"]),
        "catalog_snapshot": {"total": 142, "EVIDENCED": 16,
                             "BASELINE_REQUIRED_NOT_RUN": 26,
                             "other_gated": 100},
        "errata": "see ERRATA.md (4 entries, all preserved in git history)",
    }
    (run_dir / "R1_LEDGER.json").write_text(json.dumps(ledger, indent=2), encoding="utf-8")

    # redaction scan over all text evidence (policy: 'rocm' is a known-public
    # username per redaction.KNOWN_PUBLIC_USERNAMES; binary tensors skipped)
    from radeon_ort import redaction
    scan: dict = {"files_scanned": 0, "violations": {}}
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file() or p.suffix.lower() in {".npz", ".npy", ".png", ".jpg", ".sha256"}:
            continue
        if p.name == "redaction_scan.json":
            continue
        leaks = redaction.scan_file(p)
        scan["files_scanned"] += 1
        if leaks:
            scan["violations"][p.relative_to(run_dir).as_posix()] = leaks
    scan["ok"] = not scan["violations"]
    (run_dir / "redaction_scan.json").write_text(json.dumps(scan, indent=2), encoding="utf-8")

    mp = write_manifest(run_dir, run_dir.name, "tools/r1_finalize.py")
    print(json.dumps(ledger, indent=1))
    print(f"manifest -> {mp}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else
                  str(REPO / "reports" / "gfx1151" / "R1_20261117T215114Z")))
