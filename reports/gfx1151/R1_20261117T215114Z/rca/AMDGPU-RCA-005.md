# AMDGPU-RCA-005 — MIGraphX emits gpu::precompile_op output-buffer-mismatch warnings during yolo26n-obb (1024) compile

- severity: P2  status: **REPRODUCED**  stage: compile
- first seen: 2026-11-18T07:31:20Z  last seen: 2026-11-17T23:45:02Z  head: `ce9f577`
- environment: `{"os_build": "Windows 11 26200", "driver": "32.0.22032.6002", "ep_dll_sha256": "cb7496b3ab8f9daf2948b6030db5ec795596072ffcb25d3c55a28e4c0df7f8ab", "ort": "onnxruntime-windowsml 1.25.2.202605110140"}`
- exception: `None`

## Observation

During cold compile of yolo26n-obb (1024x1024) the EP logs ~24 WARN lines of this signature (probe + rebuild), listing conv/pool/pointwise/reshape ops. Not observed on the six 640/768/224 models. Execution completes; numeric parity vs CPU ORT allclose=True for output0.

## Hypotheses

- Optimizer falls back to a non-precompiled variant for these ops (warning is informational; correctness path unaffected)
- Precompiled kernels produce different buffer layouts (potential silent numeric divergence path)

## Next discriminating experiment

Compare yolo26n-obb raw output0 tensors CPU vs AMDGPU element-wise beyond allclose (max abs diff at 5e-3 threshold currently); rerun obb task parity with fixed rotated-IoU comparator and record metric deltas; if task parity passes with tight margins, downgrade to informational.

## Confounders considered

- 1024 input size only
- first-compile state
- GameViewer
