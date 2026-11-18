"""python -m runner.build_compatibility_matrix --run-dir <dir> — composite CSV.

gfx1100/gfx1201 columns are always NOT_RUN unless receipts exist with that host gfx.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from radeon_ort.reporting import build_compatibility_matrix, coverage_summary, write_ledger


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--catalog", default=None)
    a = ap.parse_args()
    run_dir = Path(a.run_dir)
    receipts = []
    for rp in sorted(run_dir.glob("**/receipt_*.json")):
        receipts.append(json.loads(rp.read_text(encoding="utf-8")))
    out = build_compatibility_matrix(receipts, run_dir / "compatibility_matrix.csv")
    print(f"[matrix] wrote {out} ({len(receipts)} rows)")

    cat = []
    if a.catalog and Path(a.catalog).is_file():
        cat = json.loads(Path(a.catalog).read_text(encoding="utf-8")).get("entries", [])
    cov = coverage_summary(receipts, cat)
    (run_dir / "COVERAGE_SUMMARY.json").write_text(json.dumps(cov, indent=2), encoding="utf-8")
    write_ledger(receipts, run_dir / "CASE_LEDGER.json")
    print(f"[matrix] coverage: {cov['covered_checkpoints']}/{cov['catalog_entries']} "
          f"(full_coverage_claim_allowed={cov['full_coverage_claim_allowed']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
