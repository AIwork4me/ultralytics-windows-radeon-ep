"""Reporting: compatibility matrix CSV, coverage summary, ledger."""

from __future__ import annotations

import csv
import json
from pathlib import Path

COMPAT_COLUMNS = ["model", "task", "variant",
                  "gfx1151_AMDGPU", "gfx1151_DML",
                  "gfx1100_AMDGPU", "gfx1100_DML",
                  "gfx1201_AMDGPU", "gfx1201_DML"]


def build_compatibility_matrix(receipts: list[dict], out_csv: str | Path) -> Path:
    """Composite matrix. gfx1100/gfx1201 are NOT_RUN until that hardware evidences runs."""
    rows = []
    for rc in receipts:
        m = rc["model"]
        rows.append({
            "model": m.get("family"), "task": m.get("task"),
            "variant": m.get("checkpoint"),
            "gfx1151_AMDGPU": rc["status"]["amdgpu"] if rc["host"].get("gfx") == "gfx1151" else "NOT_RUN",
            "gfx1151_DML": rc["status"]["dml"] if rc["host"].get("gfx") == "gfx1151" else "NOT_RUN",
            "gfx1100_AMDGPU": "NOT_RUN", "gfx1100_DML": "NOT_RUN",
            "gfx1201_AMDGPU": "NOT_RUN", "gfx1201_DML": "NOT_RUN",
        })
    out = Path(out_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COMPAT_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return out


def coverage_summary(receipts: list[dict], catalog_entries) -> dict:
    # catalog_entries may be a list of entries OR a {checkpoint: entry} dict
    if isinstance(catalog_entries, dict):
        entries = list(catalog_entries.values())
    else:
        entries = list(catalog_entries or [])
    covered = {r["case_id"] for r in receipts}
    total = len(entries)
    by_status: dict[str, int] = {}
    for rc in receipts:
        s = rc["status"]
        key = f"amdgpu={s['amdgpu']}|dml={s['dml']}"
        by_status[key] = by_status.get(key, 0) + 1
    # entry-level disposition: model base case covered if any receipt exists for its checkpoint+ep pair
    ckpts_covered = {r["model"]["checkpoint"] for r in receipts}
    not_run = [e.get("checkpoint", "?") for e in entries
               if e.get("checkpoint") not in ckpts_covered]
    return {
        "catalog_entries": total,
        "receipts": len(receipts),
        "covered_checkpoints": len(ckpts_covered),
        "not_run_count": len(not_run),
        "not_run_sample": not_run[:25],
        "status_histogram": by_status,
        "full_coverage_claim_allowed": len(not_run) == 0,
    }


def write_ledger(receipts: list[dict], out_json: str | Path) -> Path:
    ledger = {
        "schema_version": 1,
        "entries": [
            {"case_id": r["case_id"], "run_id": r["run_id"],
             "status": r["status"], "receipt_sha256_note": "see manifest"}
            for r in receipts
        ],
    }
    out = Path(out_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    return out
