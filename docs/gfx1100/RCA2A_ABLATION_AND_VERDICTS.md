# RCA2A Ablation & Verdicts — gfx1100 (RX 7900 XTX, AMDGPU EP 1.8.65.0 / MIGraphX 2_17_0)

Every number below is re-derivable from `receipts/` + `G4_STATISTICS.json`
(runner: `python -I runner/rca2a_g4_stats.py --run-root $RUN_ROOT`).
Timing-granularity discipline: steady per-run latencies quantize to ~15.6 ms
steps on this host, so the **mean** of per-iteration monotonic deltas is the
gate statistic; p50/p95 are REFERENCE_ONLY. `n` = independent fresh processes.

## 1. Cold session-create decomposition (CLEAN attempts only)

### yolo26n (E0 mean create 43.64 s over n=3)

| arm | create_s per rep | mean | vs E0 | steady mean ms (gate) |
|---|---|---|---|---|
| E0_BASELINE | 43.66 / 43.95 / 43.30 | 43.64 | — | 2.91 |
| E2_SKIP_BENCHMARKING | 17.72 / 17.75 / 17.92 | 17.80 | **−59.2 %** (paired median −59.4 %, bootstrap CI95 [58.6, 59.6], low-power n=3) | 2.94 (+1.0 %, NOT REGRESSED) |
| E6_COMPUTE_MAX (exhaustive) | 572.78 / 506.91 (n=2 clean; rep0 killed at pre-registered 900 s, evidence preserved) | 539.84 | **+1237 %** (≈12.4×) | 2.89 (+1.9 %, NOT REGRESSED — exhaustive tune buys no steady speed here) |
| E7 in-process recreate | 42.89–43.30 (create), recreate cycles 42.8–43.3 | ≈E0 | 0 % | first-run-after ≈ 0.016 s |
| E4_MXR_CACHE cold tune (rep0) | 44.73 | 44.73 | +2.5 % | 2.97 |
| **E4_MXR_CACHE cached** (rep1/2/9) | 0.78 / 0.77 / 1.02 | **0.86** | **−98.0 %** | 3.00 (+2.6 %, NOT REGRESSED) |
| E4_MXR_FRC (force recompile) | 44.64 / 43.47 / 43.58 | 43.90 | ratio 1.006×E0 → switch EFFECTIVE | 3.0 |

### rtdetr-l (E0 mean create 115.87 s over n=3)

| arm | create_s per rep | mean | vs E0 | steady mean ms (gate) |
|---|---|---|---|---|
| E0_BASELINE | 115.59 / 115.64 / 116.38 | 115.87 | — | 12.63 |
| E2_SKIP_BENCHMARKING | 89.23 / 88.50 / 89.55 | 89.09 | **−23.1 %** (paired median −23.1 %, CI95 [22.8, 23.5], low-power) | 15.16 (**+20.0 % STEADY_REGRESSED → COLD_FAST_STEADY_SLOW**) |
| E6_COMPUTE_MAX | rep0 killed at 900 s ceiling; **BLOCKED_BY_PRESET_CEILING** (AMENDMENT-1: est. 13–20× ⇒ ~1500–2300 s+, optional arm not worth speculative GPU hours) | — | — | — |
| E7 in-process recreate | 115.22 / 115.88 / 117.19 create; recreates 115.4–116.5 | ≈E0 | 0 % | first-run-after ≤ 0.031 s |
| E4_MXR_CACHE cold tune (rep0) | 121.95 | 121.95 | +5.2 % | 12.50 |
| **E4_MXR_CACHE cached** (rep1/2/9) | 3.86 / 3.72 / 4.05 | **3.88** | **−96.7 %** | 12.68 (+0.1 %, NOT REGRESSED) |
| E4_MXR_FRC (force recompile) | 121.52 / 124.47 / 122.22 | 122.74 | ratio 1.059×E0 → switch EFFECTIVE | 12.7 |

## 2. Cache evidence chain (`.mxr` compiled-model cache)

1. **Persist**: `ORT_MIGRAPHX_CACHE_DIR` → `21100-<graphid>-<mlss>-<shapehash>.mxr`
   written on cold tune (yolo 11,075,403 B; rtdetr 183,121,308 B; per-model
   graphid ⇒ natural per-model keying).
