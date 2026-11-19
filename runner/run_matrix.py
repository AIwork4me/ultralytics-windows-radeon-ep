"""python -m runner.run_matrix — v2 case orchestrator (fail-closed).

This CLI is now a thin, bounded coordinator over `runner.r1_pipeline`:

  * real-image, persisted, hash-verified input bundles (no `hash()` seeds),
  * CPU ORT by-output-name reference,
  * AMDGPU first / DML independently,
  * vendor postprocess + PT task-parity metrics and predeclared thresholds,
  * immutable v2 receipts and honest BLOCKED_*/NOT_RUN dispositions.

The historical v1 implementation (random tensors, PT_APP smoke mislabeled as
task parity, always-zero exit code) was audited as P0-broken and is replaced.
Exit code: 0 only when every requested (model, EP) case is PASS_GPU_*; 2 when a
case is blocked/not run; 1 on harness failure or any FAIL_* status.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from radeon_ort.receipts import utc_now_iso
from runner import REPO_ROOT


def load_catalog(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def make_case_id(entry: dict, ep: str) -> str:
    return f"{Path(entry['checkpoint']).stem}__{entry['task']}__{ep}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="gfx1151")
    ap.add_argument("--tier", choices=["core", "full"], default="core")
    ap.add_argument("--ep", choices=["amdgpu", "dml", "both"], default="both")
    ap.add_argument("--catalog", default=str(REPO_ROOT / "catalog" / "model_catalog.json"))
    ap.add_argument("--models-root", default=str(REPO_ROOT / "models"))
    ap.add_argument("--reports-root", default=str(REPO_ROOT / "reports"))
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--max-minutes-per-case", type=float, default=20)
    ap.add_argument("--model", default=None, help="filter by substring")
    ap.add_argument("--task", default=None)
    ap.add_argument("--resume", action="store_true", help="reuse existing run dir & skip finished receipts")
    ap.add_argument("--winml-python", default=None)
    ap.add_argument("--export-python", default=None)
    ap.add_argument("--image", default=None)
    ap.add_argument("--obb-image", default=None)
    ap.add_argument("--benchmark", action="store_true")
    ap.add_argument("--pt-oracle", action="store_true", help="accepted for CLI compatibility; v2 always runs the PT task oracle")
    ap.add_argument("--with-negative", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    run_id = a.run_id or ("RUN_" + utc_now_iso().replace("-", "").replace(":", "").replace("T", "_").replace("Z", ""))
    out_dir = Path(a.reports_root) / a.host / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    cat = load_catalog(Path(a.catalog))
    entries = list(cat["entries"].values())
    if a.tier == "core":
        entries = [e for e in entries if e.get("tier") == "A"]
    if a.model:
        entries = [e for e in entries if a.model.lower() in e["checkpoint"].lower()]
    if a.task:
        entries = [e for e in entries if e["task"] == a.task]
    entries = [e for e in entries if e.get("eligibility") == "eligible"]
    if a.limit:
        entries = entries[: a.limit]
    if not entries:
        print("[matrix] FAIL: no catalog entry matched (refusing a vacuous success)")
        return 2

    eps = ["amdgpu", "dml"] if a.ep == "both" else [a.ep]
    winml_python = a.winml_python or sys.executable
    export_python = a.export_python or sys.executable
    models = [e["checkpoint"] for e in entries]
    print(f"[matrix] run_id={run_id} models={len(models)} eps={eps} out={out_dir}")

    cmd = [winml_python, "-m", "runner.r1_pipeline",
           "--run-dir", str(out_dir),
           "--catalog", str(Path(a.catalog)),
           "--models", ",".join(models),
           "--eps", ",".join(eps),
           "--winml-python", winml_python,
           "--export-python", export_python,
           "--case-timeout-s", str(max(60, int(a.max_minutes_per_case * 60)))]
    if a.image:
        cmd += ["--image", a.image]
    if a.obb_image:
        cmd += ["--obb-image", a.obb_image]
    if a.benchmark:
        cmd += ["--benchmark"]
    if a.with_negative:
        cmd += ["--with-negative"]
    print("[matrix] exec: " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=str(REPO_ROOT))
    if r.returncode != 0:
        print(f"[matrix] FAIL: r1_pipeline exited rc={r.returncode}")
        return 1

    # ----- independent end-of-run audit of the receipts (never trust rc alone)
    receipts = {}
    for rp in sorted((out_dir / "receipts").glob("receipt_*.json")):
        try:
            rec = json.loads(rp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        receipts[rec.get("case_id")] = rec

    counts = {"PASS_FULL": 0, "PASS_PARTIAL": 0, "FAIL": 0, "BLOCKED": 0, "NOT_RUN": 0}
    problems: list[str] = []
    for entry in entries:
        for ep in eps:
            cid = make_case_id(entry, ep)
            rec = receipts.get(cid)
            if rec is None:
                counts["NOT_RUN"] += 1
                problems.append(f"{cid}: no receipt")
                continue
            status = rec.get("status", {}).get(ep, "NOT_RUN")
            if status == "PASS_GPU_EXECUTED_FULL":
                counts["PASS_FULL"] += 1
            elif status == "PASS_GPU_EXECUTED_PARTIAL":
                counts["PASS_PARTIAL"] += 1
            elif status.startswith("BLOCKED_"):
                counts["BLOCKED"] += 1
            elif status.startswith("FAIL_"):
                counts["FAIL"] += 1
                problems.append(f"{cid}: {status}")
            else:
                counts["NOT_RUN"] += 1
                problems.append(f"{cid}: {status}")
    print(f"[matrix] audit: {counts}")
    for p in problems[:50]:
        print(f"[matrix]   {p}")
    expected = len(entries) * len(eps)
    passed = counts["PASS_FULL"] + counts["PASS_PARTIAL"]
    if counts["FAIL"]:
        return 1
    # BLOCKED / NOT_RUN cases are not success: a run with any of them exits 2.
    return 0 if passed == expected else 2


if __name__ == "__main__":
    sys.exit(main())
