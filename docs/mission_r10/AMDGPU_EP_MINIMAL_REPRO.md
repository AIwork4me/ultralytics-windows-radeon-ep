# Windows ML AMDGPU EP catalog — minimal reproduction (R10-C)

Date: 2026-11-19 (Asia/Shanghai)
Host: HP ZBook Ultra G1a 14 — AMD Ryzen AI MAX+ PRO 395, Radeon 8060S (gfx1151, DID `0x1586`)
Verdict: **REPRO_CONFIRMED / BLOCKED_EXTERNAL** — the script reproduces an empty
`ExecutionProviderCatalog` with `S_OK` HRESULTs, independently of this repository.

---

## 1. What the reproduction is

`repro/windowsml_amdgpu_catalog_probe.py` — a single file, standard library plus
the official `windowsml` package, that answers one question:

> On this host, does the Windows ML 2.x `ExecutionProviderCatalog` list any
> execution provider, and specifically `AMDGPUExecutionProvider`?

It is deliberately **not** part of the harness: it never imports `radeon_ort`
(there is an assertion for that), it installs nothing, registers nothing,
downloads nothing, and writes nothing except the optional `--out` file. The
purpose is that a Microsoft or AMD maintainer can run it on a clean machine and
get the same evidence without any of this repository's context.

Checks it performs, each reported independently as `PASS` / `FAIL` / `UNKNOWN`:

| check | how |
| --- | --- |
| `official_windowsml_package` | `importlib.metadata.version("windowsml")` |
| `windows_11_24h2_or_later` | `sys.getwindowsversion().build` (needs 26100+) |
| `amd_gpu_adapter_present` | DXGI `EnumAdapters1` via `ctypes`, vendor `0x1002`, not software |
| `catalog_create_hresult` | `WinMLEpCatalogCreate` called directly through `windowsml._bindings` |
| `catalog_enum_hresult` | `WinMLEpCatalogEnumProviders` called directly |
| `catalog_provider_count` | `EpCatalog().find_all_providers()` |
| `amdgpu_provider_listed` | name match on `AMDGPUExecutionProvider` |

It also records, per provider entry: name, version, `ready_state` (numeric and
`Ready`/`NotReady`/`NotPresent`), certification, redacted library path and
package family name; plus the WinML shared-runtime package version, the
`windowsml` and `onnxruntime` Python versions, the Windows build + UBR, and the
GPU DID + driver version decoded from `IDXGIAdapter::CheckInterfaceSupport`
(`LARGE_INTEGER` = `(major<<16|minor, build<<16|revision)`).

## 2. How to run it (clean machine)

```powershell
python -m venv .venv-repro
.venv-repro\Scripts\pip install -r repro\requirements.txt   # windowsml==2.5.96
.venv-repro\Scripts\python repro\windowsml_amdgpu_catalog_probe.py --gfx gfx1151
```

Exit codes: `0` = at least one provider listed, `3` = API OK but zero providers
(`BLOCKED_EXTERNAL`), `4` = `windowsml` not installed, `5` = probe error.

## 3. Real captured output on this host (2026-11-19)

Command actually executed (from the repository root, `.venv-winml` already has
`windowsml 2.5.96`):

```powershell
.venv-winml\Scripts\python.exe repro\windowsml_amdgpu_catalog_probe.py --gfx gfx1151 --out reports\mission_r10\REPRO_RUN1.json
```

Console:

