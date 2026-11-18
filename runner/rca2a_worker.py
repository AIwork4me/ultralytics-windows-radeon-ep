"""RCA2A worker: stage-timeline + cache-fingerprint instrumented single-EP child.

Fresh interpreter per attempt (-I), env injected by the supervisor BEFORE this
process starts (so AMDGPU/MIGraphX knobs are live before any ORT import).
stdout/stderr are ALWAYS files owned by the supervisor — this child never
reads or writes a pipe (RCA1 root-cause contract, commit dee28a9).

Timeline (events.jsonl, append-only, fsynced):
  EP_DISCOVERY -> EP_DLL_REGISTER -> GPU_IDENTITY_RESOLVE -> EP_DEVICE_PROBE
  -> CACHE_SNAPSHOT(pre) -> SESSION_CREATE -> CACHE_SNAPSHOT(post_create)
  -> FIRST_RUN -> CACHE_SNAPSHOT(post_first_run) -> WARMUP -> STEADY_RUN
  -> (SESSION_RECREATE cycles) -> CACHE_SNAPSHOT(final) -> SESSION_TEARDOWN

Cache fingerprinting is the RCA2A addition: every snapshot records, for each
watched path, file sizes/mtimes/SHA256 (bounded) and directory listings, so a
cache "hit" claim must correspond to observable file state changes — never to
mere file existence.

The child records facts only; classification and GPU_PASS validation belong to
the supervisor (rca2a_main.py). Exit codes mirror RCA1: 0 clean | 41 parity
drift | 42 runtime error | 44 identity fail | 45 device mismatch.
"""
from __future__ import annotations

import argparse
import faulthandler
import hashlib
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

HB_DEFAULT_S = 10.0
SHA_BUDGET_BYTES = 64 * 1024 * 1024  # hash files up to 64 MiB, larger ones: size+mtime only

S = {
    "phase": "PROCESS_START",
    "iter": -1,
    "session_ordinal": 0,
    "native_since": None,
    "native_what": None,
    "stop": False,
}
_LOCK = threading.Lock()


def _now() -> tuple[str, int]:
    import datetime
    return (datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            time.monotonic_ns())


class Emitter:
    def __init__(self, path: Path, base: dict):
        self._f = open(path, "a", encoding="utf-8")
        self._base = base
        self._mu = threading.Lock()

    def emit(self, kind: str, phase: str, *, mark: str = "point", **kw) -> None:
        ts, mono = _now()
        ev = dict(self._base)
        ev.update({"kind": kind, "phase": phase, "mark": mark, "ts": ts,
                   "monotonic_ns": mono, "pid": os.getpid(), "ppid": os.getppid(),
                   "elapsed_s": round((mono - self._base["_t0_mono"]) / 1e9, 3), **kw})
        with self._mu:
            self._f.write(json.dumps(ev, default=str) + "\n")
            self._f.flush()
            os.fsync(self._f.fileno())

    def close(self):
        self._f.close()


def _cpu_s() -> float:
    t = os.times()
    return t.user + t.system


def _heartbeat_loop(em: Emitter, hb_s: float):
    while not S["stop"]:
        time.sleep(hb_s)
        if S["stop"]:
            break
        native_for = (time.monotonic() - S["native_since"]) if S["native_since"] else 0.0
        em.emit("heartbeat", S["phase"], iter=S["iter"],
                session_ordinal=S["session_ordinal"], cpu_s=round(_cpu_s(), 3),
                native_for_s=round(native_for, 1), native_what=S["native_what"])


def _sha256_file(p: Path) -> tuple[str, bool]:
    """(sha256, hashed) — hashed=False when over budget (size+mtime still recorded)."""
    try:
        if p.stat().st_size > SHA_BUDGET_BYTES:
            return ("OVER_BUDGET", False)
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return (h.hexdigest(), True)
    except OSError:
        return ("UNREADABLE", False)


