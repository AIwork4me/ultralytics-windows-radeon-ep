"""R7-B: Windows Job Object process-tree lifecycle, exercised for real.

These are not mocks: every case spawns actual Windows processes inside an
actual Job Object and then asserts on the observed pid/parent tree. Skipped on
non-Windows because the whole mechanism is Win32-specific.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from radeon_ort import win_job

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects only")

WORKER = Path(__file__).resolve().parents[1] / "fixtures" / "job_fault_worker.py"


def _run(tmp_path: Path, mode: str, timeout_s: float, **kw) -> tuple[dict, list[int]]:
    pidfile = tmp_path / (mode + ".pids.json")
    out = tmp_path / (mode + ".out")
    argv = [sys.executable, str(WORKER), "--mode", mode,
            "--pidfile", str(pidfile), "--outfile", str(out)]
    log_path = tmp_path / (mode + ".log")
    res = win_job.spawn_in_job(argv, cwd=str(tmp_path), timeout_s=timeout_s,
                               label="test-" + mode, log_path=log_path, **kw)
    pids = json.loads(pidfile.read_text())["pids"] if pidfile.is_file() else []
    return res.as_dict(), pids


def _alive(pids: list[int]) -> dict[int, bool]:
    time.sleep(0.4)
    return {p: win_job.process_alive(p) for p in pids}


def test_normal_exit_is_clean_and_contained(tmp_path):
    res, pids = _run(tmp_path, "normal", 15)
    assert res["returncode"] == 0
    assert res["timed_out"] is False
    assert res["job_assigned"] is True
    assert res["container"] == "job_object"
    assert res["survivors"] == []
    assert pids and not any(_alive(pids).values())


def test_nonzero_exit_is_propagated(tmp_path):
    res, _ = _run(tmp_path, "fail", 15)
    assert res["returncode"] == 7
    assert res["timed_out"] is False


def test_wedged_worker_is_killed_by_the_job(tmp_path):
    res, pids = _run(tmp_path, "block", 1.5)
    assert res["timed_out"] is True
    assert res["returncode"] == 124
    assert res["terminated_by_job"] is True
    assert res["survivors"] == []
    assert pids and not any(_alive(pids).values())


def test_live_grandchild_is_reclaimed(tmp_path):
    res, pids = _run(tmp_path, "grandchild", 2.0)
    assert res["timed_out"] is True
    assert res["survivors"] == []
    alive = _alive(pids)
    assert alive and not any(alive.values()), alive


def test_multi_level_descendants_are_reclaimed(tmp_path):
    res, pids = _run(tmp_path, "deep", 2.0)
    assert res["timed_out"] is True
    assert res["survivors"] == []
    alive = _alive(pids)
    assert len(pids) >= 3, pids
    assert not any(alive.values()), alive


def test_partial_output_survives_as_failed_evidence(tmp_path):
    res, pids = _run(tmp_path, "partial", 1.5)
    out = tmp_path / "partial.out"
    assert res["timed_out"] is True
    assert out.is_file() and out.read_bytes().startswith(b"PARTIAL")
    assert not any(_alive(pids).values())


def test_job_assignment_failure_is_recorded_not_hidden(tmp_path, monkeypatch):
    monkeypatch.setattr(win_job.JobObject, "assign",
                        lambda self, pid: {"ok": False, "error": "simulated denial"})
    res, _ = _run(tmp_path, "normal", 15)
    # the run still happens, but the degraded container is recorded verbatim
    assert res["returncode"] == 0
    assert res["job_assigned"] is False
    assert res["job_error"] == "simulated denial"
    assert any("assignment failed" in n for n in res["notes"])


def test_cancellation_terminates_the_tree(tmp_path):
    import threading

    ev = threading.Event()
    pidfile = tmp_path / "cancel.pids.json"
    argv = [sys.executable, str(WORKER), "--mode", "grandchild",
            "--pidfile", str(pidfile)]

    def _fire():
        time.sleep(1.0)
        ev.set()

    th = threading.Thread(target=_fire)
    th.start()
    res = win_job.spawn_in_job(argv, cwd=str(tmp_path), timeout_s=60,
                               label="test-cancel", cancel_event=ev)
    th.join()
    d = res.as_dict()
    assert d["cancelled"] is True
    assert d["returncode"] == 130
    assert d["survivors"] == []
    pids = json.loads(pidfile.read_text())["pids"] if pidfile.is_file() else []
    assert pids and not any(_alive(pids).values())


def test_unrelated_processes_are_not_touched(tmp_path):
    """A sibling process outside the job must survive a timeout in the job."""
    bystander = subprocess.Popen(
        [sys.executable, str(WORKER), "--mode", "block"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        res, pids = _run(tmp_path, "grandchild", 1.5)
        assert res["timed_out"] is True
        assert win_job.process_alive(bystander.pid) is True
        assert not any(_alive(pids).values())
    finally:
        bystander.kill()
        bystander.wait(timeout=10)


def test_spawn_resume_is_robust_under_repetition(tmp_path):
    """Regression: a Toolhelp snapshot can miss the just-created thread.

    If the child is never resumed it stays suspended until its wall-clock
    timeout - a real wedge seen during R7 evidence generation. The resume path
    retries and falls back to NtResumeProcess, so repeated spawns must all run.
    """
    failed = []
    for i in range(15):
        pidfile = tmp_path / ("rep%d.pids.json" % i)
        argv = [sys.executable, str(WORKER), "--mode", "normal",
                "--pidfile", str(pidfile)]
        res = win_job.spawn_in_job(argv, cwd=str(tmp_path), timeout_s=30,
                                   label="rep%d" % i)
        if res.returncode != 0 or not res.resume.get("resumed_threads"):
            failed.append({"i": i, "rc": res.returncode, "resume": res.resume,
                           "notes": res.notes})
    assert failed == [], failed


def test_job_reports_accounting_after_cleanup(tmp_path):
    res, _ = _run(tmp_path, "block", 1.2)
    before = res["job_stats_before_cleanup"]
    after = res["job_stats_after_cleanup"]
    assert before.get("available") is True
    assert before.get("active_processes", 0) >= 1
    assert after.get("active_processes", 1) == 0