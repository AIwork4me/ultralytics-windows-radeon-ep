# ultralytics-windows-radeon-ep

Independent, fail-closed validation of **Ultralytics YOLO ONNX models** on
**Windows ML 2.x** with the **AMDGPU Execution Provider** and **DirectML EP**,
starting on the **AMD Radeon 8060S (gfx1151, Ryzen AI MAX APU)**.

> A GPU `PASS` here means *proven*: verified physical device identity, positive
> target-EP profiler node count, numeric parity vs the same-ONNX CPU ORT
> reference, and a task-level oracle. `sess.get_providers()` alone proves nothing.

## Status

| Host | AMDGPU EP | DirectML | Evidence |
|---|---|---|---|
| gfx1151 — Radeon 8060S | `BLOCKED_EP` — Windows ML catalog offers no AMD GPU EP on this host | **9/9 Tier A PASS** (physical) | `reports/gfx1151/R5_GFX1151_20261119T1240/` + `reports/mission_r4/PROBE_AFTER_A1.json` |
| gfx1100 — RX 7900 XTX | PENDING_PHYSICAL_HOST | PENDING_PHYSICAL_HOST | handoff: `docs/cross_host/GFX1100_HANDOFF.md` |
| gfx1201 — RX 9070 XT | PENDING_PHYSICAL_HOST | PENDING_PHYSICAL_HOST | handoff: `docs/cross_host/GFX1201_HANDOFF.md` |

Verified on this host (2026-11-19): Windows 11 25H2 build 26200.9974, Radeon 8060S
DID `0x1586` / LUID `0x00000000:0x000154E4`, driver 32.0.22018.5,
`windowsml 2.5.96`, `onnxruntime-windowsml 1.25.2`. The official catalog answers
`S_OK` with **zero** providers, so AMDGPU is an external block — not a code bug,
and not something a driver bump alone can fix.

`CPU_CI_DOES_NOT_ATTEST_GPU`: GitHub CPU CI validates contracts and adversarial
anti-forgery tests only.

## Quickstart (Windows, Radeon GPU)

```powershell
git clone https://github.com/AIwork4me/ultralytics-windows-radeon-ep
cd ultralytics-windows-radeon-ep
python -m venv .venv-winml
.venv-winml\Scripts\pip install -e .[winml] pytest
# host fingerprint + WinML EP catalog + device identity + CPU/AMDGPU/DML canary:
.venv-winml\Scripts\python -m runner.preflight --gfx gfx1151
# Tier A seven-task smoke (requires models in models/pt, see docs/INSTALL_WINDOWS.md):
.venv-winml\Scripts\python -m runner.smoke --winml-python .venv-winml\Scripts\python --export-python <export-venv-python>
# strict receipt verification:
.venv-winml\Scripts\python -m runner.verify_receipts --run-dir reports\gfx1151\<run_id>
```

## Unified API (one entrypoint, three policies)

```python
from radeon_ort.unified_runtime import predict

result = predict(
    model_path="models/onnx/yolo26n.raw_b1_fp32_static.onnx",
    inputs={"images": tensor},          # name -> ndarray; hashed by name/dtype/shape/bytes
    policy="prefer_amdgpu_allow_dml",   # require_amdgpu | prefer_amdgpu_allow_dml | gpu_first_allow_cpu | cpu_opt_in
    target_gfx="gfx1151",               # inferred from the live DID when omitted, never defaulted
    allow_cpu=False,                    # CPU runs only with explicit consent
    require_full_gpu=True,
)
print(result["runtime_status"])         # GPU_FULL | GPU_FULL_DML | CPU_ONLY_USER_AUTHORIZED | BLOCKED | FAIL
print(result["attempted_eps"], result["fallback_reason"])
```

A GPU result requires positive target-EP profiler nodes, **zero** CPU nodes, by-name
elementwise parity against an independent CPU ORT run, and a device-binding label
from a small whitelist. `gpu_full` and `task_verified` are reported separately.
CLI: `python -m runner.unified_predict --help`; contract:
`docs/unified/CONTRACTS.md`.

### Task verification (R7): two independent oracles

Numeric parity answers "did the GPU compute the same numbers". Task correctness
needs a second, independent oracle: the pinned Ultralytics reference model run on
the **byte-identical** preprocessed tensor, decoded by the vendor postprocess and
compared against pre-declared thresholds.

```python
result = predict(
    model_path="models/onnx/yolo26n.raw_b1_fp32_static.onnx",
    inputs={"images": tensor},
    policy="prefer_amdgpu_allow_dml",
    target_gfx="gfx1151",
    task="detect",
    verify_task=True,                       # R7-A
    pt_reference="models/pt/yolo26n.pt",    # bound by sha256 in the evidence
)
result["gpu_full"], result["task_verified"]  # two independent claims
result["task_parity"]["status"]              # PASS_TASK on this host
```

