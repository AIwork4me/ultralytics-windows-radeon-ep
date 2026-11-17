"""Receipt construction and the STRICT fail-closed GPU-PASS validator.

A GPU PASS (PASS_GPU_EXECUTED_FULL / PARTIAL) is only derivable from positive
evidence present in the receipt:
  1. session EP name matches the target provider, AND
  2. target-EP profiler node count is a positive integer (>0), AND
  3. device identity verified=True with target gfx, AND
  4. all compared outputs finite + allclose against the CPU oracle (by name), AND
  5. no CPU-provider node compute for a FULL claim (PARTIAL must declare counts).
Anything else cannot be upgraded to PASS by hand-editing: validate_receipt_strict
recomputes the derivable status and rejects mismatches (forged labels).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path

RECEIPT_SCHEMA_VERSION = 1

GPU_PASS_FULL = "PASS_GPU_EXECUTED_FULL"
GPU_PASS_PARTIAL = "PASS_GPU_EXECUTED_PARTIAL"
GPU_FAILS = {
    "FAIL_NO_GPU_COMPUTE": "target-EP node count is zero: no GPU compute happened",
    "FAIL_GPU_PARITY": "GPU outputs diverge from CPU ORT reference beyond thresholds",
    "FAIL_RUNTIME": "session creation or run raised",
    "FAIL_TASK": "task-level oracle failed while numeric parity held",
}
GPU_BLOCKS = {"BLOCKED_EP", "BLOCKED_IDENTITY", "BLOCKED_RESOURCE"}


class ReceiptValidationError(ValueError):
    pass


@dataclass
class ReceiptStatus:
    model_asset: str = "MISSING_WITH_REASON"
    export: str = "NOT_APPLICABLE"
    cpu_oracle: str = "NOT_RUN"
    amdgpu: str = "NOT_RUN"
    dml: str = "NOT_RUN"
    task_oracle: str = "NOT_RUN"
    benchmark: str = "NOT_MEASURED"
    host: str = "BLOCKED_ENV"
    reasons: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "model_asset": self.model_asset,
            "export": self.export,
            "cpu_oracle": self.cpu_oracle,
            "amdgpu": self.amdgpu,
            "dml": self.dml,
            "task_oracle": self.task_oracle,
            "benchmark": self.benchmark,
            "host": self.host,
        }


def utc_now_iso() -> str:
    from datetime import datetime
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_receipt(*, run_id: str, case_id: str, status: ReceiptStatus, host: dict,
                  model: dict, session: dict | None = None, parity: dict | None = None,
                  task: dict | None = None, benchmark_data: dict | None = None,
                  errors: list[dict] | None = None, provenance: dict) -> dict:
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "receipt_version": 1,
        "run_id": run_id,
        "case_id": case_id,
        "created_utc": utc_now_iso(),
        "rerun_of": None,
        "status": status.as_dict(),
        "status_reasons": status.reasons,
        "host": host,
        "model": model,
        "session": session or {},
        "parity": parity,
        "task": task,
        "benchmark_data": benchmark_data,
        "errors": errors or [],
        "provenance": provenance,
    }


def load_schema() -> dict:
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / "receipt.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def validate_schema(receipt: dict) -> None:
    try:
        import jsonschema
    except ImportError as e:  # pragma: no cover
        raise ReceiptValidationError("jsonschema not installed; cannot validate") from e
    jsonschema.validate(receipt, load_schema())


def derive_gpu_status(receipt: dict, ep: str) -> str:
    """Recompute the honest GPU status for ep in {'amdgpu','dml'} from raw evidence."""
    claimed = receipt["status"][ep]
    if claimed in ("NOT_RUN",) or claimed in GPU_BLOCKS:
        return claimed  # block/not-run states are declarations, not derivable
    host = receipt.get("host", {})
    session = receipt.get("session") or {}
    parity = receipt.get("parity") or {}
    task = receipt.get("task") or {}
    errors = receipt.get("errors") or []

    ep_name = {"amdgpu": "AMDGPU", "dml": "DML"}[ep]
    if session.get("ep") != ep_name:
        return "NOT_RUN" if claimed == "NOT_RUN" else "FAIL_RUNTIME"

    if not host.get("verified_device_identity"):
        return "BLOCKED_IDENTITY"

    counts = session.get("provider_node_counts") or {}
    # ORT profiler keys are real provider names: AMDGPU / DmlExecutionProvider
    ort_key = "AMDGPU" if ep == "amdgpu" else "DmlExecutionProvider"
    target_nodes = counts.get(ort_key, counts.get(ep_name, 0))
    if not isinstance(target_nodes, int) or isinstance(target_nodes, bool) or target_nodes < 0:
        raise ReceiptValidationError(f"{ep}: target-EP node count must be a non-negative integer")
    if target_nodes == 0:
        return "FAIL_NO_GPU_COMPUTE"

    # runtime errors for this ep mean FAIL_RUNTIME even if provider listed
    ep_errors = [e for e in errors if e.get("stage", "").startswith(ep_name)]
    if any(e.get("type") == "RUNTIME" for e in ep_errors):
        return "FAIL_RUNTIME"

    outputs = parity.get("outputs") or []
    if outputs:
        if not parity.get("by_name_matching", False):
            return "FAIL_GPU_PARITY"
        if not all(o.get("finite") for o in outputs):
            return "FAIL_GPU_PARITY"
        if not all(o.get("allclose") for o in outputs):
            return "FAIL_GPU_PARITY"
        if not parity.get("all_outputs_allclose"):
            return "FAIL_GPU_PARITY"

    cpu_nodes = counts.get("CPU", 0)
    if cpu_nodes == 0 and not session.get("stderr_fallback_signals"):
        status = GPU_PASS_FULL
    else:
        status = GPU_PASS_PARTIAL

    if task.get("oracle_kind") and task.get("passed") is False:
        return "FAIL_TASK"
    return status


def validate_receipt_strict(receipt: dict, *, expect_ep: str | None = None) -> dict:
    """Schema validate + recompute derivable statuses + reject forged labels.

    Returns {'ok': bool, 'derived': {...}, 'problems': [...]}.
    Never raises for content problems; raises only for structural JSON issues.
    """
    try:
        validate_schema(receipt)
    except Exception as e:  # jsonschema.ValidationError and friends
        return {"ok": False, "derived": dict(receipt.get("status", {})),
                "problems": [f"schema violation: {getattr(e, 'message', e)}"]}
    problems: list[str] = []
    derived = dict(receipt["status"])

    # host-status vs host-block gfx consistency (wrong-target-GPU forgery)
    ok_map = {"VERIFIED_GFX1151": "gfx1151", "VERIFIED_GFX1100": "gfx1100",
              "VERIFIED_GFX1201": "gfx1201"}
    claimed_gfx = ok_map.get(receipt["status"]["host"])
    if claimed_gfx and receipt.get("host", {}).get("gfx") != claimed_gfx:
        problems.append(
            f"status.host={receipt['status']['host']} contradicts host.gfx="
            f"{receipt.get('host', {}).get('gfx')!r}"
        )
    if receipt["status"]["host"].startswith("VERIFIED_") and not receipt.get("host", {}).get("verified_device_identity"):
        problems.append("status.host VERIFIED_* but verified_device_identity=false")

    # parity output coverage: every declared model output must be compared
    parity = receipt.get("parity") or {}
    out_names = (receipt.get("model") or {}).get("output_names")
    if parity.get("outputs") is not None and out_names:
        compared = {o.get("name") for o in parity["outputs"]}
        missing = [n for n in out_names if n not in compared]
        if missing and receipt["status"].get("amdgpu", "NOT_RUN").startswith("PASS_GPU"):
            problems.append(f"parity missing declared outputs: {missing}")

    for ep in ("amdgpu", "dml"):
        try:
            d = derive_gpu_status(receipt, ep)
        except ReceiptValidationError as e:
            return {"ok": False, "derived": derived, "problems": [str(e)]}
        derived[ep] = d
        if receipt["status"][ep] != d:
            problems.append(
                f"status.{ep} forged or stale: receipt says {receipt['status'][ep]!r}, "
                f"evidence derives {d!r}"
            )
    # FULL claims must not carry CPU compute nodes
    for ep, ep_name in (("amdgpu", "AMDGPU"), ("dml", "DML")):
        ort_key = "AMDGPU" if ep == "amdgpu" else "DmlExecutionProvider"
        if receipt["status"][ep] == GPU_PASS_FULL:
            counts = (receipt.get("session") or {}).get("provider_node_counts") or {}
            if counts.get("CPU", 0) > 0:
                problems.append(f"{ep}: FULL claim but CPU nodes > 0")
            if counts.get(ort_key, counts.get(ep_name, 0)) <= 0:
                problems.append(f"{ep}: FULL claim but {ort_key} node count not positive")
        # any recorded error for this EP is incompatible with a PASS claim
        if receipt["status"][ep].startswith("PASS_GPU"):
            ep_errors = [e for e in (receipt.get("errors") or [])
                         if str(e.get("stage", "")).upper().startswith(ep_name)]
            if ep_errors:
                problems.append(
                    f"{ep}: PASS claim but {len(ep_errors)} error(s) recorded "
                    f"({[e.get('type') for e in ep_errors]})"
                )
    # parity with NaN metrics can never be allclose
    for o in ((receipt.get("parity") or {}).get("outputs") or []):
        if o.get("allclose") and not o.get("finite"):
            problems.append(f"output {o.get('name')}: allclose=True with finite=False")
    ok = not problems
    if expect_ep and ok:
        ep = expect_ep.lower()
        if not receipt["status"][ep].startswith("PASS_GPU"):
            problems.append(f"expected GPU PASS for {ep}, got {receipt['status'][ep]}")
            ok = False
    return {"ok": ok, "derived": derived, "problems": problems}


def save_receipt(receipt: dict, out_dir: str | Path) -> Path:
    out = Path(out_dir) / f"receipt_{receipt['case_id']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = out.exists()
    if existing:
        raise ReceiptValidationError(f"refusing to overwrite existing immutable receipt {out}")
    out.write_text(json.dumps(receipt, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return out


def verify_receipt_file(path: str | Path) -> dict:
    receipt = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_receipt_strict(receipt)