```text
===WINML_AMDGPU_CATALOG_PROBE_JSON_BEGIN===
{
  "blocked_status": "BLOCKED_EXTERNAL",
  "captured_utc": "2026-11-19T13:52:19Z",
  "catalog": {
    "api_ok": true,
    "error": null,
    "provider_count": 0,
    "providers": [],
    "raw_hresults": {
      "available": true,
      "create_hresult": "0x00000000",
      "enum_hresult": "0x00000000",
      "enumerated_count": 0
    }
  },
  "checks": [
    {
      "check": "official_windowsml_package",
      "detail": "2.5.96",
      "status": "PASS"
    },
    {
      "check": "windows_11_24h2_or_later",
      "detail": "build=26200 (Windows ML 2.x EPs need 24H2 / build 26100+)",
      "status": "PASS"
    },
    {
      "check": "amd_gpu_adapter_present",
      "detail": "2 AMD adapter(s)",
      "status": "PASS"
    },
    {
      "check": "catalog_create_hresult",
      "detail": "0x00000000",
      "status": "PASS"
    },
    {
      "check": "catalog_enum_hresult",
      "detail": "0x00000000",
      "status": "PASS"
    },
    {
      "check": "catalog_provider_count",
      "detail": 0,
      "status": "FAIL"
    },
    {
      "check": "amdgpu_provider_listed",
      "detail": "catalog is empty",
      "status": "FAIL"
    }
  ],
  "gfx_requested": "gfx1151",
  "host": {
    "gpu": {
      "adapters": [
        {
          "description": "AMD Radeon(TM) 8060S Graphics",
          "device_id": "0x1586",
          "driver_version": "32.0.22018.5",
          "index": 0,
          "is_software": false,
          "revision": 209,
          "subsys_id": "0x8D01103C",
          "vendor_id": "0x1002"
        },
        {
          "description": "AMD Radeon(TM) 8060S Graphics",
          "device_id": "0x1586",
          "driver_version": "32.0.22018.5",
          "index": 1,
          "is_software": false,
          "revision": 209,
          "subsys_id": "0x8D01103C",
          "vendor_id": "0x1002"
        },
        {
          "description": "Microsoft Basic Render Driver",
          "device_id": "0x008C",
          "driver_version": "10.0.26100.9818",
          "index": 2,
          "is_software": true,
          "revision": 0,
          "subsys_id": "0x00000000",
          "vendor_id": "0x1414"
        }
      ],
      "available": true,
      "errors": []
    },
    "windows_build": {
      "build": 26200,
      "display_version": "25H2",
      "major": 10,
      "minor": 0,
      "platform": "Windows-11-10.0.26200-SP0",
      "ubr": 9974
    }
  },
  "read_only": true,
  "schema": "winml_amdgpu_catalog_probe/v1",
  "stable_digest": "66baf7b657232a2a0a586566807c3b26c0b6131edc1557fa438ef0466ec6150b",
  "versions": {
    "ep_packages": [
      "WindowsWorkload.EP.AMD.VitisAI.Framework.1.8 1.8.80.0"
    ],
    "error": null,
    "onnxruntime": "1.25.2",
    "windows_ml_shared_runtime": "WindowsWorkload.WinMLShared.7.0 1.2610.1050.0",
    "windowsml_python": "2.5.96"
  }
}
===WINML_AMDGPU_CATALOG_PROBE_JSON_END===
```

Process exit code: **3** (catalog API succeeded, zero providers). The only
stderr line was ONNX Runtime's benign `Init provider bridge failed` warning,
emitted because `onnxruntime-windowsml` is installed in the same interpreter and
its provider bridge cannot initialise without a registered EP; it is not produced
by this script's catalog path.

Raw JSON files: `reports/mission_r10/REPRO_RUN1.json`,
`reports/mission_r10/REPRO_RUN2.json`, `reports/mission_r10/REPRO_RUN3.json`.

## 4. Idempotency evidence

Two consecutive runs, two seconds apart:

| run | `captured_utc` | `stable_digest` | exit |
| --- | --- | --- | --- |
| 1 | `2026-11-19T13:52:19Z` | `66baf7b657232a2a0a586566807c3b26c0b6131edc1557fa438ef0466ec6150b` | 3 |
| 2 | `2026-11-19T13:52:21Z` | `66baf7b657232a2a0a586566807c3b26c0b6131edc1557fa438ef0466ec6150b` | 3 |
| 3 | `2026-11-19T13:58:41Z` (after a `datetime.UTC` code cleanup) | `66baf7b657232a2a0a586566807c3b26c0b6131edc1557fa438ef0466ec6150b` | 3 |

All three digests are **identical** (`true`), including across a code edit that did
not change the observation — exactly the property the digest is supposed to have.
`stable_digest` is a sha256 over the whole report except `captured_utc` and
itself, so an unchanged host always produces the same digest; any change in
catalog contents, HRESULTs, versions, adapter list or package inventory changes
it. This is what makes the script usable as a monitor (§6).

