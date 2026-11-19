r"""python -m runner.unified_predict - CLI for the unified Radeon runtime.

Example:

    .venv-winml\Scripts\python.exe -m runner.unified_predict ^
        --model models\onnx\yolo26n.raw_b1_fp32_static.onnx ^
        --inputs-npz data\fixtures\yolo26n_input.npz ^
        --policy prefer_amdgpu_allow_dml --gfx gfx1151 --out reports\unified\run.json

Exit codes (documented in docs/unified/CONTRACTS.md):

    0   GPU_FULL / GPU_FULL_DML / CPU_ONLY_USER_AUTHORIZED
    2   contract error (bad arguments, conflicting consent)
    30  blocked (no EP could execute under the requested policy)
    31  AMDGPU failed (require_amdgpu policy)
    34  device/identity blocked
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from radeon_ort.unified_runtime import (
    BLOCKED,
    CPU_AUTHORIZED,
    GPU_FULL,
    GPU_FULL_DML,
    ContractError,
    predict,
    verify_run_result,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--inputs-npz", required=True,
                    help="npz of name -> array feeds")
    ap.add_argument("--policy", default="require_amdgpu")
    ap.add_argument("--gfx", default=None,
                    help="gfx target; inferred from DXGI DID when omitted")
    ap.add_argument("--target-gpu", default=None)
    ap.add_argument("--allow-cpu", action="store_true")
    ap.add_argument("--no-require-full-gpu", action="store_true")
    ap.add_argument("--task", default=None)
    ap.add_argument("--timeout-s", type=int, default=900)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--work-dir", default=None)
    ap.add_argument("--winml-python", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    z = np.load(a.inputs_npz)
    inputs = {k: z[k] for k in z.files}
    try:
        result = predict(
            model_path=a.model, inputs=inputs, policy=a.policy,
            target_gfx=a.gfx, target_gpu=a.target_gpu,
            allow_cpu=a.allow_cpu,
            require_full_gpu=not a.no_require_full_gpu,
            task=a.task, timeout_s=a.timeout_s,
            winml_python=a.winml_python, work_dir=a.work_dir,
            run_id=a.run_id)
    except ContractError as e:
        print(f"[unified] CONTRACT ERROR: {e}")
        return 2

    verdict = verify_run_result(result)
    payload = {k: v for k, v in result.items() if k != "outputs"}
    payload["verifier"] = verdict
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=str) + "\n",
                   encoding="utf-8")
    print(json.dumps({
        "runtime_status": result.get("runtime_status"),
        "actual_ep": result.get("actual_ep"),
        "target_nodes": result.get("target_ep_profile_nodes"),
        "cpu_nodes": result.get("cpu_profile_nodes"),
        "binding": result.get("device_binding_confidence"),
        "verifier": verdict["verdict"],
        "out": str(out),
    }, indent=2))

    status = result.get("runtime_status")
    if status in (GPU_FULL, GPU_FULL_DML, CPU_AUTHORIZED):
        return 0
    if status == BLOCKED:
        return 34 if result.get("fallback_reason", "").find("DID") >= 0 else 30
    return 31


if __name__ == "__main__":
    sys.exit(main())
