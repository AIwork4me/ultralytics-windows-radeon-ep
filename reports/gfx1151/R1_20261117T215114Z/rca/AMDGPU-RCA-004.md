# AMDGPU-RCA-004 — Dual AMDGPU ep-device enumeration indistinguishable; candidate->LUID mapping unproven

- severity: P1  status: **MITIGATED**  stage: identity
- first seen: 2026-11-17T21:49:36Z  last seen: 2026-11-17T22:58:52Z  head: `eb3ba42`
- environment: `{"os_build": "Windows 11 26200", "driver": "32.0.22032.6002", "ep_dll_sha256": "cb7496b3ab8f9daf2948b6030db5ec795596072ffcb25d3c55a28e4c0df7f8ab", "ort": "onnxruntime-windowsml 1.25.2.202605110140", "python": "3.13.13 (.venv-winml)"}`
- exception: `None`

## Observation

Confirmed again 2026-11-18: both AMDGPU ep-devices expose identical metadata (vendor AMD, version 0.1.0, same library), no LUID/ordinal field. R1-C resolution: receipts now record device_binding with EP_DEVICE_SELECTED + per-candidate log + probe/final profile evidence + DXGI corroboration stored SEPARATELY as dxgi_corroboration (explicitly not a candidate->LUID proof). verdicts.identity distinguishes EP_DEVICE_SELECTED from PHYSICAL_GPU_IDENTITY_CORROBORATED; the latter is not claimed because the mapping is unproven.

## Experiments

- {"hypothesis": "ep-device metadata cannot distinguish the two candidates", "command": "ort.get_ep_devices() metadata dump x3 processes", "controlled_variable": "none (observation)", "predicted_if_true": "identical metadata dicts", "actual_result": "identical; only outcome (HIP 101 vs OK) distinguishes", "reproducible": true, "repetitions": 3, "falsified": false}

## Causal inference

EP API limitation on build 1.8.65.0. Honest labeling adopted: EP_DEVICE_SELECTED (probe-evidenced) + DXGI corroboration of the physical GPU; candidate->LUID mapping stays IDENTITY_BINDING_UNPROVEN. This is a labeling fix, not a hardware-identity doubt: single physical 8060S is corroborated by PnP/DXGI (VID 1002 DID 1586, unique non-software AMD adapter pair with identical description).

## Mitigation

schema v2 device_binding contract + validator enforcement (PASS requires EP_DEVICE_SELECTED or stronger + probe>0 + final profile>0).

## Remaining uncertainty

If a future EP build exposes LUIDs, upgrade to PHYSICAL_GPU_IDENTITY_CORROBORATED with direct evidence.
