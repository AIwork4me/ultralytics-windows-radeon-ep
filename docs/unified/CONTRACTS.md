# Unified Runtime Contracts — RunResult & APIs

> 统一运行入口契约 — RunResult 与 API。所有字段在 receipts 中机器可校验；
> 严格 verifier 按 rca2a_schema 传统独立复算，绝不信任 worker 自报 label。

## 1. `predict()` API

```python
from radeon_ort.unified_runtime import predict

result = predict(
    model_path="models/onnx/yolo26n.onnx",     # required, bytes hashed → onnx_sha256
    inputs={"images": tensor},                 # required, hashed → input_sha256 (sorted-key byte join)
    policy="prefer_amdgpu_allow_dml",          # require_amdgpu | prefer_amdgpu_allow_dml | cpu_opt_in
    target_gpu="RX 9070 XT",                   # optional human name; DID pinning is authoritative
    target_gfx="gfx1201",                      # optional arch pin (overrides name)
    allow_cpu=False,                           # MUST be an explicit user action; False by default
    require_full_gpu=True,                     # PARTIAL (any CPU node) is refused, not downgraded
    task="detect",                             # optional task oracle
    timeout_s=1200,                            # bounded, per attempt
)
```

Preconditions (fail-closed, typed errors): `model_path` must exist and be a file;
`inputs` must be non-empty tensors; `policy` must be one of the three; `allow_cpu=True`
combined with `policy != "cpu_opt_in"` is a contract error (consent is not additive
sugar); unknown `target_gfx` → `UNSUPPORTED_GFX_TARGET` listing supported archs.

## 2. `RunResult` (frozen field list)

| field | type | notes |
|---|---|---|
| `requested_policy` | str | echo of the policy argument |
| `attempted_eps` | list[str] | ordered, e.g. `["amdgpu", "dml"]`; empty if blocked before attempt |
| `actual_ep` | str\|None | EP that produced the returned outputs (`"amdgpu"`/`"dml"`/`"cpu"`/None) |
| `actual_provider` | str\|None | session-internal provider name from profiler, not get_providers() |
| `status` | str | `GPU_FULL` \| `GPU_FULL_DML` \| `PARTIAL_CPU` \| `CPU_ONLY_USER_AUTHORIZED` \| `FAIL` \| `BLOCKED` |
| `cpu_user_authorized` | bool | echo of explicit consent; False unless user set it |
| `device_identity` | dict | physical chain: pnp, vid/did, dxgi desc, luid (boot-local), ordinal-basis |
| `device_binding_confidence` | str | `PHYSICAL_BINDING_CONFIRMED` \| `EP_DEVICE_SELECTED+PHYSICAL_GPU_CORROBORATED;BINDING_UNPROVEN` \| `UNPROVEN` |
| `ep_version` / `ep_dll_sha` | str | from the registered library actually used |
| `onnx_sha256` / `input_sha` | str | 64-hex; binding to frozen plan semantics |
| `target_ep_profile_nodes` | int | profiler count of nodes on target EP; **>0 required for GPU_FULL** |
| `cpu_profile_nodes` | int | profiler CPU node count; **must be 0 for GPU_FULL** |
| `numeric_oracle` | dict | per-output by-name elementwise allclose vs independent CPU ORT, worst |Δ| |
| `task_oracle` | dict | metric vs PT adapter (IoU/OKS/mIoU/…) or `NOT_RUN` with reason |
| `fallback_reason` | str\|None | machine-readable first-cause of any fallback/blocked |
| `cache_status` | str | `HIT`/`MISS`/`INVALIDATED`/`CACHE_NOT_VALIDATED`/`CORRUPT` + key |
| `provenance_head` | str | code_git_sha (40-hex or DIRTY-prefixed), worktree_dirty |
| `source_receipt_sha` | str | SHA-256 of the immutable receipt this result was assembled from |

Invariants (verifier-enforced, adversarially tested):

1. `status == "GPU_FULL"` ⇒ `target_ep_profile_nodes > 0` ∧ `cpu_profile_nodes == 0` ∧
   `numeric_oracle.allclose == True` ∧ `device_binding_confidence != "UNPROVEN"`.
2. `status == "CPU_ONLY_USER_AUTHORIZED"` ⇒ `cpu_user_authorized is True` — a CPU result with
   consent False is a contract breach (verifier fails it).
3. `status == "PARTIAL_CPU"` never satisfies `require_full_gpu=True` (returned as refusal
   with `fallback_reason`).
4. `actual_ep == "dml"` ⇒ an AMDGPU attempt record with its failure exists in
   `attempted_eps` context (disclosure rule).
5. SHAs: git = 40-hex (or 64), content = 64-hex; mixing them fails (rca2a tradition).

## 3. Exit codes (CLI `python -m runner.unified_predict`)

```
0  GPU_FULL / GPU_FULL_DML / CPU_ONLY_USER_AUTHORIZED (with --allow-cpu)
2  contract error (bad args, conflicting consent)
31 AMDGPU_FAILED (fail-closed after attempt; includes BLOCKED_EP registration failure)
32 DML_FAILED (attempted and failed under allowed policy)
34 BLOCKED_* (identity ambiguous, install authorization missing, unsupported arch…)
35 SILENT_CPU_FALLBACK_BLOCKED (provider list lied about GPU session)
```

## 4. Receipts

One append-only JSON receipt per attempt under `reports/<gfx>/<run_id>/receipts/`,
schema `unified_run_v1`, containing the full RunResult plus timeline, env echo,
watchdog state, worker exit code and child PID ownership record. Reruns get new
`run_id`s; historical receipts are never edited (AGENTS.md rule 3).
