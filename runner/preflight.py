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
    ap.add_argument("--gfx", default=None,
                    help="gfx target; inferred from live DXGI DID when omitted")
    ap.add_argument("--gpu-needle", default=None,
                    help="optional WMI name needle; DID matching is the default")
    ap.add_argument("--reports-root", default=str(REPO_ROOT / "reports"))
    ap.add_argument("--run-id", default=None)
    a = ap.parse_args()

    # Resolve the live target before creating any evidence directory. Never
    # assume gfx1151 on a gfx1100/gfx1201 host.
    gfx = a.gfx
    try:
        adapters = device_identity.enum_dxgi_adapters()
    except OSError as e:
        print(f"[preflight] FAIL: DXGI enumeration failed: {e}")
        return 1
    if gfx is None:
        inf = device_identity.infer_gfx_from_adapters(adapters)
        if not inf.get("ok"):
            print(f"[preflight] FAIL: {inf.get('reason')}")
            return 1
        gfx = inf["gfx"]
    a.gfx = gfx

    run_id = a.run_id or ("PREFLIGHT_" + utc_now_iso().replace("-", "").replace(":", "").replace("T", "_").replace("Z", ""))
    out_dir = Path(a.reports_root) / gfx / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[preflight] run_id={run_id} gfx={gfx} out={out_dir}")

    # 1) host fingerprint (DID-pinned; the name needle is only optional)
    host = host_probe.collect_host(
        gpu_name_needle=a.gpu_needle, gfx=gfx,
        gpu_device_ids=device_identity.target_device_ids(gfx))
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
               "available_providers": providers,
               "ep_devices": ep.data.get("ep_devices"),
               "windowsml": ep.data.get("windowsml"),
               "catalog_provider_count": ep.data.get("catalog_provider_count")}
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
    # Fail-closed verdict: a DML-only canary is NOT a dual-EP GO. The mission's
    # P0 gate requires AMDGPU and DML independently attested.
    cstatus = canary.get("status", "")
    if cstatus == "PASS_AMDGPU+DML":
        verdict, rc = "GO", 0
    elif cstatus == "PASS_DML_ONLY":
        verdict, rc = "CONDITIONAL_BLOCKED_AMDGPU (DML canary PASS)", 2
    elif cstatus == "PASS_AMDGPU_ONLY":
        verdict, rc = "CONDITIONAL_BLOCKED_DML (AMDGPU canary PASS)", 2
    else:
        verdict, rc = "BLOCKED/FAILED (see artifacts)", 1
    print(f"[preflight] verdict: {verdict}")
    return rc


def _adapter_dict(a) -> dict:
    return {
        "index": a.index, "description": a.description,
        "vendor_id": f"0x{a.vendor_id:04X}", "device_id": f"0x{a.device_id:04X}",
        "dedicated_video_mb": a.dedicated_video_mb, "shared_sys_mb": a.shared_sys_mb,
        "luid_str": a.luid_str, "is_software": a.is_software,
    }


def _run_canary(out_dir: Path, gfx: str) -> dict:
    """Canary ONNX (MatMul+Add) run on CPU, AMDGPU, DML in subprocesses; parity + nodes.

    v2: inputs/outputs are persisted .npz artifacts so the shared worker verifies
    the exact tensor bytes and the CPU reference by output name. A canary PASS
    requires positive target-EP nodes, zero CPU compute nodes and numeric parity.
    """
    import subprocess
    import sys

    import numpy as np

    from radeon_ort.amdgpu_session import canary_model_path

    tmp = out_dir / "canary"
    tmp.mkdir(exist_ok=True)
    model = canary_model_path(tmp)
    inputs = device_identity.canary_inputs()
    feeds_npz = tmp / "canary_feeds.npz"
    np.savez(feeds_npz, **inputs)
    model_sha = sha256_file(model)

    results = {}
    ref_npz = tmp / "canary_ref.npz"
    for ep in ("cpu", "amdgpu", "dml"):
        case = {"ep": ep, "gfx": gfx, "onnx_path": str(model),
                "feeds_npz": str(feeds_npz),
                "atol": 2e-4, "rtol": 2e-4, "benchmark": False,
                "profile_prefix": str(tmp / f"canary_{ep}")}
        if ep == "cpu":
            case["outputs_npz"] = str(ref_npz)
        else:
            case["reference_npz"] = str(ref_npz)
        case_p = tmp / f"case_{ep}.json"
        case_p.write_text(json.dumps(case), encoding="utf-8")
        out_p = tmp / f"frag_{ep}.json"
        if out_p.exists():
            out_p.unlink()
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
        results[ep] = {
            "returncode": r.returncode,
            "providers": (frag.get("session") or {}).get("requested_providers"),
            "provider_node_counts": (frag.get("session") or {}).get("provider_node_counts"),
            "parity": frag.get("parity"),
            "input_tensor_sha256": frag.get("input_tensor_sha256"),
            "device_binding": frag.get("device_binding"),
            "stderr_tail": r.stderr[-1200:],
            "stdout_tail": r.stdout[-600:],
        }

    def _ep_ok(ep: str, key: str) -> dict:
        item = results.get(ep, {})
        counts = item.get("provider_node_counts") or {}
        nodes = int(counts.get(key, 0) or 0)
        cpu = int(counts.get("CPU", counts.get("CPUExecutionProvider", 0)) or 0)
        parity = item.get("parity") or {}
        parity_ok = bool(parity.get("all_outputs_allclose"))
        ok = (item.get("returncode") == 0 and nodes > 0 and cpu == 0 and parity_ok)
        return {"ok": ok, "nodes": nodes, "cpu_nodes": cpu,
                "parity_ok": parity_ok, "returncode": item.get("returncode")}

    amd = _ep_ok("amdgpu", "AMDGPU")
    dml = _ep_ok("dml", "DmlExecutionProvider")
    if amd["ok"] and dml["ok"]:
        status = "PASS_AMDGPU+DML"
    elif amd["ok"]:
        status = "PASS_AMDGPU_ONLY"
    elif dml["ok"]:
        status = "PASS_DML_ONLY"
    else:
        status = "FAIL_NO_GPU_COMPUTE"
    return {"model_sha256": model_sha,
            "inputs_sha256": results.get("cpu", {}).get("input_tensor_sha256"),
            "results": results, "status": status,
            "amdgpu_nodes": amd["nodes"], "dml_nodes": dml["nodes"],
            "amdgpu_ok": amd["ok"], "dml_ok": dml["ok"]}


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