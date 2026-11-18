# AMDGPU-RCA-005 — MIGraphX gpu::precompile_op output-buffer-mismatch warnings during yolo26n-obb compile — no numeric/task impact (evidence-supported no-change closure)

- severity: P2  status: **CLOSED_VERIFIED**  stage: compile
- first seen: 2026-11-18T07:31:20Z  last seen: 2026-11-18T00:00:40Z  head: `ef2cf48`
- environment: `{"os_build": "Windows 11 26200", "driver": "32.0.22032.6002", "ep_dll_sha256": "cb7496b3ab8f9daf2948b6030db5ec795596072ffcb25d3c55a28e4c0df7f8ab", "ort": "onnxruntime-windowsml 1.25.2.202605110140"}`
- exception: `None`

## Observation

Warnings appear on every cold compile of the 1024px OBB graph (probe + rebuild + negative-control run) and not on 224/640/768 graphs.

## Experiments

- {"hypothesis": "warnings are informational; execution and parity unaffected", "command": "obb task parity with corrected vendor-NMS decode (both EPs)", "controlled_variable": "decoder correctness only (GPU path unchanged)", "predicted_if_true": "tight task parity despite warnings", "actual_result": "AMDGPU+DML: 15/15 matched, rotated_iou_mean=0.99999998, angle_absdiff=4.5e-6 deg; numeric parity allclose vs CPU ORT", "reproducible": true, "repetitions": 2, "falsified": false}

## Causal inference

No-change closure: with the harness decoder corrected, the warning-emitting compile produces task results indistinguishable from PT (IoU 0.99999998). The warnings mark optimizer fallback for specific precompiled ops, with no measurable numeric or semantic effect on this model.

## Mitigation

none required; watch item for future 1024px graphs

## Remaining uncertainty

Whether extreme-input cases could diverge; bounded by parity thresholds on every future run
