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
| gfx1151 — Radeon 8060S | validating (Tier A in progress) | validating | `reports/gfx1151/` |
| gfx1100 — RX 7900 XTX | NOT_RUN | NOT_RUN | future host |
| gfx1201 — RX 9070 XT | NOT_RUN | NOT_RUN | future host (see gfx1201 issue) |

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
