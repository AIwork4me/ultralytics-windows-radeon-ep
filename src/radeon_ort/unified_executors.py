"""unified_executors - subprocess-isolated executors for unified_runtime.predict.

Every EP attempt runs in a fresh interpreter inside its own Windows Job Object
(mission R7-B) with:

  * a private attempt directory holding the case JSON, fragment JSON, candidate
    outputs and log - never a shared, reusable path (mission R7-C);
  * a content-addressed input bundle bound to the current input fingerprint;
  * a wall-clock timeout enforced by the parent, and a bounded, whole-tree
    cleanup on timeout, cancellation or error;
  * an independent CPU ORT reference produced by its own attempt, so numeric
    parity is always "this GPU output vs a CPU run of the same ONNX bytes".

The attempt id is carried in the case JSON and echoed back in the fragment. A
fragment whose attempt id does not match the current attempt is refused, so a
stale file from a previous run can never satisfy a new verification.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

import numpy as np

from . import win_job
from .unified_runtime import Attempt, _attempt_from_fragment

CASE_SCHEMA = 1
DEFAULT_WORKER_MODULE = "runner._ep_worker"
# The only keys a caller may inject into a worker case. Everything else in the
# case is security-critical (inputs, model, reference output, attempt id).
SIMULATION_KEYS = frozenset({"fake_mode"})


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _store_path(path: Path | str, root: Path) -> str:
    """Repo-relative when possible, so a receipt never embeds a host path.

    Receipts are committed evidence and must stay portable and free of
    machine-identifying paths; a path outside the repo is kept absolute because
    silently rewriting it would misrepresent where the artefact lives.
    """
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except (ValueError, OSError):
        return str(path)


def _atomic_write_text(path: Path, text: str) -> None:
    """Write via a sibling temp file plus os.replace (never a torn file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex[:8])
    # binary write so Windows newline translation cannot alter the bytes
    with open(tmp, "wb") as fh:
        fh.write(text.encode("utf-8"))
    os.replace(tmp, path)


def _feed_fingerprint(inputs: dict) -> str:
    """Mirror _ep_worker's feed hash so both sides agree byte-for-byte."""
    from .input_contract import tensor_sha256

    h = hashlib.sha256()
    for name in sorted(inputs):
        # _ep_worker loads every feed as float32 and hashes it with
        # input_contract.tensor_sha256 (dtype|shape|bytes); use the same function
        # so the two sides cannot drift apart.
        arr = np.ascontiguousarray(np.asarray(inputs[name], dtype=np.float32))
        h.update(name.encode("utf-8") + b"|")
        h.update(tensor_sha256(arr).encode("ascii"))
    return h.hexdigest()


