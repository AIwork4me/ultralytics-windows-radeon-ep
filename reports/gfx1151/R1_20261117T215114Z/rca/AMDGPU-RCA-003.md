# AMDGPU-RCA-003 — AMDGPU ep-device candidate[0] fails with HIP 101 invalid device ordinal (GPU=0)

- severity: P1  status: **ISOLATED**  stage: session-create
- first seen: 2026-11-17T21:49:37Z  last seen: 2026-11-17T22:58:52Z  head: `eb3ba42`
- environment: `{"os_build": "Windows 11 26200", "driver": "32.0.22032.6002", "ep_dll_sha256": "cb7496b3ab8f9daf2948b6030db5ec795596072ffcb25d3c55a28e4c0df7f8ab", "ort": "onnxruntime-windowsml 1.25.2.202605110140", "python": "3.13.13 (.venv-winml)"}`
- exception: `None`

## Observation

Reproduced deterministically across independent processes on 2026-11-18 (yolo26n real-model run): candidate[0] = global ep-device index 3 -> HIP failure 101 at hipSetDevice(0); candidate[1] = global index 4 -> session OK, profiler AMDGPU nodes=1 (fused), probe + rebuilt-final both positive.

## Experiments

- {"hypothesis": "candidate index maps to HIP ordinal and ordinal 0 is invalid in this driver+EP", "command": "create_amdgpu_session(device_candidates=[dev0]) then [dev1]", "controlled_variable": "ep-device candidate only", "predicted_if_true": "dev0 -> HIP 101 GPU=0; dev1 -> OK", "actual_result": "exactly that, 3/3 independent processes", "reproducible": true, "repetitions": 3, "falsified": false}

## Causal inference

Isolated to the vendor EP's device-ordinal assignment: the EP enumerates two AMDGPU devices but binds ordinal 0 to a nonexistent HIP device. Root cause internal to closed DLL (amdgpu-ep.dll allocator.cc hipSetDevice); external boundary — not fixable in-repo. Harness fail-closed candidate loop is the correct mitigation and is now attested per-receipt.

## Mitigation

create_amdgpu_session tries candidates in order, requires probe profile AMDGPU>0, revalidates the rebuilt final session, and records the full candidate_log in every receipt.

## Remaining uncertainty

Why the EP enumerates a broken ordinal-0 device at all (driver enumeration offset vs MSIX bug) — cannot be discriminated without vendor-side introspection.
