# GFX1151-R1 final report (EN summary — authoritative detail in R1_FINAL_CN.md)

Verdict: **GFX1151_R1_GO_FOR_OWNER_REVIEW** — all gates GO;
`AMDGPU_FIRST_GO` on AMDGPU's own evidence.

- Validator v2 (orthogonal verdicts, expected-output manifest, worker-rc gate,
  binding attestation, metric-vs-threshold gate, RCA linkage); 61 adversarial
  tests; vacuous-parity/forged-task holes closed; legacy G2/G3 receipts still
  verify (PT_APP = smoke-only interpretation).
- Deterministic inputs: canonical-SHA256 input_id; persisted .npy bundles; PT
  oracle consumes identical tensor bytes; cross-process/cross-EP SHA equality
  verified (16 cases / 48 fragments).
- Device binding: candidate[0] HIP 101 / candidate[1] executes (3/3 procs);
  probe + rebuilt-final revalidation; EP_DEVICE_SELECTED with DXGI
  corroboration kept separate; candidate→LUID honestly UNPROVEN.
- True task parity: **32/32 GPU cases = 16 models × {AMDGPU, DML} all
  PASS_GPU_EXECUTED_FULL + PASS_TASK** at predeclared thresholds (detect IoU
  0.999999; seg mask IoU 1.0; sem pixel/mIoU 1.0; depth rel 3e-7; cls drift
  1.1e-6; pose OKS 1.0; obb rotated IoU 0.99999998 / angle 4.5e-6°);
  4/4 discriminating negative controls.
- RCA register: 6 issues, none suppressed (2 CLOSED_VERIFIED, 1 ISOLATED
  external, 3 watch/mitigated). Integer-output parity contract added via
  versioned design note after RCA-006 (1 px / 409600 argmax tie).
- Perf: AMDGPU cold session-create 71–201 s/model (RCA-002 unpersistable
  MIGraphX cache), warm p50 1.17–16.31 ms; 15-min soaks 0 errors both EPs
  (AMDGPU 155,007 runs; DML 269,534 — comparator only).
- Git: branch `mission/gfx1151-r1-amdgpu-first-task-parity` @
  `8ce34c5162de78bf3ea373e5aba384fe9d7f0d36` = remote = Draft stacked
  PR #3 head (base = PR #2 branch, untouched Draft); CI windows-cpu-ci pass
  at exact head (run 44219621167). CPU CI does not attest GPU.
- Evidence root `reports/gfx1151/R1_20261117T215114Z/`: 32 receipts, 749-entry
  manifest, redaction clean, ERRATA preserves every superseded artifact in git
  history. Catalog: 16 EVIDENCED / 26 BASELINE_REQUIRED NOT_RUN (unchanged,
  separate workstream).
