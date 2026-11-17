# AMDGPU-RCA-001 — ORT env creation logs 'Init provider bridge failed.' in winml venv

- severity: P2  status: **REPRODUCED**  stage: registration
- first seen: 2026-11-17T21:48:04Z  last seen: 2026-11-17T21:52:58Z  head: `2724c4cd`
- environment: `{"os_build": "Windows 11 26200", "driver": "32.0.22032.6002", "ep_dll_msix": "MicrosoftCorporationII.WinML.AMD.GPU.EP.1.8_1.8.65.0_x64__8wekyb3d8bbwe", "ep_dll_sha256": "cb7496b3ab8f9daf2948b6030db5ec795596072ffcb25d3c55a28e4c0df7f8ab", "ort": "onnxruntime-windowsml 1.25.2.202605110140", "python": "3.13.13 (.venv-winml)"}`
- exception: `None`

## Observation

The warning is emitted once per process at onnxruntime import/env creation in .venv-winml, BEFORE any amdgpu-ep.dll registration and independent of it. Also seen in G1-era runs. GPU sessions still create and execute (canary cand[1] OK; G2/G3 32/32 receipts).

## Hypotheses

- ORT python bridge tries to preload a provider bridge DLL that is absent in the windowsml distribution (cosmetic, no functional effect)
- Bridge init failure disables some provider surface we have not used yet (e.g. direct AMDGPUExecutionProvider class registration)

## Next discriminating experiment

Fresh process: import onnxruntime with ORT log level verbose and capture which bridge init is attempted; compare providers surface with/without the warning. Bounded: 3 runs.

## Confounders considered

- venv-only artifact
- WindowsApps ACL
