"""RCA register updates from the 2026-11-18 R1-B/C/D live runs.

Run: .venv-winml/Scripts/python -I tools/r1_update_rca.py <rca_dir>
Appends revisions (history preserved)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from radeon_ort.rca import RcaIssue, RcaRegister, utc_now_iso  # noqa: E402

HEAD = "eb3ba42"
ENV = {
    "os_build": "Windows 11 26200", "driver": "32.0.22032.6002",
    "ep_dll_sha256": "cb7496b3ab8f9daf2948b6030db5ec795596072ffcb25d3c55a28e4c0df7f8ab",
    "ort": "onnxruntime-windowsml 1.25.2.202605110140", "python": "3.13.13 (.venv-winml)",
}


def main(rca_dir: str) -> int:
    reg = RcaRegister(rca_dir)
    now = utc_now_iso()
    updates = [
        RcaIssue(
            issue_id="AMDGPU-RCA-002", severity="P2", status="MITIGATED",
            stage="compile",
            title="MIGraphX auto_save_problem_cache write fails (WindowsApps protected path)",
            head=HEAD, first_seen_utc="2026-11-17T21:49:38Z", last_seen_utc=now,
            environment=ENV,
            observation=(
                "Quantified: yolo26n (640 static fp32) cold session-create takes "
                "~186 s wall in a fresh process (probe compile ~63 s + rebuild "
                "~123 s; model compiled twice by design), while the tiny canary "
                "MatMul graph takes 1.3 s. Consistent with per-process cold "
                "MIGraphX compilation whose problem_cache cannot persist "
                "(WindowsApps ACL). In-process second create is fast."),
            experiments=[
                {"hypothesis": "cache write failure -> cold compile each process",
                 "command": "_ep_worker.py --case yolo26n amdgpu (fresh process)",
                 "controlled_variable": "process freshness",
                 "predicted_if_true": "session-create >> in-process rebuild",
                 "actual_result": "186 s cold vs 1.3 s canary; rebuild 123 s in same process",
                 "reproducible": True, "repetitions": 2, "falsified": False},
            ],
            causal_inference=(
                "External root cause: MSIX 1.8.65.0 attempts problem_cache.json "
                "inside its own read-only WindowsApps directory; every process "
                "pays a full cold compile for YOLO-scale graphs. Not a "
                "correctness issue (parity allclose, task parity pass); a "
                "per-process latency cost only."),
            mitigation=(
                "Harness-level: benchmark separates session-create from warm "
                "inference (R1-F); no system/ACL change (forbidden). Possible "
                "future: user-level MIGRAPHX cache env var if the EP honors one "
                "(untested — child-env experiment logged as next step)."),
            remaining_uncertainty="Whether any supported env var redirects this EP's cache.",
            impact="latency only; no execution/parity failure across 32+ runs",
            gpu_work_happened=True,
        ),
        RcaIssue(
            issue_id="AMDGPU-RCA-003", severity="P1", status="ISOLATED",
            stage="session-create",
            title="AMDGPU ep-device candidate[0] fails with HIP 101 invalid device ordinal (GPU=0)",
            head=HEAD, first_seen_utc="2026-11-17T21:49:37Z", last_seen_utc=now,
            environment=ENV,
            observation=(
                "Reproduced deterministically across independent processes on "
                "2026-11-18 (yolo26n real-model run): candidate[0] = global "
                "ep-device index 3 -> HIP failure 101 at hipSetDevice(0); "
                "candidate[1] = global index 4 -> session OK, profiler AMDGPU "
                "nodes=1 (fused), probe + rebuilt-final both positive."),
            experiments=[
                {"hypothesis": "candidate index maps to HIP ordinal and ordinal 0 is invalid in this driver+EP",
                 "command": "create_amdgpu_session(device_candidates=[dev0]) then [dev1]",
                 "controlled_variable": "ep-device candidate only",
                 "predicted_if_true": "dev0 -> HIP 101 GPU=0; dev1 -> OK",
                 "actual_result": "exactly that, 3/3 independent processes",
                 "reproducible": True, "repetitions": 3, "falsified": False},
            ],
            causal_inference=(
                "Isolated to the vendor EP's device-ordinal assignment: the EP "
                "enumerates two AMDGPU devices but binds ordinal 0 to a "
                "nonexistent HIP device. Root cause internal to closed DLL "
                "(amdgpu-ep.dll allocator.cc hipSetDevice); external boundary — "
                "not fixable in-repo. Harness fail-closed candidate loop is the "
                "correct mitigation and is now attested per-receipt."),
            mitigation=(
                "create_amdgpu_session tries candidates in order, requires "
                "probe profile AMDGPU>0, revalidates the rebuilt final session, "
                "and records the full candidate_log in every receipt."),
            remaining_uncertainty=(
                "Why the EP enumerates a broken ordinal-0 device at all (driver "
                "enumeration offset vs MSIX bug) — cannot be discriminated "
                "without vendor-side introspection."),
            impact="candidate[0] unusable; all executions correctly routed to candidate[1]",
            gpu_work_happened=True,
        ),
        RcaIssue(
            issue_id="AMDGPU-RCA-004", severity="P1", status="MITIGATED",
            stage="identity",
            title="Dual AMDGPU ep-device enumeration indistinguishable; candidate->LUID mapping unproven",
            head=HEAD, first_seen_utc="2026-11-17T21:49:36Z", last_seen_utc=now,
            environment=ENV,
            observation=(
                "Confirmed again 2026-11-18: both AMDGPU ep-devices expose "
                "identical metadata (vendor AMD, version 0.1.0, same library), "
                "no LUID/ordinal field. R1-C resolution: receipts now record "
                "device_binding with EP_DEVICE_SELECTED + per-candidate log + "
                "probe/final profile evidence + DXGI corroboration stored "
                "SEPARATELY as dxgi_corroboration (explicitly not a "
                "candidate->LUID proof). verdicts.identity distinguishes "
                "EP_DEVICE_SELECTED from PHYSICAL_GPU_IDENTITY_CORROBORATED; "
                "the latter is not claimed because the mapping is unproven."),
            experiments=[
                {"hypothesis": "ep-device metadata cannot distinguish the two candidates",
                 "command": "ort.get_ep_devices() metadata dump x3 processes",
                 "controlled_variable": "none (observation)",
                 "predicted_if_true": "identical metadata dicts",
                 "actual_result": "identical; only outcome (HIP 101 vs OK) distinguishes",
                 "reproducible": True, "repetitions": 3, "falsified": False},
            ],
            causal_inference=(
                "EP API limitation on build 1.8.65.0. Honest labeling adopted: "
                "EP_DEVICE_SELECTED (probe-evidenced) + DXGI corroboration of "
                "the physical GPU; candidate->LUID mapping stays "
                "IDENTITY_BINDING_UNPROVEN. This is a labeling fix, not a "
                "hardware-identity doubt: single physical 8060S is corroborated "
                "by PnP/DXGI (VID 1002 DID 1586, unique non-software AMD "
                "adapter pair with identical description)."),
            mitigation=(
                "schema v2 device_binding contract + validator enforcement "
                "(PASS requires EP_DEVICE_SELECTED or stronger + probe>0 + "
                "final profile>0)."),
            remaining_uncertainty=(
                "If a future EP build exposes LUIDs, upgrade to "
                "PHYSICAL_GPU_IDENTITY_CORROBORATED with direct evidence."),
            impact=(
                "Legacy G2/G3 receipts' device_luid field is DXGI "
                "corroboration only (documented errata); new receipts carry "
                "attestation."),
            gpu_work_happened=True,
        ),
    ]
    for issue in updates:
        reg.append(issue)
        reg.write_issue_doc(issue)
    reg.render_markdown(HEAD)
    print("updated", len(updates), "issues")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else
                  "reports/gfx1151/R1_20261117T215114Z/rca"))
