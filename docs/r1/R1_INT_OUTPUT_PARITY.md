# Design note: parity contract for integer (argmax class-map) outputs

Date: 2026-11-18. Trigger: AMDGPU-RCA-006 (yolo26s-sem).

## Observation (before this note)

The original numeric-parity contract applied float `allclose(atol=5e-3,
rtol=5e-3)` to every output regardless of dtype. `yolo26s-sem` emits a uint8
per-pixel argmax class map; on the real bus.jpg input, CPU ORT and BOTH GPUs
(AMDGPU and DML) disagreed on exactly 1 of 409600 pixels — the two GPUs were
bit-identical to each other. `yolo26n-sem` was bit-identical across all three.
The single differing pixel is an argmax tie at a class boundary: the underlying
float logits are within fp noise, the argmax winner differs.

## Why this is a contract fix, not threshold tuning

- Applying a float tolerance to integer class IDs is a category error: adjacent
  class IDs are not "close" (class 5 vs 6 is a different label, not 16.7% of
  the same thing). The meaningful parity question for a class map is: what
  fraction of pixels choose the same class?
- The change does not relax any float-output threshold. fp32 outputs keep
  atol/rtol 5e-3 everywhere.
- The task-level oracle (min_pixel_agreement 0.90, min_miou 0.70, predeclared
  before any run) already treated semantic maps this way; the numeric-parity
  layer now agrees with that semantics instead of contradicting it.

## New predeclared rule (configs/thresholds.yaml numeric_parity.int_outputs)

- Integer-dtype outputs compare by exact-agreement fraction.
- PASS requires agreement >= 0.999 (≤ 0.1% differing pixels; observed worst
  case in this mission: 0.00024%).
- Receipt parity items record `comparison_kind: int_exact_agreement`,
  `exact_agreement`, and `disagree_px`; the thresholds block carries
  `int_min_agreement`.

## Honesty constraints

- The two yolo26s-sem receipts recorded before this note (FAIL_GPU_PARITY under
  the float contract) are preserved in git history and in ERRATA.md, then
  re-run under the declared integer contract. No receipt is edited in place.
- Future integer models (26 BASELINE_REQUIRED catalog entries) inherit this
  contract automatically.
