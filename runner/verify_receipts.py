"""python -m runner.verify_receipts --reports-root reports/gfx1151/<run_id>

Strict validation of every receipt: schema, derived-status recomputation, forged
label detection, manifest integrity, redaction scan. Non-zero exit on any problem.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from radeon_ort import redaction
from radeon_ort.manifest import verify_manifest
from radeon_ort.receipts import verify_receipt_file


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    a = ap.parse_args()
    run_dir = Path(a.run_dir)
    problems_all: list[str] = []

    receipts = sorted(run_dir.glob("**/receipt_*.json"))
    print(f"[verify] {len(receipts)} receipt(s) under {run_dir}")
    for rp in receipts:
        try:
            v = verify_receipt_file(rp)
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {rp.name}: structural error {e}")
            problems_all.append(f"{rp.name}: structural {e}")
            continue
        if v["ok"]:
            print(f"OK   {rp.name} derived={v['derived']['amdgpu']}|{v['derived']['dml']}")
        else:
            for p in v["problems"]:
                print(f"FAIL {rp.name}: {p}")
                problems_all.append(f"{rp.name}: {p}")

    mpath = run_dir / "manifest.sha256"
    if mpath.is_file():
        mv = verify_manifest(run_dir, mpath)
        if mv["ok"]:
            print(f"[verify] manifest OK ({len(json.loads(mpath.read_text(encoding='utf-8'))['entries'])} entries)")
        else:
            for kind in ("mismatches", "missing", "unlisted"):
                for x in mv[kind]:
                    print(f"FAIL manifest {kind}: {x}")
                    problems_all.append(f"manifest {kind}: {x}")
    else:
        print("[verify] manifest.sha256 absent (run finalize first)")

    for rp in receipts:
        leaks = redaction.scan_file(rp)
        for l in leaks:
            print(f"FAIL redaction {rp.name}: {l}")
            problems_all.append(f"redaction {rp.name}: {l}")

    print(f"[verify] verdict: {'ALL_OK' if not problems_all else f'{len(problems_all)} PROBLEM(S)'}")
    return 0 if not problems_all else 1


if __name__ == "__main__":
    sys.exit(main())