class WorkerExecutor:
    """One-EP-per-process executor with Job Object lifecycle and attempt isolation."""

    def __init__(self, *, gfx: str | None, winml_python: str | None,
                 work_dir: Path, timeout_s: int = 900,
                 repo_root: str | Path = ".",
                 cancel_event=None, container: str = "job_object",
                 kill_grace_s: float = 5.0, cpu_reference: str = "allow",
                 worker_module: str = "runner._ep_worker",
                 worker_case_extra: dict | None = None):
        self.gfx = gfx
        self.python = winml_python or sys.executable
        self.work = Path(work_dir)
        self.timeout_s = int(timeout_s)
        self.root = Path(repo_root)
        self.cancel_event = cancel_event
        self.container = container
        self.kill_grace_s = float(kill_grace_s)
        self.cpu_reference = cpu_reference
        # Injectable so the isolation/lifecycle tests can drive a deterministic
        # stub worker without needing DXGI, ORT or a GPU.
        self.worker_module = worker_module
        # Simulation hook: extra *non-security* keys merged into the worker case
        # JSON. It exists so the fault-injection tests can select stub-worker
        # behaviour; it must never be able to redirect the inputs, the model,
        # the reference or the attempt identity, because the RunResult would
        # otherwise describe the caller's tensor while the worker executed a
        # different one.
        self.worker_case_extra = dict(worker_case_extra or {})
        bad = sorted(set(self.worker_case_extra) - SIMULATION_KEYS)
        if bad:
            raise ValueError(
                "worker_case_extra may only contain " + str(sorted(SIMULATION_KEYS))
                + "; refused keys: " + str(bad))
        self.simulated = (worker_module != DEFAULT_WORKER_MODULE
                          or bool(self.worker_case_extra))
        self.last_feed_fingerprint: str | None = None
        self.attempts_dir = self.work / "attempts"
        self.inputs_dir = self.work / "inputs"
        self.attempts_dir.mkdir(parents=True, exist_ok=True)
        self.inputs_dir.mkdir(parents=True, exist_ok=True)
        (self.work / "logs").mkdir(exist_ok=True)
        self._seq = 0
        self._cpu_ref: Path | None = None
        self._cpu_ref_attempt: str | None = None
        self._cpu_ref_sha: str | None = None
        self._outputs: dict[str, dict] = {}
        self.history: list[dict] = []
        # Every Attempt this executor produced, including the internal CPU
        # numeric reference that the caller never asked for by name. predict()
        # merges these into the RunResult so an internal CPU execution can never
        # be invisible.
        self.spawned: list[Attempt] = []

    # -- identity / isolation ---------------------------------------------
    def _new_attempt(self, ep: str) -> tuple[str, Path]:
        self._seq += 1
        attempt_id = "A%03d-%s-%s" % (self._seq, ep, uuid.uuid4().hex[:12])
        d = self.attempts_dir / attempt_id
        if d.exists():  # an attempt id is single-use by construction
            raise RuntimeError("ATTEMPT_ID_REUSED: " + attempt_id)
        d.mkdir(parents=True, exist_ok=False)
        return attempt_id, d

    def _feeds_path(self, inputs: dict) -> tuple[Path, str]:
        """Content-addressed feeds bundle: a different tensor cannot collide."""
        fp = _feed_fingerprint(inputs)
        d = self.inputs_dir / fp
        d.mkdir(parents=True, exist_ok=True)
        path = d / "feeds.npz"
        sidecar = d / "feeds.sha256"
        if path.is_file() and sidecar.is_file():
            if sidecar.read_text(encoding="utf-8").strip() == _sha256_file(path):
                return path, fp
        tmp = d / ("feeds.tmp-" + uuid.uuid4().hex[:8] + ".npz")
        np.savez(tmp, **{k: np.asarray(v, dtype=np.float32) for k, v in inputs.items()})
        os.replace(tmp, path)
        _atomic_write_text(sidecar, _sha256_file(path))
        return path, fp

    # -- worker plumbing ---------------------------------------------------
    def _run_worker(self, ep: str, model_path: str, *, reference: Path | None,
                    feeds: Path, feed_fp: str,
                    reference_attempt: str | None = None) -> Attempt:
        attempt_id, adir = self._new_attempt(ep)
        case_path = adir / "case.json"
        frag_path = adir / "fragment.json"
        out_npz = adir / "outputs.npz"
        log_path = self.work / "logs" / ("worker_" + attempt_id + ".log")
        case = {
            "schema": CASE_SCHEMA,
            "attempt_id": attempt_id,
            "ep": ep,
            "gfx": self.gfx,
            "onnx_path": _store_path(model_path, self.root),
            "feeds_npz": _store_path(feeds, self.root),
            "expected_feed_sha256": feed_fp,
            "outputs_npz": _store_path(out_npz, self.root),
            "profile_prefix": _store_path(adir / "profile", self.root),
        }
        case.update(self.worker_case_extra)
        if reference is not None:
            case["reference_npz"] = _store_path(reference, self.root)
            case["reference_attempt_id"] = reference_attempt
            case["atol"] = 5e-3
            case["rtol"] = 5e-3
        _atomic_write_text(case_path, json.dumps(case, indent=2))

        cmd = [self.python, "-m", self.worker_module,
               "--case", str(case_path), "--out", str(frag_path)]
        spawn = win_job.spawn_in_job(
            cmd, cwd=self.root, log_path=log_path, timeout_s=self.timeout_s,
            label=attempt_id, kill_grace_s=self.kill_grace_s,
            cancel_event=self.cancel_event)

        frag: dict = {}
        frag_error = None
        if frag_path.is_file():
            try:
                frag = json.loads(frag_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                frag_error = "FRAGMENT_UNPARSABLE: " + str(e)

        rc = int(spawn.returncode if spawn.returncode is not None else -1)
        attempt = _attempt_from_fragment(ep, frag, exit_code=rc,
                                         log_path=_store_path(log_path, self.root))
        attempt.attempt_id = attempt_id
        attempt.attempt_dir = _store_path(adir, self.root)
        attempt.worker_pid = spawn.pid
        attempt.reference_attempt_id = reference_attempt
        attempt.process = spawn.as_dict()
        attempt.container = spawn.container
        if spawn.container == "job_object" and not spawn.job_assigned:
            attempt.container = "job_object_degraded"
            attempt.process["container"] = "job_object_degraded"
        attempt.partial_artifacts = []
        attempt.fragment_matches_attempt = (
            bool(frag) and frag.get("attempt_id") == attempt_id)

        if frag and not attempt.fragment_matches_attempt:
            # A fragment written for another attempt can never be adopted.
            attempt.ok = False
            attempt.status = "FAIL"
            attempt.error = ("ATTEMPT_MISMATCH: fragment attempt_id="
                             + str(frag.get("attempt_id")) + " expected=" + attempt_id)
        if frag_error:
            attempt.ok = False
            attempt.status = "FAIL"
            attempt.error = frag_error
        if spawn.cancelled:
            attempt.ok = False
            attempt.status = "BLOCKED"
            attempt.error = "CANCELLED_BY_CALLER"
        elif spawn.timed_out:
            attempt.ok = False
            attempt.status = "BLOCKED"
            attempt.error = "TIMEOUT_" + str(self.timeout_s) + "s"
        elif rc != 0 and not attempt.error:
            attempt.error = "worker_exit=" + str(rc)

        # Partial artefacts after a failure are kept as evidence but labelled.
        if not attempt.ok:
            for p in (out_npz, frag_path, case_path):
                if p.is_file():
                    # repo-relative: a receipt must never embed a host path
                    attempt.partial_artifacts.append(_store_path(p, self.root))

        if attempt.ok and out_npz.is_file():
            try:
                with np.load(out_npz) as z:
                    self._outputs[ep] = {k: z[k] for k in z.files}
                attempt.outputs = self._outputs[ep]
                attempt.outputs_sha256 = _sha256_file(out_npz)
                attempt.outputs_path = _store_path(out_npz, self.root)
            except (OSError, ValueError) as e:
                attempt.ok = False
                attempt.status = "FAIL"
                attempt.error = "OUTPUTS_UNREADABLE: " + str(e)
        elif attempt.ok and not out_npz.is_file():
            attempt.ok = False
            attempt.status = "FAIL"
            attempt.error = "OUTPUTS_MISSING"

        self.spawned.append(attempt)
        self.history.append({
            "attempt_id": attempt_id, "ep": ep, "ok": attempt.ok,
            "status": attempt.status, "error": attempt.error,
            "exit_code": rc, "container": spawn.container,
            "job_assigned": spawn.job_assigned, "timed_out": spawn.timed_out,
            "cancelled": spawn.cancelled, "survivors": spawn.survivors,
            "attempt_dir": str(adir), "log": str(log_path),
        })
        return attempt

    # -- public API --------------------------------------------------------
    def execute(self, *, ep: str, model_path: str, inputs: dict,
                reference: dict | None = None) -> Attempt:
        feeds, fp = self._feeds_path(inputs or {})
        self.last_feed_fingerprint = fp
        if ep == "cpu":
            attempt = self._run_worker("cpu", model_path, reference=None,
                                       feeds=feeds, feed_fp=fp)
            if attempt.ok and self._outputs.get("cpu") and attempt.outputs_path:
                self._cpu_ref = Path(attempt.outputs_path)
                self._cpu_ref_attempt = attempt.attempt_id
                self._cpu_ref_sha = attempt.outputs_sha256
            return attempt
        if self.cpu_reference == "forbid":
            # Honest refusal: without an independent reference we cannot claim
            # numeric parity, and a GPU result without parity is not evidence.
            a = self._run_worker(ep, model_path, reference=None,
                                 feeds=feeds, feed_fp=fp)
            a.ok = False
            a.status = "BLOCKED"
            a.error = "CPU_REFERENCE_FORBIDDEN: numeric parity cannot be established"
            return a
        if self._cpu_ref is None or not self._cpu_ref.is_file():
            cpu = self._run_worker("cpu", model_path, reference=None,
                                   feeds=feeds, feed_fp=fp)
            if not cpu.ok or not cpu.outputs_path:
                return _failed(ep, "CPU_REFERENCE_FAILED: " + str(cpu.error),
                               cpu, process=cpu.process)
            self._cpu_ref = Path(cpu.outputs_path)
            self._cpu_ref_attempt = cpu.attempt_id
            self._cpu_ref_sha = cpu.outputs_sha256
        return self._run_worker(ep, model_path, reference=self._cpu_ref,
                                feeds=feeds, feed_fp=fp,
                                reference_attempt=self._cpu_ref_attempt)


def _failed(ep: str, error: str, caused_by: Attempt, *, process: dict | None = None) -> Attempt:
    """A pre-attempt refusal that still carries the attempt that caused it."""
    a = Attempt(ep=ep, ok=False, status="BLOCKED", error=error,
                exit_code=caused_by.exit_code, log_path=caused_by.log_path)
    a.process = process if process is not None else (caused_by.process or {})
    a.container = caused_by.container
    a.reference_attempt_id = caused_by.attempt_id
    # The refusal is caused by another attempt; keep that attempt's identity and
    # its leftover artefacts visible instead of dropping them.
    a.attempt_dir = caused_by.attempt_dir
    a.worker_pid = caused_by.worker_pid
    a.partial_artifacts = list(caused_by.partial_artifacts or [])
    a.fragment_matches_attempt = caused_by.fragment_matches_attempt
    return a