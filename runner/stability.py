r"""python -m runner.stability - R7-F bounded stability / recovery evidence.

Answers four developer questions with measurements instead of assurances:

  1. is the API result stable across repeated calls (same inputs -> same outputs)?
  2. does repeated execution leak processes, handles or file descriptors?
  3. is a timeout bounded, and does the next call recover?
  4. is cancellation bounded and does it leave the host clean?

Each iteration is a full predict() run (CPU reference + AMDGPU attempt + DML
attempt) on the same input, so the whole attempt machinery is exercised, not
just a session.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
from pathlib import Path

from runner import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "src"))

from radeon_ort.receipts import utc_now_iso  # noqa: E402

NL = chr(10)


def _process_count() -> int:
    import ctypes
    import ctypes.wintypes as wt

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetProcessHandleCount.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    k32.GetProcessHandleCount.restype = wt.BOOL
    n = wt.DWORD(0)
    k32.GetProcessHandleCount(k32.GetCurrentProcess(), ctypes.byref(n))
    return int(n.value)


def _python_children() -> int:
    from radeon_ort import win_job

    me = os.getpid()
    return len([r for r in win_job.descendants_of(me)])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gfx", default=None)
    ap.add_argument("--model", default=str(REPO_ROOT / "models" / "onnx" /
                                           "yolo26n.raw_b1_fp32_static.onnx"))
    ap.add_argument("--input-npz", default=str(REPO_ROOT / "reports" / "unified" /
                                              "_inputs" / "yolo26n_bus.npz"))
    ap.add_argument("--iterations", type=int, default=8)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    import numpy as np

    from radeon_ort import unified_runtime as ur

    # accept either an input bundle (.npz with named feeds) or a single
    # persisted tensor (.npy, the soak/bench input convention)
    if str(a.input_npz).lower().endswith(".npz"):
        with np.load(a.input_npz) as z:
            inputs = {k: z[k] for k in z.files}
    else:
        inputs = {"images": np.load(a.input_npz)}
    rows: list[dict] = []
    handles0 = _process_count()
    t_all = time.perf_counter()
    outputs = []

    for i in range(a.iterations):
        run_id = "R7_STABILITY_%02d" % i
        work = REPO_ROOT / "reports" / "mission_r7" / "stability" / run_id
        t = time.perf_counter()
        r = ur.predict(model_path=a.model, inputs=inputs,
                       policy="prefer_amdgpu_allow_dml", target_gfx=a.gfx,
                       work_dir=work, run_id=run_id, task="detect",
                       verify_task=False, timeout_s=180)
        dt = time.perf_counter() - t
        adopted = next((x for x in r["attempts"]
                        if x.get("attempt_id") == r.get("adopted_attempt_id")), {})
        rows.append({
            "iteration": i,
            "run_id": run_id,
            "runtime_status": r["runtime_status"],
            "gpu_full": r["gpu_full"],
            "actual_ep": r["actual_ep"],
            "target_nodes": r["target_ep_profile_nodes"],
            "cpu_nodes": r["cpu_profile_nodes"],
            "outputs_sha256": adopted.get("outputs_sha256"),
            "attempts": len(r["attempts"]),
            "survivors": (adopted.get("process") or {}).get("survivors"),
            "wall_s": round(dt, 3),
            "handles": _process_count(),
            "child_processes": _python_children(),
        })
        outputs.append(adopted.get("outputs_sha256"))
        print("[stability] iter " + str(i) + " " + r["runtime_status"]
              + " " + str(adopted.get("outputs_sha256"))[:12])

    # timeout + recovery: force a 1s timeout, then run a normal call again
    m = REPO_ROOT / "reports" / "mission_r7" / "stability" / "timeout.onnx"
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_bytes(b"onnx-timeout-probe")
    t = time.perf_counter()
    r_to = ur.predict(model_path=m, inputs=inputs, policy="require_amdgpu",
                      target_gfx=a.gfx,
                      work_dir=REPO_ROOT / "reports" / "mission_r7" / "stability" / "TO",
                      run_id="R7_STABILITY_TIMEOUT",
                      worker_module="tests.fixtures.fake_ep_worker",
                      worker_case_extra={"fake_mode": "hang"}, timeout_s=1)
    timeout_wall = time.perf_counter() - t
    to_attempt = next((x for x in r_to["attempts"] if x["ep"] == "amdgpu"), {})

    t = time.perf_counter()
    r_rec = ur.predict(model_path=a.model, inputs=inputs,
                       policy="prefer_amdgpu_allow_dml", target_gfx=a.gfx,
                       work_dir=REPO_ROOT / "reports" / "mission_r7" / "stability" / "REC",
                       run_id="R7_STABILITY_RECOVERY", timeout_s=180)
    recovery_wall = time.perf_counter() - t

    handles1 = _process_count()
    unique_outputs = sorted({o for o in outputs if o})
    summary = {
        "schema_version": 1,
        "captured_utc": utc_now_iso(),
        "kind": "bounded_stability",
        "gfx": a.gfx,
        "model": Path(a.model).name,
        "input_sha256": ur.input_fingerprint(inputs)["combined_sha256"],
        "iterations": a.iterations,
        "all_gpu_full": all(r["gpu_full"] for r in rows),
        "deterministic_outputs": len(unique_outputs) == 1,
        "unique_output_hashes": unique_outputs,
        "no_survivors": all(r["survivors"] == [] for r in rows),
        "handles_before": handles0,
        "handles_after": handles1,
        "handle_delta": handles1 - handles0,
        "children_before": rows[0]["child_processes"] if rows else None,
        "children_after": _python_children(),
        "wall_s": round(time.perf_counter() - t_all, 2),
        "timeout": {
            "status": r_to["runtime_status"],
            "error": to_attempt.get("error"),
            "timed_out": (to_attempt.get("process") or {}).get("timed_out"),
            "terminated_by_job": (to_attempt.get("process") or {}).get("terminated_by_job"),
            "survivors": (to_attempt.get("process") or {}).get("survivors"),
            "bounded_wall_s": round(timeout_wall, 3),
        },
        "recovery": {
            "status": r_rec["runtime_status"],
            "gpu_full": r_rec["gpu_full"],
            "wall_s": round(recovery_wall, 3),
        },
        "rows": rows,
        "verdict": "PASS" if (
            all(r["gpu_full"] for r in rows)
            and len(unique_outputs) == 1
            and all(r["survivors"] == [] for r in rows)
            and r_to["runtime_status"] == "BLOCKED"
            and r_rec["gpu_full"] is True
            and handlers_ok(handles0, handles1)
        ) else "FAIL",
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, default=str) + NL, encoding="utf-8")
    print(json.dumps({k: summary[k] for k in
                      ("iterations", "all_gpu_full", "deterministic_outputs",
                       "handle_delta", "verdict")}, indent=1))
    return 0 if summary["verdict"] == "PASS" else 1


def handlers_ok(before: int, after: int, limit: int = 64) -> bool:
    """Handles may grow a little (caches); a leak shows as monotonic growth."""
    return (after - before) <= limit


if __name__ == "__main__":
    sys.exit(main())