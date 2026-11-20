# UPSTREAM ISSUE (English, ready to paste)

**Target repository:** `microsoft/WindowsAppSDK` (issues)
**Why this repo:** `microsoft/WindowsML`'s own issue template routes Windows ML
runtime/API problems that are not sample-specific to the WindowsAppSDK issue
tracker. This report is about the Windows ML 2.x runtime API
(`ExecutionProviderCatalog`), not about a sample.

**Title (≤ 120 chars):**

```
[Windows ML 2.x][AMDGPU EP] ExecutionProviderCatalog returns S_OK with zero providers on Radeon 8060S (gfx1151), same runtime binary lists 4 providers on RX 7900 XTX
```

**Suggested labels:** `bug` (if triage prefers, `question` is acceptable — the
core ask includes "what are the authoritative catalogue inputs?")

---

## 1. Problem statement

On a Windows 11 25H2 machine with an AMD Radeon 8060S (gfx1151, PCI DID
`0x1586`), the Windows ML 2.x execution-provider catalogue returns **S_OK with
zero providers**. Because `EnsureReadyAsync()` can only be called on a provider
object, there is no supported way to acquire *any* execution provider on this
device — not the AMD GPU EP, and not even WebGPU or the AMD NPU (VitisAI) EP,
although a VitisAI EP framework package is already installed machine-wide.

The same `windowsml` 2.5.96 runtime binary on an RX 7900 XTX (gfx1100) host
returns **four** providers from the identical API call, so this is a
device/system-input difference inside the runtime, not an SDK or binding defect.

## 2. Expected behaviour

Per the Windows ML documentation, `FindAllProviders()` returns the EPs available
to the user's device *including non-installed ones* with
`ReadyState = NotPresent`:

> "The execution providers returned will vary based on the user's device and
> available execution providers. On a compatible Qualcomm device without any
> execution providers currently installed, the above code outputs
> `QNNExecutionProvider: NotPresent`."
> — <https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/initialize-execution-providers>

Expected on an AMD GPU device that meets the documented prerequisites
(Windows 11 24H2+/26100+, Windows ML ≥ 2.5.96): at minimum
`AMDGPUExecutionProvider` (and `WebGpuExecutionProvider`, whose only documented
requirement is "any DX12-capable GPU") appear in the list, with some
`ReadyState`.

## 3. Actual behaviour

| Probe | Result |
| --- | --- |
| `WinMLEpCatalogCreate` | `S_OK` (`0x00000000`) |
| `WinMLEpCatalogEnumProviders` | `S_OK`, **0 providers** (3/3 runs, multiple processes) |
| `WinMLEpCatalogFindProvider("AMDGPUExecutionProvider", NULL)` | `0x80070490` (element not found) |
| `WinMLEpCatalogFindProvider(..., "Microsoft.WinML.AMD.GPU.EP.Framework.2_8wekyb3d8bbwe")` | `0x80070490` |
| `WinMLEpCatalogFindProvider(..., "MicrosoftCorporationII.WinML.AMD.GPU.EP.1.8_8wekyb3d8bbwe")` | `0x80070490` |
| `WinMLEpCatalogCreateWithMode(mode)` — **undocumented export**, modes 0/1/2 | all `S_OK`, **0 providers** |
| `WinMLEpCatalogCreateWithMode(3/4/5)` | `0x80070057` `E_INVALIDARG` |
| WinAppSDK 2.3 projection (`ExecutionProviderCatalog`) | 0 providers |
| Official `windowsml` 2.5.96 flat-C | 0 providers |

There is **no HRESULT failure anywhere**: the catalog simply materialises no
entry. `ort.get_ep_devices()` lists only `CPUExecutionProvider` and
`DmlExecutionProvider`; `get_available_providers()` is
`DmlExecutionProvider, CPUExecutionProvider`; no `amdgpu-ep.dll` exists anywhere
on disk.

## 4. Reproduction steps

Read-only, no installation, no admin, no GPU session:

```powershell
python -m venv .venv-winml
.\.venv-winml\Scripts\pip install "windowsml==2.5.96" "onnxruntime-windowsml==1.25.2"
.\.venv-winml\Scripts\python tools\mission_c_catalog_internals.py   # prints JSON
```

