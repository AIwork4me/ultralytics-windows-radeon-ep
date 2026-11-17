# QUICKSTART_8060S — one image, one model, both EPs

```powershell
# after INSTALL_WINDOWS.md
.venv-winml\Scripts\python -m runner.preflight --gfx gfx1151

# detect on an image (Tier A), both EPs, with PT oracle + benchmark:
.venv-winml\Scripts\python -m runner.run_matrix --tier core --ep both `
  --model yolo26n-detect --image C:\path\to\bus.jpg `
  --winml-python .venv-winml\Scripts\python `
  --export-python .venv-export\Scripts\python --pt-oracle --benchmark

# segmentation only:
.venv-winml\Scripts\python -m runner.run_matrix --tier core --ep amdgpu `
  --model yolo26n-seg --image C:\path\to\bus.jpg ...

# explicit DirectML compatibility path (independent of AMDGPU result):
.venv-winml\Scripts\python -m runner.run_matrix --tier core --ep dml `
  --model yolo26n-detect ...

# verify + matrix:
.venv-winml\Scripts\python -m runner.verify_receipts --run-dir reports\gfx1151\<run_id>
.venv-winml\Scripts\python -m runner.build_compatibility_matrix --run-dir reports\gfx1151\<run_id> --catalog catalog\model_catalog.json
```

Every case produces `receipts/receipt_<case>.json`; read the AMDGPU/DML status
fields — they are derived from profiler evidence, not hand labels.
