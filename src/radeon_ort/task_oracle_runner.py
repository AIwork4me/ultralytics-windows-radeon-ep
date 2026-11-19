"""Task oracle runner for the unified runtime (mission R7-A).

predict() can prove *numeric* parity (its GPU output vs a CPU ORT run of the
same ONNX on the same bytes) but that is not task correctness. This module adds
the second, independent oracle the repository already trusts in the Matrix
harness: the pinned Ultralytics PT model executed on the SAME preprocessed
tensor, post-processed by the vendor decode, and compared with the
predeclared thresholds in configs/thresholds.yaml.

Non-negotiable properties:

  * the PT reference runs in its OWN process (the export venv, CPU only) and in
    its own Job Object - it never shares an interpreter with the GPU attempt;
  * the GPU side is never asked whether it succeeded: its raw output array is
    re-decoded and re-compared from scratch;
  * the ONNX file, the PT checkpoint, the input bytes, the thresholds and the
    pre/post-processing parameters are all bound into the evidence block;
  * a missing/incompatible reference yields TASK_ORACLE_BLOCKED with a concrete
    reason - never a fabricated PASS.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np

from . import win_job

SUPPORTED_TASKS = ("detect", "segment", "semantic", "depth", "classify", "pose", "obb")

POSTPROCESS_SOURCE = "ultralytics 8.4.215 vendor decode (non_max_suppression/process_mask)"
PT_ORACLE_SOURCE = "ultralytics 8.4.215 PT application oracle (pt_oracle_v2.py)"

PASS_TASK = "PASS_TASK"
FAIL_TASK = "FAIL_TASK"
BLOCKED = "TASK_ORACLE_BLOCKED"
NOT_RUN = "NOT_RUN"


class OracleBlocked(RuntimeError):
    """The reference cannot be produced or trusted; never downgraded to a PASS."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code + ((": " + detail) if detail else ""))
        self.code = code
        self.detail = detail


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run(cmd: list[str], *, cwd: Path, log_path: Path, timeout_s: int,
         label: str) -> win_job.SpawnResult:
    return win_job.spawn_in_job(cmd, cwd=cwd, log_path=log_path,
                                timeout_s=timeout_s, label=label)


def resolve_task_spec(*, onnx_path: str | Path, task: str, repo_root: str | Path) -> dict:
    """Bind the ONNX artefact to its catalog entry (task + default imgsz)."""
    root = Path(repo_root)
    catalog_path = root / "catalog" / "model_catalog.json"
    name = Path(onnx_path).name
    out = {"catalog_match": False, "task": task, "imgsz": 640,
           "catalog_entry": None, "catalog_sha256": None, "catalog_onnx_name": name}
    if not catalog_path.is_file():
        out["reason"] = "MODEL_CATALOG_MISSING"
        return out
    out["catalog_sha256"] = sha256_file(catalog_path)
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        out["reason"] = "MODEL_CATALOG_UNPARSABLE"
        return out
    for key, entry in (catalog.get("entries") or {}).items():
        cand = Path(key).stem + ".raw_b1_fp32_static.onnx"
        if cand == name:
            out.update({
                "catalog_match": True,
                "catalog_entry": key,
                "catalog_task": entry.get("task"),
                "imgsz": int(entry.get("imgsz_default") or 640),
                "nms_mode": entry.get("nms_mode"),
            })
            break
    return out


def _tensor_npy(inputs: dict, target: Path) -> tuple[str, str]:
    """Persist the exact tensor that was fed to the EP (float32, C-contiguous)."""
    if not inputs:
        raise OracleBlocked("TASK_ORACLE_NO_INPUT")
    name = min(inputs)
    if len(inputs) != 1:
        raise OracleBlocked("TASK_ORACLE_MULTI_INPUT_UNSUPPORTED",
                            "found " + str(sorted(inputs)))
    arr = np.ascontiguousarray(np.asarray(inputs[name], dtype=np.float32))
    target.parent.mkdir(parents=True, exist_ok=True)
    # np.save appends ".npy" unless the name already ends in it; keep the temp
    # name *.npy so os.replace targets exactly the file we wrote.
    tmp = target.with_name(target.stem + ".tmp.npy")
    np.save(tmp, arr)
    os.replace(tmp, target)
    digest = hashlib.sha256(arr.tobytes()).hexdigest()
    return name, digest


