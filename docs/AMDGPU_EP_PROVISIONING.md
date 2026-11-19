# AMDGPU EP missing — read-only investigation and provisioning plan

Date: 2026-11-19 (Asia/Shanghai)
Host: HP ZBook Ultra G1a 14, AMD Ryzen AI MAX+ PRO 395, Radeon 8060S (gfx1151)
Scope: **read-only**. No driver, Windows component, MSIX, or system package was
installed or changed during this investigation. The only writes are local
Python packages (A1, user scope) and repository evidence files.

> **R10-A revision (2026-11-19).** Section 2 was re-verified against the
> *published* Microsoft Learn page. The previous revision (R3-D) had concluded
> that `AMDGPUExecutionProvider` was not documented at all; that conclusion came
> from a **stale docs-branch markdown snapshot**, not from the live page, and is
> now **superseded** (it is preserved verbatim in §2.2 for history). The host
> verdict is unchanged: the catalog on this machine is still empty, so
> `AMDGPU: BLOCKED_EP` / `BLOCKED_EXTERNAL` still stands (§8). Correcting the
> documentation does not upgrade any evidence.

## 1. Observed state (verified on this host)

| Item | Observed value | Evidence |
| --- | --- | --- |
| OS | Windows 11 25H2, build `26200.9974` | `HOST_FINGERPRINT.json` / `Get-ItemProperty` |
| GPU | AMD Radeon(TM) 8060S Graphics, `PCI\VEN_1002&DEV_1586`, DXGI LUID `0x00000000:0x000154E4` | DXGI + PnP probe |
| AMD driver | `32.0.22018.5`, driver date `2025-09-17` | `Win32_VideoController`, DXGI UMD version |
| Windows ML shared runtime | `WindowsWorkload.WinMLShared.7.0` `1.2610.1050.0` | `Get-AppxPackage` |
| Windows App Runtime | `2.6.5.0`, `2.7.0.0` (+ 1.7/1.8/CBS) | `Get-AppxPackage` |
| Installed EP package | `WindowsWorkload.EP.AMD.VitisAI.Framework.1.8` `1.8.80.0` (AMD **NPU** EP, 1.8-era) | `Get-AppxPackage` |
| AMD GPU EP (`amdgpu-ep.dll`) | **not found** in site-packages or `C:\Program Files\WindowsApps` | `windows_ml_ep_catalog.scan_amdgpu_ep_dll()` -> `[]` |
| ORT in `.venv-winml` | `onnxruntime-windowsml 1.25.2.202605110140`, providers `DmlExecutionProvider`, `CPUExecutionProvider` | `pip list`, `ort.get_available_providers()` |
| Legacy Python projection | `wasdk-Microsoft.Windows.AI.MachineLearning 2.3.0` + bootstrap 2.3.0 | `pip list`, module file |
| Official Python bindings | `windowsml==2.5.96` | **installed 2026-11-19** (A1, `.venv-winml` and `.venv-r4`); catalog still empty |
| Catalog (`wasdk` 2.3.0) | `find_all_providers()` -> **0 providers** | live probe |
| Catalog (`windowsml` 2.5.96 flat C API) | `EpCatalog.find_all_providers()` -> **0 providers** | live probe + `repro/windowsml_amdgpu_catalog_probe.py` |
| Raw catalog HRESULTs | `WinMLEpCatalogCreate` = `0x00000000`, `WinMLEpCatalogEnumProviders` = `0x00000000` | `reports/mission_r10/PROBE_*.json` |
| EP devices visible to ORT | CPU + DML only (DML `device_id=0`) | `ort.get_ep_devices()` |
| `register_execution_provider_library` | unavailable to register: no DLL exists | `register_attempt` = `amdgpu-ep.dll not found (BLOCKED_EP)` |
| EP acquisition channel | **Microsoft Store** (Windows ML >= 2.5.96) | §2.1, `reports/mission_r10/PROBE_*.json:update_state` |

The `0 providers` result is not a Python-projection artifact: **both** the old
`wasdk` projection and the official 2.5.96 flat C bindings return an empty list,
and the raw HRESULTs are `S_OK`. The OS-level EP catalog is genuinely empty for
this host today.

