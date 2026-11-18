"""python -m runner.unified_predict — CLI for the unified runtime.

Exit codes (CONTRACTS.md §3):
  0  success (GPU_FULL / GPU_FULL_DML / CPU_ONLY_USER_AUTHORIZED)
  2  contract error
 31  AMDGPU_FAILED (fail-closed)
 32  DML_FAILED
 34  BLOCKED_*
 35  SILENT_CPU_FALLBACK_BLOCKED
CPU is never attempted without --allow-cpu (plus policy cpu_opt_in).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from radeon_ort.unified_runtime import (  # noqa: E402
    BLOCKED, ContractError, CPU_AUTHORIZED, FAIL, GPU_FULL, GPU_FULL_DML,
    PARTIAL_CPU, predict, verify_run_result,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True)
    ap.add_argument("--policy", default="require_amdgpu",
                    choices=["require_amdgpu", "prefer_amdgpu_allow_dml",
                             "cpu_opt_in"])
    ap.add_argument("--target-gpu", default=None)
    ap.add_argument("--target-gfx", default=None)
    ap.add_argument("--allow-cpu", action="store_true",
                    help="explicit user consent; only meaningful with "
                         "--policy cpu_opt_in")
    ap.add_argument("--allow-partial", action="store_true",
                    help="downgrade require_full_gpu (PARTIAL reported honestly)")
    ap.add_argument("--task", default=None)
    ap.add_argument("--timeout-s", type=int, default=1200)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    import numpy as np

    rng = np.random.default_rng(20261119)
    inputs = {"x": (rng.standard_normal((1, 3, 32, 32)) * 0.5).astype(np.float32)}
    # deterministic canary-style input; real callers pass their own tensors

    try:
        r = predict(a.model, inputs, policy=a.policy, target_gpu=a.target_gpu,
                    target_gfx=a.target_gfx, allow_cpu=a.allow_cpu,
                    require_full_gpu=not a.allow_partial, task=a.task,
                    timeout_s=a.timeout_s)
    except ContractError as e:
        print(json.dumps({"status": "CONTRACT_ERROR", "error": str(e)}))
        return 2

    verdict = verify_run_result(r)
    payload = {"run_result": {k: v for k, v in r.items() if k != "outputs"},
               "verifier": verdict}
    if a.out:
        Path(a.out).write_text(json.dumps(payload, indent=1, default=str),
                               encoding="utf-8")
    print(json.dumps({"status": r["status"],
                      "actual_ep": r["actual_ep"],
                      "fallback_reason": r["fallback_reason"],
                      "verifier": verdict["verdict"]}, default=str))
    fr = str(r.get("fallback_reason") or "")
    if r["status"] in (GPU_FULL, GPU_FULL_DML, CPU_AUTHORIZED):
        return 0
    if r["status"] == PARTIAL_CPU:
        return 35
    if r["status"] == BLOCKED:
        return 34
    if "SILENT_CPU_FALLBACK" in fr:
        return 35
    if r["actual_ep"] == "dml" or "DML_FAILED" in fr:
        return 32
    if r["status"] == FAIL and r["attempted_eps"] == ["amdgpu"]:
        return 31
    return 31 if r["status"] == FAIL else 2


if __name__ == "__main__":
    sys.exit(main())
