# Unified Runtime Contracts - RunResult and APIs

> 统一运行入口契约。所有字段机器可校验；严格 verifier 独立复算，绝不信任执行器自报 label。
> R7 起增加独立 Task Oracle、Job Object 进程树生命周期、Attempt 隔离与分层证据。

## 1. predict() API

    from radeon_ort.unified_runtime import predict

    result = predict(
        model_path="models/onnx/yolo26n.raw_b1_fp32_static.onnx",  # required, hashed
        inputs={"images": tensor},        # required; hashed by name/dtype/shape/bytes
        policy="prefer_amdgpu_allow_dml", # see section 2
        target_gfx="gfx1151",             # optional; inferred from DID, never defaulted
        allow_cpu=False,                  # explicit consent for a CPU *business* result
        require_full_gpu=True,            # PARTIAL (any CPU node) is refused
        task="detect",                    # one of the 7 tasks
        verify_task=True,                 # R7-A: run the independent PT task oracle
        pt_reference="models/pt/yolo26n.pt",  # required when verify_task=True
        export_python=r".venv-export\Scripts\python.exe",  # torch+ultralytics venv
        task_params={"imgsz": 640, "conf": 0.25},
        timeout_s=900,                    # wall clock per subprocess attempt
        winml_python=None,                # interpreter that carries ORT + EP
        work_dir=None, run_id=None,       # evidence location / id
        cpu_reference="allow",            # "allow" | "forbid" (see 3.1)
        container="job_object",           # "job_object" | "plain"
        cancel_event=None,                # threading.Event -> bounded cancellation
    )

Preconditions (fail-closed, `ContractError` -> CLI exit 2): model file must
exist; inputs non-empty; policy in section 2; `allow_cpu=True` only with
`cpu_opt_in` or `gpu_first_allow_cpu`; unknown `target_gfx` ->
`UNSUPPORTED_GFX_TARGET`; `verify_task=True` requires a known `task` and a
`pt_reference`; an existing `receipt_<run_id>.json` is
`RUN_ID_ALREADY_FROZEN` unless `allow_reuse_run_id=True`.

## 2. Policies

| policy | order | CPU allowed |
| --- | --- | --- |
| `require_amdgpu` | AMDGPU | no |
| `prefer_amdgpu_allow_dml` | AMDGPU -> DML | no |
| `gpu_first_allow_cpu` | AMDGPU -> DML -> CPU | only with `allow_cpu=True` |
| `cpu_opt_in` | CPU only | required |

## 3. CPU semantics (explicit, never implicit)

1. `allow_cpu=False` forbids a CPU **business result**. It does **not** forbid
   the internal CPU ORT numeric-parity reference.
2. The reference is a real CPU execution and is always disclosed:
   `cpu_reference.used`, `cpu_reference.attempts[]`, and an `internal: true`
   entry in `attempts[]` with `purpose="cpu_numeric_reference"`. The result
   never claims the request was CPU-free.
3. `cpu_reference="forbid"` runs no CPU job. Numeric parity then cannot be
   established, so the attempt is refused with
   `CPU_REFERENCE_FORBIDDEN` and no GPU FULL claim is made. Verification is
   not skipped while claiming an evidence-grade GPU PASS.
4. A CPU business result is only ever `CPU_ONLY_USER_AUTHORIZED` with
   `cpu_user_authorized is True` and `gpu_full is False`.

## 4. RunResult fields

| field | type | notes |
| --- | --- | --- |
| `schema_version` | str | `unified_run_v1` |
| `requested_policy` | str | echo |
| `attempted_eps` | list[str] | policy attempts actually made (excludes internal refs) |
| `actual_ep` | str or null | `amdgpu` / `dml` / `cpu` |
| `runtime_status` | str | `GPU_FULL` / `GPU_FULL_DML` / `CPU_ONLY_USER_AUTHORIZED` / `BLOCKED` / `FAIL` |
| `gpu_full` / `task_verified` | bool | **independent** claims |
| `target_gfx` | str | resolved architecture |
| `physical_gpu` | dict or null | device identity evidence from the attempt |
| `device_binding_confidence` | str | `PHYSICAL_BINDING_CONFIRMED` / `EP_DEVICE_SELECTED_ORDINAL_MAPPING` / `UNPROVEN` |
| `model_sha256` | str | 64-hex |
| `input_sha256` | str | 64-hex aggregate over name/dtype/shape/bytes |
| `input_fingerprint` | dict | per-input dtype/shape/nbytes/sha256 |
| `target_ep_profile_nodes` | int | **>0** required for a GPU claim |
| `cpu_profile_nodes` | int | **==0** required for a GPU claim |
| `numeric_parity` | dict | by-name elementwise allclose vs CPU ORT |
| `task_parity` | dict | `status` in `PASS_TASK / FAIL_TASK / TASK_ORACLE_BLOCKED / NOT_RUN / NOT_REQUESTED` |
| `task_oracle` | dict or null | full oracle evidence (binding, thresholds, metrics, logs) |
| `fallback_reason` | str or null | first cause, including the AMDGPU failure |
| `attempts` | list | per-attempt ep/ok/error/nodes/binding/exit_code/log/attempt_id/process |
| `cpu_reference` | dict | `mode`, `used`, `attempts[]`, `counts_as_business_execution` |
| `code_provenance` | dict | git head, harness sha, dirty flag |
| `receipt` | dict | path/body_sha256/file_sha256/written |
| `receipt_sha256` | str | sha256 of the persisted `unified_run_v1` receipt body |
| `readiness` | dict | `gpu_full` / `task_verified` / `evidence_verified` / `product_ready` / `code_clean` |
| `evidence_grade` | str | `EVIDENCE` (real worker) or `INJECTED_TEST` |