`tools/mission_c_catalog_internals.py` calls only documented flat-C entry points
plus `WinMLEpCatalogCreateWithMode` behind an `AttributeError` guard. It creates
no inference session and registers no EP.

Reference implementation in the public evidence repo:
<https://github.com/AIwork4me/ultralytics-windows-radeon-ep>
(`tools/mission_c_catalog_internals.py`, `reports/mission_c/C1_CATALOG_INTERNALS.json`).

## 5. Environment

**Affected host (this report)**

| Item | Value |
| --- | --- |
| OS | Windows 11 25H2, build `26200.9974` |
| Machine | HP ZBook Ultra G1a 14, Ryzen AI MAX+ PRO 395 |
| GPU | AMD Radeon(TM) 8060S Graphics, gfx1151 |
| PnP | `PCI\VEN_1002&DEV_1586&SUBSYS_8D01103C&REV_D1` |
| Vendor/Device ID | `0x1002` / `0x1586` |
| Display driver | `32.0.22018.5` (2025-09-17, HP OEM package) |
| Windows ML binding | `windowsml` 2.5.96 (documented minimum) |
| OS WinML runtime | `WindowsWorkload.WinMLShared.7.0` `1.2610.1050.0` |
| ONNX Runtime | `onnxruntime-windowsml` 1.25.2.202605110140 |
| Python | CPython 3.12.10 x64 |
| Privilege | non-admin (Medium integrity) — so no ETW capture is attached |
| Installed EP packages | `WindowsWorkload.EP.AMD.VitisAI.Framework.1.8` 1.8.80.0 (NPU) only; **no AMD GPU EP** |
| Microsoft Store | `Microsoft.WindowsStore` 22610.1401.10.0 present; `AppInstallManager` instantiates; `StoreContext.GetDefault()` succeeds; no Store policy key |

**Comparison host (gfx1100, from a separate A/B experiment)**

OS `26300.9974` (26H2); RX 7900 XTX `0x744C`; driver `32.0.21030.2001`;
`windowsml` 2.5.96; ORT 1.25.2; AMD GPU EP MSIX
`Microsoft.WinML.AMD.GPU.EP.Framework.2` 1.8.65.0 installed.

## 6. gfx1100 vs gfx1151 comparison

| Field | gfx1100 (7900 XTX) | gfx1151 (8060S, this host) |
| --- | --- | --- |
| OS build | 26300.9974 | 26200.9974 |
| Windows ML | 2.5.96 | 2.5.96 |
| ONNX Runtime | 1.25.2 | 1.25.2 |
| Runtime interop DLL SHA256 | `8205a572b36423860a7a584e67d6c4f2dd2ece497ab8a3c82129c114fb6d7be0` | **identical** |
| Catalog create / enum HRESULT | `S_OK` / `S_OK` | `S_OK` / `S_OK` |
| **Providers returned** | **4** | **0** |
| Among them | `AMDGPUExecutionProvider` `NotReady(1)`, `MIGraphXExecutionProvider` `NotReady(1)`, `OpenVINOExecutionProvider` `NotPresent`, `WebGpuExecutionProvider` `NotPresent` | — |
| AMD GPU EP package | 1.8.65.0 installed | absent |
| `amdgpu-ep.dll` SHA256 | `cb7496b3…c0df7f8ab` | absent |
| Direct EP registration | works, 2 AMDGPU profiler nodes, 0 CPU nodes | not possible (no DLL) |

Note that the working host lists **OpenVINO** (an Intel-vendor EP) and **WebGPU**
on an all-AMD machine. That is the basis for thinking the list is *not* simply
"EPs filtered by this device's GPU".

## 7. Native / Python catalog results

| Surface | Providers |
| --- | --- |
| Python flat-C (`windowsml` 2.5.96, `WinMLEpCatalogCreate`+`EnumProviders`) | 0 |
| Python flat-C, `create-with-mode` 0/1/2 | 0 |
| WinAppSDK 2.3 `ExecutionProviderCatalog` projection (bootstrapped) | 0 |
| WinAppSDK 1.8 / 2.6 / 2.7 bootstraps | 0 |
| Unbootstrapped wasdk (control) | fails with "no package identity" (expected control) |

Seven bootstrap surfaces and the flat-C API agree, in separate processes.

## 8. MSIX / DLL provenance

