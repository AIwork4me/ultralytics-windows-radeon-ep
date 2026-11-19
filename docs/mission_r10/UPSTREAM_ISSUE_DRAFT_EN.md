# Windows ML 2.x: Empty ExecutionProviderCatalog on Radeon 8060S (gfx1151) despite successful catalog initialization

**Status: `ESCALATION_DRAFT_READY` — this issue has NOT been posted.**
Drafted 2026-11-19 from a read-only investigation on a single physical host.

---

## Summary

On this Radeon 8060S host, Windows ML 2.x returns an **empty
`ExecutionProviderCatalog` despite successful API initialization**.

Concretely: with the official `windowsml` 2.5.96 Python bindings,

* `WinMLEpCatalogCreate` returns **`S_OK`** (`0x00000000`),
* `WinMLEpCatalogEnumProviders` returns **`S_OK`** (`0x00000000`),
* and the enumeration yields **zero entries** — not a single provider, not even
  one with `ReadyState = NotPresent`.

The documented behavior is that `FindAllProviders()` returns "all EPs (including
non-installed EPs) available to the user's device", so a compatible device should
at minimum see the relevant EP names with `ReadyState = NotPresent`. Here the
list is completely empty, so there is no provider object to call
`EnsureReadyAsync()` on and the AMDGPU path cannot be started at all.

This report is deliberately scoped to **this host**. It does **not** claim that
all AMD GPUs are affected, and it does **not** assert whether Windows ML, the AMD
driver, or device targeting in the Microsoft Store is responsible — establishing
that is the purpose of the questions at the end.

## Environment

| Item | Value |
| --- | --- |
| OS | Windows 11 Pro 25H2, build **26200.9974** (UBR 9974) |
| CPU / platform | AMD Ryzen AI MAX+ PRO 395 (Strix Halo APU) |
| GPU | AMD Radeon(TM) 8060S Graphics, `PCI\VEN_1002&DEV_1586&SUBSYS_8D01103C&REV_D1` |
| GPU architecture | `gfx1151` (RDNA 3.5, integrated) |
| Display driver (WDDM) | **32.0.22018.5**, dated **2025-09-17**, HP OEM package |
| DXGI | LUID `0x00000000:0x000154E4`; adapters enumerate as two identical 8060S entries + "Microsoft Basic Render Driver" |
| Windows ML shared runtime | `WindowsWorkload.WinMLShared.7.0` **1.2610.1050.0** |
| Windows App Runtime | 1.7 (`7000.785.2325.0`), 1.8 (`8000.994.2142.0`), 2.7 (`2.7.0.0`) |
| Python bindings | `windowsml` **2.5.96** (PyPI, user scope) |
| ONNX Runtime | `onnxruntime-windowsml` **1.25.2.202605110140**; providers = `DmlExecutionProvider`, `CPUExecutionProvider` |
| Installed EP packages | only `WindowsWorkload.EP.AMD.VitisAI.Framework.1.8` `1.8.80.0` (AMD **NPU** EP) |
| AMD GPU EP package | **absent** (`MicrosoftCorporationII.WinML.AMD.GPU.EP.1.8` not installed; no `amdgpu-ep.dll` anywhere under `C:\Program Files\WindowsApps`) |
| Microsoft Store | `Microsoft.WindowsStore` `22610.1401.8.0` (present, not blocked by policy) |
| Windows Update | no policy restrictions, no paused updates, no pending servicing reboot |

Documented prerequisites for `AMDGPUExecutionProvider` (Microsoft Learn,
"Windows ML execution providers", last updated 2026-11-18):

* minimum `Microsoft.Windows.AI.MachineLearning` **2.5.96** — satisfied;
* "Adrenalin Edition **24.12.1 or later**" — the installed WDDM driver is
  32.0.22018.5 dated 2025-09-17 (see question 2 about the version mapping);
* EPs are dynamically available on Windows 11 **24H2 (build 26100) or greater** —
  satisfied (26200).

## Steps to Reproduce

Any of the three, all read-only:

1. Python (official bindings only, no other dependency):

   ```python
   import windowsml
   with windowsml.EpCatalog() as catalog:
       providers = catalog.find_all_providers()
   print(len(providers), [p.name for p in providers])
   ```

2. Raw C entry points through `ctypes` (bypasses the Python wrapper):

   ```python
   import ctypes
   from windowsml import _bindings as B
   handle = B.EpCatalogHandle()
   print(hex(B.WinMLEpCatalogCreate(ctypes.byref(handle)) & 0xFFFFFFFF))
   seen = []
   @B.EpEnumCallback
   def cb(ep, info, ctx):
       seen.append(1); return True
   print(hex(B.WinMLEpCatalogEnumProviders(handle, cb, None) & 0xFFFFFFFF), len(seen))
   ```

3. A single-file standalone reproduction (stdlib + `windowsml` only, no
   repository checkout, no ONNX Runtime required) will be attached; it prints a
   JSON block with HRESULTs, catalog count, per-provider entries, ReadyState,
   `windowsml`/ORT versions, Windows build, GPU DID and driver version.

## Expected Behavior

On a compatible AMD GPU device with Windows ML 2.5.96+ on 24H2+, the catalog
should list the EPs available to the device — at minimum
`AMDGPUExecutionProvider` with `ReadyState = NotPresent` — so that
`EnsureReadyAsync()` can acquire the package and
`RegisterExecutionProviderLibrary("AMDGPU", path)` can register it with ONNX
Runtime.

## Actual Behavior

`find_all_providers()` returns an empty list. Both calls succeed with `S_OK`;
no exception, no extended error, no diagnostic text is produced. There is nothing
to call `EnsureReadyAsync()` on, so the documented install path cannot be
started and the failure is silent.

