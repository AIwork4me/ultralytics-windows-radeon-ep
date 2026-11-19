"""Run and receipt contracts for contract-aware resume (fail-closed).

A resumed run may only reuse a receipt when ALL of the following are identical
to the contract captured when the run was created:

  * harness code (git HEAD + content hash of the harness tree),
  * threshold snapshot (byte-level acceptance criteria),
  * model (.pt and .onnx SHA256),
  * input bundle (input_id, image SHA256, tensor SHA256),
  * execution provider,
  * device identity (gfx + DXGI vendor/device ID + stable host id).

The contract is written ONCE into ``<run_dir>/RUN_CONTRACT.json``. Receipts
carry a per-case ``contract`` block containing the hashes that the resume path
re-checks. Any mismatch is a hard error: the old receipt is never edited and the
case is never re-run under the same ``run_id`` (create a new run id instead).

LUID is deliberately NOT part of the contract: it is boot-local (see
``device_identity``), so a reboot would otherwise invalidate an otherwise
identical hardware/HIP identity. The pinned DXGI DID + stable host id are the
identity; the LUID is recorded as session corroboration only.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from .manifest import sha256_file

CONTRACT_SCHEMA_VERSION = 1
THRESHOLDS_SNAPSHOT_NAME = "thresholds_snapshot.json"
RUN_CONTRACT_NAME = "RUN_CONTRACT.json"

# Files whose content defines "the harness". A change here must invalidate
# resume, because old receipts were produced by different code.
HARNESS_DIRS = ("runner", "src/radeon_ort")
HARNESS_FILES = ("pyproject.toml",)
HARNESS_SUFFIXES = (".py", ".yaml", ".yml", ".json", ".toml")


class ContractError(RuntimeError):
    """Raised when a run cannot be created/resumed under its contract."""


def canonical_json(obj) -> str:
    """Stable JSON used for every hash in this module."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_harness_file(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if "__pycache__" in rel.parts:
        return False
    return path.suffix in HARNESS_SUFFIXES


def harness_files(repo_root: str | Path) -> list[Path]:
    """Deterministically sorted harness files (no reports/evidence weights)."""
    root = Path(repo_root)
    out: list[Path] = []
    for d in HARNESS_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_file() and _is_harness_file(p, root):
                out.append(p)
    for name in HARNESS_FILES:
        p = root / name
        if p.is_file():
            out.append(p)
    return sorted(out, key=lambda p: p.relative_to(root).as_posix())


