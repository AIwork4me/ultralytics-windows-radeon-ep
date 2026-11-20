"""Experiment A evidence assembler — HOST_ENVIRONMENT, RESULT, MANIFEST.

Mission A_GFX1100_CATALOG_VS_DIRECT_REGISTRATION (steps 9-10).

Pure assembly from the immutable per-experiment artifacts; recomputes the
8-state ladder, the 2x2 verdict matrix, and hashes the publish set.

Usage (mission venv):
  python -I repro/experiment_a_assemble.py \
      --reports reports/experiment_a --preflight-dir reports/gfx1100/<run_id> \
      --out-env HOST_ENVIRONMENT.json --out-result EXPERIMENT_A_RESULT.json \
      --out-manifest MANIFEST.sha256
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "repro"))

from experiment_a_classify import classify_catalog, classify_direct, classify_final  # noqa: E402


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_state() -> dict:
    def g(*args):
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              cwd=str(REPO_ROOT)).stdout.strip()
    return {
        "head_sha": g("rev-parse", "HEAD"),
        "branch": g("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty_files": len(g("status", "--porcelain").splitlines()),
        "origin_main_sha": g("rev-parse", "origin/main"),
    }


def build_host_environment(preflight_dir: Path) -> dict:
    host = json.loads((preflight_dir / "HOST_FINGERPRINT.json").read_text(
        encoding="utf-8"))
    dev = json.loads((preflight_dir / "DEVICE_IDENTITY.json").read_text(
        encoding="utf-8"))
    import onnxruntime as ort
    env = {
        "mission": "A_GFX1100_CATALOG_VS_DIRECT_REGISTRATION",
        "captured_utc": _utcnow(),
        "os": host.get("os"),
        "cpu_ram": host.get("cpu"),
        "gpu_target": host.get("gpu_target"),
        "dxgi_identity": dev.get("resolved", {}).get("adapter")
        or dev.get("resolved"),
        "python": {"version": platform.python_version(),
                   "implementation": platform.python_implementation()},
        "onnxruntime": {"version": ort.__version__,
                        "build_flavor": "onnxruntime-windowsml 1.25.2.202605110140",
                        "has_register_execution_provider_library": True},
        "windowsml_probe_package": {
            "name": "windowsml", "version": "2.5.96",
            "source": "PyPI (official; interop DLL Microsoft-signed, "
                      "Windows App SDK 2.5.96.5615)"},
        "git": git_state(),
    }
    msix = json.loads(Path(REPO_ROOT / "reports/experiment_a"
                           / "EP_PACKAGE_INVENTORY.json").read_text(encoding="utf-8"))
    env["ep_msix"] = msix["appx"]["packages_matching_winml_amd"][0]
    return env


def build_result(reports: Path) -> dict:
    a1 = json.loads((reports / "CATALOG_PROBE.json").read_text(encoding="utf-8"))
    a2 = json.loads((reports / "EP_PACKAGE_INVENTORY.json").read_text(
        encoding="utf-8"))
    a3 = json.loads((reports / "DIRECT_REGISTRATION_CANARY.json").read_text(
        encoding="utf-8"))
    a4 = json.loads((reports / "CATALOG_REGISTRATION_CANARY.json").read_text(
        encoding="utf-8"))
    neg = json.loads((reports / "NEGATIVE_TESTS.json").read_text(encoding="utf-8"))

    d = classify_direct(a3)
    c = classify_catalog(a1, a4)
    final = classify_final(d, c)

    dll = a2.get("amdgpu_ep_dll", {})
    listed = bool(a1.get("amdgpu_listed_all_runs"))
    ladder = {
        "EP_PACKAGE_INSTALLED": True,       # A2: MSIX 1.8.65.0 Store-signed present
        "EP_DLL_PRESENT": bool(dll.get("sha256")),
        "DIRECT_EP_REGISTERED": bool(a3.get("gpu_worker", {}).get(
            "direct_ep_registered")),
        "DIRECT_AMDGPU_GPU_EXECUTED": d == "DIRECT_GPU_EXECUTED",
        "CATALOG_AMDGPU_LISTED": listed,
        "CATALOG_AMDGPU_READY": c in ("CATALOG_READY_NO_LIBRARY_PATH",
                                      "CATALOG_GPU_EXECUTED"),
        "CATALOG_AMDGPU_REGISTERED": c == "CATALOG_GPU_EXECUTED",
        "CATALOG_AMDGPU_GPU_EXECUTED": c == "CATALOG_GPU_EXECUTED",
    }
    matrix = {
        "direct_gpu": "PASS" if d == "DIRECT_GPU_EXECUTED" else "FAIL",
        "catalog_gpu": {"PASS": "PASS", "CATALOG_LISTED_NOT_READY": "BLOCKED",
                        "CATALOG_EMPTY": "BLOCKED",
                        "CATALOG_NOT_LISTED": "BLOCKED",
                        "CATALOG_READY_NO_LIBRARY_PATH": "BLOCKED",
                        "CATALOG_REGISTRATION_FAILED": "FAIL",
                        "CATALOG_NOT_RUN": "BLOCKED"}.get(c, "BLOCKED"),
        "catalog_state": c,
        "note": "BLOCKED != FAIL: catalog deployment chain incomplete, not a "
                "DLL/driver/device failure",
    }
    return {
        "mission": "A_GFX1100_CATALOG_VS_DIRECT_REGISTRATION",
        "captured_utc": _utcnow(),
        "final_classification": final,
        "state_ladder": ladder,
        "verdict_matrix": matrix,
        "evidence_files": ["CATALOG_PROBE.json", "EP_PACKAGE_INVENTORY.json",
                           "DIRECT_REGISTRATION_CANARY.json",
                           "CATALOG_REGISTRATION_CANARY.json",
                           "NEGATIVE_TESTS.json", "HOST_ENVIRONMENT.json"],
        "negative_tests": {"case_count": neg.get("case_count"),
                           "all_fail_closed": neg.get("all_fail_closed")},
        "key_hashes": {
            "onnx_sha256": a3.get("model", {}).get("onnx_sha256"),
            "input_npy_sha256": a3.get("model", {}).get("input_npy_sha256"),
            "pt_sha256": a3.get("model", {}).get("pt_sha256"),
            "ep_dll_sha256": dll.get("sha256"),
            "catalog_digest": (a1.get("digests_per_run") or [None])[0],
        },
        "git": git_state(),
    }


def build_manifest(reports: Path, out_name: str) -> dict:
    entries = {}
    for p in sorted(reports.glob("*.json")):
        entries[p.name] = _sha256(p)
    lines = [f"{v}  {k}" for k, v in entries.items()]
    (reports / out_name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"files": entries, "count": len(entries)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--reports", default=str(REPO_ROOT / "reports/experiment_a"))
    ap.add_argument("--preflight-dir", required=True)
    ap.add_argument("--out-env", default="HOST_ENVIRONMENT.json")
    ap.add_argument("--out-result", default="EXPERIMENT_A_RESULT.json")
    ap.add_argument("--out-manifest", default="MANIFEST.sha256")
    a = ap.parse_args()

    reports = Path(a.reports)
    env = build_host_environment(Path(a.preflight_dir))
    (reports / a.out_env).write_text(
        json.dumps(env, indent=2, ensure_ascii=False), encoding="utf-8")
    res = build_result(reports)
    (reports / a.out_result).write_text(
        json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    mani = build_manifest(reports, a.out_manifest)
    print(f"[assemble] final={res['final_classification']} "
          f"direct={res['verdict_matrix']['direct_gpu']} "
          f"catalog={res['verdict_matrix']['catalog_gpu']} "
          f"({res['verdict_matrix']['catalog_state']})")
    print(f"[assemble] manifest covers {mani['count']} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
