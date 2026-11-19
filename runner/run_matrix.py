"""python -m runner.run_matrix — v2 case orchestrator (fail-closed).

This CLI is a thin, bounded coordinator over `runner.r1_pipeline`:

  * real-image, persisted, hash-verified input bundles,
  * CPU ORT by-output-name reference,
  * AMDGPU first / DML independently,
  * vendor postprocess + PT task-parity metrics and predeclared thresholds,
  * immutable v2 receipts and honest BLOCKED_*/NOT_RUN dispositions.

P0-C: the final PASS/exit code is decided by independent strict validation of
every receipt (schema + derived status + run-contract match), NOT by trusting
the receipt's own `status` field. Exit codes:
  0  every requested (model, EP) case strict-validates as PASS_GPU_FULL/PARTIAL
  1  harness failure, forged/stale receipt, contract mismatch, or any FAIL_*
  2  blocked / not-run / missing evidence (no full PASS)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from radeon_ort import contract as contract_mod
from radeon_ort import redaction
from radeon_ort.receipts import utc_now_iso, validate_receipt_strict
from runner import REPO_ROOT


def load_catalog(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def make_case_id(entry: dict, ep: str) -> str:
    return f"{Path(entry['checkpoint']).stem}__{entry['task']}__{ep}"


def _resolve_host(host: str | None, name_needle: str | None) -> dict:
    # Import lazily so the CLI keeps working without Windows-only imports at
    # module import time (CPU CI imports this module in unit tests).
    from runner.r1_pipeline import _host_block
    return _host_block(host, name_needle=name_needle)


def _find_run_dir(reports_root: Path, run_id: str) -> Path | None:
    direct = reports_root / run_id
    if (direct / contract_mod.RUN_CONTRACT_NAME).is_file():
        return direct
    for p in sorted(reports_root.glob(f"*/{run_id}")):
        if (p / contract_mod.RUN_CONTRACT_NAME).is_file():
            return p
    return None


def _redacted_argv(argv: list[str]) -> list[str]:
    out: list[str] = []
    for a in argv:
        try:
            out.append(redaction.redact_path(a) if Path(a).is_absolute() else a)
        except Exception:  # noqa: BLE001 - redaction must never break the run
            out.append("<redacted>")
    return out


def pipeline_classification(rc: int, reasons: list[str]) -> str:
    """First-error classification of a r1_pipeline run (R3-A).

    The stage name is preserved so a caller can tell an external block from a
    real failure without re-reading the whole transcript.
    """
    joined = " | ".join(reasons)
    if "CONTRACT FAIL" in joined:
        return "CONTRACT_REFUSED"
    if "target device not verified" in joined:
        return "DEVICE_UNVERIFIED"
    if "VALIDATION PROBLEMS" in joined:
        return "EVIDENCE_INVALID"
    if "EXIT classification=" in joined:
        return "PIPELINE_REPORTED"
    if rc == 2:
        return "BLOCKED_OR_NOT_RUN"
    if rc != 0:
        return "FAILURE"
    return "OK"


def _run_pipeline(cmd: list[str], log_path: Path) -> tuple[int, list[str]]:
    """Run the producer, stream its output, and keep the first failure lines."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    reasons: list[str] = []
    console_errors: list[str] = []
    proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, bufsize=0)
    assert proc.stdout is not None
    with log_path.open("wb") as log:
        for raw in proc.stdout:
            try:
                sys.stdout.buffer.write(raw)
                sys.stdout.buffer.flush()
            except Exception as e:  # noqa: BLE001 - console must not abort the run
                console_errors.append(f"{type(e).__name__}: {e}")
            log.write(raw)
            log.flush()
            line = raw.decode("utf-8", "replace").rstrip()
            if "[r1]" in line and ("FAIL" in line or "CONTRACT" in line
                                   or "EXIT classification" in line):
                reasons.append(line)
    proc.wait()
    if console_errors:
        reasons.append(f"[matrix] console passthrough degraded: {console_errors[-1]}")
    return int(proc.returncode or 0), reasons


