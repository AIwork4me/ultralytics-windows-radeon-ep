## Summary

<!-- what + why; link issues -->

## Evidence discipline checklist

- [ ] No GPU PASS claimed without: device identity + target-EP profile node count + CPU parity + task oracle
- [ ] Receipts are immutable, new run_id for reruns; failures preserved (never deleted for green)
- [ ] gfx1100 / gfx1201 columns remain NOT_RUN unless this PR adds real hardware receipts
- [ ] No weights / secrets / absolute user paths / telemetry identifiers committed
- [ ] `python -m runner.verify_receipts --run-dir <dir>` = ALL_OK (paste output)
- [ ] CPU CI green at this exact HEAD; `CPU_CI_DOES_NOT_ATTEST_GPU` acknowledged

## Test plan

<!-- commands you actually ran -->