def harness_sha256(repo_root: str | Path) -> str:
    """Content hash of the harness tree; path + file SHA256, order independent."""
    root = Path(repo_root)
    h = hashlib.sha256()
    for p in harness_files(root):
        rel = p.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(sha256_file(p).encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def _git(repo_root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=str(repo_root), text=True,
            stderr=subprocess.DEVNULL).strip()
    except Exception:  # noqa: BLE001 - git absent is an honest "unknown"
        return ""


def code_state(repo_root: str | Path) -> dict:
    """Exact code provenance. Dirty covers harness paths only (not evidence)."""
    root = Path(repo_root)
    head = _git(root, "rev-parse", "HEAD") or "unknown"
    dirty = bool(_git(root, "status", "--porcelain", "--untracked-files=no", "--",
                      "runner", "src", "tests", "schemas", "configs", "catalog",
                      "pyproject.toml"))
    return {
        "git_head_sha": head,
        "worktree_dirty": dirty,
        "harness_sha256": harness_sha256(root),
    }


def thresholds_sha256(thresholds: dict) -> str:
    return sha256_text(canonical_json(thresholds))


def thresholds_snapshot_path(run_dir: str | Path) -> Path:
    return Path(run_dir) / THRESHOLDS_SNAPSHOT_NAME


def write_thresholds_snapshot(run_dir: str | Path, thresholds: dict) -> Path:
    """Write the acceptance criteria once. Never rewrite an existing snapshot."""
    path = thresholds_snapshot_path(run_dir)
    if path.exists():
        raise ContractError(f"refusing to overwrite existing threshold snapshot {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(thresholds, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


def load_thresholds_snapshot(run_dir: str | Path) -> dict:
    path = thresholds_snapshot_path(run_dir)
    if not path.is_file():
        raise ContractError(
            f"{path} is missing: a run without a predeclared threshold snapshot "
            "cannot be resumed or verified fail-closed")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ContractError(f"invalid threshold snapshot {path}: {e}") from e
    if not isinstance(data, dict) or not data:
        raise ContractError(f"empty threshold snapshot {path}")
    return data


def ensure_thresholds(run_dir: str | Path, live_thresholds: dict, *,
                      resume: bool) -> tuple[dict, str]:
    """Return (snapshot, sha). Resume never adopts a changed live config.

    New run: write the snapshot from the live config.
    Resume: require the snapshot to exist AND be identical to the live config;
    a modified ``configs/thresholds.yaml`` under the same run id is a hard error
    (P0-D: acceptance criteria cannot move after the fact).
    """
    path = thresholds_snapshot_path(run_dir)
    live_sha = thresholds_sha256(live_thresholds)
    if resume:
        snap = load_thresholds_snapshot(run_dir)
        snap_sha = thresholds_sha256(snap)
        if canonical_json(snap) != canonical_json(live_thresholds):
            raise ContractError(
                "THRESHOLDS_CONFIG_CHANGED: configs/thresholds.yaml differs from "
                f"{path.name} (snapshot {snap_sha[:16]}..., live {live_sha[:16]}...); "
                "a new run_id is required")
        return snap, snap_sha
    if path.exists():
        snap = load_thresholds_snapshot(run_dir)
        if canonical_json(snap) != canonical_json(live_thresholds):
            raise ContractError(
                "THRESHOLDS_CONFIG_CHANGED: existing run directory has a "
                "different threshold snapshot; use --resume with the original "
                "config or create a new run_id")
        return snap, thresholds_sha256(snap)
    write_thresholds_snapshot(run_dir, live_thresholds)
    return live_thresholds, live_sha


def contract_sha256(contract: dict) -> str:
    body = {k: v for k, v in contract.items() if k != "contract_sha256"}
    return sha256_text(canonical_json(body))


def build_run_contract(*, run_id: str, gfx: str, eps: list[str], models: list[dict],
                       cases: list[str], thresholds: dict, code: dict,
                       device_identity: dict, images: dict, options: dict,
                       created_utc: str) -> dict:
    contract = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "run_id": run_id,
        "created_utc": created_utc,
        "gfx": gfx,
        "eps": list(eps),
        "models": models,
        "cases": cases,
        "thresholds": thresholds,
        "thresholds_sha256": thresholds_sha256(thresholds),
        "code": code,
        "device_identity": device_identity,
        "images": images,
        "options": options,
    }
    contract["contract_sha256"] = contract_sha256(contract)
    return contract


def write_run_contract(run_dir: str | Path, contract: dict) -> Path:
    path = Path(run_dir) / RUN_CONTRACT_NAME
    if path.exists():
        raise ContractError(f"refusing to overwrite existing run contract {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


def load_run_contract(run_dir: str | Path) -> dict:
    path = Path(run_dir) / RUN_CONTRACT_NAME
    if not path.is_file():
        raise ContractError(
            f"{path} is missing: legacy run directories cannot be resumed "
            "contract-aware; create a new run_id")
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ContractError(f"invalid run contract {path}: {e}") from e
    if contract.get("schema_version") != CONTRACT_SCHEMA_VERSION:
        raise ContractError(
            f"unsupported run contract schema_version={contract.get('schema_version')!r}")
    if contract.get("contract_sha256") != contract_sha256(contract):
        raise ContractError(
            "RUN_CONTRACT_TAMPERED: contract_sha256 does not match the contract body")
    return contract


def _model_map(models: list[dict]) -> dict[str, dict]:
    return {m["checkpoint"]: m for m in models}


def verify_run_contract(contract: dict, *, repo_root: str | Path, run_dir: str | Path,
                        thresholds: dict, host_block: dict, models: list[str],
                        eps: list[str], images: dict | None = None,
                        options: dict | None = None) -> list[str]:
    """Problems that forbid resuming this run id (empty list means OK)."""
    problems: list[str] = []
    if contract.get("contract_sha256") != contract_sha256(contract):
        problems.append("RUN_CONTRACT_TAMPERED: contract SHA mismatch")
    if contract.get("run_id") != Path(run_dir).name:
        problems.append(
            f"run contract run_id={contract.get('run_id')!r} != directory name "
            f"{Path(run_dir).name!r}")

    current = code_state(repo_root)
    ccode = contract.get("code") or {}
    if ccode.get("git_head_sha") != current["git_head_sha"]:
        problems.append(
            f"CODE_SHA_CHANGED: contract {ccode.get('git_head_sha')!r} != current "
            f"{current['git_head_sha']!r}; create a new run_id")
    if ccode.get("harness_sha256") != current["harness_sha256"]:
        problems.append(
            "HARNESS_CONTENT_CHANGED: runner/src/config/schema content differs from "
            "the contract; create a new run_id")
    if ccode.get("worktree_dirty"):
        problems.append(
            "CODE_STATE_DIRTY: the contract was created from a dirty harness "
            "worktree; it cannot be resumed or attested")
    if current["worktree_dirty"]:
        problems.append(
            "CODE_STATE_DIRTY: current harness worktree is dirty; commit or stash "
            "before resuming")

    want_sha = contract.get("thresholds_sha256")
    got_sha = thresholds_sha256(thresholds)
    if want_sha != got_sha:
        problems.append(
            f"THRESHOLDS_CHANGED: contract {want_sha!r} != current {got_sha!r}")
    if canonical_json(contract.get("thresholds") or {}) != canonical_json(thresholds):
        problems.append("THRESHOLDS_BODY_CHANGED: contract body differs from snapshot")

    if sorted(contract.get("eps") or []) != sorted(eps):
        problems.append(
            f"EP_SET_CHANGED: contract {contract.get('eps')} != requested {eps}")
    if sorted(m["checkpoint"] for m in contract.get("models") or []) != sorted(models):
        problems.append(
            "MODEL_SET_CHANGED: contract models differ from requested models")

    cd = contract.get("device_identity") or {}
    for key in ("gfx", "host_id", "vendor_id", "device_id"):
        if cd.get(key) != host_block.get(key):
            problems.append(
                f"DEVICE_IDENTITY_CHANGED: {key} contract={cd.get(key)!r} "
                f"current={host_block.get(key)!r}")
    if not cd.get("host_id") or cd.get("host_id") == "unknown":
        problems.append("DEVICE_IDENTITY_UNSTABLE: contract host_id is missing/unknown")
    if cd.get("did_match") is False:
        problems.append(
            "DEVICE_IDENTITY_WEAK: contract used a name-only fallback, not a DID pin")
    if not host_block.get("verified_device_identity"):
        problems.append(
            "DEVICE_IDENTITY_UNVERIFIED: current live DXGI identity did not verify")
    if cd.get("luid") != host_block.get("device_luid"):
        # LUID is boot-local by design; never a resume failure. Recorded by the
        # caller as an informational note instead.
        pass

    if images is not None:
        cimg = contract.get("images") or {}
        for name, want in images.items():
            got = (cimg.get(name) or {}).get("sha256")
            if want and want.get("sha256") != got:
                problems.append(
                    f"INPUT_IMAGE_CHANGED: {name} contract={got!r} "
                    f"current={(want or {}).get('sha256')!r}")
    if options is not None:
        copts = contract.get("options") or {}
        for k, v in options.items():
            if copts.get(k) != v:
                problems.append(
                    f"RUN_OPTION_CHANGED: {k} contract={copts.get(k)!r} current={v!r}")
    return problems


def build_receipt_contract(run_contract: dict, *, ep: str, model: dict,
                           input_block: dict) -> dict:
    """Per-receipt contract block checked by resume and the strict validator."""
    return {
        "run_contract_sha256": run_contract["contract_sha256"],
        "code": dict(run_contract["code"]),
        "thresholds_sha256": run_contract["thresholds_sha256"],
        "device_identity": dict(run_contract["device_identity"]),
        "ep": ep,
        "model": {
            "checkpoint": model.get("checkpoint"),
            "pt_sha256": model.get("pt_sha256"),
            "onnx_sha256": model.get("onnx_sha256"),
        },
        "input": {
            "input_id": input_block.get("input_id"),
            "image_sha256": input_block.get("image_sha256"),
            "tensor_sha256": dict(input_block.get("tensor_sha256") or {}),
        },
    }


def validate_receipt_contract(receipt: dict, run_contract: dict) -> list[str]:
    """Problems that make an existing receipt stale/forged for this run."""
    problems: list[str] = []
    rc = receipt.get("contract")
    if not isinstance(rc, dict) or not rc:
        return ["RECEIPT_CONTRACT_MISSING: receipt has no contract block"]
    if receipt.get("run_id") != run_contract.get("run_id"):
        problems.append(
            f"RECEIPT_RUN_ID_MISMATCH: receipt={receipt.get('run_id')!r} "
            f"contract={run_contract.get('run_id')!r}")
    if rc.get("run_contract_sha256") != run_contract.get("contract_sha256"):
        problems.append(
            "RECEIPT_RUN_CONTRACT_MISMATCH: receipt was produced under a different "
            "RUN_CONTRACT.json")
    if rc.get("code") != run_contract.get("code"):
        problems.append("RECEIPT_CODE_MISMATCH: code state differs from run contract")
    if rc.get("thresholds_sha256") != run_contract.get("thresholds_sha256"):
        problems.append(
            "RECEIPT_THRESHOLDS_MISMATCH: thresholds differ from run contract")
    if rc.get("device_identity") != run_contract.get("device_identity"):
        problems.append(
            "RECEIPT_DEVICE_MISMATCH: device identity differs from run contract")

    active = "amdgpu" if receipt.get("status", {}).get("amdgpu") != "NOT_RUN" else "dml"
    if rc.get("ep") not in (None, active):
        problems.append(
            f"RECEIPT_EP_MISMATCH: contract ep={rc.get('ep')!r} active={active!r}")

    model = receipt.get("model") or {}
    cm = rc.get("model") or {}
    if cm.get("checkpoint") not in (None, model.get("checkpoint")):
        problems.append("RECEIPT_MODEL_CHECKPOINT_MISMATCH")
    for key in ("pt_sha256", "onnx_sha256"):
        want = cm.get(key)
        got = model.get(key)
        if want is None:
            if got is not None:
                problems.append(
                    f"RECEIPT_MODEL_{key.upper()}_UNPINNED: receipt has {key} but "
                    "contract recorded null")
        elif want != got:
            problems.append(
                f"RECEIPT_MODEL_{key.upper()}_MISMATCH: contract={want!r} "
                f"receipt={got!r}")

    inp = receipt.get("input") or {}
    ci = rc.get("input") or {}
    if ci.get("input_id") != inp.get("input_id"):
        problems.append(
            f"RECEIPT_INPUT_ID_MISMATCH: contract={ci.get('input_id')!r} "
            f"receipt={inp.get('input_id')!r}")
    if ci.get("image_sha256") != inp.get("image_sha256"):
        problems.append("RECEIPT_INPUT_IMAGE_MISMATCH")
    if dict(ci.get("tensor_sha256") or {}) != dict(inp.get("tensor_sha256") or {}):
        problems.append("RECEIPT_INPUT_TENSOR_MISMATCH")
    return problems


def expected_thresholds_for_receipt(receipt: dict, thresholds: dict) -> dict | None:
    """Same predeclared-threshold projection used by verify_receipts."""
    if not thresholds:
        return None
    if int(receipt.get("schema_version", 1)) < 2:
        return None
    yaml_task_key = {
        "detect": "detection", "segment": "segmentation", "semantic": "semantic",
        "depth": "depth", "classify": "classify", "pose": "pose", "obb": "obb",
    }
    out = {
        "atol": (thresholds.get("numeric_parity") or {}).get("fp32", {}).get("atol"),
        "rtol": (thresholds.get("numeric_parity") or {}).get("fp32", {}).get("rtol"),
    }
    tp = receipt.get("task_parity") or {}
    task = tp.get("task")
    if task:
        key = yaml_task_key.get(task, task)
        out["task"] = (thresholds.get("task_oracle") or {}).get(key, {})
    return out
