# Unified Runtime Architecture

    predict()                      src/radeon_ort/unified_runtime.py
      |- validate_args             policy/consent/gfx/task contract
      |- input_fingerprint         name+dtype+shape+bytes -> aggregate SHA
      |- run_id freeze check       an existing receipt is immutable
      |- gfx resolution            DXGI DID -> gfx; multi/zero candidates refused
      |- WorkerExecutor            src/radeon_ort/unified_executors.py
           |- attempt <id> dir     case/fragment/outputs/profile, never shared
           |- input bundle         content-addressed + re-hashed by the worker
           |- Win Job Object       src/radeon_ort/win_job.py (suspend -> assign -> resume)
           |- cpu reference        runner._ep_worker (internal, disclosed)
           |- amdgpu attempt       runner._ep_worker -> amdgpu_session
           \- dml attempt          runner._ep_worker -> dml_session
                -> fragment JSON (provider nodes, parity, binding)
      evaluate_attempt()           evidence gates -> GPU_FULL or a named refusal
      task oracle                  src/radeon_ort/task_oracle_runner.py
           |- PT reference         export venv, own process + own job, CPU only
           |- vendor postprocess   onnx_postprocess.py on the EP raw output
           \- comparator           task_parity.compare vs predeclared thresholds
      _finalize()                  code_state + receipt bytes + unified_run_v1
      verify_run_result()          independent re-derivation, layered verdicts

## Evidence classes (never interchangeable)

1. **Physical GPU** - PnP VID/DID + DXGI description + boot-local LUID (host side).
2. **Catalog EP entry** - Windows ML EpName/version/ReadyState/library_path.
3. **ORT alias** - the name the library was registered under in this process.
4. **OrtEpDevice** - EP-internal device candidate (metadata LUID when exposed).

The real host evidence for this repository: AMDGPU/DML on ORT 1.25.2 expose no
EP-reported LUID, so a DML result carries
`EP_DEVICE_SELECTED_ORDINAL_MAPPING` with an explicit mapping assumption, and an
AMDGPU result can only claim `PHYSICAL_BINDING_CONFIRMED` when the EP reports a
LUID equal to the DXGI LUID. Everything else is `UNPROVEN` and cannot pass.

## Two independent oracles

| oracle | question | where |
| --- | --- | --- |
| numeric parity | "did the GPU compute the same numbers?" | CPU ORT in `_ep_worker` |
| task oracle | "does the task result agree with the reference model?" | PT reference + vendor decode + thresholds |

They are deliberately separate: a numerically perfect run of a wrong task is
still a FAIL, and a task that matches on average can still be refused if any
output fails strict numeric parity.

## Isolation

Each attempt runs in a fresh interpreter inside its own Windows Job Object with
its own attempt directory, its own content-addressed input bundle, and a
parent-enforced timeout. A wedged EP - and every process it spawned - is
reclaimed by terminating the job; the attempt is recorded as `TIMEOUT_<n>s`
with its partial artefacts and a re-probed survivor list. One GPU-heavy process
at a time remains a repository rule (AGENTS.md 5).

The PT oracle runs in a *different* interpreter (the export venv, CPU only) and
in its own job, so it can never contaminate the GPU execution process.

## Failure taxonomy

`SILENT_CPU_FALLBACK`, `*_PROFILE_ZERO_NODES`, `*_CPU_NODES_PRESENT`,
`PARITY_*`, `DEVICE_BINDING_*`, `AMDGPU_FAILED`, `DML_FAILED`,
`CPU_NOT_AUTHORIZED`, `CPU_REFERENCE_FORBIDDEN`, `TIMEOUT_*`,
`CANCELLED_BY_CALLER`, `EXECUTOR_EXCEPTION`, `ATTEMPT_MISMATCH`,
`INPUT_FINGERPRINT_MISMATCH`, `RUN_ID_ALREADY_FROZEN`, `TASK_ORACLE_BLOCKED`
- each is reported with the first cause and the attempt trace, never collapsed
into a single "failed".