The catalogue metadata is **embedded in the Microsoft-signed runtime binary**
(`Microsoft.Windows.AI.MachineLearning.dll` shipped by `windowsml` 2.5.96,
SHA256 `8205a572…d7be0`). It contains 28 EP entries. The two AMD GPU EP entries
are:

```json
{ "Name": "AMDGPUExecutionProvider",
  "SupportedDeviceKinds": ["GPU"],
  "Packages": [
    { "PackageFamilyName": "Microsoft.WinML.AMD.GPU.EP.2_8wekyb3d8bbwe",
      "MinimumPackageVersion": "1.8.64.0",
      "Product": { "ProductID": "9NMRQLX4HLSK" },
      "SupportedVendorIDs": ["0x1002"],
      "UnsupportedDeviceIDs": [ "0x13C0", "0x1506", ... ] },
    { "PackageFamilyName": "Microsoft.WinML.AMD.GPU.EP.Framework.2_8wekyb3d8bbwe",
      "MinimumPackageVersion": "1.8.64.0",
      "Product": { "ProductID": "9PNKGVD4CPCV" },
      "SupportedVendorIDs": ["0x1002"],
      "UnsupportedDeviceIDs": [ ... ] } ] }
```

Two facts we would like confirmed by the Windows ML team:

1. **`0x1586` is not in either `UnsupportedDeviceIDs` list**, and vendor
   `0x1002` is supported — so the published metadata does not exclude the Radeon
   8060S.
