r"""python -m runner.unified_task_matrix - 7-task oracle matrix through predict().

Runs every Tier A checkpoint through the *unified* developer entrypoint
(predict) with verify_task=True, so the task-oracle path is exercised exactly
as a developer would use it, not through a private harness.

For each (checkpoint, task):
  * the input tensor is built by the shared preprocessing path and persisted,
  * predict() runs the policy chain (AMDGPU -> DML), numeric parity against its
    own CPU ORT reference, and the independent PT task oracle,
  * the RunResult receipt is written under <run-root>/<run_id>/,
  * the summary records runtime_status / gpu_full / task_verified / metrics.

Exit 0 only when every case satisfies: adopted GPU result, task_verified=True,
and RUNRESULT_VALID. Anything else is a non-zero exit - a blocked oracle is
never rounded up to a PASS.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runner import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "src"))

from radeon_ort.receipts import utc_now_iso

TIER_A = [
    ("yolo26n.pt", "detect"),
    ("yolo26n-seg.pt", "segment"),
    ("yolo26n-sem.pt", "semantic"),
    ("yolo26n-depth.pt", "depth"),
    ("yolo26n-cls.pt", "classify"),
    ("yolo26n-pose.pt", "pose"),
    ("yolo26n-obb.pt", "obb"),
]


def _build_input(image: Path, imgsz: int, task: str, out_npz: Path) -> dict:
    """Preprocess once, in this interpreter (cv2 is available in the winml venv)."""
    import cv2
    import numpy as np

    from radeon_ort.input_contract import preprocess_real_image, tensor_sha256

    img = cv2.imread(str(image), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("image decode failed: " + str(image))
    arr, params = preprocess_real_image(img, imgsz, task)
    out_npz.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out_npz, images=arr)
    import hashlib
    return {
        "image": image.name,
        "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        "tensor_sha256": tensor_sha256(arr),
        "tensor_shape": list(arr.shape),
        "preprocess": params,
        "npz": out_npz.name,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gfx", default=None)
    ap.add_argument("--policy", default="prefer_amdgpu_allow_dml")
    ap.add_argument("--ep", choices=["dml", "amdgpu", "auto"], default="auto")
    ap.add_argument("--image", default=str(REPO_ROOT / "data" / "fixtures" / "bus.jpg"))
    ap.add_argument("--obb-image", default=str(REPO_ROOT / "data" / "fixtures" / "dota_p1053.jpg"))
    ap.add_argument("--models", default=",".join(m for m, _ in TIER_A))
    ap.add_argument("--run-root", default=str(REPO_ROOT / "reports" / "unified"))
    ap.add_argument("--run-prefix", default="GFX1151_R7_TASK")
    ap.add_argument("--timeout-s", type=int, default=900)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    from radeon_ort.unified_runtime import predict, validate_run_result_schema, verify_run_result

    root = Path(a.run_root)
    out = Path(a.out)
    rows: list[dict] = []
    wanted = {m.strip() for m in a.models.split(",") if m.strip()}

    for checkpoint, task in TIER_A:
        if checkpoint not in wanted:
            continue
        onnx = REPO_ROOT / "models" / "onnx" / (Path(checkpoint).stem + ".raw_b1_fp32_static.onnx")
        pt = REPO_ROOT / "models" / "pt" / checkpoint
        if not onnx.is_file() or not pt.is_file():
            rows.append({"checkpoint": checkpoint, "task": task,
                         "status": "ASSET_MISSING"})
            continue
        imgsz = 640
        try:
            catalog = json.loads((REPO_ROOT / "catalog" / "model_catalog.json")
                                 .read_text(encoding="utf-8"))
            imgsz = int((catalog["entries"].get(checkpoint) or {}).get("imgsz_default") or 640)
        except Exception:  # noqa: BLE001 - default size is fine for a smoke matrix
            pass
        image = Path(a.obb_image if task == "obb" else a.image)
        run_id = a.run_prefix + "_" + Path(checkpoint).stem.upper().replace("-", "_")
        work = root / run_id
        npz = work / "inputs" / (Path(checkpoint).stem + ".npz")
        try:
            input_meta = _build_input(image, imgsz, task, npz)
        except Exception as e:  # noqa: BLE001
            rows.append({"checkpoint": checkpoint, "task": task,
                         "status": "INPUT_BUILD_FAILED",
                         "error": type(e).__name__ + ": " + str(e)})
            continue
        import numpy as np
        inputs = {"images": np.load(npz)["images"]}
        row: dict = {"checkpoint": checkpoint, "task": task, "imgsz": imgsz,
                     "run_id": run_id, "input": input_meta}
        try:
            r = predict(model_path=onnx, inputs=inputs, policy=a.policy,
                        target_gfx=a.gfx, task=task, verify_task=True,
                        pt_reference=pt, work_dir=work, run_id=run_id,
                        timeout_s=a.timeout_s,
                        task_params={"imgsz": imgsz, "conf": 0.25})
        except Exception as e:  # noqa: BLE001 - record the failure, never hide it
            row.update({"status": "PREDICT_EXCEPTION",
                        "error": type(e).__name__ + ": " + str(e)})
            rows.append(row)
            continue
        v = verify_run_result(r, model_path=onnx, inputs=inputs, work_dir=work, deep=True)
        s = validate_run_result_schema(r)
        tp = r.get("task_parity") or {}
        row.update({
            "runtime_status": r.get("runtime_status"),
            "actual_ep": r.get("actual_ep"),
            "gpu_full": r.get("gpu_full"),
            "task_verified": r.get("task_verified"),
            "task_status": tp.get("status"),
            "task_reason": tp.get("reason"),
            "metrics": tp.get("metrics"),
            "thresholds": tp.get("thresholds"),
            "target_nodes": r.get("target_ep_profile_nodes"),
            "cpu_nodes": r.get("cpu_profile_nodes"),
            "binding": r.get("device_binding_confidence"),
            "adopted_attempt_id": r.get("adopted_attempt_id"),
            "verifier": v["verdict"],
            "verifier_failed": v.get("failed"),
            "schema_ok": s.get("ok"),
            "receipt": r.get("receipt"),
            "status": "PASS" if (r.get("gpu_full") and r.get("task_verified")
                                 and v["verdict"] == "RUNRESULT_VALID") else "FAIL",
        })
        rows.append(row)
        print("[task-matrix] " + checkpoint + " -> " + row["status"]
              + " (" + str(row.get("task_status")) + ")")

    ok = [r for r in rows if r.get("status") == "PASS"]
    summary = {
        "schema_version": 1,
        "captured_utc": utc_now_iso(),
        "scope": "unified predict() with verify_task=True on real images; "
                 "one checkpoint per task",
        "policy": a.policy,
        "gfx": a.gfx,
        "cases": len(rows),
        "passed": len(ok),
        "all_passed": len(ok) == len(rows) and len(rows) > 0,
        "results": rows,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("cases", "passed", "all_passed")}, indent=1))
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