## 2. Microsoft's documented requirements

### 2.1 Current, verified against the published page (R10-A, 2026-11-19)

Source (published page, **not** a raw source file):

* <https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/supported-execution-providers>
  - page footer "Last updated on **2026-11-18**"
  - fetched 2026-11-19T13:52:29Z (2026-11-19 21:52 Asia/Shanghai)
  - sha256 of the fetched body:
    `67b29c82899ef522791f482da5f1b6845a6d86f3a02eb43ec71e745a27fdb013`
  - byte-stable across two independent fetches (identical hash)
* Markdown source `main` branch (corroboration, same content, `ms.date`
  `11/18/2026`):
  <https://raw.githubusercontent.com/MicrosoftDocs/windows-ai-docs/main/docs/new-windows-ml/supported-execution-providers.md>
  - sha256 `306bb2472107fb804412ae03c95025c42f73da95ff730f6b8a705e5193d34e71`

What that page actually says (verbatim where quoted):

| EP | EpName | Documented requirement (quoted) |
| --- | --- | --- |
| **AMD GPU** | `AMDGPUExecutionProvider` | "Minimum required Microsoft.Windows.AI.MachineLearning version: **2.5.96**"; "**Adrenalin Edition 24.12.1 or later**" |
| MIGraphX (**Deprecated**) | `MIGraphXExecutionProvider` | "AMD RDNA 3 or later GPU"; "AMD GPU driver version **25.10.13.09 or later**"; "This execution provider is not supported for GenAI scenarios today." |
| VitisAI (AMD NPU) | `VitisAIExecutionProvider` | "Adrenalin Edition **25.6.3 or later** with NPU driver version **32.00.0203.280 or later**" |
| NvTensorRtRtx | `NvTensorRtRtxExecutionProvider` | RTX 30xx+ with driver 32.0.15.5585+ and CUDA 12.5 |
| OpenVINO | `OpenVINOExecutionProvider` | Intel Windows ML support guidance |
| QNN | `QNNExecutionProvider` | Snapdragon X Elite/Plus, Hexagon NPU driver 30.0.140.0+ |
| WebGPU (experimental) | `WebGpuExecutionProvider` | "Any DirectX 12-capable GPU with up-to-date drivers" |

Additional statements on the same page:

* The dynamically downloaded EPs (including AMD GPU) are "available on Windows 11
  PCs running version **24H2 (build 26100) or greater** (depending on device and
  driver compatibility) for dynamic download via the Windows ML
  `ExecutionProviderCatalog` APIs."
* "**MIGraphXExecutionProvider is deprecated in Windows ML 2.x. AMD recommends
  transitioning to AMDGPUExecutionProvider for all AMD GPU devices.**
  MIGraphXExecutionProvider will be retired from the Windows ML API interface
  with a new Windows ML major release."
* The `AMDGPUExecutionProvider` row is listed **only** under the "Windows ML
  2.x" tab; the "Windows ML 1.8.x" tab (same page) lists MIGraphX (not marked
  deprecated) and no AMD GPU EP.
* The page states **no** gfx target list for `AMDGPUExecutionProvider`. Whether
  gfx1151 (RDNA 3.5, `0x1586`) is covered cannot be read from this page; that is
  one of the open questions in
  [`docs/mission_r10/UPSTREAM_ISSUE_DRAFT_EN.md`](mission_r10/UPSTREAM_ISSUE_DRAFT_EN.md).

Acquisition channel (different page, same revision level):

* <https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/execution-provider-errors>
  (last updated 2026-11-07, sha256 `5cda77adde0d59d4336fdee130ace28bbb33ce69c003f0d47245ef2438204730):
  "With Windows ML **2.5.96 or greater**, execution providers are acquired
  through the **Microsoft Store**. ... There are currently no known issues
  acquiring execution providers in Windows ML 2.5.96 and greater."
  The Windows Update pause / pending-reboot / managed-device troubleshooting on
  that page applies to **2.4.89 and earlier**.
* Release/support matrix (linked from the Learn page as "Windows ML Execution
  Provider Releases"):
  <https://github.com/microsoft/WindowsML/wiki/Windows-ML-Execution-Provider-Releases>
  (raw markdown sha256 `153355aae9bdf7f407d77fe5873849489cb52d964ad74177694bf516a473e6b4).
  It records for "AMD GPU and MIGraphX (AMD)": current MSIX `1.8.65.0`
  (released 2026-10-28, "**AMD GPU EP added**"), upcoming `2.2611.67.0` /
  GPU EP `7.15.2611.126` (GA 2026-12-01), and that "the AMD GPU EP is only
  compatible with Windows ML 2.5.96 or greater".

