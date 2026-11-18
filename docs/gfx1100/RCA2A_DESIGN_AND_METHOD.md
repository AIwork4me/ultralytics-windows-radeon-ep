# RCA2A Design & Method — MIGraphX Auto-Tuning Attribution & Cache Effectiveness (gfx1100)

Mission: `UWR-GFX1100-RCA2A` · Host: Windows 11 (26300) + AMD Radeon RX 7900 XTX
(gfx1100 / DEV_744C, LUID 72346 desktop adapter) · EP: `Microsoft.WinML.AMD.GPU.EP.Framework.2`
1.8.65.0 MSIX (`amdgpu-ep.dll` SHA256 `cb7496b3ab8f9daf2948b6030db5ec795596072ffcb25d3c55a28e4c0df7f8ab`,
backend MIGraphX internal `version_2_17_0`).

## 1. Research question

1. What share of the observed 42–58 s cold session-create cost is MIGraphX/rocMLIR
   kernel-candidate **benchmarking** (auto-tuning) versus graph optimization / JIT
   compile / other fixed cost — measured by a recognized single-variable switch?
2. Which tuning/cache mechanisms in the **shipped Windows MSIX build** actually work:
   problem cache (tuning solutions), `.mxr` compiled-model cache, exhaustive tune,
   compute mode? Three-layer evidence: `UPSTREAM_SUPPORTED` (pinned source) →
   `SHIPPED_DLL_RECOGNIZED` (string scan) → `EFFECT_OBSERVED` (fresh-process canary).
3. Can first-tune results be **persisted and reused across processes** (cold-start
   reduction) with GPU truth, numeric parity, task parity and steady P50/P95
   unchanged (≤5 % regression tolerance)?

## 2. Three-layer capability method

- **Layer 1 upstream** (pinned): ROCm/AMDMIGraphX `2e2cb927` (dev-env-vars doc,
  `target.cpp`, `binary_cache.hpp`, `compile_ops.cpp`) and
  onnxruntime/onnxruntime-ep-amdgpu `main` (`gpu_options.h`, `gpu_ep.cc`,
  `mgx_ep.cc`, `mgx_program_ops.cc`). Distinguish: candidate benchmarking,
  problem cache (tuning solutions), binary cache, compiled `.mxr` model cache —
  four different layers that must never be conflated.
- **Layer 2 shipped DLL**: ASCII string scan of `amdgpu-ep.dll`,
  `migraphx-backend.dll`, `migraphx_gpu.dll`, `migraphx.dll` for exact env-var /
  option-key tokens. Presence = recognition only, never execution support.
- **Layer 3 physical**: one fresh child process per switch, isolated emptied cache
  dir, tiny canary GEMM ONNX via the AMDGPU EP with LUID-pinned physical device;
  effects judged from stdout/stderr log lines, cache-file state and wall time.
  CPU fallback is fail-closed (providers list re-checked; ORT python merely warns
  on unknown provider names — RCA2A-001).

Canary results (2026-11-18, live): default path DOES benchmark (13 configs for a
single 256³ GEMM, visible only under trace); `MIGRAPHX_SKIP_BENCHMARKING=1`
collapses it to 1 config and completes; `ORT_MIGRAPHX_EXHAUSTIVE_TUNE=1` raises
canary compile 0.63 s → 12.5 s; `ORT_MIGRAPHX_CACHE_DIR` yields a real `.mxr`
(270,891 B, `21100-<graphid>-<mlss>-<shapes-hash>.mxr`); `MIGRAPHX_TRACE_BENCHMARKING`
prints then crashes session init (RCA2A-003); `MIGRAPHX_GPU_OPTIONS` with
`problem_cache_files` crashes outright (RCA2A-004); `ORT_MIGRAPHX_PROBLEM_CACHE`
(read side) shows no observable effect at canary level; `MIGRAPHX_PROBLEM_CACHE` /
`MIGRAPHX_BINARY_CACHE` strings do not exist in the shipped build.

## 3. Frozen A/B matrix (EXPERIMENT_PLAN_LOCK.json)

Models: `yolo26n.raw_b1_fp32_static.onnx` (conv-light) and
`rtdetr-l.raw_b1_fp32_static.onnx` (transformer/GEMM), locked by SHA256 in the
plan; identical feeds/CPU references from the RCA1 soak cases.

