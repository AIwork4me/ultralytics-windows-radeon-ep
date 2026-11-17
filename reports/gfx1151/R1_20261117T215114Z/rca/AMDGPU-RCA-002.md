# AMDGPU-RCA-002 — MIGraphX auto_save_problem_cache write fails (WindowsApps protected path)

- severity: P2  status: **REPRODUCED**  stage: compile
- first seen: 2026-11-17T21:49:38Z  last seen: 2026-11-17T21:52:58Z  head: `2724c4cd`
- environment: `{"os_build": "Windows 11 26200", "driver": "32.0.22032.6002", "ep_dll_msix": "MicrosoftCorporationII.WinML.AMD.GPU.EP.1.8_1.8.65.0_x64__8wekyb3d8bbwe", "ep_dll_sha256": "cb7496b3ab8f9daf2948b6030db5ec795596072ffcb25d3c55a28e4c0df7f8ab", "ort": "onnxruntime-windowsml 1.25.2.202605110140", "python": "3.13.13 (.venv-winml)"}`
- exception: `auto_save_problem_cache: save failed: write_buffer: Failure opening file: C:\Program Files\WindowsApps\...\ExecutionProvider\problem_cache.json`

## Observation

Emitted by the AMDGPU EP when a graph compiles in our process: the bundled MIGraphX attempts to persist its problem cache next to the MSIX DLL under WindowsApps, which is ACL-protected (read/execute for user). Emitted 3x during one canary session-create+run. Compilation and execution still succeed.

## Hypotheses

- External root cause: MSIX package does not relocate its cache dir; expected user-writable path is not configured by this EP build (ep_options empty, no documented option on 1.8.65.0)
- Consequence is cold-compile cost per process (no persistent cache), not correctness

## Next discriminating experiment

Measure session-create wall time in N=3 fresh processes vs repeated in-process creates; if flat, cache miss cost is bounded to first compile per process. Check whether MIGRAPHX_* env vars are honored (child-process env only, no system change).

## Confounders considered

- antivirus lock
- concurrent GPU processes
