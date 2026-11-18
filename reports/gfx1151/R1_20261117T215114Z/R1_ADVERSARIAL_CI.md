# R1 adversarial & CI summary

## Local test suites (final HEAD, .venv-winml)

- `tests/adversarial/test_no_false_pass.py` — 25 v1-era forgery patterns
- `tests/adversarial/test_no_false_pass_v2.py` — 35 v2 patterns (mission R1-A
  list: vacuous parity, rc!=0 fragment, alias masquerade, binding unproven,
  probe-zero, PT_APP as PASS_TASK, fake thresholds, backend claims without
  evidence, RCA-linkage requirement, DML identity regression, metric-vs-
  declared-threshold gate, expected-empty without negative control, ...)
- `tests/unit/test_input_contract.py` — 7 determinism tests incl. cross-process
  PYTHONHASHSEED 0 vs random input_id/tensor-sha equality
- unit/contracts suites (schemas v1+v2, redaction, manifest, task metrics)
- Final: **114 passed, 1 skipped** (hardware suite separately below)

## Hardware tests (physical 8060S)

`tests/hardware/test_gpu_canary.py` — AMDGPU canary (self-registered EP,
attested selection), DML canary, AMDGPU-vs-CPU canary parity: **3 passed**
after the R1 fix (tests now register the EP library themselves — the original
defect of assuming prior registration is closed).

## CPU CI caveat (unchanged policy)

`CPU_CI_DOES_NOT_ATTEST_GPU` — GitHub Actions validates contracts, schemas,
adversarial anti-forgery, determinism only. All GPU/hardware claims in this run
come from this physical host's receipts, never from CI.

## Honest failure preservation

Every harness defect found during R1 is preserved: first-attempt receipts with
defects are committed at their commits (see ERRATA.md items 1–5), removed only
with an errata entry, and re-run fresh. The validator itself caught three of
the defects at save time (NaN refusal; PASS_TASK-without-declared-thresholds;
score_drift-vs-max gate) — evidence the fail-closed gates work as designed.