def run_task_oracle(*, onnx_path: str | Path, pt_reference: str | Path,
                    outputs_npz: str | Path, inputs: dict, task: str,
                    export_python: str | Path, repo_root: str | Path,
                    attempt_dir: str | Path, result_dir: str | Path,
                    imgsz: int | None = None, conf: float = 0.25,
                    timeout_s: int = 900) -> dict:
    """Produce the task_parity evidence block, or a TASK_ORACLE_BLOCKED record.

    Never raises for a blocked reference: the caller receives status=BLOCKED
    with a reason, because "we could not verify" must be representable without
    turning the whole run into an exception.
    """
    t0 = time.perf_counter()
    root = Path(repo_root)
    attempt_dir = Path(attempt_dir)
    result_dir = Path(result_dir)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)
    block: dict = {
        "status": NOT_RUN, "passed": False, "task": task,
        "reason": None, "reason_code": None,
        "oracle_source": {
            "reference": PT_ORACLE_SOURCE,
            "candidate_postprocess": POSTPROCESS_SOURCE,
            "comparator": "radeon_ort.task_parity.compare",
        },
        "binding": {}, "params": {"imgsz": imgsz, "conf": conf},
        "thresholds": {}, "metrics": {}, "ref": {}, "cand": {},
        "logs": {}, "elapsed_s": None,
    }

    def blocked(code: str, detail: str = "") -> dict:
        block["status"] = BLOCKED
        block["passed"] = False
        block["reason_code"] = code
        block["reason"] = code + ((": " + detail) if detail else "")
        block["elapsed_s"] = round(time.perf_counter() - t0, 4)
        return block

    # --- preconditions -------------------------------------------------------
    onnx_path = Path(onnx_path)
    pt_reference = Path(pt_reference) if pt_reference else None
    if task not in SUPPORTED_TASKS:
        return blocked("TASK_ORACLE_UNSUPPORTED_TASK", str(task))
    if pt_reference is None or not pt_reference.is_file():
        return blocked("TASK_ORACLE_REFERENCE_MISSING", str(pt_reference))
    if not onnx_path.is_file():
        return blocked("TASK_ORACLE_ONNX_MISSING", str(onnx_path))
    if not Path(outputs_npz).is_file():
        return blocked("TASK_ORACLE_OUTPUTS_MISSING", str(outputs_npz))
    export_python = Path(export_python) if export_python else None
    if export_python is None or not export_python.is_file():
        return blocked("TASK_ORACLE_INTERPRETER_MISSING", str(export_python))

    spec = resolve_task_spec(onnx_path=onnx_path, task=task, repo_root=root)
    block["catalog"] = spec
    resolved_imgsz = int(imgsz or spec.get("imgsz") or 640)
    block["params"]["imgsz"] = resolved_imgsz
    if spec.get("catalog_match") and spec.get("catalog_task") not in (None, task):
        return blocked("TASK_ORACLE_MODEL_TASK_MISMATCH",
                       "onnx catalog task=" + str(spec.get("catalog_task"))
                       + " requested=" + task)

    thresholds_path = root / "configs" / "thresholds.yaml"
    try:
        import yaml
        thresholds = yaml.safe_load(thresholds_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - a missing threshold file blocks the oracle
        return blocked("TASK_ORACLE_THRESHOLDS_UNAVAILABLE", type(e).__name__ + ": " + str(e))
    thresholds_text = json.dumps(thresholds, sort_keys=True)
    th_sha = _sha256_text(thresholds_text)
    th_json = result_dir / "thresholds_snapshot.json"
    th_json.write_text(thresholds_text, encoding="utf-8")

    # --- bind the artefacts --------------------------------------------------
    onnx_sha = sha256_file(onnx_path)
    pt_sha = sha256_file(pt_reference)
    tensor_name, tensor_sha = _tensor_npy(inputs, attempt_dir / "tensor.npy")
    block["binding"] = {
        "task": task,
        "onnx_path_redacted": onnx_path.name,
        "onnx_sha256": onnx_sha,
        "pt_reference_redacted": pt_reference.name,
        "pt_reference_sha256": pt_sha,
        "input_name": tensor_name,
        "input_tensor_sha256": tensor_sha,
        "thresholds_sha256": th_sha,
        "thresholds_source": "configs/thresholds.yaml",
        "imgsz": resolved_imgsz,
        "conf": conf,
        "preprocess": {
            "pipeline": "radeon_ort.input_contract.preprocess_real_image"
                        " (letterbox + /255 + CHW, BGR->RGB)",
            "note": "PT and ONNX receive the byte-identical tensor saved here",
        },
        "postprocess": {
            "pipeline": POSTPROCESS_SOURCE,
            "conf": conf,
            "iou": "ultralytics PREDICT_IOU",
            "max_det": 300,
            "task": task,
        },
    }

    # --- 1) PT reference (export venv, CPU only, own job) --------------------
    pt_json = result_dir / "pt_reference.json"
    pt_npz = result_dir / "pt_reference.npz"
    pt_log = result_dir / "log_pt_oracle.txt"
    pt_script = root / "src" / "radeon_ort" / "pt_oracle_v2.py"
    r = _run([str(export_python), "-I", str(pt_script),
              "--pt", str(pt_reference), "--task", task,
              "--tensor-npy", str(attempt_dir / "tensor.npy"),
              "--conf", str(conf),
              "--out-json", str(pt_json), "--out-npz", str(pt_npz)],
             cwd=root, log_path=pt_log, timeout_s=timeout_s, label="pt-oracle")
    block["logs"]["pt"] = {"path": pt_log.name, "exit_code": r.returncode,
                           "timed_out": r.timed_out, "job_assigned": r.job_assigned}
    if r.timed_out:
        return blocked("TASK_ORACLE_TIMEOUT", "PT reference exceeded " + str(timeout_s) + "s")
    if r.returncode != 0 or not pt_json.is_file():
        return blocked("TASK_ORACLE_PT_FAILED",
                       "exit=" + str(r.returncode) + " see " + pt_log.name)
    try:
        pt_payload = json.loads(pt_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return blocked("TASK_ORACLE_PT_UNPARSABLE", str(e))
    pt_task = pt_payload.get("task")
    block["ref"] = {
        "source": "PT_ULTRALYTICS",
        "task": pt_task,
        "n_entities": pt_payload.get("n", len(pt_payload.get("boxes") or [])),
        "pt_sha256": pt_sha,
        "json_sha256": sha256_file(pt_json),
        "npz_sha256": sha256_file(pt_npz) if pt_npz.is_file() else None,
        "python": export_python.name,
    }
    if pt_task and pt_task != task:
        return blocked("TASK_ORACLE_TASK_REBIND",
                       "PT checkpoint reports task=" + str(pt_task) + " requested=" + task)
    if not pt_npz.is_file():
        return blocked("TASK_ORACLE_PT_NPZ_MISSING")

    # --- 2) vendor postprocess of the EP's RAW output ------------------------
    cand_json = result_dir / "candidate_postprocess.json"
    cand_npz = result_dir / "candidate_postprocess.npz"
    pp_log = result_dir / "log_postprocess.txt"
    pp_script = root / "src" / "radeon_ort" / "onnx_postprocess.py"
    r2 = _run([str(export_python), "-I", str(pp_script),
               "--npz", str(outputs_npz), "--task", task,
               "--imgsz", str(resolved_imgsz), "--conf", str(conf),
               "--out-json", str(cand_json), "--out-npz", str(cand_npz)],
              cwd=root, log_path=pp_log, timeout_s=timeout_s, label="postprocess")
    block["logs"]["postprocess"] = {"path": pp_log.name, "exit_code": r2.returncode,
                                    "timed_out": r2.timed_out,
                                    "job_assigned": r2.job_assigned}
    if r2.timed_out:
        return blocked("TASK_ORACLE_POSTPROCESS_TIMEOUT")
    if r2.returncode != 0 or not cand_json.is_file():
        return blocked("TASK_ORACLE_POSTPROCESS_FAILED",
                       "exit=" + str(r2.returncode) + " see " + pp_log.name)
    block["cand"] = {
        "source": "EP_POST",
        "outputs_npz_sha256": sha256_file(outputs_npz),
        "json_sha256": sha256_file(cand_json),
        "npz_sha256": sha256_file(cand_npz) if cand_npz.is_file() else None,
    }

    # --- 3) independent comparison against predeclared thresholds -----------
    tp_json = result_dir / "task_parity.json"
    tp_log = result_dir / "log_task_parity.txt"
    tp_script = root / "src" / "radeon_ort" / "task_parity.py"
    r3 = _run([str(export_python), "-I", str(tp_script),
               "--pt-json", str(pt_json), "--pt-npz", str(pt_npz),
               "--cand-json", str(cand_json), "--cand-npz", str(cand_npz),
               "--task", task, "--imgsz", str(resolved_imgsz),
               "--thresholds-json", str(th_json),
               "--out", str(tp_json)],
              cwd=root, log_path=tp_log, timeout_s=timeout_s, label="task-parity")
    block["logs"]["compare"] = {"path": tp_log.name, "exit_code": r3.returncode,
                                "timed_out": r3.timed_out,
                                "job_assigned": r3.job_assigned}
    if r3.timed_out:
        return blocked("TASK_ORACLE_COMPARE_TIMEOUT")
    if r3.returncode != 0 or not tp_json.is_file():
        return blocked("TASK_ORACLE_COMPARE_FAILED",
                       "exit=" + str(r3.returncode) + " see " + tp_log.name)
    try:
        tp = json.loads(tp_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return blocked("TASK_ORACLE_COMPARE_UNPARSABLE", str(e))
    if tp.get("not_implemented_reason") and "no comparator" in str(tp.get("not_implemented_reason")):
        return blocked("TASK_ORACLE_COMPARATOR_MISSING", str(tp.get("not_implemented_reason")))

    block.update({
        "status": PASS_TASK if tp.get("passed") else FAIL_TASK,
        "passed": bool(tp.get("passed")),
        "metrics": tp.get("metrics") or {},
        "thresholds": tp.get("thresholds_declared") or {},
        "thresholds_declared": tp.get("thresholds_declared") or {},
        "ref_fingerprint_sha256": tp.get("ref_fingerprint_sha256"),
        "cand_fingerprint_sha256": tp.get("cand_fingerprint_sha256"),
        "oracle_kind": tp.get("oracle_kind"),
        "not_implemented_reason": tp.get("not_implemented_reason"),
        "task_parity_block_sha256": sha256_file(tp_json),
        "reason": None if tp.get("passed") else "TASK_METRICS_BELOW_THRESHOLD",
        "reason_code": None if tp.get("passed") else "TASK_METRICS_BELOW_THRESHOLD",
    })
    block["elapsed_s"] = round(time.perf_counter() - t0, 4)
    return block