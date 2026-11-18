# Unified Runtime Failure Matrix — every refusal, classified

> 失败矩阵：每种异常输入/环境形态 × 策略 ⇒ 机器可判定的结果。
> 原则：fail-closed；宁可 BLOCKED/FAIL，不可含糊 PASS；CPU 未经同意绝不出现在执行路径。

| # | scenario 场景 | policy | expected outcome |
|---|---|---|---|
| 1 | EP alias wrong / DLL missing (`BLOCKED_EP`) | any | `BLOCKED`, reason `BLOCKED_EP`, exit 34; no CPU session |
| 2 | requested gfx arch not present on host (e.g. gfx1201 asked, gfx1100 found) | any | `BLOCKED`, `WRONG_OR_AMBIGUOUS_DEVICE` naming found adapters; exit 34 |
| 3 | two AMD dGPUs, ambiguous identity | any | `BLOCKED` (never "adapter 0") unless LUID pinned |
| 4 | candidate EP device metadata lacks LUID | amdgpu | select = `EP_DEVICE_SELECTED+PHYSICAL_GPU_CORROBORATED;BINDING_UNPROVEN`; GPU_FULL withheld |
| 5 | GPU provider registered but graph runs 0 target-EP nodes (CPU-only session) | any | `SILENT_CPU_FALLBACK_BLOCKED`, exit 35 — never PARTIAL-as-PASS |
| 6 | CPU nodes > 0 alongside GPU nodes | require_full_gpu=True | refusal `PARTIAL_CPU` + counts; never `GPU_FULL` |
| 7 | CPU fallback attempted without user consent | any | contract breach → verifier INVALID + runtime refuses (no CPU call at all) |
| 8 | user passed `--allow-cpu` | cpu_opt_in | `CPU_ONLY_USER_AUTHORIZED`, consent echoed True |
| 9 | DML selects non-target adapter (other vendor / virtual) | prefer…dml | `DML_FAILED`, LUID/ordinal mismatch disclosed, exit 32 |
| 10 | AMDGPU fails, DML allowed+same physical GPU succeeds | prefer…dml | `GPU_FULL_DML` + original `AMDGPU_FAILED` disclosure (never AMDGPU_PASS) |
| 11 | both EPs fail, no CPU consent | any | `FAIL` terminal; CPU never invoked |
| 12 | watchdog timeout in child | any | kill owned PID tree only; receipt `TIMEOUT_<PHASE>` + `worker_exit_code`; exit 34/31 per phase |
| 13 | corrupt / read-only cache dir | any | `cache_status=CORRUPT`; run proceeds cache-cold (correctness unaffected) or BLOCKED if write-required |
| 14 | cross-gfx cache reuse attempt (gfx1100 entry on gfx1201) | any | key mismatch → `INVALIDATED`; structurally impossible (arch in key) |
| 15 | ONNX/input SHA drift vs frozen plan | any | `RECEIPT_INVALID_*_MISMATCH` family; refuse to bind |
| 16 | NaN/Inf in GPU outputs vs CPU oracle | any | `numeric_oracle.allclose=False` ⇒ FAIL (41) |
| 17 | Catalog shows AMDGPU Ready but ORT session lacks EP (projection/env mismatch) | any | Catalog is metadata only → attempt real registration; failure ⇒ #1 semantics |
| 18 | ensure_ready would download/install system components without authorization | any | `BLOCKED_INSTALL_AUTHORIZATION` (exit 34); mock path returns same label in tests |
| 19 | empty PT oracle / missing reference outputs | any | `numeric_oracle` cannot be assembled ⇒ GPU_FULL withheld (`NOT_RUN` reason recorded) |
| 20 | task oracle metric below pre-registered threshold | any | `task_oracle.status=FAIL` ⇒ overall FAIL despite numeric parity |
| 21 | `get_providers()` lists GPU but profiler shows none | any | treated as #5 (metadata is not proof) |
| 22 | redaction leak attempt in receipt (path/hostname) | any | receipt rejected by redaction gate before publish |
| 23 | duplicate EP candidate devices (mirror adapters) | amdgpu | deterministic pinning by physical LUID; unpinnable ⇒ #3 |
| 24 | subprocess fragment "succeeds" after watchdog kill | any | supervisor owns exit code; late writes ignored, receipt marks killed_by_watchdog |
| 25 | unknown gfx target string | any | `UNSUPPORTED_GFX_TARGET` listing {gfx1100, gfx1151, gfx1201} |

## Soak / steady-state gates (hardware runs)

* `GPU_FULL` soak claim requires ≥900 s **effective steady** (excluding session create,
  warmup, recreate pauses), ≥1 controlled session recreate classified within budget,
  periodic CPU-oracle parity during the run (rca2a `SOAK_MIN_EFFECTIVE_SECONDS=900.0`).
* Sub-3 ms models: per-iteration percentiles are enqueue-timer dominated; report
  throughput (n, mean, effective seconds) as authoritative (see RCA2A review P2-3).
