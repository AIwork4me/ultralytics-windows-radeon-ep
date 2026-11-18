# PR #5 independent reproduction — replaces the frozen historical MIN_REPRODUCE.txt

The legacy MIN_REPRODUCE.txt in the immutable GFX1201_DIAG_20261118T2359Z
bundle references PR #6's unified_predict command and an earlier fixed-run-id
freezer. Keep that historical file unchanged; for PR #5 standalone use this
current CPU + GPU test sequence.

Precondition: physical RX 9070 XT must be verified through PnP + DXGI before
claiming ANY gfx1201 GPU PASS; a gfx1100 host must BLOCK as expected.

On a Windows host with a correctly configured WinML environment:

    .venv-winml\Scripts\python -m runner.preflight --gfx gfx1201 --gpu-needle "9070"
    .venv-winml\Scripts\python runner/unified_canary.py --ep amdgpu --gfx gfx1201 --out results\CANARY_AMDGPU.json
    .venv-winml\Scripts\python runner/unified_canary.py --ep dml --gfx gfx1201 --out results\CANARY_DML.json
    .venv-winml\Scripts\python -m runner.run_matrix --tier core --ep both --benchmark --pt-oracle

Use a **new, previously unused** UTC run ID for each evidence freeze:

    .venv-winml\Scripts\python -m runner.freeze_gfx1201_evidence --run-id GFX1201_DIAG_20261119T0700Z --canary-run-dir results

The freezer preserves the old run and refuses an existing nonempty directory.
The initial canary command creates local ONNX and profile files; do not commit
weights, ONNX, cache binaries, raw tensors, full user paths, PnP instance IDs or
other unredacted machine identifiers. Manual redaction and manifest verification
are required before GitHub publication. Real gfx1201 model and 900s soak PASS
are a separate hardware validation gate: absent physical 9070 XT => NOT_RUN.
