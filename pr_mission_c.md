## What this is

Mission C: root-cause closure for the Windows ML 2.x `AMDGPUExecutionProvider`
on the Radeon 8060S (gfx1151, DID `0x1586`), plus the two P0 harness defects that
made earlier evidence misleading.

**Base note — this is a stacked PR.** It is based on
`mission/experiment-b-gfx1151-amdgpu-root-cause` (PR #14) because the A/B
comparator (`tools/experiment_b_ab_compare.py`) and Experiment B's evidence only
exist on that branch. The diff here is exactly the Mission C change set; once
#14 merges, this base retargets to `main`. No force-push, no history rewrite, no
edits to frozen evidence.

## Verdict

```
C_CATALOG_BLOCKER_CONFIRMED_ROOT_CAUSE_UNVERIFIED

CONFIRMED_PROXIMATE_BLOCKER = CATALOG_EMPTY
UNDERLYING_ROOT_CAUSE       = UNVERIFIED
```

The AMDGPU EP still cannot run on this host, and **no GPU success is claimed**.
What is new is a reproducible, controlled statement of *where* it stops, proof of
what it is *not*, and an upstream escalation.

## Evidence highlights (all machine-readable under `reports/mission_c/`)

| Finding | Evidence |
| --- | --- |
| Catalog returns `S_OK` with **0** providers in **all three accepted modes** (`CreateWithMode` 0/1/2; 3+ → `E_INVALIDARG`) | `C1_CATALOG_INTERNALS.json` |
| `FindProvider("AMDGPUExecutionProvider", <official PFN>)` → `0x80070490` | same |
| The provider metadata is a **28-entry table embedded in the signed runtime DLL**, with Store product ids, vendor ids and per-device blacklists | same |
| **`0x1586` is NOT in the AMDGPU entry's `UnsupportedDeviceIDs`**, vendor `0x1002` is supported → the empty list is *not* a documented device blacklist | same |
| The AMD GPU EP is a real public Store product: `Microsoft.WinML.AMD.GPU.EP.Framework.2` → `9PNKGVD4CPCV` ("Windows ML Runtime AMD GPU Execution Provider Framework 2") | same + [Store page](https://apps.microsoft.com/detail/9PNKGVD4CPCV) |
| Real A/B diff: same interop DLL SHA256 on both hosts, 4 providers vs 0; OS build differs (26300 vs 26200) | `C0_AB_COMPARISON_V2.json` |
| ETW needed to see the internal decision → **BLOCKED_ADMIN_DEPENDENCY** (non-admin session) with an executable capture plan | `C3_ETW_CAPTURE_MANIFEST.json` |
| DirectML still physically healthy on this host (1 node, parity allclose, max_abs 2.86e-06) | `C5_DML_REGRESSION.json` |
| 219-check verifier over the package: `ALL_OK` | `tools/verify_mission_c_evidence.py` |

## Code changes (minimal, each with a regression test)

1. **`select_amdgpu_ep_device()` — fail-closed.** With an explicit `target_luid`,
   the selection must be the one and only matching EP device; otherwise it returns
   `None`. Previously a non-matching LUID fell through to the "Discrete=1"/"take
   first" paths and returned a *different physical GPU* (Experiment A's
   `luid_pin_fail`, basis even said "no LUID given").
2. **`runner/_ep_worker.py` — input-source contract.** Exactly one of
   `feeds_npz`/`input_manifest` must be declared and must exist; otherwise exit
   **36 = `HARNESS_CONTRACT_VIOLATION`**. Previously a mis-wired case died with an
   uncaught `KeyError`, indistinguishable from an EP failure.
3. **`tools/experiment_b_ab_compare.py` — real A support.** Added extractors for
   Experiment A's actual schemas and numeric coercion. It had never consumed an A
   artifact and crashed (`str > int`) on the first real one.

New: `tools/mission_c_catalog_internals.py`, `tools/mission_c_build_evidence.py`,
`tools/verify_mission_c_evidence.py`, `tests/unit/test_mission_c_ep_selection.py`
(12 CPU-only adversarial tests).

CI gains an evidence self-check step (CPU only).

## Docs (`docs/mission_c/`, Chinese report + English upstream material)

`EXECUTIVE_REPORT_CN.md` (mandatory A–L), `A_B_FINAL_DIFFERENTIAL_CN.md`,
`ROOT_CAUSE_ANALYSIS.md`, `OFFICIAL_EP_COMPATIBILITY.md`,
`UPSTREAM_MICROSOFT_ISSUE_EN.md`, `FEEDBACK_HUB_SUBMISSION_GUIDE.md`,
`NEXT_ACTIONS.md`.

## Test plan

- [x] `pytest tests/unit tests/contracts tests/adversarial` → 434 passed, 1 skipped
- [x] `tools/verify_mission_c_evidence.py` → 219 checks, `ALL_OK`
- [x] `runner.preflight` on the host → DML canary PASS, AMDGPU `rc=34 BLOCKED_EP`
- [ ] CPU CI on the exact HEAD (see checks below)

`CPU_CI_DOES_NOT_ATTEST_GPU`: none of the above is AMDGPU GPU evidence.

## What was deliberately NOT done

- no driver change (no verifiable incompatibility evidence + no authorization)
- no MSIX / module install, no `ensure_ready` (system change; unauthorized)
- no copy of `amdgpu-ep.dll` from another host
- no edits to frozen Experiment A/B receipts (their defects are documented instead,
  incl. Experiment A's manifest not validating against its own committed artifacts)

## Escalation

Posted (one issue, dedupe-checked across 4 repos):
<https://github.com/microsoft/WindowsAppSDK/issues/6994>
