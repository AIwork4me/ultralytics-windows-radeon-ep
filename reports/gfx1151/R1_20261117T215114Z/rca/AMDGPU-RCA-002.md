# AMDGPU-RCA-002 — MIGraphX auto_save_problem_cache write fails (WindowsApps protected path)

- severity: P2  status: **MITIGATED**  stage: compile
- first seen: 2026-11-17T21:49:38Z  last seen: 2026-11-17T22:58:52Z  head: `eb3ba42`
- environment: `{"os_build": "Windows 11 26200", "driver": "32.0.22032.6002", "ep_dll_sha256": "cb7496b3ab8f9daf2948b6030db5ec795596072ffcb25d3c55a28e4c0df7f8ab", "ort": "onnxruntime-windowsml 1.25.2.202605110140", "python": "3.13.13 (.venv-winml)"}`
- exception: `None`

## Observation

Quantified: yolo26n (640 static fp32) cold session-create takes ~186 s wall in a fresh process (probe compile ~63 s + rebuild ~123 s; model compiled twice by design), while the tiny canary MatMul graph takes 1.3 s. Consistent with per-process cold MIGraphX compilation whose problem_cache cannot persist (WindowsApps ACL). In-process second create is fast.

## Experiments

- {"hypothesis": "cache write failure -> cold compile each process", "command": "_ep_worker.py --case yolo26n amdgpu (fresh process)", "controlled_variable": "process freshness", "predicted_if_true": "session-create >> in-process rebuild", "actual_result": "186 s cold vs 1.3 s canary; rebuild 123 s in same process", "reproducible": true, "repetitions": 2, "falsified": false}

## Causal inference

External root cause: MSIX 1.8.65.0 attempts problem_cache.json inside its own read-only WindowsApps directory; every process pays a full cold compile for YOLO-scale graphs. Not a correctness issue (parity allclose, task parity pass); a per-process latency cost only.

## Mitigation

Harness-level: benchmark separates session-create from warm inference (R1-F); no system/ACL change (forbidden). Possible future: user-level MIGRAPHX cache env var if the EP honors one (untested — child-env experiment logged as next step).

## Remaining uncertainty

Whether any supported env var redirects this EP's cache.
