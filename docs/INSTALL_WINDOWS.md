# INSTALL_WINDOWS — three-venv layout

## Prerequisites (no admin changes required)

- Windows 11 24H2+ (build 26100+). Windows ML vendor EP dynamic install needs it.
- Windows App SDK runtime already installed by the OS/OEM is fine — we never
  install system runtimes ourselves. If missing: `BLOCKED_ADMIN_DEPENDENCY`.
- Python 3.11+ x64 (python.org/winget; NOT Microsoft Store). A full CPython
  (e.g. conda's) also works; document your interpreter in receipts.
- AMD driver per Microsoft's AMDGPU EP page (Adrenalin 24.12.1+; we record the
  WDDM driver version/date from dxdiag and never upgrade drivers ourselves).

## Venvs

| venv | purpose | install |
|---|---|---|
| `.venv-winml` | inference, CPU/AMDGPU/DML sessions | `pip install -e .[winml]` (pulls wasdk 2.3.0 + onnxruntime-windowsml 1.25.2) |
| `.venv-export` | Ultralytics PT + ONNX export + PT oracle | `pip install -e .[export]` |
| `.venv-cpu` (CI) | contracts/adversarial tests | `pip install -e .[dev]` |

Never install `onnxruntime`, `onnxruntime-directml`, and `onnxruntime-windowsml`
into the SAME venv — the wheels collide. DML attestation uses the DML provider
inside `onnxruntime-windowsml`; if a case needs `onnxruntime-directml`, use a
fourth venv `.venv-dml`.

If Ultralytics' `model.export()` auto-installs `onnxruntime` into the wrong
venv, prevent it: pre-install `onnx` + `onnxslim` in `.venv-export` and set
`YOLO_AUTOINSTALL=false`.

## Model assets

Weights are downloaded by YOU (license acceptance is yours) into `models/pt/`
(git-ignored). The harness never auto-downloads unless `--allow-asset-download`
is explicitly passed on a trusted runner.

## Verify install

```powershell
.venv-winml\Scripts\python -m runner.preflight --gfx gfx1151
```

Expected artifacts: `reports/gfx1151/<run_id>/{HOST_FINGERPRINT,EP_INSTALL_AND_PROBE,DEVICE_IDENTITY,GPU_CANARY}.json`.
