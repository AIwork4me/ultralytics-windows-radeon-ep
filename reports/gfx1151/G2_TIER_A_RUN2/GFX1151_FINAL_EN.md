# GFX1151 Final Report (EN) — Windows ML 2.x × Ultralytics × Radeon 8060S

**Mission:** `UWR-GFX1151-FOUNDATION-TO-VALIDATION` · **Date:** 2026-11-17 · **Repo:**
AIwork4me/ultralytics-windows-radeon-ep · **Draft PR #2** · Run `G2_TIER_A_RUN2`

## Verdicts

| Gate | Verdict |
|---|---|
| G0 foundation (repo/CI/invite/protection) | GO |
| G1 EP preflight (identity + CPU/AMDGPU/DML canary) | GO |
| G2 YOLO26 seven-task + yolo11n/v8n | GO — 18/18 `PASS_GPU_EXECUTED_FULL` |
| G3 catalog disposition | CONDITIONAL_GO — 142-entry honest ledger; 33 Tier B baselines NOT_RUN (resume cmd recorded) |
| G4 perf/soak/adversarial | GO — 5/5 negative injections fail-closed; soak artifacts; benchmarks in receipts |

`gfx1151_ONLY_GO` = yes. Cross-host claims: gfx1100/gfx1201 remain **NOT_RUN** (issue #1
tracks the gfx1201 runbook for LZ-QWQ, write collaborator, invitation accepted).

## What a GPU PASS required (and got) per case

1. Physical 8060S identity: DXGI vendor 0x1002/device 0x1586 + LUID + PnP cross-check
   (integrated GPU: no Discrete/dedicated-VRAM requirement; dual enumeration deduped).
2. Registered EP provenance: machine-wide `MicrosoftCorporationII.WinML.AMD.GPU.EP.1.8`
   `amdgpu-ep.dll` (sha256 cb7496b3…), registered via the documented
   `ort.register_execution_provider_library("AMDGPU", path)`.
3. Target-EP profiler node count > 0 (real provider key), zero CPU nodes for FULL claims.
4. By-name fp32 parity vs CPU ORT on the identical ONNX (max_abs ≤ 1.6e-3).
5. PT-application task oracle (same image/thresholds) PASS.
6. Immutable receipt + strict validator recomputation (forged labels fail CI).

## Silent-CPU-fallback findings (live, adversarially tested)

- Wrong provider name (`AMDGPUExecutionProvider`) → ORT python wrapper rebuilds a
  CPU-only session and returns rc=0 — only the profiler catches it (observed twice;
  encoded in `tests/adversarial` and `runner.negative_injection`).
- AMDGPU ep-device dev[0] (display enumeration) fails `hipSetDevice(0)` with HIP 101;
  dev[1] executes. Device selection is a fail-closed probe, never adapter-0 fallback.

## Numbers (warmed Session.run, 30 samples, perf_counter_ns)

p50 by case: AMDGPU 1.5 ms (yolo26n-cls) … 14.2 ms (yolo26n-depth); DML 0.6 … 9.6 ms.
Full p50/p95/p99/mean/std, cold session-create, and per-output parity metrics live in
`receipts/receipt_*.json` under `benchmark_data` / `parity`.

## Remaining

- Tier B breadth: run the resume command in `DISPOSITION_LEDGER.json` (33 baselines,
  then RESOURCE_GATED/OPTIONAL_STRESS tiers with budgets).
- WinML ≥ 2.5.96 surface (AMDGPUExecutionProvider): re-verify when wasdk publishes it.
- `ensure_ready_async` 0x80073B0C root cause (Store/network env) — low priority while
  the vendor EP MSIX is preinstalled.
