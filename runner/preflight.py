"""python -m runner.preflight — real host fingerprint + EP catalog + canaries.

Writes reports/<gfx>/<run_id>/{HOST_FINGERPRINT.json, EP_INSTALL_AND_PROBE.json,
DEVICE_IDENTITY.json, GPU_CANARY.json} and prints a GO/BLOCKED summary.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from radeon_ort import device_identity, host_probe, redaction, windows_ml_ep_catalog
from radeon_ort.manifest import sha256_file
from radeon_ort.receipts import utc_now_iso
from runner import REPO_ROOT


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gfx", default="gfx1151")
    ap.add_argument("--gpu-needle", default="8060S")
    ap.add_argument("--reports-root", default=str(REPO_ROOT / "reports"))
    ap.add_argument("--run-id", default=None)
    a = ap.parse_args()

    run_id = a.run_id or ("PREFLIGHT_" + utc_now_iso().replace("-", "").replace(":", "").replace("T", "_").replace("Z", ""))
    out_dir = Path(a.reports_root) / a.gfx / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[preflight] run_id={run_id} out={out_dir}")

    # 1) host fingerprint
    host = host_probe.collect_host(gpu_name_needle=a.gpu_needle, gfx=a.gfx)
    (out_dir / "HOST_FINGERPRINT.json").write_text(json.dumps(host, indent=2), encoding="utf-8")
    print(f"[preflight] OS build {host['os']['build']}.{host['os']['ubr']} ({host['os']['display_version']})")
    print(f"[preflight] CPU {host['cpu']['cpu_name']} RAM {host['cpu']['ram_gb']}GB")
    t = host["gpu_target"]
    print(f"[preflight] GPU target: {t['name']} driver {t['driver_version']} ({t.get('driver_date')})")

    # 2) EP catalog probe
    ep = windows_ml_ep_catalog.catalog_probe()
    providers = windows_ml_ep_catalog.list_ort_providers()
    ep_data = {"captured_utc": utc_now_iso(), "api": ep.data["api"],
               "amdgpu_dlls": ep.data["amdgpu_dlls"],
               "register_attempt": ep.data["register_attempt"],
               "available_providers": providers}
    (out_dir / "EP_INSTALL_AND_PROBE.json").write_text(json.dumps(ep_data, indent=2), encoding="utf-8")
    print(f"[preflight] ort providers: {providers}")
    print(f"[preflight] register: {ep.data['register_attempt']}")

    # 3) device identity (DXGI -> LUID -> selection)
    ident = {}
    try:
        adapters = device_identity.enum_dxgi_adapters()
        ident["adapters"] = [_adapter_dict(x) for x in adapters]
        res = device_identity.resolve_target_adapter(adapters, gfx=a.gfx)
        if res.get("ok"):
            res["adapter"] = _adapter_dict(res["adapter"])
            host["gpu_target"].update({
                "verified": True,
                "identity_method": res["identity_method"] + "; PnP cross-check pending canary",
                "luid": res["adapter"]["luid_str"],
                "dedicated_vram_mb": res["adapter"]["dedicated_video_mb"],
                "shared_memory_mb": res["adapter"]["shared_sys_mb"],
            })
            (out_dir / "HOST_FINGERPRINT.json").write_text(json.dumps(host, indent=2), encoding="utf-8")
            ident["resolved"] = res
            print(f"[preflight] device identity OK: {res['adapter']['description']} LUID={res['adapter']['luid_str']}")
        else:
            ident["resolved"] = res
            print(f"[preflight] device identity FAILED: {res['reason']}")
    except OSError as e:
        ident["error"] = str(e)
        print(f"[preflight] DXGI enumeration failed: {e}")
    (out_dir / "DEVICE_IDENTITY.json").write_text(json.dumps(_jsonable(ident), indent=2), encoding="utf-8")

    # 4) canary (only if ORT importable and target resolved)
    canary = {"run_utc": utc_now_iso(), "status": "NOT_RUN"}
    resolved_ok = ident.get("resolved", {}).get("ok", False)
    if resolved_ok and providers:
        canary.update(_run_canary(out_dir, a.gfx))
    (out_dir / "GPU_CANARY.json").write_text(json.dumps(_jsonable(canary), indent=2), encoding="utf-8")
    print(f"[preflight] canary: {canary.get('status')}")

    leaks = redaction.scan_file(out_dir / "HOST_FINGERPRINT.json")
    print(f"[preflight] redaction scan (host fingerprint): {'CLEAN' if not leaks else leaks}")

    print(f"[preflight] DONE -> {out_dir}")
    ok = resolved_ok and canary.get("status", "").startswith("PASS")
    print(f"[preflight] verdict: {'GO' if ok else 'BLOCKED/INCOMPLETE (see artifacts)'}")
    return 0


def _adapter_dict(a) -> dict:
    return {
        "index": a.index, "description": a.description,
        "vendor_id": f"0x{a.vendor_id:04X}", "device_id": f"0x{a.device_id:04X}",
        "dedicated_video_mb": a.dedicated_video_mb, "shared_sys_mb": a.shared_sys_mb,
        "luid_str": a.luid_str, "is_software": a.is_software,
    }


def _run_canary(out_dir: Path, gfx: str) -> dict:
    """Canary ONNX (MatMul+Add) run on CPU, AMDGPU, DML in subprocesses; parity + nodes."""
    import subprocess
    import sys

    from radeon_ort.amdgpu_session import canary_model_path

    tmp = out_dir / "canary"
    tmp.mkdir(exist_ok=True)
    model = canary_model_path(tmp)
    inputs = device_identity.canary_inputs()
    feeds = {k: v.tolist() for k, v in inputs.items()}
    model_sha = sha256_file(model)

    results = {}
    ref_outputs = None
    for ep in ("cpu", "amdgpu", "dml"):
        case = {"ep": ep, "gfx": gfx, "onnx_path": str(model), "feeds": feeds,
                "atol": 2e-4, "rtol": 2e-4, "benchmark": False,
                "reference_outputs": None, "profile_prefix": f"canary_{ep}"}
        if ep != "cpu" and ref_outputs is not None:
            case["reference_outputs"] = ref_outputs
        case_p = tmp / f"case_{ep}.json"
        case_p.write_text(json.dumps(case), encoding="utf-8")
        out_p = tmp / f"frag_{ep}.json"
        r = subprocess.run(
            [sys.executable, "-I", str(REPO_ROOT / "runner" / "_ep_worker.py"),
             "--case", str(case_p), "--out", str(out_p)],
            capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace")
        frag = {}
        if out_p.is_file():
            try:
                frag = json.loads(out_p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                frag = {}
        if ep == "cpu" and frag.get("outputs"):
            ref_outputs = frag["outputs"]
        results[ep] = {
            "returncode": r.returncode,
            "providers": (frag.get("session") or {}).get("requested_providers"),
            "provider_node_counts": (frag.get("session") or {}).get("provider_node_counts"),
            "parity": frag.get("parity"),
            "stderr_tail": r.stderr[-600:],
            "stdout_tail": r.stdout[-600:],
        }

    amdgpu = results.get("amdgpu", {})
    amdgpu_nodes = ((amdgpu.get("provider_node_counts") or {}).get("AMDGPU", 0))
    dml = results.get("dml", {})
    dml_nodes = ((dml.get("provider_node_counts") or {}).get("DmlExecutionProvider", 0))
    status = "PASS_AMDGPU+DML" if (amdgpu_nodes > 0 and dml_nodes > 0) else (
        "PASS_AMDGPU_ONLY" if amdgpu_nodes > 0 else (
            "PASS_DML_ONLY" if dml_nodes > 0 else "FAIL_NO_GPU_COMPUTE"))
    return {"model_sha256": model_sha, "results": results, "status": status,
            "amdgpu_nodes": amdgpu_nodes, "dml_nodes": dml_nodes}


def _jsonable(o):
    import numpy as np
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, Path):
        return redaction.redact_path(o)
    return o


if __name__ == "__main__":
    sys.exit(main())