2. `ProductID 9PNKGVD4CPCV` resolves publicly to **"Windows ML Runtime AMD GPU
   Execution Provider Framework 2"** on the Microsoft Store
   (<https://apps.microsoft.com/detail/9PNKGVD4CPCV>), and `9NMRQLX4HLSK` is the
   `EP.2` generation of the same provider.

Also in the same binary: source paths
`src/Microsoft.Windows.AI.MachineLearning/ExecutionProviderCatalogData.cpp`,
`.../ExecutionProviderStore.cpp`, `.../FlatC/WinMLEpCatalogApi.cpp`; and internal
telemetry reason strings

```
FindAllProvidersTest::reason::no_store
FindAllProvidersTest::reason::failure
DownloadExecutionProvidersTest::reason::no_target_package
DownloadExecutionProvidersTest::reason::not_acquirable
DownloadExecutionProvidersTest::reason::not_supported
ExecutionProviderEnsureReadyTest::reason::store_unavailable
GetJsonTest::reason::cancelled
```

plus references to `Windows.ApplicationModel.Store.Preview.InstallControl` and
`DXCoreCreateAdapterFactory`. These appear to be exactly the decision points
that collapse into "S_OK + empty list" at the public API, but the public API
exposes none of them.

## 9. ETW / Event Log findings

**Not available on this host** (non-admin; Windows ML's own guidance requires
Administrator for `wpr`). We have prepared the capture per
<https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/logs>
(`WinML.wprp`, `Get-WinMLRundown.ps1`, `WindowsMLProfile.wpaProfile`) and will
attach a trace if you need one — please say which process/scenario you want
traced.

What read-only logs do show: no AMD GPU EP deployment events, no Store errors,
no pending servicing operations, and DML runs normally on the same driver
(DirectML canary: 1 profiler node, numeric parity `allclose`, `max_abs_diff`
2.86e-06).

## 10. Controls and excluded causes

| Candidate cause | Status | Evidence |
| --- | --- | --- |
| Wrong/old Windows ML SDK | excluded | 2.5.96 = documented minimum; interop DLL byte-identical to the working host |
| Python binding bug | excluded | native flat-C and WinAppSDK projection agree |
| WinRT activation / package identity | excluded | 7 bootstrap surfaces, including identity-free flat-C |
| Missing OS runtime | excluded | `WindowsWorkload.WinMLShared.7.0` present; OS build ≥ 26100 |
| Store app missing / blocked by policy | excluded as sole cause | Store present, `AppInstallManager` + `StoreContext.GetDefault()` succeed, no policy key |
| Device blacklist | excluded | `0x1586` not in `UnsupportedDeviceIDs` |
| GPU driver (DX12) broken | excluded | DML executes on the same driver |
| Our harness | controlled | single purpose-built probe, 3 independent modes, 3 processes |

## 11. Confirmed vs unverified root cause

* **Confirmed (reproducible, proximate blocker):** `ExecutionProviderCatalog`
  materialises **zero** provider entries for this device, across every accepted
  catalogue mode, every public API surface, and a direct `FindProvider` lookup.
  Therefore no official acquisition path (`EnsureReadyAsync`) can even be
  addressed.
* **Unverified (needs Microsoft instrumentation or an answer):** *why* the
  catalogue runtime decides to emit no entry on this device. It is upstream of
  enumeration and mode selection, and the public API does not expose the reason.
* **Not claimed:** that gfx1151 is unsupported by the AMD GPU EP. In fact a
  *different* gfx1151 machine (PnP `SUBSYS_17141043`, driver `32.0.22032.6002`)
  ran the same `amdgpu-ep.dll` 1.8.65.0 with positive AMDGPU profiler nodes and
  passing task parity. That is cross-host context, not a claim about this host.

## 12. Questions for maintainers

1. What are the authoritative inputs used to populate or filter the Windows ML
   2.x `ExecutionProviderCatalog` (Store product query? UUP/workload service?
   OS build? OEM subsystem? driver branch? DXCore adapter attributes?).
2. Why does `VEN_1002&DEV_1586` return zero providers — *including WebGPU* —
   while a gfx1100 machine lists four? Is filtering applied before or after the
   device evaluation?
3. Is the AMD GPU EP package 1.8.65.0 (Store product `9PNKGVD4CPCV`) targeted at
   the Radeon 8060S / `0x1586`? If not, what is the supported acquisition path
   for this device?
4. Is HP OEM driver `32.0.22018.5` on a supported AMD GPU EP driver branch?
   Which Adrenalin release maps to it?
5. Is there a documented way to diagnose a catalogue that returns `S_OK` but
   enumerates zero providers? Is `WinMLEpCatalogCreateWithMode` intended to be
   public, and what do modes 0/1/2 mean?
6. Can Microsoft provide a supported acquisition or recovery procedure for this
   device (e.g. an explicit Store/WU path for the EP framework package)?
7. Which ETW events (of the reason strings above) distinguish "device not
   targeted" from "runtime activation/store failure", so we can capture the
   right trace?
8. If catalogue enumeration is empty, what is the recommended supported
   bring-your-own path — and is a Microsoft-signed AMDGPU EP redistributable
   planned? Today the BYO page says the EP must be built from source.

## 13. Public evidence links

* Evidence package (machine-readable, `MANIFEST.sha256` + 220-check verifier):
  <https://github.com/AIwork4me/ultralytics-windows-radeon-ep/tree/mission/c-gfx1151-amdgpu-root-cause-closure/reports/mission_c>
* Probe source: `tools/mission_c_catalog_internals.py` in the same repository
* Comparison experiment (gfx1100, PR #15):
  <https://github.com/AIwork4me/ultralytics-windows-radeon-ep/pull/15>
* Prior blocked-state experiment (gfx1151, PR #14):
  <https://github.com/AIwork4me/ultralytics-windows-radeon-ep/pull/14>
* Microsoft docs used:
  [supported EPs](https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/supported-execution-providers) ·
  [install EPs](https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/initialize-execution-providers) ·
  [EP download troubleshooting](https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/execution-provider-errors) ·
  [capture logs](https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/logs) ·
  [EP release matrix](https://github.com/microsoft/WindowsML/wiki/Windows-ML-Execution-Provider-Releases)

## 14. Privacy / security notes

No user names, SIDs, tokens, machine serials, absolute user paths, private ETW
traces or licensed driver files are included. All paths in the evidence package
are redacted to `%USERPROFILE%` / `C:/Program Files/WindowsApps/...`; the
package is machine-checked for leak patterns before publication. The host is
identified by a salted `host_id` and by public hardware identifiers only.

---

## Posting checklist (internal, not part of the issue)

- [x] target repo verified (`microsoft/WindowsAppSDK` issues; WindowsML routes here)
- [x] duplicate search: none found
- [x] no account/SID/token/serial/personal path/private ETL
- [x] key numbers recomputable from public receipts
- [x] confirmed vs unverified root cause labelled
- [x] tone: factual, blameless
- [x] publish permission available (`gh auth status` = `AIwork4me`, `repo` scope)
