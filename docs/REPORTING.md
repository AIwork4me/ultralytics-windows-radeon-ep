# REPORTING — evidence layout per run

`reports/<gfx>/<run_id>/`

- `HOST_FINGERPRINT.json` — OS/CPU/GPU/driver/python (redacted)
- `EP_INSTALL_AND_PROBE.json` — WinML API surface, AMDGPU EP DLLs (path+sha256), register attempt, provider list
- `DEVICE_IDENTITY.json` — DXGI adapters, resolution, LUID
- `GPU_CANARY.json` — CPU/AMDGPU/DML canary: parity + node counts
- `receipts/receipt_<case>.json` — immutable per-case evidence (schemas/receipt.json)
- `raw/` — worker stdout/stderr, ORT profile JSONs, case JSONs
- `manifest.sha256` — every file hashed; `verify_receipts` audits mismatches+unlisted
- `CASE_LEDGER.json`, `compatibility_matrix.csv`, `COVERAGE_SUMMARY.json`
- `GFX1151_FINAL_CN.md` / `_EN.md` / `GFX1151_OPEN_GAPS.md` (mission G4)

Root `reports/compatibility_matrix.csv` is a generated composite; gfx1100/gfx1201
columns stay `NOT_RUN` until that hardware's receipts exist.
