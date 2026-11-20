# OFFICIAL EP DEPENDENCY MATRIX — AMDGPU EP on Windows ML 2.x

Scope: what the **official** documentation and distribution channels actually require for
`AMDGPUExecutionProvider`, what this host satisfies, and which layer therefore gates it.
Run ID: `B_GFX1151_AMDGPU_20261120T0336Z` (§evidence: `reports/experiment_b/`).

Citation rule: every row points to a fetched URL with access time and sha256 of the raw body
(`OFFICIAL_DOC_CITATIONS.json`). "Docs say X" is never upgraded to "device supports X".

## 1. Layer matrix

| # | Layer | Official requirement (verbatim where quoted) | Source (accessed 2026-11-20) | Observed on this host | Verdict |
| --- | --- | --- | --- | --- | --- |
| L1 | OS | Windows 11 **24H2 (build 26100) or greater** for dynamic download via `ExecutionProviderCatalog` | supported-execution-providers, `ms.date 2026-11-18`, md sha256 `306bb247…` | `26200.9974` (25H2) | **MET** |
| L2 | Windows ML SDK | "Minimum required **Microsoft.Windows.AI.MachineLearning version: 2.5.96**" | same page | `windowsml 2.5.96` + `WinMLShared.7.0 1.2610.1050.0` | **MET** |
| L3 | Python/API surface | supported path: `ExecutionProviderCatalog.find_all_providers()` → `ensure_ready_async()` → `ort.register_execution_provider_library` | initialize-execution-providers (`2026-05-30`, `fc082bf4…`), register-execution-providers (`2026-02-13`, `a1cc2153…`) | 5 usable surfaces enumerate **0** providers, HRESULT `S_OK` | **BROKEN AT CATALOG** |
| L4 | Catalog entry | dynamically downloaded EPs are "available ... for dynamic download via the Windows ML `ExecutionProviderCatalog` APIs" | supported-execution-providers | AMDGPU / WebGPU / VitisAI all absent | **NOT OFFERED** |
| L5 | EP package | "The AMD GPU EP and MIGraphX EP are **shipped in the same package**"; current MSIX `1.8.65.0` (2026-10-28), "compatible with Windows ML 2.x and 1.8.x" | WindowsML wiki, md sha256 `153355aa…` | package absent in HKCU (140) and HKLM (74) scopes | **ABSENT** |
| L6 | EP binary | (`amdgpu-ep.dll` inside the MSIX `ExecutionProvider\` folder) | same | 0 files under 4 glob patterns | **ABSENT** |
| L7 | Driver | "**Adrenalin Edition 24.12.1 or later**" — the only driver statement on the page | supported-execution-providers | WDDM `32.0.22018.5`, INF internal `25.20.18`, Adrenalin **UNKNOWN** | **UNVERIFIED** |
| L8 | GPU target list | **not published** for `AMDGPUExecutionProvider` (no gfx/DID list anywhere on the page) | supported-execution-providers | gfx1151 / `0x1586` — no official statement exists | **UNVERIFIED** |
| L9 | Acquisition channel | "With Windows ML 2.5.96 or greater, execution providers are acquired through the **Microsoft Store**" | execution-provider-errors, `ms.date 2026-11-02`, `bd1380dd…` | Store installed; 0 acquisition errors in 4000 events; WU offers no EP package | **NO OFFER** |
| L10 | BYO | AMDGPU EP "**must be built from source**"; docs link `github.com/onnxruntime/onnxruntime-ep-amdgpu` | bring-your-own-eps `2026-11-10` `10bbdf7b…` + artifact probe | repo: 0 releases, 0 assets; NuGet 404; search 0 | **NO ARTIFACT** |
| L11 | Kernel/operator coverage | not documented (requires the EP to test) | — | cannot be measured without L5/L6 | **UNVERIFIED** |

## 2. Gate chain

```text
L1 OS  ──┐
L2 SDK ──┴─> L3 API works ──> L4 catalog entry  [GATE CLOSED HERE]
                                   │
                                   ├─> L5 MSIX ──> L6 DLL ──> S6 register ──> S7 session ──> S8 parity
                                   │
L7 driver ─────────────────────────┘   (cannot be evaluated while L4 is closed)
L8 gfx list ───────────────────────┘
L11 kernels ───────────────────────┘
```

**The single closed gate is L4.** Everything to its right is unevaluable, not failed.
That distinction matters: a closed gate is not evidence of incompatibility.

## 3. What each official party would have to publish to open it

| Party | Missing artifact / statement | Would open |
| --- | --- | --- |
| Microsoft (Windows ML) | the rule by which the 2.x catalog enumerates providers, and why this DID sees **none** (not even `WebGpuExecutionProvider`, whose documented requirement is only "any DirectX 12-capable GPU") | L4 |
| Microsoft (Store/Update targeting) | whether `Microsoft.WinML.AMD.GPU.EP.Framework.*` (1.8.65.0) is targeted at `VEN_1002&DEV_1586` | L4/L5 |
| AMD | a published gfx support list for the AMDGPU EP (does it include gfx1151?) | L8/L11 |
| AMD + HP | the Adrenalin version that corresponds to OEM WDDM `32.0.22018.5` / INF `25.20.18` | L7 |
| AMD (or Microsoft) | a signed redistributable of `onnxruntime-ep-amdgpu` for Windows, or an explicit "MSIX only" statement | L10 |

## 4. Anti-statements (things this matrix does NOT claim)

- It does **not** claim gfx1151 is unsupported: the architecture requirement published for the
  deprecated MIGraphX EP ("AMD RDNA 3 or later GPU") is met by RDNA 3.5, and no gfx list exists
  for the AMDGPU EP.
- It does **not** claim the HP OEM driver blocks the EP: no official statement ties the EP to a
  retail AMD installer, and DirectML runs on the same driver with 9/9 Tier-A PASS.
- It does **not** claim a newer Windows ML would help: the SDK minimum is already met and the
  SDK cannot create a catalog entry the OS does not offer.
- It does **not** treat `S_OK` as readiness, and it does **not** treat a created ONNX Runtime
  session as execution (see `NEGATIVE_TESTS.json`: an unknown provider name falls back to CPU
  silently on this build).

## 5. Version comparability notes

| Pair | Comparable? | Why |
| --- | --- | --- |
| `windowsml 2.5.96` vs required `2.5.96` | yes | same versioning scheme |
| OS `26200` vs required `26100+` | yes | build numbers |
| WDDM `32.0.22018.5` vs "Adrenalin 24.12.1+" | **no** | different numbering systems, no documented mapping |
| INF internal `25.20.18` vs "Adrenalin 24.12.1+" | **no** | driver-package build string, not a marketing release |
| MSIX `1.8.65.0` vs Windows ML `2.5.96` | yes (compatibility, not ordering) | wiki states the package is "compatible with Windows ML 2.x and 1.8.x" |
