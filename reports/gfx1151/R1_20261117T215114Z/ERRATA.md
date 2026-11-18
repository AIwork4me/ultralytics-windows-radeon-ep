# R1 errata

1. 2026-11-18: smoke receipt `receipt_yolo26n__detect__amdgpu.json` (pre-matrix
   smoke) was generated with a comparator threshold-wiring defect
   (task key 'detect' vs yaml key 'detection' -> empty thresholds_declared).
   The strict validator caught it ("PASS_TASK without declared thresholds") and
   the record was preserved in git history at commit preceding this errata,
   then removed from the run dir so the official matrix starts from a clean,
   consistent denominator. Not counted in any reported denominator. The case
   was re-run fresh by the official matrix with the fixed harness.

2. 2026-11-18: all six DML receipts of the first matrix pass derived
   BLOCKED_IDENTITY due to a validator defect (identity gate required
   probe.amdgpu_nodes>0 for every EP). Preserved in git history at the commit
   preceding this note, then deleted and re-run with the fixed validator
   (per-EP binding evidence). DML numeric/task evidence itself was unaffected
   (parity True in the fragments).

3. 2026-11-18: yolo26n-obb receipts of the first pass FAILED TO SAVE
   (fail-closed JSON NaN refusal) and were never written; the obb comparator
   paired rotated boxes with an axis-aligned matcher (0 matches) and emitted
   NaN means. Comparator fixed (rotated-IoU pairing + non-finite->null);
   obb re-run fresh. Fragments of the failed first pass remain in raw/ as
   honest failure evidence.

4. 2026-11-18: obb/cls/seg receipts of the earlier passes carried decoder
   defects (obb: vendor rotated-NMS column order — conf read as angle; cls:
   double softmax vs in-graph-normalized export; seg: 4D protos into 3D
   vendor process_mask). All three were harness decode bugs, NOT GPU faults —
   numeric parity GPU-vs-CPU was allclose in every case. Defective receipts
   preserved in git history at commit preceding this note, deleted, and the
   cases re-run with the fixed decoders. obb after fix: 15/15 matched,
   rotated IoU 0.99999998, angle diff 4.5e-6 deg (both EPs).

5. 2026-11-18: yolo26s-sem receipts of the representative pass recorded
   FAIL_GPU_PARITY because float allclose was applied to a uint8 class map
   (1/409600 px argmax tie; both GPUs identical; see AMDGPU-RCA-006 and
   docs/r1/R1_INT_OUTPUT_PARITY.md). Preserved in git history, then re-run
   under the declared integer-output contract (exact-agreement >= 0.999).
