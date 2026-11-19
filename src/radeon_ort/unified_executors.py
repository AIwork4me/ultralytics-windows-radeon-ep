"""unified_executors - subprocess-isolated executors for unified_runtime.predict.

Every EP attempt runs in a fresh interpreter (runner._ep_worker) with:

  * its own case JSON, fragment JSON, profile prefix and log file,
  * a wall-clock timeout enforced by the parent (kill + BLOCKED/TIMEOUT),
  * an independent CPU ORT reference computed first, so parity is always
    "this GPU output vs a CPU run of the same ONNX and the same bytes".

No attempt shares interpreter state with another attempt, so a wedged EP cannot
poison the caller and a stale provider registration cannot leak between EPs.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from .unified_runtime import Attempt, _attempt_from_fragment


class WorkerExecutor:
    def __init__(self, *, gfx: str | None, winml_python: str | None,
                 work_dir: Path, timeout_s: int = 900,
                 repo_root: str | Path = "."):
        self.gfx = gfx
        self.python = winml_python or sys.executable
        self.work = Path(work_dir)
        self.timeout_s = int(timeout_s)
        self.root = Path(repo_root)
        self.work.mkdir(parents=True, exist_ok=True)
        (self.work / "logs").mkdir(exist_ok=True)
        self._feeds = self.work / "inputs.npz"
        self._cpu_ref: Path | None = None
        self._outputs: dict[str, dict] = {}

    # -- worker plumbing ---------------------------------------------------
    def _write_feeds(self, inputs: dict) -> Path:
        if not self._feeds.is_file():
            np.savez(self._feeds, **{k: np.asarray(v) for k, v in inputs.items()})
        return self._feeds

    def _run_worker(self, ep: str, model_path: str, *,
                    reference: Path | None) -> Attempt:
        case_path = self.work / f"case_{ep}.json"
        frag_path = self.work / f"frag_{ep}.json"
        out_npz = self.work / f"out_{ep}.npz"
        log_path = self.work / "logs" / f"worker_{ep}.log"
        if frag_path.exists():
            frag_path.unlink()
        case = {
            "ep": ep,
            "gfx": self.gfx,
            "onnx_path": str(model_path),
            "feeds_npz": str(self._feeds),
            "outputs_npz": str(out_npz),
            "profile_prefix": str(self.work / f"prof_{ep}"),
        }
        if reference is not None:
            case["reference_npz"] = str(reference)
            case["atol"] = 5e-3
            case["rtol"] = 5e-3
        case_path.write_text(json.dumps(case, indent=2), encoding="utf-8")
        cmd = [self.python, "-m", "runner._ep_worker",
               "--case", str(case_path), "--out", str(frag_path)]
        rc = -1
        timed_out = False
        with log_path.open("wb") as log:
            try:
                proc = subprocess.run(cmd, cwd=str(self.root), stdout=log,
                                      stderr=subprocess.STDOUT,
                                      timeout=self.timeout_s, check=False)
                rc = int(proc.returncode)
            except subprocess.TimeoutExpired as e:
                timed_out = True
                rc = 124
                log.write(f"\nTIMEOUT after {self.timeout_s}s: {e}\n".encode())
        frag: dict = {}
        if frag_path.is_file():
            try:
                frag = json.loads(frag_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                frag = {}
        attempt = _attempt_from_fragment(ep, frag, exit_code=rc,
                                         log_path=str(log_path))
        if timed_out:
            attempt.ok = False
            attempt.status = "BLOCKED"
            attempt.error = f"TIMEOUT_{self.timeout_s}s"
        elif rc != 0 and not attempt.error:
            attempt.error = f"worker_exit={rc}"
        if out_npz.is_file():
            self._outputs[ep] = dict(np.load(out_npz))
            attempt.outputs = self._outputs[ep]
        return attempt

    # -- public API --------------------------------------------------------
    def execute(self, *, ep: str, model_path: str, inputs: dict,
                reference: dict | None = None) -> Attempt:
        self._write_feeds(inputs)
        if ep == "cpu":
            attempt = self._run_worker("cpu", model_path, reference=None)
            if attempt.ok and self._outputs.get("cpu"):
                self._cpu_ref = self.work / "out_cpu.npz"
            return attempt
        if self._cpu_ref is None or not self._cpu_ref.is_file():
            cpu = self._run_worker("cpu", model_path, reference=None)
            if not cpu.ok:
                return Attempt(ep=ep, ok=False, status="BLOCKED",
                               error="CPU_REFERENCE_FAILED: " + str(cpu.error),
                               exit_code=cpu.exit_code, log_path=cpu.log_path)
            self._cpu_ref = self.work / "out_cpu.npz"
        return self._run_worker(ep, model_path, reference=self._cpu_ref)