All three citations, with per-URL access timestamps and hashes, are recorded
machine-readably in `reports/mission_r10/DOC_CITATIONS.json`; the raw fetched
bodies are in `reports/mission_r10/_raw/`.

### 2.2 SUPERSEDED — the R3-D reading (kept for history, do not cite)

The previous revision of this document stated:

> "An earlier revision of this document asserted that the page listed
> `AMDGPUExecutionProvider` with 'Windows ML 2.5.96+ / Adrenalin 24.12.1+'.
> That claim is **not reproducible** against the live page on 2026-11-19:
> the string `AMDGPU` does not appear in the page at all (0 matches);
> `2.5.96` and `24.12` do not appear either (0 matches); the AMD GPU row is
> MIGraphX with an **exact** driver requirement of 25.10.13.09; the AMD NPU row
> is VitisAI."

**That finding was wrong, and the way it was produced is the actual defect.**
It was not read from the published page. It was read from the raw markdown on the
**`docs` branch** of the docs repository:

* <https://raw.githubusercontent.com/MicrosoftDocs/windows-ai-docs/docs/docs/new-windows-ml/supported-execution-providers.md>
  - `ms.date: 04/28/2026`
  - sha256 `8fdc3cf4bcfb5021c868c5fa4a557df68167933600c846b10b1530439a7b3404`

