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

## Owner-authorized provisioning exception (scope-limited)

Rule 6 (no system changes) remains the default for every CI runner, shared
host, forked PR and third-party machine. Exactly one **named physical
validation host** may carry a written, narrow exception, and only when the
repository owner records all of the following in a mission document:

1. the host identity (stable `host_id` plus the gfx DID) that the exception
   applies to;
2. the exact component, version and source (for example
   `pip install windowsml==2.5.96` from PyPI, or an official signed MSIX with
   its publisher, package identity and SHA256);
3. the rollback path (previous version, restore command or artifact);
4. one component per step, with a read-only probe before and after;
5. before/after evidence under `reports/mission_r4/` (never edited afterwards).

The exception never authorizes disabling Defender/Secure Boot/signature
enforcement, installing drivers from non-OEM sources, editing the contents of
`C:\Program Files\WindowsApps`, force-installing a package with a broken
signature, or any change on a host that is not named in the mission. Anything
outside that envelope is recorded as `BLOCKED_POLICY_CONFLICT` and is not
executed.

