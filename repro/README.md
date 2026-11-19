# repro/ — minimal reproduction for the empty Windows ML EP catalog

One question, one script, no repository import:

> On this Radeon 8060S host, Windows ML 2.x returns an **empty
> ExecutionProviderCatalog** even though the API call itself succeeds.

## Run it

```powershell
python -m venv .venv-repro
.venv-repro\Scripts\pip install -r repro\requirements.txt
.venv-repro\Scripts\python repro\windowsml_amdgpu_catalog_probe.py --gfx gfx1151
```

Optional: `--out probe.json` also writes the report to a file.

## What it does

* calls `windowsml.EpCatalog().find_all_providers()` (the official Windows ML
  Python surface for `Microsoft.Windows.AI.MachineLearning >= 2.5.96`);
* calls the same C entry points through `ctypes` (`WinMLEpCatalogCreate`,
  `WinMLEpCatalogEnumProviders`) so an HRESULT cannot be swallowed by the
  Python wrapper;
* enumerates display adapters through **DXGI with `ctypes`** (no pywin32, no
  WMI) and decodes the UMD driver version from
  `IDXGIAdapter::CheckInterfaceSupport`;
* reads the OS build/UBR and the installed WinML/EP AppX packages from the
  registry (read-only);
* prints per-item `PASS`/`FAIL`/`UNKNOWN` checks and one stable JSON block.

It never installs, registers, downloads, or writes anything except the optional
`--out` file, and it asserts that `radeon_ort` was never imported.

## Output

The machine-readable block is delimited so it can be piped:

```
===WINML_AMDGPU_CATALOG_PROBE_JSON_BEGIN===
{ ... }
===WINML_AMDGPU_CATALOG_PROBE_JSON_END===
```

`stable_digest` is a sha256 over everything **except** `captured_utc`, so two
consecutive runs on an unchanged host produce the same digest. That is the
idempotency check.

## Exit codes

| code | meaning |
| --- | --- |
| 0 | the catalog listed at least one provider |
| 3 | the catalog API succeeded but listed **zero** providers (`BLOCKED_EXTERNAL`) |
| 4 | the `windowsml` package is not installed in this interpreter |
| 5 | unexpected error while probing the catalog |

Exit code 3 is **not** a crash: it is the observed state of this host and the
reason the upstream issue exists. See
[`docs/mission_r10/AMDGPU_EP_MINIMAL_REPRO.md`](../docs/mission_r10/AMDGPU_EP_MINIMAL_REPRO.md)
for the captured real output.

## Privacy

The report contains the GPU device id and driver version (hardware facts, not
identity), but no PnP instance id, no user path, no machine name and no LUID.
Any `library_path` that contains an absolute user profile is rewritten to
`\Users\<redacted>`.
