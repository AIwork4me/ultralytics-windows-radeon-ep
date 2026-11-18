# R1 Initial Audit — observed implementation vs mission contract

Exact head: `mission/gfx1151-r1-amdgpu-first-task-parity` branched from PR #2 head
`563c24e5e27a6abd61328720efd97ee618f10021` (base `ee47a4496eb7f23fc29aaa8e9a22e08ab7f700bd`).
Audit date: 2026-11-18. All statements below were read from source at this head;
none are inferred from prior chat.

## Environment facts (live-captured)

- OS: Windows 11 26200 (10.0.26200), 128 GB RAM.
- GPU: `AMD Radeon(TM) 8060S Graphics`, PNP `PCI\VEN_1002&DEV_1586&SUBSYS_17141043&REV_C1`,
  driver 32.0.22032.6002. Second adapter present: "GameViewer Virtual Display Adapter"
  (ROOT\DISPLAY\0000) — observed only, never modified.
- AMDGPU EP vendor MSIX: `MicrosoftCorporationII.WinML.AMD.GPU.EP.1.8_1.8.65.0_x64__8wekyb3d8bbwe`,
  `amdgpu-ep.dll` SHA256 `cb7496b3ab8f9daf2948b6030db5ec795596072ffcb25d3c55a28e4c0df7f8ab` (548304 B).
- Venvs: `.venv-winml` (Python 3.13.13, onnxruntime-windowsml 1.25.2.202605110140, onnx 1.23.2,
  numpy 2.5.5, **no cv2, no torch**), `.venv-export` (torch 2.15.0, ultralytics **8.4.215** (pinned ✓),
  onnxruntime 1.32.0, opencv 5.0.0.93), `.venv-cpu` (numpy only). A fourth `.venv-winml130` exists.
- Repo clean (`git status --porcelain=v1` empty) before branching; 348 tracked files under `reports/`.

## Observed implementation (files read in full)

`AGENTS.md` (9 rules, fail-closed), `README.md`, `runner/{run_matrix,_ep_worker,verify_receipts}.py`,
`src/radeon_ort/{receipts,cpu_reference,task_oracles,task_adapters,amdgpu_session,device_identity,
profiler_audit,benchmark,fallback_policy,windows_ml_ep_catalog,model_export}.py`,
`schemas/receipt.json` (v1), `configs/thresholds.yaml`, `tests/**` (incl. 25 adversarial cases),
`catalog/model_catalog.json` (142 entries), prior runs `G1/G2/G3/SOAK_RUN1`, `data/fixtures/bus.jpg`,
16 checkpoints already exported to `models/onnx/*.onnx` with `.pt` present.

## Contract gaps found (severity, evidence)

1. **P0 — vacuous parity**: `receipts.derive_gpu_status` skips all parity checks when
   `parity.outputs == []` (`if outputs:`) → a GPU FULL PASS is derivable with zero compared
   outputs. Also `cpu_reference.compare_outputs` never flags *extra* candidate outputs, and an
   empty reference dict yields `all_outputs_allclose=True`.
2. **P0 — forged task parity**: `run_matrix.run_one_case` sets `task_oracle="PASS_TASK"` whenever
   the PT oracle subprocess returns 0 (`oracle_kind:"PT_APP"`, `passed:True` with only a box
   count). No GPU postprocess ↔ PT comparison exists anywhere. This is the old `PT_APP` marker
   masquerading as task parity (G2 receipts carry exactly this, e.g.
   `receipt_yolo26n__detect__amdgpu.json`).
3. **P0 — nondeterministic inputs**: feeds are `rng.random(...)` seeded by
   `20260101 + (hash(case_id) % 100000)` — `hash()` is `PYTHONHASHSEED`-dependent → different
   bytes per process run. No real-image input path exists in `run_matrix`; `--image` is only
   passed to the PT oracle, never to GPU inference.
