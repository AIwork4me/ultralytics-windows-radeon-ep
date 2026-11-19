"""python -m runner.bench_repeats — independent repeated latency measurement.

For each repetition a FRESH session is created (so session-compile cost is not
hidden), profiled once to attest GPU node attribution, then measured:
session-create, first-run (cold), cold-start total, warm run p50/p95/p99/mean/std,
and batch-1 throughput. Pre/post processing is NOT_MEASURED here (the task
pipeline measures those separately); no wall-clock of the whole script is ever
reported as kernel latency.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from runner import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "src"))


def _pct(s: list[float], p: float) -> float | None:
    if not s:
        return None
    k = (len(s) - 1) * p
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx", required=True)
    ap.add_argument("--input-npy", required=True)
    ap.add_argument("--ep", choices=["dml", "amdgpu"], default="dml")
    ap.add_argument("--gfx", default=None,
                    help="gfx target; inferred from DXGI DID when omitted")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--samples", type=int, default=30)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    if a.gfx is None:
        from radeon_ort.device_identity import (enum_dxgi_adapters,
                                                infer_gfx_from_adapters)
        inf = infer_gfx_from_adapters(enum_dxgi_adapters())
        if not inf.get("ok"):
            out = {"status": "BLOCKED_IDENTITY", "ep": a.ep,
                   "error": inf.get("reason"), "onnx": Path(a.onnx).name}
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
            print(json.dumps(out))
            return 2
        a.gfx = inf["gfx"]

    import numpy as np

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    x = np.load(a.input_npy).astype(np.float32)
    feeds = {"images": x}
    reps = []
    for rep in range(a.reps):
        t0 = time.perf_counter_ns()
        if a.ep == "amdgpu":
            from radeon_ort.amdgpu_session import create_amdgpu_session, ensure_amdgpu_registered
            try:
                ensure_amdgpu_registered()
            except RuntimeError as e:
                out = {"status": "BLOCKED_EP", "ep": a.ep, "error": str(e),
                       "onnx": Path(a.onnx).name, "reps": a.reps}
                Path(a.out).parent.mkdir(parents=True, exist_ok=True)
                Path(a.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
                print(json.dumps(out))
                return 2
            res = create_amdgpu_session(a.onnx, enable_profiling=True,
                                        profile_prefix=str(Path(a.out).with_suffix(f".rep{rep}.prof")),
                                        feeds_for_validation=feeds)
            sess = res.session
            counts = ((res.attestation.get("final_profile") or {})
                      .get("provider_node_counts") or {})
        else:
            from radeon_ort.dml_session import create_dml_session, resolve_dml_device_id_for_adapter
            from radeon_ort.device_identity import enum_dxgi_adapters, resolve_target_adapter
            from radeon_ort import profiler_audit
            r = resolve_target_adapter(enum_dxgi_adapters(), gfx=a.gfx)
            if not r.get("ok"):
                raise RuntimeError(f"BLOCKED_IDENTITY: {r.get('reason')}")
            idx = resolve_dml_device_id_for_adapter(r["adapter"].index)
            sess = create_dml_session(
                a.onnx, device_id=idx, enable_profiling=True,
                profile_prefix=str(Path(a.out).with_suffix(f".rep{rep}.prof")))
            sess.run(None, feeds)
            counts = profiler_audit.audit_profile(sess.end_profiling())["provider_node_counts"]
        session_ms = (time.perf_counter_ns() - t0) / 1e6

        t1 = time.perf_counter_ns()
        sess.run(None, feeds)
        first_ms = (time.perf_counter_ns() - t1) / 1e6

        for _ in range(a.warmup):
            sess.run(None, feeds)
        samples = []
        for _ in range(a.samples):
            t = time.perf_counter_ns()
            sess.run(None, feeds)
            samples.append((time.perf_counter_ns() - t) / 1e6)
        samples.sort()
        key = "AMDGPU" if a.ep == "amdgpu" else "DmlExecutionProvider"
        reps.append({
            "rep": rep, "session_create_ms": session_ms, "first_run_ms": first_ms,
            "cold_start_total_ms": session_ms + first_ms,
            "warm_ms": {"p50": _pct(samples, .50), "p95": _pct(samples, .95),
                        "p99": _pct(samples, .99),
                        "mean": statistics.fmean(samples),
                        "std": statistics.pstdev(samples) if len(samples) > 1 else 0.0,
                        "samples": len(samples)},
            "throughput_img_s": 1000.0 / statistics.fmean(samples) if samples else None,
            "provider_node_counts": counts,
            "target_ep_nodes": int(counts.get(key, 0) or 0),
            "cpu_nodes": int(counts.get("CPU", counts.get("CPUExecutionProvider", 0)) or 0),
            "ep_devices": [d.ep_name for d in __import__("onnxruntime").get_ep_devices()],
        })

    def agg(field: str):
        vals = [r["warm_ms"][field] for r in reps]
        return {"mean": statistics.fmean(vals), "min": min(vals), "max": max(vals)}

    from radeon_ort.device_identity import (enum_dxgi_adapters,
                                            memory_provenance,
                                            resolve_target_adapter)
    dev_res = resolve_target_adapter(enum_dxgi_adapters(), gfx=a.gfx)
    dev = dev_res.get("adapter") if dev_res.get("ok") else None
    out = {
        "schema_version": 1, "ep": a.ep, "onnx": Path(a.onnx).name,
        "input_npy": Path(a.input_npy).name,
        "gfx": a.gfx,
        "device": {
            "state": "EP_DEVICE_SELECTED" if dev is not None else "BLOCKED_IDENTITY",
            "luid": dev.luid_str if dev else None,
            "vendor_id": f"0x{dev.vendor_id:04X}" if dev else None,
            "device_id": f"0x{dev.device_id:04X}" if dev else None,
            "dxgi_corroboration_only": True,
            "note": "DML/AMDGPU expose no EP-reported LUID on this build; the "
                    "LUID above is host-side DXGI evidence, not EP-reported",
        },
        "memory": (memory_provenance(dev)
                   if dev is not None else {"measurement_status": "NOT_MEASURED"}),
        "warmup": a.warmup, "samples_per_rep": a.samples, "repetitions": a.reps,
        "pre_post_processing": "NOT_MEASURED (separate pipeline phases)",
        "timer": "time.perf_counter_ns",
        "reps": reps,
        "aggregate_warm_p50": agg("p50"), "aggregate_warm_p95": agg("p95"),
        "gpu_attested_all_reps": all(r["target_ep_nodes"] > 0 and r["cpu_nodes"] == 0
                                     for r in reps),
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({"ep": a.ep, "p50": out["aggregate_warm_p50"],
                      "p95": out["aggregate_warm_p95"],
                      "gpu": out["gpu_attested_all_reps"]}))
    return 0 if out["gpu_attested_all_reps"] else 1


if __name__ == "__main__":
    sys.exit(main())
