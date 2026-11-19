# Unified Runtime Contracts — RunResult and APIs

> 统一运行入口契约。所有字段机器可校验；严格 verifier 独立复算，绝不信任执行器自报 label。

## 1. predict() API

    from radeon_ort.unified_runtime import predict

    result = predict(
        model_path="models/onnx/yolo26n.raw_b1_fp32_static.onnx",  # required, hashed
        inputs={"images": tensor},        # required; hashed by name/dtype/shape/bytes
        policy="prefer_amdgpu_allow_dml", # see section 2
        target_gfx="gfx1151",             # optional; inferred from DID, never defaulted
        allow_cpu=False,                  # explicit consent only
        require_full_gpu=True,            # PARTIAL (any CPU node) is refused
        task="detect",                    # recorded; task oracle is NOT run here
        timeout_s=900,                    # wall clock per subprocess attempt
        winml_python=None,                # interpreter that carries ORT + EP
        work_dir=None, run_id=None,       # evidence location / id
    )

Preconditions (fail-closed, `ContractError` -> CLI exit 2): model file must
exist; inputs non-empty; policy in section 2; `allow_cpu=True` only with
`cpu_opt_in` or `gpu_first_allow_cpu`; unknown `target_gfx` ->
`UNSUPPORTED_GFX_TARGET`.

## 2. Policies

| policy | order | CPU allowed |
| --- | --- | --- |
| `require_amdgpu` | AMDGPU | no |
| `prefer_amdgpu_allow_dml` | AMDGPU -> DML | no |
| `gpu_first_allow_cpu` | AMDGPU -> DML -> CPU | only with `allow_cpu=True` |
| `cpu_opt_in` | CPU only | required |

## 3. RunResult fields

| field | type | notes |
| --- | --- | --- |
| `requested_policy` | str | echo |
| `attempted_eps` | list[str] | ordered attempts actually made |
| `actual_ep` | str or null | `amdgpu` / `dml` / `cpu` |
| `runtime_status` | str | `GPU_FULL` / `GPU_FULL_DML` / `CPU_ONLY_USER_AUTHORIZED` / `BLOCKED` / `FAIL` |
| `gpu_full` / `task_verified` | bool | independent claims |
| `target_gfx` | str | resolved architecture |
| `physical_gpu` | dict or null | device identity evidence from the attempt |
| `device_binding_confidence` | str | `PHYSICAL_BINDING_CONFIRMED` / `EP_DEVICE_SELECTED_ORDINAL_MAPPING` / `UNPROVEN` |
| `model_sha256` | str | 64-hex |
| `input_sha256` | str | 64-hex aggregate over name/dtype/shape/bytes |
| `input_fingerprint` | dict | per-input dtype/shape/nbytes/sha256 |
| `target_ep_profile_nodes` | int | **>0** required for a GPU claim |
| `cpu_profile_nodes` | int | **==0** required for a GPU claim |
| `numeric_parity` | dict | by-name elementwise allclose vs CPU ORT |
| `task_parity` | dict | `NOT_RUN` unless a task oracle ran |
| `fallback_reason` | str or null | first cause, including the AMDGPU failure |
| `attempts` | list | per-attempt ep/error/nodes/binding/exit_code/log |
| `code_provenance` | dict | git head, harness sha, dirty flag |
| `receipt_sha256` | str | sha256 of the persisted `unified_run_v1` receipt |

## 4. Invariants (verifier-enforced)

1. `runtime_status == GPU_FULL` implies AMDGPU nodes > 0, CPU nodes == 0, strict
   parity, binding == `PHYSICAL_BINDING_CONFIRMED`, `gpu_full is True`.
2. `runtime_status == GPU_FULL_DML` implies DML nodes > 0, CPU nodes == 0,
   strict parity, binding == `EP_DEVICE_SELECTED_ORDINAL_MAPPING`.
3. `CPU_ONLY_USER_AUTHORIZED` implies `cpu_user_authorized is True` and
   `gpu_full is False`.
4. A provider that is not the requested EP (for example a CPU session) is a
   `SILENT_CPU_FALLBACK` refusal.
5. Any binding label outside the whitelist is refused - arbitrary strings never
   pass.

## 5. CLI

    python -m runner.unified_predict --model <onnx> --inputs-npz <npz> ^
      --policy prefer_amdgpu_allow_dml --gfx gfx1151 --out result.json

Exit codes: `0` GPU_FULL / GPU_FULL_DML / CPU_ONLY_USER_AUTHORIZED, `2`
contract error, `30` blocked, `31` AMDGPU failed, `34` device/identity
blocked.
