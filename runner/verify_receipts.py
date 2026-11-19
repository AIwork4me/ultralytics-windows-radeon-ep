"""python -m runner.verify_receipts --run-dir reports/<gfx>/<run_id>

Strict validation of every receipt: schema, derived-status recomputation, forged
label detection, run-contract match, manifest integrity, redaction scan. Non-zero
exit on any problem.

v2 receipts additionally cross-check declared numeric/task thresholds against the
run's `thresholds_snapshot.json` (predeclared values only — no post-hoc tuning).
A run that carries `RUN_CONTRACT.json` is contract-verified: code SHA, threshold
snapshot, model/input hashes, EP and device identity must match every receipt.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from radeon_ort import contract as contract_mod  # noqa: E402
from radeon_ort import redaction  # noqa: E402
from radeon_ort.manifest import verify_manifest  # noqa: E402
from radeon_ort.receipts import verify_receipt_file  # noqa: E402


def _load_thresholds(run_dir: Path) -> tuple[dict | None, str]:
    snap = contract_mod.thresholds_snapshot_path(run_dir)
    if snap.is_file():
        try:
            return json.loads(snap.read_text(encoding="utf-8")), "thresholds_snapshot.json"
        except json.JSONDecodeError as e:
            print(f"[verify] FAIL: invalid threshold snapshot: {e}")
            return None, "invalid-snapshot"
    th_path = Path(__file__).resolve().parents[1] / "configs" / "thresholds.yaml"
    if th_path.is_file():
        try:
            import yaml
            return yaml.safe_load(th_path.read_text(encoding="utf-8")), "configs/thresholds.yaml"
        except ImportError:
            print("[verify] pyyaml absent; threshold cross-check skipped")
    return None, "none"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    a = ap.parse_args()
    run_dir = Path(a.run_dir)
    problems_all: list[str] = []
    if not run_dir.is_dir():
        print(f"[verify] FAIL: run dir does not exist: {run_dir}")
        return 1

    thresholds, th_source = _load_thresholds(run_dir)
    print(f"[verify] thresholds source: {th_source}")
    if thresholds is None:
        problems_all.append("no threshold snapshot/config available")

    run_contract = None
    cpath = run_dir / contract_mod.RUN_CONTRACT_NAME
    if cpath.is_file():
        try:
            run_contract = contract_mod.load_run_contract(run_dir)
        except contract_mod.ContractError as e:
            print(f"[verify] FAIL: {e}")
            problems_all.append(f"run contract: {e}")
        else:
            if thresholds is not None and (
                    contract_mod.thresholds_sha256(thresholds)
                    != run_contract.get("thresholds_sha256")):
                problems_all.append(
                    "threshold snapshot SHA != run contract thresholds_sha256")
            if contract_mod.canonical_json(thresholds or {}) != contract_mod.canonical_json(
                    run_contract.get("thresholds") or {}):
                problems_all.append(
                    "threshold snapshot body != run contract thresholds body")
            if run_contract.get("run_id") != run_dir.name:
                problems_all.append("run contract run_id != directory name")
            if (run_contract.get("code") or {}).get("worktree_dirty"):
                problems_all.append(
                    "run contract was created from a dirty harness worktree; "
                    "it cannot attest a GPU PASS")
    else:
        print("[verify] NOTE: no RUN_CONTRACT.json (legacy run); contract checks skipped")

    receipts = sorted(run_dir.glob("**/receipt_*.json"))
    print(f"[verify] {len(receipts)} receipt(s) under {run_dir}")
    if not receipts:
        # A vacuous run must never print ALL_OK: zero receipts cannot attest
        # anything, and this exact fail-open behaviour was a P0 audit finding.
        print("[verify] FAIL: zero receipts found (empty evidence cannot certify a run)")
        problems_all.append("zero receipts: empty evidence cannot certify a run")
    for rp in receipts:
        try:
            rec = json.loads(rp.read_text(encoding="utf-8"))
            if rec.get("run_id") != run_dir.name:
                problems_all.append(
                    f"{rp.name}: run_id={rec.get('run_id')!r} != directory {run_dir.name!r}")
            expected = contract_mod.expected_thresholds_for_receipt(rec, thresholds or {})
            v = verify_receipt_file(rp, expected_thresholds=expected)
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
        if run_contract is not None:
            cps = contract_mod.validate_receipt_contract(rec, run_contract)
            for p in cps:
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
        # Missing manifest is a fail-closed condition for committed evidence.
        print("[verify] FAIL: manifest.sha256 absent (run finalize/manifest first)")
        problems_all.append("manifest.sha256 absent")

    for rp in receipts:
        leaks = redaction.scan_file(rp)
        for l in leaks:
            print(f"FAIL redaction {rp.name}: {l}")
            problems_all.append(f"redaction {rp.name}: {l}")

    print(f"[verify] verdict: {'ALL_OK' if not problems_all else f'{len(problems_all)} PROBLEM(S)'}")
    return 0 if not problems_all else 1


if __name__ == "__main__":
    sys.exit(main())