Arms (all AMDGPU EP, LUID-pinned, FILE stdio, profiled):

| arm | env | cache watch | n |
|-----|-----|-------------|---|
| E0_BASELINE | – | arm-private empty dir | 3×2 |
| E2_SKIP_BENCH | `MIGRAPHX_SKIP_BENCHMARKING=1` | arm-private | 3×2 |
| E4_MXR_CACHE | `ORT_MIGRAPHX_CACHE_DIR=<shared>` | shared dir | 3×2 |
| E4_MXR_FRC | + `ORT_MIGRAPHX_FORCE_RECOMPILE=1` | shared dir | 3×2 |
| E6_COMPUTE_MAX | `ORT_MIGRAPHX_COMPUTE_MODE=maximum` | arm-private | 3×2 |
| E7_RECREATE | – (2 in-process session rebuilds) | arm-private | 3×2 |

Execution phases (to keep causal ordering honest): Phase 1 E0/E2/E6/E7
randomized-interleaved per rep; Phase 2 E4_MXR_CACHE rep0→1→2 sequential on a
provably empty shared dir (first tune → two new-process reuses); Phase 3
E4_MXR_FRC on the populated dir (forced-recompile invalidation); Phase 4 E8 DML
control (1 process) + E1 trace diagnostic (expected crash, candidate counts only)
+ 15-minute stability soak per model with session recreation and cache reuse.

Timeline recorded per attempt: `ep_discovery → register → identity → device probe
→ cache snapshot(pre) → session create → snapshot(post) → first run → snapshot →
warmup(30) → steady(200) → [recreate cycles] → snapshot(final) → teardown`, with
fsynced heartbeat events and monotonic phase clocks. Watchdogs: phase ceilings
from RCA1 calibration (SESSION_CREATE 900 s, FIRST_RUN 600 s, steady no-progress
180 s, total 3600 s) measured from the child's own phase-begin events; kills only
the mission-owned PID tree; every kill recorded with CPU-advance evidence.

## 4. Acceptance gates

- `AUTOTUNING_EXECUTION_CONFIRMED`: benchmark candidate counts under trace
  (bounded below if trace crashes early) + `E2` config-count collapse + paired
  wall-clock deltas, n≥3 processes per cell.
- `AUTOTUNING_CONTRIBUTION_QUANTIFIED`: E0 vs E2 paired create_s/first_run diffs
  with bootstrap CI; JIT/graph-opt split that cannot be instrumented is reported
  as `GRAPH_OPT_JIT_AUTOTUNE_SPLIT_NOT_INSTRUMENTED`, never invented.
- `MODEL_CACHE_EFFECTIVE` (E4): file created in process A; process B (fresh) with
  same ONNX+shape loads it (create_s drop, file not rewritten, `loaded_from_cache`
  telemetry when visible); FRC recompiles; parity unchanged.
- `STEADY_NOT_REGRESSED`: cached-vs-baseline steady P50/P95 each ≤ +5 %.
- `ACCURACY_AND_GPU_PASS`: profiler AMDGPU nodes > 0, supervisor-independent CPU
  parity (atol/rtol 5e-3 frozen from RCA1 cases), no silent CPU fallback
  (providers re-checked), physical LUID chain, clean exit.
- Unsupported mechanisms: `NOT_SUPPORTED_IN_SHIPPED_BUILD` with evidence, never
  phrased as “the mechanism is worthless”.

## 5. Supervision & integrity

- FILE-only child stdio (RCA1 root-cause fix, commit dee28a9 lineage); no pipes
  anywhere on the timing path.
- Receipts (schema `rca2a_receipt_v1`) carry `code_git_sha`, `worktree_dirty`,
  `main_base_sha`, `plan_sha256`, expected-vs-echoed env (a switch that never
  reached the process fails validation), ordered timeline, cache snapshots, and
  supervisor-derived GPU validation. Fail-closed validator + adversarial tests.
- Physical runs executed from a byte-frozen run tree whose every file SHA256 is
  recorded in `RUN_CODE_MANIFEST.json` (base files verified against the
  `origin/main` tree blob SHAs); the published branch re-runs the confirmation
  suite from its exact HEAD (mission §1 publication rule).
