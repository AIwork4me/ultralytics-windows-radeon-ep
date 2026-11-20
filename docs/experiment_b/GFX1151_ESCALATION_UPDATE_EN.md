# Escalation update — AMDGPU EP on gfx1151 (Radeon 8060S): catalog offers zero providers

Status: **OPEN / BLOCKED_EXTERNAL**. This updates the R10 draft
(`docs/mission_r10/UPSTREAM_ISSUE_DRAFT_EN.md`) with the Experiment B evidence:
three-repeat catalog reproduction, a seven-surface catalog matrix, dual-scope MSIX
inventory, deployment-event analysis, driver metadata and an official-artifact probe.

- Repository: `AIwork4me/ultralytics-windows-radeon-ep`
- Run ID: `B_GFX1151_AMDGPU_20261120T0336Z`
- Evidence: `reports/experiment_b/` (manifest: `reports/experiment_b/MANIFEST.sha256`)

## Host

| Field | Value |
| --- | --- |
| System | HP ZBook Ultra G1a 14, Ryzen AI MAX+ PRO 395 |
| GPU | AMD Radeon(TM) 8060S Graphics, `PCI\VEN_1002&DEV_1586&SUBSYS_8D01103C&REV_D1`, gfx1151 (RDNA 3.5) |
| DXGI | LUID `0x00000000:0x000154E4` (boot-local; adapter enumerated twice by DXGI) |
| OS | Windows 11 25H2 `10.0.26200.9974` |
| Display driver | HP OEM `32.0.22018.5` (2025-09-17), INF `oem21.inf` from `u0419920.inf`, banner `2510081301-25.20.18-250917a-419920C-HP`, signed by Microsoft Windows Hardware Compatibility Publisher |
| Windows ML | `windowsml 2.5.96` (Python, official flat C bindings), `WindowsWorkload.WinMLShared.7.0 1.2610.1050.0` |
| ONNX Runtime | `onnxruntime-windowsml 1.25.2` → `DmlExecutionProvider`, `CPUExecutionProvider` |

## Symptom (reproducible, 3/3)

```text
WinMLEpCatalogCreate            -> 0x00000000 (S_OK)
WinMLEpCatalogEnumProviders     -> 0x00000000 (S_OK)
find_all_providers() count      -> 0
```

The empty result is **not** a binding artefact. Seven catalog surfaces were probed, each in
its own process:

| Surface | Result |
| --- | --- |
| `wasdk` projection, no bootstrap | `0x80073CF4` "process has no package identity" |
| `wasdk` projection, bootstrap 1.7 | `AttributeError`: no 0-arg overload on this Windows version |
| `wasdk` projection, bootstrap 1.8 / 2.3 / 2.6 / 2.7 | usable, **0 providers** each |
| official `windowsml` 2.5.96 flat C API | usable, **0 providers** |

Not a single provider is listed — not `AMDGPUExecutionProvider`, not `MIGraphXExecutionProvider`,
not `VitisAIExecutionProvider` (whose framework package **is installed** on this host), and not
`WebGpuExecutionProvider` (documented requirement: "any DirectX 12-capable GPU").

## What is installed vs. absent (dual-scope, registry-backed)

| Scope | Source | Total packages | AMD GPU EP |
| --- | --- | --- | --- |
| current user | `Get-AppxPackage` + HKCU AppModel repository | 129 / 140 | **0** |
| machine | HKLM `Appx\AppxAllUserStore\Applications` | 74 | **0** |

- `amdgpu-ep.dll`: 0 hits across site-packages, per-user WindowsApps and machine WindowsApps globs.
  (The account cannot list `C:\Program Files\WindowsApps` directly, so the finding rests on the
  AppX registry stores, which are the authority AppX itself uses.)
- The only AMD EP present is `WindowsWorkload.EP.AMD.VitisAI.Framework.1.8 1.8.80.0` — an **NPU**
  package; it cannot serve an AMDGPU session and is not counted as one.
- Deployment logs (2464 relevant events, two logs): `VitisAI` 137, `WinMLShared` 112,
  **`AMD.GPU`/`AMDGPU` 0**, `amdgpu` 0. The AMD NPU framework was registered on 2026-11-18
  16:25–16:43, i.e. **the Windows Workload EP delivery path works on this host; it simply never
  delivered an AMD GPU EP package.**

