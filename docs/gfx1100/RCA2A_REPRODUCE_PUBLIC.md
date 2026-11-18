# RCA2A Public Reproduction Guide (gfx1100)

All commands were executed on Windows 11 (build 26300) with a physical AMD
Radeon RX 7900 XTX (gfx1100 / DEV_744C) and the shipped
`Microsoft.WinML.AMD.GPU.EP.Framework.2` 1.8.65.0 MSIX (MIGraphX internal
`version_2_17_0`). GPU evidence can only be reproduced on matching hardware +
EP stack; everything else (unit/adversarial tests, receipt validation,
statistics) runs on any CPU host.

## 0. Layout

- `$RUN_ROOT` — experiment root holding `EXPERIMENT_PLAN_LOCK.json`,
  `attempts/<attempt_id>/` (events.jsonl, receipt_rca2a.json, latencies.npz,
  child stdout/stderr FILES, ORT profiles), `CACHE_LABS/`, `ANALYSIS/`.
  In the public package the absolute local root is redacted to `%RUN_ROOT%`.
- Harness: `runner/rca2a_{main,worker,probe,g4_stats}.py`,
  `src/radeon_ort/rca2a_{ep,schema,cache_key,metrics}.py` (+ the one-file
  main-branch fix `dml_session.py` documented in `main_overrides/README.md`).

## 1. CPU-only verification (any host)

```bash
python -I -m pytest tests/unit/test_rca2a_units.py tests/unit/test_rca2a_dml_override.py \
                    tests/adversarial/test_rca2a_adversarial.py -q
python -I runner/rca2a_main.py verify   --run-root $RUN_ROOT
python -I runner/rca2a_main.py summarize --run-root $RUN_ROOT
python -I runner/rca2a_g4_stats.py      --run-root $RUN_ROOT
```

`verify` re-derives every receipt status from raw fields (schema
`rca2a_receipt_v1`); CPU CI never attests GPU results
(`CPU_CI_DOES_NOT_ATTEST_GPU`).

## 2. GPU experiment matrix (frozen plan, `plan_sha256` in the lock file)

Models (SHA-locked, never committed to the repo):

| model | ONNX sha256 (first 16) | input sha256 (first 16) | shape |
|---|---|---|---|
| yolo26n | `488e7e9876cbeafc` | `64d11ef4c1e47028` | 1×3×640×640 fp32 |
| rtdetr-l | `85f19ea31ed6a7df` | `64d11ef4c1e47028` | 1×3×640×640 fp32 |

Per attempt, in a fresh process (`-I`), env injected before any ORT import,
stdout/stderr redirected to files (RCA1 `dee28a9` contract):

```bash
python -I runner/rca2a_main.py runall --run-root $RUN_ROOT --attempt-tag <tag> \
    --main-base <origin/main-sha> --profile            # E0/E2/E4/E4_FRC/E6/E7 (frozen order)
python -I runner/rca2a_main.py run --run-root $RUN_ROOT --arm E6_COMPUTE_MAX \
    --model yolo26n --rep 9 --attempt-tag <tag> --main-base <sha> --profile \
    --session-create-ceiling 2700                      # AMENDMENT-1 (pre-registered)
```

Phase-4 sub-plans (E1 trace / E8 DML / soaks) each freeze their own
`EXPERIMENT_PLAN_LOCK.json` under `$RUN_ROOT/PHASE4_*` (AMENDMENT-2); soaks use
`--steady-iters` sized from measured per-iteration wall time to reach ≥900 s
effective steady inference with 2 session recreates as parity checkpoints.

## 3. Cache-lab discipline

- `ORT_MIGRAPHX_CACHE_DIR` points at arm-private or the shared
  `%RUN_ROOT%/CACHE_LABS/mxr_shared` directory; every attempt fingerprints the
  watched path (size/mtime/SHA256 ≤64 MiB budget) at pre_session / post_create
  / post_first_run / final.
- A cross-process **hit** = new-process create_s at cache level AND the `.mxr`
  fingerprints unchanged across all snapshots of that attempt.
- **Invalidation** = `ORT_MIGRAPHX_FORCE_RECOMPILE=1` arm: create_s returns to
  baseline (≥70 % of E0 mean) AND the `.mxr` is rewritten in-attempt AND the
  post-batch SHA differs from the preserved Phase-2 SHA
  (`CACHE_MANIFEST_PUBLIC.json` stages `phase2_final_preserve_and_live` →
  `post_frc_batch`).

## 4. What is NOT in the public package

Model weights/ONNX, `.mxr` binaries, full ORT profiles of soak sessions, raw
npz tensors, host identifiers, absolute local paths, and the 95 GB historical
freeze + RCA1 raw tree stay LOCAL-ONLY; `LOCAL_RAW_EVIDENCE_INDEX.json` maps
each withheld artifact to its local SHA256 (relative paths only).
