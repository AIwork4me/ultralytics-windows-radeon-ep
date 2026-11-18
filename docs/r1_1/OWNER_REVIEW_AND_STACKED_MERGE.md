# GFX1151-R1.1 — Final Review & Stacked PR Closure

**Scope:** CPU-only evidence/validator hardening and owner-review reconciliation.
**Historical GPU evidence:** `reports/gfx1151/R1_20261117T215114Z/` is immutable,
including its 749-entry manifest, original receipts, raw profiles and errata.
No new physical GPU test is claimed by this amendment.

## Review index

| Layer | Files / evidence | Audit objective |
|---|---|---|
| Fail-closed v2 validator | `src/radeon_ort/receipts.py`, `tests/adversarial/test_r11_strict_thresholds.py` | Declared task threshold presence, metric completeness, numeric-vs-task independence |
| Task semantics | `src/radeon_ort/task_parity.py` | Classification score-drift limit is an actual PASS condition |
| Original GPU provenance | `reports/gfx1151/R1_20261117T215114Z/R1_PROVENANCE.md` | Physical gfx1151, MSIX, ONNX, software versions |
| Original GPU receipts | `reports/gfx1151/R1_20261117T215114Z/receipts/` | 16 AMDGPU + 16 DML (representative slice only) |
| Device evidence | `reports/gfx1151/R1_20261117T215114Z/R1_DEVICE_BINDINGS.json` | Selected EP device, probe/final profiles, DXGI only corroborative |
| AMDGPU RCA | `reports/gfx1151/R1_20261117T215114Z/rca/` | Six issues; unresolved hypotheses explicitly remain open |
| Soak | `reports/gfx1151/R1_20261117T215114Z/SOAK_amdgpu.json` | 900 s inference phase for YOLO26n; does not prove all-catalog stability |
| Historical errata | `reports/gfx1151/R1_20261117T215114Z/ERRATA.md` | Original failures retained in Git history |

## R1.1 errata (does not rewrite the frozen report)

1. **Errata count:** `R1_LEDGER.json` says 4; `ERRATA.md` contains **5 numbered entries**. The ledger's text is stale, not a reason to modify frozen artifacts.
2. **CI snapshot:** at R1 PR head `1ed4376e18d4ada599a3c464cbb0297e615cc4b6`, `windows-cpu-ci` and `secret-scan` both completed **SUCCESS** (the R1 report's pending label is stale). Every **new** head requires fresh checks; CPU CI never attests GPU.
3. **RCA-002 causal precision:** a failed write to `problem_cache.json` under read-only WindowsApps and long cold session creation are observed. A writable-cache A/B experiment has **not** been performed, so the extent to which cache write failure causes 71–201 s startup remains **NOT_FULLY_ISOLATED**. Probe + final session rebuild also recompiles. Do not claim `ROOT_CAUSE_CONFIRMED` for all startup latency.
4. **Semantic int parity timing:** the `numeric_parity.int_outputs.min_exact_agreement: 0.999` contract was introduced **after** observing an argmax boundary disagreement. It is a documented, versioned post-finding change, not a pre-experiment threshold. Old FAIL receipts are preserved.
5. **Real scope:** 32 GPU cases = 16 model/task entries × 2 EPs, plus targeted negatives. `26 BASELINE_REQUIRED` remain NOT_RUN in the 142-entry snapshot. The AMDGPU EP candidate→DXGI LUID mapping is still UNPROVEN.
6. **MSIX/Windows ML:** the validated path is manually registered `AMDGPU` from installed AMD MSIX, not a completed Windows ML Catalog install/ensure-ready flow.

## Merge gates and order

- Validate v2 adversarial tests and all CPU CI at **exact new HEAD**.
- Verify `secret-scan` success; verify original 749-item manifest is unchanged.
- Confirm no accidental weights, secrets, absolute workstation paths, or out-of-scope files.
- Close stacked PR **#3 into PR #2's feature branch first**, using a normal merge commit (no squash/rebase of GPU evidence history), then review PR #2's resulting exact HEAD against `main`.
- Publish to `main` only if GitHub required checks/reviews and owner-review gates permit. Do not falsely claim GPU data were rerun after these CPU-only edits.
- Merge actions must use expected exact HEAD SHAs; never force push.

## Remaining separate projects

- R2: 26 baseline-required catalog entries and long-tail, AMDGPU-first.
- R3: session selection/caching/cold-start minimization in genuine physical runs.
- Cross-host gfx1100 local-only results must not be uploaded or copied without separate authorization.
