# AMDGPU EP missing — read-only investigation and provisioning plan

Date: 2026-11-19 (Asia/Shanghai)  
Host: HP ZBook Ultra G1a 14, AMD Ryzen AI MAX+ PRO 395, Radeon 8060S (gfx1151)  
Scope: **read-only**. No driver, Windows component, MSIX, or system package was
installed or changed during this investigation. The only write was downloading a
Python wheel to a temporary folder for inspection (not installed/registered).

## 1. Observed state (verified on this host)

| Item | Observed value | Evidence |
| --- | --- | --- |
| OS | Windows 11 25H2, build `26200.9974` | `HOST_FINGERPRINT.json` / `Get-ItemProperty` |
| GPU | AMD Radeon(TM) 8060S Graphics, `PCI\VEN_1002&DEV_1586`, DXGI LUID `0x00000000:0x000154E4` | DXGI + PnP probe |
| AMD driver | `32.0.22018.5`, driver date `2025-09-17` | `Win32_VideoController` |
| Windows ML shared runtime | `WindowsWorkload.WinMLShared.7.0` `1.2610.1050.0` | `Get-AppxPackage` |
| Windows App Runtime | `2.6.5.0`, `2.7.0.0` (+ 1.7/1.8/CBS) | `Get-AppxPackage` |
| Installed EP package | `WindowsWorkload.EP.AMD.VitisAI.Framework.1.8` `1.8.80.0` (AMD **NPU** EP, 1.8-era) | `Get-AppxPackage` |
| AMD GPU EP (`amdgpu-ep.dll`) | **not found** in site-packages or `C:\Program Files\WindowsApps` | `windows_ml_ep_catalog.scan_amdgpu_ep_dll()` → `[]` |
| ORT in `.venv-winml` | `onnxruntime-windowsml 1.25.2.202605110140`, providers `DmlExecutionProvider`, `CPUExecutionProvider` | `pip list`, `ort.get_available_providers()` |
| Legacy Python projection | `wasdk-Microsoft.Windows.AI.MachineLearning 2.3.0` + bootstrap 2.3.0 | `pip list`, module file |
| Official Python bindings | `windowsml==2.5.96` | **installed 2026-11-19** (A1, `.venv-winml` and `.venv-r4`); catalog still empty | `pip list`, `PROBE_AFTER_A1.json` |
| Catalog (`wasdk` 2.3.0) | `find_all_providers()` → **0 providers** | live probe |
| Catalog (`windowsml` 2.5.96 flat C API) | `EpCatalog.find_all_providers()` → **0 providers** | wheel imported from temp via `PYTHONPATH` (no install) |
| EP devices visible to ORT | CPU + DML only (DML `device_id=0`) | `ort.get_ep_devices()` |
| `register_execution_provider_library` | unavailable to register: no DLL exists | `register_attempt` = `amdgpu-ep.dll not found (BLOCKED_EP)` |

The `0 providers` result is not a Python-projection artifact: **both** the old
`wasdk` projection and the official 2.5.96 flat C bindings return an empty list.
The OS-level EP catalog is genuinely empty for this host today.

## 2. Microsoft's documented requirements (re-verified 2026-11-19)

Sources fetched live on 2026-11-19 from the docs source branch
`MicrosoftDocs/windows-ai-docs`:

* <https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/supported-execution-providers>
  (page `ms.date` 2026-04-28)
* <https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/initialize-execution-providers>
  (page `ms.date` 2026-05-30)
* <https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/register-execution-providers>
  (page `ms.date` 2026-02-13)

### 2.1 What the live page actually lists (WinML 2.x tab)

