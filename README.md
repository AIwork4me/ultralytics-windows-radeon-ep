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

See [docs/INSTALL_WINDOWS.md](docs/INSTALL_WINDOWS.md) for the three-venv layout
(winml / export / cpu), [docs/DEVICE_IDENTITY.md](docs/DEVICE_IDENTITY.md) for how
the 8060S is selected (integrated GPU: no `Discrete=True` requirement), and
[docs/PROVIDER_FALLBACK.md](docs/PROVIDER_FALLBACK.md) for the strict no-silent-
CPU policy.

## Repository layout

- `src/radeon_ort/` — harness modules (device identity, EP catalog, sessions, receipts, oracles, benchmark, manifest, redaction)
- `runner/` — CLI: `preflight`, `generate_catalog`, `export_models`, `smoke`, `run_matrix` (v2), `r1_pipeline`, `resume_matrix`, `bench_repeats`, `soak`, `negative_injection`, `analyze_profiles`, `finalize_run`, `build_compatibility_matrix`, `redact_evidence`, `prepare_evidence`, `verify_receipts`
- `schemas/` — versioned JSON contracts for receipts / manifest / host / catalog
- `catalog/` — pinned upstream snapshot + generated model catalog
- `configs/` — gfx1151 profile, numeric thresholds (fixed before runs), provider policy
- `tests/` — unit / contracts / adversarial (anti-forgery) / hardware
- `reports/<gfx>/<run_id>/` — receipts, profiles, manifests, matrices

## License

MIT for project-authored harness code. Ultralytics weights/code, ONNX Runtime,
and Windows App SDK keep their own licenses — this repo never commits weights.
