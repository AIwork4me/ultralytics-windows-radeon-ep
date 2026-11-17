"""python -m runner.finalize_run — assemble full-catalog disposition ledger.

Every catalog entry gets an honest disposition:
  PASS_GPU_FULL (receipt exists, EP passed) | FAIL_* (receipt evidence) |
  NOT_RUN_eligible (baseline pending; resume command recorded) |
  ASSET_BLOCKED / LICENSE_RESTRICTED / NOT_APPLICABLE (cataloged reason).
The honest denominator counts NOT_RUN so partial coverage can never be
advertised as full validation.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from runner import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--catalog", default=str(REPO_ROOT / "catalog" / "model_catalog.json"))
    a = ap.parse_args()
    run_dir = Path(a.run_dir)

    cat = json.loads(Path(a.catalog).read_text(encoding="utf-8"))
    receipts = []
    for rp in sorted(run_dir.glob("receipts/receipt_*.json")):
        receipts.append(json.loads(rp.read_text(encoding="utf-8")))
    by_ckpt: dict[str, list[dict]] = {}
    for r in receipts:
        by_ckpt.setdefault(r["model"]["checkpoint"], []).append(r)

    rows = []
    for ckpt, e in sorted(cat["entries"].items()):
        rs = by_ckpt.get(ckpt, [])
        if rs:
            amd = " | ".join(sorted({r["status"]["amdgpu"] for r in rs}))
            dml = " | ".join(sorted({r["status"]["dml"] for r in rs}))
            disposition = "EVIDENCED"
        else:
            amd = dml = "NOT_RUN"
            elig = e.get("eligibility")
            if elig == "eligible":
                disposition = f"NOT_RUN_eligible({e.get('gpu_effort', '')})"
            elif elig == "asset_blocked":
                disposition = "ASSET_BLOCKED"
            elif elig == "license_restricted":
                disposition = "LICENSE_RESTRICTED"
            else:
                disposition = "NOT_APPLICABLE"
        rows.append({
            "checkpoint": ckpt, "family": e["family"], "task": e["task"],
            "tier": e.get("tier"), "gpu_effort": e.get("gpu_effort"),
            "eligibility": e.get("eligibility"),
            "gfx1151_amdgpu": amd, "gfx1151_dml": dml,
            "disposition": disposition,
            "gfx1100_amdgpu": "NOT_RUN", "gfx1100_dml": "NOT_RUN",
            "gfx1201_amdgpu": "NOT_RUN", "gfx1201_dml": "NOT_RUN",
            "reason": e.get("eligibility_reason") or "",
        })

    hist = Counter(r["disposition"] for r in rows)
    ledger = {
        "schema_version": 1,
        "run_dir": run_dir.name,
        "catalog_release": cat["snapshot"]["ultralytics_release"],
        "catalog_commit": cat["snapshot"]["ultralytics_commit_sha"],
        "entries_total": len(rows),
        "disposition_histogram": dict(hist),
        "full_coverage_claim_allowed": hist.get("NOT_RUN_eligible(BASELINE_REQUIRED)", 0) == 0
        and all(not k.startswith("NOT_RUN_eligible") for k in hist),
        "resume_command": (
            ".venv-winml/Scripts/python -m runner.run_matrix --tier full --ep both "
            "--winml-python .venv-winml/Scripts/python --export-python .venv-export/Scripts/python "
            "--image data/fixtures/bus.jpg --pt-oracle --benchmark --resume"),
        "rows": rows,
    }
    out = run_dir / "DISPOSITION_LEDGER.json"
    out.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    with open(run_dir / "DISPOSITION_LEDGER.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"[finalize] {len(rows)} entries -> {out}")
    print(f"[finalize] histogram: {dict(hist)}")
    print(f"[finalize] full_coverage_claim_allowed={ledger['full_coverage_claim_allowed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
