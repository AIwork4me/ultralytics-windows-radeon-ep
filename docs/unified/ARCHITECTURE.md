# Unified Runtime Architecture

    predict()                      src/radeon_ort/unified_runtime.py
      |- validate_args             policy/consent/gfx contract
      |- input_fingerprint         name+dtype+shape+bytes -> aggregate SHA
      |- gfx resolution            DXGI DID -> gfx; multi/zero candidates refused
      \- WorkerExecutor            src/radeon_ort/unified_executors.py
           |- cpu reference        runner._ep_worker (subprocess, own log)
           |- amdgpu attempt       runner._ep_worker -> amdgpu_session
           \- dml attempt          runner._ep_worker -> dml_session
                -> fragment JSON (provider nodes, parity, binding, benchmark)
      evaluate_attempt()           evidence gates -> GPU_FULL or a named refusal
      _finalize()                  code_state + receipt_sha256 + unified_run_v1

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

## Isolation

Each attempt runs in a fresh interpreter with its own case/fragment/log files and
a parent-enforced timeout. A wedged EP is killed and recorded as
`TIMEOUT_<n>s`; it cannot hang the caller or leak provider state into the next
attempt. One GPU-heavy process at a time remains a repository rule (AGENTS.md 5).

## Failure taxonomy

`SILENT_CPU_FALLBACK`, `*_PROFILE_ZERO_NODES`, `*_CPU_NODES_PRESENT`,
`PARITY_*`, `DEVICE_BINDING_*`, `AMDGPU_FAILED`, `DML_FAILED`,
`CPU_NOT_AUTHORIZED`, `TIMEOUT_*`, `EXECUTOR_EXCEPTION` - each is reported
with the first cause and the attempt trace, never collapsed into a single
"failed".
