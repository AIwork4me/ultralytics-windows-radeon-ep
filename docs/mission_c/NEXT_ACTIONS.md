# NEXT ACTIONS

Ordered by "what unblocks the most, with the least risk".

---

## P0 — waiting on external input

### N1. Watch the upstream issue
<https://github.com/microsoft/WindowsAppSDK/issues/6994>

| Trigger | Action |
| --- | --- |
| maintainer asks for an ETW trace | run the `C3_ETW_CAPTURE_MANIFEST.json` procedure **with Administrator + owner approval**, redact, attach |
| maintainer asks for a different scenario | re-run the same probe with the requested process/scenario; keep a new `run_id` |
| maintainer says "device not targeted" | record as `CONFIRMED_ROOT_CAUSE` with the official statement as evidence; proceed to N2 |
| maintainer says "should work" | ask for the specific internal check to instrument; capture it |

Never open a second issue for the same symptom.

### N2. Re-probe after any change
Any of these makes the catalogue worth re-reading (new `run_id`, always):

```powershell
.venv-winml\Scripts\python.exe tools\mission_c_catalog_internals.py
.venv-winml\Scripts\python.exe -m runner.preflight --gfx gfx1151 --run-id <NEW_RUN_ID>
```

Triggers: OS cumulative update, Windows ML / `windowsml` update, EP MSIX version
bump (watch the release matrix — `2.2611.67.0` was announced for GA 2026-12-01),
HP driver package update, Store product change.

**First thing to check after a change:** does the catalogue list
`AMDGPUExecutionProvider` at all? `ReadyState=NotPresent` is enough to proceed.

---

## P1 — needs a human decision (owner authorization)

### N3. Authorize the Store component install (only if justified)

If Microsoft confirms `9PNKGVD4CPCV` is targeted at this device — or the
catalogue starts listing it — the install is still a **system component change**
and needs a written, component-level owner authorization per `AGENTS.md`
"Owner-authorized provisioning exception":

1. host identity: `host_53f62d2ac2c6` + `0x1586`;
2. exact component: `Microsoft.WinML.AMD.GPU.EP.Framework.2`, publisher
   `CN=Microsoft Corporation …`, expected SHA256 to be recorded **before** install;
3. rollback: `Remove-AppxPackage` of that exact package full name;
4. one component, probe before and after;
5. evidence under `reports/mission_c/provisioning/<run_id>/` (never edited later).

Until then the correct status stays
`BLOCKED_AUTHORIZATION_FOR_ENSURE_READY_UNNECESSARY`.

### N4. ETW capture authorization
One short (≤ 20 s) `wpr` session with `WinML.wprp` while running the probe.
Needs: Administrator elevation, the three official capture files (record their
SHA256 first), and consent to keep the raw ETL local-only.
This is the single highest-value local action remaining — it is the only planned
way to turn `UNDERLYING_ROOT_CAUSE = UNVERIFIED` into a confirmed cause.

---

## P2 — repository hygiene (no external dependency)

### N5. Fix Experiment A's manifest (new branch, not a rewrite)
`reports/experiment_a/MANIFEST.sha256` does not validate (C-INT-1). Do **not**
edit the frozen files. Instead, on the Experiment A branch, add a *new* file
`reports/experiment_a/MANIFEST_V2.sha256` computed from the committed blobs, plus
a short note explaining the historical mismatch. That preserves the original
artifact and gives future readers a working integrity anchor.

### N6. Keep the new probes honest
`tools/mission_c_catalog_internals.py` calls one undocumented export
(`WinMLEpCatalogCreateWithMode`). If a future `windowsml` release drops it, the
probe must keep degrading gracefully (it already does: `AttributeError` is caught
and recorded as `export_absent`).

### N7. Feed the A/B tool with future A artifacts
`tools/experiment_b_ab_compare.py` now understands Experiment A's schema. When
Experiment A is re-run for a new gfx target, pass its JSONs via `--a-json` so the
comparison never regresses to `A_EVIDENCE_PENDING`.

---

## P3 — only after a real AMDGPU EP exists on this host

Run the full ladder from the mission, in order, with a new `run_id`:

1. Package discovery → 2. DLL signature → 3. EP registration → 4. EP device
discovery → 5. device selection (**now fail-closed**) → 6. session creation →
7. minimal ONNX GPU execution → 8. CPU numeric parity → 9. YOLO26n detection →
10. PT task oracle → 11. 7-task matrix → 12. 9-model Tier-A → 13. timeout/cancel/
recovery → 14. bounded soak → 15. DML safety regression.

Stop at the first failure; preserve evidence; never advance without authorization.
A `PASS` requires: `actual_ep=AMDGPUExecutionProvider`,
`target_ep_profile_nodes > 0`, `cpu_profile_nodes = 0`, numeric parity,
task oracle, and `receipt_verifier = ALL_OK`.

---

## What NOT to do

| Anti-action | Why |
| --- | --- |
| copy `amdgpu-ep.dll` from the other gfx1151 host | violates AGENTS.md rule 2/6 and the mission's system-safety rules; also produces an unattestable claim |
| install the EP MSIX from an unverified download | signature/provenance cannot be proven |
| replace the HP OEM driver "to see if it helps" | no verifiable incompatibility evidence; no authorization |
| re-run the same failing probe in a loop | produces no information; the blocker is external |
| mark the mission `C_GFX1151_AMDGPU_PHYSICAL_GO` from `get_providers()` or a successful session | `get_providers()` is never proof |
