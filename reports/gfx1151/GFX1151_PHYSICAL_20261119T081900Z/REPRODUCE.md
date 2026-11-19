# Reproduce — GFX1151_PHYSICAL_20261119T081900Z

Executed on a fresh Windows 11 25H2 host (build 26200.9974), AMD Ryzen AI MAX+
PRO 395 / Radeon 8060S (PCI 1002:1586, gfx1151), driver 32.0.22018.5,
Python 3.12.10, onnxruntime-windowsml 1.25.2, Ultralytics 8.4.215.

> **AMDGPU EP status on this host: BLOCKED_EP.** No `amdgpu-ep.dll` exists in
> site-packages, `%LOCALAPPDATA%\Microsoft\WindowsApps`, or
> `C:\Program Files\WindowsApps`; the Windows ML `ExecutionProviderCatalog`
> returns zero providers and `EnsureAndRegisterCertifiedAsync()` returns an empty
> list. Installing a system-wide EP / Windows ML 2.x package is outside this
> mission's no-system-change boundary, so AMDGPU cases are recorded as
> `BLOCKED_EP`, never as CPU/GPU passes. DML is validated independently.

## 1. Environment (no admin)

`powershell
winget install --id Python.Python.3.12 --scope user --silent
git clone https://github.com/AIwork4me/ultralytics-windows-radeon-ep.git
cd ultralytics-windows-radeon-ep
git rev-parse HEAD                    # d24f7377813cddcab744c14c82733e890b881234
python -m venv .venv-winml
python -m venv .venv-export
python -m venv .venv-cpu
.\.venv-winml\Scripts\python -m pip install -e '.[winml]' pytest 'onnx>=1.16' psutil
.\.venv-export\Scripts\python -m pip install -e '.[export]' onnxslim 'ultralytics==8.4.215'
.\.venv-cpu\Scripts\python -m pip install -e '.[dev]'
`

`onnx` is required in the winml venv by the canary; it is not in the `winml`
extra (audit finding, fixed in docs).

## 2. Weights (manual asset gate; never committed)

`powershell
$env:YOLO_AUTOINSTALL='false'
$env:YOLO_CONFIG_DIR="$PWD\Ultralytics"
cd models/pt
python -c "from ultralytics import YOLO; [YOLO(n) for n in ['yolo26n.pt','yolo26n-seg.pt','yolo26n-sem.pt','yolo26n-depth.pt','yolo26n-cls.pt','yolo26n-pose.pt','yolo26n-obb.pt','yolo11n.pt','yolov8n.pt']]"
`

## 3. Preflight (separate run_id)

`powershell
$PREFLIGHT_ID = "GFX1151_PREFLIGHT_20261118T234053Z"
.\.venv-winml\Scripts\python -m runner.preflight --gfx gfx1151 --gpu-needle "8060S" --run-id $PREFLIGHT_ID
# observed: canary PASS_DML_ONLY, verdict CONDITIONAL_BLOCKED_AMDGPU, exit 2
`

## 4. ONNX export

`powershell
.\.venv-export\Scripts\python -m runner.export_models --export-python .\.venv-export\Scripts\python.exe
# -> models/onnx/<stem>.raw_b1_fp32_static.onnx (9 files)
`

## 5. Tier A matrix (9 checkpoints x 2 EPs)

`powershell
$RUN = "GFX1151_PHYSICAL_20261119T081900Z"
.\.venv-winml\Scripts\python -m runner.run_matrix --host gfx1151 --tier core --ep both `
  --run-id $RUN --winml-python .\.venv-winml\Scripts\python.exe `
  --export-python .\.venv-export\Scripts\python.exe --image data/fixtures/bus.jpg `
  --pt-oracle --benchmark --max-minutes-per-case 20
# observed exit 2: 9 DML PASS_GPU_EXECUTED_FULL + 9 AMDGPU BLOCKED_EP (fail-closed)
`

`run_matrix` is now a v2 coordinator over `runner.r1_pipeline` (real-image input
bundle, CPU ORT by-name parity, PT task parity, immutable v2 receipts). A blocked
or failed case makes the process exit non-zero; exit 0 requires every requested
case to be PASS_GPU_*.

## 6. Repeated benchmark (3 fresh sessions per model)

`powershell
$NPY = (Get-ChildItem "$RUN/inputs/input_*.npy" | Select-Object -First 1).FullName
.\.venv-winml\Scripts\python -m runner.bench_repeats --onnx models/onnx/yolo26n.raw_b1_fp32_static.onnx --input-npy $NPY --ep dml --reps 3 --out "$RUN/benchmark/BENCH_DML_yolo26n.json"
# repeated for yolo26n-seg and yolo26n-pose
`

## 7. Soak (15 min per model, 30 s heartbeat, 180 s watchdog)

`powershell
.\.venv-winml\Scripts\python -m runner.soak --gfx gfx1151 --ep dml `
  --run-id GFX1151_SOAK_DETECT_20261119T075000Z --tag yolo26n-detect --minutes 15 `
  --onnx models/onnx/yolo26n.raw_b1_fp32_static.onnx --input-npy $NPY
`

## 8. Negative injection

`powershell
.\.venv-winml\Scripts\python -m runner.negative_injection --gfx gfx1151 --run-id GFX1151_NEG_INJECT_20261119
`

## 9. Evidence freeze

`powershell
.\.venv-winml\Scripts\python -m runner.finalize_run --run-dir "reports/gfx1151/$RUN"
.\.venv-winml\Scripts\python -m runner.build_compatibility_matrix --run-dir "reports/gfx1151/$RUN" --catalog catalog/model_catalog.json
.\.venv-winml\Scripts\python -m runner.analyze_profiles --run-dir "reports/gfx1151/$RUN"
.\.venv-winml\Scripts\python -m runner.redact_evidence --run-dir "reports/gfx1151/$RUN"
.\.venv-winml\Scripts\python -m runner.prepare_evidence --run-dir "reports/gfx1151/$RUN" --dest "%USERPROFILE%//Desktop/YOLO/local_evidence"
.\.venv-winml\Scripts\python -c "from radeon_ort.manifest import write_manifest; write_manifest(r'reports/gfx1151/$RUN','$RUN','independent-gfx1151-physical-validation')"
.\.venv-winml\Scripts\python -m runner.verify_receipts --run-dir "reports/gfx1151/$RUN"
`
