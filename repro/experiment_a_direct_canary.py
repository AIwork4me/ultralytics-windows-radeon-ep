"""Experiment A3 — Direct Registration Canary (driver).

Mission A_GFX1100_CATALOG_VS_DIRECT_REGISTRATION (step 6).

Proves (or refuses) `GFX1100_DIRECT_AMDGPU_GPU_EXECUTED` under the mission's
hard gates:
    actual_ep = AMDGPUExecutionProvider
    target_ep_profile_nodes > 0
    cpu_profile_nodes = 0
    numeric_parity = PASS          (by-name, per-element vs CPU ORT subprocess)
    task_parity = PASS_TASK        (PT oracle, pinned Ultralytics 8.4.215)
    device_identity = VERIFIED_GFX1100 (DXGI DID 0x744C + EP-device LUID match)

Every subprocess is fresh-interpreter with file-redirected output and a
bounded watchdog (RCA1 lesson). Assembly happens only from files.

Usage (mission venv):
  python -I repro/experiment_a_direct_canary.py --gfx gfx1100 \
      --onnx <path> --npy <path> --pt <path> \
      --work-dir <scratch> --out reports/experiment_a/DIRECT_REGISTRATION_CANARY.json \
      --mission-venv <python> --export-venv <python>
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from radeon_ort.redaction import redact_path  # noqa: E402
from runner.unified_canary import compare_named_outputs  # noqa: E402


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_step(name: str, cmd: list[str], work_dir: Path, timeout_s: int) -> dict:
    """Fresh-process step, FILE-redirected stdout/stderr, bounded watchdog."""
    log_out = work_dir / f"{name}.stdout.log"
    log_err = work_dir / f"{name}.stderr.log"
    t0 = time.monotonic()
    try:
        with log_out.open("wb") as fo, log_err.open("wb") as fe:
            proc = subprocess.run(cmd, stdout=fo, stderr=fe, timeout=timeout_s,
                                  cwd=str(REPO_ROOT))
        wall = round(time.monotonic() - t0, 2)
        return {"step": name, "cmd_python": Path(cmd[0]).name,
                "cmd_script": Path(cmd[-1] if cmd[-1].endswith(".py") else cmd[2]).name
                if len(cmd) > 2 else "",
                "exit_code": proc.returncode, "wall_s": wall, "timeout": False}
    except subprocess.TimeoutExpired:
        return {"step": name, "exit_code": None, "wall_s": timeout_s,
                "timeout": True}
    except Exception as e:  # noqa: BLE001
        return {"step": name, "exit_code": None,
                "error": f"{type(e).__name__}: {e}"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gfx", default="gfx1100")
    ap.add_argument("--onnx", required=True)
    ap.add_argument("--npy", required=True)
    ap.add_argument("--pt", required=True)
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mission-venv", required=True)
    ap.add_argument("--export-venv", required=True)
    ap.add_argument("--gpu-timeout-s", type=int, default=900)
    a = ap.parse_args()

    work = Path(a.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    onnx_p, npy_p, pt_p = Path(a.onnx), Path(a.npy), Path(a.pt)
    mpy = str(Path(a.mission_venv))
    epy = str(Path(a.export_venv))

    result: dict = {
        "mission": "A_GFX1100_CATALOG_VS_DIRECT_REGISTRATION",
        "experiment": "A3_DIRECT_REGISTRATION_CANARY",
        "gfx": a.gfx,
        "captured_utc": _utcnow(),
        "model": {"onnx_name": onnx_p.name, "onnx_sha256": _sha256(onnx_p),
                  "input_npy_sha256": _sha256(npy_p), "pt_name": pt_p.name,
                  "pt_sha256": _sha256(pt_p)},
        "registration_mode": "DIRECT (ort.register_execution_provider_library "
                             "with MSIX-resolved amdgpu-ep.dll; NOT catalog)",
        "steps": [],
    }

    # 0) resolve the DLL via Get-AppxPackage (read-only)
    from radeon_ort.rca2a_ep import _appx_ep_dll_path
    dll = _appx_ep_dll_path()
    if not dll:
        result["verdict"] = "BLOCKED_EP_DLL_NOT_FOUND"
        _finish(a.out, result)
        return 2
    result["ep_dll"] = {"name": Path(dll).name, "sha256": _sha256(Path(dll)),
                        "path_redacted": redact_path(dll),
                        "resolution": "Get-AppxPackage InstallLocation (read-only)"}

    # 1) GPU worker (fresh interpreter)
    gpu_npz = work / "gpu_outputs.npz"
    gpu_json = work / "gpu_worker.json"
    st = run_step("gpu_worker",
                  [mpy, "-I", str(REPO_ROOT / "repro" / "experiment_a_canary_worker.py"),
                   "--onnx", str(onnx_p), "--npy", str(npy_p), "--dll", dll,
                   "--alias", "AMDGPUExecutionProvider", "--gfx", a.gfx,
                   "--out-npz", str(gpu_npz), "--out-json", str(gpu_json),
                   "--bootstrap", "byo_msix",
                   "--bootstrap-note", "MSIX DLL registered directly in-process"],
                  work, a.gpu_timeout_s)
    result["steps"].append(st)
    if st["exit_code"] != 0:
        result["verdict"] = "DIRECT_GPU_RUN_FAILED"
        result["worker_receipt"] = _load(gpu_json)
        _finish(a.out, result)
        return 2
    gpu = _load(gpu_json)
    result["gpu_worker"] = gpu

    # 2) CPU reference (fresh interpreter)
    cpu_npz = work / "cpu_outputs.npz"
    cpu_json = work / "cpu_worker.json"
    st = run_step("cpu_reference",
                  [mpy, "-I", str(REPO_ROOT / "repro" / "experiment_a_cpu_worker.py"),
                   "--onnx", str(onnx_p), "--npy", str(npy_p),
                   "--out-npz", str(cpu_npz), "--out-json", str(cpu_json)],
                  work, 600)
    result["steps"].append(st)
    if st["exit_code"] != 0:
        result["verdict"] = "CPU_REFERENCE_FAILED"
        _finish(a.out, result)
        return 2

    # 3) numeric parity (by-name, per-element)
    import numpy as np
    g = dict(np.load(gpu_npz))
    c = dict(np.load(cpu_npz))
    num = compare_named_outputs(
        {k: v for k, v in c.items()}, {k: v for k, v in g.items()},
        atol=2e-3, rtol=2e-2)
    result["numeric_parity"] = num

    # 4) postprocess GPU outputs (export venv: pinned ultralytics NMS)
    cand_json = work / "gpu_post.json"
    cand_npz = work / "gpu_post.npz"
    st = run_step("gpu_postprocess",
                  [epy, "-I", str(REPO_ROOT / "src" / "radeon_ort" / "onnx_postprocess.py"),
                   "--npz", str(gpu_npz), "--task", "detect", "--imgsz", "640",
                   "--out-json", str(cand_json), "--out-npz", str(cand_npz)],
                  work, 600)
    result["steps"].append(st)

    # 5) PT task oracle (export venv, same tensor bytes)
    pt_json = work / "pt_oracle.json"
    pt_npz = work / "pt_oracle.npz"
    st = run_step("pt_oracle",
                  [epy, "-I", str(REPO_ROOT / "src" / "radeon_ort" / "pt_oracle_v2.py"),
                   "--pt", str(pt_p), "--task", "detect", "--tensor-npy", str(npy_p),
                   "--out-json", str(pt_json), "--out-npz", str(pt_npz)],
                  work, 900)
    result["steps"].append(st)

    task = {"status": "NOT_RUN"}
    if cand_json.is_file() and pt_json.is_file():
        from radeon_ort import task_parity
        import yaml
        th = yaml.safe_load((REPO_ROOT / "configs" / "thresholds.yaml").read_text(
            encoding="utf-8"))
        task = task_parity.compare(
            json.loads(pt_json.read_text(encoding="utf-8")), str(pt_npz),
            json.loads(cand_json.read_text(encoding="utf-8")), str(cand_npz),
            "detect", th, imgsz=640)
    result["task_parity"] = task

    # 6) verdict under the mission's hard gates
    gates = {
        "actual_ep": gpu.get("ep_devices_after_register") and any(
            d["ep_name"] == "AMDGPUExecutionProvider"
            for d in gpu["ep_devices_after_register"]),
        "target_ep_profile_nodes": (gpu.get("target_ep_profile_nodes", 0) or 0) > 0,
        "cpu_profile_nodes": gpu.get("cpu_profile_nodes", 1) == 0,
        "numeric_parity": bool(num.get("allclose")),
        "task_parity": task.get("passed") is True,
        "device_identity": gpu.get("ep_device_luid_match", {}).get("verified_gfx1100",
                                                                  False),
    }
    result["gates"] = gates
    result["input_sha256"] = gpu.get("onnx_sha256")
    if all(gates.values()):
        result["verdict"] = "GFX1100_DIRECT_AMDGPU_GPU_EXECUTED"
        rc = 0
    else:
        result["verdict"] = "DIRECT_GATES_FAILED"
        rc = 1
    _finish(a.out, result)
    return rc


def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"missing": str(p.name)}


def _finish(out: str, result: dict) -> None:
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[A3] verdict={result.get('verdict')} -> {p}")


if __name__ == "__main__":
    sys.exit(main())
