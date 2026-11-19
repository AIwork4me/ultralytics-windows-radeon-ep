# GFX1151 Physical Validation — Open Gaps Register

Run: `GFX1151_PHYSICAL_20261119T081900Z` (base `d24f737`)

Status labels: `OPEN` / `BLOCKED_EXTERNAL` / `MITIGATED` / `ACCEPTED_LIMITATION`.

## P0

### P0-1 AMDGPU EP absent on clean host — BLOCKED_EXTERNAL
- **Evidence**: `EP_INSTALL_AND_PROBE.json`, `GPU_CANARY.json`, 9 receipts
  `status.amdgpu=BLOCKED_EP`.
- **Observed**: no `amdgpu-ep.dll` under site-packages, user WindowsApps or
  machine WindowsApps; WinML `find_all_providers()==[]`;
  `ensure_and_register_certified_async()==[]`; ORT providers only
  `DmlExecutionProvider, CPUExecutionProvider`.
- **Why not fixed here**: installing a system-wide Windows ML EP / WindowsAppRuntime
  package or changing the GPU driver is prohibited by the mission
  (`BLOCKED_ADMIN_DEPENDENCY`).
- **Closure criterion**: authorized install of the AMD GPU EP MSIX (or host image
  with it), then rerun preflight → 9×2 Tier A with real AMDGPU profiles.

### P0-2 Full catalog not executed — OPEN
- 135 eligible; 9 executed (6.7%); 126 `NOT_RUN` (33 baseline-required,
  81 resource-gated, 12 optional-stress).
- No `GFX1151_FULL_CATALOG_GO` claim is made. `full_coverage_claim_allowed=false`.
- **Closure**: 4-checkpoint shard orchestration + resource budgets, then execute
  with resume; keep every OOM/timeout as a classified failure.

### P0-3 DML physical binding is indirect — ACCEPTED_LIMITATION
- `device_binding.state=EP_DEVICE_SELECTED`; DXGI LUID + vendor/device ID are
  corroboration, but the DML EP exposes no LUID/ordinal (AMDGPU-RCA-004).
- **Consequence**: no `PHYSICAL_GPU_IDENTITY_CORROBORATED` claim; binding
  confidence is "indirect, single AMD adapter on host".

## P1

### P1-1 Task oracle is single-image — OPEN
- detect/segment/pose/obb/classify/semantic/depth all use one fixture image
  (OBB one DOTA image). Metrics prove pipeline equivalence, not dataset accuracy.
- **Closure**: 20–50 image fixtures; report dataset-level P/R/mIoU/OKS.

### P1-2 Pose OKS scale proxy — OPEN
- `task_parity.py` uses `s = 2 * imgsz²` instead of per-object bbox area.
- Pose max_abs 4.53e-3 vs 5e-3 threshold leaves ~10% margin.

### P1-3 No E2E pre/post timing, no power/temperature — NOT_MEASURED
- Benchmarks cover session-create, first run, warm `Session.run`.
- `psutil` installed but no validated temperature/power meter; shared memory is
  never reported as dedicated VRAM.

### P1-4 No shard/resource-budget orchestrator — OPEN
- `run_matrix` runs one catalog list sequentially with per-case timeout; it does
  not yet enforce 4-checkpoint shards or OOM-aware continuation.

## P2

- P2-1 `session.routing_notes` duplicated `BLOCKED_EP:` prefix (cosmetic).
- P2-2 RAM discrepancy 95.8 GB (Win32) vs 131072 MB (dxdiag).
- P2-3 Negative injection writes local canary ONNX; excluded from published tree
  by `LOCAL_ONLY_INDEX`.
- P2-4 No GPU driver event-log capture.

## Audit findings fixed in this branch

| Finding | Before | Fix |
|---|---|---|
| `run_matrix` exit 0 on failure | always `return 0` | exit 2 for BLOCKED/NOT_RUN, 1 for FAIL |
| `run_matrix` broken vs v2 worker | used old `feeds` schema | delegates to `r1_pipeline` v2 |
| `--pt-oracle` smoke = PASS_TASK | `PT_APP` label | real `TASK_PARITY` metrics + thresholds |
| `verify_receipts` ALL_OK with 0 receipts | vacuous pass | fail on zero receipts |
| `verify_receipts` missing manifest | printed note only | fail-closed |
| `finalize_run` "EVIDENCED" ignored status | any receipt counted | per-EP disposition from statuses |
| soak overwrite | `SOAK_<ep>.json` | `SOAK_<ep>__<tag>.json` + no overwrite |
| preflight GO on DML-only | `startswith("PASS")` | both EPs required; exit 2 |
| preflight canary broken | v1 worker schema | v2 .npz multi-input worker path |
| DML CPU fallback accepted | no provider check | `DML_FAILED` if DML absent |
| host probe rc=1 / PnP leak | raised / full instance id | parse-driven + prefix redaction |
| blocked receipt schema invalid | null graph total/ORT | typed defaults |
| `min_matches` validator alias | false problem | `matched` alias |