def _fingerprint(path_str: str) -> dict:
    """Fingerprint a file OR a directory tree (recursive, bounded depth 4)."""
    p = Path(path_str)
    out = {"path": path_str, "exists": p.exists()}
    if not p.exists():
        return out
    files = []
    if p.is_file():
        files = [p]
    else:
        for q in sorted(p.rglob("*")):
            depth = len(q.relative_to(p).parts)
            if depth > 4:
                continue
            if q.is_file():
                files.append(q)
            elif len(files) > 400:
                break
        files = files[:400]
    entries = []
    total = 0
    for q in files:
        try:
            st = q.stat()
        except OSError:
            continue
        total += st.st_size
        sha, hashed = _sha256_file(q)
        entries.append({"rel": str(q.relative_to(p)) if p.is_dir() else q.name,
                        "bytes": st.st_size,
                        "mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime)),
                        "sha256": sha, "hashed": hashed})
    out.update({"kind": "dir" if p.is_dir() else "file", "n_files": len(entries),
                "total_bytes": total, "entries": entries})
    return out


def _native(what: str):
    with _LOCK:
        S["native_since"] = time.monotonic()
        S["native_what"] = what


def _native_done():
    with _LOCK:
        S["native_since"] = None
        S["native_what"] = None


def _set_phase(em: Emitter, phase: str, mark: str = "begin", **kw):
    with _LOCK:
        S["phase"] = phase
    em.emit("phase", phase, mark=mark, **kw)


def _env_echo() -> dict:
    """Prove which tuning/cache knobs were actually present in THIS process env
    before any ORT import (supervisor injects them; worker never sets them)."""
    keys = sorted(k for k in os.environ
                  if k.startswith("MIGRAPHX_") or k.startswith("ORT_MIGRAPHX_")
                  or k.startswith("MIOPEN_") or k in ("HIP_VISIBLE_DEVICES",))
    return {k: os.environ[k] for k in keys}


def _graph_opt_level(ort, name: str):
    g = ort.GraphOptimizationLevel
    m = {"all": ("ort_enable_all", "ORT_ENABLE_ALL"),
         "basic": ("ort_enable_basic", "ORT_ENABLE_BASIC"),
         "disable_all": ("ort_disable_all", "ORT_DISABLE_ALL")}
    for attr in m[name]:
        if hasattr(g, attr):
            return getattr(g, attr)
    raise AttributeError(f"no graph-opt level for {name} on {g}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--ep", required=True, choices=["amdgpu", "dml", "cpu"])
    ap.add_argument("--attempt-id", required=True)
    ap.add_argument("--arm", required=True, help="experiment arm id from the frozen plan")
    ap.add_argument("--warmup-iters", type=int, default=30)
    ap.add_argument("--steady-iters", type=int, default=200)
    ap.add_argument("--recreate-count", type=int, default=0,
                    help="number of session recreate cycles AFTER steady (E7)")
    ap.add_argument("--recreate-steady-iters", type=int, default=10,
                    help="steady iters between recreates")
    ap.add_argument("--parity-final", action="store_true", default=True)
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--graph-opt", choices=["all", "basic", "disable_all"], default="all")
    ap.add_argument("--cache-watch", action="append", default=[],
                    help="path (file or dir) to fingerprint at each snapshot point")
    ap.add_argument("--heartbeat-s", type=float, default=HB_DEFAULT_S)
    ap.add_argument("--stop-after", default=None,
                    choices=[None, "SESSION_CREATE", "FIRST_RUN", "WARMUP"],
                    help="early-exit point for cold-start-only experiments")
    a = ap.parse_args()

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stacks_path = out / "faulthandler_stacks.txt"
    case = json.loads(Path(a.case).read_text(encoding="utf-8"))

    import numpy as np
    import onnxruntime as ort

    versions = {"python": sys.version.split()[0], "onnxruntime": ort.__version__,
                "numpy": np.__version__}
    onnx_p = Path(case["onnx_path"])
    feeds = {k: np.asarray(v, dtype=np.float32) for k, v in case["feeds"].items()}
    input_sha = hashlib.sha256(
        b"".join(t.tobytes() for _, t in sorted(feeds.items()))).hexdigest()

    def _sha(p: Path) -> str:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    onnx_sha = _sha(onnx_p)
    ref = {k: np.asarray(v) for k, v in (case.get("reference_outputs") or {}).items()}
    atol, rtol = case.get("atol", 5e-3), case.get("rtol", 5e-3)

    base = {"_t0_mono": time.monotonic_ns(), "attempt_id": a.attempt_id,
            "arm": a.arm, "ep": a.ep, "model": onnx_p.name, "model_sha256": onnx_sha,
            "input_sha256": input_sha,
            "input_shapes": {k: list(v.shape) for k, v in feeds.items()},
            "versions": versions, "argv": sys.argv[1:],
            "env_echo": _env_echo()}
    em = Emitter(out / "events.jsonl", base)
    faulthandler.enable(file=open(out / "faulthandler_fatal.txt", "w", encoding="utf-8"))
    hb = threading.Thread(target=_heartbeat_loop, daemon=True, args=(em, a.heartbeat_s))
    hb.start()
    rc = 0
    try:
        rc = _run(a, em, out, case, ort, np, feeds, ref, atol, rtol, onnx_p)
    except SystemExit as e:
        rc = int(e.code or 0)
    except Exception as e:  # noqa: BLE001
        em.emit("runtime_error", S["phase"], mark="fail",
                error=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-3000:])
        rc = 42
    finally:
        with _LOCK:
            S["stop"] = True
        em.emit("process_exit", "PROCESS_EXIT", exit_code=rc,
                iter=S["iter"], session_ordinal=S["session_ordinal"])
        em.close()
    return rc


