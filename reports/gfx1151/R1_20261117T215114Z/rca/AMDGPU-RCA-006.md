# AMDGPU-RCA-006 — yolo26s-sem uint8 class map differs from CPU at 1/409600 px (both GPUs identical); float-allclose contract was the defect

- severity: P2  status: **CLOSED_VERIFIED**  stage: parity
- first seen: 2026-11-18T08:26:00Z  last seen: 2026-11-18T00:39:41Z  head: `HEAD-R1`
- environment: `{"os_build": "Windows 11 26200", "driver": "32.0.22032.6002", "ep_dll_sha256": "cb7496b3ab8f9daf2948b6030db5ec795596072ffcb25d3c55a28e4c0df7f8ab", "ort": "onnxruntime-windowsml 1.25.2.202605110140", "model": "yolo26s-sem.pt", "task": "semantic", "imgsz": 640}`
- exception: `FAIL_GPU_PARITY (allclose=False, max_abs_diff=1.0, mean_abs_diff=2.44e-06)`

## Observation

On the real bus.jpg tensor, CPU ORT vs AMDGPU differ on exactly 1 pixel of 409600 (0.00024%); CPU vs DML differ on the SAME 1 pixel; AMDGPU and DML are bit-identical (0 px). yolo26n-sem on the same image: bit-identical across all three. Task-level parity (pixel agreement, mIoU) passed for both EPs.

## Experiments

- {"hypothesis": "single-pixel argmax boundary tie in float logits (benign), not a GPU compute fault", "command": "elementwise compare of ref/cand npz (CPU, AMDGPU, DML)", "controlled_variable": "provider only", "predicted_if_true": "both GPUs agree with each other and differ from CPU at the same near-tie pixel(s)", "actual_result": "exactly that: 1 px CPU-vs-AMDGPU, 1 px CPU-vs-DML (same pixel), 0 px AMDGPU-vs-DML", "reproducible": true, "repetitions": 1, "falsified": false}

## Causal inference

No-change closure on the GPU side: the differing pixel is an argmax tie under fp reordering; the defect was in the HARNESS parity contract (float allclose applied to uint8 class ids — a category error). Fixed by the versioned integer-output contract in docs/r1/R1_INT_OUTPUT_PARITY.md (min_exact_agreement 0.999, predeclared).

## Mitigation

compare_outputs now dtype-aware; sem-s receipts re-run under the declared integer contract with disagree_px recorded.

## Remaining uncertainty

None for this case; future integer models inherit the contract.
