# ROOT CAUSE ANALYSIS — gfx1151 / Radeon 8060S AMDGPU EP

**Verdict**

```
CONFIRMED_PROXIMATE_BLOCKER = CATALOG_EMPTY
UNDERLYING_ROOT_CAUSE       = UNVERIFIED
```

Machine-readable counterpart: [`reports/mission_c/C6_ROOT_CAUSE_MATRIX.json`](../../reports/mission_c/C6_ROOT_CAUSE_MATRIX.json)
(validated by `tools/verify_mission_c_evidence.py`, 245 checks, `ALL_OK`).

---

## 1. What "confirmed" means here

Confirmed = reproduced with independent controls, and the negative control
distinguishes the hypothesis from the alternative.

| Observation | Controls | Independent reproduction |
| --- | --- | --- |
| `WinMLEpCatalogCreate` → `S_OK` | documented minimum binding 2.5.96 | 3 processes |
| `WinMLEpCatalogEnumProviders` → `S_OK`, 0 entries | same binary returns 4 on gfx1100 | 3 processes + `runner.preflight` |
| `WinMLEpCatalogCreateWithMode(0/1/2)` → `S_OK`, 0 entries | modes 3..5 → `E_INVALIDARG` (proves the mode range is real) | one process per mode |
| `WinMLEpCatalogFindProvider("AMDGPUExecutionProvider", PFN)` → `0x80070490` | 2 official PFNs + no PFN | 3 lookups |
| WindowsAppSDK 2.3 projection → 0 | unbootstrapped control fails differently ("no package identity") | separate process |
| No `amdgpu-ep.dll` anywhere | `Get-AppxPackage` both scopes | repeated |
| Store API alive | `AppInstallManager` instance + `StoreContext.GetDefault()` | in-session |

The empty catalogue is therefore not an artefact of one binding, one process, or
one HRESULT path.

## 2. What is *not* confirmed, and why we say so

The runtime's own decision inputs are not exposed by any public API. The binary
references `Windows.ApplicationModel.Store.Preview.InstallControl`,
`DXCoreCreateAdapterFactory`, and carries per-decision telemetry strings
(`FindAllProvidersTest::reason::no_store`, `...::failure`,
`DownloadExecutionProvidersTest::reason::no_target_package`, `not_acquirable`, …),
but:

* reading them requires ETW, which requires **Administrator** on this host
  (the session is Medium integrity, Administrators is deny-only);
* `WinMLEpCatalogCreateWithMode` — an export clearly intended to select a
  catalogue behaviour — is **not declared in the published header**, so its
  semantics are unknown;
* the Microsoft Store targeting rules for the AMD GPU EP (`9PNKGVD4CPCV`) are
  not documented anywhere public.

Therefore the honest label is `UNDERLYING_ROOT_CAUSE = UNVERIFIED`, escalated as
a factual question to Microsoft (issue #6994).

## 3. Hypothesis ledger

| ID | Hypothesis | Status | Confidence | Decisive evidence |
| --- | --- | --- | --- | --- |
| H1 | SDK / runtime activation broken or too old | `REFUTED` | HIGH | 2.5.96 = documented minimum; interop DLL byte-identical to the host that returns 4 |
| H2 | Python projection artefact | `REFUTED` | HIGH | flat-C and WinAppSDK projection both 0; ctypes hits the DLL directly |
| H3 | WinRT package identity / bootstrap | `REFUTED` | HIGH | 7 bootstrap surfaces; identity-free flat-C also 0 |
| H4 | Catalogue metadata source declines to emit an entry | `SUPPORTED_HYPOTHESIS` | MEDIUM | embedded Store/UUP table + internal reason strings + `0x80070490` |
| H5 | Store targeting / flighting excludes this device | `SUPPORTED_HYPOTHESIS` | MEDIUM | acquisition is Store-based ≥ 2.5.96; nothing offered |
| H6 | EP MSIX acquisition is the root cause | `REFUTED` (as cause) | HIGH | acquisition cannot even be addressed; absence is downstream |
| H7 | HP OEM driver incompatible | `UNVERIFIED` | LOW | DML runs on it; WebGPU (DX12-only) also absent |
| H8 | gfx1151 device compatibility unsupported | `SUPPORTED_HYPOTHESIS` | MEDIUM | `0x1586` not blacklisted; cross-host gfx1151 execution succeeded |
| H9 | DLL ABI / dependency mismatch | `REFUTED` | HIGH | no DLL here; same DLL loaded fine on another gfx1151 host |
| H10 | GPU kernel / operator coverage | `REFUTED` (as blocker) | HIGH | failure precedes any kernel; cross-host kernels ran |

## 4. Causal chain (as far as the evidence goes)

```
app calls WinMLEpCatalogCreate            -> S_OK            (confirmed)
  runtime loads embedded catalogue table  -> 28 entries      (confirmed by static extraction)
  runtime evaluates per-device eligibility
      inputs: DXCore adapters, Store/UUP products, OS build, package state
      (undocumented; not observable without ETW)             (UNVERIFIED)
  -> entry list is EMPTY for DID 0x1586   -> 0 providers     (confirmed)
WinMLEpCatalogFindProvider(AMDGPU)        -> 0x80070490      (confirmed)
=> ensure_ready_async() has no object to act on              (confirmed)
=> no EP package is acquired, no amdgpu-ep.dll exists        (confirmed)
=> no AMDGPU session; physical validation stays NOT_RUN      (confirmed)
```

Everything above the `UNVERIFIED` line is reproducible on any Windows 11 host
with the probe; everything below it is fully determined by that single step.

## 5. Why this is still a valuable engineering result

1. It converts a vague "the AMDGPU EP doesn't work on 8060S" into a precise,
   reproducible API-level statement with controls that exclude the five most
   common false explanations.
2. It recovers the *authoritative catalogue metadata* (28 entries, Store product
   IDs, vendor/device-ID filters) from the signed binary, which lets anyone check
   targeting claims instead of guessing — and shows `0x1586` is not blacklisted.
3. It separates "gfx1151 kernel support" from "catalogue delivery", which are
   routinely conflated. The cross-host execution evidence settles the former;
   the empty catalogue is the latter.
4. It fixes two real harness defects that were producing misleading results
   (A/B comparison never imported A; a pinned LUID could silently select a
   different physical GPU).

## 6. Responsibility boundary

| Layer | Owner | State |
| --- | --- | --- |
| Harness correctness (fail-closed selection, A/B import, contract codes) | this repository | **fixed** |
| Catalogue materialisation decision for DID `0x1586` | Microsoft (Windows ML / Store) | **escalated** (#6994) |
| gfx1151 kernel/operator coverage in the EP build | AMD | cross-host evidence positive; no local claim |
| OEM driver qualification (`32.0.22018.5`) | HP + AMD | `UNVERIFIED`, no change made |
