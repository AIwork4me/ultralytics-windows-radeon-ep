"""Experiment A4 — Catalog Registration Canary (conditional; fail-closed).

Mission A_GFX1100_CATALOG_VS_DIRECT_REGISTRATION (step 7).

Executes ONLY IF the official catalog (A1 evidence) lists an AMDGPU provider
AND that provider is Ready AND hands a usable library path. Per the mission
and AGENTS.md rule 6 (no system changes):
  - provider listed but NotReady → record `CATALOG_NOT_READY`, never call
    ensure_ready / ensure_ready_async / try_register (unauthorized install);
  - Ready but library_path empty/error → record `CATALOG_READY_NO_LIBRARY_PATH`
    (BYO registration remains the only DLL path — A3 proves that one);
  - usable library path → register THAT path (not the MSIX-resolved one) in a
    fresh ORT process and run the same canary; only real GPU execution grants
    `GFX1100_CATALOG_AMDGPU_GPU_EXECUTED`. The A3 direct PASS is never
    borrowed for this verdict.

Usage (mission venv):
  python -I repro/experiment_a_catalog_canary.py --gfx gfx1100 \
      --catalog-probe reports/experiment_a/CATALOG_PROBE.json \
      --onnx <path> --npy <path> --work-dir <scratch> \
      --out reports/experiment_a/CATALOG_REGISTRATION_CANARY.json \
      --mission-venv <python>
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from radeon_ort.redaction import redact_path  # noqa: E402


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gfx", default="gfx1100")
    ap.add_argument("--catalog-probe", required=True)
    ap.add_argument("--onnx", required=True)
    ap.add_argument("--npy", required=True)
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mission-venv", required=True)
    a = ap.parse_args()

    probe = json.loads(Path(a.catalog_probe).read_text(encoding="utf-8"))
    amd = (probe.get("runs") or [{}])[0].get("amdgpu_entry")

    out: dict = {
        "mission": "A_GFX1100_CATALOG_VS_DIRECT_REGISTRATION",
        "experiment": "A4_CATALOG_REGISTRATION_CANARY",
        "gfx": a.gfx,
        "captured_utc": _utcnow(),
        "catalog_evidence_source": str(Path(a.catalog_probe).name),
        "authorization_boundary": (
            "AGENTS.md rule 6: no system changes. ensure_ready / "
            "ensure_ready_async / try_register are NOT called on this host; "
            "a NotReady provider is recorded, never installed."),
    }

    if not probe.get("amdgpu_listed_any_run"):
        out["verdict"] = "GFX1100_CATALOG_EMPTY"
        out["gpu_attempted"] = False
        _finish(a.out, out)
        return 0

    rs = amd.get("ready_state")
    rs_name = amd.get("ready_state_name")
    lib = amd.get("library_path_redacted")
    lib_err = (isinstance(lib, dict) and lib.get("error")) or ""

    out["catalog_amdgpu"] = {
        "ready_state": rs, "ready_state_name": rs_name,
        "library_path_redacted": lib,
    }

    if rs != 0:
        out["verdict"] = "CATALOG_NOT_READY"
        out["gpu_attempted"] = False
        out["detail"] = (
            f"AMDGPUExecutionProvider listed with ready_state={rs} "
            f"({rs_name}); making it ready requires ensure_ready → a "
            f"download/install system change that is not authorized on this "
            f"host. library_path read: {lib_err or lib!r}.")
        _finish(a.out, out)
        return 0

    if not lib or lib_err:
        out["verdict"] = "CATALOG_READY_NO_LIBRARY_PATH"
        out["gpu_attempted"] = False
        out["detail"] = ("provider reports Ready but exposes no library path "
                         f"({lib_err or 'empty'}) — registration from catalog "
                         "metadata impossible; BYO/MSIX (A3) is the only DLL path.")
        _finish(a.out, out)
        return 0

    # Ready + library path: run the SAME canary worker with the CATALOG path
    import subprocess
    work = Path(a.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    raw_lib = amd.get("_library_path_raw")  # only present if unredacted passthrough
    if not raw_lib:
        out["verdict"] = "CATALOG_LIBRARY_PATH_UNRESOLVABLE"
        out["gpu_attempted"] = False
        out["detail"] = ("redacted evidence cannot be resolved back to a real "
                         "path for execution (by design); rerun probe with "
                         "raw-path handoff to execute this branch.")
        _finish(a.out, out)
        return 0

    gpu_json = work / "catalog_gpu_worker.json"
    gpu_npz = work / "catalog_gpu_outputs.npz"
    cmd = [str(Path(a.mission_venv)), "-I",
           str(REPO_ROOT / "repro" / "experiment_a_canary_worker.py"),
           "--onnx", a.onnx, "--npy", a.npy, "--dll", raw_lib,
           "--alias", "AMDGPUExecutionProvider", "--gfx", a.gfx,
           "--out-npz", str(gpu_npz), "--out-json", str(gpu_json),
           "--bootstrap", "catalog",
           "--bootstrap-note", "library path from Windows ML 2.x catalog"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                       cwd=str(REPO_ROOT))
    out["worker_exit_code"] = r.returncode
    try:
        w = json.loads(gpu_json.read_text(encoding="utf-8"))
        out["worker"] = w
        if (r.returncode == 0 and w.get("target_ep_profile_nodes", 0) > 0
                and w.get("cpu_profile_nodes", 1) == 0
                and w.get("ep_device_luid_match", {}).get("verified_gfx1100")):
            out["verdict"] = "GFX1100_CATALOG_AMDGPU_GPU_EXECUTED"
            out["gpu_attempted"] = True
        else:
            out["verdict"] = "CATALOG_REGISTRATION_FAILED"
            out["gpu_attempted"] = True
    except Exception as e:  # noqa: BLE001
        out["verdict"] = "CATALOG_REGISTRATION_FAILED"
        out["worker_error"] = f"{type(e).__name__}: {e}"
        out["stderr_tail"] = (r.stderr or "")[-600:]
    _finish(a.out, out)
    return 0


def _finish(out: str, payload: dict) -> None:
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[A4] verdict={payload.get('verdict')} -> {p}")


if __name__ == "__main__":
    sys.exit(main())