| EP | EpName | Documented requirement |
| --- | --- | --- |
| MIGraphX (AMD) | `MIGraphXExecutionProvider` | "GPU with version 25.10.13.09 (exactly)"; not supported for GenAI |
| VitisAI (AMD) | `VitisAIExecutionProvider` | Adrenalin 25.6.3..25.9.1 with NPU driver 32.00.0203.280..297 (AMD **NPU**) |
| NvTensorRtRtx | `NvTensorRtRtxExecutionProvider` | RTX 30xx+, driver 32.0.15.5585+ |
| OpenVINO | `OpenVINOExecutionProvider` | Intel CPU/GPU/NPU driver minima |
| QNN | `QNNExecutionProvider` | Snapdragon X Elite/Plus |

The page states that only the **current** version of each EP is supported and
that updates arrive through Windows Update optional non-security preview
("D week") releases. Included (not downloaded) EPs are CPU and DirectML.

### 2.2 R3-D correction: no `AMDGPUExecutionProvider` requirement exists in the live table

An earlier revision of this document asserted that the page listed
`AMDGPUExecutionProvider` with "Windows ML 2.5.96+ / Adrenalin 24.12.1+".
That claim is **not reproducible** against the live page on 2026-11-19:

- the string `AMDGPU` does not appear in the page at all (0 matches);
- `2.5.96` and `24.12` do not appear either (0 matches);
- the AMD GPU row is MIGraphX with an **exact** driver requirement of
  25.10.13.09; the AMD NPU row is VitisAI.

Consequences applied to this repository:

1. The MIGraphX 25.10.13.09 minimum is **not** a documented requirement of any
   `AMDGPUExecutionProvider` and must not be used as one; it is recorded only
   as the MIGraphX EP's own requirement.
2. No driver minimum can be asserted for a provider the catalog does not list.
   Windows WDDM driver version (32.0.22018.5 here) and the AMD
   software/Adrenalin numbering do not share a scale, so a numeric comparison
   is not evidence of compatibility in either direction.
3. The Python package version (`windowsml==2.5.96`) is a **binding** version,
   not a catalog/EP version. Installing it cannot create an EP entry the OS does
   not offer.
4. `runner.probe_amdgpu_ep` records `AMDGPUExecutionProvider` as "NOT listed
   in the live catalog table on 2026-11-19" in its `ep_reference` block, next
   to the MIGraphX/VitisAI facts and their source URL, instead of silently
   importing the old claim.

## 3. Why does the catalog return an empty list?

Verified facts:

1. The OS has the Windows ML shared runtime (2.7-era framework package) and
   Windows App Runtime 2.6/2.7, but **no AMD GPU EP MSIX**.
2. The only installed AMD EP is `WindowsWorkload.EP.AMD.VitisAI.Framework.1.8`
   (NPU, 1.8-era). The 2.x catalog does not surface this 1.8 package on this
   host. It is not an AMD **GPU** EP and cannot run the AMDGPU session.
3. Both catalog implementations (legacy projection and official 2.5.96 C API)
   enumerate zero providers, including zero `NotPresent` entries. On a
   compatible device the documented behavior is to list non-installed EPs with
   `ReadyState=NotPresent`, so an empty list means the OS currently offers **no
   catalog entry** for this device.
4. No `amdgpu-ep.dll` exists, so even a manual `register_execution_provider_library`
   call cannot work.

Assessment (fail-closed, no guess presented as fact):

* **BLOCKED_EP is correct.** The AMD GPU EP is not installed and the catalog
  cannot offer it.
* The empty catalog is consistent with (a) no 2.x AMD GPU EP package being
  provisioned for this device, and (b) the AMD driver not meeting the documented
  minimum so the EP is filtered as incompatible. Which filter dominates is
  **UNKNOWN** from read-only telemetry; the provisioning plan below is designed
  to distinguish them.
* The legacy Python projection 2.3.0 is below the documented 2.5.96 minimum, but
  it is **not the sole cause**: the official 2.5.96 bindings also return 0.
  Upgrading Python alone will not install the EP.

## 4. Dynamic install vs BYO EP

### 4.1 Official dynamic install (preferred)

