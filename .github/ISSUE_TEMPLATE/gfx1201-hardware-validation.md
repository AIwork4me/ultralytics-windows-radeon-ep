---
name: gfx1201 hardware validation (RX 9070 XT)
about: Independent validation runbook checklist for a future gfx1201 host
title: "[gfx1201] RX 9070 XT Windows ML AMDGPU/DML validation"
labels: ["hardware", "gfx1201"]
---

**Scope**: this issue tracks an INDEPENDENT gfx1201 host run. gfx1201 results are
NOT_RUN until hardware evidence exists. Never recycle gfx1151 receipts.

## Preconditions
- [ ] Windows 11 24H2+ (build >= 26100), WDDM driver meeting Microsoft AMDGPU EP minimums
- [ ] Windows App SDK runtime >= 2.5.96 for Microsoft.Windows.AI.MachineLearning
- [ ] Python 3.11+ (python.org/winget, x64)
- [ ] Clone + `pip install -e .[winml]` (pinned: wasdk 2.3.0 / onnxruntime-windowsml 1.25.2)

## Steps
- [ ] `python -m runner.preflight --gfx gfx1201 --gpu-needle "9070"` -> HOST/EP/DEVICE/CANARY json
- [ ] Verify device identity: vendor 0x1002, gfx1201-class device id, LUID recorded
- [ ] CPU + AMDGPU + DML canary in separate subprocesses (parity + node counts > 0)
- [ ] `python -m runner.run_matrix --tier core --ep both --benchmark`
- [ ] `python -m runner.verify_receipts --run-dir <run>` — must be ALL_OK
- [ ] Commit receipts + manifest under reports/gfx1201/<run_id>/ and open Draft PR

## Acceptance gates
- GPU PASS only with: verified identity + positive target-EP profile nodes + CPU parity + task oracle
- No silent CPU fallback; DML attested independently
- Manifest + redaction scan clean

## Non-goals
- No claims about gfx1151/gfx1100; no driver modifications; no unreviewed CI on shared runners
