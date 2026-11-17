# AMDGPU RCA Register — mission GFX1151-R1

Head: `2724c4cd` — rendered 2026-11-17T21:52:58Z from `AMDGPU_ISSUES.jsonl` (append-only).

| ID | Sev | Status | Stage | Title | Impact | GPU work? |
|---|---|---|---|---|---|---|
| AMDGPU-RCA-001 | P2 | REPRODUCED | registration | [ORT env creation logs 'Init provider bridge failed.' in winml venv](AMDGPU-RCA-001.md) | Unknown-but-bounded: all observed GPU executions succeed despite it; treated as  | True |
| AMDGPU-RCA-002 | P2 | REPRODUCED | compile | [MIGraphX auto_save_problem_cache write fails (WindowsApps protected path)](AMDGPU-RCA-002.md) | Possible slower session-create across processes; no execution or parity failure  | True |
| AMDGPU-RCA-003 | P1 | REPRODUCED | session-create | [AMDGPU ep-device candidate[0] fails with HIP 101 invalid device ordinal (GPU=0)](AMDGPU-RCA-003.md) | Candidate[0] unusable for every model; harness works around via fail-closed cand | True |
| AMDGPU-RCA-004 | P1 | NEW | identity | [Dual AMDGPU ep-device enumeration indistinguishable; legacy receipts stored DXGI-0 LUID not the bound ep-device](AMDGPU-RCA-004.md) | Identity claims in legacy receipts must be downgraded to EP_DEVICE_SELECTED(prob | True |

4 issue(s). Unresolved (not CLOSED_VERIFIED): 4.