Flow: official Python bindings (`windowsml>=2.5.96`) or
`Microsoft.WindowsAppSDK.ML` / `Microsoft.Windows.AI.MachineLearning >= 2.5.96`
→ `ExecutionProviderCatalog.find_all_providers()` → find
`AMDGPUExecutionProvider` → `ensure_ready()` / `EnsureReadyAsync()` → Windows
downloads and installs the EP MSIX through Windows Update → register the
returned `library_path` with ONNX Runtime.

Applicability here: **only after** the catalog lists the provider. Today it does
not, so `ensure_ready()` cannot even be addressed. Prerequisites are the OS/driver
compatibility filter and the provider package being offered by Windows Update.

Important: `catalog.EnsureAndRegisterCertifiedAsync()` **cannot register EPs
into the Python ORT environment**; the Microsoft Python docs explicitly say not
to use that API for Python registration. The supported Python path is
`provider.ensure_ready_async().get()` then
`ort.register_execution_provider_library(provider.name, provider.library_path)`.

### 4.2 BYO EP (fallback / controlled environments)

Flow: obtain the official AMD GPU EP MSIX, verify its Microsoft signature,
extract `ExecutionProvider\amdgpu-ep.dll` (plus its dependent HIP/DirectX
libraries), and register that DLL path with
`ort.register_execution_provider_library("AMDGPU", path)`.

Applicability here: technically possible **if** an official, correctly signed
2.x EP package exists for gfx1151. It does not remove the driver prerequisite,
and it moves a system component into a manually managed folder. It is therefore
a separately approved fallback, not a read-only workaround. Do **not** copy a
1.8 package's DLL or a DLL from another machine and label it a 2.x EP.

## 5. Provisioning plan

### Phase 0 — read-only checks (no approval needed)

```powershell
# Windows optional updates / driver state
Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,DriverDate
Get-AppxPackage *WinML*,*AMD*,*MachineLearning* | Select-Object Name,Version

# Catalog with the officially supported bindings (only if installed later)
.venv-winml\Scripts\python -m runner.preflight --gfx gfx1151
```

Record a new `run_id`; do not reuse any PR #8 run directory.

### Phase 1 — Python binding upgrade (no admin; explicit approval requested)

Action: `pip install windowsml==2.5.96` into `.venv-winml` (Python-only; no
system component, no driver). Then run `find_all_providers()` and record the
result. This does not install an EP.

Why: the repo currently uses the legacy 2.3.0 projection; the official Python
surface for 2.5.96 is `windowsml`. This is required to call `ensure_ready()`.

### Phase 2 — AMD GPU driver update (admin + reboot; explicit approval required)

Action: update the AMD graphics driver to **25.10.13.09 or later** (or the
current recommended WHQL release) through HP/AMD official channels. Record the
old/new `DriverVersion` and `DriverDate`. Reboot.

Why: documented AMDGPU EP minimum. Without it, dynamic install may remain
filtered and BYO may fail at HIP initialization. This is a system change and is
**not** performed by this mission.

### Phase 3 — official dynamic EP install (system-wide; explicit approval required)

Action: with the 2.5.96+ bindings, enumerate providers, and only if
`AMDGPUExecutionProvider` is present call `ensure_ready()`. Verify the installed
package and `amdgpu-ep.dll` hash; then register it with ORT.

Why: keeps Microsoft's certified package/servicing path. Requires network and
Windows Update; may require elevation depending on the package.

### Phase 4 — BYO EP fallback (system component; explicit approval required)

Action: only if Phase 3 cannot be used in this environment, obtain the official
signed AMD GPU EP MSIX, verify signature/hash, extract it to a controlled
folder, and register the DLL. Record the package identity, version, signature
and SHA256 in a new preflight evidence directory.

Why: allows offline/managed deployment, but is operationally riskier and must
not bypass the driver requirement.

### Phase 5 — gfx1151 rerun (no approval needed once EP is present)

Use a new `run_id`, `--gfx gfx1151`, `--ep amdgpu,dml`, and the strict contract
path. The AMDGPU receipts must show positive `AMDGPU` profiler nodes, zero CPU
nodes, CPU-ORT by-name parity, and a task oracle; `get_providers()` alone is
never proof.

