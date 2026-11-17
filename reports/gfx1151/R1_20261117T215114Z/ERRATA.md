# R1 errata

1. 2026-11-18: smoke receipt `receipt_yolo26n__detect__amdgpu.json` (pre-matrix
   smoke) was generated with a comparator threshold-wiring defect
   (task key 'detect' vs yaml key 'detection' -> empty thresholds_declared).
   The strict validator caught it ("PASS_TASK without declared thresholds") and
   the record was preserved in git history at commit preceding this errata,
   then removed from the run dir so the official matrix starts from a clean,
   consistent denominator. Not counted in any reported denominator. The case
   was re-run fresh by the official matrix with the fixed harness.
