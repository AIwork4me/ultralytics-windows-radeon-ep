"""Seed the R1 AMDGPU RCA register with anomalies observed during R1-0 preflight.

Run: .venv-winml/Scripts/python -I tools/r1_seed_rca.py <rca_dir>
Idempotent per issue_id: re-running appends a revision (history preserved).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from radeon_ort.rca import RcaIssue, RcaRegister, utc_now_iso  # noqa: E402

HEAD = "2724c4cd"  # R1 branch head at seeding time (2026-11-17T21:51Z)
ENV = {
    "os_build": "Windows 11 26200",
    "driver": "32.0.22032.6002",
    "ep_dll_msix": "MicrosoftCorporationII.WinML.AMD.GPU.EP.1.8_1.8.65.0_x64__8wekyb3d8bbwe",
    "ep_dll_sha256": "cb7496b3ab8f9daf2948b6030db5ec795596072ffcb25d3c55a28e4c0df7f8ab",
    "ort": "onnxruntime-windowsml 1.25.2.202605110140",
    "python": "3.13.13 (.venv-winml)",
}


def main(rca_dir: str) -> int:
    reg = RcaRegister(rca_dir)
    now = utc_now_iso()
    issues = [
        RcaIssue(
            issue_id="AMDGPU-RCA-001", severity="P2", status="REPRODUCED",
            stage="registration",
            title="ORT env creation logs 'Init provider bridge failed.' in winml venv",
            head=HEAD, first_seen_utc="2026-11-17T21:48:04Z", last_seen_utc=now,
            environment=ENV,
            exception=None,
            stderr_excerpt="[W:onnxruntime:Default, onnxruntime_pybind_module.cc:45 "
                           "onnxruntime::python::CreateOrtEnv] Init provider bridge failed.",
            observation=(
                "The warning is emitted once per process at onnxruntime import/env "
                "creation in .venv-winml, BEFORE any amdgpu-ep.dll registration and "
                "independent of it. Also seen in G1-era runs. GPU sessions still "
                "create and execute (canary cand[1] OK; G2/G3 32/32 receipts)."),
            hypotheses=[
                "ORT python bridge tries to preload a provider bridge DLL that is "
                "absent in the windowsml distribution (cosmetic, no functional effect)",
                "Bridge init failure disables some provider surface we have not used "
                "yet (e.g. direct AMDGPUExecutionProvider class registration)",
            ],
            impact="Unknown-but-bounded: all observed GPU executions succeed despite it; "
                   "treated as warning-to-audit, not harmless-by-assumption.",
            gpu_work_happened=True,
            next_discriminating_experiment=(
                "Fresh process: import onnxruntime with ORT log level verbose and "
                "capture which bridge init is attempted; compare providers surface "
                "with/without the warning. Bounded: 3 runs."),
            confounders_considered=["venv-only artifact", "WindowsApps ACL"],
        ),
        RcaIssue(
            issue_id="AMDGPU-RCA-002", severity="P2", status="REPRODUCED",
            stage="compile",
            title="MIGraphX auto_save_problem_cache write fails (WindowsApps protected path)",
            head=HEAD, first_seen_utc="2026-11-17T21:49:38Z", last_seen_utc=now,
            environment=ENV,
            exception="auto_save_problem_cache: save failed: write_buffer: Failure opening "
                      "file: C:\\Program Files\\WindowsApps\\...\\ExecutionProvider\\problem_cache.json",
            stderr_excerpt="2026-11-18 05:49:38 [WARN] [:0] auto_save_problem_cache: save "
                           "failed: C:\\j\\AMDMIGraphX\\src\\file_buffer.cpp:77",
            observation=(
                "Emitted by the AMDGPU EP when a graph compiles in our process: the "
                "bundled MIGraphX attempts to persist its problem cache next to the "
                "MSIX DLL under WindowsApps, which is ACL-protected (read/execute for "
                "user). Emitted 3x during one canary session-create+run. Compilation "
                "and execution still succeed."),
            hypotheses=[
                "External root cause: MSIX package does not relocate its cache dir; "
                "expected user-writable path is not configured by this EP build "
                "(ep_options empty, no documented option on 1.8.65.0)",
                "Consequence is cold-compile cost per process (no persistent cache), "
                "not correctness",
            ],
            impact="Possible slower session-create across processes; no execution or "
                   "parity failure observed. Never modify WindowsApps/ACLs (forbidden).",
            gpu_work_happened=True,
            next_discriminating_experiment=(
                "Measure session-create wall time in N=3 fresh processes vs repeated "
                "in-process creates; if flat, cache miss cost is bounded to first "
                "compile per process. Check whether MIGRAPHX_* env vars are honored "
                "(child-process env only, no system change)."),
            confounders_considered=["antivirus lock", "concurrent GPU processes"],
        ),
        RcaIssue(
            issue_id="AMDGPU-RCA-003", severity="P1", status="REPRODUCED",
            stage="session-create",
            title="AMDGPU ep-device candidate[0] fails with HIP 101 invalid device ordinal (GPU=0)",
            head=HEAD, first_seen_utc="2026-11-17T21:49:37Z", last_seen_utc=now,
            environment=ENV,
            exception="[ONNXRuntimeError] : 1 : FAIL : HIP failure 101: invalid device "
                      "ordinal; GPU=0; file=C:\\j\\amdgpu-ep\\src\\shared\\hip\\allocator.cc; "
                      "line=14; expr=hipSetDevice(device_id)",
            observation=(
                "With 2 AMDGPU OrtEpDevices enumerated (global indices 3 and 4 of 5), "
                "session creation on candidate[0] deterministically raises HIP 101 at "
                "hipSetDevice(device_id=0) inside the vendor EP; candidate[1] succeeds "
                "and profiles AMDGPU compute. Reproduced live 2026-11-17T21:49Z "
                "(canary) and consistent with G1-era behavior (dev[0] HIP 101, dev[1] OK)."),
            hypotheses=[
                "The two OrtEpDevices both target the single physical iGPU but the EP "
                "assigns HIP ordinals 0/1; ordinal 0 is invalid in this driver+EP "
                "combination (only ordinal 1 backed), i.e. EP enumeration bug",
                "Ordinal space is offset by the other 3 non-AMDGPU ep-devices; "
                "candidate index != HIP device id and cand[0] maps to a nonexistent id",
                "GameViewer virtual display reorders/offsets device enumeration",
            ],
            experiments=[],
            impact="Candidate[0] unusable for every model; harness works around via "
                   "fail-closed candidate loop (never a silent skip). GPU work happens "
                   "on candidate[1]. Root cause is inside the closed vendor DLL — "
                   "external boundary.",
            gpu_work_happened=True,
            next_discriminating_experiment=(
                "Independent short-lived processes x3: enumerate ep devices, attempt "
                "cand[0] then cand[1], record HIP error text GPU=n per candidate. If "
                "GPU=0 always for cand[0] and GPU=1 for cand[1] => enumeration-offset "
                "hypothesis strengthened; then try reversing device order to check "
                "ordinal assignment stability."),
            confounders_considered=["GameViewer virtual display", "driver 32.0.22032.6002",
                                    "MSIX 1.8.65.0 dual-install"],
        ),
        RcaIssue(
            issue_id="AMDGPU-RCA-004", severity="P1", status="NEW",
            stage="identity",
            title="Dual AMDGPU ep-device enumeration indistinguishable; legacy receipts stored DXGI-0 LUID not the bound ep-device",
            head=HEAD, first_seen_utc="2026-11-17T21:49:36Z", last_seen_utc=now,
            environment=ENV,
            observation=(
                "Both AMDGPU OrtEpDevices expose IDENTICAL ep_vendor/ep_options/"
                "ep_metadata (library_path, version 0.1.0) — no LUID, ordinal, or "
                "adapter hint. Old G2/G3 receipts record session.device_luid from the "
                "DXGI adapter chosen BEFORE the probe (index 0 -> 0x0:0x11746) while "
                "the session actually bound ep-device candidate[1]; therefore the "
                "legacy device_luid field is DXGI corroboration, NOT binding evidence. "
                "Identical name/VID/DID does not prove the two DXGI LUIDs (two "
                "enumerations of the same iGPU) map 1:1 to ep-device candidates."),
            hypotheses=[
                "The physical 8060S is one GPU enumerated twice; ep candidates are two "
                "HAL views (one broken per RCA-003) — binding to cand[1] still lands "
                "on the only physical GPU, but this must be PROVEN, not assumed",
                "Candidates could map to the two DXGI enumerations in unknown order",
            ],
            impact="Identity claims in legacy receipts must be downgraded to "
                   "EP_DEVICE_SELECTED(probe-evidenced) + DXGI corroboration only; "
                   "PHYSICAL_GPU_IDENTITY_CORROBORATED requires new R1-C evidence.",
            gpu_work_happened=True,
            next_discriminating_experiment=(
                "R1-C: per-candidate probe in isolated processes capturing profile "
                "counts + memory telemetry; correlate with DXGI/PnP evidence; if "
                "candidates remain indistinguishable, mark IDENTITY_BINDING_UNPROVEN "
                "for candidate->LUID mapping and record selection attestation instead."),
            confounders_considered=["boot-local LUID instability", "GameViewer adapter"],
        ),
    ]
    for issue in issues:
        reg.append(issue)
        reg.write_issue_doc(issue)
    md = reg.render_markdown(HEAD)
    print(f"seeded {len(issues)} issues -> {reg.jsonl}")
    print(f"register: {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else
                  "reports/gfx1151/R1_20261117T215114Z/rca"))
