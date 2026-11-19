r"""python -m runner.job_fault_evidence - R7-B fault-injection evidence.

Runs the process-tree lifecycle against the eight scenarios the mission
requires and writes one machine-readable record per scenario. This is evidence,
not a unit test: it is meant to be re-run on the physical host and archived.

  1 worker exits normally
  2 worker blocks forever            (timeout reclamation)
  3 worker leaves a live grandchild
  4 worker leaves multi-level descendants
  5 worker writes a partial output then times out
  6 job assignment is refused        (degraded container is recorded)
  7 the caller cancels the run
  8 the GPU attempt times out and the policy continues to DML

Exit 0 only when every scenario behaves as declared.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

from runner import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "src"))

from radeon_ort.receipts import utc_now_iso  # noqa: E402

NL = chr(10)
WORKER = REPO_ROOT / "tests" / "fixtures" / "job_fault_worker.py"


def _alive(pids, win_job) -> dict:
    time.sleep(0.4)
    return {str(p): win_job.process_alive(p) for p in pids}


def _spawn(tmp: Path, mode: str, timeout_s: float, win_job, *, extra=None):
    # absolute paths: the child runs with cwd=tmp, so a relative pidfile would
    # be re-rooted inside tmp and never found by the parent.
    tmp = Path(tmp).resolve()
    pidfile = tmp / (mode + ".pids.json")
    out = tmp / (mode + ".out")
    argv = [sys.executable, str(WORKER), "--mode", mode,
            "--pidfile", str(pidfile), "--outfile", str(out)]
    res = win_job.spawn_in_job(argv, cwd=tmp, timeout_s=timeout_s,
                               log_path=tmp / (mode + ".log"),
                               label="evidence-" + mode, **(extra or {}))
    pids = json.loads(pidfile.read_text())["pids"] if pidfile.is_file() else []
    return res, pids, pidfile, out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", default=None)
    a = ap.parse_args()

    from radeon_ort import win_job
    from radeon_ort import unified_runtime as ur

    work = (Path(a.work) if a.work
            else Path(a.out).parent.resolve() / "job_fault_work")
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    out = Path(a.out)
    scenarios: list[dict] = []

    def rec(name: str, expect: str, observed: dict, passed: bool, note: str = "") -> None:
        scenarios.append({"scenario": name, "expected": expect,
                          "observed": observed, "passed": bool(passed),
                          "note": note})
        print("[fault] " + name + " -> " + ("PASS" if passed else "FAIL"))

    # 1 -------------------------------------------------------------------
    r, pids, _, _ = _spawn(work, "normal", 20, win_job)
    d = r.as_dict()
    rec("normal_exit", "exit 0, job assigned, no survivors",
        {"returncode": d["returncode"], "job_assigned": d["job_assigned"],
         "survivors": d["survivors"], "alive": _alive(pids, win_job)},
        d["returncode"] == 0 and d["job_assigned"] and d["survivors"] == [])

    # 2 -------------------------------------------------------------------
    r, pids, _, _ = _spawn(work, "block", 1.5, win_job)
    d = r.as_dict()
    alive = _alive(pids, win_job)
    rec("wedged_worker_timeout", "timeout, job termination, no survivors",
        {"timed_out": d["timed_out"], "returncode": d["returncode"],
         "terminated_by_job": d["terminated_by_job"], "survivors": d["survivors"],
         "alive": alive},
        d["timed_out"] and d["terminated_by_job"] and d["survivors"] == []
        and not any(alive.values()))

    # 3 -------------------------------------------------------------------
    r, pids, _, _ = _spawn(work, "grandchild", 2.0, win_job)
    d = r.as_dict()
    alive = _alive(pids, win_job)
    rec("live_grandchild", "grandchild reclaimed with the parent",
        {"recorded_pids": pids, "alive": alive, "survivors": d["survivors"]},
        len(pids) >= 2 and not any(alive.values()) and d["survivors"] == [])

    # 4 -------------------------------------------------------------------
    r, pids, _, _ = _spawn(work, "deep", 2.0, win_job)
    d = r.as_dict()
    alive = _alive(pids, win_job)
    rec("multilevel_descendants", "every descendant reclaimed",
        {"recorded_pids": pids, "alive": alive, "survivors": d["survivors"]},
        len(pids) >= 3 and not any(alive.values()))

    # 5 -------------------------------------------------------------------
    r, pids, _, outfile = _spawn(work, "partial", 1.5, win_job)
    d = r.as_dict()
    partial = outfile.is_file() and outfile.read_bytes().startswith(b"PARTIAL")
    rec("partial_output_then_timeout",
        "timeout with the partial artefact retained and labelled",
        {"timed_out": d["timed_out"], "partial_file_present": partial,
         "bytes": outfile.stat().st_size if outfile.is_file() else 0},
        d["timed_out"] and partial)

    # 6 -------------------------------------------------------------------
    orig = win_job.JobObject.assign

    def _deny(self, pid):
        return {"ok": False, "error": "simulated denial"}

    win_job.JobObject.assign = _deny
    try:
        r, _, _, _ = _spawn(work, "normal", 20, win_job)
        d = r.as_dict()
    finally:
        win_job.JobObject.assign = orig
    rec("job_assign_failure", "run proceeds, degraded container recorded",
        {"returncode": d["returncode"], "job_assigned": d["job_assigned"],
         "job_error": d["job_error"], "notes": d["notes"]},
        d["returncode"] == 0 and d["job_assigned"] is False
        and d["job_error"] == "simulated denial")

    # 7 -------------------------------------------------------------------
    ev = threading.Event()
    pidfile = work / "cancel.pids.json"
    argv = [sys.executable, str(WORKER), "--mode", "grandchild",
            "--pidfile", str(pidfile)]
    th = threading.Thread(target=lambda: (time.sleep(1.0), ev.set()))
    th.start()
    r = win_job.spawn_in_job(argv, cwd=work, timeout_s=60,
                             log_path=work / "cancel.log", label="evidence-cancel",
                             cancel_event=ev)
    th.join()
    d = r.as_dict()
    pids = json.loads(pidfile.read_text())["pids"] if pidfile.is_file() else []
    alive = _alive(pids, win_job)
    rec("user_cancel", "cancelled, exit 130, tree reclaimed",
        {"cancelled": d["cancelled"], "returncode": d["returncode"],
         "survivors": d["survivors"], "alive": alive},
        d["cancelled"] and d["returncode"] == 130 and not any(alive.values()))

    # 8 -------------------------------------------------------------------
    m = work / "fault.onnx"
    m.write_bytes(b"onnx-fault")
    r8 = ur.predict(
        model_path=m, inputs={"images": __import__("numpy").zeros((1, 3, 8, 8),
                                                              dtype="float32")},
        policy="prefer_amdgpu_allow_dml", target_gfx="gfx1151",
        work_dir=work / "amd_timeout", run_id="FAULT_AMDGPU_TIMEOUT",
        worker_module="tests.fixtures.fake_ep_worker",
        worker_case_extra={"fake_mode": {"amdgpu": "hang", "cpu": "ok",
                                         "dml": "ok"}},
        timeout_s=2)
    amd = next((x for x in r8["attempts"] if x["ep"] == "amdgpu"), {})
    dml = next((x for x in r8["attempts"] if x["ep"] == "dml"), {})
    rec("amdgpu_timeout_then_dml",
        "AMDGPU timeout recorded, policy continues to an independent DML attempt",
        {"runtime_status": r8["runtime_status"],
         "amd_error": amd.get("error"), "amd_timed_out":
         (amd.get("process") or {}).get("timed_out"),
         "dml_ok": dml.get("ok"), "attempted_eps": r8["attempted_eps"],
         "fallback_reason": r8["fallback_reason"]},
        amd.get("error") == "TIMEOUT_2s"
        and (amd.get("process") or {}).get("timed_out") is True
        and dml.get("ok") is True
        and r8["runtime_status"] == ur.GPU_FULL_DML)

    summary = {
        "schema_version": 1,
        "captured_utc": utc_now_iso(),
        "scope": "Windows Job Object process-tree lifecycle fault injection",
        "host_container": "job_object",
        "scenarios": scenarios,
        "passed": sum(1 for s in scenarios if s["passed"]),
        "total": len(scenarios),
        "all_passed": all(s["passed"] for s in scenarios),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, default=str) + NL, encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("passed", "total", "all_passed")}, indent=1))
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())