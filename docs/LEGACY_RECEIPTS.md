# Legacy receipts: verify-only, never resumed

PR #8's receipts were produced before the run-contract existed. They carry real
evidence (session profile counts, CPU parity, task parity, manifest), and they
remain valid historical evidence. They do **not** carry a `contract` block, so
they cannot prove the exact code/model/input/threshold/device quartet that the
current contract-aware resume requires.

## Policy

- `verify_receipts` still validates legacy receipts with `validate_receipt_strict`
  and the recorded `thresholds_snapshot.json`. On 2026-11-19 the PR #8 run
  `GFX1151_PHYSICAL_20261119T081900Z` verified as `ALL_OK` (18 receipts,
  manifest 233 entries).
- `runner.run_matrix --resume` and `runner.r1_pipeline --resume` **refuse** a
  legacy run directory with `ContractError: ... legacy run directories cannot be
  resumed contract-aware`. This is intentional: adopting a contract after the
  fact from the same receipt would be circular (the receipt cannot independently
  prove the code that produced it).
- A new acceptance run must use a new `run_id`. Legacy receipts stay in git and
  in the evidence index; they are not edited, deleted, or re-blessed.

## Migration

1. Keep old run directories untouched.
2. Create a new run with `runner.run_matrix` (or `r1_pipeline`) on a clean commit.
3. The new `RUN_CONTRACT.json` and per-receipt `contract` blocks bind code,
   model, input, EP, thresholds and device identity.
4. Compare old and new only as historical vs current evidence; never copy a
   legacy receipt into the new run.

This is a deliberate fail-closed trade-off: convenience of resuming old evidence
is sacrificed to keep attestation non-circular.
