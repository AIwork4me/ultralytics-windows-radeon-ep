# AMDGPU-RCA-003 — AMDGPU ep-device candidate[0] fails with HIP 101 invalid device ordinal (GPU=0)

- severity: P1  status: **REPRODUCED**  stage: session-create
- first seen: 2026-11-17T21:49:37Z  last seen: 2026-11-17T21:52:58Z  head: `2724c4cd`
- environment: `{"os_build": "Windows 11 26200", "driver": "32.0.22032.6002", "ep_dll_msix": "MicrosoftCorporationII.WinML.AMD.GPU.EP.1.8_1.8.65.0_x64__8wekyb3d8bbwe", "ep_dll_sha256": "cb7496b3ab8f9daf2948b6030db5ec795596072ffcb25d3c55a28e4c0df7f8ab", "ort": "onnxruntime-windowsml 1.25.2.202605110140", "python": "3.13.13 (.venv-winml)"}`
- exception: `[ONNXRuntimeError] : 1 : FAIL : HIP failure 101: invalid device ordinal; GPU=0; file=C:\j\amdgpu-ep\src\shared\hip\allocator.cc; line=14; expr=hipSetDevice(device_id)`

## Observation

With 2 AMDGPU OrtEpDevices enumerated (global indices 3 and 4 of 5), session creation on candidate[0] deterministically raises HIP 101 at hipSetDevice(device_id=0) inside the vendor EP; candidate[1] succeeds and profiles AMDGPU compute. Reproduced live 2026-11-17T21:49Z (canary) and consistent with G1-era behavior (dev[0] HIP 101, dev[1] OK).

## Hypotheses

- The two OrtEpDevices both target the single physical iGPU but the EP assigns HIP ordinals 0/1; ordinal 0 is invalid in this driver+EP combination (only ordinal 1 backed), i.e. EP enumeration bug
- Ordinal space is offset by the other 3 non-AMDGPU ep-devices; candidate index != HIP device id and cand[0] maps to a nonexistent id
- GameViewer virtual display reorders/offsets device enumeration

## Next discriminating experiment

Independent short-lived processes x3: enumerate ep devices, attempt cand[0] then cand[1], record HIP error text GPU=n per candidate. If GPU=0 always for cand[0] and GPU=1 for cand[1] => enumeration-offset hypothesis strengthened; then try reversing device order to check ordinal assignment stability.

## Confounders considered

- GameViewer virtual display
- driver 32.0.22032.6002
- MSIX 1.8.65.0 dual-install
