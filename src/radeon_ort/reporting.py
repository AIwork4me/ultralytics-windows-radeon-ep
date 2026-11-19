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
    """Composite matrix, ONE row per checkpoint (per-EP receipts aggregated).

    gfx1100/gfx1201 are NOT_RUN until that hardware evidences runs.
    """
    by_ckpt: dict[str, list[dict]] = {}
    for rc in receipts:
        by_ckpt.setdefault(rc["model"].get("checkpoint"), []).append(rc)
    rows = []
    for ckpt, rs in sorted(by_ckpt.items()):
        m = rs[0]["model"]
        def joined(ep: str) -> str:
            vals = sorted({r["status"][ep] for r in rs
                           if r.get("host", {}).get("gfx") == "gfx1151"})
            return " | ".join(vals) if vals else "NOT_RUN"
        rows.append({
            "model": m.get("family"), "task": m.get("task"),
            "variant": ckpt,
            "gfx1151_AMDGPU": joined("amdgpu"),
            "gfx1151_DML": joined("dml"),
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
    """Per-EP coverage against the catalog. NOT_RUN is always in the denominator."""
    if isinstance(catalog_entries, dict):
        entries = list(catalog_entries.values())
    else:
        entries = list(catalog_entries or [])
    eligible = [e for e in entries if e.get("eligibility") == "eligible"]
    by_ckpt: dict[str, list[dict]] = {}
    for rc in receipts:
        by_ckpt.setdefault(rc["model"]["checkpoint"], []).append(rc)

    def ep_status(ckpt: str, ep: str) -> str | None:
        rs = by_ckpt.get(ckpt, [])
        if not rs:
            return None
        # Each receipt is per-EP: the other EP is NOT_RUN in it. Only the active
        # side of a receipt speaks for that EP.
        vals = {r["status"][ep] for r in rs if r["status"][ep] != "NOT_RUN"}
        if not vals:
            return "NOT_RUN"
        return next(iter(vals)) if len(vals) == 1 else "MIXED"

    counts = {"amdgpu": {}, "dml": {}}
    for ep in ("amdgpu", "dml"):
        for e in eligible:
            s = ep_status(e.get("checkpoint"), ep)
            key = s or "NOT_RUN"
            counts[ep][key] = counts[ep].get(key, 0) + 1

    def n_pass(ep: str, prefix: str) -> int:
        return sum(v for k, v in counts[ep].items() if k.startswith(prefix))

    attempted = [e for e in eligible
                 if ep_status(e.get("checkpoint"), "amdgpu") not in (None, "NOT_RUN")
                 and ep_status(e.get("checkpoint"), "dml") not in (None, "NOT_RUN")]
    full_pass = [e for e in eligible
                 if ep_status(e.get("checkpoint"), "amdgpu") == "PASS_GPU_EXECUTED_FULL"
                 and ep_status(e.get("checkpoint"), "dml") == "PASS_GPU_EXECUTED_FULL"]
    covered = set(by_ckpt)
    not_run = [e.get("checkpoint", "?") for e in entries if e.get("checkpoint") not in covered]
    return {
        "catalog_entries": len(entries),
        "eligible_entries": len(eligible),
        "target_cases_both_eps": len(eligible) * 2,
        "receipts": len(receipts),
        "covered_checkpoints": len(covered),
        "eligible_attempted_both_eps": len(attempted),
        "eligible_full_pass_both_eps": len(full_pass),
        "not_run_count": len(not_run),
        "not_run_sample": not_run[:25],
        "status_histogram": dict(sorted(
            __import__("collections").Counter(
                f"amdgpu={r['status']['amdgpu']}|dml={r['status']['dml']}"
                for r in receipts).items())),
        "amdgpu_status_counts": counts["amdgpu"],
        "dml_status_counts": counts["dml"],
        "amdgpu_pass_full": n_pass("amdgpu", "PASS_GPU_EXECUTED_FULL"),
        "amdgpu_pass_partial": n_pass("amdgpu", "PASS_GPU_EXECUTED_PARTIAL"),
        "amdgpu_fail": sum(v for k, v in counts["amdgpu"].items() if k.startswith("FAIL")),
        "amdgpu_blocked": sum(v for k, v in counts["amdgpu"].items() if k.startswith("BLOCKED")),
        "amdgpu_not_run": counts["amdgpu"].get("NOT_RUN", 0),
        "dml_pass_full": n_pass("dml", "PASS_GPU_EXECUTED_FULL"),
        "dml_pass_partial": n_pass("dml", "PASS_GPU_EXECUTED_PARTIAL"),
        "dml_fail": sum(v for k, v in counts["dml"].items() if k.startswith("FAIL")),
        "dml_blocked": sum(v for k, v in counts["dml"].items() if k.startswith("BLOCKED")),
        "dml_not_run": counts["dml"].get("NOT_RUN", 0),
        "full_coverage_claim_allowed": len(attempted) == len(eligible) and len(eligible) > 0,
        "physical_gpu_full_pass_allowed": len(full_pass) == len(eligible) and len(eligible) > 0,
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
