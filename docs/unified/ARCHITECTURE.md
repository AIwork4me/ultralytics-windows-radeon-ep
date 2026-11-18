# Unified Windows ML 2.x GPU-First Runtime — Architecture

> 统一 Windows ML 2.x GPU 优先推理运行入口 — 架构设计
> Status: DESIGN (mission M3-C0). Implementation: `src/radeon_ort/unified_runtime.py` (vertical slice).
> Scope: gfx1151 (Radeon 8060S), gfx1100 (RX 7900 XTX), gfx1201 (RX 9070 XT) — one entrypoint, per-arch hardware evidence stays per-arch.

## 1. Design goal / 设计目标

One auditable call — `predict(model_path, inputs, policy=..., target_gpu=..., allow_cpu=False,
require_full_gpu=True)` — returns a `RunResult` that **proves or refuses** every claim:

* which physical GPU executed (VID/DID/PnP/DXGI/boot-LUID + final session GPU profile),
* which Execution Provider actually ran the graph (target-EP profiler node count > 0),
* numeric parity vs an independent CPU ORT oracle on the same ONNX + same input bytes,
* task-level oracle result from a versioned adapter,
* and — critically — **who authorized CPU** (nobody by default).

`get_providers()` is metadata, never proof (AGENTS.md rule 1; PROVIDER_FALLBACK.md).

## 2. User intent → policy / 用户意图与策略

| policy | semantics 语义 |
|---|---|
| `require_amdgpu` (default) | providers = `['AMDGPUExecutionProvider']` only. Any failure → `FAIL_RUNTIME`/`BLOCKED_EP` receipt. No CPU session behind it. 失败即关闭，绝不静默 CPU。 |
| `prefer_amdgpu_allow_dml` | AMDGPU first, fail-closed. Only if policy allowed AND AMDGPU attempt fully failed, a **new subprocess** (fresh interpreter, fresh session) tries DML on the **same physical Radeon** (LUID-checked). DML success reports `DML_PASS`, never `AMDGPU_PASS`; the original AMDGPU failure is always disclosed. |
| `cpu_opt_in` | Only with explicit user CLI/API flag (`allow_cpu=True` / `--allow-cpu`). Returns `CPU_ONLY_USER_AUTHORIZED`. Never reached implicitly; DML's provider list containing CPU does NOT constitute user authorization. |

CPU 同意（consent）是独立布尔，不能从 policy 推导，不能从环境继承，不能被 fallback 链"顺手"触发。

## 3. The four identity classes / 四类身份（不可互换）

| class | examples | lifetime | proof strength |
|---|---|---|---|
| **Physical GPU** | PnP `PCI\VEN_1002&DEV_7550`, DXGI desc + VID/DID + boot-local LUID, adapter ordinal (per-API, NOT transferable) | boot | strongest; ground truth |
| **Catalog EP entry** (Windows ML 2.x) | `EpName`/provider name, version, `ReadyState`, `library_path`, package id | install | metadata; Ready ≠ registered-in-my-ORT |
| **ORT registered library alias** | `AMDGPUExecutionProvider` (Catalog EpName) vs historical manual alias `AMDGPU` | process | registration fact in THIS process |
| **OrtEpDevice** | EP-device candidates with metadata (LUID, Discrete) — EP-internal index, NOT a DXGI ordinal | process/session | candidate→physical mapping must be proven, else `UNPROVEN` |

Rules: 不同类别的编号/索引不可互相赋值。When the chain physical→catalog→alias→ep-device has any
unproven link, the RunResult records `device_binding_confidence = "UNPROVEN"` and `PASS_GPU_EXECUTED_FULL`
is withheld (corroboration-only labels). Table-entry DIDs alone never grant `PHYSICAL_BINDING_CONFIRMED`.

## 4. Catalog-first flow (Windows ML 2.x) / Catalog 优先流程

```
discover (find_all_providers; read-only)
  → explicit target selection (EpName + version pin)
  → ensure_ready ONLY under an approved installation action (default: never; mock in tests;
    unauthorized install → BLOCKED_INSTALL_AUTHORIZATION)
  → obtain library_path
  → register in the SAME Python ORT runtime (ort.register_execution_provider_library)
  → bind the proven device (LUID-pinned add_provider_for_devices / provider options)
  → independently validate (canary + profiler nodes + parity + task oracle)
```

**Python-specific caveat (live-verified 2026-11):** `EnsureAndRegisterCertifiedAsync()` /
WinRT `try_register` may register into another native environment — NOT necessarily the
CPython `onnxruntime` of this venv. Therefore registration is only believed after
`ort.get_ep_devices()` (or a real session) proves the EP inside THIS interpreter. Catalog
`ReadyState` alone can never mark a run PASS.

BYO/MSIX manual registration (`Microsoft.WinML.AMD.GPU.EP.Framework.2` AppX +
`_rederive_real_paths()`/Get-AppxPackage fallback) remains a **first-class developer/legacy
path**, recorded distinctly (`bootstrap = "byo_msix"` vs `"catalog"`); the two are never
conflated in receipts.

## 5. Runtime layout / 运行时组成

```
src/radeon_ort/unified_identity.py    # 4-class identity chain + per-gfx DID pinning (B0)
src/radeon_ort/winml_catalog_adapter.py  # read-only discovery + authorized ensure-ready + registration state machine
src/radeon_ort/unified_runtime.py     # predict(): policy engine, subprocess isolation, RunResult assembly
runner/unified_predict.py             # CLI:  python -m runner.unified_predict --policy ... --allow-cpu
```

Reuse (not rewrite): `device_identity` (DXGI/PnP), `rca2a_ep` (LUID-pinned AMDGPU session),
`dml_session` (DML device pinning), `cpu_reference` (parity), `task_oracles` (task gates),
`rca2a_cache_key` (cache identity), `receipts` (immutable evidence).

Subprocess discipline (RCA1 lesson, commit dee28a9): every EP attempt runs in a fresh
interpreter with FILE-redirected stdout/stderr, phase-aware watchdog, bounded timeouts;
the supervisor assembles evidence from files, never pipes.

## 6. Cache key & invalidation / 缓存键与失效

Cache identity = `rca2a_cache_key.compute_cache_key`: full ONNX SHA-256, input signature
(shapes+dtypes), graph-optimization options, gfx arch, EP DLL SHA-256, EP/ORT/WinML versions,
driver, cache ABI/format version, effective tuning flags. Consequences:

* cross-arch reuse (gfx1100↔gfx1151↔gfx1201) is structurally impossible (arch in key),
* driver/EP-DLL drift invalidates,
* corrupted/unwritable cache ⇒ `CACHE_CORRUPT`/`CACHE_WRITE_FAIL` receipts, never "hit",
* default keeps first-run Auto-Tuning ON (no `MIGRAPHX_SKIP_BENCHMARKING=1` default —
  E2 showed RT-DETR −20% steady regression with skip).

## 7. Evidence & publication / 证据与发布

Every attempt appends an immutable receipt (new `run_id`); failures stay red. Published
evidence is redacted (no weights, no `.mxr`, no host paths/IDs). CI (`windows-cpu-ci`,
`secret-scan`) attests contracts only — `CPU_CI_DOES_NOT_ATTEST_GPU` remains a hard rule;
GPU evidence flows exclusively from reviewed branches with physical receipts.
