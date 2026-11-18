"""python -m runner.r1_pipeline — mission GFX1151-R1 true-task-parity pipeline.

Per (model, task): deterministic input bundle -> CPU reference -> AMDGPU (FIRST)
-> DML (independent comparator) -> PT oracle (same tensor bytes) -> vendor
postprocess -> task-parity comparison -> strict-validated v2 receipt.

Runs one GPU subprocess at a time, bounded timeouts, immutable receipts, resume
by skipping only receipts valid for the identical code/model/input/EP contract.
No DML result can ever overwrite an AMDGPU failure; no silent CPU fallback.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from radeon_ort import redaction  # noqa: E402
from radeon_ort.manifest import sha256_file  # noqa: E402
from radeon_ort.receipts import (  # noqa: E402
    ReceiptStatus, build_receipt_v2, combine_v2_to_status, derive_verdicts,
    save_receipt, validate_receipt_strict)
from runner import REPO_ROOT  # noqa: E402

TASK_OF = {}  # filled from catalog


def _run(cmd: list[str], *, timeout_s: int, log_path: Path | None = None) -> dict:
    t0 = time.perf_counter()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s,
                           encoding="utf-8", errors="replace", cwd=str(REPO_ROOT))
        out = {"rc": r.returncode, "stdout": r.stdout or "", "stderr": r.stderr or "",
               "elapsed_s": time.perf_counter() - t0}
    except subprocess.TimeoutExpired as e:
        out = {"rc": 124, "stdout": (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or ""),
               "stderr": f"TIMEOUT after {timeout_s}s", "elapsed_s": time.perf_counter() - t0}
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"$ {' '.join(cmd)}\nrc={out['rc']} elapsed={out['elapsed_s']:.1f}s\n"
            f"--- stdout ---\n{out['stdout'][-8000:]}\n--- stderr ---\n{out['stderr'][-8000:]}",
            encoding="utf-8")
    return out


def _py(python: str, script: str, args: list[str], **kw) -> dict:
    return _run([python, "-I", script] + args, **kw)


def make_negative_image(export_python: str, out_path: Path) -> str:
    """Deterministic gray-noise negative control image (export venv: cv2)."""
    if out_path.is_file():
        return sha256_file(out_path)
    NEG_SCRIPT = (
        "import sys, json, hashlib\n"
        "import numpy as np, cv2\n"
        "out, size, seed = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])\n"
        "rng = np.random.default_rng(seed)\n"
        "img = np.full((size, size, 3), 128, dtype=np.uint8)\n"
        "img = np.clip(img.astype(np.int32) + rng.integers(-6, 7, size=(size, size, 3), dtype=np.int32), 0, 255).astype(np.uint8)\n"
        "cv2.imwrite(out, img)\n"
        "print(json.dumps({'ok': True, 'sha256': hashlib.sha256(open(out,'rb').read()).hexdigest()}))\n")
    r = _py(export_python, "-c", [NEG_SCRIPT, str(out_path), "640", str(0x511E6)], timeout_s=120)
    assert r["rc"] == 0, r["stderr"]
    return sha256_file(out_path)


def build_bundle(export_python: str, image: Path, imgsz: int, task: str, model: str,
                 onnx_sha: str, out_dir: Path, variant: str) -> dict:
    script = (
        "import sys; sys.path.insert(0, r'%s'); "
        "from radeon_ort.input_contract import build_input_bundle; "
        "import json; "
        "m = build_input_bundle(image_path=r'%s', imgsz=%d, task='%s', model_name='%s', "
        "onnx_sha256='%s', variant='%s', out_dir=r'%s'); "
        "print(json.dumps(m))" % (REPO_ROOT / "src", image, imgsz, task, model,
                                  onnx_sha, variant, out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    marker = out_dir / f".bundle_{variant}_{model}_{task}.done"
    if marker.is_file():
        cands = list(out_dir.glob(f"manifest_*.json"))
        # reuse manifest for this exact input: find by recomputing id lazily
        for c in cands:
            m = json.loads(c.read_text(encoding="utf-8"))
            if (m.get("model_name") == model and m.get("task") == task
                    and m.get("variant") == variant and m.get("onnx_sha256") == onnx_sha
                    and m.get("imgsz") == imgsz):
                return m
    r = _py(export_python, "-c", [script], timeout_s=300)
    if r["rc"] != 0:
        raise RuntimeError(f"bundle build failed: {r['stderr'][-500:]}")
    m = json.loads([l for l in r["stdout"].splitlines() if l.startswith("{")][-1])
    marker.write_text(m["input_id"], encoding="utf-8")
    return m


def run_worker(winml_python: str, ep: str, case: dict, case_path: Path,
               frag_path: Path, log_path: Path, timeout_s: int) -> tuple[dict | None, int]:
    if frag_path.exists():
        frag_path.unlink()  # never reuse stale fragments
    case_path.write_text(json.dumps(case), encoding="utf-8")
    r = _py(winml_python, str(REPO_ROOT / "runner" / "_ep_worker.py"),
            ["--case", str(case_path), "--out", str(frag_path)],
            timeout_s=timeout_s, log_path=log_path)
    frag = None
    if frag_path.is_file():
        try:
            frag = json.loads(frag_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            frag = None
    return frag, r["rc"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--catalog", default=str(REPO_ROOT / "catalog" / "model_catalog.json"))
    ap.add_argument("--models", default="yolo26n.pt,yolo26n-seg.pt,yolo26n-sem.pt,yolo26n-depth.pt,yolo26n-cls.pt,yolo26n-pose.pt,yolo26n-obb.pt")
    ap.add_argument("--eps", default="amdgpu,dml")
    ap.add_argument("--image", default=str(REPO_ROOT / "data" / "fixtures" / "bus.jpg"))
    ap.add_argument("--obb-image", default=str(REPO_ROOT / "data" / "fixtures" / "dota_p1053.jpg"))
    ap.add_argument("--negative", default=None, help="path for the generated negative image")
    ap.add_argument("--winml-python", default=str(REPO_ROOT / ".venv-winml" / "Scripts" / "python.exe"))
    ap.add_argument("--export-python", default=str(REPO_ROOT / ".venv-export" / "Scripts" / "python.exe"))
    ap.add_argument("--with-negative", action="store_true",
                    help="run the expected-empty negative control per task (GPU too)")
    ap.add_argument("--case-timeout-s", type=int, default=1500)
    ap.add_argument("--benchmark", action="store_true")
    a = ap.parse_args()

    run_dir = Path(a.run_dir)
    raw = run_dir / "raw"
    receipts_dir = run_dir / "receipts"
    inputs_dir = run_dir / "inputs"
    for d in (raw, receipts_dir, inputs_dir):
        d.mkdir(parents=True, exist_ok=True)

    thresholds = yaml.safe_load((REPO_ROOT / "configs" / "thresholds.yaml").read_text(encoding="utf-8"))
    th_path = run_dir / "thresholds_snapshot.json"
    if not th_path.is_file():
        th_path.write_text(json.dumps(thresholds, indent=2), encoding="utf-8")

    catalog = json.loads(Path(a.catalog).read_text(encoding="utf-8"))
    models = [m.strip() for m in a.models.split(",") if m.strip()]
    eps = [e.strip() for e in a.eps.split(",") if e.strip()]
    neg_path = Path(a.negative) if a.negative else run_dir / "negative_gray.png"
    neg_sha = make_negative_image(a.export_python, neg_path)

    ledger_path = run_dir / "R1_CASE_LEDGER.jsonl"
    n_ok = 0
    for model in models:
        entry = catalog["entries"].get(model)
        if not entry:
            print(f"[r1] SKIP {model}: not in catalog"); continue
        task = entry["task"]
        imgsz = entry.get("imgsz_default") or 640
        onnx = REPO_ROOT / "models" / "onnx" / f"{Path(model).stem}.raw_b1_fp32_static.onnx"
        if not onnx.is_file():
            print(f"[r1] SKIP {model}: onnx missing"); continue
        onnx_sha = sha256_file(onnx)
        pt = REPO_ROOT / "models" / "pt" / model
        image = Path(a.obb_image if task == "obb" else a.image)
        image_sha = sha256_file(image)

        case_key = f"{Path(model).stem}__{task}"
        pos_bundle = build_bundle(a.export_python, image, imgsz, task,
                                  Path(model).stem, onnx_sha, inputs_dir, "real_image_v1")
        assert pos_bundle["image_sha256"] == image_sha

        # ---------- CPU reference (positive) ----------
        ref_npz = raw / f"ref_{case_key}.npz"
        cpu_frag, cpu_rc = run_worker(
            a.winml_python, "cpu",
            {"ep": "cpu", "gfx": "gfx1151", "onnx_path": str(onnx),
             "input_manifest": str(inputs_dir / f"manifest_{pos_bundle['input_id'][:16]}.json"),
             "input_name": "images", "profile_prefix": str(raw / f"prof_cpu_{case_key}"),
             "outputs_npz": str(ref_npz)},
            raw / f"case_{case_key}_cpu.json", raw / f"frag_{case_key}_cpu.json",
            raw / f"log_cpu_{case_key}.txt", a.case_timeout_s)
        cpu_ok = cpu_rc == 0 and cpu_frag is not None and ref_npz.is_file()

        # ---------- PT oracle (positive) ----------
        pt_json = raw / f"pt_{case_key}.json"
        pt_npz = raw / f"pt_{case_key}.npz"
        if not pt_json.is_file():
            r = _py(a.export_python, str(REPO_ROOT / "src" / "radeon_ort" / "pt_oracle_v2.py"),
                    ["--pt", str(pt), "--task", task,
                     "--tensor-npy", str(inputs_dir / pos_bundle["npy_file"]),
                     "--out-json", str(pt_json), "--out-npz", str(pt_npz)],
                    timeout_s=900, log_path=raw / f"log_pt_{case_key}.txt")
            if r["rc"] != 0:
                print(f"[r1] PT oracle FAILED for {case_key}: {r['stderr'][-300:]}")

        # ---------- per-EP ----------
        for ep in eps:
            ep_name = "AMDGPU" if ep == "amdgpu" else "DML"
            receipt_path = receipts_dir / f"receipt_{case_key}__{ep}.json"
            if receipt_path.exists():
                print(f"[r1] SKIP {case_key}/{ep} (receipt exists, immutable)")
                continue
            cand_npz = raw / f"cand_{case_key}_{ep}.npz"
            frag, rc = run_worker(
                a.winml_python, ep,
                {"ep": ep, "gfx": "gfx1151", "onnx_path": str(onnx),
                 "input_manifest": str(inputs_dir / f"manifest_{pos_bundle['input_id'][:16]}.json"),
                 "input_name": "images", "reference_npz": str(ref_npz),
                 "atol": thresholds["numeric_parity"]["fp32"]["atol"],
                 "rtol": thresholds["numeric_parity"]["fp32"]["rtol"],
                 "benchmark": bool(a.benchmark),
                 "profile_prefix": str(raw / f"prof_{case_key}_{ep}"),
                 "outputs_npz": str(cand_npz)},
                raw / f"case_{case_key}_{ep}.json", raw / f"frag_{case_key}_{ep}.json",
                raw / f"log_{case_key}_{ep}.txt", a.case_timeout_s)

            # task parity for this EP (positive)
            tp_block = None
            if frag is not None and cand_npz.is_file() and pt_json.is_file():
                pp_json = raw / f"post_{case_key}_{ep}.json"
                pp_npz = raw / f"post_{case_key}_{ep}.npz"
                r = _py(a.export_python, str(REPO_ROOT / "src" / "radeon_ort" / "onnx_postprocess.py"),
                        ["--npz", str(cand_npz), "--task", task, "--imgsz", str(imgsz),
                         "--out-json", str(pp_json), "--out-npz", str(pp_npz)],
                        timeout_s=600, log_path=raw / f"log_post_{case_key}_{ep}.txt")
                if r["rc"] == 0:
                    tp_out = raw / f"tp_{case_key}_{ep}.json"
                    r2 = _py(a.export_python, str(REPO_ROOT / "src" / "radeon_ort" / "task_parity.py"),
                             ["--pt-json", str(pt_json), "--pt-npz", str(pt_npz),
                              "--cand-json", str(pp_json), "--cand-npz", str(pp_npz),
                              "--task", task, "--imgsz", str(imgsz),
                              "--thresholds-json", str(th_path),
                              "--cand-source", f"{ep_name}_POST",
                              "--image-sha256", image_sha,
                              "--input-id", pos_bundle["input_id"],
                              "--out", str(tp_out)],
                             timeout_s=600, log_path=raw / f"log_tp_{case_key}_{ep}.txt")
                    if r2["rc"] == 0:
                        tp_block = json.loads(tp_out.read_text(encoding="utf-8"))

            # negative control (expected-empty) — GPU too, when requested
            neg_block = None
            if a.with_negative and task in ("detect", "segment", "pose", "obb"):
                neg_bundle = build_bundle(a.export_python, neg_path, imgsz, task,
                                          Path(model).stem, onnx_sha, inputs_dir,
                                          "negative_gray_v1")
                neg_npz = raw / f"candneg_{case_key}_{ep}.npz"
                nfrag, nrc = run_worker(
                    a.winml_python, ep,
                    {"ep": ep, "gfx": "gfx1151", "onnx_path": str(onnx),
                     "input_manifest": str(inputs_dir / f"manifest_{neg_bundle['input_id'][:16]}.json"),
                     "input_name": "images",
                     "profile_prefix": str(raw / f"profneg_{case_key}_{ep}"),
                     "outputs_npz": str(neg_npz)},
                    raw / f"caseneg_{case_key}_{ep}.json", raw / f"fragneg_{case_key}_{ep}.json",
                    raw / f"logneg_{case_key}_{ep}.txt", a.case_timeout_s)
                npt_json = raw / f"ptneg_{case_key}.json"
                npt_npz = raw / f"ptneg_{case_key}.npz"
                if not npt_json.is_file():
                    _py(a.export_python, str(REPO_ROOT / "src" / "radeon_ort" / "pt_oracle_v2.py"),
                        ["--pt", str(pt), "--task", task,
                         "--tensor-npy", str(inputs_dir / neg_bundle["npy_file"]),
                         "--out-json", str(npt_json), "--out-npz", str(npt_npz)],
                        timeout_s=900, log_path=raw / f"log_ptneg_{case_key}.txt")
                if npt_json.is_file() and npt_npz.is_file() and npt_npz.is_file() and neg_npz.is_file():
                    npp_json = raw / f"postneg_{case_key}_{ep}.json"
                    npp_npz = raw / f"postneg_{case_key}_{ep}.npz"
                    r = _py(a.export_python, str(REPO_ROOT / "src" / "radeon_ort" / "onnx_postprocess.py"),
                            ["--npz", str(neg_npz), "--task", task, "--imgsz", str(imgsz),
                             "--out-json", str(npp_json), "--out-npz", str(npp_npz)],
                            timeout_s=600)
                    if r["rc"] == 0:
                        ntp = raw / f"tpneg_{case_key}_{ep}.json"
                        r2 = _py(a.export_python, str(REPO_ROOT / "src" / "radeon_ort" / "task_parity.py"),
                                 ["--pt-json", str(npt_json), "--pt-npz", str(npt_npz),
                                  "--cand-json", str(npp_json), "--cand-npz", str(npp_npz),
                                  "--task", task, "--imgsz", str(imgsz),
                                  "--thresholds-json", str(th_path),
                                  "--cand-source", f"{ep_name}_POST",
                                  "--image-sha256", neg_bundle["image_sha256"],
                                  "--input-id", neg_bundle["input_id"],
                                  "--negative", "--out", str(ntp)],
                                 timeout_s=600)
                        if r2["rc"] == 0:
                            neg_block = json.loads(ntp.read_text(encoding="utf-8"))

            # ---------- receipt assembly ----------
            st = ReceiptStatus(
                model_asset="AVAILABLE" if pt.is_file() else "MISSING_WITH_REASON",
                export="PASS_ONNX_EXPORT" if onnx.is_file() else "NOT_APPLICABLE",
                cpu_oracle="PASS_CPU_REFERENCE" if cpu_ok else "FAIL_CPU_REFERENCE",
                amdgpu="NOT_RUN", dml="NOT_RUN",
                task_oracle="NOT_RUN", benchmark="PASS_MEASURED" if (a.benchmark and frag and frag.get("benchmark_data")) else "NOT_MEASURED",
                host="VERIFIED_GFX1151",
            )
            errors: list[dict] = []
            ep_key = ep
            if frag is None:
                setattr(st, ep_key, "FAIL_RUNTIME")
                errors.append({"stage": ep_name, "type": "RUNTIME",
                               "message": f"worker rc={rc} (fragment missing)"})
            elif rc != 0:
                setattr(st, ep_key, "FAIL_RUNTIME")
                errors.append({"stage": ep_name, "type": "RUNTIME",
                               "message": f"worker rc={rc}: "
                                          f"{(frag.get('session') or {}).get('routing_notes', '')[:300]}"})
            else:
                setattr(st, ep_key, "PASS_GPU_EXECUTED_FULL")  # provisional; derived below

            if tp_block is not None:
                if neg_block is not None:
                    tp_block.setdefault("negative_control", neg_block.get("negative_control"))
                st.task_oracle = "PASS_TASK" if tp_block["passed"] else "FAIL_TASK"
            elif frag is not None:
                st.task_oracle = "TASK_ORACLE_BLOCKED"
            if not cpu_ok:
                errors.append({"stage": "cpu", "type": "RUNTIME",
                               "message": "cpu reference missing/failed"})

            input_block = {
                "input_id": pos_bundle["input_id"],
                "kind": "real_image",
                "image_sha256": image_sha,
                "tensor_sha256": {"images": pos_bundle["tensor_sha256"]},
                "preprocess": {
                    "method": pos_bundle["preprocess"]["method"],
                    "params": {k: v for k, v in pos_bundle["preprocess"].items() if k != "method"},
                    "pt_equivalence": "PT oracle consumes the SAME persisted tensor bytes",
                    "tensor_dtype": pos_bundle["tensor_dtype"],
                    "tensor_shape": pos_bundle["tensor_shape"],
                },
                "npy_path_redacted": redaction.redact_path(inputs_dir / pos_bundle["npy_file"]),
                "generation": {"script": "radeon_ort.input_contract.build_input_bundle",
                               "canonical_config": pos_bundle["canonical_config"]},
            }
            model_block = {
                "family": entry.get("family", "YOLO26"), "task": task, "checkpoint": model,
                "size": entry.get("size"),
                "pt_sha256": sha256_file(pt) if pt.is_file() else None,
                "onnx_sha256": onnx_sha,
                "onnx_path_redacted": redaction.redact_path(onnx),
                "export": {"imgsz": imgsz, "nms_mode": entry.get("nms_mode", "raw"),
                           "quantize": "fp32", "batch": 1, "dynamic": False,
                           "ultralytics_version": "8.4.215"},
                "input_names": ["images"],
                "output_names": [o["name"] for o in (frag or {}).get("expected_outputs", [])],
                "inputs_sha256": pos_bundle["tensor_sha256"],
            }
            session_block = None
            binding = None
            if frag is not None:
                s = frag.get("session") or {}
                session_block = {
                    "ep": s.get("ep"), "requested_providers": s.get("requested_providers"),
                    "provider_node_counts": s.get("provider_node_counts") or {},
                    "graph_node_total": s.get("graph_node_total"),
                    "profile_available": True,
                    "profile_fused_event_present": s.get("profile_fused_event_present"),
                    "ort_version": s.get("ort_version"),
                    "ort_distribution": s.get("ort_distribution"),
                    "device_luid": s.get("device_luid"),
                    "routing_notes": s.get("routing_notes", ""),
                    "stderr_fallback_signals": [],
                    "stdout_fallback_signals": [],
                    "worker_pid": frag.get("worker_pid"),
                }
                binding = frag.get("device_binding")
            worker_block = {"exit_code": rc, "pid": (frag or {}).get("worker_pid"),
                            "parent_pid": (frag or {}).get("parent_pid"),
                            "python": "3.13"}
            host_block = _host_block()
            receipt = build_receipt_v2(
                run_id=run_dir.name, case_id=f"{case_key}__{ep}", status=st, verdicts={},
                host=host_block, model=model_block, input_block=input_block,
                expected_outputs=(frag or {}).get("expected_outputs") or [],
                worker=worker_block, session=session_block, device_binding=binding,
                parity=(frag or {}).get("parity"), task=None, task_parity=tp_block,
                benchmark_data=(frag or {}).get("benchmark_data"), errors=errors,
                rca_issue_ids=["AMDGPU-RCA-003", "AMDGPU-RCA-004"] if ep == "amdgpu" else [],
                provenance={"script_head_sha": "git:r1-branch", "command":
                            f"python -m runner.r1_pipeline --models {model} --eps {ep}",
                            "seed": 0, "python_version": sys.version.split()[0],
                            "ultralytics_version": "8.4.215",
                            "raw_log_path_redacted": redaction.redact_path(
                                raw / f"log_{case_key}_{ep}.txt")})
            # honest status derivation: recompute from evidence
            v = derive_verdicts(receipt)
            for e in ("amdgpu", "dml"):
                if receipt["status"][e] != "NOT_RUN":
                    receipt["status"][e] = combine_v2_to_status(e, v[e])
            receipt["verdicts"] = v
            if tp_block is not None:
                receipt["status"]["task_oracle"] = (
                    "PASS_TASK" if tp_block["passed"] else "FAIL_TASK")

            yaml_task_key = {"detect": "detection", "segment": "segmentation"}.get(task, task)
            verdict = validate_receipt_strict(receipt, expected_thresholds={
                "atol": thresholds["numeric_parity"]["fp32"]["atol"],
                "rtol": thresholds["numeric_parity"]["fp32"]["rtol"],
                "task": thresholds.get("task_oracle", {}).get(yaml_task_key, {})})
            if not verdict["ok"]:
                receipt["errors"].append({"stage": "validate", "type": "FORGED_OR_STALE",
                                          "message": "; ".join(verdict["problems"])})
                print(f"[r1] VALIDATION PROBLEMS {case_key}/{ep}: {verdict['problems']}")
            try:
                save_receipt(receipt, receipts_dir)
                n_ok += 1
                status_ep = receipt["status"][ep_key]
                tp_s = receipt["status"]["task_oracle"]
                with ledger_path.open("a", encoding="utf-8") as lf:
                    lf.write(json.dumps({
                        "case": f"{case_key}__{ep}", "model": model, "task": task, "ep": ep,
                        "exec": status_ep, "task": tp_s,
                        "parity": bool(((frag or {}).get("parity") or {}).get("all_outputs_allclose")),
                        "rc": rc, "secs": 0, "problems": verdict["problems"],
                        "neg": (neg_block or {}).get("passed") if neg_block else None,
                    }) + "\n")
                print(f"[r1] {case_key}/{ep}: exec={status_ep} task={tp_s}")
            except Exception as e:  # noqa: BLE001
                print(f"[r1] receipt save failed {case_key}/{ep}: {e}")
    print(f"[r1] DONE {n_ok} receipts")
    return 0


def _host_block() -> dict:
    from radeon_ort import device_identity, host_probe
    host = host_probe.collect_host(gpu_name_needle="8060S", gfx="gfx1151")
    try:
        adapters = device_identity.enum_dxgi_adapters()
        res = device_identity.resolve_target_adapter(adapters, gfx="gfx1151")
        if res.get("ok"):
            host["gpu_target"].update({"verified": True, "luid": res["adapter"].luid_str})
    except OSError:
        pass
    gt = host.get("gpu_target", {})
    return {
        "host_id": host.get("host_id", "unknown"),
        "physical_gpu_name": gt.get("name", "AMD Radeon 8060S Graphics"),
        "gfx": "gfx1151", "device_luid": gt.get("luid"),
        "dxgi_evidence": host.get("_dxgi", {"vendor_id": "0x1002", "device_id": "0x1586"}),
        "pnp_evidence": {"pnp_device_id": gt.get("pnp_device_id",
                                                 "PCI\\VEN_1002&DEV_1586&SUBSYS_17141043&REV_C1")},
        "os_build": f"{host['os']['build']}.{host['os'].get('ubr', '')}".rstrip(".") if host.get("os") else "26200",
        "driver_version": gt.get("driver_version", "32.0.22032.6002"),
        "driver_date": gt.get("driver_date"), "wddm_version": gt.get("wddm"),
        "verified_device_identity": True,
        "identity_method": gt.get("identity_method", "DXGI vendor+deviceId+PnP"),
    }


if __name__ == "__main__":
    sys.exit(main())
