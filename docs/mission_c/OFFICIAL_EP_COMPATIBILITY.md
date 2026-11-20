# OFFICIAL EP COMPATIBILITY — AMD GPU EP on gfx1151

Machine-readable: [`reports/mission_c/C2_STORE_PACKAGE_ELIGIBILITY.json`](../../reports/mission_c/C2_STORE_PACKAGE_ELIGIBILITY.json),
[`C1_CATALOG_INTERNALS.json`](../../reports/mission_c/C1_CATALOG_INTERNALS.json),
[`C2_DRIVER_COMPATIBILITY.json`](../../reports/mission_c/C2_DRIVER_COMPATIBILITY.json).

---

## 1. Four states, never merged

The mission requires these to stay separate. They are:

| State | Value | How it was established |
| --- | --- | --- |
| `OFFICIAL_MSIX_RELEASED` | **TRUE** | Release matrix: AMD GPU EP added in MSIX `1.8.65.0`, released 2026-10-28. The signed runtime binary's own catalogue table contains `AMDGPUExecutionProvider`. |
| `OFFICIAL_PACKAGE_TARGETED_TO_HOST` | **UNVERIFIED** | `0x1586` is not in `UnsupportedDeviceIDs` and vendor `0x1002` is supported, but no entry is produced. Targeting rules are undocumented. |
| `OFFICIAL_PACKAGE_ACQUIRABLE_ON_HOST` | **FALSE (measured)** | 0 providers in every accepted catalogue mode; `FindProvider` = `0x80070490`; no EP MSIX in current-user or machine scope; 0 AMD GPU EP deployment events. |
| `OFFICIAL_INDEPENDENT_BYO_ARTIFACT_AVAILABLE` | **FALSE** | Official BYO page: `onnxruntime-ep-amdgpu` must be built from source. |

The important distinction: **"Microsoft published the EP" and "this device can get
the EP" are different facts.** PR #14 conflated them; this Mission separates
them.

## 2. Catalogue metadata recovered from the signed binary

Source: `windowsml` 2.5.96's
`Microsoft.Windows.AI.MachineLearning.dll`
(SHA256 `8205a572b36423860a7a584e67d6c4f2dd2ece497ab8a3c82129c114fb6d7be0`,
byte-identical on the gfx1100 host).

28 provider entries. AMD GPU EP entries:

| PackageFamilyName | MinimumPackageVersion | Delivery | ProductID |
| --- | --- | --- | --- |
| `Microsoft.WinML.AMD.GPU.EP.2_8wekyb3d8bbwe` | 1.8.64.0 | Store product | `9NMRQLX4HLSK` |
| `Microsoft.WinML.AMD.GPU.EP.Framework.2_8wekyb3d8bbwe` | 1.8.64.0 | Store product | `9PNKGVD4CPCV` |

`9PNKGVD4CPCV` resolves publicly to **"Windows ML Runtime AMD GPU Execution
Provider Framework 2"**
(<https://apps.microsoft.com/detail/9PNKGVD4CPCV>), which independently confirms
that the product is real and that the `Framework.2` package family installed on
the gfx1100 host came from the Store.

### Device filter analysis

```
SupportedVendorIDs    : ["0x1002"]      <- AMD, matches this host
UnsupportedDeviceIDs  : ~80 legacy DIDs <- 0x1586 NOT present
SupportedDeviceKinds  : ["GPU"]
```

**Conclusion:** the published metadata does not exclude the Radeon 8060S. The
empty catalogue cannot be explained by a documented device blacklist.

## 3. Documented requirements vs this host

| Requirement (Microsoft) | This host | Verdict |
| --- | --- | --- |
| Windows ML ≥ 2.5.96 | `windowsml` 2.5.96 | ✅ met |
| Windows 11 24H2 (build 26100) or greater | 25H2 `26200.9974` | ✅ met |
| Adrenalin Edition 24.12.1 or later | driver `32.0.22018.5` (WDDM) — **cannot be mapped to an Adrenalin marketing version** | `DRIVER_COMPATIBILITY_UNVERIFIED` |
| (no published gfx/GPU list) | gfx1151 / `0x1586` | not constrained by any published list |

## 4. Why the driver is *not* the leading explanation

1. DirectML executes on this exact driver right now: canary with 1 Dml node,
   `allclose=True`, `max_abs_diff=2.86e-06`.
2. `WebGpuExecutionProvider` — documented requirement "any DirectX 12-capable
   GPU" — is also absent. A GPU-driver compatibility filter cannot remove an EP
   whose only requirement the machine visibly meets.
3. The AMD EP never reaches a load attempt, so no driver-level error exists to
   interpret.

Hence: driver stays `UNVERIFIED` and **no driver change was made**.

## 5. The `custom_kernels_gfx1151.dll` question

The 1.8.65.0 package (as installed on the gfx1100 host) contains
`custom_kernels_gfx1151.dll`
(`a3488ccd4213a3e6800fac6ef9988b8f537eb84899de3ced23f44ac2d16b9f84`).

What it **does** prove:

* the 1.8.65.0 build ships gfx1151-targeted kernel code, i.e. gfx1151 is a known
  compilation target for this EP;
* it is strong support for H8-type "compatibility exists" claims.

What it **does not** prove:

* that the 0x1586 device is targeted by the Store/catalogue delivery rules;
* that the kernels run correctly on this specific machine (no EP here);
* anything about this host's blocked state.

The decisive compatibility evidence is behavioural, and it is cross-host: on a
**different** gfx1151 machine (`SUBSYS_17141043`, driver `32.0.22032.6002`)
`amdgpu-ep.dll` 1.8.65.0 executed with positive AMDGPU profiler nodes and passing
task parity. That evidence is recorded as context and **never transplanted** onto
this host (AGENTS.md rule 2).

## 6. What would change the verdict

| Event | Consequence |
| --- | --- |
| Catalogue lists `AMDGPUExecutionProvider` (`NotPresent` is enough) | call `ensure_ready_async()` → verify package signature/hash → register → run the C5 ladder |
| Microsoft states the device is not targeted + gives a supported acquisition path | provisioning can proceed under owner authorization |
| Microsoft fixes/changes targeting so the product reaches `0x1586` | same as row 1 |
| Official signed BYO redistributable appears | BYO path becomes legitimate |
