# RCA2A Review Index — what to read, in what order, and where every claim lives

Reviewer fast-path (Owner/ChatGPT). PR base: `main` @ `492975284f7c93684d6dbd67801a5634f450f7a0`.
Branch HEAD / PR URL / CI run: appended at publication time (see final report §B).

## 1. One-paragraph claim

On the shipped Windows AMDGPU EP 1.8.65.0 (MIGraphX 2_17_0), session-create cost
is dominated by different layers per model; the **kernel-candidate
benchmarking** layer alone is 59 % (yolo26n) / 23 % (rtdetr-l) of create
(proven by the DLL-recognized single-variable `MIGRAPHX_SKIP_BENCHMARKING`
switch). The **`.mxr` compiled-model cache** (`ORT_MIGRAPHX_CACHE_DIR`) works
cross-process: cold-tune once, then every fresh process creates the session
**−98 % / −97 % faster**, with unchanged numerics, unchanged steady mean
(+2.6 % / +0.1 %, gate ≤5 %) and a working invalidation switch
(`ORT_MIGRAPHX_FORCE_RECOMPILE`). The **problem cache** (tuning solutions)
cannot be redirected in this build (fail-closed crash) and **kernel binary
cache** tokens are absent — four distinct layers, four distinct verdicts.

## 2. Reading order

| # | file | what it proves / contains |
|---|---|---|
| 1 | `docs/gfx1100/RCA2A_DESIGN_AND_METHOD.md` | research questions, three-layer capability method, frozen matrix |
| 2 | `docs/gfx1100/RCA2A_ABLATION_AND_VERDICTS.md` | every number vs its receipt, verdict ledger |
| 3 | `reports/gfx1100/rca2a/RCA2A_20261118T1140Z/G4_STATISTICS.json` | machine-recomputable stats & gates (run `runner/rca2a_g4_stats.py`) |
| 4 | `reports/.../receipts/*.json` | one redacted receipt per attempt (schema `rca2a_receipt_v1`); classifications re-derived by `runner/rca2a_main.py verify` |
| 5 | `reports/.../CAPABILITY_MATRIX.json` | upstream-pinned → DLL-string → physical-effect per switch |
| 6 | `reports/.../RCA_REGISTER_PUBLIC.md` | RCA2A-001…008 with root causes |
| 7 | `reports/.../CACHE_MANIFEST_PUBLIC.json` | `.mxr` SHA/size/mtime stages (phase2 preserve → post-FRC rewrite) |
| 8 | `reports/.../ENVIRONMENT_LOCK_PUBLIC.json` | host/EP fingerprint (24 DLL SHA256, MSIX version, upstream pins) |
| 9 | `reports/.../THRESHOLDS_LOCK.json` | pre-registered acceptance thresholds (never relaxed) |
| 10 | `reports/.../SOAK_RESULTS.json` | 15-min effective-steady soaks, recreate parity checkpoints |
| 11 | `docs/gfx1100/RCA2A_REPRODUCE_PUBLIC.md` | how to re-verify (CPU) / re-run (GPU) |
| 12 | `reports/.../GFX1100_RCA2A_FINAL_CN.md` | final Chinese A–J report |
| 13 | `reports/.../REDACTION_SCAN.json` + `MANIFEST.sha256` | leak scan result + content freeze |

## 3. Code being reviewed

- `runner/rca2a_main.py` — supervisor: plan/run/verify/summarize, FILE stdio,
  phase watchdogs, fail-closed GPU_PASS validation, immutable receipts.
- `runner/rca2a_worker.py` — instrumented child: stage timeline, cache
  fingerprints, parity at first-run & every recreate, silent-CPU-fallback block.
- `runner/rca2a_probe.py` — capability probes → `CAPABILITY_MATRIX.json`.
- `runner/rca2a_g4_stats.py` — G4 statistics/gates from raw receipts.
- `src/radeon_ort/rca2a_{ep,schema,cache_key,metrics}.py` — EP registration
  (Appx fallback, LUID pinning), receipt schema v1, cache-key rules, stats.
- `src/radeon_ort/dml_session.py` — **one backward-compatible keyword added**
  (`sess_options_override`, RCA2A-008; see `main_overrides/README.md`).
- `tests/unit/test_rca2a_units.py`, `tests/unit/test_rca2a_dml_override.py`,
  `tests/adversarial/test_rca2a_adversarial.py` — 38 CPU tests incl. forged-
  label, missing-event, CPU-fallback-forgery, cache-confusion adversarial cases.

## 4. Review questions the submitter explicitly flags

1. The n=3-per-arm sample size is the frozen plan's; CIs are flagged
   `low_power` and quoted as descriptive only.
2. E6 (compute_mode=maximum) on rtdetr-l is `BLOCKED_BY_PRESET_CEILING`
   (AMENDMENT-1) — an honest optional-arm ceiling, not a hidden failure.
3. `MIGRAPHX_TRACE_BENCHMARKING` crashes session init in this build
   (RCA2A-003): candidate-count evidence is a lower bound from pre-crash output.
4. rtdetr-l DML parity drift is a pre-existing RCA3 issue, out of scope; the
   E8 DML control uses yolo26n only and never substitutes AMDGPU gates.
5. Publication code was re-run on the exact PR HEAD (PUBCONF receipts,
   `code_git_sha` = branch HEAD, `worktree_dirty=false`).

## 5. Local-only boundary (not in this PR)

95 GB historical freeze, RCA1 raw tree, ONNX/PT/mxr binaries, raw tensors,
full soak ORT profiles, host identifiers. `LOCAL_RAW_EVIDENCE_INDEX.json`
holds SHA256 anchors for withheld artifacts.
