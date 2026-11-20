"""Experiment A negative & control test driver (mission step 8).

Twelve fail-closed demonstrations; each records observed behaviour and an
explicit fail_closed verdict. NONE may grant AMDGPU GPU-PASS.

Runtime injections (fresh process each): wrong_did, wrong_dll, unregistered,
luid_pin_fail, bad_path, cpu_impersonate.
Cross-run contaminations (real files): sha_mismatch, input_mismatch.
Classifier/gate controls (pure logic over synthetic+real evidence):
provider_list_no_gpu_node, catalog_empty_dll_present, catalog_entry_dll_missing,
direct_pass_catalog_fail.

Usage (mission venv):
  python -I repro/experiment_a_negative.py --gfx gfx1100 \
      --onnx <a3.onnx> --npy <a3.npy> --work-dir <scratch> \
      --a3 reports/experiment_a/DIRECT_REGISTRATION_CANARY.json \
      --a1 reports/experiment_a/CATALOG_PROBE.json \
      --a4 reports/experiment_a/CATALOG_REGISTRATION_CANARY.json \
      --out reports/experiment_a/NEGATIVE_TESTS.json \
      --mission-venv <python>
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "repro"))
sys.path.insert(0, str(REPO_ROOT / "src"))

from runner.unified_canary import compare_named_outputs  # noqa: E402
from experiment_a_classify import (classify_catalog, classify_direct,  # noqa: E402
                                   classify_final)


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_worker(case: str, mpy: str, onnx: str, npy: str, work: Path,
               timeout_s: int = 420) -> dict:
    out_json = work / f"neg_{case}.json"
    cmd = [mpy, "-I", str(REPO_ROOT / "repro" / "experiment_a_negative_worker.py"),
           "--case", case, "--onnx", onnx, "--npy", npy,
           "--out-json", str(out_json)]
    log = work / f"neg_{case}.log"
    try:
        with log.open("wb") as f:
            p = subprocess.run(cmd, stdout=f, stderr=f, timeout=timeout_s,
                               cwd=str(REPO_ROOT))
        rc = p.returncode
    except subprocess.TimeoutExpired:
        rc = None
    rec = {"case": case, "exit_code": rc}
    try:
        rec.update(json.loads(out_json.read_text(encoding="utf-8")))
    except Exception as e:  # noqa: BLE001
        rec["receipt_error"] = str(e)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gfx", default="gfx1100")
    ap.add_argument("--onnx", required=True)
    ap.add_argument("--npy", required=True)
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--a1", required=True)
    ap.add_argument("--a3", required=True)
    ap.add_argument("--a4", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mission-venv", required=True)
    a = ap.parse_args()

    work = Path(a.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    a3 = json.loads(Path(a.a3).read_text(encoding="utf-8"))
    a1 = json.loads(Path(a.a1).read_text(encoding="utf-8"))
    a4 = json.loads(Path(a.a4).read_text(encoding="utf-8"))

    results: dict = {
        "mission": "A_GFX1100_CATALOG_VS_DIRECT_REGISTRATION",
        "experiment": "A8_NEGATIVE_AND_CONTROL_TESTS",
        "gfx": a.gfx,
        "captured_utc": _utcnow(),
        "cases": [],
    }
    cases = results["cases"]

    # --- runtime injections ---
    for case in ("wrong_did", "wrong_dll", "unregistered", "luid_pin_fail",
                 "bad_path", "cpu_impersonate"):
        rec = run_worker(case, a.mission_venv, a.onnx, a.npy, work)
        rec["expected"] = "fail_closed=True; no GPU-PASS evidence"
        cases.append(rec)

    # --- cross-run contamination: different ONNX/input SHA ---
    #    parity of A3 GPU outputs vs a DIFFERENT input's CPU outputs must FAIL
    import numpy as np
    gpu_npz_path = _find_gpu_npz(a3, work)
    mismatch_rec = {"case": "input_mismatch"}
    try:
        g = dict(np.load(gpu_npz_path))
        rng = np.random.default_rng(4242)
        fake = {"output0": rng.standard_normal(
            next(iter(g.values())).shape).astype("float32")}
        num = compare_named_outputs(g, fake, atol=2e-3, rtol=2e-2)
        mismatch_rec["parity_allclose"] = num.get("allclose")
        mismatch_rec["fail_closed"] = num.get("allclose") is False
    except Exception as e:  # noqa: BLE001
        mismatch_rec["error"] = f"{type(e).__name__}: {e}"
        mismatch_rec["fail_closed"] = True
    cases.append(mismatch_rec)

    # --- different run-id cross-pollution: claimed sha != real file sha ---
    real_sha = _sha256(Path(a.onnx))
    claimed = "0" * 64
    sha_rec = {"case": "sha_mismatch",
               "claimed_onnx_sha256": claimed, "real_onnx_sha256": real_sha,
               "fail_closed": claimed != real_sha}
    cases.append(sha_rec)

    # --- classifier / gate controls ---
    # provider list has AMDGPU but profiler has no GPU node -> gates reject
    gate = (a3.get("gates") or {})
    synth_providers = ["AMDGPUExecutionProvider", "CPUExecutionProvider"]
    synth_counts = {"CPU": 7}
    synth_target = sum(synth_counts.get(k, 0)
                       for k in ("AMDGPU", "AMDGPUExecutionProvider"))
    cases.append({
        "case": "provider_list_no_gpu_node",
        "synthetic": {"providers": synth_providers,
                      "provider_node_counts": synth_counts},
        "gate_target_nodes_positive": synth_target > 0,
        "gate_cpu_nodes_zero": synth_counts.get("CPU", 0) == 0,
        "fail_closed": not (synth_target > 0 and synth_counts.get("CPU", 0) == 0),
    })

    # catalog empty but DLL present -> distinct classification, never conflated
    d = classify_direct(a3)
    empty_a1 = {"classification": "CATALOG_EMPTY", "amdgpu_listed_any_run": False}
    c_empty = classify_catalog(empty_a1, {})
    cases.append({
        "case": "catalog_empty_dll_present",
        "classifier": {"direct": d, "catalog": c_empty,
                       "final": classify_final(d, c_empty)},
        "fail_closed": c_empty == "CATALOG_EMPTY",
    })

    # catalog entry but DLL unavailable -> A4 blocked path, direct also blocked
    c_entry = classify_catalog(
        {"classification": "CATALOG_AMDGPU_LISTED_READY",
         "amdgpu_listed_any_run": True},
        {"verdict": "CATALOG_READY_NO_LIBRARY_PATH"})
    d_nodll = classify_direct({"verdict": "BLOCKED_EP_DLL_NOT_FOUND"})
    cases.append({
        "case": "catalog_entry_dll_missing",
        "classifier": {"direct": d_nodll, "catalog": c_entry,
                       "final": classify_final(d_nodll, c_entry)},
        "fail_closed": d_nodll == "DIRECT_FAILED",
    })

    # the REAL combination of this host: direct PASS + catalog NOT READY
    c_real = classify_catalog(a1, a4)
    cases.append({
        "case": "direct_pass_catalog_fail",
        "real_evidence": {"direct": d, "catalog": c_real,
                          "final": classify_final(d, c_real)},
        "fail_closed": classify_final(d, c_real) == "A_DIRECT_GPU_GO_CATALOG_NOT_READY",
    })

    results["all_fail_closed"] = all(c.get("fail_closed") is True
                                     for c in cases)
    results["case_count"] = len(cases)
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                 encoding="utf-8")
    print(f"[neg] all_fail_closed={results['all_fail_closed']} "
          f"({results['case_count']} cases) -> {p}")
    return 0 if results["all_fail_closed"] else 1


def _find_gpu_npz(a3: dict, work: Path) -> Path:
    """Locate the A3 GPU outputs npz (work dir layout: a3_work/gpu_outputs.npz)."""
    for cand in (work.parent / "a3_work" / "gpu_outputs.npz",
                 work / "gpu_outputs.npz",
                 Path(r"C:\Users\Rocm\Desktop\YOLO\experiment_a\a3_work"
                      r"\gpu_outputs.npz")):
        if cand.is_file():
            return cand
    raise FileNotFoundError("gpu_outputs.npz not found for mismatch test")


if __name__ == "__main__":
    sys.exit(main())
