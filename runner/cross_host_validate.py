"""python -m runner.cross_host_validate — one-command cross-host acceptance.

This is the single entrypoint a colleague on a gfx1100 / gfx1201 physical host
runs from clone to layered verdict. It never re-implements a stage: every
GPU-executing stage is an existing ``runner.*`` module invoked as a subprocess,
so local behaviour matches CI exactly.

Hard gate
---------
Before any GPU-executing stage the live DXGI/DID identity is compared with
``--gfx``. A mismatch stops the run with stage status ``BLOCKED``,
reason ``WRONG_PHYSICAL_DEVICE`` and exit code 34, and **no** evidence is written
under ``<report-root>/<gfx>/`` (that directory would otherwise look like that
architecture's evidence). This is why ``--gfx gfx1100`` and ``--gfx gfx1201``
on the gfx1151 reference host can only ever emit a harness-ready verdict.

Stages (each emits PASS / FAIL / BLOCKED / NOT_RUN / SKIPPED)
------------------------------------------------------------
    detect_host -> check_dependencies -> ep_probe -> device_canary -> tier_a
    -> multi_input_oracle -> benchmarks -> soak -> evidence_freeze -> final_verdict

Verdict vocabulary (exact strings; nothing else is emitted)
----------------------------------------------------------
    <GFX>_CROSS_HOST_HARNESS_READY   plan/harness ready; no physical claim
    <GFX>_PHYSICAL_PENDING           no physical run completed yet
    <GFX>_PHYSICAL_GO                every required stage passed on that hardware
    <GFX>_CONDITIONAL_GO             one EP complete, another has an external block
    <GFX>_BLOCKED_EP                 no target EP executable (no CPU fallback)
    <GFX>_NO_GO                      evidence/task failure

Exit codes: 0 = PHYSICAL_GO (or a valid ``--dry-run`` plan), 1 = NO_GO / invalid
plan, 2 = non-fatal non-GO (harness-ready / pending / blocked / conditional),
34 = WRONG_PHYSICAL_DEVICE (no GPU work performed).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from radeon_ort import contract as contract_mod
from radeon_ort import cross_host, redaction
from radeon_ort.device_identity import enum_dxgi_adapters, target_device_ids
from radeon_ort.manifest import write_manifest
from radeon_ort.receipts import utc_now_iso
from runner import REPO_ROOT

STAGES = (
    "detect_host",
    "check_dependencies",
    "ep_probe",
    "device_canary",
    "tier_a",
    "multi_input_oracle",
    "benchmarks",
    "soak",
    "evidence_freeze",
    "final_verdict",
)

# Longest-lived first: a broken module must be visible before any GPU stage.
CLI_MODULES = (
    "preflight",
    "probe_amdgpu_ep",
    "unified_canary",
    "run_matrix",
    "resume_matrix",
    "multi_input_oracle",
    "bench_repeats",
    "soak",
    "negative_injection",
    "redact_evidence",
    "finalize_run",
    "verify_receipts",
    "build_compatibility_matrix",
)

POLICIES = ("gpu-first", "amdgpu-only", "dml-only")
TIERS = ("smoke", "core", "full")
EP_LABEL = {"amdgpu": "AMDGPU", "dml": "DML"}
DEFAULT_BENCH_ONNX = REPO_ROOT / "models" / "onnx" / "yolo26n.raw_b1_fp32_static.onnx"
DEFAULT_BENCH_PT = REPO_ROOT / "models" / "pt" / "yolo26n.pt"

_DEP_PROBE = r'''
import json, sys
out = {"python_version": sys.version.split()[0], "errors": []}
for name in ("numpy", "onnxruntime", "onnx", "yaml", "jsonschema"):
    try:
        mod = __import__(name)
        out[name] = getattr(mod, "__version__", "unknown")
    except Exception as exc:
        out[name] = None
        out["errors"].append(f"{name}: {type(exc).__name__}: {exc}")
try:
    import onnxruntime as ort
    out["ort_providers"] = sorted(ort.get_available_providers())
except Exception:
    out["ort_providers"] = []
try:
    import importlib.metadata as md
    out["windowsml_version"] = md.version("windowsml")
except Exception:
    out["windowsml_version"] = None
print(json.dumps(out, sort_keys=True))
'''

_EXPORT_PROBE = r'''
import json, sys
out = {"python_version": sys.version.split()[0], "errors": []}
for name in ("numpy", "onnx", "cv2", "torch", "ultralytics"):
    try:
        mod = __import__(name)
        out[name] = getattr(mod, "__version__", "unknown")
    except Exception as exc:
        out[name] = None
        out["errors"].append(f"{name}: {type(exc).__name__}: {exc}")
print(json.dumps(out, sort_keys=True))
'''


# --- plan ----------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    gfx: str
    tier: str
    policy: str
    run_id: str
    dry_run: bool
    run_id_is_placeholder: bool
    report_root: Path
    winml_python: Path
    export_python: Path
    timeout_s: int
    skip_soak: bool
    json_out: Path | None
    catalog: Path
    thresholds: Path
    image: Path
    obb_image: Path
    models: str | None
    bench_onnx: Path
    bench_input_npy: Path | None
    bench_reps: int
    bench_samples: int
    soak_minutes: float | None

    @property
    def run_dir(self) -> Path:
        return self.report_root / self.gfx / self.run_id


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="python -m runner.cross_host_validate",
        description="One-command fail-closed acceptance run for a physical "
                    "gfx1100 / gfx1201 host.",
    )
    ap.add_argument("--gfx", required=True, choices=list(cross_host.SUPPORTED_GFX),
                    help="physical architecture this host must be")
    ap.add_argument("--tier", choices=list(TIERS), default="core",
                    help="smoke = 1 checkpoint; core = Tier A; full = full catalog")
    ap.add_argument("--policy", choices=list(POLICIES), default="gpu-first",
                    help="EP policy; CPU is never an implicit fallback")
    ap.add_argument("--run-id", default=None,
                    help="new immutable run id (required unless --dry-run)")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate the plan and print commands; no sessions, no GPU")
    ap.add_argument("--report-root", default=str(REPO_ROOT / "reports"))
    ap.add_argument("--winml-python", default=sys.executable,
                    help="interpreter carrying ORT + the target EP")
    ap.add_argument("--export-python",
                    default=str(REPO_ROOT / ".venv-export" / "Scripts" / "python.exe"))
    ap.add_argument("--timeout-s", type=int, default=900,
                    help="per-subprocess bound for GPU stages")
    ap.add_argument("--skip-soak", action="store_true")
    ap.add_argument("--json-out", default=None,
                    help="structured report path (default: run dir, or _blocked/)")
    ap.add_argument("--catalog", default=str(REPO_ROOT / "catalog" / "model_catalog.json"))
    ap.add_argument("--thresholds", default=str(REPO_ROOT / "configs" / "thresholds.yaml"))
    ap.add_argument("--image", default=str(REPO_ROOT / "data" / "fixtures" / "bus.jpg"))
    ap.add_argument("--obb-image",
                    default=str(REPO_ROOT / "data" / "fixtures" / "dota_p1053.jpg"))
    ap.add_argument("--models", default=None,
                    help="comma list for multi_input_oracle (default: tier-appropriate)")
    ap.add_argument("--bench-onnx", default=str(DEFAULT_BENCH_ONNX))
    ap.add_argument("--bench-input-npy", default=None,
                    help="persisted input tensor; default: run bundle or deterministic zeros")
    ap.add_argument("--bench-reps", type=int, default=3)
    ap.add_argument("--bench-samples", type=int, default=50)
    ap.add_argument("--soak-minutes", type=float, default=None)
    a = ap.parse_args(argv)
    if a.run_id is None and not a.dry_run:
        ap.error("--run-id is required unless --dry-run")
    if a.run_id is not None:
        problem = cross_host.run_id_architecture_problem(a.run_id, a.gfx)
        if problem:
            ap.error(problem)
    return a


def _repo_path(value) -> Path:
    """Resolve a caller path the same way the subprocess stages will.

    Every GPU stage runs with ``cwd=REPO_ROOT``, so a relative ``--report-root``
    or ``--catalog`` must not be interpreted against the caller's cwd.
    """
    path = Path(value)
    return path if path.is_absolute() else (REPO_ROOT / path)


def build_plan(a: argparse.Namespace) -> Plan:
    run_id = a.run_id or "<RUN_ID>"
    return Plan(
        gfx=a.gfx,
        tier=a.tier,
        policy=a.policy,
        run_id=run_id,
        dry_run=bool(a.dry_run),
        run_id_is_placeholder=a.run_id is None,
        report_root=_repo_path(a.report_root),
        winml_python=_repo_path(a.winml_python),
        export_python=_repo_path(a.export_python),
        timeout_s=max(60, int(a.timeout_s)),
        skip_soak=bool(a.skip_soak),
        json_out=(Path(a.json_out) if a.json_out else None),
        catalog=_repo_path(a.catalog),
        thresholds=_repo_path(a.thresholds),
        image=_repo_path(a.image),
        obb_image=_repo_path(a.obb_image),
        models=a.models,
        bench_onnx=_repo_path(a.bench_onnx),
        bench_input_npy=(_repo_path(a.bench_input_npy) if a.bench_input_npy else None),
        bench_reps=max(1, int(a.bench_reps)),
        bench_samples=max(1, int(a.bench_samples)),
        soak_minutes=a.soak_minutes,
    )


def ep_scope(policy: str) -> tuple[str, ...]:
    if policy == "gpu-first":
        return ("amdgpu", "dml")
    return (policy.split("-", 1)[0],)


def run_matrix_ep(policy: str) -> str:
    scope = ep_scope(policy)
    return scope[0] if len(scope) == 1 else "both"


def soak_budget_minutes(plan: Plan) -> float:
    if plan.soak_minutes is not None:
        return max(0.5, float(plan.soak_minutes))
    return 5.0 if plan.tier == "core" else 15.0


# --- command builders (pure; used by --dry-run and by execution) ----------------


def _py(plan: Plan) -> str:
    return str(plan.winml_python)


def cmd_ep_probe(plan: Plan, run_dir: Path) -> list[str]:
    return [_py(plan), "-m", "runner.probe_amdgpu_ep",
            "--out", str(run_dir / "EP_PROBE.json"), "--gfx", plan.gfx]


def cmd_canary(plan: Plan, run_dir: Path, ep: str) -> list[str]:
    return [_py(plan), "-m", "runner.unified_canary",
            "--ep", ep, "--gfx", plan.gfx,
            "--out", str(run_dir / f"CANARY_{EP_LABEL[ep]}.json")]


def cmd_tier_a(plan: Plan) -> list[str]:
    tier = "core" if plan.tier == "smoke" else plan.tier
    argv = [_py(plan), "-m", "runner.run_matrix",
            "--gfx", plan.gfx, "--tier", tier, "--ep", run_matrix_ep(plan.policy),
            "--reports-root", str(plan.report_root), "--run-id", plan.run_id,
            "--winml-python", str(plan.winml_python),
            "--export-python", str(plan.export_python),
            "--catalog", str(plan.catalog),
            "--image", str(plan.image), "--obb-image", str(plan.obb_image),
            "--max-minutes-per-case", f"{plan.timeout_s / 60.0:.1f}"]
    if plan.tier == "smoke":
        argv += ["--limit", "1"]
    else:
        argv += ["--with-negative"]
    return argv


def _multi_models(plan: Plan) -> str | None:
    if plan.models:
        return plan.models
    if plan.tier == "smoke":
        return "yolo26n.pt"
    return None


def cmd_multi_input(plan: Plan, run_dir: Path, ep: str) -> list[str]:
    argv = [_py(plan), "-m", "runner.multi_input_oracle",
            "--gfx", plan.gfx, "--ep", ep,
            "--out", str(run_dir / f"MULTI_INPUT_{EP_LABEL[ep]}.json"),
            "--image", str(plan.image), "--obb-image", str(plan.obb_image),
            "--timeout-s", str(plan.timeout_s)]
    models = _multi_models(plan)
    if models:
        argv += ["--models", models]
    return argv


def cmd_bench(plan: Plan, run_dir: Path, ep: str, input_npy: Path) -> list[str]:
    return [_py(plan), "-m", "runner.bench_repeats",
            "--onnx", str(plan.bench_onnx), "--input-npy", str(input_npy),
            "--ep", ep, "--gfx", plan.gfx,
            "--reps", str(plan.bench_reps), "--samples", str(plan.bench_samples),
            "--out", str(run_dir / f"BENCH_{EP_LABEL[ep]}_yolo26n.json"),
            "--telemetry-out", str(run_dir / f"BENCH_{EP_LABEL[ep]}_telemetry.jsonl")]


def cmd_soak(plan: Plan, ep: str, input_npy: Path) -> list[str]:
    return [_py(plan), "-m", "runner.soak",
            "--ep", ep, "--gfx", plan.gfx,
            "--minutes", f"{soak_budget_minutes(plan):g}",
            "--onnx", str(plan.bench_onnx), "--input-npy", str(input_npy),
            "--reports-root", str(plan.report_root), "--run-id", plan.run_id,
            "--tag", f"yolo26n_{ep}"]


def cmd_redact(plan: Plan, run_dir: Path, *, check: bool = False) -> list[str]:
    argv = [_py(plan), "-m", "runner.redact_evidence", "--run-dir", str(run_dir)]
    if check:
        argv.append("--check")
    return argv


def cmd_finalize(plan: Plan, run_dir: Path) -> list[str]:
    return [_py(plan), "-m", "runner.finalize_run", "--run-dir", str(run_dir),
            "--host", plan.gfx, "--catalog", str(plan.catalog)]


def cmd_compat(plan: Plan, run_dir: Path) -> list[str]:
    return [_py(plan), "-m", "runner.build_compatibility_matrix",
            "--run-dir", str(run_dir), "--catalog", str(plan.catalog)]


def cmd_verify(plan: Plan, run_dir: Path) -> list[str]:
    return [_py(plan), "-m", "runner.verify_receipts", "--run-dir", str(run_dir)]


def planned_commands(plan: Plan, run_dir: Path) -> dict[str, list[list[str]]]:
    """Every command the pipeline would execute, in stage order."""
    scope = ep_scope(plan.policy)
    return {
        "ep_probe": [cmd_ep_probe(plan, run_dir)],
        "device_canary": [cmd_canary(plan, run_dir, ep) for ep in scope],
        "tier_a": [cmd_tier_a(plan)],
        "multi_input_oracle": [cmd_multi_input(plan, run_dir, ep) for ep in scope],
        "benchmarks": [cmd_bench(plan, run_dir, ep, Path("<INPUT_NPY>")) for ep in scope],
        "soak": [cmd_soak(plan, ep, Path("<INPUT_NPY>"))
                 for ep in (scope[:1] if plan.tier != "full" else scope)],
        "evidence_freeze": [cmd_redact(plan, run_dir), cmd_finalize(plan, run_dir),
                            cmd_compat(plan, run_dir), cmd_verify(plan, run_dir),
                            cmd_redact(plan, run_dir, check=True)],
    }


# --- subprocess plumbing --------------------------------------------------------


def _subprocess_env() -> dict:
    env = dict(os.environ)
    src = str(REPO_ROOT / "src")
    existing = env.get("PYTHONPATH") or ""
    env["PYTHONPATH"] = src + (os.pathsep + existing if existing else "")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def run_command(argv: list[str], *, timeout_s: int) -> dict:
    """Run one runner module; bounded, captured, never raises on rc != 0."""
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            [str(x) for x in argv], cwd=str(REPO_ROOT), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout_s,
            env=_subprocess_env(), check=False)
        return {
            "rc": int(proc.returncode),
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "elapsed_s": round(time.perf_counter() - started, 2),
            "timeout": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "rc": 124,
            "stdout": _as_text(exc.stdout),
            "stderr": f"TIMEOUT after {timeout_s}s",
            "elapsed_s": round(time.perf_counter() - started, 2),
            "timeout": True,
        }
    except OSError as exc:
        return {
            "rc": 127,
            "stdout": "",
            "stderr": f"could not start {argv[0]!r}: {exc}",
            "elapsed_s": round(time.perf_counter() - started, 2),
            "timeout": False,
        }


def _as_text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


_POSIX_USER_SLASHES = re.compile(r"%USERPROFILE%/+", re.IGNORECASE)


def _clean_path_text(text: str) -> str:
    """Collapse the double slash redaction.redact_path leaves after %USERPROFILE%."""
    return _POSIX_USER_SLASHES.sub("%USERPROFILE%/", text)


def _redact_path(value) -> str:
    return _clean_path_text(redaction.redact_path(value))


def _redact_arg(value) -> str:
    text = str(value)
    try:
        if Path(text).is_absolute():
            return _redact_path(text)
        return _clean_path_text(redaction.redact_text(text))
    except (OSError, ValueError):
        return _clean_path_text(redaction.redact_text(text))


def _redact_argv(argv: list[str]) -> list[str]:
    return [_redact_arg(a) for a in argv]


def _command_record(argv: list[str], result: dict | None = None) -> dict:
    record: dict = {"argv_redacted": _redact_argv(argv)}
    if result is not None:
        record.update({
            "rc": result["rc"],
            "elapsed_s": result["elapsed_s"],
            "timeout": result["timeout"],
            "stdout_tail": redaction.redact_text(result["stdout"][-1500:]),
            "stderr_tail": redaction.redact_text(result["stderr"][-1500:]),
        })
    return record


def _run_stage_command(argv: list[str], *, timeout_s: int) -> tuple[dict, dict]:
    result = run_command(argv, timeout_s=timeout_s)
    return result, _command_record(argv, result)


def _load_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


# --- plan validation ------------------------------------------------------------


def _probe_imports(python: Path, script: str) -> dict:
    result = run_command([str(python), "-c", script], timeout_s=120)
    payload = None
    for line in reversed((result["stdout"] or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = None
            break
    if not isinstance(payload, dict):
        return {"ok": False, "rc": result["rc"],
                "error": (result["stderr"] or "no JSON from dependency probe")[-500:]}
    payload["ok"] = result["rc"] == 0 and not payload.get("errors")
    payload["rc"] = result["rc"]
    return payload


def probe_interpreter(plan: Plan) -> dict:
    return _probe_imports(plan.winml_python, _DEP_PROBE)


def probe_export_interpreter(plan: Plan) -> dict:
    return _probe_imports(plan.export_python, _EXPORT_PROBE)


def validate_module_clis(plan: Plan) -> list[dict]:
    checks = []
    for module in CLI_MODULES:
        result = run_command([str(plan.winml_python), "-m", f"runner.{module}", "--help"],
                             timeout_s=180)
        checks.append({
            "check": f"cli:runner.{module}",
            "ok": result["rc"] == 0,
            "detail": f"rc={result['rc']}" + ("" if result["rc"] == 0
                                               else f": {(result['stderr'] or '')[-200:]}"),
        })
    return checks


def _load_yaml(path: Path) -> dict | None:
    try:
        import yaml
    except ImportError:
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _check_file(check: str, path: Path, *, kind: str) -> dict:
    return {"check": check, "ok": path.is_file(),
            "detail": f"{kind} {_redact_path(path)}"}


def plan_checks(plan: Plan, gate: dict, dxgi_error: str | None, *,
                probe_clis: bool) -> list[dict]:
    checks: list[dict] = []
    checks.append(_check_file("winml_python", plan.winml_python, kind="interpreter"))
    checks.append(_check_file("export_python", plan.export_python, kind="interpreter"))
    # runner.multi_input_oracle resolves these two fixed repo-root venvs itself.
    checks.append(_check_file("multi_input_winml_venv",
                              REPO_ROOT / ".venv-winml" / "Scripts" / "python.exe",
                              kind="interpreter"))
    checks.append(_check_file("multi_input_export_venv",
                              REPO_ROOT / ".venv-export" / "Scripts" / "python.exe",
                              kind="interpreter"))
    checks.append(_check_file("bench_onnx", plan.bench_onnx, kind="model"))
    checks.append(_check_file("anchor_pt", DEFAULT_BENCH_PT, kind="model"))
    checks.append(_check_file("catalog", plan.catalog, kind="catalog"))
    checks.append(_check_file("thresholds", plan.thresholds, kind="thresholds"))
    for schema in ("receipt.json", "receipt_v2.json"):
        path = REPO_ROOT / "schemas" / schema
        checks.append(_check_file(f"schema:{schema}", path, kind="schema"))
    for image in (plan.image, plan.obb_image):
        checks.append(_check_file(f"image:{image.name}", image, kind="fixture"))

    if dxgi_error:
        checks.append({"check": "dxgi_identity_read", "ok": False,
                       "detail": f"DXGI enumeration failed: {dxgi_error}"})
    else:
        checks.append({"check": "dxgi_identity_read", "ok": True,
                       "detail": f"live gfx={gate.get('live_gfx')!r}; "
                                 f"observed DID={gate.get('observed_device_ids')}"})
        checks.append({
            "check": "physical_gate",
            "ok": True,  # a correct refusal is a valid plan, never a plan error
            "detail": (f"would PASS for {plan.gfx}" if gate.get("status") == "PASS"
                       else f"would REFUSE ({gate.get('reason')}): {gate.get('detail')}"),
        })

    thresholds = _load_yaml(plan.thresholds)
    if thresholds is None:
        checks.append({"check": "thresholds_parse", "ok": False,
                       "detail": "configs/thresholds.yaml is missing or unparseable"})
    else:
        fp32 = ((thresholds.get("numeric_parity") or {}).get("fp32") or {})
        required = [
            ("thresholds:numeric_parity.fp32.atol", fp32.get("atol") is not None),
            ("thresholds:numeric_parity.fp32.rtol", fp32.get("rtol") is not None),
            ("thresholds:task_oracle", isinstance(thresholds.get("task_oracle"), dict)),
            ("thresholds:soak", isinstance(thresholds.get("soak"), dict)),
        ]
        for name, ok in required:
            checks.append({"check": name, "ok": bool(ok),
                           "detail": "" if ok else "missing from thresholds"})

    catalog = _load_json(plan.catalog)
    if catalog is None:
        checks.append({"check": "catalog_parse", "ok": False,
                       "detail": "catalog/model_catalog.json is missing or unparseable"})
    else:
        entries = catalog.get("entries")
        checks.append({"check": "catalog_entries", "ok": isinstance(entries, dict) and bool(entries),
                       "detail": f"{len(entries) if isinstance(entries, dict) else 0} entries"})
        tier_a = [e for e in entries.values() if isinstance(e, dict) and e.get("tier") == "A"]             if isinstance(entries, dict) else []
        missing = []
        for entry in tier_a:
            ckpt = entry.get("checkpoint")
            if not ckpt:
                continue
            if not (REPO_ROOT / "models" / "pt" / ckpt).is_file():
                missing.append(ckpt)
        checks.append({
            "check": "tier_a_assets",
            "ok": not missing,
            "detail": (f"{len(tier_a)} Tier A checkpoints present" if not missing
                       else f"missing PT assets: {missing[:8]}"),
        })

    for schema in ("receipt.json", "receipt_v2.json"):
        path = REPO_ROOT / "schemas" / schema
        parsed = _load_json(path)
        checks.append({"check": f"schema_parse:{schema}", "ok": parsed is not None,
                       "detail": "" if parsed is not None else "unparseable"})

    parent = plan.report_root if plan.report_root.exists() else plan.report_root.parent
    writable = parent.exists() and os.access(parent, os.W_OK)
    checks.append({"check": "report_root_writable", "ok": writable,
                   "detail": _redact_path(parent)})

    dep = probe_interpreter(plan)
    checks.append({"check": "interpreter_imports", "ok": bool(dep.get("ok")),
                   "detail": (f"python={dep.get('python_version')} "
                              f"ort={dep.get('onnxruntime')} "
                              f"onnx={dep.get('onnx')} windowsml={dep.get('windowsml_version')}"
                              if dep.get("ok") else dep.get("error"))})
    exp = probe_export_interpreter(plan)
    checks.append({"check": "export_interpreter_imports", "ok": bool(exp.get("ok")),
                   "detail": (f"python={exp.get('python_version')} "
                              f"onnx={exp.get('onnx')} torch={exp.get('torch')} "
                              f"ultralytics={exp.get('ultralytics')} cv2={exp.get('cv2')}"
                              if exp.get("ok") else exp.get("error"))})
    if probe_clis:
        checks.extend(validate_module_clis(plan))
    return checks


# --- stage execution helpers ----------------------------------------------------


def _stage(stage: str, status: str, **kw) -> dict:
    return cross_host.make_stage_record(stage, status, **kw)


def _gate_blocked_stage(stage: str, gate: dict) -> dict:
    return _stage(stage, "BLOCKED", reason=str(gate.get("reason")),
                  detail=str(gate.get("detail")))


def _ep_probe_classify(plan: Plan, payload: dict | None, rc: int) -> dict:
    if not isinstance(payload, dict):
        return {"status": "FAIL", "reason": "EP_PROBE_UNREADABLE",
                "detail": f"probe rc={rc} but no parseable report"}
    state = str(payload.get("state") or "UNKNOWN")
    readiness = payload.get("ep_readiness") or {}
    providers = readiness.get("ort_providers") or []
    dml_ready = any("dml" in str(p).lower() for p in providers)
    amdgpu_ready = state in ("EP_REGISTERED", "GPU_EXECUTED")
    refusal = cross_host.ep_readiness_refusal(
        plan.policy, amdgpu_ready=amdgpu_ready, dml_ready=dml_ready,
        amdgpu_state=state)
    return {
        "status": "PASS" if refusal is None else "BLOCKED",
        "reason": refusal,
        "detail": f"amdgpu_state={state} amdgpu_ready={amdgpu_ready} "
                  f"dml_ready={dml_ready} rc={rc}",
        "amdgpu_ready": amdgpu_ready,
        "dml_ready": dml_ready,
        "amdgpu_state": state,
    }


def _ready_eps(plan: Plan, ep_info: dict) -> list[str]:
    ready = {"amdgpu": bool(ep_info.get("amdgpu_ready")),
             "dml": bool(ep_info.get("dml_ready"))}
    return [ep for ep in ep_scope(plan.policy) if ready.get(ep)]


def _canary_outcomes(plan: Plan, run_dir: Path, ready: list[str],
                     timeout_s: int) -> dict:
    results: dict[str, str] = {}
    records: list[dict] = []
    for ep in ep_scope(plan.policy):
        if ep not in ready:
            results[ep] = "BLOCKED_EP"
            continue
        argv = cmd_canary(plan, run_dir, ep)
        _result, record = _run_stage_command(argv, timeout_s=timeout_s)
        canary = _load_json(run_dir / f"CANARY_{EP_LABEL[ep]}.json") or {}
        status, reason = cross_host.canary_stage_status(canary)
        record["classification"] = status
        if reason:
            record["classification_reason"] = reason
        records.append(record)
        results[ep] = status if status == "PASS" else (
            "BLOCKED_EP" if status == "BLOCKED" else "FAIL")
    statuses = set(results.values())
    if "FAIL" in statuses:
        stage_status = "FAIL"
    elif "BLOCKED_EP" in statuses:
        stage_status = "BLOCKED"
    else:
        stage_status = "PASS"
    return {"status": stage_status, "ep_results": results, "commands": records}


def _aggregate(results: dict[str, str]) -> str:
    values = set(results.values())
    if "FAIL" in values:
        return "FAIL"
    if "BLOCKED_EP" in values:
        return "BLOCKED"
    if values and values == {"PASS"}:
        return "PASS"
    return "NOT_RUN"


def _collect_receipt_outcomes(run_dir: Path) -> dict[str, str]:
    collected: dict[str, list[str]] = {}
    for rp in sorted((run_dir / "receipts").glob("receipt_*.json")):
        receipt = _load_json(rp)
        if not receipt:
            continue
        status = receipt.get("status") or {}
        ep = "amdgpu" if status.get("amdgpu") not in (None, "NOT_RUN") else "dml"
        value = str(status.get(ep) or "NOT_RUN")
        if value.startswith("PASS_GPU"):
            label = "PASS"
        elif value.startswith("BLOCKED"):
            label = "BLOCKED_EP"
        elif value.startswith("FAIL"):
            label = "FAIL"
        else:
            label = "NOT_RUN"
        collected.setdefault(ep, []).append(label)
    out: dict[str, str] = {}
    for ep, labels in collected.items():
        unique = set(labels)
        if "FAIL" in unique:
            out[ep] = "FAIL"
        elif "BLOCKED_EP" in unique:
            out[ep] = "BLOCKED_EP"
        elif unique == {"PASS"}:
            out[ep] = "PASS"
        else:
            out[ep] = "NOT_RUN"
    return out


def _find_bench_input(plan: Plan, run_dir: Path) -> Path | None:
    if plan.bench_input_npy is not None:
        return plan.bench_input_npy if plan.bench_input_npy.is_file() else None
    manifest_dir = run_dir / "inputs"
    if manifest_dir.is_dir():
        for manifest_path in sorted(manifest_dir.glob("manifest_*.json")):
            manifest = _load_json(manifest_path) or {}
            if manifest.get("model_name") != DEFAULT_BENCH_PT.stem:
                continue
            if manifest.get("task") != "detect":
                continue
            npy = manifest_dir / str(manifest.get("npy_file") or "")
            if npy.is_file():
                return npy
        fallback = sorted(manifest_dir.glob("*.npy"))
        if fallback:
            return fallback[0]
    return None


def _make_bench_input(plan: Plan, run_dir: Path) -> Path | None:
    out_dir = run_dir / "bench_inputs"
    out = out_dir / "bench_yolo26n_zeros.npy"
    if out.is_file():
        return out
    out_dir.mkdir(parents=True, exist_ok=True)
    script = ("import numpy as np, sys; "
              "np.save(sys.argv[1], np.zeros((1, 3, 640, 640), dtype=np.float32))")
    result = run_command([str(plan.winml_python), "-c", script, str(out)], timeout_s=120)
    return out if result["rc"] == 0 and out.is_file() else None


def _multi_eps(plan: Plan, ready: list[str]) -> list[str]:
    if plan.tier == "full":
        return ready
    return ready[:1]


def _decide_verdict(plan: Plan, *, gate: dict, dry_run: bool,
                    stage_statuses: dict[str, str],
                    ep_results: dict[str, str]) -> str:
    if dry_run or gate.get("status") != "PASS":
        return cross_host.gfx_verdict(plan.gfx, "CROSS_HOST_HARNESS_READY")
    if any(status == "FAIL" for stage, status in stage_statuses.items()
           if stage != "final_verdict"):
        return cross_host.gfx_verdict(plan.gfx, "NO_GO")

    required = list(ep_scope(plan.policy))
    passed = [ep for ep in required if ep_results.get(ep) == "PASS"]
    blocked = [ep for ep in required if ep_results.get(ep) == "BLOCKED_EP"]

    physical_ok = all(stage_statuses.get(s) == "PASS" for s in
                      ("device_canary", "tier_a", "multi_input_oracle", "benchmarks"))
    soak_ok = stage_statuses.get("soak") in ("PASS", "SKIPPED")
    frozen_ok = stage_statuses.get("evidence_freeze") == "PASS"
    if physical_ok and soak_ok and frozen_ok and len(passed) == len(required):
        return cross_host.gfx_verdict(plan.gfx, "PHYSICAL_GO")
    if passed and blocked:
        return cross_host.gfx_verdict(plan.gfx, "CONDITIONAL_GO")
    if blocked and not passed:
        return cross_host.gfx_verdict(plan.gfx, "BLOCKED_EP")
    return cross_host.gfx_verdict(plan.gfx, "PHYSICAL_PENDING")


# --- report ---------------------------------------------------------------------


def _redact_tree(value):
    if isinstance(value, str):
        return _clean_path_text(redaction.redact_text(value))
    if isinstance(value, dict):
        return {k: _redact_tree(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_tree(v) for v in value]
    return value


def dump_report(report: dict) -> str:
    text = json.dumps(_redact_tree(report), indent=2, default=str) + "\n"
    leaks = redaction.scan_for_leaks(text)
    if leaks:
        text = redaction.redact_text(text)
        leaks = redaction.scan_for_leaks(text)
    if leaks:
        raise RuntimeError("REDACTION_FAILED: evidence would leak: " + "; ".join(leaks[:5]))
    return text


def _report_path(plan: Plan, report: dict) -> Path | None:
    if plan.json_out is not None:
        return plan.json_out
    if report.get("mode") == "dry-run":
        return None
    if (report.get("physical_gate") or {}).get("status") != "PASS":
        return plan.report_root / "_blocked" / f"{plan.run_id}.json"
    return plan.run_dir / "CROSS_HOST_VALIDATION.json"


def _emit(plan: Plan, report: dict) -> Path | None:
    path = _report_path(plan, report)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_report(report), encoding="utf-8")
    return path


def _base_report(plan: Plan, gate: dict, mode: str) -> dict:
    return {
        "schema_version": 1,
        "tool": "runner.cross_host_validate",
        "generated_utc": utc_now_iso(),
        "mode": mode,
        "plan": {
            "gfx": plan.gfx,
            "tier": plan.tier,
            "policy": plan.policy,
            "run_id": plan.run_id,
            "run_id_is_placeholder": plan.run_id_is_placeholder,
            "eps": list(ep_scope(plan.policy)),
            "run_matrix_ep": run_matrix_ep(plan.policy),
            "report_root_redacted": _redact_path(plan.report_root),
            "run_dir_redacted": _redact_path(plan.run_dir),
            "winml_python_name": plan.winml_python.name,
            "export_python_name": plan.export_python.name,
            "timeout_s": plan.timeout_s,
            "skip_soak": plan.skip_soak,
            "soak_minutes": (None if plan.tier == "smoke" or plan.skip_soak
                             else soak_budget_minutes(plan)),
            "bench_reps": plan.bench_reps,
            "bench_samples": plan.bench_samples,
        },
        "physical_gate": gate,
        "stages": [],
        "final_verdict": None,
        "exit_code": None,
        "notes": [],
    }


def _print_stage(record: dict) -> None:
    line = f"[cross-host] {record['stage']:<20} {record['status']}"
    if record.get("reason"):
        line += f"  reason={record['reason']}"
    print(line)


# --- dry-run --------------------------------------------------------------------


def _planned_stage_records(plan: Plan, run_dir: Path) -> list[dict]:
    commands = planned_commands(plan, run_dir)
    records = [
        _stage("detect_host", "NOT_RUN",
               detail="in-process DXGI/DID read; no ONNX session"),
        _stage("check_dependencies", "NOT_RUN",
               detail="in-process interpreter/module/model/schema validation"),
        _stage("ep_probe", "NOT_RUN",
               commands=[_command_record(argv) for argv in commands["ep_probe"]]),
        _stage("device_canary", "NOT_RUN",
               commands=[_command_record(argv) for argv in commands["device_canary"]]),
        _stage("tier_a", "NOT_RUN",
               commands=[_command_record(argv) for argv in commands["tier_a"]]),
        _stage("multi_input_oracle", "NOT_RUN",
               commands=[_command_record(argv)
                         for argv in commands["multi_input_oracle"]]),
        _stage("benchmarks", "NOT_RUN",
               commands=[_command_record(argv) for argv in commands["benchmarks"]]),
        _stage("soak", "NOT_RUN",
               commands=[_command_record(argv) for argv in commands["soak"]]),
        _stage("evidence_freeze", "NOT_RUN",
               commands=[_command_record(argv)
                         for argv in commands["evidence_freeze"]]),
        _stage("final_verdict", "NOT_RUN"),
    ]
    return records


def run_dry(plan: Plan, gate: dict, dxgi_error: str | None) -> int:
    print(f"[cross-host] DRY-RUN gfx={plan.gfx} tier={plan.tier} policy={plan.policy} "
          f"run_id={plan.run_id}")
    print("[cross-host] mode: plan validation only - no ONNX session, no GPU work")
    checks = plan_checks(plan, gate, dxgi_error, probe_clis=True)
    failed = [c for c in checks if not c["ok"]]
    for check in checks:
        if not check["ok"]:
            print(f"[cross-host] PLAN FAIL {check['check']}: {check['detail']}")
    print(f"[cross-host] plan checks: {len(checks) - len(failed)} OK, {len(failed)} FAIL")

    if dxgi_error:
        print(f"[cross-host] physical gate: UNREADABLE ({dxgi_error})")
    elif gate.get("status") == "PASS":
        print(f"[cross-host] physical gate: PASS ({gate.get('detail')})")
    else:
        print(f"[cross-host] physical gate: WOULD REFUSE reason={gate.get('reason')} "
              f"({gate.get('detail')})")
        print("[cross-host] an executed run would stop before any GPU stage with "
              "exit code 34 and would write no evidence under "
              f"{_redact_path(plan.report_root / plan.gfx)}/")

    print("[cross-host] commands that WOULD run:")
    commands = planned_commands(plan, plan.run_dir)
    for stage, argv_list in commands.items():
        for argv in argv_list:
            print(f"  {stage:<20} " + " ".join(_redact_argv(argv)))

    verdict = cross_host.gfx_verdict(plan.gfx, "CROSS_HOST_HARNESS_READY")
    report = _base_report(plan, gate, "dry-run")
    report["stages"] = _planned_stage_records(plan, plan.run_dir)
    report["plan_checks"] = checks
    report["final_verdict"] = verdict
    report["exit_code"] = 0 if not failed else 1
    if failed:
        report["notes"].append(f"{len(failed)} plan check(s) failed")
    print(f"[cross-host] final verdict: {verdict}")
    path = _emit(plan, report)
    if path is not None:
        print(f"[cross-host] report: {_redact_path(path)}")
    return 0 if not failed else 1


# --- execution ------------------------------------------------------------------


def _read_adapters() -> tuple[list, str | None]:
    try:
        return enum_dxgi_adapters(), None
    except Exception as exc:  # noqa: BLE001 - DXGI absence is an honest BLOCKED
        return [], f"{type(exc).__name__}: {exc}"


def _gate_unreadable(detail: str) -> dict:
    return {
        "requested_gfx": None,
        "status": "BLOCKED",
        "reason": "DXGI_ENUMERATION_FAILED",
        "detail": detail,
        "live_gfx": None,
        "expected_device_ids": [],
        "observed_device_ids": [],
        "observed_adapters": [],
        "selected_device_id": None,
        "selected_luid": None,
        "did_match": None,
    }


def _blocked_by_gate(plan: Plan, gate: dict, mode: str) -> int:
    print(f"[cross-host] HARD GATE REFUSED: {gate.get('reason')} - {gate.get('detail')}")
    print("[cross-host] no ONNX session, EP probe or GPU stage was started")
    print(f"[cross-host] no evidence directory was created under "
          f"{_redact_path(plan.report_root / plan.gfx)}/")
    stages = [
        _stage("detect_host", "BLOCKED", reason=str(gate.get("reason")),
               detail=str(gate.get("detail"))),
        _stage("check_dependencies", "NOT_RUN", reason="PHYSICAL_GATE_REFUSED"),
        _stage("ep_probe", "SKIPPED", reason="PHYSICAL_GATE_REFUSED"),
    ]
    for stage in ("device_canary", "tier_a", "multi_input_oracle", "benchmarks", "soak"):
        stages.append(_gate_blocked_stage(stage, gate))
    stages.append(_stage("evidence_freeze", "SKIPPED", reason="NO_PHYSICAL_EVIDENCE"))
    verdict = cross_host.gfx_verdict(plan.gfx, "CROSS_HOST_HARNESS_READY")
    stages.append(_stage("final_verdict", "PASS", detail=verdict,
                         extra={"verdict": verdict}))
    report = _base_report(plan, gate, mode)
    report["stages"] = stages
    report["final_verdict"] = verdict
    report["exit_code"] = 34
    report["notes"].append("WRONG_PHYSICAL_DEVICE is never converted into a GPU claim")
    path = _emit(plan, report)
    if path is not None:
        print(f"[cross-host] report: {_redact_path(path)}")
    print(f"[cross-host] final verdict: {verdict}")
    return 34


def _unreadable_dxgi(plan: Plan, gate: dict, mode: str) -> int:
    stages = [_stage("detect_host", "BLOCKED", reason=str(gate.get("reason")),
                     detail=str(gate.get("detail")))]
    for stage in STAGES[1:-1]:
        stages.append(_stage(stage, "NOT_RUN", reason="DEVICE_IDENTITY_UNREADABLE"))
    verdict = cross_host.gfx_verdict(plan.gfx, "PHYSICAL_PENDING")
    stages.append(_stage("final_verdict", "BLOCKED", detail=verdict,
                         extra={"verdict": verdict}))
    report = _base_report(plan, gate, mode)
    report["stages"] = stages
    report["final_verdict"] = verdict
    report["exit_code"] = 2
    path = _emit(plan, report)
    if path is not None:
        print(f"[cross-host] report: {_redact_path(path)}")
    print(f"[cross-host] final verdict: {verdict}")
    return 2


def _existing_evidence_problem(plan: Plan) -> str | None:
    run_dir = plan.run_dir
    if (run_dir / contract_mod.RUN_CONTRACT_NAME).exists():
        return f"{_redact_path(run_dir)} already has RUN_CONTRACT.json"
    if (run_dir / "receipts").is_dir() and any((run_dir / "receipts").glob("receipt_*.json")):
        return f"{_redact_path(run_dir)} already has immutable receipts"
    return None


def run_execute(plan: Plan, gate: dict, dxgi_error: str | None) -> int:
    if dxgi_error:
        return _unreadable_dxgi(plan, gate, "execute")
    if gate.get("status") != "PASS":
        return _blocked_by_gate(plan, gate, "execute")

    problem = _existing_evidence_problem(plan)
    if problem:
        print(f"[cross-host] FAIL: refusing to reuse an existing run: {problem}. "
              "Create a new --run-id.")
        return 2

    run_dir = plan.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    stages: list[dict] = []
    ep_results: dict[str, str] = {}
    stage_status: dict[str, str] = {}

    # 1) detect_host — DXGI/DID read + non-identifying host fingerprint.
    try:
        from radeon_ort import host_probe
        fingerprint = host_probe.collect_host(
            gfx=plan.gfx, gpu_device_ids=target_device_ids(plan.gfx))
        (run_dir / "HOST_FINGERPRINT.json").write_text(
            json.dumps(fingerprint, indent=2, default=str) + "\n", encoding="utf-8")
        host_detail = f"live gfx={gate.get('live_gfx')} DID={gate.get('selected_device_id')}"
    except Exception as exc:  # noqa: BLE001 - WMI failure must not fake identity
        host_detail = f"host fingerprint unavailable: {type(exc).__name__}"
    detect = _stage("detect_host", "PASS", detail=host_detail,
                    extra={"physical_gate": gate})
    stages.append(detect)
    stage_status["detect_host"] = "PASS"
    _print_stage(detect)

    # 2) check_dependencies — no GPU work.
    checks = plan_checks(plan, gate, dxgi_error, probe_clis=False)
    failed = [c for c in checks if not c["ok"]]
    dep = _stage("check_dependencies", "PASS" if not failed else "FAIL",
                 reason=None if not failed else "DEPENDENCY_MISSING",
                 detail=(f"{len(checks) - len(failed)}/{len(checks)} checks passed"
                         if not failed else "; ".join(c["check"] for c in failed[:6])),
                 extra={"checks": checks})
    stages.append(dep)
    stage_status["check_dependencies"] = dep["status"]
    _print_stage(dep)
    if failed:
        return _finish_early(plan, gate, stages, stage_status, ep_results,
                             "dependency check failed")

    # 3) ep_probe — read-only catalog probe; never creates a session.
    probe_argv = cmd_ep_probe(plan, run_dir)
    result, record = _run_stage_command(probe_argv, timeout_s=max(120, plan.timeout_s))
    payload = _load_json(run_dir / "EP_PROBE.json")
    ep_info = _ep_probe_classify(plan, payload, result["rc"])
    ep_stage = _stage("ep_probe", ep_info["status"], reason=ep_info["reason"],
                      detail=ep_info["detail"], commands=[record],
                      extra={"amdgpu_state": ep_info["amdgpu_state"],
                             "amdgpu_ready": ep_info["amdgpu_ready"],
                             "dml_ready": ep_info["dml_ready"]})
    stages.append(ep_stage)
    stage_status["ep_probe"] = ep_stage["status"]
    _print_stage(ep_stage)
    if ep_info["status"] == "FAIL":
        return _finish_early(plan, gate, stages, stage_status, ep_results,
                             "EP probe could not read the environment")
    if ep_info["status"] == "BLOCKED":
        for stage in ("device_canary", "tier_a", "multi_input_oracle",
                      "benchmarks", "soak"):
            rec = _stage(stage, "SKIPPED", reason="EP_UNAVAILABLE",
                         detail=ep_info["reason"])
            stages.append(rec)
            stage_status[stage] = rec["status"]
        for ep in ep_scope(plan.policy):
            ep_results.setdefault(ep, "BLOCKED_EP")
        return _finish(plan, gate, stages, stage_status, ep_results)

    ready = _ready_eps(plan, ep_info)

    # 4) device_canary — per-EP, identity-pinned, profiler-checked.
    canary_stage = _canary_outcomes(plan, run_dir, ready, plan.timeout_s)
    canary_rec = _stage("device_canary", canary_stage["status"],
                        reason=(None if canary_stage["status"] == "PASS"
                                else "CANARY_DID_NOT_PASS"),
                        commands=canary_stage["commands"],
                        extra={"ep_results": canary_stage["ep_results"]})
    stages.append(canary_rec)
    stage_status["device_canary"] = canary_rec["status"]
    ep_results.update(canary_stage["ep_results"])
    _print_stage(canary_rec)
    if canary_rec["status"] == "FAIL":
        return _finish_early(plan, gate, stages, stage_status, ep_results,
                             "device canary failed", run_dir=run_dir)

    # 5) tier_a — the existing matrix orchestrator.
    argv = cmd_tier_a(plan)
    timeout = max(plan.timeout_s, 600 if plan.tier == "smoke" else 21_600)
    result, record = _run_stage_command(argv, timeout_s=timeout)
    matrix_exit = _load_json(run_dir / "MATRIX_EXIT.json") or {}
    tier_status = ("PASS" if result["rc"] == 0
                   else "FAIL" if result["rc"] == 1
                   else "BLOCKED" if result["rc"] == 2 else "FAIL")
    tier_rec = _stage("tier_a", tier_status,
                      reason=(None if tier_status == "PASS" else
                              str(matrix_exit.get("classification") or f"rc={result['rc']}")),
                      commands=[record],
                      extra={"matrix_exit_classification":
                             matrix_exit.get("classification"),
                             "matrix_audit_counts": matrix_exit.get("audit_counts")})
    stages.append(tier_rec)
    stage_status["tier_a"] = tier_rec["status"]
    _print_stage(tier_rec)
    receipt_outcomes = _collect_receipt_outcomes(run_dir)
    ep_results.update(receipt_outcomes)
    if tier_status == "FAIL":
        return _finish_early(plan, gate, stages, stage_status, ep_results,
                             "tier A matrix failed", run_dir=run_dir)

    # 6) multi_input_oracle — task correctness over several inputs.
    multi_statuses: dict[str, str] = {}
    multi_records: list[dict] = []
    for ep in _multi_eps(plan, ready):
        m_argv = cmd_multi_input(plan, run_dir, ep)
        m_result, m_record = _run_stage_command(m_argv, timeout_s=plan.timeout_s)
        m_status = ("PASS" if m_result["rc"] == 0
                    else "BLOCKED" if m_result["rc"] == 2 else "FAIL")
        m_record["classification"] = m_status
        multi_records.append(m_record)
        multi_statuses[ep] = m_status
    if not multi_statuses:
        multi_stage = _stage("multi_input_oracle", "BLOCKED",
                             reason="NO_READY_EP")
    else:
        agg = _aggregate(multi_statuses)
        multi_stage = _stage("multi_input_oracle", agg,
                             reason=None if agg == "PASS" else "TASK_ORACLE_DID_NOT_PASS",
                             commands=multi_records,
                             extra={"ep_results": multi_statuses})
    stages.append(multi_stage)
    stage_status["multi_input_oracle"] = multi_stage["status"]
    _print_stage(multi_stage)
    if multi_stage["status"] == "FAIL":
        return _finish_early(plan, gate, stages, stage_status, ep_results,
                             "multi-input task oracle failed", run_dir=run_dir)

    # 7) benchmarks — hash-bound latency for the ready EP(s).
    input_npy = _find_bench_input(plan, run_dir) or _make_bench_input(plan, run_dir)
    bench_statuses: dict[str, str] = {}
    bench_records: list[dict] = []
    if input_npy is None:
        bench_stage = _stage("benchmarks", "SKIPPED", reason="NO_INPUT_BUNDLE")
    else:
        for ep in _multi_eps(plan, ready):
            b_argv = cmd_bench(plan, run_dir, ep, input_npy)
            b_result, b_record = _run_stage_command(b_argv, timeout_s=plan.timeout_s)
            b_status = ("PASS" if b_result["rc"] == 0
                        else "BLOCKED" if b_result["rc"] == 2 else "FAIL")
            b_record["classification"] = b_status
            bench_records.append(b_record)
            bench_statuses[ep] = b_status
        agg = _aggregate(bench_statuses) if bench_statuses else "BLOCKED"
        bench_stage = _stage("benchmarks", agg,
                             reason=None if agg == "PASS" else "BENCHMARK_DID_NOT_PASS",
                             commands=bench_records,
                             extra={"ep_results": bench_statuses,
                                    "input_redacted": _redact_path(input_npy)})
    stages.append(bench_stage)
    stage_status["benchmarks"] = bench_stage["status"]
    _print_stage(bench_stage)

    # 8) soak — bounded single-GPU stability run.
    if plan.skip_soak:
        soak_stage = _stage("soak", "SKIPPED", reason="SKIP_SOAK_REQUESTED")
    elif plan.tier == "smoke":
        soak_stage = _stage("soak", "SKIPPED", reason="TIER_SMOKE")
    elif input_npy is None or not ready:
        soak_stage = _stage("soak", "SKIPPED", reason="NO_INPUT_OR_EP")
    else:
        ep = ready[0]
        s_argv = cmd_soak(plan, ep, input_npy)
        s_timeout = int(soak_budget_minutes(plan) * 60) + 300
        s_result, s_record = _run_stage_command(s_argv, timeout_s=s_timeout)
        s_status = ("PASS" if s_result["rc"] == 0
                    else "BLOCKED" if s_result["rc"] == 2 else "FAIL")
        soak_stage = _stage("soak", s_status,
                            reason=None if s_status == "PASS" else f"rc={s_result['rc']}",
                            commands=[s_record], extra={"ep": ep})
    stages.append(soak_stage)
    stage_status["soak"] = soak_stage["status"]
    _print_stage(soak_stage)

    return _finish(plan, gate, stages, stage_status, ep_results, run_dir=run_dir)


def _finish_early(plan: Plan, gate: dict, stages: list[dict],
                  stage_status: dict[str, str], ep_results: dict[str, str],
                  reason: str, *, run_dir: Path | None = None) -> int:
    for stage in STAGES:
        if stage in stage_status or stage in ("evidence_freeze", "final_verdict"):
            continue
        rec = _stage(stage, "SKIPPED", reason=f"STOPPED_AFTER_FAILURE: {reason}")
        stages.append(rec)
        stage_status[stage] = rec["status"]
    return _finish(plan, gate, stages, stage_status, ep_results, run_dir=run_dir)


def _finish(plan: Plan, gate: dict, stages: list[dict],
            stage_status: dict[str, str], ep_results: dict[str, str],
            *, run_dir: Path | None = None) -> int:
    # evidence_freeze runs even after a FAIL so failures are preserved and
    # independently verifiable; it is skipped only when no receipt exists.
    if "evidence_freeze" not in stage_status:
        freeze = (_freeze_evidence(plan, run_dir, gate)
                  if run_dir is not None else None)
        if freeze is None:
            freeze = _stage("evidence_freeze", "SKIPPED", reason="NO_RECEIPTS")
        stages.append(freeze)
        stage_status["evidence_freeze"] = freeze["status"]
        _print_stage(freeze)

    verdict = _decide_verdict(plan, gate=gate, dry_run=False,
                             stage_statuses=stage_status, ep_results=ep_results)
    if any(status == "FAIL" for status in stage_status.values()):
        verdict = cross_host.gfx_verdict(plan.gfx, "NO_GO")
    verdict_status = ("PASS" if verdict.endswith(("_PHYSICAL_GO", "_CONDITIONAL_GO",
                                                  "_CROSS_HOST_HARNESS_READY"))
                      else "BLOCKED" if verdict.endswith(("_BLOCKED_EP", "_PHYSICAL_PENDING"))
                      else "FAIL")
    final = _stage("final_verdict", verdict_status, detail=verdict,
                   extra={"verdict": verdict, "ep_results": ep_results})
    stages.append(final)
    stage_status["final_verdict"] = final["status"]
    _print_stage(final)

    exit_code = _exit_code(verdict)
    report = _base_report(plan, gate, "execute")
    report["stages"] = stages
    report["final_verdict"] = verdict
    report["exit_code"] = exit_code
    report["ep_results"] = ep_results
    report["notes"].append(
        "CPU execution is never an implicit fallback; blocked stages stay blocked")
    if verdict.endswith("_CROSS_HOST_HARNESS_READY"):
        report["notes"].append(
            "harness readiness only: no physical GPU PASS is claimed for this gfx")
    path = _emit(plan, report)
    if path is not None:
        print(f"[cross-host] report: {_redact_path(path)}")
    print(f"[cross-host] final verdict: {verdict} (exit {exit_code})")
    return exit_code


def _exit_code(verdict: str) -> int:
    if verdict.endswith("_PHYSICAL_GO"):
        return 0
    if verdict.endswith("_NO_GO"):
        return 1
    return 2


def _write_cross_host_csv(path: Path, matrix: dict) -> None:
    hosts = matrix.get("hosts") or []
    fieldnames = ["checkpoint", "family", "task"]
    for gfx in hosts:
        fieldnames += [f"{gfx}_AMDGPU", f"{gfx}_DML"]
    rows = []
    for row in matrix.get("rows") or []:
        flat = {"checkpoint": row.get("checkpoint"), "family": row.get("family"),
                "task": row.get("task")}
        for gfx in hosts:
            results = (row.get("results") or {}).get(gfx) or {}
            flat[f"{gfx}_AMDGPU"] = results.get("amdgpu", "NOT_RUN")
            flat[f"{gfx}_DML"] = results.get("dml", "NOT_RUN")
        rows.append(flat)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _freeze_evidence(plan: Plan, run_dir: Path, gate: dict) -> dict | None:
    receipts = sorted((run_dir / "receipts").glob("receipt_*.json"))
    if not receipts:
        return None
    records: list[dict] = []
    steps = [
        ("redact", cmd_redact(plan, run_dir)),
        ("finalize", cmd_finalize(plan, run_dir)),
        ("compatibility", cmd_compat(plan, run_dir)),
    ]
    for _name, argv in steps:
        result, record = _run_stage_command(argv, timeout_s=max(300, plan.timeout_s))
        records.append(record)
        if result["rc"] != 0:
            return _stage("evidence_freeze", "FAIL",
                          reason=f"EVIDENCE_STEP_FAILED rc={result['rc']}",
                          commands=records)
    receipts_data = [r for r in (_load_json(p) for p in receipts) if r]
    matrix = cross_host.build_cross_host_matrix(receipts_data)
    (run_dir / "CROSS_HOST_MATRIX.json").write_text(
        json.dumps(matrix, indent=2) + "\n", encoding="utf-8")
    _write_cross_host_csv(run_dir / "CROSS_HOST_MATRIX.csv", matrix)
    manifest = write_manifest(run_dir, plan.run_id, "runner/cross_host_validate.py")
    verify_argv = cmd_verify(plan, run_dir)
    result, record = _run_stage_command(verify_argv, timeout_s=max(300, plan.timeout_s))
    records.append(record)
    if result["rc"] != 0:
        return _stage("evidence_freeze", "FAIL", reason="VERIFY_RECEIPTS_FAILED",
                      commands=records)

    # Cross-host guard: the core strict verifier checks schema/derived status,
    # but it does not compare a receipt's DID against its gfx label. Refuse a
    # receipt/contract produced for another architecture here, by name.
    arch_problems: list[str] = []
    contract = None
    if (run_dir / contract_mod.RUN_CONTRACT_NAME).is_file():
        try:
            contract = contract_mod.load_run_contract(run_dir)
        except contract_mod.ContractError as exc:
            return _stage("evidence_freeze", "FAIL", reason="RUN_CONTRACT_INVALID",
                          detail=str(exc), commands=records)
        arch_problems.extend(
            cross_host.run_contract_architecture_problems(contract, gate))
    for receipt_path in receipts:
        receipt = _load_json(receipt_path)
        if receipt:
            arch_problems.extend(
                cross_host.receipt_architecture_problems(receipt, contract))
    if arch_problems:
        return _stage("evidence_freeze", "FAIL",
                      reason="CROSS_ARCH_EVIDENCE_REFUSED",
                      detail="; ".join(arch_problems[:6]), commands=records)

    check_argv = cmd_redact(plan, run_dir, check=True)
    result, record = _run_stage_command(check_argv, timeout_s=max(120, plan.timeout_s))
    records.append(record)
    if result["rc"] != 0:
        return _stage("evidence_freeze", "FAIL", reason="REDACTION_CHECK_FAILED",
                      commands=records)
    return _stage("evidence_freeze", "PASS",
                  detail=f"manifest {manifest.name}; verify_receipts OK",
                  commands=records)


# --- entrypoint -----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv)
    plan = build_plan(a)
    adapters, dxgi_error = _read_adapters()
    if dxgi_error:
        gate = _gate_unreadable(dxgi_error)
    else:
        gate = cross_host.gate_physical_target(adapters, plan.gfx)
    if plan.dry_run:
        return run_dry(plan, gate, dxgi_error)
    return run_execute(plan, gate, dxgi_error)


if __name__ == "__main__":
    sys.exit(main())
