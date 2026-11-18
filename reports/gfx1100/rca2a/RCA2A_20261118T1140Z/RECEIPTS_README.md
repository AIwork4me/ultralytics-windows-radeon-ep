# Receipts in this package

- `receipts/<attempt_id>.json` — one redacted receipt per attempt (schema `rca2a_receipt_v1`).
- `RESULTS_RAW_PUBLIC.jsonl` — the same receipts as one JSONL, in execution order across all run roots.
- Receipts from the private run trees record `code_git_sha: UNKNOWN` with `worktree_dirty: true` (the run tree has no `.git`). The strict validator intentionally fails them (`RECEIPT_INVALID_CODE_GIT_SHA`) — this is the fail-closed `PUBLICATION_CODE_NOT_PHYSICALLY_VALIDATED` clause, not data corruption. The publication-valid set is the PUBCONF rerun executed from the real branch HEAD (`code_git_sha` = this PR's validated code HEAD, `worktree_dirty=false`), added in the evidence commit.
- Absolute local paths are redacted to `%RUN_ROOT%`, `%RESULTS_ROOT%`, `%WT%`, `%REPO%`; the generic MSIX install path under `C:\Program Files\WindowsApps\...` is kept as environment fingerprint (identical on every machine with this package, no user identity).
