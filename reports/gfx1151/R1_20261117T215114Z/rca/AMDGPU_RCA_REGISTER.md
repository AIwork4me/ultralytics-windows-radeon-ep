# AMDGPU RCA Register — mission GFX1151-R1

Head: `eb3ba42` — rendered 2026-11-17T22:58:52Z from `AMDGPU_ISSUES.jsonl` (append-only).

| ID | Sev | Status | Stage | Title | Impact | GPU work? |
|---|---|---|---|---|---|---|
| AMDGPU-RCA-001 | P2 | REPRODUCED | registration | [ORT env creation logs 'Init provider bridge failed.' in winml venv](AMDGPU-RCA-001.md) | Unknown-but-bounded: all observed GPU executions succeed despite it; treated as  | True |
| AMDGPU-RCA-002 | P2 | MITIGATED | compile | [MIGraphX auto_save_problem_cache write fails (WindowsApps protected path)](AMDGPU-RCA-002.md) | latency only; no execution/parity failure across 32+ runs | True |
| AMDGPU-RCA-003 | P1 | ISOLATED | session-create | [AMDGPU ep-device candidate[0] fails with HIP 101 invalid device ordinal (GPU=0)](AMDGPU-RCA-003.md) | candidate[0] unusable; all executions correctly routed to candidate[1] | True |
| AMDGPU-RCA-004 | P1 | MITIGATED | identity | [Dual AMDGPU ep-device enumeration indistinguishable; candidate->LUID mapping unproven](AMDGPU-RCA-004.md) | Legacy G2/G3 receipts' device_luid field is DXGI corroboration only (documented  | True |

4 issue(s). Unresolved (not CLOSED_VERIFIED): 4.