```powershell
.venv-winml\Scripts\python -m runner.unified_predict --model models\onnx\yolo26n.raw_b1_fp32_static.onnx `
  --inputs-npz in.npz --policy prefer_amdgpu_allow_dml --gfx gfx1151 `
  --task detect --verify-task --pt-reference models\pt\yolo26n.pt `
  --run-id GFX1151_R7_TASK_DETECT --out r7_detect.json
# 7 tasks in one matrix (real images, real GPU):
.venv-winml\Scripts\python -m runner.unified_task_matrix --gfx gfx1151 `
  --out reports\mission_r7\unified_task_matrix.json
```

A missing or incompatible reference is `TASK_ORACLE_BLOCKED` with a concrete
reason - never a fabricated `PASS_TASK`. Exit code `4` means "the GPU ran but the
task did not verify".

### Process-tree safety and attempt isolation (R7-B/R7-C)

Every attempt runs in its own **Windows Job Object** (`KILL_ON_JOB_CLOSE`,
created suspended, assigned, then resumed), so a wedged EP and every process it
spawned are reclaimed together. Each attempt also gets a unique `attempt_id` and
a private directory; input bundles are content-addressed and re-hashed by the
worker, so a stale or substituted file cannot feed a new GPU PASS.
Re-using a `run_id` whose receipt exists is refused.

### Measured end-to-end performance (R7-E, this host)

20 real photographs (Unsplash License, hashes in
`data/fixtures/bench_images/SOURCES.json`), DML, 3 independent processes,
warmup 5 + 20 requests each:

| model | model-only p50 | pipeline E2E p50 | session create | throughput |
|---|---|---|---|---|
| yolo26n detect | 5.2 ms | **18.2 ms** | 233-257 ms | ~52 FPS |
| yolo26n-seg | 6.4 ms | **21.4 ms** | 263-270 ms | ~45 FPS |
| yolo26n-pose | 5.3 ms | **16.9 ms** | 257-259 ms | ~56 FPS |

The pipeline overhead (JPEG decode 6.9 ms + preprocess 4.0 ms) is more than
double the GPU inference time on this APU: optimise the input path before the EP.
Temperature/power/GPU activity are `NOT_MEASURED` (no supported sensor path) and
are never replaced by a fixed clock. Full data:
`reports/mission_r7/e2e_bench.json`, analysis:
`docs/mission_r7/R7_FINAL_CN.md`.

### Cross-host validation (R8/R9)

```powershell
# plan-only on any host; refuses GPU work on the wrong architecture
.venv-winml\Scripts\python -m runner.cross_host_validate --gfx gfx1100 --tier core --policy gpu-first --dry-run
# on the real RX 7900 XTX / RX 9070 XT host:
.venv-winml\Scripts\python -m runner.cross_host_validate --gfx gfx1100 --tier core --policy gpu-first --run-id <NEW_ID>
```

A host whose live DXGI DID is not the requested architecture stops with
`WRONG_PHYSICAL_DEVICE` (exit 34) before any ONNX session or GPU stage.

See [docs/INSTALL_WINDOWS.md](docs/INSTALL_WINDOWS.md) for the three-venv layout
(winml / export / cpu), [docs/DEVICE_IDENTITY.md](docs/DEVICE_IDENTITY.md) for how
the 8060S is selected (integrated GPU: no `Discrete=True` requirement), and
[docs/PROVIDER_FALLBACK.md](docs/PROVIDER_FALLBACK.md) for the strict no-silent-
CPU policy.

## Repository layout

- `src/radeon_ort/` — harness modules (device identity, EP catalog, sessions, receipts, oracles, benchmark, manifest, redaction)
- `runner/` — CLI: `preflight`, `generate_catalog`, `export_models`, `smoke`, `run_matrix` (v2), `r1_pipeline`, `resume_matrix`, `bench_repeats`, `soak`, `negative_injection`, `analyze_profiles`, `finalize_run`, `build_compatibility_matrix`, `redact_evidence`, `prepare_evidence`, `verify_receipts`, `unified_predict`, `unified_task_matrix`, `bench_e2e`, `stability`, `job_fault_evidence`
- `schemas/` — versioned JSON contracts for receipts / manifest / host / catalog
- `catalog/` — pinned upstream snapshot + generated model catalog
- `configs/` — gfx1151 profile, numeric thresholds (fixed before runs), provider policy
- `tests/` — unit / contracts / adversarial (anti-forgery) / hardware
- `reports/<gfx>/<run_id>/` — receipts, profiles, manifests, matrices

## License

MIT for project-authored harness code. Ultralytics weights/code, ONNX Runtime,
and Windows App SDK keep their own licenses — this repo never commits weights.
