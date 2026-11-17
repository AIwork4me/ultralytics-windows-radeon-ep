# CORRECTNESS — oracles and thresholds

## Oracle 1: CPU ORT numeric parity (same ONNX, same feeds)

- By-output-NAME comparison (no positional swaps), dtype/shape recorded.
- Metrics: max_abs, mean_abs, max_rel, p99_abs, finite, allclose.
- Thresholds fixed in `configs/thresholds.yaml` BEFORE any GPU output is seen
  (fp32: 5e-3; canary: 2e-4; int8 vs int8-CPU only, quality vs fp32 separately).

## Oracle 2: PT application semantics

Same image, same letterbox/thresholds; task-level metrics:

| task | metric | gate |
|---|---|---|
| detect | IoU-matched precision/recall | ≥0.90/≥0.90, ≥1 match |
| segment | mask IoU vs PT | ≥0.70 |
| semantic | mIoU | ≥0.70 |
| depth | MAE/RMSE with valid mask | rel MAE ≤0.15 vs PT |
| classify | Top1 match (Top5 recorded) | required |
| pose | OKS | ≥0.80 |
| obb | rotated IoU | ≥0.70 |

Output contracts differ (`nms=None/True/False`): the task adapter reads the
model's actual output contract from the catalog/export record.

## Receipt statuses

Multi-dimensional (MODEL_ASSET / EXPORT / CPU_ORACLE / AMDGPU / DML /
TASK_ORACLE / BENCHMARK / HOST) — never one ambiguous PASS. Derived statuses are
recomputed by the strict validator; forged labels fail CI.
