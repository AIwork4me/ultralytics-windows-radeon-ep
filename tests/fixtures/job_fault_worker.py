"""Deterministic fault-injection worker for the R7-B Job Object tests.

This is a TEST FIXTURE, not part of the runtime. It exists so the process-tree
lifecycle can be exercised against real Windows behaviour instead of mocks:

  normal      print a line and exit 0
  fail        exit 7
  block       sleep until killed (a wedged EP)
  grandchild  spawn one long-lived child, record both pids, then block
  deep        spawn a chain of N long-lived descendants, record every pid
  partial     write half an output file, then block (a timeout that leaves junk)
  ignore      block and additionally install a no-op SIGTERM-ish handler

Every mode records its own pid (and any descendant pids) to --pidfile so the
parent can assert after cleanup that nothing survived.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _record(pidfile: Path, pids: list[int]) -> None:
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    prior: list[int] = []
    if pidfile.is_file():
        try:
            prior = json.loads(pidfile.read_text(encoding="utf-8")).get("pids", [])
        except Exception:  # noqa: BLE001
            prior = []
    merged = sorted(set(prior) | {int(p) for p in pids})
    pidfile.write_text(json.dumps({"pids": merged}), encoding="utf-8")


def _block(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(0.05)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True)
    ap.add_argument("--pidfile", default=None)
    ap.add_argument("--outfile", default=None)
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--seconds", type=float, default=3600.0)
    a = ap.parse_args()
    pidfile = Path(a.pidfile) if a.pidfile else None
    if pidfile:
        _record(pidfile, [os.getpid()])

    if a.mode == "normal":
        print("fault-worker: normal exit")
        return 0
    if a.mode == "fail":
        print("fault-worker: failing on purpose", file=sys.stderr)
        return 7
    if a.mode == "block":
        _block(a.seconds)
        return 0
    if a.mode == "ignore":
        _block(a.seconds)
        return 0
    if a.mode == "grandchild":
        child = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--mode", "block",
             "--seconds", str(a.seconds), "--pidfile", str(pidfile)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if pidfile:
            _record(pidfile, [child.pid])
        _block(a.seconds)
        return 0
    if a.mode == "deep":
        if a.depth > 0:
            child = subprocess.Popen(
                [sys.executable, os.path.abspath(__file__), "--mode", "deep",
                 "--depth", str(a.depth - 1), "--seconds", str(a.seconds),
                 "--pidfile", str(pidfile)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if pidfile:
                _record(pidfile, [child.pid])
        _block(a.seconds)
        return 0
    if a.mode == "partial":
        out = Path(a.outfile) if a.outfile else Path("partial.out")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"PARTIAL:" + bytes(64))
        print("fault-worker: partial output written, now wedged")
        _block(a.seconds)
        return 0
    print("fault-worker: unknown mode " + a.mode, file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
