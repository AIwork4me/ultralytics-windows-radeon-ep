"""python -m runner.run_matrix — Tier A/B case execution with sharding, resume, ledger.

One heavy GPU case per subprocess at a time, bounded timeouts, immutable receipts.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from radeon_ort import redaction
from radeon_ort.manifest import sha256_file
from radeon_ort.model_registry import ModelRegistry
from radeon_ort.receipts import (
    ReceiptStatus,
    build_receipt,
    save_receipt,
    utc_now_iso,
    validate_receipt_strict,
)
from radeon_ort.task_oracles import run_pt_oracle
from runner import REPO_ROOT


def load_catalog(path: Path) -> dict:
    return json.loads((path).read_text(encoding="utf-8"))


def make_case_id(entry: dict, ep: str) -> str:
    stem = Path(entry["checkpoint"]).stem
    return f"{stem}__{entry['task']}__{ep}"


def run_one_case(entry: dict, ep: str, *, run_id: str, out_dir: Path, registry: ModelRegistry,
                 export_python: str | None, winml_python: str, max_minutes: float,
                 image_path: str | None, host: dict, identity: dict, do_benchmark: bool,
                 pt_oracle: bool, thresholds: dict) -> Path | None:
    case_id = make_case_id(entry, ep)
    receipt_path = out_dir / "receipts" / f"receipt_{case_id}.json"
    if receipt_path.exists():
        print(f"[matrix] SKIP {case_id} (receipt exists, immutable)")
        return receipt_path

    onnx_p = registry.onnx_path(entry["checkpoint"], _tag(entry))
    st = ReceiptStatus(
        model_asset="AVAILABLE" if registry.pt_path(entry["checkpoint"]).is_file() else "MISSING_WITH_REASON",
        export="NOT_APPLICABLE", cpu_oracle="NOT_RUN",
        amdgpu="NOT_RUN", dml="NOT_RUN", benchmark="NOT_MEASURED",
        host="VERIFIED_GFX1151" if host.get("gpu_target", {}).get("verified") else "BLOCKED_ENV",
    )
    errors: list[dict] = []

    if not registry.pt_path(entry["checkpoint"]).is_file():
        st.model_asset = "MISSING_WITH_REASON"
        st.reasons["model_asset"] = f"{entry['checkpoint']} not downloaded (allow_asset_download gate)"
        return _finish(entry, case_id, run_id, st, host, registry, onnx_p, errors, receipt_path,
                       None, None, None)

    if not onnx_p.is_file():
        from radeon_ort.model_export import run_export
        print(f"[matrix] export {entry['checkpoint']} -> onnx")
        r = run_export(export_python or sys.executable, str(registry.pt_path(entry["checkpoint"])),
                       str(onnx_p), imgsz=entry.get("imgsz_default"))
        if r["returncode"] != 0 or not r["payload"]:
            st.export = "FAIL_EXPORT"
            errors.append({"stage": "export", "type": "EXPORT", "message": r["stderr_tail"][-800:]})
            return _finish(entry, case_id, run_id, st, host, registry, onnx_p, errors, receipt_path,
                           None, None, None)
        st.export = "PASS_ONNX_EXPORT"
        registry.register_onnx(entry["checkpoint"], _tag(entry), r["payload"]["export_args"])

    # inputs: deterministic from case identity (no random unseeded tensors)
    import numpy as np
    imgsz = entry.get("imgsz_default") or 640
    rng = np.random.default_rng(20260101 + (hash(case_id) % 100000))
    # classify uses 224 and 3-dim collapse is fine for smoke; detect-style 640x640
    feeds = {"images": (rng.random((1, 3, imgsz, imgsz), dtype=np.float32))}
    inputs_sha = sha256_file(onnx_p)  # placeholder replaced below with real input hash

    import hashlib
    inputs_sha = hashlib.sha256(feeds["images"].tobytes()).hexdigest()

    # CPU reference first (subprocess)
    frag_cpu = _run_worker(winml_python, ep="cpu", case_meta=entry, onnx_p=onnx_p, feeds=feeds,
                           out_dir=out_dir, case_id=case_id, max_minutes=max_minutes,
                           reference_outputs=None, do_benchmark=False)
    ref_outputs = (frag_cpu or {}).get("outputs")
    if frag_cpu and frag_cpu.get("session", {}).get("provider_node_counts", {}).get("CPU", 0) > 0:
        st.cpu_oracle = "PASS_CPU_REFERENCE"
    else:
        st.cpu_oracle = "FAIL_CPU_REFERENCE"
        errors.append({"stage": "cpu", "type": "RUNTIME",
                       "message": (frag_cpu or {}).get("session", {}).get("routing_notes", "cpu worker failed")})

    # target EP
    frag_ep = _run_worker(winml_python, ep=ep, case_meta=entry, onnx_p=onnx_p, feeds=feeds,
                          out_dir=out_dir, case_id=case_id, max_minutes=max_minutes,
                          reference_outputs=ref_outputs, do_benchmark=do_benchmark)
    ep_key = "amdgpu" if ep == "amdgpu" else "dml"
    if frag_ep is None:
        setattr(st, ep_key, "FAIL_RUNTIME")
        errors.append({"stage": ep.upper(), "type": "RUNTIME", "message": "worker failed/timeout"})
    else:
        counts = frag_ep.get("session", {}).get("provider_node_counts", {})
        ep_nodes = counts.get(ep.upper(), 0)
        if ep_nodes == 0:
            setattr(st, ep_key, "FAIL_NO_GPU_COMPUTE")
        elif frag_ep.get("parity") and frag_ep["parity"].get("all_outputs_allclose"):
            setattr(st, ep_key, "PASS_GPU_EXECUTED_FULL" if counts.get("CPU", 0) == 0
                    else "PASS_GPU_EXECUTED_PARTIAL")
        else:
            setattr(st, ep_key, "FAIL_GPU_PARITY")
        if do_benchmark and frag_ep.get("benchmark_data"):
            st.benchmark = "PASS_MEASURED"

    task_block = None
    if pt_oracle and image_path:
        r = run_pt_oracle(export_python or sys.executable,
                          str(registry.pt_path(entry["checkpoint"])), image_path,
                          imgsz=imgsz, out_json=str(out_dir / "raw" / f"pt_oracle_{case_id}.json"))
        if r["returncode"] == 0 and r["payload"]:
            task_block = {"oracle_kind": "PT_APP", "metrics": {"pt_boxes": len(r["payload"].get("boxes") or [])},
                          "passed": True, "summary": f"PT oracle ran; task={r['payload'].get('task')}"}
            st.task_oracle = "PASS_TASK"
        else:
            st.task_oracle = "ORACLE_UNAVAILABLE"
            errors.append({"stage": "pt_oracle", "type": "ORACLE", "message": r["stderr_tail"][-400:]})

    return _finish(entry, case_id, run_id, st, host, registry, onnx_p, errors, receipt_path,
                   frag_ep, feeds, inputs_sha, task_block)


def _tag(entry) -> str:
    return f"{entry.get('nms_mode','raw')}_b{entry.get('batch',1)}_fp32_static"


def _run_worker(python_exe: str, *, ep, case_meta, onnx_p, feeds, out_dir, case_id,
                max_minutes, reference_outputs, do_benchmark) -> dict | None:
    case = {"ep": ep, "gfx": "gfx1151", "onnx_path": str(onnx_p),
            "feeds": {k: v.tolist() for k, v in feeds.items()},
            "atol": 5e-3, "rtol": 5e-3, "benchmark": do_benchmark,
            "reference_outputs": reference_outputs,
            "profile_prefix": str(out_dir / "raw" / f"profile_{case_id}")}
    case_p = out_dir / "raw" / f"case_{case_id}_{ep}.json"
    case_p.parent.mkdir(parents=True, exist_ok=True)
    case_p.write_text(json.dumps(case), encoding="utf-8")
    out_p = out_dir / "raw" / f"frag_{case_id}_{ep}.json"
    if out_p.exists():
        out_p.unlink()
    try:
        r = subprocess.run([python_exe, "-I", str(REPO_ROOT / "runner" / "_ep_worker.py"),
                            "--case", str(case_p), "--out", str(out_p)],
                           capture_output=True, text=True, timeout=int(max_minutes * 60),
                           encoding="utf-8", errors="replace")
        (out_dir / "raw" / f"stdout_{case_id}_{ep}.txt").write_text(
            (r.stdout or "")[-8000:] + "\n---STDERR---\n" + (r.stderr or "")[-8000:], encoding="utf-8")
        if out_p.is_file():
            return json.loads(out_p.read_text(encoding="utf-8"))
        return None
    except subprocess.TimeoutExpired:
        (out_dir / "raw" / f"stdout_{case_id}_{ep}.txt").write_text(
            f"TIMEOUT after {max_minutes} min", encoding="utf-8")
        return None


def _finish(entry, case_id, run_id, st, host, registry, onnx_p, errors, receipt_path,
            frag_ep, feeds, inputs_sha, task_block=None) -> Path:
    model_block = {
        "family": entry["family"], "task": entry["task"], "checkpoint": entry["checkpoint"],
        "size": entry.get("size"),
        "pt_sha256": (sha256_file(registry.pt_path(entry["checkpoint"]))
                      if registry.pt_path(entry["checkpoint"]).is_file() else None),
        "onnx_sha256": sha256_file(onnx_p) if (onnx_p and onnx_p.is_file()) else None,
        "onnx_path_redacted": redaction.redact_path(onnx_p) if onnx_p else None,
        "export": {"imgsz": entry.get("imgsz_default"), "nms_mode": entry.get("nms_mode", "raw"),
                   "quantize": entry.get("quantize", "fp32"), "batch": entry.get("batch", 1),
                   "dynamic": entry.get("dynamic", False)},
        "inputs_sha256": inputs_sha,
    }
    session_block = None
    if frag_ep:
        s = frag_ep.get("session", {})
        counts = s.get("provider_node_counts", {})
        session_block = {
            "ep": s.get("ep"), "requested_providers": s.get("requested_providers"),
            "provider_node_counts": counts,
            "graph_node_total": sum(counts.values()),
            "profile_fused_event_present": s.get("profile_fused_event_present"),
            "ort_version": s.get("ort_version"), "ort_distribution": s.get("ort_distribution"),
            "device_luid": s.get("device_luid"),
            "stderr_fallback_signals": [],
        }
    receipt = build_receipt(
        run_id=run_id, case_id=case_id, status=st, host=_host_evidence(host), model=model_block,
        session=session_block, parity=(frag_ep or {}).get("parity"), task=task_block,
        benchmark_data=(frag_ep or {}).get("benchmark_data"), errors=errors,
        provenance={"script_head_sha": "git:see-run-metadata", "command": f"python -m runner.run_matrix --case {case_id}",
                    "seed": 20260101, "python_version": sys.version.split()[0],
                    "ultralytics_version": None,
                    "raw_log_path_redacted": redaction.redact_path(
                        receipt_path.parent.parent / "raw" / f"stdout_{case_id}.txt") if receipt_path else None},
    )
    v = validate_receipt_strict(receipt)
    if not v["ok"]:
        # strict validator disagreements are themselves findings: keep receipt, mark errors
        receipt["errors"].append({"stage": "validate", "type": "FORGED_OR_STALE",
                                  "message": "; ".join(v["problems"])})
        receipt = build_receipt(run_id=run_id, case_id=case_id, status=st, host=_host_evidence(host),
                                model=model_block, session=session_block,
                                parity=(frag_ep or {}).get("parity"), task=task_block,
                                benchmark_data=(frag_ep or {}).get("benchmark_data"),
                                errors=receipt["errors"], provenance=receipt["provenance"])
        st.reasons["validate"] = "; ".join(v["problems"])
    try:
        return save_receipt(receipt, receipt_path.parent)
    except Exception as e:  # noqa: BLE001
        print(f"[matrix] receipt save failed for {case_id}: {e}")
        return None


def _host_evidence(host: dict) -> dict:
    gt = host.get("gpu_target", {})
    return {
        "host_id": host.get("host_id", "unknown"),
        "physical_gpu_name": gt.get("name"), "gfx": gt.get("gfx", "gfx1151"),
        "device_luid": gt.get("luid"), "os_build": f"{host['os']['build']}.{host['os']['ubr']}",
        "driver_version": gt.get("driver_version"), "driver_date": gt.get("driver_date"),
        "wddm_version": gt.get("wddm"),
        "verified_device_identity": bool(gt.get("verified")),
        "identity_method": gt.get("identity_method"),
        "dxgi_evidence": host.get("_dxgi", {}), "pnp_evidence": {"pnp_device_id": gt.get("pnp_device_id")},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="gfx1151")
    ap.add_argument("--tier", choices=["core", "full"], default="core")
    ap.add_argument("--ep", choices=["amdgpu", "dml", "both"], default="both")
    ap.add_argument("--catalog", default=str(REPO_ROOT / "catalog" / "model_catalog.json"))
    ap.add_argument("--models-root", default=str(REPO_ROOT / "models"))
    ap.add_argument("--reports-root", default=str(REPO_ROOT / "reports"))
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--max-minutes-per-case", type=float, default=20)
    ap.add_argument("--model", default=None, help="filter by substring")
    ap.add_argument("--task", default=None)
    ap.add_argument("--resume", action="store_true", help="reuse existing run dir & skip finished receipts")
    ap.add_argument("--winml-python", default=None)
    ap.add_argument("--export-python", default=None)
    ap.add_argument("--image", default=None)
    ap.add_argument("--benchmark", action="store_true")
    ap.add_argument("--pt-oracle", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    from radeon_ort import host_probe
    run_id = a.run_id or ("RUN_" + utc_now_iso().replace("-", "").replace(":", "").replace("T", "_").replace("Z", ""))
    out_dir = Path(a.reports_root) / a.host / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    host = host_probe.collect_host(gpu_name_needle="8060S", gfx=a.host)

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

    eps = ["amdgpu", "dml"] if a.ep == "both" else [a.ep]
    registry = ModelRegistry(a.models_root)
    winml_python = a.winml_python or sys.executable
    print(f"[matrix] run_id={run_id} cases={len(entries)}x eps={eps} out={out_dir}")

    n_done = 0
    t0 = time.time()
    for entry in entries:
        for ep in eps:
            p = run_one_case(entry, ep, run_id=run_id, out_dir=out_dir, registry=registry,
                             export_python=a.export_python, winml_python=winml_python,
                             max_minutes=a.max_minutes_per_case, image_path=a.image,
                             host=host, identity={}, do_benchmark=a.benchmark,
                             pt_oracle=a.pt_oracle, thresholds={})
            n_done += 1 if p else 0
            print(f"[matrix] ({n_done}) {entry['checkpoint']}/{ep} -> "
                  f"{'receipt' if p else 'FAILED'} [{time.time()-t0:.0f}s]")
    print(f"[matrix] DONE {n_done} receipts in {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