def _write_exit_record(out_dir: Path, record: dict) -> None:
    try:
        (out_dir / "MATRIX_EXIT.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as e:
        print(f"[matrix] WARN: could not write MATRIX_EXIT.json: {e}")


def _audit(out_dir: Path, contract: dict, *, run_id: str) -> tuple[int, dict]:
    """Strictly validate every receipt; derive PASS from evidence, not labels."""
    problems: list[str] = []
    notes: list[str] = []
    if contract.get("contract_sha256") != contract_mod.contract_sha256(contract):
        return 1, {"problems": ["RUN_CONTRACT_TAMPERED"], "counts": {}}
    if (contract.get("code") or {}).get("worktree_dirty"):
        return 1, {"problems": [
            ("CODE_STATE_DIRTY: a run created from a dirty harness worktree "
             "cannot attest a GPU PASS")], "counts": {}}

    try:
        thresholds = contract_mod.load_thresholds_snapshot(out_dir)
    except contract_mod.ContractError as e:
        return 1, {"problems": [str(e)], "counts": {}}
    if contract_mod.thresholds_sha256(thresholds) != contract.get("thresholds_sha256"):
        return 1, {"problems": ["THRESHOLDS_SNAPSHOT_CONTRACT_MISMATCH"], "counts": {}}
    if contract_mod.canonical_json(thresholds) != contract_mod.canonical_json(
            contract.get("thresholds") or {}):
        return 1, {"problems": ["THRESHOLDS_BODY_CONTRACT_MISMATCH"], "counts": {}}

    eps = list(contract.get("eps") or [])
    cases = list(contract.get("cases") or [])
    if not eps or not cases:
        return 1, {"problems": ["EMPTY_CONTRACT_SCOPE: no cases/EPs can attest a run"],
                   "counts": {"PASS_FULL": 0, "PASS_PARTIAL": 0, "FAIL": 0,
                              "BLOCKED": 0, "NOT_RUN": 0}}
    expected = [f"{case}__{ep}" for case in cases for ep in eps]
    allowed_asset = set()
    for m in contract.get("models") or []:
        if m.get("task"):
            allowed_asset.add(f"{Path(m['checkpoint']).stem}__{m.get('task')}__asset")

    receipts: dict[str, dict] = {}
    asset_receipts: dict[str, dict] = {}
    for rp in sorted((out_dir / "receipts").glob("receipt_*.json")):
        try:
            rec = json.loads(rp.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            problems.append(f"{rp.name}: unparseable receipt ({e})")
            continue
        cid = rec.get("case_id")
        if rec.get("run_id") != run_id:
            problems.append(f"{rp.name}: run_id={rec.get('run_id')!r} != {run_id!r}")
        if cid in allowed_asset and cid not in expected:
            asset_receipts[cid] = rec
        elif cid in receipts:
            problems.append(f"duplicate receipt case_id={cid!r}")
        else:
            receipts[cid] = rec

    counts = {"PASS_FULL": 0, "PASS_PARTIAL": 0, "FAIL": 0, "BLOCKED": 0, "NOT_RUN": 0}
    extra = sorted(set(receipts) - set(expected))
    if extra:
        problems.append(f"unexpected receipt case_ids (not in run contract): {extra[:10]}")
        counts["FAIL"] += 1

    for case in cases:
        for ep in eps:
            cid = f"{case}__{ep}"
            rec = receipts.get(cid)
            if rec is None:
                # A same-checkpoint asset receipt is an honest blocked case, not
                # a PASS; keep it out of the PASS denominator.
                asset = asset_receipts.get(f"{case}__asset")
                if asset is not None:
                    counts["BLOCKED"] += 1
                    problems.append(f"{cid}: asset/export blocked (no execution receipt)")
                else:
                    counts["NOT_RUN"] += 1
                    problems.append(f"{cid}: no receipt")
                continue
            v = validate_receipt_strict(
                rec, expected_thresholds=contract_mod.expected_thresholds_for_receipt(
                    rec, thresholds))
            cps = contract_mod.validate_receipt_contract(rec, contract)
            if not v.get("ok") or cps:
                problems.append(
                    f"{cid}: strict validation failed: "
                    f"{(v.get('problems') or []) + cps}")
                counts["FAIL"] += 1
                continue
            derived = (v.get("derived") or {}).get(ep, "NOT_RUN")
            if derived == "PASS_GPU_EXECUTED_FULL":
                counts["PASS_FULL"] += 1
            elif derived == "PASS_GPU_EXECUTED_PARTIAL":
                counts["PASS_PARTIAL"] += 1
                problems.append(f"{cid}: PARTIAL pass is not a full GPU PASS")
            elif str(derived).startswith("FAIL_"):
                counts["FAIL"] += 1
                problems.append(f"{cid}: {derived}")
            elif str(derived).startswith("BLOCKED_"):
                counts["BLOCKED"] += 1
                problems.append(f"{cid}: {derived}")
            else:
                counts["NOT_RUN"] += 1
                problems.append(f"{cid}: {derived}")

    # Asset receipts may never carry a PASS claim. They are not strictly
    # schema-validated as execution receipts (they have no session by design),
    # but their contract and status are still checked here.
    for cid, rec in asset_receipts.items():
        st = rec.get("status") or {}
        if any(str(st.get(k, "")).startswith("PASS") for k in ("amdgpu", "dml", "task_oracle")):
            problems.append(f"{cid}: asset receipt carries a PASS claim")
        cps = contract_mod.validate_receipt_contract(rec, contract)
        if cps:
            problems.append(f"{cid}: asset receipt contract mismatch: {cps}")

    expected_n = len(expected)
    passed = counts["PASS_FULL"] + counts["PASS_PARTIAL"]
    tamper_markers = ("strict validation", "contract", "unexpected", "duplicate",
                      "run_id", "threshold", "TAMPER")
    tampered = any(any(m in p for m in tamper_markers) for p in problems)
    if counts["FAIL"] or tampered:
        rc = 1
    elif problems or passed != expected_n:
        rc = 2
    else:
        rc = 0
    return rc, {"problems": problems, "notes": notes, "counts": counts,
                "expected": expected_n, "passed": passed,
                "asset_receipts": len(asset_receipts)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=None,
                    help="deprecated alias of --gfx (gfx1100/gfx1151/gfx1201)")
    ap.add_argument("--gfx", default=None,
                    help="gfx target (gfx1100/gfx1151/gfx1201); inferred from DXGI "
                         "DID when omitted")
    ap.add_argument("--gpu-name-needle", default=None)
    ap.add_argument("--tier", choices=["core", "full"], default="core")
    ap.add_argument("--ep", choices=["amdgpu", "dml", "both"], default="both")
    ap.add_argument("--catalog", default=str(REPO_ROOT / "catalog" / "model_catalog.json"))
    ap.add_argument("--models-root", default=str(REPO_ROOT / "models"))
    ap.add_argument("--reports-root", default=str(REPO_ROOT / "reports"))
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--max-minutes-per-case", type=float, default=20)
    ap.add_argument("--model", default=None, help="filter by substring")
    ap.add_argument("--task", default=None)
    ap.add_argument("--resume", action="store_true", help="continue an existing contract run")
    ap.add_argument("--winml-python", default=None)
    ap.add_argument("--export-python", default=None)
    ap.add_argument("--image", default=None)
    ap.add_argument("--obb-image", default=None)
    ap.add_argument("--benchmark", action="store_true")
    ap.add_argument("--pt-oracle", action="store_true", help="accepted for CLI compatibility; v2 always runs the PT task oracle")
    ap.add_argument("--with-negative", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)

    reports_root = Path(a.reports_root)
    run_id = a.run_id or ("RUN_" + utc_now_iso().replace("-", "").replace(":", "").replace("T", "_").replace("Z", ""))
    if a.gfx and a.host and a.gfx != a.host:
        print(f"[matrix] FAIL: --gfx={a.gfx!r} conflicts with --host={a.host!r}")
        return 2
    requested_host = a.gfx or a.host
    host_block = _resolve_host(requested_host, a.gpu_name_needle)
    if not host_block.get("verified_device_identity"):
        print(f"[matrix] FAIL: target device not verified: {host_block.get('identity_method')}")
        return 2
    gfx = host_block.get("gfx")
    if gfx not in ("gfx1100", "gfx1151", "gfx1201"):
        print(f"[matrix] FAIL: unsupported gfx={gfx!r}")
        return 2

    if a.resume:
        out_dir = _find_run_dir(reports_root, run_id)
        if out_dir is None:
            print(f"[matrix] FAIL: no contract run directory for run_id={run_id!r}")
            return 2
        try:
            contract = contract_mod.load_run_contract(out_dir)
        except contract_mod.ContractError as e:
            print(f"[matrix] CONTRACT FAIL: {e}")
            return 2
        if requested_host and requested_host != contract.get("gfx"):
            print(f"[matrix] CONTRACT FAIL: --gfx/--host={requested_host!r} != "
                  f"contract gfx={contract.get('gfx')!r}")
            return 2
        gfx = contract["gfx"]
        models = [m["checkpoint"] for m in contract.get("models") or []]
        eps = list(contract.get("eps") or [])
    else:
        out_dir = reports_root / gfx / run_id
        if (out_dir / contract_mod.RUN_CONTRACT_NAME).exists():
            print(f"[matrix] FAIL: {out_dir} already has RUN_CONTRACT.json; use --resume "
                  "or a new run_id")
            return 2
        if out_dir.is_dir() and any((out_dir / "receipts").glob("receipt_*.json")):
            print(f"[matrix] FAIL: {out_dir} already has immutable receipts but no "
                  "run contract; create a new run_id")
            return 2
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
        models = [e["checkpoint"] for e in entries]

    winml_python = a.winml_python or sys.executable
    export_python = a.export_python or sys.executable
    print(f"[matrix] run_id={run_id} gfx={gfx} models={len(models)} eps={eps} out={out_dir}")

    cmd = [winml_python, "-m", "runner.r1_pipeline",
           "--run-dir", str(out_dir),
           "--catalog", str(Path(a.catalog)),
           "--gfx", gfx,
           "--models", ",".join(models),
           "--eps", ",".join(eps),
           "--winml-python", winml_python,
           "--export-python", export_python,
           "--case-timeout-s", str(max(60, int(a.max_minutes_per_case * 60)))]
    if a.gpu_name_needle:
        cmd += ["--gpu-name-needle", a.gpu_name_needle]
    if a.resume:
        cmd += ["--resume"]
    if a.image:
        cmd += ["--image", a.image]
    if a.obb_image:
        cmd += ["--obb-image", a.obb_image]
    if a.benchmark:
        cmd += ["--benchmark"]
    if a.with_negative:
        cmd += ["--with-negative"]
    print("[matrix] exec: " + " ".join(cmd))
    log_path = out_dir / "logs" / "r1_pipeline.log"
    pipe_rc, reasons = _run_pipeline(cmd, log_path)
    classification = pipeline_classification(pipe_rc, reasons)
    record = {
        "schema_version": 1,
        "stage": "r1_pipeline",
        "started_utc": utc_now_iso(),
        "pipeline_exit_code": pipe_rc,
        "classification": classification,
        "reasons": reasons[-20:],
        "argv_redacted": _redacted_argv(cmd),
        "log_path_redacted": redaction.redact_path(log_path),
    }
    if pipe_rc != 0:
        _write_exit_record(out_dir, {**record, "matrix_exit_code": 2 if pipe_rc == 2 else 1})
        print(f"[matrix] r1_pipeline exited rc={pipe_rc} classification={classification}")
        for reason in reasons[-5:]:
            print(f"[matrix]   {reason}")
        if pipe_rc == 2:
            print("[matrix] BLOCKED/NOT_RUN: classified refusal from r1_pipeline "
                  "(exit 2)")
            return 2
        print("[matrix] FAIL: r1_pipeline reported a real failure (exit 1)")
        return 1

    try:
        contract = contract_mod.load_run_contract(out_dir)
    except contract_mod.ContractError as e:
        _write_exit_record(out_dir, {**record, "matrix_exit_code": 1,
                                     "contract_error": str(e)})
        print(f"[matrix] FAIL: {e}")
        return 1
    rc, audit = _audit(out_dir, contract, run_id=run_id)
    print(f"[matrix] strict audit: {audit.get('counts')} "
          f"passed={audit.get('passed')}/{audit.get('expected')} "
          f"asset_receipts={audit.get('asset_receipts')}")
    for p in (audit.get("problems") or [])[:80]:
        print(f"[matrix]   {p}")
    if rc == 0:
        print("[matrix] PASS: every requested case strict-validated as GPU PASS")
    elif rc == 1:
        print("[matrix] FAIL: forged/stale/error evidence (exit 1)")
    else:
        print("[matrix] BLOCKED/NOT_RUN: incomplete evidence cannot PASS (exit 2)")
    _write_exit_record(out_dir, {**record, "matrix_exit_code": rc,
                                 "audit_counts": audit.get("counts"),
                                 "audit_expected": audit.get("expected"),
                                 "audit_passed": audit.get("passed"),
                                 "audit_problems": (audit.get("problems") or [])[:80]})
    return rc


if __name__ == "__main__":
    sys.exit(main())