## 5. Redaction check

The report contains hardware facts only:

* GPU device id `0x1586`, subsystem id `0x8D01103C`, driver `32.0.22018.5`
  — allowed by the mission (DID and driver version are fine);
* **no** PnP instance id (the `PCI\VEN_1002&DEV_1586&SUBSYS_...&REV_D1\4&85C8329&0&0041`
  string is never read);
* **no** LUID, no machine name, no user path;
* `library_path` / `package_root_path` are rewritten through a
  `\Users\<redacted>` filter if they ever contain an absolute user profile.

This was verified by inspecting the produced files. The command actually run:

```powershell
$hits = Select-String -Path reports\mission_r10\*.json `
  -Pattern 'Users','LUID','COMPUTERNAME','USERNAME','PCI\VEN' -SimpleMatch -AllMatches
if ($hits) { $hits } else { "NO_MATCH" }
```

Result: `NO_MATCH` over all 7 JSON files (`DOC_CITATIONS.json`, `PROBE_20261119T134753Z.json`,
`PROBE_R10_A_20261119T135033Z.json`, `PROBE_R10_B_20261119T135036Z.json`,
`PROBE_R10_VS_R4_20261119T135041Z.json`, `REPRO_RUN1.json`, `REPRO_RUN2.json`,
`REPRO_RUN3.json`).
The repository's own scanner agrees: `radeon_ort.redaction.scan_file` over the same files
returns `LEAKS= []`.

## 6. Using it as a monitor (manual only — no task was created)

Manual run and compare (no scheduler, nothing installed):

```powershell
$prev = "reports\mission_r10\REPRO_RUN2.json"
.venv-winml\Scripts\python.exe repro\windowsml_amdgpu_catalog_probe.py --gfx gfx1151 --out reports\mission_r10\REPRO_NEXT.json
# compare the two digests
$a = (Get-Content $prev -Raw | ConvertFrom-Json).stable_digest
$b = (Get-Content reports\mission_r10\REPRO_NEXT.json -Raw | ConvertFrom-Json).stable_digest
"changed=" + ($a -ne $b)
```

For a richer change report use the repository probe, which diffs two runs
field by field:

```powershell
$env:PYTHONPATH="src"
.venv-winml\Scripts\python.exe -m runner.probe_amdgpu_ep `
  --out reports\mission_r10\PROBE_NEXT.json --gfx gfx1151 `
  --diff reports\mission_r10\PROBE_R10_B_20261119T135036Z.json
```

### Optional Windows Task Scheduler snippet (NOT registered in this session)

Copy/paste only if you explicitly want a recurring local probe. It creates no
network traffic, installs nothing, and writes only inside `reports\mission_r10\`:

```powershell
# OPTIONAL - not executed by this mission
$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument '-NoProfile -Command "& ''C:\path\to\repo\.venv-winml\Scripts\python.exe'' -m runner.probe_amdgpu_ep --out reports\mission_r10\PROBE_SCHED.json --gfx gfx1151"' `
  -WorkingDirectory "C:\path\to\repo"
$trigger = New-ScheduledTaskTrigger -Daily -At 09:00
Register-ScheduledTask -TaskName "WinML-AMDGPU-EP-Probe" -Action $action -Trigger $trigger
# remove with: Unregister-ScheduledTask -TaskName "WinML-AMDGPU-EP-Probe" -Confirm:$false
```

**No scheduled task was created during R10.** This block is documentation only.

## 7. What the reproduction does and does not prove

Proves, on this host, with only the official package:

* the Windows ML 2.x catalog API is callable and returns `S_OK` for both
  `Create` and `EnumProviders`;
* the returned provider list is **empty** — not "AMDGPU missing", but *no
  provider of any vendor*, including `WebGpuExecutionProvider`;
* the host satisfies the documented OS prerequisite (build 26200 >= 26100) and
  has a real AMD GPU (`0x1586`) with a 2025-09-17 driver.

Does **not** prove:

* which component filters the list (Windows ML runtime, Microsoft Store
  targeting, or the AMD driver) — that is exactly what the escalation asks;
* that gfx1151 is unsupported;
* that the driver is at fault, or that a driver update would fix it;
* that AMDGPU would work if the package were installed.