2. **Cross-process hit**: rep1/2/9 create collapses 43.6→0.8 s / 115.9→3.9 s
   while in-attempt fingerprints show the `.mxr` SHA/size/mtime UNCHANGED
   (hit ≠ rewrite). Reproduced on a second day-batch (rep9) before FRC.
3. **Invalidation control**: `ORT_MIGRAPHX_FORCE_RECOMPILE=1` (DLL-recognized,
   upstream `mgx_ep.cc load_compiled_program` gate) → create returns to
   baseline (ratios 1.006 / 1.059) AND the `.mxr` is rewritten in-attempt
   (pre→post snapshot SHA/size/mtime change) AND post-batch SHAs differ from
   the preserved Phase-2 copies (`CACHE_MANIFEST_PUBLIC.json`).
4. **Same-key integrity**: filename = version+graphid+shape-hash; FRC rewrote
   the same names (no stale-key divergence), GPU_PASS + parity re-validated on
   every FRC attempt.

## 3. The four cache layers — separate verdicts (never conflated)

| layer | shipped-build status | evidence |
|---|---|---|
| Kernel-candidate benchmarking (rocMLIR autotune) | **EXECUTES** in default path (13 configs/canary op under trace; skip switch collapses 13→1 with matching create-time drop) | CAPABILITY_MATRIX + E2 |
| Problem cache (tuning solutions) | **READ-ONLY shipped library** (gfx1100: 672 solutions); user redirect `MIGRAPHX_GPU_OPTIONS problem_cache_files` **crashes** (fail-closed) → `NOT_SUPPORTED_IN_SHIPPED_BUILD`; `auto_save_problem_cache` tries to write the read-only package dir every session (harmless WARN) ⇒ tuning solutions cannot be persisted in this build | RCA2A-004/005/007 |
| Kernel binary cache (`MIGRAPHX_BINARY_CACHE`) | env token **absent** from shipped DLLs → `NOT_SUPPORTED_IN_SHIPPED_BUILD` | CAPABILITY_MATRIX |
| Compiled-model cache (`.mxr`) | **EFFECTIVE + INVALIDATABLE** (this report §2) — orthogonal to problem cache: E4 hit does not persist tuning solutions, it persists the compiled program | RCA2A-006 |

## 4. Diagnostics & negative controls

- **E1 TRACE** (`MIGRAPHX_TRACE_BENCHMARKING=1`, yolo26n, fresh process,
  FILE stdio): stdout prints `Benchmarking gpu::mlir_op: 25 configs` and
  `Fastest solution: v3:64,64,64,16,2,4,1,1,2` during session create (~38 s),
  then `EPFail : 11 : Failed to call function` → child rc=42, classification
  RUNTIME_ERROR (RCA2A-003 reproduced on the full model; canary showed the same
  at 13 configs). Candidate counts are **lower bounds from pre-crash output**
  (crash may fire at an early operator). Never compared for timing.
- **E8 DML control** (yolo26n on DirectML, same case/oracle/supervisor):
  first run exposed two harness defects, both fixed with regression tests
  before the recorded rerun — RCA2A-008 (worker→dml_session call contract) and
  RCA2A-009 (supervisor wrongly required an amdgpu-only probe event on the DML
  path). Recorded rerun `E8_DML_yolo26n_rep1_p2b`: CLEAN_COMPLETE,
  **GPU_PASS_VALIDATED** (gpu_identity vendor 0x1002, DmlExecutionProvider,
  231 DML profile nodes > 0, supervisor parity max_abs 2.47e-03), create
  0.203 s. Both receipts retained (rep0_p2 = the honest pre-fix INVALIDATED
  output). DML is a control only — it never substitutes an AMDGPU gate.
- **E6 exhaustive** (compute_mode=maximum) under AMENDMENT-1 (2700 s ceiling,
  pre-registered): `E6_COMPUTE_MAX_yolo26n_rep9_p2b` CLEAN_COMPLETE
  GPU_PASS_VALIDATED, create 506.9 s → two clean samples total
  (572.78 / 506.91 s, mean 539.8 s ≈ 12.4× baseline), steady mean 2.89 ms ≈
  E0 2.91 ms (+1.9 %) — exhaustive tuning buys **no** steady speed on this
  model. rtdetr-l E6 stays `BLOCKED_BY_PRESET_CEILING` (AMENDMENT-1).