## 6. Approval checklist (nothing below has been executed)

| # | Action | Privilege | Risk | Approval needed |
| --- | --- | --- | --- | --- |
| A1 | `pip install windowsml==2.5.96` in `.venv-winml` | user | **DONE 2026-11-19** (no EP appeared) | n/a |
| A2 | Update AMD GPU driver to >= 25.10.13.09 | admin + reboot | medium/high (system driver) | yes |
| A3 | Official dynamic EP install via `ensure_ready()` | may require elevation/Windows Update | medium (system-wide MSIX) | yes |
| A4 | BYO EP MSIX extraction/registration | admin/controlled folder | medium/high (unsigned-copy risk if mishandled) | yes |
| A5 | Any Windows optional-update / servicing change | admin | medium | yes |

Until A1–A5 are explicitly approved, the only correct verdict remains
`AMDGPU: BLOCKED_EP`. No AMDGPU PASS is claimed, and PR #8's historical
receipts are untouched.

## 7. What would change the verdict

A successful provisioning must produce, on a new `run_id`:

1. `find_all_providers()` contains `AMDGPUExecutionProvider` (Ready or
   NotPresent), and after install `amdgpu-ep.dll` exists with a recorded hash;
2. `ort.get_ep_devices()` lists an `AMDGPU` device;
3. a session's profiler attributes >0 nodes to `AMDGPU` and 0 to CPU;
4. CPU-ORT by-name parity and a task oracle pass;
5. strict receipt validation and run-contract verification both pass.

If only (1)/(2) are true, the verdict is still not a GPU PASS.

## 8. Current status (2026-11-19, after A1)

### 8.1 A1 executed: official bindings installed, catalog still empty

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

The empty list is not a Python-projection artifact: the **official** 2.5.96 C
API and the **legacy** 2.3.0 projection both answer successfully with zero
providers, and the OS has no AMD GPU EP package. Installing the binding cannot
change what the OS catalog offers.

### 8.2 Windows Update state (A5 check, read-only)

A `Microsoft.Update.Session` search on 2026-11-19 returned exactly one
optional driver update (`Microsoft Corporation AudioProcessingObject Driver
Update`) and one Defender definition update. No AMD display driver and no
Windows ML EP package is offered to this device. No update is paused, no reboot
is pending.

### 8.3 Why A2/A3/A4 were not executed

- **A2 (driver)**: no documented AMDGPU EP requirement exists to compare against
  (section 2.2); the installed driver is the HP OEM package (`oem21.inf`,
  32.0.22018.5, 2025-09-17); Windows Update offers nothing; a driver change
  would not create a catalog entry. Replacing a working OEM display driver
  without a proven blocker would violate the "minimal change" rule.
- **A3 (catalog install)**: impossible - `find_all_providers()` has no
  `AMDGPUExecutionProvider` (or MIGraphX/VitisAI) entry to call
  `ensure_ready()` on. There is nothing to download.
- **A4 (BYO signed EP)**: no official, signed 2.x AMD GPU EP MSIX artifact is
  available on this host or from a verifiable Microsoft channel; the only
  artifact referenced in this repo's history belongs to a different machine and
  a different (1.8-era) package. Importing it would violate AGENTS.md rule 2 and
  the mission's own A4 conditions. Recorded `BLOCKED_OFFICIAL_EP_ARTIFACT`.

### 8.4 Verdict

`R4_BLOCKED_EXTERNAL` - the official Windows ML 2.x API surface is healthy
(bindings installed, catalog callable, S_OK), the host meets the OS/runtime
prerequisites, but no AMD GPU execution provider is offered or installed. No
AMDGPU PASS is claimed and no AMD GPU execution was attempted.

What would change it: the OS catalog listing an AMD GPU EP
(`ReadyState=NotPresent` is enough to call `ensure_ready()`), or an official
signed EP MSIX for this OS generation. See section 7 for the acceptance chain.