## Official statements that matter

- `AMDGPUExecutionProvider` is documented for Windows ML 2.x with "Minimum required
  Microsoft.Windows.AI.MachineLearning version: 2.5.96" and "**Adrenalin Edition 24.12.1 or later**".
  No gfx/DID list is published anywhere on that page.
- WindowsML wiki: "**The AMD GPU EP and MIGraphX EP are shipped in the same package**, but the AMD
  GPU EP is only compatible with Windows ML 2.5.96 or greater, and was added in the 2026-10-28
  release." Current MSIX `1.8.65.0` (2026-10-28), "compatible with Windows ML 2.x and 1.8.x".
  The next build (`2.2611.67.0`, GPU EP `7.15.2611.126`) is Insiders-only with a 2026-12-01 GA plan
  and is therefore **not** a currently obtainable condition.
- Windows ML ≥ 2.5.96 acquires EPs through the **Microsoft Store**; the troubleshooting page for
  earlier versions (Windows Update pause / pending reboot) does not apply here. This host has no
  Store-side acquisition error, no pending reboot, and Store/AppX/WU services are not disabled.
- BYO page: the AMDGPU EP "**must be built from source**". The official repository
  `onnxruntime/onnxruntime-ep-amdgpu` (MIT) has **0 releases and 0 release assets**; the NuGet
  packages `onnxruntime-ep-amdgpu` and `Microsoft.ML.OnnxRuntime.EP.AmDGpu` both return **404** and
  the NuGet search returns 0 hits. There is no Microsoft-signed Windows redistributable to import.

## Questions for Microsoft and AMD

**Microsoft (Windows ML / Store targeting)**

1. What is the enumeration source of `ExecutionProviderCatalog` on Windows ML ≥ 2.5.96, and what
   device/driver inputs decide whether a provider appears for a given DID?
2. Why does a fully patched 25H2 host with the documented SDK minimum, a working shared runtime and
   an installed AMD NPU EP framework see **zero** providers — including `WebGpuExecutionProvider`,
   whose only documented requirement is a DX12-capable GPU?
3. Is `Microsoft.WinML.AMD.GPU.EP.Framework.*` (1.8.65.0) targeted at `VEN_1002&DEV_1586`
   (Radeon 8060S / Strix Halo), and through which channel (Store vs Windows Update)?
4. If an EP package is installed out-of-band, should the 2.x catalog list it with
   `ReadyState=NotPresent`, or is a catalog entry exclusively server-side?

**AMD**

5. Does the AMDGPU EP (as shipped in MSIX 1.8.65.0) include gfx1151 kernels, and what is the
   officially supported gfx list? The page documents no gfx targets at all.
6. Which Adrenalin release corresponds to OEM WDDM `32.0.22018.5` / INF build `25.20.18`? The
   documented minimum is expressed in Adrenalin marketing versions, which cannot be derived from
   WDDM or INF strings.
7. Is an HP-OEM-packaged display driver (signed by WHCP, INF banner `...-419920C-HP`) supported for
   the AMDGPU EP, or is a retail AMD driver required?

## What would close this

Either: the catalog lists `AMDGPUExecutionProvider` for this DID (any `ReadyState`), or an
officially signed AMDGPU EP artifact with a published gfx1151 statement becomes obtainable.
On that day the local acceptance chain is fixed and already implemented:

```text
physical_device = VERIFIED_GFX1151
actual_ep       = AMDGPUExecutionProvider
target_ep_profile_nodes > 0
cpu_profile_nodes        = 0
numeric_parity           = PASS      (by output name vs CPU ORT)
task_parity              = PASS_TASK (independent PyTorch oracle)
receipt_verifier         = ALL_OK
```

Nothing weaker is reported as a GPU PASS. In particular, this build of the ONNX Runtime Python
binding **silently falls back to CPU** when a provider name is unknown
(`EP Error Unknown Provider Type … Falling back to ['CPUExecutionProvider']`), so a created
session is not evidence of execution; only provider identity plus profiler node counts are.

## Not claimed

- gfx1151 is unsupported. (No evidence; the documented architecture floor for the deprecated
  MIGraphX EP is met.)
- The HP OEM driver is the cause. (No evidence; DirectML passes 9/9 Tier-A on the same driver.)
- A newer Windows ML SDK would fix it. (The SDK cannot create a catalog entry.)