That file matches the R3-D description exactly (0 × `AMDGPU`, 0 × `2.5.96`,
0 × `24.12`, and `ms.date: 04/28/2026` is precisely the "page ms.date
2026-04-28" the R3-D text quoted). It is a **seven-month-old snapshot**, not the
live page: it also lacks the WebGPU row, the Windows ML CG EP section, and the
MIGraphX deprecation notice, and it still describes EP updates as arriving
through Windows Update "D week" releases.

Concrete differences between the two sources (same path, different branch):

| Aspect | `docs` branch (stale) | `main` branch == published page |
| --- | --- | --- |
| `ms.date` | `04/28/2026` | `11/18/2026` (footer "Last updated on 2026-11-18") |
| `AMDGPU` mentions | 0 | 4 |
| `2.5.96` mentions | 0 | 2 |
| `Adrenalin 24.12` | absent | "Adrenalin Edition 24.12.1 or later" |
| AMD GPU section | absent | "AMD GPU (AMD)" section with EpName/min version |
| MIGraphX status | plain row | "MIGraphX (Deprecated)" + deprecation note |
| WebGPU / CG EP sections | absent | present |
| MIGraphX MSIX version | `1.8.55.0` (2026 4D) | `1.8.65.0` (2026-10-28) |
| Status | **do not cite** | **current public fact** |

The published page (the one a user sees) matches `main`, not `docs`. Both the
HTML and the `main` markdown were fetched and hashed on 2026-11-19 and agree.

### 2.3 Consequences applied to this repository

1. `AMDGPUExecutionProvider` **is** documented for Windows ML 2.x, with
   `Microsoft.Windows.AI.MachineLearning >= 2.5.96` and **Adrenalin Edition
   24.12.1 or later**. `docs/AMDGPU_EP_PROVISIONING.md`, the `EP_REFERENCE`
   block of `runner/probe_amdgpu_ep.py`, and the README status wording now say
   so, each carrying the source URL, access timestamp and body hash.
2. The **25.10.13.09** minimum belongs to **MIGraphX**, not to the AMD GPU EP,
   and MIGraphX is deprecated. Quoting it as the AMD GPU EP requirement was
   wrong and is corrected here.
3. No driver minimum can be **verified** from read-only telemetry: Windows WDDM
   driver version (`32.0.22018.5`) and the AMD software/Adrenalin numbering do
   not share a scale. The driver date (`2025-09-17`) is later than the
   2024-12 Adrenalin 24.12.1 release, which is corroborating but not proof, and
   the installed driver is an HP OEM package.
4. The Python package version (`windowsml==2.5.96`) is a **binding** version.
   Installing it cannot create a catalog entry the OS does not offer.
5. The repository's earlier judgement is **annotated and superseded, not
   deleted**: §2.2 keeps the R3-D text, the wrong source URL and the reason it
   was wrong, and `runner/probe_amdgpu_ep.py` carries the same note in
   `EP_REFERENCE_SUPERSEDES`.

## 3. Why does the catalog return an empty list?

Verified facts:

1. The OS has the Windows ML shared runtime (2.7-era framework package) and
   Windows App Runtime 2.6/2.7, but **no AMD GPU EP MSIX**
   (`MicrosoftCorporationII.WinML.AMD.GPU.EP.1.8` is absent; the release matrix
   shows `1.8.65.0` as the current AMD GPU EP build).
2. The only installed AMD EP is `WindowsWorkload.EP.AMD.VitisAI.Framework.1.8`
   (NPU, 1.8-era). The 2.x catalog does not surface this 1.8 package on this
   host. It is not an AMD **GPU** EP and cannot run the AMDGPU session.
3. Both catalog implementations (legacy projection and official 2.5.96 C API)
   enumerate zero providers, including zero `NotPresent` entries, while the raw
   HRESULTs are `S_OK`. On a compatible device the documented behavior is to
   list non-installed EPs with `ReadyState=NotPresent`, so an empty list means
   the OS currently offers **no catalog entry** for this device.
4. No `amdgpu-ep.dll` exists, so even a manual
   `register_execution_provider_library` call cannot work.

Assessment (fail-closed, no guess presented as fact):

* **BLOCKED_EP is correct.** The AMD GPU EP is not installed and the catalog
  cannot offer it. `S_OK` from the catalog API is **not** proof that AMDGPU is
  usable; only `EP_REGISTERED` after registration and `GPU_EXECUTED` after a
  real GPU canary would be.
* The empty catalog is consistent with (a) no AMD GPU EP package being
  provisioned/offered for this device through the Microsoft Store, and (b) a
  device/driver compatibility filter inside Windows ML. Which one dominates is
  **UNKNOWN** from read-only telemetry. Note that the same empty list also omits
  `WebGpuExecutionProvider`, whose documented requirement is only "any
  DirectX 12-capable GPU"; that is one more data point suggesting the list is
  filtered before it reaches the driver-compatibility check, but it is an
  observation, not a proof.
* The legacy Python projection 2.3.0 is below the documented 2.5.96 minimum, but
  it is **not the sole cause**: the official 2.5.96 bindings also return 0.
  Upgrading Python alone will not install the EP.

## 4. Dynamic install vs BYO EP

### 4.1 Official dynamic install (preferred)

Flow: official Python bindings (`windowsml>=2.5.96`) or
`Microsoft.WindowsAppSDK.ML` / `Microsoft.Windows.AI.MachineLearning >= 2.5.96`
-> `ExecutionProviderCatalog.find_all_providers()` -> find
`AMDGPUExecutionProvider` -> `ensure_ready_async()` -> the EP package is
acquired (Microsoft Store for Windows ML >= 2.5.96; Windows Update for earlier
versions) -> register the returned `library_path` with ONNX Runtime.

Applicability here: **only after** the catalog lists the provider. Today it does
not, so `ensure_ready_async()` cannot even be addressed. Prerequisites are the
provider package being offered to this device and the OS/driver compatibility
filter passing.

Important: `catalog.EnsureAndRegisterCertifiedAsync()` **cannot register EPs
into the Python ORT environment**; the Microsoft Python docs explicitly say not
to use that API for Python registration. The supported Python path is
`provider.ensure_ready_async().get()` then
`ort.register_execution_provider_library(provider.name, provider.library_path)`.

### 4.2 BYO EP (fallback / controlled environments)

Flow: obtain the official AMD GPU EP package, verify its Microsoft signature,
extract `ExecutionProvider\amdgpu-ep.dll` (plus its dependent HIP/DirectX
libraries), and register that DLL path with
`ort.register_execution_provider_library("AMDGPU", path)`.

The official BYO page
(<https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/bring-your-own-eps>,
last updated 2026-11-10, sha256 `b71117180358241fca9b9ef1c9a1ba417effd3a9d7f6035081cd62982220d55f)
lists the AMDGPU EP's known package as
`onnxruntime/onnxruntime-ep-amdgpu` and notes it "**must be built from
source**", i.e. there is no Microsoft-published redistributable for the Windows
ML AMDGPU EP that this host could simply download. BYO also does not remove the
driver prerequisite, and it moves a system component into a manually managed
folder. It is therefore a separately approved fallback, not a read-only
workaround. Do **not** copy a 1.8 package's DLL or a DLL from another machine and
label it a 2.x EP.

## 5. Provisioning plan

### Phase 0 — read-only checks (no approval needed)

```powershell
# Windows optional updates / driver state
Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,DriverDate
Get-AppxPackage *WinML*,*AMD*,*MachineLearning* | Select-Object Name,Version

# catalog + HRESULTs + readiness, no session created
.venv-winml\Scripts\python -m runner.probe_amdgpu_ep --out reports\mission_r10\PROBE_<UTC>.json --gfx gfx1151
# compare against the previous probe (new/gone providers, version drift, HRESULT change)
.venv-winml\Scripts\python -m runner.probe_amdgpu_ep --out reports\mission_r10\PROBE_<UTC>.json --gfx gfx1151 --diff reports\mission_r10\<previous>.json
```

Record a new `run_id`; do not reuse any PR #8 run directory.

### Phase 1 — Python binding upgrade (no admin; DONE)

Action: `pip install windowsml==2.5.96` into `.venv-winml` (Python-only; no
system component, no driver). Then run `find_all_providers()` and record the
result. This does not install an EP. **Executed 2026-11-19 (A1); the catalog is
still empty.**

### Phase 2 — establish whether the EP package is offered at all

The documented driver requirement for the AMD GPU EP is "Adrenalin Edition
24.12.1 or later" (§2.1). The installed package is `32.0.22018.5` dated
2025-09-17, which is *later* than the 24.12.1 release window, but the WDDM and
Adrenalin version scales differ and HP OEM packaging can lag. A driver update is
therefore **not** the first action: the AMD GPU EP package
(`MicrosoftCorporationII.WinML.AMD.GPU.EP.1.8`, current build `1.8.65.0`) is not
installed at all, and Windows ML 2.5.96+ acquires EPs through the Microsoft
Store. The next step is to establish, through Microsoft/AMD, whether that package
is targeted at this device (see the escalation draft). Changing a working OEM
display driver on a hunch violates the "minimal change" rule.

### Phase 3 — official dynamic EP install (system-wide; explicit approval required)

Action: with the 2.5.96+ bindings, enumerate providers, and only if
`AMDGPUExecutionProvider` is present call `ensure_ready_async()`. Verify the
installed package and `amdgpu-ep.dll` hash; then register it with ORT.

Why: keeps Microsoft's certified package/servicing path. Requires network and
elevation depending on the package.

### Phase 4 — BYO EP fallback (system component; explicit approval required)

Action: only if Phase 3 cannot be used, build or obtain the official signed AMD
GPU EP, verify signature/hash, and register the DLL from a controlled folder.
Record the package identity, version, signature and SHA256 in a new preflight
evidence directory.

Why: allows offline/managed deployment, but it is operationally riskier, has no
Microsoft-published binary, and must not bypass the driver requirement.

### Phase 5 — gfx1151 rerun (no approval needed once EP is present)

Use a new `run_id`, `--gfx gfx1151`, `--ep amdgpu,dml`, and the strict contract
path. The AMDGPU receipts must show positive `AMDGPU` profiler nodes, zero CPU
nodes, CPU-ORT by-name parity, and a task oracle; `get_providers()` alone is
never proof.

## 6. Approval checklist

| # | Action | Privilege | Risk | Approval needed |
| --- | --- | --- | --- | --- |
| A1 | `pip install windowsml==2.5.96` in `.venv-winml` | user | **DONE 2026-11-19** (no EP appeared) | n/a |
| A2 | Update AMD GPU driver (documented requirement: Adrenalin 24.12.1+) | admin + reboot | medium/high (system driver) | yes — only after the EP package is proven to be offered |
| A3 | Official dynamic EP install via `ensure_ready_async()` | may require elevation / Store sign-in | medium (system-wide MSIX) | yes |
| A4 | BYO EP build/extraction/registration | admin/controlled folder | medium/high (unsigned-copy risk if mishandled) | yes |
| A5 | Any Windows optional-update / servicing change | admin | medium | yes |

Until the EP package is offered and A3/A4 are explicitly approved, the only
correct verdict remains `AMDGPU: BLOCKED_EP`. No AMDGPU PASS is claimed, and
PR #8's historical receipts are untouched.

## 7. What would change the verdict

A successful provisioning must produce, on a new `run_id`:

1. `find_all_providers()` contains `AMDGPUExecutionProvider` (Ready or
   NotPresent), and after install `amdgpu-ep.dll` exists with a recorded hash;
2. `ort.get_ep_devices()` lists an `AMDGPU` device;
3. a session's profiler attributes >0 nodes to `AMDGPU` and 0 to CPU;
4. CPU-ORT by-name parity and a task oracle pass;
5. strict receipt validation and run-contract verification both pass.

If only (1)/(2) are true, the verdict is still not a GPU PASS.

## 8. Status history

### 8.1 R4 (2026-11-19): A1 executed, catalog still empty

Command (user scope, no admin, no system component):

    .venv-winml\Scripts\python.exe -m pip install "windowsml==2.5.96"

Result: `windowsml 2.5.96` installed; `onnxruntime-windowsml` unchanged at
`1.25.2.202605110140`.

Read-only re-probe (`runner.probe_amdgpu_ep --out reports/mission_r4/PROBE_AFTER_A1.json`),
state machine v2:

| Signal | Observed |
| --- | --- |
| `state` | **CATALOG_EMPTY** |
| `verdict` / `blocked_status` | `BLOCKED_EP` / `BLOCKED_EXTERNAL` |
| official API `WinMLEpCatalogCreate` / `EnumProviders` | `0x00000000` / `0x00000000` (S_OK) |
| official catalog providers | 0 |
| legacy 2.3.0 projection | `api_ok=true`, 0 providers |
| ORT providers / EP devices | `DmlExecutionProvider`, `CPUExecutionProvider` only |
| AMD GPU EP DLLs / packages | none |
| OS build | 26200 (>= 26100, requirement met) |
| Windows ML shared runtime | `WindowsWorkload.WinMLShared.7.0 1.2610.1050.0` |
| Windows App Runtime | 1.7 / 1.8 / 2.7 present |
| only AMD EP package | `WindowsWorkload.EP.AMD.VitisAI.Framework.1.8 1.8.80.0` (NPU, not GPU) |

`reports/mission_r4/` is immutable evidence and was **not** modified by R10.
R10 re-probed the same host (`reports/mission_r10/PROBE_*.json`) and
`--diff` against `reports/mission_r4/PROBE_AFTER_A1.json` reports
"no observable change (same state, catalog, HRESULTs and versions)".

### 8.2 Windows Update state (A5 check, read-only)

R4: a `Microsoft.Update.Session` search on 2026-11-19 returned exactly one
optional driver update (`Microsoft Corporation AudioProcessingObject Driver
Update`) and one Defender definition update. No AMD display driver and no
Windows ML EP package is offered to this device. No update is paused, no
servicing reboot is pending. (R10 confirmed the same registry state: no
`RebootRequired`/`RebootPending`/`PackagesPending` keys, no WU policy
restrictions, Store package present.)

Caveat added by R10: for Windows ML **2.5.96+** the EP acquisition channel is the
**Microsoft Store**, so a Windows Update search is no longer the decisive
diagnostic it was for 2.4.89 and earlier. `runner.probe_amdgpu_ep` now records
the channel it applies (`update_state.ep_acquisition_channel`).

### 8.3 Why A2/A3/A4 were not executed (as of R4)

* **A2 (driver)**: at R4 time the documentation reading was wrong (§2.2). With
  the corrected facts the requirement is "Adrenalin 24.12.1 or later", the
  installed driver is dated 2025-09-17, and Windows Update offers no AMD display
  driver. The EP package itself is not installed and is not offered, so a driver
  change cannot create a catalog entry. Replacing a working OEM display driver
  without a proven blocker would violate the "minimal change" rule.
* **A3 (catalog install)**: impossible — `find_all_providers()` has no
  `AMDGPUExecutionProvider` (or MIGraphX/VitisAI) entry to call
  `ensure_ready_async()` on. There is nothing to download.
* **A4 (BYO signed EP)**: the official BYO page says the AMDGPU EP
  "must be built from source"; no Microsoft-published, signed 2.x AMD GPU EP
  redistributable is available on this host or from a verifiable channel. The
  `1.8.65.0` package identity referenced in this repository's history belongs to
  another machine; importing it would violate AGENTS.md rule 2 and the mission's
  A4 conditions. Recorded `BLOCKED_OFFICIAL_EP_ARTIFACT`.
* **A5 (servicing)**: nothing to change; the runtime prerequisites are met.

### 8.4 Verdict

`R4_BLOCKED_EXTERNAL` (unchanged by R10) — the official Windows ML 2.x API
surface is healthy (bindings installed, catalog callable, `S_OK`), the host meets
the documented OS/runtime prerequisites, but no AMD GPU execution provider is
offered or installed. No AMDGPU PASS is claimed and no AMD GPU execution was
attempted.

What would change it: the catalog listing an AMD GPU EP
(`ReadyState=NotPresent` is enough to call `ensure_ready_async()`), or an
official signed EP package for this OS generation. See section 7 for the
acceptance chain, and
[`docs/mission_r10/R10_AMDGPU_EP_ESCALATION_FINAL_CN.md`](mission_r10/R10_AMDGPU_EP_ESCALATION_FINAL_CN.md)
for the escalation state.

## 9. Citations (R10-A, fetched 2026-11-19)

| Page | Role | Last updated | Accessed (UTC / Asia-Shanghai) | sha256 of fetched body |
| --- | --- | --- | --- | --- |
| [supported-execution-providers](https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/supported-execution-providers) | EP support matrix (AMD GPU row) | 2026-11-18 | 2026-11-19T13:52:29Z / 21:52 | `67b29c82…fdb013` |
| [initialize-execution-providers](https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/initialize-execution-providers) | install / ReadyState semantics | 2026-05-30 | 2026-11-19T13:52:29Z / 21:52 | `eded821c…25ed6` |
| [register-execution-providers](https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/register-execution-providers) | ORT registration | 2026-04-24 | 2026-11-19T13:52:29Z / 21:52 | `56a5a3fa…d2b51` |
| [execution-provider-errors](https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/execution-provider-errors) | official EP troubleshooting (Store vs WU) | 2026-11-07 | 2026-11-19T13:52:29Z / 21:52 | `5cda77ad…04730` |
| [bring-your-own-eps](https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/bring-your-own-eps) | official BYO path | 2026-11-10 | 2026-11-19T13:52:29Z / 21:52 | `b7111718…20d55f` |
| [Windows ML EP Releases (wiki)](https://github.com/microsoft/WindowsML/wiki/Windows-ML-Execution-Provider-Releases) | EP release/support matrix | n/a | 2026-11-19T13:52:29Z / 21:52 | `153355aa…a473e6b4` |
| [docs-branch markdown (stale)](https://raw.githubusercontent.com/MicrosoftDocs/windows-ai-docs/docs/docs/new-windows-ml/supported-execution-providers.md) | **superseded source — do not cite** | ms.date 2026-04-28 | 2026-11-19T13:52:29Z / 21:52 | `8fdc3cf4…7b3404` |

Full per-URL metadata (byte counts, text hashes, stability across fetches) is in
`reports/mission_r10/DOC_CITATIONS.json`; the fetched bodies are in
`reports/mission_r10/_raw/`.
