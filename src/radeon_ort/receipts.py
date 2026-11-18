"""Receipt construction and the STRICT fail-closed GPU-PASS validator.

v1 (legacy, preserved): statuses are recomputed from raw evidence; forged labels
fail. Frozen v1 receipts validate under ``mode="legacy"`` which additionally
interprets old ``PT_APP`` task blocks as smoke-only (never task parity).

v2 (R1-A): additive orthogonal classifications (``verdicts``), expected-output
manifest from the ONNX/session IO contract, worker exit-code enforcement,
device-binding attestation, honest fused-node attribution, task-parity evidence
contract, backend-routing evidence, RCA linkage. A v2 GPU PASS requires ALL of:
  1. session EP name matches the target provider, AND
  2. worker exit code == 0, AND
  3. device binding attestation with positive probe AMDGPU/DML nodes, AND
  4. final profile attributes >0 nodes to the real provider key, AND
  5. parity compares EXACTLY the expected output set (both directions, unique
     names, dtype+shape per contract, finite, allclose at declared thresholds), AND
  6. FULL claims have zero CPU compute nodes and no fallback signals, AND
  7. task PASS only from a TASK_PARITY block with real metrics vs a PT reference
     (``PT_APP`` is smoke-only and can never yield PASS_TASK).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path

RECEIPT_SCHEMA_VERSION = 2

GPU_PASS_FULL = "PASS_GPU_EXECUTED_FULL"
GPU_PASS_PARTIAL = "PASS_GPU_EXECUTED_PARTIAL"
GPU_FAILS = {
    "FAIL_NO_GPU_COMPUTE": "target-EP node count is zero: no GPU compute happened",
    "FAIL_GPU_PARITY": "GPU outputs diverge from CPU ORT reference beyond thresholds",
    "FAIL_RUNTIME": "session creation or run raised",
    "FAIL_TASK": "task-level oracle failed while numeric parity held",
}
GPU_BLOCKS = {"BLOCKED_EP", "BLOCKED_IDENTITY", "BLOCKED_RESOURCE"}

# ORT profiler keys are real provider names (never the python alias):
ORT_PROVIDER_KEYS = {"amdgpu": "AMDGPU", "dml": "DmlExecutionProvider", "cpu": "CPUExecutionProvider"}
# CPU compute may appear under either the real key or the short alias; both count.
CPU_COUNT_KEYS = ("CPUExecutionProvider", "CPU")


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
        "schema_version": 1,
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


def build_receipt_v2(*, run_id: str, case_id: str, status: ReceiptStatus, verdicts: dict,
                     host: dict, model: dict, input_block: dict, expected_outputs: list[dict],
                     worker: dict, session: dict | None = None, device_binding: dict | None = None,
                     parity: dict | None = None, task: dict | None = None,
                     task_parity: dict | None = None, benchmark_data: dict | None = None,
                     errors: list[dict] | None = None, rca_issue_ids: list[str] | None = None,
                     provenance: dict) -> dict:
    r = build_receipt(run_id=run_id, case_id=case_id, status=status, host=host, model=model,
                      session=session, parity=parity, task=task, benchmark_data=benchmark_data,
                      errors=errors, provenance=provenance)
    r["schema_version"] = 2
    r["receipt_version"] = 2
    r["verdicts"] = verdicts
    r["input"] = input_block
    r["expected_outputs"] = expected_outputs
    r["worker"] = worker
    r["device_binding"] = device_binding
    r["task_parity"] = task_parity
    r["rca_issue_ids"] = rca_issue_ids or []
    return r


def load_schema(schema_version: int = 1) -> dict:
    name = "receipt.json" if schema_version == 1 else f"receipt_v{schema_version}.json"
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / name
    return json.loads(schema_path.read_text(encoding="utf-8"))


def validate_schema(receipt: dict) -> None:
    try:
        import jsonschema
    except ImportError as e:  # pragma: no cover
        raise ReceiptValidationError("jsonschema not installed; cannot validate") from e
    jsonschema.validate(receipt, load_schema(int(receipt.get("schema_version", 1))))


# --- helpers -------------------------------------------------------------------


def _count_key(counts: dict, *keys: str) -> int:
    for k in keys:
        v = counts.get(k, 0)
        if isinstance(v, bool) or not isinstance(v, int):
            raise ReceiptValidationError(f"node count for {k!r} must be an int, got {v!r}")
        if v < 0:
            raise ReceiptValidationError(f"node count for {k!r} must be >= 0, got {v}")
        if v:
            return v
    return 0


def _cpu_nodes(counts: dict) -> int:
    return _count_key(counts, *CPU_COUNT_KEYS)


def _finite_metrics(problem_sink: list[str], where: str, value, path: str = "") -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, float) and not math.isfinite(value):
        problem_sink.append(f"{where}: non-finite metric at {path or where}: {value}")
    elif isinstance(value, dict):
        for k, v in value.items():
            _finite_metrics(problem_sink, where, v, f"{path}.{k}" if path else k)
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _finite_metrics(problem_sink, where, v, f"{path}[{i}]")


def _shape_matches(expected: list | None, actual: list | None) -> bool:
    if expected is None or actual is None:
        return True  # absent declaration cannot be checked positionally
    if len(expected) != len(actual):
        return False
    return all(e is None or int(e) == int(a) for e, a in zip(expected, actual))


def _ep_errors(receipt: dict, ep_name: str) -> list[dict]:
    return [e for e in (receipt.get("errors") or [])
            if str(e.get("stage", "")).upper().startswith(ep_name.upper())]


# --- v1 legacy derivation (tightened) ------------------------------------------


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
    ort_key = ORT_PROVIDER_KEYS[ep]
    target_nodes = _count_key(counts, ort_key, ep_name)
    if target_nodes == 0:
        return "FAIL_NO_GPU_COMPUTE"

    # runtime errors for this ep mean FAIL_RUNTIME even if provider listed
    ep_errors = [e for e in errors if e.get("stage", "").startswith(ep_name)]
    if any(e.get("type") == "RUNTIME" for e in ep_errors):
        return "FAIL_RUNTIME"

    outputs = parity.get("outputs") or []
    # R1-A tightening: a numeric PASS is NOT derivable from an empty/missing
    # parity block (vacuous pass closed). Frozen receipts with real outputs are
    # unaffected.
    if not outputs:
        return "FAIL_GPU_PARITY"
    if not parity.get("by_name_matching", False):
        return "FAIL_GPU_PARITY"
    if not all(o.get("finite") for o in outputs):
        return "FAIL_GPU_PARITY"
    if not all(o.get("allclose") for o in outputs):
        return "FAIL_GPU_PARITY"
    if not parity.get("all_outputs_allclose"):
        return "FAIL_GPU_PARITY"

    cpu_nodes = _cpu_nodes(counts)
    if cpu_nodes == 0 and not session.get("stderr_fallback_signals"):
        status = GPU_PASS_FULL
    else:
        status = GPU_PASS_PARTIAL

    if task.get("oracle_kind") and task.get("passed") is False:
        return "FAIL_TASK"
    return status


def _validate_v1(receipt: dict, expect_ep: str | None) -> dict:
    problems: list[str] = []
    interpretations: list[str] = []
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
        compared = [o.get("name") for o in parity["outputs"]]
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
        ort_key = ORT_PROVIDER_KEYS[ep]
        if receipt["status"][ep] == GPU_PASS_FULL:
            counts = (receipt.get("session") or {}).get("provider_node_counts") or {}
            try:
                if _cpu_nodes(counts) > 0:
                    problems.append(f"{ep}: FULL claim but CPU nodes > 0")
                if _count_key(counts, ort_key, ep_name) <= 0:
                    problems.append(f"{ep}: FULL claim but {ort_key} node count not positive")
            except ReceiptValidationError as e:
                problems.append(str(e))
        # any recorded error for this EP is incompatible with a PASS claim
        if receipt["status"][ep].startswith("PASS_GPU"):
            ep_errors = _ep_errors(receipt, ep_name)
            if ep_errors:
                problems.append(
                    f"{ep}: PASS claim but {len(ep_errors)} error(s) recorded "
                    f"({[e.get('type') for e in ep_errors]})"
                )
    # parity with NaN metrics can never be allclose
    for o in ((receipt.get("parity") or {}).get("outputs") or []):
        if o.get("allclose") and not o.get("finite"):
            problems.append(f"output {o.get('name')}: allclose=True with finite=False")

    # legacy interpretation: old PT_APP task blocks are smoke-only, never
    # task-parity evidence (mission §4). Recorded as interpretation, not a
    # problem, so frozen v1 receipts stay verifiable while nothing is blessed.
    task = receipt.get("task") or {}
    if task.get("oracle_kind") == "PT_APP":
        interpretations.append(
            "task_oracle=PASS_TASK on a v1 PT_APP receipt is PT_APP_SMOKE_ONLY under "
            "R1 rules (PT application ran; NOT task parity)")

    ok = not problems
    if expect_ep and ok:
        ep = expect_ep.lower()
        if not receipt["status"][ep].startswith("PASS_GPU"):
            problems.append(f"expected GPU PASS for {ep}, got {receipt['status'][ep]}")
            ok = False
    return {"ok": ok, "derived": derived, "problems": problems,
            "interpretations": interpretations}


# --- v2 derivation + validation -------------------------------------------------


def _expected_manifest(receipt: dict) -> dict:
    m = {}
    for o in receipt.get("expected_outputs") or []:
        m[o["name"]] = o
    return m


def derive_verdicts(receipt: dict) -> dict:
    """Orthogonal verdicts per EP + identity, derived from raw evidence only.

    execution reflects *compute* (nodes/identity/worker rc/errors); numeric_parity
    reflects the comparison contract; task_parity reflects the PT-reference
    comparison. The v1-combined status is the conjunction of the three.
    """
    out: dict = {"identity": "BLOCKED", "amdgpu": {}, "dml": {}}
    db = receipt.get("device_binding") or {}
    host_ok = bool((receipt.get("host") or {}).get("verified_device_identity"))
    # the receipt's active EP (receipts are per-EP; one is NOT_RUN)
    active_ep = "amdgpu" if receipt["status"]["amdgpu"] != "NOT_RUN" else "dml"

    def _binding_evidence(ep: str) -> bool:
        """EP-appropriate probe evidence that the bound device executed."""
        if ep == "amdgpu":
            return (db.get("probe") or {}).get("amdgpu_nodes", 0) > 0
        counts = ((db.get("final_profile") or {}).get("provider_node_counts")) or {}
        return counts.get("DmlExecutionProvider", 0) > 0

    if db.get("state") in ("EP_DEVICE_SELECTED", "PHYSICAL_GPU_IDENTITY_CORROBORATED") \
            and _binding_evidence(active_ep) and host_ok:
        out["identity"] = db["state"]
    elif db.get("state") == "IDENTITY_BINDING_UNPROVEN":
        out["identity"] = "IDENTITY_BINDING_UNPROVEN"
    elif host_ok:
        out["identity"] = "IDENTITY_BINDING_UNPROVEN"
    # else BLOCKED (host identity unverified)

    parity = receipt.get("parity") or {}
    outputs = parity.get("outputs") or []
    if not outputs or not parity.get("by_name_matching"):
        numeric = "NOT_COMPARED"
    else:
        numeric = "PASS" if (parity.get("all_outputs_allclose")
                             and all(o.get("allclose") and o.get("finite") for o in outputs)) \
            else "FAIL"
    tp = receipt.get("task_parity") or {}
    smoke = receipt.get("task") or {}
    for ep in ("amdgpu", "dml"):
        ort_key = ORT_PROVIDER_KEYS[ep]
        session = receipt.get("session") or {}
        counts = (session or {}).get("provider_node_counts") or {}
        ep_nodes = _count_key(counts, ort_key, {"amdgpu": "AMDGPU", "dml": "DML"}[ep])
        cpu = _cpu_nodes(counts)
        rc = (receipt.get("worker") or {}).get("exit_code", 0)
        errors = _ep_errors(receipt, ort_key)
        if receipt["status"][ep] == "NOT_RUN":
            execution = "NOT_RUN"
        elif receipt["status"][ep] in GPU_BLOCKS:
            execution = receipt["status"][ep]
        elif session.get("ep") != {"amdgpu": "AMDGPU", "dml": "DML"}[ep]:
            execution = "FAIL_RUNTIME"
        elif rc != 0:
            execution = "FAIL_RUNTIME"
        elif out["identity"] in ("BLOCKED", "IDENTITY_BINDING_UNPROVEN"):
            execution = "BLOCKED_IDENTITY"
        elif ep_nodes == 0:
            execution = "FAIL_NO_COMPUTE"
        elif errors:
            execution = "FAIL_RUNTIME"
        else:
            execution = "PASS_PARTIAL" if (cpu > 0 or session.get("stdout_fallback_signals")
                                           or session.get("stderr_fallback_signals")) \
                else "PASS_FULL"
        if tp:
            mine = str(tp.get("cand_source", "")).upper().startswith(
                {"amdgpu": "AMDGPU", "dml": "DML"}[ep].upper())
            if not mine:
                task_v = "NOT_RUN"
            elif tp.get("oracle_kind") != "TASK_PARITY":
                task_v = "TASK_PARITY_NOT_IMPLEMENTED"
            elif tp.get("passed") is True:
                task_v = "PASS_TASK_PARITY"
            else:
                task_v = "FAIL_TASK_PARITY"
        else:
            task_v = "NOT_RUN"
        out[ep] = {
            "execution": execution,
            "numeric_parity": numeric,
            "task_parity": task_v,
            "pt_app_smoke": ("PASS" if (smoke.get("oracle_kind") == "PT_APP"
                                        and smoke.get("passed") is not False
                                        and rc == 0) else
                             "FAIL" if smoke.get("oracle_kind") == "PT_APP" else "NOT_RUN"),
            "backend_routing": "BACKEND_UNVERIFIED",
            "benchmark": "MEASURED" if receipt.get("benchmark_data") else "NOT_MEASURED",
        }
    return out


def combine_v2_to_status(ep: str, verdict: dict) -> str:
    """Conjunction of orthogonal verdicts -> the v1-combined status label."""
    ex = verdict["execution"]
    if ex in ("NOT_RUN", "BLOCKED_EP", "BLOCKED_IDENTITY", "BLOCKED_RESOURCE",
              "FAIL_RUNTIME", "FAIL_NO_COMPUTE"):
        return V2_EXEC_TO_STATUS[ex]
    if verdict["numeric_parity"] != "PASS":
        return "FAIL_GPU_PARITY"
    if verdict["task_parity"] == "FAIL_TASK_PARITY":
        return "FAIL_TASK"
    return GPU_PASS_FULL if ex == "PASS_FULL" else GPU_PASS_PARTIAL


V2_EXEC_TO_STATUS = {
    "PASS_FULL": GPU_PASS_FULL,
    "PASS_PARTIAL": GPU_PASS_PARTIAL,
    "FAIL_NO_COMPUTE": "FAIL_NO_GPU_COMPUTE",
    "FAIL_RUNTIME": "FAIL_RUNTIME",
    "NOT_RUN": "NOT_RUN",
    "BLOCKED_EP": "BLOCKED_EP",
    "BLOCKED_IDENTITY": "BLOCKED_IDENTITY",
    "BLOCKED_RESOURCE": "BLOCKED_RESOURCE",
}


def _validate_v2(receipt: dict, expect_ep: str | None,
                 expected_thresholds: dict | None) -> dict:
    problems: list[str] = []
    derived = dict(receipt["status"])
    verdicts = receipt.get("verdicts") or {}

    # host-status vs host-block gfx consistency (wrong-target-GPU forgery)
    ok_map = {"VERIFIED_GFX1151": "gfx1151", "VERIFIED_GFX1100": "gfx1100",
              "VERIFIED_GFX1201": "gfx1201"}
    claimed_gfx = ok_map.get(receipt["status"]["host"])
    if claimed_gfx and (receipt.get("host") or {}).get("gfx") != claimed_gfx:
        problems.append(
            f"status.host={receipt['status']['host']} contradicts host.gfx="
            f"{(receipt.get('host') or {}).get('gfx')!r}")
    if receipt["status"]["host"].startswith("VERIFIED_") and not (
            (receipt.get("host") or {}).get("verified_device_identity")):
        problems.append("status.host VERIFIED_* but verified_device_identity=false")

    # --- expected-output manifest sanity
    exp = _expected_manifest(receipt)
    if not exp:
        problems.append("expected_outputs manifest is empty (required in v2)")
    if len(exp) != len(receipt.get("expected_outputs") or []):
        problems.append("expected_outputs contains duplicate names")

    # --- parity contract
    parity = receipt.get("parity")
    claimed_exec = {ep: (verdicts.get(ep) or {}).get("execution") for ep in ("amdgpu", "dml")}
    any_pass_claim = any(receipt["status"][ep].startswith("PASS_GPU") for ep in ("amdgpu", "dml"))
    if any_pass_claim:
        if not parity or not parity.get("outputs"):
            problems.append("GPU PASS claim with missing/empty parity outputs (vacuous pass)")
        else:
            names = [o.get("name") for o in parity["outputs"]]
            if len(names) != len(set(names)):
                problems.append(f"parity outputs contain duplicate names: {names}")
            missing = [n for n in exp if n not in names]
            extra = [n for n in names if n not in exp]
            if missing:
                problems.append(f"parity missing expected outputs: {missing}")
            if extra:
                problems.append(f"parity has unexpected outputs (not in graph contract): {extra}")
            for o in parity["outputs"]:
                e = exp.get(o.get("name"))
                if not e:
                    continue
                if o.get("dtype") != e.get("dtype"):
                    problems.append(
                        f"output {o['name']}: dtype {o.get('dtype')!r} != expected {e.get('dtype')!r}")
                if not _shape_matches(e.get("shape"), o.get("shape")):
                    problems.append(
                        f"output {o['name']}: shape {o.get('shape')} != expected {e.get('shape')}")
                if o.get("allclose") and not o.get("finite"):
                    problems.append(f"output {o['name']}: allclose=True with finite=False")
                if not o.get("allclose") and receipt["status"]["amdgpu"].startswith("PASS_GPU"):
                    problems.append(f"output {o['name']}: allclose=False incompatible with PASS")
            agg = all(o.get("allclose") and o.get("finite") for o in parity["outputs"])
            if bool(parity.get("all_outputs_allclose")) != agg:
                problems.append("all_outputs_allclose inconsistent with per-output results")
            if not parity.get("by_name_matching"):
                problems.append("by_name_matching must be true for numeric parity")
            th = parity.get("thresholds") or {}
            if expected_thresholds is not None:
                for k in ("atol", "rtol"):
                    want = expected_thresholds.get(k)
                    got = th.get(k)
                    if want is not None and got != want:
                        problems.append(
                            f"parity threshold {k}={got!r} != predeclared {want!r}")
            elif not th:
                problems.append("parity thresholds block missing (declared thresholds required)")
        _finite_metrics(problems, "parity", parity)

    # --- worker exit code
    rc = (receipt.get("worker") or {}).get("exit_code")
    if not isinstance(rc, int) or isinstance(rc, bool):
        problems.append(f"worker.exit_code must be an int, got {rc!r}")
    elif rc != 0:
        for ep in ("amdgpu", "dml"):
            if receipt["status"][ep].startswith("PASS_GPU"):
                problems.append(
                    f"{ep}: PASS claim but worker exit_code={rc} (fragment cannot certify)")

    # --- device binding attestation
    for ep in ("amdgpu", "dml"):
        if receipt["status"][ep].startswith("PASS_GPU"):
            db = receipt.get("device_binding") or {}
            if db.get("ep") != {"amdgpu": "AMDGPU", "dml": "DML"}[ep]:
                problems.append(f"{ep}: PASS claim without matching device_binding.ep")
            elif db.get("state") not in ("EP_DEVICE_SELECTED", "PHYSICAL_GPU_IDENTITY_CORROBORATED"):
                problems.append(
                    f"{ep}: PASS claim with device_binding.state={db.get('state')!r}")
            elif (db.get("probe") or {}).get("amdgpu_nodes", 0) <= 0 and ep == "amdgpu":
                problems.append(f"{ep}: PASS claim but probe evidenced zero AMDGPU nodes")
            final_counts = ((db.get("final_profile") or {}).get("provider_node_counts")) or {}
            ort_key = ORT_PROVIDER_KEYS[ep]
            try:
                if _count_key(final_counts, ort_key) <= 0:
                    problems.append(f"{ep}: device_binding.final_profile has no {ort_key} nodes")
            except ReceiptValidationError as e:
                problems.append(str(e))
            if db.get("attribution") not in ("fused_single_node", "multi_node"):
                problems.append(f"{ep}: attribution must record fused_single_node or multi_node")

    # --- identity corroboration level
    ident = verdicts.get("identity")
    if ident == "PHYSICAL_GPU_IDENTITY_CORROBORATED":
        if not (receipt.get("host") or {}).get("verified_device_identity"):
            problems.append("identity=PHYSICAL_GPU_IDENTITY_CORROBORATED but host identity unverified")
        if not ((receipt.get("device_binding") or {}).get("dxgi_corroboration")):
            problems.append("identity=PHYSICAL_GPU_IDENTITY_CORROBORATED without dxgi_corroboration")

    # --- session counts typing + FULL/PARTIAL semantics
    for ep, ep_name in (("amdgpu", "AMDGPU"), ("dml", "DML")):
        counts = (receipt.get("session") or {}).get("provider_node_counts") or {}
        ort_key = ORT_PROVIDER_KEYS[ep]
        try:
            ep_nodes = _count_key(counts, ort_key, ep_name)
            cpu = _cpu_nodes(counts)
        except ReceiptValidationError as e:
            problems.append(str(e))
            continue
        if receipt["status"][ep] == GPU_PASS_FULL:
            if cpu > 0:
                problems.append(f"{ep}: FULL claim but CPU nodes > 0")
            if ep_nodes <= 0:
                problems.append(f"{ep}: FULL claim but {ort_key} node count not positive")
            if (receipt.get("session") or {}).get("stdout_fallback_signals") \
                    or (receipt.get("session") or {}).get("stderr_fallback_signals"):
                problems.append(f"{ep}: FULL claim with fallback signals present")
        if receipt["status"][ep].startswith("PASS_GPU"):
            if _ep_errors(receipt, ep_name):
                problems.append(f"{ep}: PASS claim but errors recorded for this EP")

    # --- task parity evidence contract
    task_status = receipt["status"].get("task_oracle")
    tp = receipt.get("task_parity")
    old_task = receipt.get("task") or {}
    if old_task.get("oracle_kind") == "PT_APP" and task_status == "PASS_TASK":
        problems.append("PT_APP smoke labeled PASS_TASK (PT_APP can never be task parity)")
    if task_status == "PASS_TASK":
        if not tp or tp.get("oracle_kind") != "TASK_PARITY":
            problems.append("PASS_TASK requires a TASK_PARITY evidence block")
        else:
            if tp.get("passed") is not True:
                problems.append("PASS_TASK but task_parity.passed is not True")
            if not tp.get("metrics"):
                problems.append("PASS_TASK without metrics")
            if not tp.get("ref_source") or not tp.get("cand_source"):
                problems.append("PASS_TASK without ref/cand sources")
            if not tp.get("thresholds_declared"):
                problems.append("PASS_TASK without declared thresholds")
            if expected_thresholds and "task" in expected_thresholds:
                td = tp.get("thresholds_declared") or {}
                for k, want in expected_thresholds["task"].items():
                    if k not in td:
                        problems.append(
                            f"task threshold {k!r} missing from declared thresholds")
                    elif td[k] != want:
                        problems.append(
                            f"task threshold {k}={td[k]!r} != predeclared {want!r}")
            # metrics must actually satisfy the declared thresholds (no
            # passed=True with failing numbers)
            TH_METRIC_ALIAS = {"min_mask_iou": "mask_iou_mean", "min_oks": "oks_mean",
                               "min_rotated_iou": "rotated_iou_mean",
                               "max_rel_mae_vs_pt": "rel_mae"}
            td = tp.get("thresholds_declared") or {}
            mx = tp.get("metrics") or {}
            for k, want in td.items():
                if not k.startswith(("min_", "max_")):
                    continue
                metric_key = TH_METRIC_ALIAS.get(k, k[4:])
                value = mx.get(metric_key)
                if (not isinstance(value, (int, float)) or isinstance(value, bool)
                        or not math.isfinite(value)):
                    problems.append(
                        f"PASS_TASK missing/nonfinite required metric {metric_key!r} for {k}")
                    continue
                if k.startswith("min_") and value < want:
                    problems.append(
                        f"PASS_TASK but metric {metric_key}={value} < min {want}")
                if k.startswith("max_") and value > want:
                    problems.append(
                        f"PASS_TASK but metric {metric_key}={value} > max {want}")
            if td.get("require_top1_match") and mx.get("top1_match") is False:
                problems.append("PASS_TASK but top1_match=False with require_top1_match")
        _finite_metrics(problems, "task_parity", tp)
    # expected-empty claims (entity tasks only) need a discriminating negative control
    if tp and tp.get("passed") and tp.get("task") in ("detect", "segment", "pose", "obb") \
            and (tp.get("metrics") or {}).get("ref_entities") == 0:
        nc = tp.get("negative_control")
        if not nc or not nc.get("discriminating"):
            problems.append("expected-empty pass without a discriminating negative control")

    # --- backend routing honesty
    for ep in ("amdgpu", "dml"):
        route = (verdicts.get(ep) or {}).get("backend_routing")
        if route and route.startswith("CONFIRMED_"):
            evidence = ((receipt.get("session") or {}).get("backend_route_evidence"))
            if not evidence:
                problems.append(
                    f"{ep}: backend_routing={route} without backend_route_evidence "
                    "(umbrella provider name is not routing proof)")

    # --- RCA linkage: failed/unexpected-warning cases must reference incidents
    ep_errors = _ep_errors(receipt, "AMDGPU") + _ep_errors(receipt, "DML")
    if ep_errors and not receipt.get("rca_issue_ids"):
        problems.append("EP errors recorded but rca_issue_ids empty (RCA register linkage required)")

    # --- verdicts vs status consistency
    derived_verdicts = derive_verdicts(receipt)
    for ep in ("amdgpu", "dml"):
        want = combine_v2_to_status(ep, derived_verdicts[ep])
        derived[ep] = want
        if receipt["status"][ep] != want:
            problems.append(
                f"status.{ep} forged or stale: receipt says {receipt['status'][ep]!r}, "
                f"v2 evidence derives {want!r}")
        for dim in ("numeric_parity", "task_parity", "pt_app_smoke", "backend_routing"):
            claimed = (verdicts.get(ep) or {}).get(dim)
            if claimed and claimed != derived_verdicts[ep][dim]:
                problems.append(
                    f"verdicts.{ep}.{dim}={claimed!r} but evidence derives "
                    f"{derived_verdicts[ep][dim]!r}")

    # --- model/input provenance presence
    inp = receipt.get("input") or {}
    if any_pass_claim:
        if not inp.get("input_id"):
            problems.append("GPU PASS without input.input_id (deterministic input contract)")
        if not inp.get("tensor_sha256"):
            problems.append("GPU PASS without input.tensor_sha256")
        if not (receipt.get("model") or {}).get("onnx_sha256"):
            problems.append("GPU PASS without model.onnx_sha256")

    ok = not problems
    if expect_ep and ok:
        ep = expect_ep.lower()
        if not receipt["status"][ep].startswith("PASS_GPU"):
            problems.append(f"expected GPU PASS for {ep}, got {receipt['status'][ep]}")
            ok = False
    return {"ok": ok, "derived": derived, "problems": problems,
            "derived_verdicts": derived_verdicts}


def validate_receipt_strict(receipt: dict, *, expect_ep: str | None = None,
                            mode: str | None = None,
                            expected_thresholds: dict | None = None) -> dict:
    """Schema validate + recompute derivable statuses + reject forged labels.

    mode: None -> auto (schema_version 1 => legacy, >=2 => v2), 'legacy', or 'v2'.
    Returns {'ok': bool, 'derived': {...}, 'problems': [...], ...}.
    Never raises for content problems; raises only for structural JSON issues.
    """
    if mode is None:
        mode = "legacy" if int(receipt.get("schema_version", 1)) == 1 else "v2"
    try:
        validate_schema(receipt)
    except Exception as e:  # jsonschema.ValidationError and friends
        return {"ok": False, "derived": dict(receipt.get("status", {})),
                "problems": [f"schema violation: {getattr(e, 'message', e)}"]}
    if mode == "legacy":
        if int(receipt.get("schema_version", 1)) != 1:
            return {"ok": False, "derived": dict(receipt["status"]),
                    "problems": ["legacy mode is only valid for schema_version 1 receipts"]}
        return _validate_v1(receipt, expect_ep)
    return _validate_v2(receipt, expect_ep, expected_thresholds)


def save_receipt(receipt: dict, out_dir: str | Path) -> Path:
    out = Path(out_dir) / f"receipt_{receipt['case_id']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        raise ReceiptValidationError(f"refusing to overwrite existing immutable receipt {out}")
    out.write_text(json.dumps(receipt, indent=2, sort_keys=False, allow_nan=False) + "\n",
                   encoding="utf-8")
    return out


def verify_receipt_file(path: str | Path, **kwargs) -> dict:
    receipt = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_receipt_strict(receipt, **kwargs)
