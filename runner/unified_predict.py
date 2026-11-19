r"""python -m runner.unified_predict - CLI for the unified Radeon runtime.

Example (numeric parity only):

    .venv-winml\Scripts\python.exe -m runner.unified_predict ^
        --model models\onnx\yolo26n.raw_b1_fp32_static.onnx ^
        --inputs-npz data\fixtures\yolo26n_input.npz ^
        --policy prefer_amdgpu_allow_dml --gfx gfx1151 --out reports\unified\run.json

Example (full task verification against the PT reference):

    .venv-winml\Scripts\python.exe -m runner.unified_predict ^
        --model models\onnx\yolo26n.raw_b1_fp32_static.onnx ^
        --inputs-npz data\fixtures\yolo26n_input.npz ^
        --policy prefer_amdgpu_allow_dml --gfx gfx1151 ^
        --task detect --verify-task --pt-reference models\pt\yolo26n.pt ^
        --run-id GFX1151_R7_TASK_DETECT --out reports\unified\r7_detect.json

Exit codes (documented in docs/unified/CONTRACTS.md):

    0   GPU_FULL / GPU_FULL_DML / CPU_ONLY_USER_AUTHORIZED  (and, when
        --verify-task was requested, task_verified == true)
    2   contract error (bad arguments, conflicting consent, frozen run_id)
    4   the execution result was adopted but the task oracle did not pass
        (FAIL_TASK / TASK_ORACLE_BLOCKED) - never a silent success
    5   the adopted result failed independent verification (INVALID) - the
        evidence contradicts itself, so the CLI must not exit 0
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
    validate_run_result_schema,
    verify_run_result,
)

EXIT_TASK_NOT_VERIFIED = 4
EXIT_EVIDENCE_INVALID = 5


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
    ap.add_argument("--task", default=None,
                    help="detect|segment|semantic|depth|classify|pose|obb")
    ap.add_argument("--verify-task", action="store_true",
                    help="run the independent PT task oracle (requires --pt-reference)")
    ap.add_argument("--pt-reference", default=None,
                    help="matching .pt checkpoint for the task oracle")
    ap.add_argument("--export-python", default=None,
                    help="interpreter with torch+ultralytics for the PT oracle")
    ap.add_argument("--imgsz", type=int, default=None)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--cpu-reference", choices=["allow", "forbid"], default="allow",
                    help="allow the internal CPU ORT numeric reference, or forbid it "
                         "(forbid => parity is UNVERIFIED and no GPU FULL claim)")
    ap.add_argument("--container", choices=["job_object", "plain"], default="job_object")
    ap.add_argument("--timeout-s", type=int, default=900)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--work-dir", default=None)
    ap.add_argument("--winml-python", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    z = np.load(a.inputs_npz)
    inputs = {k: z[k] for k in z.files}
    export_python = a.export_python
    if export_python is None:
        cand = Path(__file__).resolve().parents[1] / ".venv-export" / "Scripts" / "python.exe"
        export_python = str(cand) if cand.is_file() else None
    try:
        result = predict(
            model_path=a.model, inputs=inputs, policy=a.policy,
            target_gfx=a.gfx, target_gpu=a.target_gpu,
            allow_cpu=a.allow_cpu,
            require_full_gpu=not a.no_require_full_gpu,
            task=a.task, timeout_s=a.timeout_s,
            winml_python=a.winml_python, work_dir=a.work_dir,
            run_id=a.run_id,
            verify_task=a.verify_task, pt_reference=a.pt_reference,
            export_python=export_python,
            task_params={"imgsz": a.imgsz, "conf": a.conf},
            cpu_reference=a.cpu_reference,
            container=a.container)
    except ContractError as e:
        print(f"[unified] CONTRACT ERROR: {e}")
        return 2

    work = (Path(a.work_dir) if a.work_dir
            else Path(__file__).resolve().parents[1] / "reports" / "unified"
            / str(result.get("run_id")))
    verdict = verify_run_result(
        result, model_path=a.model, inputs=inputs,
        work_dir=work, deep=True)
    schema = validate_run_result_schema(result)
    payload = {k: v for k, v in result.items() if k != "outputs"}
    payload["verifier"] = verdict
    payload["schema_check"] = schema
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=str) + "\n",
                   encoding="utf-8")

    tp = result.get("task_parity") or {}
    print(json.dumps({
        "runtime_status": result.get("runtime_status"),
        "actual_ep": result.get("actual_ep"),
        "target_nodes": result.get("target_ep_profile_nodes"),
        "cpu_nodes": result.get("cpu_profile_nodes"),
        "binding": result.get("device_binding_confidence"),
        "gpu_full": result.get("gpu_full"),
        "task_verified": result.get("task_verified"),
        "task_status": tp.get("status"),
        "task_metrics": tp.get("metrics"),
        "verifier": verdict["verdict"],
        "evidence_grade": result.get("evidence_grade"),
        "readiness": result.get("readiness"),
        "receipt": result.get("receipt"),
        "out": str(out),
    }, indent=2, default=str))

    status = result.get("runtime_status")
    if status in (GPU_FULL, GPU_FULL_DML, CPU_AUTHORIZED):
        if verdict["verdict"] == "RUNRESULT_INVALID":
            # a self-contradictory or tampered result is never a success
            return EXIT_EVIDENCE_INVALID
        if a.verify_task and not result.get("task_verified"):
            return EXIT_TASK_NOT_VERIFIED
        return 0
    if status == BLOCKED:
        return 34 if "DID" in str(result.get("fallback_reason", "")) else 30
    return 31


if __name__ == "__main__":
    sys.exit(main())