## 5. Invariants (verifier-enforced)

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
6. `task_verified is True` iff `task_parity.status == PASS_TASK` **and** the
   oracle block carries `passed: true`, non-empty metrics, declared thresholds
   and the ONNX/PT/input hashes.
7. `TASK_ORACLE_BLOCKED` never coexists with `task_verified is True`.
8. For `evidence_grade == EVIDENCE`: the adopted attempt must appear in
   `attempts[]` with its own `attempt_id`, `fragment_matches_attempt is True`,
   a matching `outputs_sha256`, non-empty `process` evidence, zero
   `survivors`, and `input_binding.ok is True`.
9. `readiness.product_ready` implies `gpu_full` **and** `task_verified`.

## 6. Evidence layering (never collapsed)

    gpu_full      the adopted EP really executed the graph on the GPU
    task_verified an independent PT reference agrees within declared thresholds
    product_ready gpu_full AND task_verified AND verifier VALID AND clean code

A numeric-parity-only result is a legitimate `GPU_FULL_DML` with
`task_verified=False`. It is not "product ready".

## 7. Independent verifier

    from radeon_ort.unified_runtime import verify_run_result, validate_run_result_schema
    verify_run_result(result, model_path=..., inputs=..., work_dir=..., deep=True)

Each check returns `True` (verified), `False` (contradiction -> `RUNRESULT_INVALID`)
or `None` (no raw evidence -> `RUNRESULT_UNVERIFIED`). A result is never VALID
on the strength of a producer-filled label. With `deep=True`, a missing
`model_path`/`inputs` becomes UNVERIFIED rather than silently skipped.

`schemas/unified_run.schema.json` is enforced by
`validate_run_result_schema()`.

## 8. Attempt isolation (R7-C)

* every attempt gets a unique `attempt_id` (also stamped into the case JSON and
  echoed in the fragment) and a private `attempts/<attempt_id>/` directory
  holding `case.json`, `fragment.json`, `outputs.npz`, profiling data;
* input bundles are content-addressed under `inputs/<feed-fingerprint>/`; a
  different tensor can never reuse another tensor's file, and the worker
  re-hashes the bundle and refuses on mismatch (`INPUT_FINGERPRINT_MISMATCH`,
  exit 3);
* a fragment whose `attempt_id` does not match the current attempt is refused
  (`ATTEMPT_MISMATCH`), so a stale file can never satisfy a new run;
* outputs are read only from the current attempt directory; a planted
  `out_<ep>.npz` or a leftover `inputs.npz` is never consulted;
* fragments are published atomically (temp file + `os.replace`), so a timeout
  can only leave "absent" or "complete", never half a JSON;
* a failure keeps its partial artefacts, listed in `attempts[].partial_artifacts`
  and marked as failed rather than silently discarded;
* re-using a `run_id` whose receipt exists is refused before any execution.

## 9. Process-tree lifecycle (R7-B)

Each attempt runs inside its own Windows Job Object
(`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`), created suspended, assigned, then
resumed, so descendants cannot escape the container. On timeout, cancellation
or error the whole job is terminated and the survivors list is re-probed with a
read-only liveness check. If the job cannot be created or assigned, the attempt
still runs and the degraded container is recorded (`job_assigned: false`,
`job_error`) - never silently upgraded.

## 10. CLI

    python -m runner.unified_predict --model <onnx> --inputs-npz <npz> ^
      --policy prefer_amdgpu_allow_dml --gfx gfx1151 --out result.json

    python -m runner.unified_predict --model <onnx> --inputs-npz <npz> ^
      --policy prefer_amdgpu_allow_dml --gfx gfx1151 ^
      --task detect --verify-task --pt-reference models\pt\yolo26n.pt ^
      --run-id GFX1151_R7_TASK_DETECT --out r7_detect.json

Exit codes: `0` adopted GPU/CPU-authorized result (and `task_verified` when
`--verify-task` was requested), `2` contract error, `4` result adopted but the
task oracle did not pass, `30` blocked, `31` AMDGPU failed, `34`
device/identity blocked.

## 11. 7-task matrix

    python -m runner.unified_task_matrix --gfx gfx1151 ^
      --policy prefer_amdgpu_allow_dml --out reports\mission_r7\unified_task_matrix.json

Runs every Tier A checkpoint through `predict(verify_task=True)` on a real
image and writes one row per task with `runtime_status`, `task_verified`,
metrics, thresholds, verifier verdict and schema result.