4. **P0 — identity binding contradiction**: `_ep_worker` records `session.device_luid` from the
   DXGI adapter selected *before* the probe (DXGI index 0 → `0x0:0x11746`), while
   `create_amdgpu_session` actually binds ep-device candidate **1** (dev[0] fails HIP 101 on this
   host). The receipt therefore stores a LUID that was *not* the ep-device binding evidence. No
   selected-device attestation is returned; the final rebuilt session is never re-profiled
   (revalidation gap), and backend route (MIGraphX/HIP vs other) is unmeasured.
5. **P1 — subprocess rc ignored**: `_run_worker` returns the fragment JSON if it exists and never
   records `r.returncode`; a worker that exits 31 (AMDGPU_FAILED) after writing a partial
   fragment with parity can still be classified from fragment content. Exit-code semantics in
   `fallback_policy.EXIT_CODES` are unused by `run_matrix`.
6. **P1 — fused-node FULL semantics**: G2/G3 receipts show `provider_node_counts={"AMDGPU":1},
   graph_node_total=1` — one EP-attributed fused node. Current code upgrades this to
   `PASS_GPU_EXECUTED_FULL` with no note that per-source-node execution is unproven (fusion
   attribution only). Validator must keep FULL/PARTIAL semantics but record
   `ep_attribution="fused_single_node"` so downstream claims stay honest.
7. **P1 — dtype/shape not validated**: parity items carry `dtype` but the validator never checks
   them against the ONNX graph's declared output dtypes; no expected-output-manifest check
   (exact output-name set both directions) exists.
8. **P1 — no orthogonal classifications**: schema v1 mixes GPU_EXECUTION, NUMERIC_PARITY,
   TASK_PARITY into one `status.amdgpu` enum; `PT_APP_SMOKE` vs `TASK_PARITY` vs
   `BACKEND_ROUTING` are not separable. Mission requires additive schema v2 + v1 reader.
9. **P2 — thresholds partially wired**: `thresholds.yaml` exists and is predeclared, but
   `run_one_case` receives `thresholds={}` and workers hardcode `atol/rtol 5e-3` from the case
   JSON; task thresholds are unused by any live code path.
10. **P2 — fallback stdout detection**: `stderr_fallback_signals` is hardcoded `[]` in
    `_finish`; no actual stdout/stderr scan for ORT fallback messages happens for the final
    session (probe-path only via profile counts).

## Positive findings (keep)

- Strict validator recomputes statuses and rejects forged labels (25 adversarial tests green at
  this head per G2/G3 CI); receipts are immutable (`save_receipt` refuses overwrite).
- `create_amdgpu_session` is fail-closed per-candidate (probe must show AMDGPU nodes>0) and
  never falls back to CPU; registered provider name "AMDGPU" handled correctly.
- DXGI identity code refuses wrong/ambiguous device, allows integrated GPU, treats LUID as
  boot-local corroboration only.
- Export pipeline pins Ultralytics 8.4.215, records export args + hashes; weights git-ignored.

## Gate 0 verdict

`GO` — baseline is safe: repo clean, frozen receipts byte-identical in git (no working-tree
edits), new branch created; work can proceed without touching prior proof (immutable-receipt
invariant enforced by `save_receipt` and manifest verification).

## Plan mapped to phases

- R1-A: schema v2 + validator repair (gaps 1,5,6,7,8) + ≥20 new adversarial tests, v1 legacy
  read-mode for G2/G3.
- R1-B: deterministic `input_id` (SHA256 over canonical config, no hash()/PID/EP/timestamp),
  real-image tensor pipeline persisted as `.npy` + hashes, `--input-manifest` CLI, cross-process
  SHA assertions (gap 3).
- R1-C: `create_amdgpu_session` returns selection attestation; final session re-profiled;
  `EP_DEVICE_SELECTED` vs `PHYSICAL_GPU_IDENTITY_CORROBORATED` separated; resolve the
  dev[1]-vs-DXGI-0 LUID contradiction (gap 4); backend route probing → `BACKEND_UNVERIFIED`
  fallback label.
- R1-D: per-task ONNX postprocess adapters compared against PT oracle results on the same image
  (gap 2, 9) with predeclared thresholds; four-way differential split.
- R1-E: incident register scaffold before GPU runs.
