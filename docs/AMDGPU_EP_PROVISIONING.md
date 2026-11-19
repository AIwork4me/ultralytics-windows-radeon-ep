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
| Official Python bindings | `windowsml==2.5.96` wheel inspected in a temp dir; **not installed** | PyPI metadata; wheel contents |
| Catalog (`wasdk` 2.3.0) | `find_all_providers()` → **0 providers** | live probe |
| Catalog (`windowsml` 2.5.96 flat C API) | `EpCatalog.find_all_providers()` → **0 providers** | wheel imported from temp via `PYTHONPATH` (no install) |
| EP devices visible to ORT | CPU + DML only (DML `device_id=0`) | `ort.get_ep_devices()` |
| `register_execution_provider_library` | unavailable to register: no DLL exists | `register_attempt` = `amdgpu-ep.dll not found (BLOCKED_EP)` |

The `0 providers` result is not a Python-projection artifact: **both** the old
`wasdk` projection and the official 2.5.96 flat C bindings return an empty list.
The OS-level EP catalog is genuinely empty for this host today.

## 2. Microsoft's documented requirements (checked 2026-11-19)

Sources:

* <https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/supported-execution-providers>
* <https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/initialize-execution-providers>
* <https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/register-execution-providers>
* <https://pypi.org/project/windowsml/2.5.96/>
* <https://www.nuget.org/packages/Microsoft.Windows.AI.MachineLearning/2.5.96>

AMD GPU EP (`AMDGPUExecutionProvider`) requirements:

| Requirement | Documented minimum | This host | Status |
| --- | --- | --- | --- |
| OS | Windows 11 24H2 (build 26100) or later | 25H2 build 26200.9974 | **meets** |
| GPU | AMD RDNA 3 or later | gfx1151 / RDNA 3.5 (8060S) | **meets** |
| `Microsoft.Windows.AI.MachineLearning` | **2.5.96** (2026-10-04) | legacy projection 2.3.0; official 2.5.96 wheel not installed | **does not meet via legacy path** |
| AMD GPU driver | **25.10.13.09 or later** | `32.0.22018.5` (2025-09-17) | **UNKNOWN / not demonstrated** |
| EP package | dynamically installed AMD GPU EP MSIX (`AMDGPUExecutionProvider`) | no AMD GPU EP package; only AMD **NPU** VitisAI 1.8 | **BLOCKED_EP** |

The driver version reported by Windows (`32.0.22018.5`) and the AMD software
version in the docs (`25.10.13.09`) use different numbering schemes; Windows
does not expose the AMD software version in WMI/DXGI. Therefore driver
compliance cannot be proven from the current telemetry. It is not asserted
either way here.

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
| A1 | `pip install windowsml==2.5.96` in `.venv-winml` | user | low (Python env only) | yes |
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

## 8. Current status (2026-11-19 re-probe)

`runner.probe_amdgpu_ep` re-ran read-only after the hardening work:

- `verdict`: `BLOCKED_EP`
- `blocked_status`: `BLOCKED_ADMIN_DEPENDENCY`
- `admin_dependency`: `true`
- `amdgpu_dlls`: `[]`
- `catalog_provider_count`: `{"wasdk_projection": 0, "windowsml_flat_c": 0}`

No driver, MSIX, Windows optional update or Python package was installed. The
required approval actions are A1–A4 above; without them AMDGPU remains blocked.
