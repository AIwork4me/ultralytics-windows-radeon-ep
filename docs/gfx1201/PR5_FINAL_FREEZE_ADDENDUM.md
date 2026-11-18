# PR #5: Final freeze review addendum

This amendment is a forward-only **CPU / validation-script** correction.
The already published gfx1100 canary and gfx1201 NOT_RUN receipts remain
historical evidence of the previous code HEAD and were not regenerated.

- Removes imports of `radeon_ort.unified_identity` (introduced only by
  stacked PR #6) from PR #5 tools: PR #5 must be independently runnable.
- `runner.unified_canary` now requires exact per-gfx DID, valid physical
  LUID and AMDGPU OrtEpDevice LUID match before claiming bound AMDGPU GPU PASS;
  uses by-name, element-wise CPU parity. DML records ordinal-only confidence
  without implying a verified EP-to-DXGI LUID binding.
- `freeze_gfx1201_evidence` now refuses a reused output run directory,
  requires a new run_id, produces actual CSV instead of a JSON string,
  and strips PnP instance IDs from any *new* published host fingerprint.
- No real RX 9070 XT hardware was available; `gfx1201` seven-task and soak
  conclusions must remain NOT_RUN. This amendment does NOT authorize a
  gfx1201 GPU PASS or retest the historical physical gfx1100 evidence.
- The historical published host fingerprint contains detailed PnP instance
  paths. This is a **privacy hygiene gap in the original commit history**;
  new runs redact it by default. No force-push or Git-history rewrite.
- The PR #5 diagnostics do not prove a production failover engine; that
  responsibility stays exclusively in Draft PR #6.

Run `pytest tests/unit/test_device_identity_gfx1201.py
tests/unit/test_gfx1201_pr5_closure.py` and full Windows CPU CI.
