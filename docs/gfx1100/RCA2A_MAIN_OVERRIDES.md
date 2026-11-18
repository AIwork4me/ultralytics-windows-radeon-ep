# main_overrides — modifications to files that already exist on origin/main

These are NOT new RCA2A files. When building the publication worktree B
(branch mission/gfx1100-rca2a-migraphx-autotune-cache from origin/main
492975284f7c93684d6dbd67801a5634f450f7a0), copy these OVER the existing
same-path files from main; the resulting `git diff` entries are intentional
and must be listed in the PR description.

- src/radeon_ort/dml_session.py — RCA2A-008 fix: `create_dml_session` gains an
  optional `sess_options_override` keyword (backward compatible; all existing
  call sites unchanged). Reason: runner/rca2a_worker.py --ep dml path (E8
  DirectML control arm) passes supervisor-prepared SessionOptions and crashed
  with TypeError before this fix. Found during Phase-4 prep, BEFORE any E8 GPU
  run; no prior receipt is affected (Phase 1-3 arms are all amdgpu).