Notably the empty list also omits `WebGpuExecutionProvider`, whose documented
requirement in the same table is only "any DirectX 12-capable GPU with up-to-date
drivers" — this host has a DX12-capable AMD GPU. We mention it as an observation
that the list appears to be filtered wholesale, not per-vendor; we do not claim
it proves anything.

## Diagnostic Evidence

Raw HRESULTs and counts (captured 2026-11-19T13:50:33Z and 13:50:36Z, identical):

| Signal | Value |
| --- | --- |
| `WinMLEpCatalogCreate` | `0x00000000` (S_OK) |
| `WinMLEpCatalogEnumProviders` | `0x00000000` (S_OK) |
| provider count (wrapper) | **0** |
| provider count (raw callback) | **0** |
| `EpCatalog` exception | none |
| legacy `wasdk-...MachineLearning` 2.3.0 projection | `api_ok = true`, also **0** providers |
| `ort.get_ep_devices()` | `CPUExecutionProvider`, `DmlExecutionProvider` only |
| `amdgpu-ep.dll` found | none (site-packages and `C:\Program Files\WindowsApps`) |
| `WindowsWorkload.WinMLShared` | `7.0 1.2610.1050.0` present |
| Windows build | 26200 (>= 26100) |
| DirectML | works on this host (9/9 physical Tier-A tasks pass via DML), so the GPU, DX12 and ORT are functional |

Two independent catalog implementations (the 2.3.0 WinRT projection and the
2.5.96 flat C API) agree on the empty result, and the raw HRESULTs are `S_OK`,
so this is an OS-level catalog answer, not a Python binding artifact.

## What We Have Ruled Out

* **Broken Python bindings** — the raw `ctypes` calls to the same C entry points
  return `S_OK`; a second, unrelated projection (2.3.0) also returns zero.
* **Too-old OS** — build 26200 > the documented 26100 floor.
* **Missing Windows ML runtime** — `WindowsWorkload.WinMLShared 7.0`
  (`1.2610.1050.0`) and Windows App Runtime 2.7 are installed.
* **A catalog call that fails** — both HRESULTs are `S_OK`; there is no error to
  inspect, which is precisely why an official diagnostic is being requested.
* **Windows Update paused / pending reboot** — no WU policy values are set, and
  none of the `RebootRequired` / `RebootPending` / `PackagesPending` registry
  keys exist. (This check is also only relevant for Windows ML 2.4.89 and
  earlier; 2.5.96+ acquires EPs through the Microsoft Store.)
* **Microsoft Store missing** — `Microsoft.WindowsStore` 22610.1401.8.0 present.
* **A left-over EP DLL that could be registered manually** — no `amdgpu-ep.dll`
  exists on the machine, so `RegisterExecutionProviderLibrary` has nothing to
  load.
* **A GPU/DX12 problem** — DirectML inference works on the same GPU with the same
  ORT build.

We have **not** ruled out, and are not asserting: device targeting of the EP
package in the Microsoft Store, an undocumented driver requirement, an RDNA 3.5 /
integrated-GPU gap in the EP, or a Windows ML catalog filter that needs a
component we have not installed.

## Questions for Maintainers

1. **Is `gfx1151` (Radeon 8060S, integrated RDNA 3.5, DID `0x1586`) a supported
   target for `AMDGPUExecutionProvider` today?** The documentation lists no gfx
   or device matrix for this EP, so support status cannot be determined from it.
2. **Are there undisclosed system or driver prerequisites** (beyond
   `Microsoft.Windows.AI.MachineLearning` 2.5.96 and "Adrenalin 24.12.1 or
   later") that determine whether the AMD GPU EP appears in the catalog at all —
   for example a specific Windows component, a servicing update, a Store
   dependency, or a GPU-generation filter?
3. **What is the official way to diagnose *why* a provider was filtered out of
   the catalog?** When `FindAllProviders()` returns an empty list there is no
   `ExtendedError` and no `DiagnosticText` to read. Is there a logging channel,
   an ETW provider, or a diagnostic API that reports the filter decision?
4. **How does OEM display-driver packaging interact with the AMD GPU EP?** This
   machine runs an HP-provided OEM driver (32.0.22018.5, 2025-09-17). Windows ML
   states the requirement as an Adrenalin Edition number; is an OEM package that
   does not expose an Adrenalin version treated as compatible, and is there a
   supported way to check without replacing the OEM driver?
5. **What is the officially supported BYO deployment path when the catalog is
   unavailable?** The "Bring your own EPs" page lists
   `onnxruntime/onnxruntime-ep-amdgpu` with the note that it "must be built from
   source". Is a prebuilt Microsoft-signed Windows ML AMD GPU EP package
   available to application developers for offline/managed deployment, and is
   building from source the only supported route?
6. **Is there a published EP release / support matrix** that maps EP versions to
   GPU generations and driver ranges? The "Windows ML Execution Provider
   Releases" page lists EP MSIX versions and release dates, but we could not find
   a device/gfx support matrix.

---

### Attachment plan (before posting)

* `windowsml_amdgpu_catalog_probe.py` output (JSON block) — the standalone file
  described in "Steps to Reproduce" item 3.
* A Feedback Hub capture filed under
  **Developer Platform > Windows Machine Learning**, as the official
  troubleshooting page instructs, referenced from this issue.

### Posting status

**NOT POSTED.** No issue, PR, or message has been sent to Microsoft, AMD, or any
upstream repository from this work. Per this project's operating rules, upstream
contact requires the repository owner's explicit decision; this file is the draft
that decision would be based on.

---

## Maintainer-facing note on this document

Language: English (upstream-facing).
Scope discipline: every statement about "no provider" is a statement about
**this host**, verified read-only on 2026-11-19. Where the cause is unknown it is
written as unknown. No component is blamed.
