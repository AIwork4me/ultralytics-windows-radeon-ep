"""RCA2A capability probe — three-layer switch support determination.

Layer 1 UPSTREAM_SUPPORTED: static table embedded from pinned upstream source
  research (MIGraphX 2e2cb92, onnxruntime-ep-amdgpu main); recorded, not probed.
Layer 2 SHIPPED_DLL_RECOGNIZED: ASCII string scan of the installed EP DLLs for
  the exact env-var / option-key tokens. Presence of a string is NOT execution
  support — it is only recognition evidence.
Layer 3 EFFECT_OBSERVED: fresh child process per switch, isolated cache dir,
  tiny canary model via AMDGPU EP; effects judged from stderr log lines, cache
  dir file state and (for trace switches) benchmark-log content.

Output: CAPABILITY_MATRIX.json with per-switch {upstream, dll_recognized,
effect_observed, evidence, verdict}. Verdicts:
  EFFECT_CONFIRMED / RECOGNIZED_BUT_NO_EFFECT / NOT_RECOGNIZED_IN_SHIPPED_DLL /
  UPSTREAM_ONLY / PROBE_ERROR
Never guess: a probe that errors stays PROBE_ERROR with its log path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

# layer 1: pinned upstream facts (research receipts in docs; summarized here)
UPSTREAM_TABLE = {
    "MIGRAPHX_TRACE_BENCHMARKING": {"repo": "ROCm/AMDMIGraphX@2e2cb92",
                                    "file": "docs/reference/MIGraphX-dev-env-vars.rst",
                                    "note": "1 basic / 2 detailed / 3 compiled traces"},
    "MIGRAPHX_SKIP_BENCHMARKING": {"repo": "ROCm/AMDMIGraphX@2e2cb92",
                                   "file": "docs/reference/MIGraphX-dev-env-vars.rst",
                                   "note": "skip MIGraphX+rocMLIR kernel benchmarking"},
    "MIGRAPHX_GPU_OPTIONS": {"repo": "ROCm/AMDMIGraphX@2e2cb92",
                             "file": "src/targets/gpu/target.cpp get_backend_options",
                             "note": "JSON-like override of gpu backend options incl. "
                                     "problem_cache_files / binary_cache"},
    "MIGRAPHX_PROBLEM_CACHE": {"repo": "upstream main uses problem_cache_files backend "
                                       "option; env var mentioned only in EP comment",
                               "file": "onnxruntime-ep-amdgpu src/migraphx/mgx_ep.cc",
                               "note": "EP delivers read-only list via compile API, "
                                       "explicitly NOT via this env var"},
    "MIGRAPHX_BINARY_CACHE": {"repo": "ROCm/AMDMIGraphX@2e2cb92",
                              "file": "src/targets/gpu/include/migraphx/gpu/binary_cache.hpp",
                              "note": "default binary-cache dir env var (newer gen)"},
    "ORT_MIGRAPHX_PROBLEM_CACHE": {"repo": "onnxruntime/onnxruntime-ep-amdgpu main",
                                   "file": "src/migraphx/mgx_ep.cc setup_problem_cache_paths",
                                   "note": "prepends a user file to the read-only search list"},
    "ORT_MIGRAPHX_CACHE_DIR": {"repo": "onnxruntime/onnxruntime-ep-amdgpu main",
                               "file": "src/migraphx/mgx_ep.cc (cache_dir_ / mxr)",
                               "note": "compiled-model (.mxr) cache directory"},
    "ORT_MIGRAPHX_EXHAUSTIVE_TUNE": {"repo": "onnxruntime/onnxruntime-ep-amdgpu main",
                                     "file": "src/migraphx/mgx_program_ops.cc compile_program",
                                     "note": "set_exhaustive_tune_flag"},
    "ORT_MIGRAPHX_COMPUTE_MODE": {"repo": "onnxruntime/onnxruntime-ep-amdgpu main",
                                  "file": "src/migraphx/mgx_ep.cc",
                                  "note": "eager|balanced|maximum; unknown -> warning + keep"},
    "ORT_MIGRAPHX_FORCE_RECOMPILE": {"repo": "onnxruntime/onnxruntime-ep-amdgpu main",
                                     "file": "src/migraphx/mgx_ep.cc load_compiled_program gate",
                                     "note": "bypass .mxr load"},
    "ORT_MIGRAPHX_DISABLE_COMPILED_MODEL_CACHING": {
        "repo": "onnxruntime/onnxruntime-ep-amdgpu main",
        "file": "src/migraphx/mgx_ep.cc save gate",
        "note": "disables .mxr save"},
    "provider_option:cache_dir": {"repo": "onnxruntime/onnxruntime-ep-amdgpu main",
                                  "file": "src/amdgpu/gpu_options.h",
                                  "note": "EP provider option relayed to migraphx backend"},
    "provider_option:exhaustive_tune": {"repo": "onnxruntime/onnxruntime-ep-amdgpu main",
                                        "file": "src/amdgpu/gpu_options.h"},
    "provider_option:compute_mode": {"repo": "onnxruntime/onnxruntime-ep-amdgpu main",
                                     "file": "src/amdgpu/gpu_options.h"},
    "provider_option:force_recompile": {"repo": "onnxruntime/onnxruntime-ep-amdgpu main",
                                        "file": "src/amdgpu/gpu_options.h"},
    "provider_option:disable_caching": {"repo": "onnxruntime/onnxruntime-ep-amdgpu main",
                                        "file": "src/amdgpu/gpu_options.h"},
    "MIGRAPHX_MLIR_TUNING_DB": {"repo": "shipped migraphx_gpu.dll v2_17_0 string: "
                                        "'MLIR tuning db not found. Please set "
                                        "MIGRAPHX_MLIR_TUNING_DB for optimal performance.'",
                                "file": "shipped binary strings",
                                "note": "older-gen MLIR tuning database"},
}

# DLLs scanned for layer 2 (umbrella, mgx backend EP, migraphx gpu target)
SCAN_DLLS = ["amdgpu-ep.dll", "migraphx-backend.dll", "migraphx_gpu.dll", "migraphx.dll"]

_CHILD_SRC = r'''
import sys, os, time, json
sys.path.insert(0, r"{repo_src}")
import numpy as np
import onnxruntime as ort
from radeon_ort.rca2a_ep import (_ensure_amdgpu_registered, create_amdgpu_session,
                                  physical_luid_key)
from radeon_ort.amdgpu_session import canary_model_path
from radeon_ort import device_identity
_ensure_amdgpu_registered()
adapters = device_identity.enum_dxgi_adapters()
res = device_identity.resolve_target_adapter(adapters, gfx="gfx1100")
assert res.get("ok"), res
luid = physical_luid_key(res["adapter"])
assert luid is not None, "no LUID derivable"
p = canary_model_path(r"{out_dir}")
t0 = time.monotonic()
sess = create_amdgpu_session(str(p), enable_profiling=False, target_luid=luid)
create_s = time.monotonic() - t0
feeds = {{"a": np.ones((256,256),dtype=np.float32), "b": np.ones((256,256),dtype=np.float32)}}
t1 = time.monotonic()
outs = sess.run(None, feeds)
first_s = time.monotonic() - t1
print(json.dumps({{"canary": "ok", "create_s": round(create_s,3), "first_s": round(first_s,4),
                   "providers": sess.get_providers()}}))
'''


def scan_dll_strings(ep_dir: Path) -> dict[str, list[str]]:
    pat = re.compile(rb"[\x20-\x7e]{8,}")
    out = {}
    for dll in SCAN_DLLS:
        p = ep_dir / dll
        if not p.is_file():
            out[dll] = []
            continue
        data = p.read_bytes()
        out[dll] = sorted({m.group().decode("ascii", "replace") for m in pat.finditer(data)})
    return out


def dll_recognizes(strings: dict[str, list[str]], token: str) -> dict:
    hits = {dll: (token in toks) for dll, toks in strings.items()}
    return {"recognized": any(hits.values()), "hits": hits}


def _dir_state(d: Path) -> dict:
    if not d.exists():
        return {"exists": False}
    files = []
    for q in sorted(d.rglob("*")):
        if q.is_file():
            files.append({"rel": str(q.relative_to(d)), "bytes": q.stat().st_size})
    return {"exists": True, "n_files": len(files), "files": files[:50]}


def run_effect_probe(name: str, env_extra: dict[str, str], out_root: Path,
                     child_py: str, timeout_s: int = 600) -> dict:
    """One fresh process per switch; FILE stdio; isolated cache dir."""
    d = out_root / f"probe_{name}"
    d.mkdir(parents=True, exist_ok=True)
    cache_dir = d / "cache"
    import shutil
    if cache_dir.exists():
        shutil.rmtree(cache_dir)  # every probe starts from a provably empty cache
    cache_dir.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.update(env_extra)
    src = _CHILD_SRC.format(repo_src=str(REPO_ROOT / "src"), out_dir=str(d))
    script = d / "child_probe.py"
    script.write_text(src, encoding="utf-8")
    stdout_p = d / "stdout.txt"
    stderr_p = d / "stderr.txt"
    t0 = time.monotonic()
    with open(stdout_p, "w", encoding="utf-8") as so, \
            open(stderr_p, "w", encoding="utf-8") as se:
        proc = subprocess.run([child_py, "-I", str(script)], stdout=so, stderr=se,
                              env=env, timeout=timeout_s,
                              creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    wall = time.monotonic() - t0
    stdout = stdout_p.read_text(encoding="utf-8", errors="replace")
    stderr = stderr_p.read_text(encoding="utf-8", errors="replace")
    state = _dir_state(cache_dir)
    return {"name": name, "env": env_extra, "rc": proc.returncode, "wall_s": round(wall, 1),
            "stdout_tail": stdout[-400:], "stderr": stderr[-4000:], "cache_dir_state": state,
            "dir": str(d)}


def classify_effect(name: str, res: dict) -> tuple[str, dict]:
    ev: dict = {"probe_dir": res["dir"], "rc": res["rc"]}
    stderr, stdout = res["stderr"], res["stdout_tail"]
    if res["rc"] != 0 or '"canary": "ok"' not in stdout:
        return "PROBE_ERROR", ev
    # fail-closed GPU truth: the child's own providers list must contain the
    # AMDGPU EP — ORT silently rebuilds CPU sessions on unknown provider names
    if '"AMDGPUExecutionProvider"' not in stdout.replace("'", '"'):
        ev["cpu_fallback_detected"] = True
        ev["providers_line"] = stdout[-200:]
        return "PROBE_ERROR_CPU_FALLBACK", ev
    if name == "baseline":
        ev["auto_save_failed_windowsapps"] = "auto_save_problem_cache: save failed" in stderr
        ev["tuning_db_warning"] = "tuning db not found" in stderr.lower()
        return "EFFECT_CONFIRMED", ev  # baseline probe itself succeeding
    if name.startswith("trace_bench_"):
        joined = stderr + os.linesep + stdout
        lines = [ln for ln in joined.splitlines()
                 if re.search(r"benchmarking gpu::|benchmarking solution|"
                              r"fastest solution|configs", ln, re.I)]
        ev["benchmark_log_lines"] = lines[:20]
        ev["n_configs_benchmarked"] = sum(
            int(m.group(1)) for ln in lines
            for m in [re.search(r"(\d+) configs", ln)] if m)
        ev["session_create_failed_epfail"] = "EPFail" in stderr
        if lines:
            return ("EFFECT_CONFIRMED" if "EPFail" not in stderr
                    else "EFFECT_OBSERVED_BUT_SESSION_FAILED"), ev
        return ("RECOGNIZED_BUT_NO_EFFECT"
                if '"canary": "ok"' in stdout else "PROBE_ERROR"), ev
    if name == "skip_benchmark_with_trace":
        # EFFECT for skip = config count DROPS vs the pure trace arm (13->1 on
        # the canary, live 2026-11-18); capture counts, compare in analysis
        joined = stderr + os.linesep + stdout
        lines = [ln for ln in joined.splitlines()
                 if re.search(r"benchmarking gpu::|benchmarking solution|"
                              r"fastest solution|configs", ln, re.I)]
        ev["benchmark_log_lines"] = lines[:10]
        ev["n_configs_benchmarked"] = sum(
            int(m.group(1)) for ln in lines
            for m in [re.search(r"(\d+) configs", ln)] if m)
        ev["session_succeeded"] = '"canary": "ok"' in stdout
        return "EFFECT_JUDGED_BY_CONFIG_COUNT_VS_TRACE_ARM", ev
    if name == "problem_cache_ort_env_read":
        joined = stderr + os.linesep + stdout
        info = [ln for ln in joined.splitlines()
                if re.search(r"problem.?cache|tuning db|loaded", ln, re.I)]
        ev["cache_related_log_lines"] = info[:20]
        ev["auto_save_failed_windowsapps"] = "auto_save_problem_cache: save failed" in stderr
        # read-side effect must show SOME load/hit evidence; absence = not recognized
        return ("EFFECT_CONFIRMED" if info else "RECOGNIZED_BUT_NO_EFFECT"), ev
    if name.startswith("problem_cache_"):
        # redirect worked iff cache file created in OUR dir AND no WindowsApps failure
        created = [f for f in (res["cache_dir_state"].get("files") or [])
                   if f["rel"].endswith(".json")]
        ev["cache_files_created"] = created
        ev["auto_save_failed_windowsapps"] = "auto_save_problem_cache: save failed" in stderr
        ev["redirected"] = bool(created) and not ev["auto_save_failed_windowsapps"]
        return ("EFFECT_CONFIRMED" if ev["redirected"] else "RECOGNIZED_BUT_NO_EFFECT"), ev
    if name.startswith("mxr_cache_"):
        mxr = [f for f in (res["cache_dir_state"].get("files") or [])
               if f["rel"].endswith(".mxr")]
        ev["mxr_files"] = mxr
        return ("EFFECT_CONFIRMED" if mxr else "RECOGNIZED_BUT_NO_EFFECT"), ev
    if name.startswith("compute_mode_bad"):
        ev["unknown_mode_warning"] = "unknown compute mode" in stderr.lower()
        return ("EFFECT_CONFIRMED" if ev["unknown_mode_warning"]
                else "RECOGNIZED_BUT_NO_EFFECT"), ev
    if name.startswith("exhaustive_"):
        # effect judged later by matrix arms (candidate counts); probe = ran clean
        return "PROBE_RAN_CLEAN_EFFECT_JUDGED_IN_MATRIX", ev
    return "RECOGNIZED_BUT_NO_EFFECT", ev


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ep-dir", required=True)
    ap.add_argument("--out", required=True, help="CAPABILITY_MATRIX.json path")
    ap.add_argument("--python", required=True, help="venv python for child probes")
    ap.add_argument("--skip-physical", action="store_true")
    ap.add_argument("--only", default=None,
                    help="comma-separated probe names to run (default all)")
    a = ap.parse_args()

    ep_dir = Path(a.ep_dir)
    out_p = Path(a.out)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    strings = scan_dll_strings(ep_dir)
    matrix: dict = {"schema": "rca2a_capability_matrix_v1",
                    "ep_dir": str(ep_dir), "dlls": {}}
    for dll, toks in strings.items():
        p = ep_dir / dll
        h = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
        matrix["dlls"][dll] = {"sha256": h, "n_strings": len(toks)}
    switches: dict = {}
    for token, up in UPSTREAM_TABLE.items():
        sw = {"upstream": up}
        sw["dll"] = dll_recognizes(strings, token)
        switches[token] = sw

    physical: dict = {}
    if not a.skip_physical:
        probes = [
            ("baseline", {}),
            ("trace_bench_2", {"MIGRAPHX_TRACE_BENCHMARKING": "2"}),
            ("skip_benchmark_with_trace", {"MIGRAPHX_SKIP_BENCHMARKING": "1",
                                           "MIGRAPHX_TRACE_BENCHMARKING": "2"}),
            ("problem_cache_gpu_options", {"MIGRAPHX_GPU_OPTIONS":
                                           '{"problem_cache_files":["%CACHE%\\\\pc.json"]}'}),
            ("problem_cache_ort_env", {"ORT_MIGRAPHX_PROBLEM_CACHE": "%CACHE%\\\\pc.json"}),
            ("mxr_cache_ort_env", {"ORT_MIGRAPHX_CACHE_DIR": "%CACHE%"}),
            ("mxr_cache_gpu_options", {"MIGRAPHX_GPU_OPTIONS":
                                       '{"binary_cache":"%CACHE%\\\\bin"}'}),
            ("compute_mode_bad", {"ORT_MIGRAPHX_COMPUTE_MODE": "bogus_mode"}),
            ("exhaustive_tune_1", {"ORT_MIGRAPHX_EXHAUSTIVE_TUNE": "1"}),
            ("trace_bench_1", {"MIGRAPHX_TRACE_BENCHMARKING": "1"}),
            ("problem_cache_ort_env_read", {"ORT_MIGRAPHX_PROBLEM_CACHE": "%SHIPPED_PC%"}),
        ]
        probe_root = out_p.parent / "PROBES"
        shipped_pc = str(Path(a.ep_dir) / "problem_cache.json")
        for name, env in probes:
            if a.only and name not in a.only.split(","):
                continue
            env2 = {k: v.replace("%CACHE%", str((probe_root / f"probe_{name}" / "cache")
                                                .resolve())).replace("%SHIPPED_PC%", shipped_pc)
                    for k, v in env.items()}
            try:
                res = run_effect_probe(name, env2, probe_root, a.python)
                verdict, ev = classify_effect(name, res)
            except subprocess.TimeoutExpired:
                verdict, ev = "PROBE_ERROR", {"error": "timeout"}
            except Exception as e:  # noqa: BLE001
                verdict, ev = "PROBE_ERROR", {"error": f"{type(e).__name__}: {e}"}
            physical[name] = {"verdict": verdict, **ev}

    matrix["switches"] = switches
    matrix["physical_probes"] = physical
    out_p.write_text(json.dumps(matrix, indent=1), encoding="utf-8")
    print(f"[probe] wrote {out_p}")
    for name, p in physical.items():
        print(f"  {name}: {p.get('verdict')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
