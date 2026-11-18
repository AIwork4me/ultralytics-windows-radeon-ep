# R1 reproduce — exact commands (gfx1151 / Radeon 8060S host)

Repo: `AIwork4me/ultralytics-windows-radeon-ep`, branch
`mission/gfx1151-r1-amdgpu-first-task-parity` (stacked on PR #2 head `563c24e`).
Run root: `reports/gfx1151/R1_20261117T215114Z/`.

Venvs (three-venv layout, see `docs/INSTALL_WINDOWS.md`; versions in
`R1_PROVENANCE.md`): `.venv-winml` (sessions), `.venv-export` (Ultralytics
8.4.215 pinned + cv2), `.venv-cpu` (CI tests).

## One-time

```powershell
git switch mission/gfx1151-r1-amdgpu-first-task-parity
# tests (CPU; hardware tests need this host):
.venv-winml\Scripts\python -m pytest tests -q
```

## Full pipeline (per model/task; AMDGPU runs BEFORE DML inside each case)

```powershell
$env:PYTHONPATH='src'
.venv-winml\Scripts\python -m runner.r1_pipeline `
  --run-dir reports\gfx1151\R1_20261117T215114Z `
  --models yolo26n.pt,yolo26n-seg.pt,yolo26n-sem.pt,yolo26n-depth.pt,yolo26n-cls.pt,yolo26n-pose.pt,yolo26n-obb.pt `
  --eps amdgpu,dml --with-negative --benchmark
# representative slice:
#   --models yolo26s.pt,yolo26s-seg.pt,yolo26s-sem.pt,yolo26s-depth.pt,yolo26s-cls.pt,yolo26s-pose.pt,yolo26s-obb.pt,yolo11n.pt,yolov8n.pt
```

Per case the pipeline: builds/verifies the deterministic input bundle
(export venv) → CPU ORT reference → AMDGPU worker (attested ep-device
selection) → DML worker → PT oracle on the SAME tensor bytes → vendor-NMS
postprocess → task-parity comparator → strict v2 receipt (immutable).

## Verification

```powershell
$env:PYTHONPATH='src'
.venv-winml\Scripts\python -m runner.verify_receipts --run-dir reports\gfx1151\R1_20261117T215114Z
.venv-winml\Scripts\python -m runner.verify_r1_inputs --run-dir reports\gfx1151\R1_20261117T215114Z
.venv-winml\Scripts\python -I tools\r1_seed_rca.py reports\gfx1151\R1_20261117T215114Z\rca   # idempotent
```

## Soak (inference phase; setup excluded)

```powershell
.venv-winml\Scripts\python -m runner.soak --ep amdgpu --minutes 15 `
  --input-npy reports\gfx1151\R1_20261117T215114Z\inputs\input_<id>.npy `
  --run-dir R1_20261117T215114Z --run-id SOAK_R1
# then --ep dml (independent; never substitutes AMDGPU)
```

## Notes

- `MSYS_NO_PATHCONV=1` needed when invoking from Git Bash (POSIX path mangling).
- Every rerun goes to a NEW run_id or a fresh receipt name; receipts are
  write-once (`save_receipt` refuses overwrite). Superseded defective receipts
  live in git history + `ERRATA.md`.
- Weights/onnx never committed (`models/` git-ignored); fixtures:
  `data/fixtures/bus.jpg` (ultralytics asset), `dota_p1053.jpg` (dota8 sample,
  license copied), `negative_gray.png` (deterministic synthetic).
