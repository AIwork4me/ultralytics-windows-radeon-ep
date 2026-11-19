"""python -m runner.soak — bounded representative soak with heartbeat + watchdog.

Runs one model repeatedly on one EP for --minutes, logging a heartbeat every 30s;
the watchdog aborts if no successful run completes for 180s. The output filename
includes the model tag so two soaks in one run_id can never overwrite each other:
`SOAK_<ep>__<tag>.json`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

from runner import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "src"))


def _pct(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p
    f, c = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", choices=["amdgpu", "dml"], default="amdgpu")
    ap.add_argument("--onnx", default=str(REPO_ROOT / "models" / "onnx" / "yolo26n.raw_b1_fp32_static.onnx"))
    ap.add_argument("--minutes", type=float, default=15)
    ap.add_argument("--heartbeat-s", type=int, default=30)
    ap.add_argument("--no-progress-timeout-s", type=int, default=180)
    ap.add_argument("--gfx", default=None,
                    help="gfx target; inferred from DXGI DID when omitted")
    ap.add_argument("--reports-root", default=str(REPO_ROOT / "reports"))
    ap.add_argument("--run-id", default="SOAK")
    ap.add_argument("--tag", default=None, help="unique case tag (default: onnx stem)")
    ap.add_argument("--input-npy", default=None,
                    help="deterministic persisted input tensor (R1 bundle); "
                         "default legacy: seeded random tensor rng(777)")
    a = ap.parse_args()

    if a.gfx is None:
        from radeon_ort.device_identity import (enum_dxgi_adapters,
                                                infer_gfx_from_adapters)
        inf = infer_gfx_from_adapters(enum_dxgi_adapters())
        if not inf.get("ok"):
            print(f"[soak] FAIL: {inf.get('reason')}")
            return 2
        a.gfx = inf["gfx"]

    tag = a.tag or Path(a.onnx).stem
    out_dir = Path(a.reports_root) / a.gfx / a.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"SOAK_{a.ep}__{tag}.json"
    if out.exists():
        print(f"[soak] FAIL: refusing to overwrite immutable soak evidence {out}")
        return 2

    import numpy as np
    if a.input_npy:
        feeds = {"images": np.load(a.input_npy).astype(np.float32)}
        input_sha = hashlib.sha256(feeds["images"].tobytes()).hexdigest()
        input_source = "persisted_npy"
    else:
        rng = np.random.default_rng(777)
        feeds = {"images": rng.random((1, 3, 640, 640), dtype=np.float32)}
        input_sha = "rng777"
        input_source = "rng777_legacy"

    t_start = time.time()
    deadline = t_start + a.minutes * 60
    runs, errors = 0, []
    lat_ms: list[float] = []
    last_progress = time.time()
    last_heartbeat = 0.0
    stage_markers: list[str] = []
    counts: dict = {}
    attribution = None
    status = "RUNNING"

    from radeon_ort import profiler_audit

    stage_markers.append("+0.0s session-create-begin")
    try:
        if a.ep == "amdgpu":
            from radeon_ort.amdgpu_session import (create_amdgpu_session,
                                                   ensure_amdgpu_registered)
            ensure_amdgpu_registered()
            result = create_amdgpu_session(a.onnx, enable_profiling=False,
                                           feeds_for_validation=feeds)
            sess = result.session
            counts = ((result.attestation.get("final_profile") or {})
                      .get("provider_node_counts") or {})
            attribution = result.attestation.get("attribution")
        else:
            from radeon_ort.device_identity import enum_dxgi_adapters, resolve_target_adapter
            from radeon_ort.dml_session import (create_dml_session,
                                                resolve_dml_device_id_for_adapter)
            res = resolve_target_adapter(enum_dxgi_adapters(), gfx=a.gfx)
            if not res.get("ok"):
                raise RuntimeError(f"BLOCKED_IDENTITY: {res.get('reason')}")
            dml_idx = resolve_dml_device_id_for_adapter(res["adapter"].index)
            sess = create_dml_session(a.onnx, device_id=dml_idx, enable_profiling=True,
                                      profile_prefix=str(out_dir / f"soak_{a.ep}_{tag}_probe"))
            sess.run(None, feeds)
            counts = profiler_audit.audit_profile(sess.end_profiling())["provider_node_counts"]
            attribution = "multi_node" if counts.get("DmlExecutionProvider", 0) > 1 else (
                "fused_single_node" if counts.get("DmlExecutionProvider", 0) == 1 else "none")
    except Exception as e:  # noqa: BLE001
        digest = {"ep": a.ep, "tag": tag, "onnx_redacted": Path(a.onnx).name,
                  "onnx_sha256": (hashlib.sha256(Path(a.onnx).read_bytes()).hexdigest()
                                  if Path(a.onnx).is_file() else None),
                  "status": "BLOCKED_EP", "error": f"{type(e).__name__}: {e}"[:600],
                  "duration_s": round(time.time() - t_start, 1), "runs": 0,
                  "provider_node_counts": counts, "input_source": input_source}
        out.write_text(json.dumps(digest, indent=2), encoding="utf-8")
        print(f"[soak] {a.ep}: BLOCKED_EP {e} -> {out}")
        return 1

    target_key = "AMDGPU" if a.ep == "amdgpu" else "DmlExecutionProvider"
    ep_nodes = int(counts.get(target_key, 0) or 0)
    cpu_nodes = int(counts.get("CPU", counts.get("CPUExecutionProvider", 0)) or 0)
    stage_markers.append(f"+{time.time()-t_start:.1f}s session-created providers={sess.get_providers()} "
                         f"{target_key}={ep_nodes} CPU={cpu_nodes}")
    if ep_nodes <= 0:
        status = "FAIL_NO_GPU_COMPUTE"

    while status == "RUNNING" and time.time() < deadline:
        t0 = time.perf_counter_ns()
        try:
            sess.run(None, feeds)
        except Exception as e:  # noqa: BLE001
            errors.append(f"+{time.time()-t_start:.1f}s {type(e).__name__}: {str(e)[:200]}")
            time.sleep(1.0)
        else:
            runs += 1
            lat_ms.append((time.perf_counter_ns() - t0) / 1e6)
            last_progress = time.time()
        now = time.time()
        if now - last_heartbeat >= a.heartbeat_s:
            stage_markers.append(f"+{now-t_start:.0f}s heartbeat runs={runs} err={len(errors)}")
            last_heartbeat = now
        if now - last_progress > a.no_progress_timeout_s:
            status = "ABORTED_NO_PROGRESS"
            break

    lat = sorted(lat_ms)
    digest = {
        "ep": a.ep, "tag": tag,
        "onnx_redacted": Path(a.onnx).name,
        "onnx_sha256": (hashlib.sha256(Path(a.onnx).read_bytes()).hexdigest()
                        if Path(a.onnx).is_file() else None),
        "input": input_sha, "input_source": input_source,
        "phase_note": "duration measures INFERENCE phase only; session-create "
                      "wall time appears in stage_markers",
        "duration_s": round(time.time() - t_start, 1),
        "status": status if status != "RUNNING" else "COMPLETED",
        "runs": runs, "errors": errors[:20],
        "provider_node_counts": dict(counts), "target_ep_nodes": ep_nodes,
        "cpu_nodes": cpu_nodes, "attribution": attribution,
        "gpu_attested": bool(ep_nodes > 0 and cpu_nodes == 0),
        "latency_ms": {
            "p50": _pct(lat, 0.50), "p95": _pct(lat, 0.95), "p99": _pct(lat, 0.99),
            "mean": statistics.fmean(lat) if lat else None,
            "std": statistics.pstdev(lat) if len(lat) > 1 else None,
            "samples": len(lat),
        },
        "stage_markers": stage_markers,
        "watchdog": {"no_progress_timeout_s": a.no_progress_timeout_s,
                     "heartbeat_s": a.heartbeat_s},
    }
    out.write_text(json.dumps(digest, indent=2), encoding="utf-8")
    print(f"[soak] {a.ep}/{tag}: {digest['status']} runs={runs} "
          f"p50={digest['latency_ms']['p50']}ms nodes={ep_nodes} -> {out}")
    return 0 if digest["status"] == "COMPLETED" and digest["gpu_attested"] else 1


if __name__ == "__main__":
    sys.exit(main())
