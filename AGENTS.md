# AGENTS.md — operating rules for AI agents in this repository

1. **Fail-closed evidence.** A GPU PASS requires: verified device identity,
   positive target-EP profiler node count, by-name numeric parity vs CPU ORT,
   task oracle. `get_providers()` is never proof. The strict receipt validator
   recomputes statuses; forged labels fail CI.
2. **Never fabricate.** No copied/recycled receipts, no gfx1100/gfx1201 claims
   without that hardware, no screenshots as sole proof. Failures are preserved
   (failure→repair→rerun evidence stays in git history).
3. **Immutable receipts.** New `run_id` for reruns; never edit or delete a
   receipt to make a report greener.
4. **No weight commits.** No .pt/.onnx, secrets, tokens, absolute user paths,
   telemetry identifiers. Run the redaction scan before pushing evidence.
5. **One GPU process at a time**, bounded timeouts, resumable shards. Never
   launch unbounded full-catalog runs.
6. **No system changes**: drivers, display adapters, WindowsApps, firmware —
   never. Missing system prerequisites are recorded `BLOCKED_ADMIN_DEPENDENCY`.
7. **No upstream PRs** to ultralytics/microsoft/onnxruntime from this repo's
   automation; own-repo branches/PRs only; no force-push; no history rewrite.
8. **CPU CI cannot attest GPU.** Keep `CPU_CI_DOES_NOT_ATTEST_GPU` semantics;
   hardware evidence flows only through reviewed branches/manual dispatch.
9. **Language**: user-facing final reports in Chinese (verdict labels/statuses
   stay English); README bilingual.
