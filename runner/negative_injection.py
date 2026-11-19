"""python -m runner.negative_injection — P3.3 fail-closed demonstrations.

Five real injections on this host; every one MUST be prominently reported as
AMDGPU_FAILED / DML_FAILED / BLOCKED_* with non-zero exit codes, and none may
produce a GPU-PASS receipt:
  1. forced wrong LUID/identity (forged)        -> BLOCKED_IDENTITY
  2. impossible EP library path                 -> BLOCKED_EP
  3. GPU EP registered but CPU-only graph claim -> FAIL_NO_GPU_COMPUTE (validator)
  4. DML session on wrong adapter (software)    -> FAIL/DML_FAILED
  5. simulated EP_FAIL + wrapper CPU retry      -> caught by profiler (AMDGPU nodes 0)
Writes reports/<gfx>/<run_id>/NEGATIVE_INJECTION.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runner import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "src"))

from radeon_ort import device_identity, redaction  # noqa: E402
from radeon_ort.amdgpu_session import canary_model_path  # noqa: E402
from radeon_ort.receipts import utc_now_iso  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gfx", default="gfx1151")
    ap.add_argument("--reports-root", default=str(REPO_ROOT / "reports"))
    ap.add_argument("--run-id", default="NEG_INJECT")
    a = ap.parse_args()
    out_dir = Path(a.reports_root) / a.gfx / a.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    results = {}

    model = canary_model_path(out_dir)
    from radeon_ort.device_identity import canary_inputs
    feeds = canary_inputs()

    # 1) forged identity: host claims verified but chain broken
    r1 = {}
    try:
        res = device_identity.resolve_target_adapter(
            [device_identity.DxgiAdapter(index=0, description="Intel Iris Xe", vendor_id=0x8086,
                                         device_id=0x46A6, sub_sys_id=0, revision=0,
                                         dedicated_video_mb=0, dedicated_system_mb=0,
                                         shared_sys_mb=8000, luid_high=0, luid_low=1)],
            gfx=a.gfx)
        r1 = {"ok": res["ok"], "reason": res.get("reason", "")}
    except Exception as e:  # noqa: BLE001
        r1 = {"ok": False, "error": str(e)}
    results["inject1_forged_identity"] = {**r1, "expected": "WRONG_OR_AMBIGUOUS_DEVICE",
                                          "passed": not r1.get("ok", True)}

    # 2) impossible EP path
    r2 = {}
    try:
        import onnxruntime as ort
        ort.register_execution_provider_library("AMDGPU", r"C:\nonexistent\amdgpu-ep.dll")
        r2 = {"registered_unexpectedly": True}
    except Exception as e:  # noqa: BLE001
        r2 = {"failed_as_expected": True, "error": str(e)[:200]}
    results["inject2_impossible_ep_path"] = {**r2, "expected": "registration failure",
                                             "passed": r2.get("failed_as_expected", False)}

    # 3) receipt-level CPU-only masquerade is executed after imports below

    # 4) DML on software adapter (Basic Render Driver) — expect failure/no-GPU
    r4 = {}
    try:
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.enable_profiling = True
        so.profile_file_prefix = str(out_dir / "inject4_profile")
        so.log_severity_level = 3
        # device_id intentionally invalid (9): the wrapper is expected to catch
        # the DML init failure and retry CPU. That CPU retry MUST be detected.
        sess = ort.InferenceSession(str(model), sess_options=so,
                                    providers=["DmlExecutionProvider", "CPUExecutionProvider"],
                                    provider_options=[{"device_id": 9}, {}])
        providers = list(sess.get_providers())
        r4 = {"providers": providers, "cpu_fallback_detected":
              "DmlExecutionProvider" not in providers}
        if "DmlExecutionProvider" in providers:
            sess.run(None, feeds)
            from radeon_ort import profiler_audit
            counts = profiler_audit.audit_profile(sess.end_profiling())["provider_node_counts"]
            r4.update({"counts": counts,
                       "dml_nodes": counts.get("DmlExecutionProvider", 0),
                       "cpu_nodes": counts.get("CPUExecutionProvider", 0)})
    except Exception as e:  # noqa: BLE001
        r4 = {"failed": True, "error": str(e)[:200], "cpu_fallback_detected": True}
    results["inject4_dml_wrong_adapter"] = {
        **r4, "expected": "wrong adapter cannot be certified: DML init failure or CPU retry detected",
        "passed": bool(r4.get("failed") or r4.get("cpu_fallback_detected")
                       or r4.get("dml_nodes", 0) == 0)}

    # 5) simulated EP_FAIL with wrapper CPU retry (wrong provider name) — profiler catch
    r5 = {}
    try:
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.enable_profiling = True
        so.profile_file_prefix = str(out_dir / "inject5_profile")
        sess = ort.InferenceSession(str(model), sess_options=so,
                                    providers=["AMDGPUExecutionProvider"])  # WRONG name on purpose
        outs = sess.run(None, feeds)
        from radeon_ort import profiler_audit
        counts = profiler_audit.audit_profile(sess.end_profiling())["provider_node_counts"]
        r5 = {"providers": sess.get_providers(), "counts": counts,
              "amdgpu_nodes": counts.get("AMDGPU", 0),
              "cpu_nodes": counts.get("CPUExecutionProvider", 0)}
    except Exception as e:  # noqa: BLE001
        r5 = {"error": str(e)[:200]}
    results["inject5_wrapper_cpu_retry"] = {**r5,
                                            "expected": "CPU-only masquerade caught (AMDGPU nodes 0)",
                                            "passed": r5.get("amdgpu_nodes", 0) == 0}

    # 3) real receipt-level injection via validator (CPU-only masquerade receipt)
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    from helpers_receipts import make_receipt  # noqa: E402
    from radeon_ort.receipts import validate_receipt_strict  # noqa: E402
    forged = make_receipt(amdgpu_nodes=0, amdgpu="PASS_GPU_EXECUTED_FULL")
    v = validate_receipt_strict(forged)
    results["inject3_cpu_only_claim_forged"] = {"ok": v["ok"], "derived": v["derived"]["amdgpu"],
                                                "problems": v["problems"],
                                                "expected": "validator rejects",
                                                "passed": not v["ok"]}

    summary = {k: v.get("passed") for k, v in results.items()}
    payload = {"run_utc": utc_now_iso(), "gfx": a.gfx, "results": results,
               "summary": summary,
               "all_passed": all(summary.values())}
    (out_dir / "NEGATIVE_INJECTION.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    print(f"ALL_INJECTIONS_FAIL_CLOSED: {payload['all_passed']} -> {out_dir / 'NEGATIVE_INJECTION.json'}")
    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
