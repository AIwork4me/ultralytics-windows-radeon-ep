# PROVIDER_FALLBACK — strict, disclosed, never silent

Policies (configs/provider_policy.yaml):

- `require_amdgpu` (default): providers = `['AMDGPUExecutionProvider']` only.
  If the EP cannot initialize, session creation raises -> receipt `FAIL_RUNTIME`
  or `BLOCKED_EP`. There is no CPU session behind it.
- `prefer_amdgpu_allow_dml`: AMDGPU first; on failure a **new subprocess** (fresh
  interpreter, fresh session) runs DML on the **same physical Radeon** (LUID-checked).
  The receipt always discloses the original AMDGPU failure; DML success is
  reported as `DML: PASS_*`, never as an AMDGPU pass or as a "fallback that
  didn't happen".
- `cpu_opt_in`: only with an explicit user flag; labeled `CPU_ONLY_USER_AUTHORIZED`.

## Detection beyond get_providers()

- ORT profiler JSON audited per run: provider node counts (`AMDGPU`/`Dml`/`CPU`).
- stdout/stderr scanned for wrapper-level CPU-retry phrases (`capture_stderr_fallback_signals`).
- FULL claims require zero CPU nodes; any CPU compute downgrades to PARTIAL with
  counts recorded.
- `sess.get_providers()` is recorded but is explicitly NOT evidence.

## Demonstration (mission P3.3)

`tests/adversarial` and the negative-injection runner cover: wrong LUID,
impossible EP path, GPU-registered-but-CPU-only graph, DML on wrong adapter,
simulated EP_FAIL with wrapper CPU retry — CLI must print `AMDGPU_FAILED` /
`DML_FAILED` with reasons and non-zero exit codes (31/32/34/35).
