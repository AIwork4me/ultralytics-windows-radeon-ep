"""python -m runner.finalize_run — assemble the full-catalog disposition ledger.

Every catalog entry gets an honest disposition derived from receipt evidence:
  PASS_GPU_FULL | PASS_GPU_PARTIAL | FAIL_EVIDENCED | BLOCKED_EVIDENCED |
  NOT_RUN_eligible (baseline pending; resume command recorded) |
  ASSET_BLOCKED / LICENSE_RESTRICTED / NOT_APPLICABLE (cataloged reason).

The denominator always counts NOT_RUN. `full_coverage_claim_allowed` only means
every eligible checkpoint was *attempted* for both EPs; `physical_gpu_full_pass_allowed`
is the much stronger claim that both EPs actually passed. They are never merged.
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

STATUS_MAP = {
    "PASS_GPU_EXECUTED_FULL": "PASS_GPU_FULL",
    "PASS_GPU_EXECUTED_PARTIAL": "PASS_GPU_PARTIAL",
    "FAIL_NO_GPU_COMPUTE": "FAIL",
    "FAIL_GPU_PARITY": "FAIL",
    "FAIL_RUNTIME": "FAIL",
    "FAIL_TASK": "FAIL",
    "FAIL_EXPORT": "FAIL",
    "BLOCKED_EP": "BLOCKED",
    "BLOCKED_IDENTITY": "BLOCKED",
    "BLOCKED_RESOURCE": "BLOCKED",
    "NOT_RUN": "NOT_RUN",
}


def _map_status(status: str) -> str:
    if status.startswith("FAIL"):
        return "FAIL"
    if status.startswith("BLOCKED"):
        return "BLOCKED"
    return STATUS_MAP.get(status, status)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--host", default=None,
                    help="gfx target; read from RUN_CONTRACT.json / report path when omitted")
    ap.add_argument("--catalog", default=str(REPO_ROOT / "catalog" / "model_catalog.json"))
    a = ap.parse_args()
    run_dir = Path(a.run_dir)
    if not run_dir.is_dir():
        print(f"[finalize] FAIL: run dir missing: {run_dir}")
        return 1

    host = a.host
    gfx_hosts = ("gfx1100", "gfx1151", "gfx1201")

    cat = json.loads(Path(a.catalog).read_text(encoding="utf-8"))
    receipts = []
    for rp in sorted(run_dir.glob("receipts/receipt_*.json")):
        receipts.append(json.loads(rp.read_text(encoding="utf-8")))
    if not receipts:
        print("[finalize] FAIL: zero receipts (refusing to invent a disposition ledger)")
        return 1

    if host is None:
        cpath = run_dir / "RUN_CONTRACT.json"
        if cpath.is_file():
            try:
                host = json.loads(cpath.read_text(encoding="utf-8")).get("gfx")
            except json.JSONDecodeError:
                host = None
        if not host and run_dir.parent.name in gfx_hosts:
            host = run_dir.parent.name
        if not host:
            print("[finalize] FAIL: cannot determine gfx host; pass --host")
            return 1
    if host not in gfx_hosts:
        print(f"[finalize] FAIL: unsupported host={host!r}")
        return 1
    by_ckpt: dict[str, list[dict]] = {}
    for r in receipts:
        by_ckpt.setdefault(r["model"]["checkpoint"], []).append(r)

    rows = []
    for ckpt, e in sorted(cat["entries"].items()):
        rs = by_ckpt.get(ckpt, [])
        per_ep = {}
        for ep in ("amdgpu", "dml"):
            statuses = sorted({r["status"][ep] for r in rs})
            per_ep[ep] = " | ".join(statuses) if statuses else "NOT_RUN"
        mapped = {ep: [_map_status(s) for s in per_ep[ep].split(" | ")] for ep in per_ep}
        if rs:
            if all(v == ["PASS_GPU_FULL"] for v in mapped.values()):
                disposition = "PASS_GPU_FULL"
            elif any("PASS_GPU_FULL" in v or "PASS_GPU_PARTIAL" in v for v in mapped.values()):
                if any("PASS_GPU_PARTIAL" in v for v in mapped.values()):
                    disposition = "PASS_GPU_PARTIAL"
                elif any("BLOCKED" in v for v in mapped.values()):
                    disposition = "PASS_GPU_FULL_ONE_EP_OTHER_BLOCKED"
                else:
                    disposition = "PASS_GPU_FULL_ONE_EP_OTHER_NOT_RUN"
            elif any("FAIL" in v for v in mapped.values()):
                disposition = "FAIL_EVIDENCED"
            elif any("BLOCKED" in v for v in mapped.values()):
                disposition = "BLOCKED_EVIDENCED"
            else:
                disposition = "NOT_RUN"
        else:
            elig = e.get("eligibility")
            if elig == "eligible":
                disposition = f"NOT_RUN_eligible({e.get('gpu_effort', '')})"
            elif elig == "asset_blocked":
                disposition = "ASSET_BLOCKED"
            elif elig == "license_restricted":
                disposition = "LICENSE_RESTRICTED"
            else:
                disposition = "NOT_APPLICABLE"
        row = {
            "checkpoint": ckpt, "family": e["family"], "task": e["task"],
            "tier": e.get("tier"), "gpu_effort": e.get("gpu_effort"),
            "eligibility": e.get("eligibility"),
            "disposition": disposition,
            "reason": e.get("eligibility_reason") or "",
        }
        for g in gfx_hosts:
            row[f"{g}_amdgpu"] = per_ep["amdgpu"] if g == host else "NOT_RUN"
            row[f"{g}_dml"] = per_ep["dml"] if g == host else "NOT_RUN"
        rows.append(row)

    hist = Counter(r["disposition"] for r in rows)
    eligible = [r for r in rows if r["eligibility"] == "eligible"]
    attempted_both = [r for r in eligible
                      if r[f"{host}_amdgpu"] != "NOT_RUN"
                      and r[f"{host}_dml"] != "NOT_RUN"]
    pass_both = [r for r in eligible
                 if r["disposition"] == "PASS_GPU_FULL"]
    ledger = {
        "schema_version": 2,
        "run_dir": run_dir.name,
        "catalog_release": cat["snapshot"]["ultralytics_release"],
        "catalog_commit": cat["snapshot"]["ultralytics_commit_sha"],
        "entries_total": len(rows),
        "eligible_total": len(eligible),
        "receipt_count": len(receipts),
        "disposition_histogram": dict(hist),
        "eligible_attempted_both_eps": len(attempted_both),
        "eligible_full_pass_both_eps": len(pass_both),
        "full_coverage_claim_allowed": len(eligible) > 0 and len(attempted_both) == len(eligible),
        "physical_gpu_full_pass_allowed": len(eligible) > 0 and len(pass_both) == len(eligible),
        "resume_command": (
            f".venv-winml/Scripts/python -m runner.resume_matrix --run-id {run_dir.name} "
            f"--host {host} --tier full --ep both "
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
    print(f"[finalize] {len(rows)} entries ({len(eligible)} eligible), {len(receipts)} receipts")
    print(f"[finalize] histogram: {dict(hist)}")
    print(f"[finalize] attempted_both={len(attempted_both)}/{len(eligible)} "
          f"full_pass_both={len(pass_both)}/{len(eligible)}")
    print(f"[finalize] full_coverage_claim_allowed={ledger['full_coverage_claim_allowed']} "
          f"physical_gpu_full_pass_allowed={ledger['physical_gpu_full_pass_allowed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
