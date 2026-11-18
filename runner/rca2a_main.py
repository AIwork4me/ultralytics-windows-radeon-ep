"""python -m runner.rca2a_main — RCA2A supervisor: plan / run / resume / summarize /
verify / publication.

Supervision contract inherited from RCA1 (local branch mission/rca1-amdgpu-soak-stall,
fix commit dee28a9, final 1aac7fb — NOT on origin/main, so this file is
self-contained and cites that provenance):
  * child stdout/stderr ALWAYS files in the attempt dir — never pipes;
  * watchdog = phase ceilings measured from the child's own monotonic phase-begin
    events + iteration-advance progress (never file mtime);
  * kill only the mission-owned child PID tree after ownership verification;
  * GPU_PASS validated independently by the supervisor (parity recompute from the
    saved npz + profile node counts), never trusted from the child.

Receipts append to RESULTS_RAW.jsonl; every receipt records code_git_sha,
worktree_dirty, main_base_sha, plan_sha256 — publication validation re-runs the
exact code from the exact HEAD that produced the physical numbers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PHASE_PRESETS = {
    # RCA1-calibrated ceilings: cold SESSION_CREATE 43-114s observed; 900s = 8x
    # worst; FIRST_RUN 600s; steady no-progress 180s; total 3600s.
    "rca2a_default": {"EP_DISCOVERY": 120, "EP_DLL_REGISTER": 120,
                      "GPU_IDENTITY_RESOLVE": 120, "EP_DEVICE_PROBE": 300,
                      "SESSION_CREATE": 900, "FIRST_RUN": 600,
                      "STEADY_NO_PROGRESS": 180, "SESSION_TEARDOWN": 300,
                      "TOTAL": 3600},
}


def utc_now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _wlog(path: Path, ev: dict):
    ev["ts"] = utc_now()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _read_events(path: Path) -> list[dict]:
    try:
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()
                if x.strip()]
    except (OSError, json.JSONDecodeError):
        return []


# ------------------------------------------------------------------ plan ----
def cmd_plan(a) -> int:
    run_root = Path(a.run_root)
    plan_path = run_root / "EXPERIMENT_PLAN_LOCK.json"
    if plan_path.exists() and not a.refreeze:
        print(f"[plan] exists (use --refreeze to re-freeze): {plan_path}")
        return 2
    cases = {}
    for m in a.models:
        case_p = Path(a.case_map).read_text(encoding="utf-8")
        cmap = json.loads(case_p)
        if m not in cmap:
            print(f"[plan] no case for model {m}")
            return 2
        cp = Path(cmap[m])
        c = json.loads(cp.read_text(encoding="utf-8"))
        onnx_sha = _sha256_file(Path(c["onnx_path"]))
        import numpy as np
        feed_bytes = b"".join(np.asarray(v, dtype=np.float32).tobytes()
                              for _, v in sorted(c["feeds"].items()))
        input_sha = hashlib.sha256(feed_bytes).hexdigest()
        cases[m] = {"case_file": str(cp), "onnx_path": c["onnx_path"],
                    "onnx_sha256": onnx_sha, "input_sha256": input_sha,
                    "input_shapes": {k: list(np.asarray(v).shape)
                                     for k, v in c["feeds"].items()}}
    arms = {}
    for spec in a.arm:
        # spec: NAME|K1:V1;K2:V2|cache_watch_dir1;dir2|recreate_count
        # (';' separates env pairs / watch paths so JSON values with ',' stay legal)
        parts = spec.split("|")
        name = parts[0]
        env: dict[str, str] = {}
        cache_watch: list[str] = []
        for kv in (parts[1].split(";") if len(parts) > 1 and parts[1] else []):
            k, _, v = kv.partition(":")
            env[k] = v
        for cw in (parts[2].split(";") if len(parts) > 2 and parts[2] else []):
            cache_watch.append(cw)
        recreate = int(parts[3]) if len(parts) > 3 and parts[3] else 0
        arms[name] = {"env": env, "cache_watch": cache_watch, "models": list(cases),
                      "ep": "amdgpu", "recreate_count": recreate}
    # run order: interleaved randomized blocks (ABBA/BAAB flavour) per model
    rng = random.Random(a.seed)
    order = []
    for rep in range(a.repeats):
        block = [(arm, m) for arm in arms for m in cases]
        rng.shuffle(block)
        order.extend({"arm": b[0], "model": b[1], "rep": rep} for b in block)
    plan = {
        "schema": "rca2a_plan_v1", "plan_sha256": "PENDING",
        "created_utc": utc_now(), "repeats": a.repeats, "seed": a.seed,
        "phase_timeouts": PHASE_PRESETS["rca2a_default"],
        "warmup_iters": a.warmup_iters, "steady_iters": a.steady_iters,
        "recreate_count": a.recreate_count,
        "arms": arms, "cases": cases, "order": order,
    }
    blob = json.dumps({k: v for k, v in plan.items() if k != "plan_sha256"},
                      sort_keys=True).encode("utf-8")
    plan["plan_sha256"] = hashlib.sha256(blob).hexdigest()
    plan_path.write_text(json.dumps(plan, indent=1), encoding="utf-8")
    print(f"[plan] froze {len(order)} attempts over {len(arms)} arms x "
          f"{len(cases)} models x {a.repeats} reps -> {plan_path}")
    return 0


# ------------------------------------------------------------------- run ----
def _current_phase(events: list[dict]) -> str:
    kinds = [e.get("kind", "") for e in events]
    if "process_exit" in kinds or "session_torn_down" in kinds:
        return "SESSION_TEARDOWN"
    if "steady_run_done" in kinds:
        return "SESSION_RECREATE" if any(e.get("kind") == "session_recreated"
                                         for e in events) else "SESSION_TEARDOWN"
    if any(e.get("kind") == "warmup_done" for e in events):
        return "STEADY_RUN"
    if "first_run_done" in kinds:
        return "WARMUP"
    if "session_created" in kinds:
        return "FIRST_RUN"
    if "ep_device_selected" in kinds:
        return "SESSION_CREATE"
    if "gpu_identity" in kinds:
        return "EP_DEVICE_PROBE"
    if "ep_devices" in kinds:
        return "GPU_IDENTITY_RESOLVE"
    if "providers_available" in kinds:
        return "EP_DLL_REGISTER"
    return "EP_DISCOVERY"


def _phase_begin_mono(events: list[dict], phase: str):
    for e in events:
        if e.get("phase", "").startswith(phase) and e.get("mark") == "begin":
            return e.get("monotonic_ns")
        if e.get("kind") == "phase" and e.get("phase") == phase and e.get("mark") == "begin":
            return e.get("monotonic_ns")
    return None


def _kill_tree(pid: int) -> dict:
    r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, text=True, timeout=30)
    return {"pid": pid, "rc": r.returncode, "out": (r.stdout + r.stderr).strip()[:300]}


def _validate_gpu_pass(attempt_dir: Path, case: dict, events: list[dict],
                       child_rc: int, ep: str, profile: bool) -> dict:
    v: dict = {"checks": []}

    def chk(name, ok, detail=""):
        v["checks"].append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})
        return bool(ok)

    if ep == "cpu":
        v["verdict"] = "CPU_ORACLE_ONLY" if child_rc == 0 else "CPU_FAIL"
        return v
    if ep == "dml":
        # RCA2A-009: ep_device_selected is emitted only on the amdgpu path;
        # for DML the physical-identity evidence is the gpu_identity event
        # (DXGI adapter resolved to the physical Radeon) that the worker always
        # emits before session creation.
        ident = next((e for e in events if e.get("kind") == "gpu_identity"), None)
        chk("gpu_identity_event", ident is not None)
        if ident:
            chk("physical_radeon_adapter", int(ident.get("vendor_id", 0)) == 0x1002,
                f"vendor_id={ident.get('vendor_id')} device_id={ident.get('device_id')}")
    else:
        probe = next((e for e in events if e.get("kind") == "ep_device_selected"), None)
        chk("ep_device_probe_event", probe is not None)
        if probe:
            chk("luid_match_basis", probe.get("selection_basis", "").startswith(
                ("LUID match", "Discrete=1 unique")))
    sess = next((e for e in events if e.get("kind") == "session_created"), None)
    if sess:
        provs = sess.get("providers", [])
        if ep == "amdgpu":
            chk("amdgpu_provider_present", "AMDGPUExecutionProvider" in provs, str(provs))
        else:
            chk("dml_provider_present", "DmlExecutionProvider" in provs, str(provs))
    ref = case.get("reference_outputs") or {}
    parity_ok = None
    if ref:
        import numpy as np
        npz_p = attempt_dir / "outputs_first.npz"
        if npz_p.is_file():
            z = np.load(npz_p)
            worst = 0.0
            names_ok = set(ref) == set(z.files)
            chk("output_names_match", names_ok)
            if names_ok:
                for k, rv in ref.items():
                    got, rv = z[k], np.asarray(rv, dtype=np.float32)
                    if got.shape != rv.shape:
                        chk(f"shape:{k}", False, f"{got.shape} vs {rv.shape}")
                        parity_ok = False
                        continue
                    worst = max(worst, float(np.abs(got.astype(np.float64)
                                                    - rv.astype(np.float64)).max()))
                v["supervisor_parity_max_abs"] = worst
                atol, rtol = case.get("atol", 5e-3), case.get("rtol", 5e-3)
                scale = float(np.abs(np.asarray(list(ref.values())[0],
                                                dtype=np.float64)).max())
                parity_ok = (parity_ok is not False) and worst <= atol + rtol * scale
                chk("supervisor_independent_parity", parity_ok, f"max_abs={worst:.3e}")
        else:
            chk("outputs_npz_present", False)
            parity_ok = False
    ep_nodes = None
    if profile:
        prof = sorted(attempt_dir.glob("ort_profile_sess*.json"))
        needle = "amdgpu" if ep == "amdgpu" else "dml"
        if prof:
            try:
                data = json.loads(prof[-1].read_text(encoding="utf-8"))
                ep_nodes = sum(1 for e in data if e.get("cat") == "Node" and
                               needle in str(e.get("args", {}).get("provider", "")).lower())
                v["profile_ep_node_count"] = ep_nodes
                chk("profile_ep_nodes_positive", (ep_nodes or 0) > 0, f"nodes={ep_nodes}")
            except (OSError, json.JSONDecodeError) as e:
                chk("profile_parse", False, str(e))
        else:
            chk("profile_file_present", False)
    ok = all(c["ok"] for c in v["checks"])
    v["verdict"] = "GPU_PASS_VALIDATED" if ok and child_rc == 0 else "GPU_PASS_INVALIDATED"
    return v


def _cache_verdict(attempt_dir: Path, watch: list[str], arm_env: dict) -> dict:
    """Derive cache-effect labels from the worker's cache_snapshot events.
    FILE_STATE_CHANGED needs >=2 snapshots with differing fingerprints."""
    events = _read_events(attempt_dir / "events.jsonl")
    snaps = [e for e in events if e.get("kind") == "cache_snapshot"]
    labels = [s.get("label") for s in snaps]
    changed = False
    if len(snaps) >= 2:
        for w in watch:
            fps = []
            for s in snaps:
                fp = next((f for f in s.get("fingerprints", [])
                           if f.get("path") == w), None)
                fps.append(json.dumps(fp, sort_keys=True) if fp else None)
            if len(set(fps)) > 1:
                changed = True
    return {"label": "CACHE_FILE_STATE_CHANGED" if changed else "CACHE_NO_EFFECT_OBSERVED",
            "snapshots": labels, "watch": watch,
            "switch_recognized": bool(arm_env),
            "second_process_evidence": None}


def cmd_run(a) -> int:
    run_root = Path(a.run_root)
    plan = json.loads((run_root / "EXPERIMENT_PLAN_LOCK.json").read_text(encoding="utf-8"))
    if a.plan_sha and a.plan_sha != plan["plan_sha256"]:
        print("[run] plan sha mismatch — refusing")
        return 2
    arm = plan["arms"].get(a.arm)
    if arm is None:
        print(f"[run] unknown arm {a.arm}")
        return 2
    model = a.model
    case_info = plan["cases"][model]
    case = json.loads(Path(case_info["case_file"]).read_text(encoding="utf-8"))

    attempt_id = f"{a.arm}_{model}_rep{a.rep}_{a.attempt_tag}"
    attempt_dir = run_root / "attempts" / attempt_id
    if attempt_dir.exists():
        print(f"[run] attempt exists (resume skips): {attempt_dir}")
        return 0
    attempt_dir.mkdir(parents=True)
    shutil.copyfile(case_info["case_file"], attempt_dir / "case_snapshot.json")

    child_py = a.python or str(REPO_ROOT / ".venv-winml" / "Scripts" / "python.exe")
    # per-attempt isolated cache dirs (arm-scoped so cross-process reuse within an
    # arm is possible, but arms never share cache state)
    cache_root = run_root / "CACHE_LABS" / a.arm
    cache_root.mkdir(parents=True, exist_ok=True)
    # cross-arm shared cache dir (E4 fill vs E7 force-recompile invalidation)
    shared_mxr = run_root / "CACHE_LABS" / "mxr_shared"
    watch = arm.get("cache_watch") or []

    env_extra = dict(arm.get("env") or {})
    # substitute %CACHE% (arm-private) and %CACHE_MXR% (shared) placeholders
    env_extra = {k: v.replace("%CACHE%", str(cache_root))
                    .replace("%CACHE_MXR%", str(shared_mxr))
                 for k, v in env_extra.items()}
    watch_resolved = [w.replace("%CACHE%", str(cache_root))
                        .replace("%CACHE_MXR%", str(shared_mxr)) for w in watch]
    for w in watch_resolved:
        Path(w).mkdir(parents=True, exist_ok=True)

    worker_args = ["--case", str(attempt_dir / "case_snapshot.json"),
                   "--out-dir", str(attempt_dir), "--ep", arm.get("ep", "amdgpu"),
                   "--attempt-id", attempt_id, "--arm", a.arm,
                   "--warmup-iters", str(plan.get("warmup_iters", 30)),
                   "--steady-iters", str(plan.get("steady_iters", 200)),
                   "--recreate-count", str(arm.get("recreate_count",
                                                   plan.get("recreate_count", 0))),
                   "--heartbeat-s", "10"]
    if a.profile:
        worker_args.append("--profile")
    for w in watch_resolved:
        worker_args += ["--cache-watch", w]
    if a.stop_after:
        worker_args += ["--stop-after", a.stop_after]

    cmd = [child_py, "-I", str(REPO_ROOT / "runner" / "rca2a_worker.py")] + worker_args
    git_sha, dirty = _git_state()
    plan_rec = {"plan_sha256": plan["plan_sha256"], "arms": plan["arms"]}
    meta = {"attempt_id": attempt_id, "arm": a.arm, "model": model,
            "rep": a.rep, "cmd": cmd, "env_expected": env_extra,
            "code_git_sha": git_sha, "worktree_dirty": dirty,
            "main_base_sha": a.main_base, "created_utc": utc_now(),
            "stop_after": a.stop_after, "profile": bool(a.profile),
            "watchdog_amendment": getattr(a, "session_create_ceiling", None)}
    (attempt_dir / "cmd.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")

    timeouts = dict(plan.get("phase_timeouts") or PHASE_PRESETS["rca2a_default"])
    if getattr(a, "session_create_ceiling", None):
        timeouts["SESSION_CREATE"] = a.session_create_ceiling
    env = dict(os.environ)
    env.update(env_extra)
    stdout_f = open(attempt_dir / "child_stdout.txt", "w", encoding="utf-8",
                    errors="replace")
    stderr_f = open(attempt_dir / "child_stderr.txt", "w", encoding="utf-8",
                    errors="replace")
    wd_log = run_root / "EXPERIMENT_EVENTS.jsonl"
    t0 = time.time()
    _wlog(wd_log, {"attempt_id": attempt_id, "event": "CHILD_SPAWN", "cmd": cmd[:2]})
    proc = subprocess.Popen(cmd, stdout=stdout_f, stderr=stderr_f, env=env,
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    _wlog(wd_log, {"attempt_id": attempt_id, "event": "CHILD_SPAWNED", "pid": proc.pid})
    evp = attempt_dir / "events.jsonl"
    stall = None
    killed = False
    last_iter, last_progress = -1, time.time()
    try:
        while True:
            rc = proc.poll()
            events = _read_events(evp)
            if rc is not None or (events and events[-1].get("kind") == "process_exit"):
                break
            phase = _current_phase(events)
            now_iter = max((e.get("iter", -1) for e in events), default=-1)
            if now_iter > last_iter:
                last_iter, last_progress = now_iter, time.time()
            if phase in ("SESSION_CREATE", "FIRST_RUN", "EP_DEVICE_PROBE",
                         "EP_DISCOVERY", "EP_DLL_REGISTER", "GPU_IDENTITY_RESOLVE",
                         "SESSION_TEARDOWN"):
                base = phase + "_BEGIN"
                m = _phase_begin_mono(events, phase)
                if m is not None and (time.monotonic_ns() - m) / 1e9 > \
                        timeouts.get(phase, 600):
                    stall = {"phase": phase, "ceiling_s": timeouts.get(phase)}
                    break
            elif phase == "STEADY_RUN":
                if time.time() - last_progress > timeouts["STEADY_NO_PROGRESS"]:
                    stall = {"phase": "STEADY_RUN", "ceiling_s":
                             timeouts["STEADY_NO_PROGRESS"], "last_iter": last_iter}
                    break
            if time.time() - t0 > timeouts["TOTAL"]:
                stall = {"phase": phase, "ceiling_s": timeouts["TOTAL"], "total": True}
                break
            time.sleep(2.0)
        child_rc = proc.poll()
        if child_rc is None:
            if stall is not None:
                ki = _kill_tree(proc.pid)
                killed = True
                _wlog(wd_log, {"attempt_id": attempt_id, "event": "CHILD_KILLED",
                               "stall": stall, **ki})
            try:
                child_rc = proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                _kill_tree(proc.pid)
                child_rc = proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            _kill_tree(proc.pid)
        stdout_f.close()
        stderr_f.close()

    events = _read_events(evp)
    errs = [e for e in events if e.get("kind") == "runtime_error"]
    if stall is None and child_rc == 0:
        classification = "CLEAN_COMPLETE"
    elif child_rc == 41:
        classification = "PARITY_DRIFT"
    elif child_rc == 44:
        classification = "IDENTITY_FAIL_CLOSED"
    elif child_rc == 45:
        classification = "DEVICE_MISMATCH_FAIL_CLOSED"
    elif stall is not None:
        classification = f"TIMEOUT_{stall['phase']}"
    elif child_rc is not None:
        classification = "PROCESS_CRASH" if child_rc not in (42,) else "RUNTIME_ERROR"
    else:
        classification = "UNKNOWN"

    validation = _validate_gpu_pass(attempt_dir, case, events, child_rc if child_rc is not None else -1,
                                    arm.get("ep", "amdgpu"), bool(a.profile))
    cache_v = _cache_verdict(attempt_dir, watch_resolved, env_extra)

    sess_ev = next((e for e in events if e.get("kind") == "session_created"), None)
    first_ev = next((e for e in events if e.get("kind") == "first_run_done"), None)
    warm_ev = next((e for e in events if e.get("kind") == "warmup_done"), None)
    steady_ev = next((e for e in events if e.get("kind") == "steady_run_done"), None)
    env_echo_ev = next((e for e in events if e.get("kind") == "providers_available"), None)
    timeline = [{"phase": e.get("phase"), "mark": e.get("mark")}
                for e in events if e.get("kind") == "phase"]
    receipt = {
        "schema_version": "rca2a_receipt_v1", "kind": "rca2a_attempt_receipt",
        "attempt_id": attempt_id, "arm": a.arm, "model": model, "rep": a.rep,
        "ep": arm.get("ep", "amdgpu"), "classification": classification,
        "child_exit_code": child_rc, "killed_by_watchdog": killed,
        "stall": stall, "wall_s": round(time.time() - t0, 1),
        "onnx_sha256": case_info["onnx_sha256"],
        "input_sha256": case_info["input_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "code_git_sha": git_sha, "worktree_dirty": dirty,
        "main_base_sha": a.main_base,
        "env_expected": env_extra,
        "env_echoed": (env_echo_ev or {}).get("env_echo") or
                      (events[0].get("env_echo") if events else None),
        "timeline": timeline,
        "phases": {"session_create_s": (sess_ev or {}).get("create_s"),
                   "first_run_s": (first_ev or {}).get("run_s"),
                   "warmup": (warm_ev or {}).get("lat_ms"),
                   "steady": (steady_ev or {}).get("lat_ms"),
                   "steady_effective_s": (steady_ev or {}).get("steady_effective_s"),
                   "recreates": (next((e for e in reversed(events)
                                       if e.get("kind") == "session_torn_down"), {})
                                  ).get("recreates")},
        "cache_verdict": cache_v,
        "gpu_validation": validation,
        "watchdog_amendment_session_create_s": getattr(
            a, "session_create_ceiling", None),
        "events_count": len(events), "finished_utc": utc_now(),
        "attempt_dir": str(attempt_dir),
    }
    (attempt_dir / "receipt_rca2a.json").write_text(json.dumps(receipt, indent=1),
                                                    encoding="utf-8")
    with open(run_root / "RESULTS_RAW.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(receipt, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())
    _wlog(wd_log, {"attempt_id": attempt_id, "classification": classification,
                   "validation": validation.get("verdict"), "rc": child_rc,
                   "wall_s": receipt["wall_s"]})
    print(f"[run] {attempt_id}: {classification} | {validation.get('verdict')} | "
          f"rc={child_rc} wall={receipt['wall_s']}s")
    return 0 if classification == "CLEAN_COMPLETE" else 1


def _git_state() -> tuple[str, bool]:
    try:
        sha = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=15).stdout.strip()
        st = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain=v1"],
                            capture_output=True, text=True, timeout=15).stdout
        return sha, bool(st.strip())
    except Exception:  # noqa: BLE001
        return "UNKNOWN", True


def cmd_runall(a) -> int:
    """Run every attempt in the frozen plan order; existing attempt dirs are
    skipped (resume). One GPU child at a time; failures are recorded, not fatal
    to the batch — every arm keeps its own classification."""
    run_root = Path(a.run_root)
    plan = json.loads((run_root / "EXPERIMENT_PLAN_LOCK.json").read_text(encoding="utf-8"))
    fails = []
    for step in plan["order"]:
        if a.arms and step["arm"] not in a.arms.split(","):
            continue
        attempt_id = f"{step['arm']}_{step['model']}_rep{step['rep']}_{a.attempt_tag}"
        if (run_root / "attempts" / attempt_id).exists():
            print(f"[runall] skip existing {attempt_id}")
            continue
        import argparse as _ap
        ns = _ap.Namespace(run_root=str(run_root), arm=step["arm"], model=step["model"],
                           rep=step["rep"], attempt_tag=a.attempt_tag,
                           main_base=a.main_base, plan_sha=a.plan_sha,
                           python=a.python, profile=a.profile, stop_after=a.stop_after)
        rc = cmd_run(ns)
        if rc != 0:
            fails.append(attempt_id)
    print(f"[runall] done; {len(fails)} non-clean attempts: {fails}")
    return 0 if not fails else 1


# ----------------------------------------------------------- summarize ----
def cmd_summarize(a) -> int:
    from radeon_ort.rca2a_metrics import summarize, bootstrap_ci
    run_root = Path(a.run_root)
    receipts = [json.loads(x) for x in
                (run_root / "RESULTS_RAW.jsonl").read_text(encoding="utf-8").splitlines()
                if x.strip()]
    rows = []
    for r in receipts:
        if r.get("classification") != "CLEAN_COMPLETE":
            rows.append({"attempt": r["attempt_id"], "arm": r["arm"],
                         "model": r["model"], "classification": r["classification"],
                         "session_create_s": None, "first_run_s": None})
            continue
        rows.append({"attempt": r["attempt_id"], "arm": r["arm"], "model": r["model"],
                     "classification": r["classification"],
                     "session_create_s": r["phases"].get("session_create_s"),
                     "first_run_s": r["phases"].get("first_run_s"),
                     "steady_p50_ms": (r["phases"].get("steady") or {}).get("p50"),
                     "steady_p95_ms": (r["phases"].get("steady") or {}).get("p95"),
                     "steady_p99_ms": (r["phases"].get("steady") or {}).get("p99")})
    import csv
    with open(run_root / "RCA2A_METRICS.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["attempt", "arm", "model", "classification",
                                          "session_create_s", "first_run_s",
                                          "steady_p50_ms", "steady_p95_ms",
                                          "steady_p99_ms"])
        w.writeheader()
        w.writerows(rows)
    # per (arm, model) stats
    stats: dict = {}
    for r in receipts:
        if r.get("classification") != "CLEAN_COMPLETE":
            continue
        key = f"{r['arm']}|{r['model']}"
        stats.setdefault(key, {"create": [], "first": [], "p50": [], "p95": []})
        ph = r.get("phases", {})
        if ph.get("session_create_s") is not None:
            stats[key]["create"].append(ph["session_create_s"])
        if ph.get("first_run_s") is not None:
            stats[key]["first"].append(ph["first_run_s"])
        st_ = ph.get("steady") or {}
        if st_.get("p50") is not None:
            stats[key]["p50"].append(st_["p50"])
            stats[key]["p95"].append(st_["p95"])
    out = {}
    for key, d in stats.items():
        out[key] = {"n_processes": len(d["create"]),
                    "session_create_s": summarize(d["create"]),
                    "session_create_ci": bootstrap_ci(d["create"]),
                    "first_run_s": summarize(d["first"]),
                    "steady_p50_ms": summarize(d["p50"]),
                    "steady_p95_ms": summarize(d["p95"])}
    (run_root / "ANALYSIS" ).mkdir(exist_ok=True)
    (run_root / "ANALYSIS" / "ARM_SUMMARY.json").write_text(
        json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1)[:4000])
    return 0


# --------------------------------------------------------------- verify ----
def cmd_verify(a) -> int:
    from radeon_ort.rca2a_schema import validate_receipt
    run_root = Path(a.run_root)
    plan = json.loads((run_root / "EXPERIMENT_PLAN_LOCK.json").read_text(encoding="utf-8"))
    plan_rec = {"plan_sha256": plan["plan_sha256"], "arms": plan["arms"]}
    receipts = [json.loads(x) for x in
                (run_root / "RESULTS_RAW.jsonl").read_text(encoding="utf-8").splitlines()
                if x.strip()]
    bad = 0
    for r in receipts:
        res = validate_receipt(r, plan=plan_rec)
        if res["verdict"] != "RECEIPT_VALID":
            bad += 1
            print(f"[verify] INVALID {r.get('attempt_id')}: {res['verdict']}")
            for c in res["checks"]:
                if not c["ok"]:
                    print(f"    - {c['check']}: {c['detail']}")
    print(f"[verify] {len(receipts) - bad}/{len(receipts)} receipts valid")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan")
    p_plan.add_argument("--run-root", required=True)
    p_plan.add_argument("--case-map", required=True)
    p_plan.add_argument("--models", nargs="+", required=True)
    p_plan.add_argument("--arm", action="append", required=True,
                        help="NAME|K1:V1,K2:V2|cache_watch_paths")
    p_plan.add_argument("--repeats", type=int, default=3)
    p_plan.add_argument("--seed", type=int, default=20261118)
    p_plan.add_argument("--warmup-iters", type=int, default=30)
    p_plan.add_argument("--steady-iters", type=int, default=200)
    p_plan.add_argument("--recreate-count", type=int, default=0)
    p_plan.add_argument("--refreeze", action="store_true")

    p_run = sub.add_parser("run")
    p_run.add_argument("--run-root", required=True)
    p_run.add_argument("--arm", required=True)
    p_run.add_argument("--model", required=True)
    p_run.add_argument("--rep", type=int, required=True)
    p_run.add_argument("--attempt-tag", default="")
    p_run.add_argument("--main-base", required=True)
    p_run.add_argument("--plan-sha", default=None)
    p_run.add_argument("--python", default=None)
    p_run.add_argument("--profile", action="store_true")
    p_run.add_argument("--no-profile", dest="profile", action="store_false")
    p_run.set_defaults(profile=True)
    p_run.add_argument("--stop-after", default=None)
    p_run.add_argument("--session-create-ceiling", type=float, default=None,
                       help="watchdog amendment: override SESSION_CREATE ceiling "
                            "(s) for legitimate long tuning runs; recorded in the "
                            "receipt and must cite a plan amendment")

    p_sum = sub.add_parser("summarize")
    p_sum.add_argument("--run-root", required=True)

    p_ver = sub.add_parser("verify")
    p_ver.add_argument("--run-root", required=True)

    p_all = sub.add_parser("runall")
    p_all.add_argument("--run-root", required=True)
    p_all.add_argument("--attempt-tag", default="")
    p_all.add_argument("--main-base", required=True)
    p_all.add_argument("--plan-sha", default=None)
    p_all.add_argument("--python", default=None)
    p_all.add_argument("--profile", action="store_true")
    p_all.add_argument("--no-profile", dest="profile", action="store_false")
    p_all.set_defaults(profile=True)
    p_all.add_argument("--stop-after", default=None)
    p_all.add_argument("--arms", default=None,
                       help="comma-separated arm filter (phase execution)")

    a = ap.parse_args()
    return {"plan": cmd_plan, "run": cmd_run, "runall": cmd_runall,
            "summarize": cmd_summarize, "verify": cmd_verify}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