## 5. Soak (15-min effective steady, `.mxr` cache-reuse path)

| model | attempt | create_s | effective steady | iters | recreates (cache-hit!) | parity | verdict |
|---|---|---|---|---|---|---|---|
| yolo26n | PHASE4 rep0_p2 | 0.766 | 877.7 s (14.63 min, below-900 marked) | 300,000 | 2 × 0.64 s, first-run-after 16 ms | 3/3 PASS | GPU_PASS_VALIDATED |
| yolo26n | PUBCONF root rep0_p2 | 0.766 | **916.8 s (15.28 min ✓)** | 315,000 | 2 × ~0.6 s | 3/3 PASS | GPU_PASS_VALIDATED |
| rtdetr-l | PHASE4 rep0_p2 | 3.688 | **943.5 s (15.72 min ✓)** | 72,500 | 2 × 3.6–3.7 s, first-run-after 31–32 ms | 3/3 PASS | GPU_PASS_VALIDATED |

0 watchdog kills, 0 silent CPU fallback, clean exits. Steady means over the
full soak: 2.87 ms (yolo, n=300k) / 12.82 ms (rtdetr, n=72.5k) — consistent
with the 200-iter arm statistics.

**New finding**: with `ORT_MIGRAPHX_CACHE_DIR` set, in-process session
recreate hits the cache (0.64 s / 3.6 s) — versus E7 (no cache dir) where the
same recreate is a full recompile (42.8 s / 115.4 s). The `.mxr` cache
therefore accelerates BOTH new processes and in-process session rebuilds.

## 6. Verdict ledger (validator-recomputable)

| gate | verdict |
|---|---|
| AUTOTUNING_EXECUTION_CONFIRMED | **YES** (trace lines + single-variable skip switch effect, both models) |
| AUTOTUNING_CONTRIBUTION_QUANTIFIED | **YES** — benchmarking = 59 % (yolo26n) / 23 % (rtdetr-l) of session-create; remainder = graph-opt/JIT/fixed cost, split `GRAPH_OPT_JIT_AUTOTUNE_SPLIT_NOT_INSTRUMENTED` (no internal MIGraphX stage timing exposed) |
| MODEL_CACHE_EFFECTIVE (.mxr) | **YES** (persist + cross-process hit + controlled invalidation) |
| PROBLEM_CACHE (user-side) | **NOT_SUPPORTED_IN_SHIPPED_BUILD** (fail-closed crash / read-only shipped library) |
| BINARY_CACHE | **NOT_SUPPORTED_IN_SHIPPED_BUILD** (token absent) |
| COLD_START_REDUCTION | **YES** −98.0 % / −96.7 % (n=3 cached processes each; low-power CI flagged) |
| STEADY_NOT_REGRESSED (cache) | **YES** (+2.6 % / +0.1 % mean, both ≤5 %) |
| STEADY (skip-benchmarking) | yolo26n OK (+1.0 %) but rtdetr-l **COLD_FAST_STEADY_SLOW** (+20.0 %) → not a production default for transformer models |
| E6 exhaustive | legitimate but ~12.4× create cost (n=2), zero steady gain on yolo26n → not recommended default; rtdetr-l BLOCKED_BY_PRESET_CEILING |
| SOAK 15-min (cache path) | **PASS both models** (916.8 s / 943.5 s effective, 2 cache-hit session recreates each, parity 3/3, 0 kills, 0 fallback) |
| DML negative control | PASS after RCA2A-008/009 harness fixes (control only, never substitutes AMDGPU) |
| GPU_TRUTH | all CLEAN attempts `GPU_PASS_VALIDATED` (LUID-pinned physical gfx1100, AMDGPU profile nodes > 0, supervisor-independent parity, provider fail-closed); E1 TRACE crash is the expected diagnostic arm failure (RCA2A-003), preserved as evidence |
