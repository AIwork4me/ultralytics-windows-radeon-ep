# AMDGPU-RCA-004 — Dual AMDGPU ep-device enumeration indistinguishable; legacy receipts stored DXGI-0 LUID not the bound ep-device

- severity: P1  status: **NEW**  stage: identity
- first seen: 2026-11-17T21:49:36Z  last seen: 2026-11-17T21:52:58Z  head: `2724c4cd`
- environment: `{"os_build": "Windows 11 26200", "driver": "32.0.22032.6002", "ep_dll_msix": "MicrosoftCorporationII.WinML.AMD.GPU.EP.1.8_1.8.65.0_x64__8wekyb3d8bbwe", "ep_dll_sha256": "cb7496b3ab8f9daf2948b6030db5ec795596072ffcb25d3c55a28e4c0df7f8ab", "ort": "onnxruntime-windowsml 1.25.2.202605110140", "python": "3.13.13 (.venv-winml)"}`
- exception: `None`

## Observation

Both AMDGPU OrtEpDevices expose IDENTICAL ep_vendor/ep_options/ep_metadata (library_path, version 0.1.0) — no LUID, ordinal, or adapter hint. Old G2/G3 receipts record session.device_luid from the DXGI adapter chosen BEFORE the probe (index 0 -> 0x0:0x11746) while the session actually bound ep-device candidate[1]; therefore the legacy device_luid field is DXGI corroboration, NOT binding evidence. Identical name/VID/DID does not prove the two DXGI LUIDs (two enumerations of the same iGPU) map 1:1 to ep-device candidates.

## Hypotheses

- The physical 8060S is one GPU enumerated twice; ep candidates are two HAL views (one broken per RCA-003) — binding to cand[1] still lands on the only physical GPU, but this must be PROVEN, not assumed
- Candidates could map to the two DXGI enumerations in unknown order

## Next discriminating experiment

R1-C: per-candidate probe in isolated processes capturing profile counts + memory telemetry; correlate with DXGI/PnP evidence; if candidates remain indistinguishable, mark IDENTITY_BINDING_UNPROVEN for candidate->LUID mapping and record selection attestation instead.

## Confounders considered

- boot-local LUID instability
- GameViewer adapter
