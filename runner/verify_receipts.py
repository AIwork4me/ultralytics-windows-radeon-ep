"""python -m runner.verify_receipts --run-dir reports/gfx1151/<run_id>

Strict validation of every receipt: schema, derived-status recomputation, forged
label detection, manifest integrity, redaction scan. Non-zero exit on any problem.

v2 receipts additionally cross-check declared numeric/task thresholds against
configs/thresholds.yaml (predeclared values only — no post-hoc tuning).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from radeon_ort import redaction  # noqa: E402
from radeon_ort.manifest import verify_manifest  # noqa: E402
from radeon_ort.receipts import verify_receipt_file  # noqa: E402

YAML_TASK_KEY = {"detect": "detection", "segment": "segmentation", "semantic": "semantic",
                 "depth": "depth", "classify": "classify", "pose": "pose", "obb": "obb"}


def _expected_thresholds(receipt: dict, thresholds: dict | None) -> dict | None:
    if not thresholds or int(receipt.get("schema_version", 1)) < 2:
        return None
    out = {
        "atol": thresholds.get("numeric_parity", {}).get("fp32", {}).get("atol"),
        "rtol": thresholds.get("numeric_parity", {}).get("fp32", {}).get("rtol"),
    }
    tp = receipt.get("task_parity") or {}
    task = tp.get("task")
    if task:
        key = YAML_TASK_KEY.get(task, task)
        out["task"] = thresholds.get("task_oracle", {}).get(key, {})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    a = ap.parse_args()
    run_dir = Path(a.run_dir)
    problems_all: list[str] = []

    thresholds = None
    th_path = Path(__file__).resolve().parents[1] / "configs" / "thresholds.yaml"
    if th_path.is_file():
        try:
            import yaml
            thresholds = yaml.safe_load(th_path.read_text(encoding="utf-8"))
        except ImportError:
            print("[verify] pyyaml absent; threshold cross-check skipped")

    receipts = sorted(run_dir.glob("**/receipt_*.json"))
    print(f"[verify] {len(receipts)} receipt(s) under {run_dir}")
    for rp in receipts:
        try:
            v = verify_receipt_file(rp, expected_thresholds=_expected_thresholds(
                json.loads(rp.read_text(encoding="utf-8")), thresholds))
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {rp.name}: structural error {e}")
            problems_all.append(f"{rp.name}: structural {e}")
            continue
        if v["ok"]:
            extra = ""
            if v.get("interpretations"):
                extra = "  NOTE: " + "; ".join(v["interpretations"])
            print(f"OK   {rp.name} derived={v['derived'].get('amdgpu')}|{v['derived'].get('dml')}{extra}")
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