def _snapshot(em: Emitter, label: str, watch: list[str]):
    fps = [_fingerprint(p) for p in watch]
    em.emit("cache_snapshot", "CACHE_SNAPSHOT", mark="point", label=label,
            watch=watch, fingerprints=fps)


def _run(a, em, out, case, ort, np, feeds, ref, atol, rtol, onnx_p) -> int:
    from radeon_ort import device_identity
    from radeon_ort.cpu_reference import compare_outputs

    gfx = case.get("gfx", "gfx1100")

    _set_phase(em, "EP_DISCOVERY", "begin")
    _native("ort.get_available_providers")
    providers = list(ort.get_available_providers())
    _native_done()
    em.emit("providers_available", "EP_DISCOVERY", mark="end", providers=providers)

    if a.ep == "amdgpu":
        _set_phase(em, "EP_DLL_REGISTER", "begin")
        from radeon_ort.amdgpu_session import _ensure_amdgpu_registered
        _ensure_amdgpu_registered()
        _native("ort.get_ep_devices")
        ep_devices = [d.ep_name for d in ort.get_ep_devices()]
        _native_done()
        em.emit("ep_devices", "EP_DLL_REGISTER", mark="end", ep_devices=ep_devices)

    _set_phase(em, "GPU_IDENTITY_RESOLVE", "begin")
    adapters = device_identity.enum_dxgi_adapters()
    res = device_identity.resolve_target_adapter(adapters, gfx=gfx)
    if not res.get("ok"):
        em.emit("identity_fail", "GPU_IDENTITY_RESOLVE", mark="fail", reason=res.get("reason"))
        return 44
    adapter = res["adapter"]
    luid = adapter.extra.get("luid_key")
    em.emit("gpu_identity", "GPU_IDENTITY_RESOLVE", mark="end",
            name=adapter.description, vendor_id=adapter.vendor_id,
            device_id=adapter.device_id, luid=luid, luid_str=adapter.luid_str,
            dedicated_video_mb=adapter.dedicated_video_mb)

    if a.ep == "amdgpu":
        _set_phase(em, "EP_DEVICE_PROBE", "begin")
        from radeon_ort.amdgpu_session import select_amdgpu_ep_device, _ep_device_metadata
        dev, sel = select_amdgpu_ep_device(luid)
        if dev is None:
            em.emit("ep_device_probe_fail", "EP_DEVICE_PROBE", mark="fail", selection=sel)
            return 44
        md = _ep_device_metadata(dev)
        if str(md.get("LUID", "")) != str(luid):
            em.emit("ep_device_mismatch", "EP_DEVICE_PROBE", mark="fail",
                    physical_luid=str(luid), ep_device=md)
            return 45
        em.emit("ep_device_selected", "EP_DEVICE_PROBE", mark="end",
                ep_device=md, selection_basis=sel.get("basis"))

    def make_session(ordinal: int):
        so = ort.SessionOptions()
        if a.profile:
            so.enable_profiling = True
            so.profile_file_prefix = str(out / f"ort_profile_sess{ordinal}")
        so.log_severity_level = 3
        so.graph_optimization_level = _graph_opt_level(ort, a.graph_opt)
        if a.ep == "amdgpu":
            from radeon_ort.amdgpu_session import create_amdgpu_session
            return create_amdgpu_session(str(onnx_p), sess_options_override=so, target_luid=luid)
        if a.ep == "dml":
            from radeon_ort.dml_session import create_dml_session
            return create_dml_session(str(onnx_p), device_id=adapter.index,
                                      sess_options_override=so)
        return ort.InferenceSession(str(onnx_p), sess_options=so,
                                    providers=["CPUExecutionProvider"])

    _snapshot(em, "pre_session", a.cache_watch)

    _set_phase(em, "SESSION_CREATE_BEGIN", "begin", session_ordinal=0, role="final")
    _native("InferenceSession(final)")
    t0 = time.monotonic()
    sess = make_session(0)
    create_s = time.monotonic() - t0
    _native_done()
    em.emit("session_created", "SESSION_CREATE_END", mark="end", session_ordinal=0,
            role="final", providers=sess.get_providers(), create_s=round(create_s, 3))
    em.emit("model_compile_observable", "MODEL_COMPILE_IF_OBSERVABLE", observable=False,
            note="compile embedded in InferenceSession ctor; no internal stage split claimed")
    _snapshot(em, "post_create", a.cache_watch)
    if a.stop_after == "SESSION_CREATE":
        return 0

    out_names = [o.name for o in sess.get_outputs()]

    def run_once(feeds_):
        _native("sess.run")
        t = time.monotonic()
        o = sess.run(None, feeds_)
        dt = time.monotonic() - t
        _native_done()
        return o, dt

    _set_phase(em, "FIRST_RUN_BEGIN", "begin")
    outs, first_dt = run_once(feeds)
    with _LOCK:
        S["iter"] = 1
    em.emit("first_run_done", "FIRST_RUN_END", mark="end", iter=1,
            run_s=round(first_dt, 4), latency_ms=round(first_dt * 1000, 2))
    first_outputs = dict(zip(out_names, outs))
    np.savez(out / "outputs_first.npz",
             **{k: np.asarray(v) for k, v in first_outputs.items()})
    if ref and a.ep != "cpu":
        pr = compare_outputs(ref, first_outputs, atol=atol, rtol=rtol)
        em.emit("parity_check", "FIRST_RUN_END", iter=1,
                allclose=pr.get("all_outputs_allclose"))
        if not pr.get("all_outputs_allclose"):
            return 41
    _snapshot(em, "post_first_run", a.cache_watch)
    if a.stop_after == "FIRST_RUN":
        return 0

    # ---- warmup (excluded from steady stats) ----
    _set_phase(em, "WARMUP_BEGIN", "begin", iters=a.warmup_iters)
    warm_lat = []
    for i in range(a.warmup_iters):
        _, dt = run_once(feeds)
        warm_lat.append(dt)
        with _LOCK:
            S["iter"] = 2 + i
    em.emit("warmup_done", "WARMUP_END", mark="end", iters=a.warmup_iters,
            lat_ms={"p50": round(float(np.median(warm_lat)) * 1000, 2),
                    "max": round(float(np.max(warm_lat)) * 1000, 2)})
    if a.stop_after == "WARMUP":
        return 0

    # ---- steady ----
    _set_phase(em, "STEADY_RUN_BEGIN", "begin", iters=a.steady_iters)
    steady_lat = []
    t_steady = time.monotonic()
    for i in range(a.steady_iters):
        outs, dt = run_once(feeds)
        steady_lat.append(dt)
        with _LOCK:
            S["iter"] = 2 + a.warmup_iters + i
        if (i + 1) % 50 == 0:
            em.emit("steady_progress", "STEADY_RUN_PROGRESS", iter=S["iter"],
                    lat_ms_p50=round(float(np.median(steady_lat[-100:])) * 1000, 2),
                    steady_s=round(time.monotonic() - t_steady, 1),
                    cpu_s=round(_cpu_s(), 3))
    last_outputs = dict(zip(out_names, outs))
    em.emit("steady_run_done", "STEADY_RUN_END", mark="end", iters=a.steady_iters,
            steady_effective_s=round(time.monotonic() - t_steady, 1),
            lat_ms={"p50": round(float(np.percentile(steady_lat, 50)) * 1000, 2),
                    "p95": round(float(np.percentile(steady_lat, 95)) * 1000, 2),
                    "p99": round(float(np.percentile(steady_lat, 99)) * 1000, 2),
                    "mean": round(float(np.mean(steady_lat)) * 1000, 2),
                    "n": len(steady_lat)})
    np.savez(out / "latencies.npz", warmup_ms=np.array(warm_lat) * 1000,
             steady_ms=np.array(steady_lat) * 1000)

    # ---- session recreate cycles (E7: cross-session cache behaviour) ----
    recreates = []
    for cyc in range(a.recreate_count):
        _set_phase(em, "SESSION_RECREATE_BEGIN", "begin", cycle=cyc)
        _native("del session")
        del sess
        _native_done()
        _native("InferenceSession(recreate)")
        t = time.monotonic()
        sess = make_session(cyc + 1)
        rc_s = time.monotonic() - t
        _native_done()
        with _LOCK:
            S["session_ordinal"] = cyc + 1
        cyc_lat = []
        o2, fr = run_once(feeds)  # first run after recreate (lazy compile cost)
        for _ in range(a.recreate_steady_iters):
            _, dt = run_once(feeds)
            cyc_lat.append(dt)
        recreates.append({"cycle": cyc, "recreate_s": round(rc_s, 3),
                          "first_run_after_s": round(fr, 4),
                          "lat_ms_p50": round(float(np.median(cyc_lat)) * 1000, 2)})
        em.emit("session_recreated", "SESSION_RECREATE_END", mark="end", cycle=cyc,
                recreate_s=round(rc_s, 3), first_run_after_s=round(fr, 4))
        if ref and a.ep != "cpu":
            pr = compare_outputs(ref, dict(zip(out_names, o2)), atol=atol, rtol=rtol)
            em.emit("parity_check", "SESSION_RECREATE_END", cycle=cyc,
                    allclose=pr.get("all_outputs_allclose"))
            if not pr.get("all_outputs_allclose"):
                return 41

    np.savez(out / "outputs_last.npz",
             **{k: np.asarray(v) for k, v in dict(zip(out_names, outs)).items()})
    _snapshot(em, "final", a.cache_watch)

    _set_phase(em, "SESSION_TEARDOWN_BEGIN", "begin")
    _native("del final session")
    del sess
    _native_done()
    em.emit("session_torn_down", "SESSION_TEARDOWN_END", mark="end",
            recreates=recreates, session_ordinal=S["session_ordinal"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
