# GPU evidence contract and contract-aware resume (2026-11-19 hardening)

This note documents the fail-closed hardening applied after the PR #8 review.
It covers P0-A (gfx hardcoding), P0-B (contract-aware resume), P0-C (strict
PASS/exit code) and P0-D (threshold snapshot immutability), plus the P1
evidence-honesty changes for EP binding and GPU memory.

## 1. The problem with "receipt exists => skip"

Before this change `runner/r1_pipeline.py` skipped any existing
`receipt_<case>__<ep>.json` purely by filename, and `runner/run_matrix.py`
accepted the receipt's own `status` field as the verdict. A receipt produced
under different code, a different ONNX file, a different input tensor, a
different EP or different thresholds could therefore be reused, and a hand-
edited `status` could produce a PASS.

`--resume` was accepted by `run_matrix` but never forwarded; `resume_matrix`
was effectively a no-op wrapper.

## 2. Run contract

A new run writes `<run_dir>/RUN_CONTRACT.json` exactly once. It contains:

| field | purpose |
| --- | --- |
| `run_id` | must equal the run directory name |
| `gfx` | gfx1100 / gfx1151 / gfx1201 |
| `eps`, `models`, `cases` | requested scope; resume cannot change it |
| `thresholds`, `thresholds_sha256` | exact acceptance criteria |
| `code.git_head_sha` | commit used |
| `code.harness_sha256` | SHA256 over `runner/`, `src/radeon_ort/`, `configs/`, `schemas/`, `pyproject.toml` |
| `code.worktree_dirty` | dirty harness worktree is not resumable/attestable |
| `device_identity` | gfx + DXGI vendor/device ID + stable `host_id` + LUID (corroboration only) |
| `images`, `options` | fixture SHA256 and benchmark/negative-control switches |
| `contract_sha256` | SHA256 of the canonical contract body |

Every new receipt also carries a `contract` block with the run-contract SHA,
code state, threshold SHA, device identity, EP, model (`pt_sha256`,
`onnx_sha256`) and input (`input_id`, `image_sha256`, `tensor_sha256`).

`contract_sha256` is recomputed on load. Editing a contract (for example
tightening a threshold after seeing a failure) is detected as
`RUN_CONTRACT_TAMPERED` and refused.

## 3. Resume semantics

`python -m runner.r1_pipeline ... --resume` and
`python -m runner.run_matrix ... --resume` now:

1. load `RUN_CONTRACT.json`; a legacy run without one cannot be resumed;
2. require the current harness SHA, git HEAD, thresholds body and device
   identity (gfx + vendor/device ID + `host_id`) to equal the contract;
3. require the requested model/EP set and the fixture/option set to equal the
   contract;
4. for every existing receipt, run the strict validator **and**
   `validate_receipt_contract` **and** compare the receipt's model/input hashes
   with the current files/input bundle;
5. skip a receipt only if all checks pass; otherwise print
   `RESUME CONTRACT FAIL` and exit non-zero **without** editing or re-running
   that case.

A changed `configs/thresholds.yaml` under an old `run_id` is rejected with
`THRESHOLDS_CONFIG_CHANGED`; the old receipt is not blessed and no new case is
run under the same id. Create a new `run_id` for a new acceptance standard.

LUID is not a resume key: it is boot-local by design. A reboot with the same
DID/host may change the LUID; gfx/vendor/device ID + `host_id` are the identity.

Dirty harness code is refused on resume (`CODE_STATE_DIRTY`). A run created from
a dirty tree is marked `worktree_dirty=true` and likewise cannot be resumed or
attested.

## 4. Strict final audit (P0-C)

`runner/run_matrix.py` no longer trusts `receipt["status"][ep]`. After the
producer returns, it:

* loads the run contract and threshold snapshot (never the live config);
* validates each receipt with `validate_receipt_strict` (schema + recomputed
  evidence) and `validate_receipt_contract`;
* counts only `derived[ep]` values as PASS;
* treats missing receipts, `BLOCKED_*`, `NOT_RUN`, and PARTIAL as exit **2**;
* treats forged/stale receipts, contract mismatch, threshold mismatch, extra
  receipts, or any `FAIL_*` as exit **1**.

Exit 0 requires every expected `(model, EP)` case to strict-validate as
`PASS_GPU_EXECUTED_FULL`.

## 5. gfx selection (P0-A)

`r1_pipeline`, `run_matrix`, `preflight`, `soak` and `bench_repeats` no longer
default to `gfx1151`. Omitting `--gfx` infers the target from the live DXGI
device ID (`GFX_DEVICE_IDS`). A request for gfx1100/gfx1201 on a host whose AMD
adapter is gfx1151 fails with `WRONG_OR_AMBIGUOUS_DEVICE` before any session is
created; a host with more than one supported gfx fails as ambiguous.

`status.host` is derived as `VERIFIED_<gfx>` from the live DID-pinned identity,
and the host block records `vendor_id`, `device_id`, `did_match`, the selected
DXGI index/LUID and the stable `host_id`. The worker receives the explicit gfx
in its case JSON; `_ep_worker.py` refuses a case without `gfx`.

## 6. P1 evidence honesty

* **P1-A**: `device_binding.dxgi_corroboration` is now labelled
  `evidence_kind=HOST_DXGI_ENUMERATION_INDIRECT`, `is_ep_reported=false`, with
  an explicit `mapping_assumption` for the DML ordinal. `ep_reported_identity`
  records that AMDGPU/DML expose no LUID/PCI identity on this build.
  `derive_verdicts` and the strict validator refuse to promote an indirect DXGI
  LUID to `PHYSICAL_GPU_IDENTITY_CORROBORATED`; that state now requires the EP's
  own reported LUID matching DXGI.
* **P1-B**: host evidence records `memory` provenance: DXGI
  `DedicatedVideoMemory`, `DedicatedSystemMemory`, `SharedSystemMemory` (MiB,
  rounded down) and WMI `AdapterRAM` (bytes). WMI `AdapterRAM` saturating near
  4 GiB is flagged; the DXGI dedicated segment on an integrated APU is labelled
  a carve-out, not soldered VRAM. None of these is used as a runtime/temperature
  metric.

## 7. Commands

```powershell
# New contract run (gfx inferred; use --gfx to pin)
.venv-winml\Scripts\python -m runner.run_matrix --tier core --ep both --benchmark

# Resume only if the contract matches
.venv-winml\Scripts\python -m runner.resume_matrix --run-id <run_id> --tier core --ep both

# Strict receipt + contract + manifest verification
.venv-winml\Scripts\python -m runner.verify_receipts --run-dir reports\<gfx>\<run_id>
```
