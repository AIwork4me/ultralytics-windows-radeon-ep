"""python -m runner.soak — bounded representative soak with heartbeat + watchdog.

Runs one passing model repeatedly on one EP for --minutes, logging a heartbeat
every 30s, watchdog aborts if no successful run for 180s. Stage markers around
sess.run. Writes reports/<gfx>/<run_id>/SOAK_<ep>.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from runner import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", choices=["amdgpu", "dml"], default="amdgpu")
    ap.add_argument("--onnx", default=str(REPO_ROOT / "models" / "onnx" / "yolo26n.raw_b1_fp32_static.onnx"))
    ap.add_argument("--minutes", type=float, default=15)
    ap.add_argument("--heartbeat-s", type=int, default=30)
    ap.add_argument("--no-progress-timeout-s", type=int, default=180)
    ap.add_argument("--gfx", default="gfx1151")
    ap.add_argument("--reports-root", default=str(REPO_ROOT / "reports"))
    ap.add_argument("--run-id", default="SOAK")
    a = ap.parse_args()

    out_dir = Path(a.reports_root) / a.gfx / a.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    import numpy as np
    rng = np.random.default_rng(777)
    feeds = {"images": rng.random((1, 3, 640, 640), dtype=np.float32)}

    t_start = time.time()
    deadline = t_start + a.minutes * 60
    runs, errors = 0, []
    lat_ms: list[float] = []
    last_progress = time.time()
    last_heartbeat = 0.0
    stage_markers: list[str] = []

    from radeon_ort import windows_ml_ep_catalog
    from radeon_ort import profiler_audit

    stage_markers.append("+0.0s session-create-begin")
    if a.ep == "amdgpu":
        from radeon_ort.amdgpu_session import (create_amdgpu_session,
                                               ensure_amdgpu_registered)
        ensure_amdgpu_registered()
        result = create_amdgpu_session(a.onnx, enable_profiling=False,
                                       feeds_for_validation=feeds)
        sess = result.session
    else:
        from radeon_ort.dml_session import create_dml_session
        from radeon_ort.device_identity import enum_dxgi_adapters, resolve_target_adapter
        res = resolve_target_adapter(enum_dxgi_adapters(), gfx=a.gfx)
        dml_idx = 0
        if res.get("ok"):
            from radeon_ort.dml_session import resolve_dml_device_id_for_adapter
            dml_idx = resolve_dml_device_id_for_adapter(res["adapter"].index)
        sess = create_dml_session(a.onnx, device_id=dml_idx, enable_profiling=False)
    stage_markers.append(f"+{time.time()-t_start:.1f}s session-created providers={sess.get_providers()}")

    status = "RUNNING"
    while time.time() < deadline:
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
            p50 = sorted(lat_ms)[len(lat_ms)//2] if lat_ms else -1
            stage_markers.append(f"+{now-t_start:.0f}s heartbeat runs={runs} p50={p50:.1f}ms err={len(errors)}")
            last_heartbeat = now
        if now - last_progress > a.no_progress_timeout_s:
            status = "ABORTED_NO_PROGRESS"
            break

    lat = sorted(lat_ms)
    digest = {
        "ep": a.ep, "onnx_redacted": str(a.onnx).split("\\")[-1],
        "duration_s": round(time.time() - t_start, 1),
        "status": status if status != "RUNNING" else "COMPLETED",
        "runs": runs, "errors": errors[:20],
        "latency_ms": {"p50": lat[len(lat)//2] if lat else None,
                       "p95": lat[int(len(lat)*0.95)] if lat else None,
                       "mean": sum(lat)/len(lat) if lat else None},
        "stage_markers": stage_markers,
        "watchdog": {"no_progress_timeout_s": a.no_progress_timeout_s,
                     "heartbeat_s": a.heartbeat_s},
    }
    out = out_dir / f"SOAK_{a.ep}.json"
    out.write_text(json.dumps(digest, indent=2), encoding="utf-8")
    print(f"[soak] {a.ep}: {digest['status']} runs={runs} p50={digest['latency_ms']['p50']}ms -> {out}")
    return 0 if digest["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    sys.exit(main())
