# R1 gap register — what remains open after this mission

## Unresolved AMDGPU items (all in rca/AMDGPU_RCA_REGISTER.md)

| ID | Status | Impact |
|---|---|---|
| AMDGPU-RCA-001 | REPRODUCED (P2) | 'Init provider bridge failed.' at env creation; no functional effect found across 30+ GPU runs; discriminating experiment defined (verbose ORT log capture) |
| AMDGPU-RCA-002 | MITIGATED (P2) | problem_cache.json cannot persist (WindowsApps ACL) → cold compile per process, 35–94 s/model (n-size); correctness unaffected |
| AMDGPU-RCA-003 | ISOLATED (P1) | ep-device candidate[0] HIP 101; external (vendor DLL); fail-closed candidate loop + attestation mitigate |
| AMDGPU-RCA-004 | MITIGATED (P1) | candidate→LUID mapping unproven (EP exposes no LUID); receipts record EP_DEVICE_SELECTED + separate DXGI corroboration |
| AMDGPU-RCA-005 | CLOSED_VERIFIED (P2) | precompile_op warnings on obb compile; no numeric/task impact (IoU 0.99999998) |
| AMDGPU-RCA-006 | CLOSED_VERIFIED (P2) | 1-px argmax tie on yolo26s-sem; integer-parity contract fixed |

## Scope gaps (honest denominators)

- Rerun matrix this mission: **32 GPU cases = 16 models × 2 EPs** (7 yolo26n
  tasks, 7 yolo26s tasks, yolo11n, yolov8n) + 8 GPU negative-control runs
  (detect/segment/pose/obb × AMDGPU+DML, n-slice). Numeric stress (random
  tensor) class NOT re-run in R1 — covered by frozen G2/G3 evidence and the
  deterministic-stress unit tests; labeled separately.
- 26 catalog BASELINE_REQUIRED entries remain NOT_RUN (unchanged; separate
  workstream — resume via `runner.r1_pipeline --models <list>` and
  `runner.run_matrix` for the long tail).
- Negative controls were run for the four entity tasks on the n-slice; the
  s-slice and detect-only models reused the same control semantics (documented,
  not silently assumed).
- OBB positive fixture is a single dota8 image (15 rotated entities); broader
  OBB coverage needs a licensed aerial set.
- classify negative-control semantics: agreement-only (a blank image still
  yields a top-1); no "empty" concept exists for cls/sem/depth — documented,
  not forced.

## Watch items

- sem-family integer maps now carry `disagree_px` in receipts; any future model
  exceeding 0.1% disagreement fails parity and needs a fresh RCA.
- ORT 1.25.2 windowsml distribution name in fragments reads "onnxruntime" for
  the CPU worker (cosmetic; session EP names are the authoritative fields).
