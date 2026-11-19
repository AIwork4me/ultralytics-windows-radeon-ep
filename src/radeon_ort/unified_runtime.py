"""Unified Windows Radeon inference entrypoint.

predict() is the single developer-facing API for AMDGPU / DirectML / CPU on
Windows ML 2.x. It never guesses:

  * the target gfx is never defaulted (gfx1100 is not a fallback);
  * a GPU result requires positive target-EP profiler nodes, zero CPU nodes,
    by-name numeric parity against an independent CPU ORT run, and a device
    binding from a small whitelist of labels (not "any non-UNPROVEN string");
  * CPU business execution happens only with explicit allow_cpu=True under a
    policy that permits it, and is reported as CPU_ONLY_USER_AUTHORIZED;
  * the internal CPU *reference* (used for numeric parity) is always disclosed:
    allow_cpu=False forbids a CPU business result, not the reference, and the
    result says so explicitly;
  * GPU FULL and TASK VERIFIED are two independent conclusions;
  * every attempt runs in its own subprocess, inside its own Windows Job Object,
    with a private attempt directory and a wall-clock timeout.

Policies (R6-C):

  require_amdgpu            AMDGPU only; anything else is refused
  prefer_amdgpu_allow_dml   AMDGPU first, independent DML attempt if allowed
  gpu_first_allow_cpu       AMDGPU -> DML -> CPU (CPU only with allow_cpu=True)
  cpu_opt_in                direct CPU execution (explicit consent only)

Evidence layering (R7-D) - never collapsed into one word:

  gpu_full        the adopted EP really executed the graph on the GPU
  task_verified   an independent PT reference agrees within declared thresholds
  evidence_grade  whether the run carries raw, re-derivable attempt evidence
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

POLICIES = ("require_amdgpu", "prefer_amdgpu_allow_dml",
            "gpu_first_allow_cpu", "cpu_opt_in")

GPU_FULL = "GPU_FULL"
GPU_FULL_DML = "GPU_FULL_DML"
PARTIAL_CPU = "PARTIAL_CPU"
CPU_AUTHORIZED = "CPU_ONLY_USER_AUTHORIZED"
BLOCKED = "BLOCKED"
FAIL = "FAIL"

CONFIRMED = "PHYSICAL_BINDING_CONFIRMED"
ORDINAL_MAPPING = "EP_DEVICE_SELECTED_ORDINAL_MAPPING"
UNPROVEN = "UNPROVEN"

EVIDENCE_GRADE = "EVIDENCE"
INJECTED_GRADE = "INJECTED_TEST"
POLICY_ONLY_SCOPE = "POLICY_ONLY"

CPU_REFERENCE_ALLOW = "allow"
CPU_REFERENCE_FORBID = "forbid"
CPU_REFERENCE_MODES = (CPU_REFERENCE_ALLOW, CPU_REFERENCE_FORBID)

# Only these labels may support a GPU FULL claim, and only for the EP listed.
ALLOWED_BINDING = {
    "amdgpu": frozenset({CONFIRMED}),
    "dml": frozenset({ORDINAL_MAPPING}),
}

TASK_STATUSES = ("PASS_TASK", "FAIL_TASK", "TASK_ORACLE_BLOCKED", "NOT_RUN",
                 "NOT_REQUESTED")

REQUIRED_RESULT_FIELDS = (
    "requested_policy", "actual_ep", "attempted_eps", "physical_gpu",
    "device_binding_confidence", "model_sha256", "input_sha256",
    "target_ep_profile_nodes", "cpu_profile_nodes", "numeric_parity",
    "task_parity", "fallback_reason", "runtime_status", "code_provenance",
    "receipt_sha256",
    "gpu_full", "task_verified", "target_gfx", "input_fingerprint", "attempts",
    "readiness", "evidence_grade", "run_id",
)


class ContractError(ValueError):
    """Bad or contradictory arguments (CLI exit 2)."""


@dataclass
class Attempt:
    """One EP attempt, normalized from the worker fragment."""

    ep: str
    ok: bool = False
    status: str = FAIL
    error: str | None = None
    provider: str | None = None
    provider_node_counts: dict = field(default_factory=dict)
    target_nodes: int = 0
    cpu_nodes: int = 0
    parity: dict = field(default_factory=dict)
    binding: dict = field(default_factory=dict)
    binding_confidence: str = UNPROVEN
    device_binding: dict = field(default_factory=dict)
    device_identity: dict = field(default_factory=dict)
    provider_list: list = field(default_factory=list)
    outputs: dict | None = None
    exit_code: int | None = None
    log_path: str | None = None
    # R7-B/R7-C attempt provenance
    attempt_id: str | None = None
    attempt_dir: str | None = None
    worker_pid: int | None = None
    process: dict = field(default_factory=dict)
    container: str = "unknown"
    partial_artifacts: list = field(default_factory=list)
    fragment_matches_attempt: bool | None = None
    outputs_sha256: str | None = None
    outputs_path: str | None = None
    reference_attempt_id: str | None = None
    input_binding: dict = field(default_factory=dict)

    def trace(self) -> dict:
        """The attempt record that goes into the RunResult and the receipt."""
        return {
            "ep": self.ep, "ok": self.ok, "status": self.status,
            "error": self.error, "target_nodes": self.target_nodes,
            "cpu_nodes": self.cpu_nodes, "binding": self.binding_confidence,
            "exit_code": self.exit_code, "log": self.log_path,
            "attempt_id": self.attempt_id, "attempt_dir": self.attempt_dir,
            "worker_pid": self.worker_pid, "container": self.container,
            "process": self.process,
            "partial_artifacts": self.partial_artifacts,
            "fragment_matches_attempt": self.fragment_matches_attempt,
            "outputs_sha256": self.outputs_sha256,
            "outputs_path": self.outputs_path,
            "reference_attempt_id": self.reference_attempt_id,
            "input_binding": self.input_binding,
        }


def _feed_fingerprint(inputs: dict) -> str:
    """Mirror of unified_executors._feed_fingerprint (float32 bundle bytes)."""
    from .input_contract import tensor_sha256

    h = hashlib.sha256()
    for name in sorted(inputs):
        arr = np.ascontiguousarray(np.asarray(inputs[name], dtype=np.float32))
        h.update(name.encode("utf-8") + b"|")
        h.update(tensor_sha256(arr).encode("ascii"))
    return h.hexdigest()


def model_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def input_fingerprint(inputs: dict) -> dict:
    """Bind every input by name, dtype, shape and exact bytes (R6-B item 7)."""
    if not isinstance(inputs, dict) or not inputs:
        raise ContractError("inputs must be a non-empty dict of tensors")
    per_input: dict[str, dict] = {}
    agg = hashlib.sha256()
    for name in sorted(inputs):
        arr = np.ascontiguousarray(np.asarray(inputs[name]))
        raw = arr.tobytes()
        sha = hashlib.sha256(raw).hexdigest()
        per_input[name] = {"dtype": str(arr.dtype), "shape": list(arr.shape),
                           "nbytes": len(raw), "sha256": sha}
        agg.update(name.encode("utf-8") + b"|")
        agg.update(str(arr.dtype).encode("utf-8") + b"|")
        agg.update(",".join(str(d) for d in arr.shape).encode("utf-8") + b"|")
        agg.update(sha.encode("ascii") + b"\n")
    return {"combined_sha256": agg.hexdigest(), "inputs": per_input}


RUN_ID_RE = None


def validate_run_id(run_id: str) -> None:
    """A run id is a single safe path component, never a path expression.

    Without this, ".." or an absolute path would place evidence (and a frozen
    receipt) outside the workspace, or overwrite another run's evidence.
    """
    import re

    global RUN_ID_RE
    if RUN_ID_RE is None:
        RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,119}$")
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        raise ContractError(
            "INVALID_RUN_ID: " + repr(run_id) + "; expected 3-120 chars of "
            "[A-Za-z0-9._-] starting with an alphanumeric")


def validate_args(*, model_path, inputs, policy, allow_cpu, target_gfx,
                  verify_task=False, pt_reference=None, task=None,
                  cpu_reference=CPU_REFERENCE_ALLOW) -> None:
    if not isinstance(model_path, (str, Path)) or not Path(model_path).is_file():
        raise ContractError(f"model_path must be an existing file: {model_path!r}")
    if policy not in POLICIES:
        raise ContractError(f"policy must be one of {POLICIES}, got {policy!r}")
    cpu_policies = ("cpu_opt_in", "gpu_first_allow_cpu")
    if allow_cpu and policy not in cpu_policies:
        raise ContractError(
            f"allow_cpu=True is only valid with {cpu_policies}; consent is not "
            f"additive sugar for policy={policy!r}")
    if policy == "cpu_opt_in" and not allow_cpu:
        raise ContractError("policy='cpu_opt_in' requires explicit allow_cpu=True")
    if cpu_reference not in CPU_REFERENCE_MODES:
        raise ContractError(
            f"cpu_reference must be one of {CPU_REFERENCE_MODES}, got {cpu_reference!r}")
    if target_gfx is not None:
        from .device_identity import GFX_DEVICE_IDS

        if target_gfx not in GFX_DEVICE_IDS:
            raise ContractError(
                f"UNSUPPORTED_GFX_TARGET: {target_gfx}; supported: "
                f"{sorted(GFX_DEVICE_IDS)}")
    if verify_task:
        if not task:
            raise ContractError("verify_task=True requires task=<one of the 7 tasks>")
        from .task_oracle_runner import SUPPORTED_TASKS

        if task not in SUPPORTED_TASKS:
            raise ContractError(
                f"verify_task=True: unsupported task {task!r}; supported: "
                f"{list(SUPPORTED_TASKS)}")
        if not pt_reference:
            raise ContractError(
                "verify_task=True requires pt_reference=<the matching .pt checkpoint>; "
                "a missing reference is TASK_ORACLE_BLOCKED, not a silent PASS")


def _cpu_count(counts: dict) -> int:
    return sum(int(v) for k, v in (counts or {}).items() if "cpu" in k.lower())


def _target_count(counts: dict, ep: str) -> int:
    if ep == "amdgpu":
        return sum(int(v) for k, v in (counts or {}).items()
                   if "amdgpu" in k.lower())
    return sum(int(v) for k, v in (counts or {}).items()
               if "dml" in k.lower() or "directml" in k.lower())


def _parity_ok(parity: dict) -> tuple[bool, str]:
    """Strict by-name elementwise check over the worker's parity block."""
    if not isinstance(parity, dict) or not parity:
        return False, "PARITY_MISSING"
    if not parity.get("by_name_matching"):
        return False, "PARITY_OUTPUT_NAMES_DIFFER"
    outputs = parity.get("outputs") or []
    if not outputs:
        return False, "PARITY_NO_OUTPUTS"
    for o in outputs:
        name = o.get("name")
        if not o.get("allclose"):
            return False, f"PARITY_NOT_ALLCLOSE:{name}"
        if o.get("finite") is False:
            return False, f"PARITY_NON_FINITE:{name}"
        shape = o.get("shape") or []
        if not shape or any((d is not None and int(d) == 0) for d in shape):
            return False, f"PARITY_EMPTY_OUTPUT:{name}"
        if not o.get("dtype"):
            return False, f"PARITY_DTYPE_MISSING:{name}"
        if o.get("ref_shape") is not None and list(o["ref_shape"]) != list(shape):
            return False, f"PARITY_SHAPE_MISMATCH:{name}"
        if o.get("ref_dtype") is not None and o["ref_dtype"] != o.get("dtype"):
            return False, f"PARITY_DTYPE_MISMATCH:{name}"
    if parity.get("all_outputs_allclose") is not True:
        return False, "PARITY_AGGREGATE_FALSE"
    return True, ""


def _provider_is_target(provider, ep: str) -> bool:
    """Accept the EP's short label (AMDGPU/DML) or its ORT provider name."""
    p = str(provider or "").lower()
    if not p:
        return False
    if "cpu" in p:
        return False
    if ep == "amdgpu":
        return "amdgpu" in p
    return "dml" in p or "directml" in p


def evaluate_attempt(a: Attempt, *, ep: str, require_full_gpu: bool) -> tuple[bool, str]:
    """Return (gpu_full, reason). Every rejection has a specific reason."""
    if not a.ok:
        return False, a.error or f"{ep.upper()}_ATTEMPT_FAILED"
    if not _provider_is_target(a.provider, ep):
        return False, f"SILENT_CPU_FALLBACK: active provider {a.provider!r}"
    if a.provider_list and not any(_provider_is_target(p, ep)
                                   for p in a.provider_list):
        return False, f"SILENT_CPU_FALLBACK: session providers {a.provider_list}"
    if a.target_nodes <= 0:
        return False, f"{ep.upper()}_PROFILE_ZERO_NODES"
    if a.cpu_nodes != 0:
        return False, f"{ep.upper()}_CPU_NODES_PRESENT:{a.cpu_nodes}"
    ok, why = _parity_ok(a.parity)
    if not ok:
        return False, why
    allowed = ALLOWED_BINDING.get(ep, frozenset())
    if a.binding_confidence not in allowed:
        return False, f"DEVICE_BINDING_{a.binding_confidence or UNPROVEN}"
    if require_full_gpu and a.status != "FULL":
        return False, f"{ep.upper()}_NOT_FULL:{a.status}"
    return True, ""


def _physical_identity(binding: dict) -> dict:
    """Host-side device identity carried in the attempt evidence.

    The EP reports no LUID on this build, so the receipt binds what the host can
    actually prove: the DXGI adapter that was enumerated and the DID match. It
    is labelled indirect so it can never be read as an EP-reported identity.
    """
    corr = (binding or {}).get("dxgi_corroboration") or {}
    if not corr:
        return {}
    return {
        "evidence_kind": corr.get("evidence_kind"),
        "is_ep_reported": bool(corr.get("is_ep_reported")),
        "adapter_index": corr.get("adapter_index"),
        "vendor_id": corr.get("vendor_id"),
        "device_id": corr.get("device_id"),
        "did_match": corr.get("did_match"),
        "luid": corr.get("luid"),
        "description": corr.get("description"),
        "identity_basis": (binding or {}).get("identity_basis"),
        "mapping_assumption": corr.get("mapping_assumption"),
    }


def _attempt_from_fragment(ep: str, frag: dict, *, exit_code: int,
                           log_path: str) -> Attempt:
    session = frag.get("session") or {}
    counts = dict(session.get("provider_node_counts") or {})
    binding = frag.get("device_binding") or {}
    confidence = (binding.get("identity_basis")
                  or binding.get("binding_confidence") or UNPROVEN)
    if ep == "dml":
        corr = binding.get("dxgi_corroboration") or {}
        if corr.get("evidence_kind") == "HOST_DXGI_ENUMERATION_INDIRECT":
            confidence = ORDINAL_MAPPING
    elif ep == "amdgpu":
        # Only an EP-reported LUID equal to the physical LUID is confirmed.
        ep_id = binding.get("ep_reported_identity") or {}
        reported = (ep_id.get("reported") or {}).get("LUID")
        physical = (binding.get("dxgi_corroboration") or {}).get("luid")
        if reported and physical and str(reported) == str(physical):
            confidence = CONFIRMED
        else:
            confidence = UNPROVEN
    return Attempt(
        ep=ep,
        ok=exit_code == 0,
        status="FULL" if exit_code == 0 else FAIL,
        error=f"worker_exit={exit_code}" if exit_code else None,
        provider=session.get("ep"),
        provider_node_counts=counts,
        target_nodes=_target_count(counts, ep),
        cpu_nodes=_cpu_count(counts),
        parity=frag.get("parity") or {},
        binding=binding,
        binding_confidence=confidence,
        device_identity=_physical_identity(binding),
        device_binding=dict(binding),
        provider_list=list(session.get("requested_providers") or []),
        outputs=None,
        exit_code=exit_code,
        log_path=log_path,
        attempt_id=frag.get("attempt_id"),
        input_binding=frag.get("input_binding") or {},
    )


def _blank_result(policy: str, allow_cpu: bool) -> dict:
    return {
        "schema_version": "unified_run_v1",
        "requested_policy": policy,
        "actual_ep": None,
        "attempted_eps": [],
        "physical_gpu": None,
        "device_binding_confidence": UNPROVEN,
        "model_sha256": None,
        "input_sha256": None,
        "target_ep_profile_nodes": 0,
        "cpu_profile_nodes": 0,
        "numeric_parity": {},
        "task_parity": {"status": "NOT_REQUESTED"},
        "fallback_reason": None,
        "runtime_status": FAIL,
        "gpu_full": False,
        "task_verified": False,
        "cpu_user_authorized": bool(allow_cpu),
        "code_provenance": {},
        "receipt_sha256": None,
        "run_id": None,
        "attempts": [],
        "outputs": None,
        "target_gfx": None,
        "input_fingerprint": {},
        "cpu_reference": {"mode": CPU_REFERENCE_ALLOW, "used": False},
        "readiness": {"gpu_full": False, "task_verified": False,
                      "evidence_verified": False, "product_ready": False},
        "evidence_grade": EVIDENCE_GRADE,
        "verification_scope": "FULL",
        "task_oracle": None,
        "receipt": {},
    }


def _repo_relative(path, root: Path) -> str:
    """Repo-relative path for committed evidence (never a host path)."""
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except (ValueError, OSError):
        return str(Path(path).name)


def _merge_executor_attempts(result: dict, executor_obj) -> None:
    """Surface attempts the executor made on its own (the CPU reference).

    allow_cpu=False forbids a CPU *business* result. It does not make the
    numeric-parity reference disappear, so every internal attempt is merged in
    and labelled rather than omitted.
    """
    spawned = getattr(executor_obj, "spawned", None) or []
    have = {a.get("attempt_id") for a in (result.get("attempts") or [])
            if a.get("attempt_id")}
    for a in spawned:
        if a.attempt_id and a.attempt_id in have:
            continue
        t = a.trace()
        t["internal"] = True
        t["purpose"] = ("cpu_numeric_reference" if a.ep == "cpu" else "internal")
        result["attempts"].append(t)
        have.add(a.attempt_id)


def _disclose_cpu_reference(result: dict) -> None:
    """Never claim a request never touched the CPU when a reference ran on it.

    allow_cpu=False forbids a CPU *business* result. The numeric-parity
    reference is still a real CPU execution and must be disclosed as such.
    """
    refs = [a for a in (result.get("attempts") or []) if a.get("ep") == "cpu"]
    used = any(a.get("ok") for a in refs)
    result["cpu_reference"] = {
        "mode": result.get("cpu_reference", {}).get("mode", CPU_REFERENCE_ALLOW),
        "used": bool(used),
        "purpose": "independent CPU ORT numeric-parity reference (not a business result)",
        "counts_as_business_execution": bool(result.get("actual_ep") == "cpu"),
        "attempt_id": (refs[0].get("attempt_id") if refs else None),
        "attempts": [{"attempt_id": a.get("attempt_id"), "ok": a.get("ok"),
                      "outputs_sha256": a.get("outputs_sha256"),
                      "internal": a.get("internal", False),
                      "purpose": a.get("purpose")} for a in refs],
        "note": "a CPU reference that ran here is a real CPU execution and is "
                "listed in attempts[] as internal; it is not a business result",
    }


def _readiness(result: dict, *, verifier_ok: bool) -> dict:
    """Three separate claims; PRODUCT_READY additionally requires clean code.

    A simulated run (stub worker or injected case override) can never be
    evidence-verified or product-ready, however coherent its self-report is.
    """
    prov = result.get("code_provenance") or {}
    dirty = bool(prov.get("worktree_dirty"))
    gpu_full = bool(result.get("gpu_full"))
    task_verified = bool(result.get("task_verified"))
    evidence = (result.get("evidence_grade") == EVIDENCE_GRADE) and verifier_ok
    return {
        "gpu_full": gpu_full,
        "task_verified": task_verified,
        "evidence_verified": bool(evidence),
        "product_ready": bool(evidence and gpu_full and task_verified
                              and not dirty),
        "code_clean": not dirty,
        "evidence_grade": result.get("evidence_grade"),
    }


def predict(model_path, inputs, *, policy="require_amdgpu", target_gfx=None,
            target_gpu=None, allow_cpu=False, require_full_gpu=True, task=None,
            timeout_s=900, winml_python=None, work_dir=None, run_id=None,
            executor: Callable[..., Attempt] | None = None,
            repo_root: str | Path | None = None,
            verify_task: bool = False, pt_reference: str | Path | None = None,
            export_python: str | Path | None = None,
            task_params: dict | None = None,
            cancel_event=None,
            cpu_reference: str = CPU_REFERENCE_ALLOW,
            container: str = "job_object",
            worker_module: str = "runner._ep_worker",
            worker_case_extra: dict | None = None) -> dict[str, Any]:
    """Run one model under an explicit execution policy. See module docstring."""
    validate_args(model_path=model_path, inputs=inputs, policy=policy,
                  allow_cpu=allow_cpu, target_gfx=target_gfx,
                  verify_task=verify_task, pt_reference=pt_reference, task=task,
                  cpu_reference=cpu_reference)
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    if target_gfx is None:
        # Never default to an architecture: infer from the live DXGI DID and
        # fail closed when the host has zero or several supported gfx targets.
        from .device_identity import enum_dxgi_adapters, infer_gfx_from_adapters

        inferred = infer_gfx_from_adapters(enum_dxgi_adapters())
        if not inferred.get("ok"):
            raise ContractError(str(inferred.get("reason")))
        target_gfx = inferred["gfx"]
    result = _blank_result(policy, allow_cpu)
    result["target_gfx"] = target_gfx
    result["model_sha256"] = model_sha256(model_path)
    fp = input_fingerprint(inputs)
    result["input_sha256"] = fp["combined_sha256"]
    result["input_fingerprint"] = fp
    result["cpu_reference"]["mode"] = cpu_reference
    result["task_requested"] = task
    result["_model_path"] = str(model_path)
    result["_inputs"] = inputs
    if verify_task:
        result["task_parity"] = {"status": "NOT_RUN",
                                 "reason": "task oracle requested but no business "
                                           "result was adopted"}
    elif task:
        result["task_parity"] = {"status": "NOT_RUN",
                                 "reason": "verify_task=False: numeric parity only; "
                                           "no independent task oracle ran"}
    else:
        result["task_parity"] = {"status": "NOT_REQUESTED"}
    run_id = run_id or ("UNIFIED_" + str(int(__import__("time").time())))
    validate_run_id(run_id)
    result["run_id"] = run_id
    work = Path(work_dir) if work_dir else root / "reports" / "unified" / run_id
    work.mkdir(parents=True, exist_ok=True)

    # R7-C: a frozen receipt is immutable. Re-using a run_id is always a
    # contract error - there is deliberately no "overwrite" escape hatch.
    existing_receipt = work / f"receipt_{run_id}.json"
    if existing_receipt.is_file():
        raise ContractError(
            "RUN_ID_ALREADY_FROZEN: " + str(existing_receipt)
            + " exists; receipts are immutable - use a new run_id")
    executor_obj = None
    if executor is None:
        from .unified_executors import WorkerExecutor

        executor_obj = WorkerExecutor(
            gfx=target_gfx, winml_python=winml_python, work_dir=work,
            timeout_s=timeout_s, repo_root=root, cancel_event=cancel_event,
            container=container, cpu_reference=cpu_reference,
            worker_module=worker_module, worker_case_extra=worker_case_extra)
        executor = executor_obj.execute
        if getattr(executor_obj, "simulated", False):
            # A stub worker (or an injected case override) can mint any label it
            # likes; such a run can never be evidence-grade.
            result["evidence_grade"] = INJECTED_GRADE
            result["verification_scope"] = "SIMULATED_WORKER"
        else:
            result["evidence_grade"] = EVIDENCE_GRADE
    else:
        result["evidence_grade"] = INJECTED_GRADE
        result["verification_scope"] = POLICY_ONLY_SCOPE

    attempts: list[Attempt] = []

    def attempt(ep: str, reference: dict | None = None) -> Attempt:
        result["attempted_eps"].append(ep)
        try:
            a = executor(ep=ep, model_path=str(model_path), inputs=inputs,
                         reference=reference)
        except Exception as e:  # noqa: BLE001 - executor failure is an attempt failure
            a = Attempt(ep=ep, error=f"EXECUTOR_EXCEPTION: {type(e).__name__}: {e}")
        attempts.append(a)
        result["attempts"].append(a.trace())
        return a

    adopted: dict = {"attempt": None}

    def adopt(a: Attempt, status: str) -> None:
        adopted["attempt"] = a
        result.update({
            "actual_ep": a.ep,
            "runtime_status": status,
            "gpu_full": status in (GPU_FULL, GPU_FULL_DML),
            "device_binding_confidence": a.binding_confidence,
            "physical_gpu": a.device_identity or None,
            "target_ep_profile_nodes": a.target_nodes,
            "cpu_profile_nodes": a.cpu_nodes,
            "numeric_parity": a.parity,
            "outputs": a.outputs,
            "adopted_attempt_id": a.attempt_id,
            "adopted_attempt_index": len(attempts) - 1,
            "adopted_outputs_sha256": a.outputs_sha256,
        })

    def finish() -> dict:
        _merge_executor_attempts(result, executor_obj)
        fp = getattr(executor_obj, "last_feed_fingerprint", None)
        if fp:
            result["feed_fingerprint_sha256"] = fp
        _disclose_cpu_reference(result)
        if verify_task:
            _run_oracle_for_adopted(
                result, adopted.get("attempt"), root=root, work=work,
                task=task, pt_reference=pt_reference,
                export_python=export_python, task_params=task_params or {},
                timeout_s=timeout_s, model_path=model_path, inputs=inputs)
        return _finalize(result, work, root)

    if policy == "cpu_opt_in":
        c = attempt("cpu")
        if c.ok:
            adopt(c, CPU_AUTHORIZED)
            result["actual_ep"] = "cpu"
            result["cpu_user_authorized"] = True
        else:
            result["fallback_reason"] = "CPU_FAILED: " + str(c.error)
            result["runtime_status"] = FAIL
        return finish()

    amd = attempt("amdgpu")
    full, why = evaluate_attempt(amd, ep="amdgpu", require_full_gpu=require_full_gpu)
    if full:
        adopt(amd, GPU_FULL)
        return finish()
    amd_reason = f"AMDGPU_FAILED: {why}"
    result["fallback_reason"] = amd_reason

    if policy == "require_amdgpu":
        result["runtime_status"] = BLOCKED if amd.status == "BLOCKED" else FAIL
        return finish()

    dml = attempt("dml", reference=None)
    dfull, dwhy = evaluate_attempt(dml, ep="dml", require_full_gpu=require_full_gpu)
    if dfull:
        adopt(dml, GPU_FULL_DML)
        result["fallback_reason"] = amd_reason
        return finish()
    dml_reason = f"DML_FAILED: {dwhy}"

    if policy == "prefer_amdgpu_allow_dml":
        result["fallback_reason"] = amd_reason + "; " + dml_reason
        result["runtime_status"] = BLOCKED
        return finish()

    # gpu_first_allow_cpu
    if allow_cpu:
        c = attempt("cpu")
        if c.ok:
            adopt(c, CPU_AUTHORIZED)
            result["actual_ep"] = "cpu"
            result["cpu_user_authorized"] = True
            result["fallback_reason"] = amd_reason + "; " + dml_reason
            return finish()
        cpu_reason = "CPU_FAILED: " + str(c.error)
    else:
        cpu_reason = "CPU_NOT_AUTHORIZED: allow_cpu=False"
    result["fallback_reason"] = f"{amd_reason}; {dml_reason}; {cpu_reason}"
    result["runtime_status"] = BLOCKED
    return finish()


def _run_oracle_for_adopted(result: dict, a: Attempt | None, *, root: Path,
                            work: Path, task: str, pt_reference, export_python,
                            task_params: dict, timeout_s: int, model_path,
                            inputs: dict) -> None:
    """Attach the independent task-oracle evidence to an adopted result."""
    from . import task_oracle_runner as tor

    if a is None:
        result["task_parity"] = {
            "status": tor.BLOCKED, "passed": False,
            "reason_code": "TASK_ORACLE_NO_BUSINESS_RESULT",
            "reason": "no GPU/CPU result was adopted, so there is nothing to verify",
            "task": task,
        }
        result["task_verified"] = False
        return
    if not pt_reference:
        result["task_parity"] = {
            "status": tor.BLOCKED, "passed": False,
            "reason_code": "TASK_ORACLE_REFERENCE_MISSING",
            "reason": "verify_task=True without pt_reference", "task": task,
        }
        result["task_verified"] = False
        return
    attempt_dir = Path(a.attempt_dir) if a.attempt_dir else work / "oracle"
    result_dir = attempt_dir / "task_oracle"
    outputs_npz: Path | None = None
    outputs_source = None
    if a.outputs_path and Path(a.outputs_path).is_file():
        outputs_npz = Path(a.outputs_path)
        outputs_source = "attempt_outputs_file"
    elif isinstance(a.outputs, dict) and a.outputs:
        attempt_dir.mkdir(parents=True, exist_ok=True)
        outputs_npz = attempt_dir / "candidate_outputs.npz"
        np.savez(outputs_npz, **{k: np.asarray(v) for k, v in a.outputs.items()})
        outputs_source = "in_memory_snapshot"
    if outputs_npz is None:
        result["task_parity"] = {
            "status": tor.BLOCKED, "passed": False,
            "reason_code": "TASK_ORACLE_OUTPUTS_MISSING",
            "reason": "the adopted attempt produced no raw output artefact to verify",
            "task": task,
        }
        result["task_verified"] = False
        return
    if export_python is None:
        cand = root / ".venv-export" / "Scripts" / "python.exe"
        export_python = cand if cand.is_file() else None
    block = tor.run_task_oracle(
        onnx_path=model_path, pt_reference=pt_reference, outputs_npz=outputs_npz,
        inputs=inputs, task=task, export_python=export_python, repo_root=root,
        attempt_dir=attempt_dir, result_dir=result_dir,
        imgsz=task_params.get("imgsz"), conf=float(task_params.get("conf", 0.25)),
        timeout_s=timeout_s)
    block["candidate_outputs_source"] = outputs_source
    block["candidate_attempt_id"] = a.attempt_id
    block["candidate_ep"] = a.ep
    result["task_oracle"] = block
    result["task_parity"] = {
        "status": block.get("status"),
        "passed": bool(block.get("passed")),
        "task": task,
        "reason": block.get("reason"),
        "reason_code": block.get("reason_code"),
        "metrics": block.get("metrics") or {},
        "thresholds": block.get("thresholds") or {},
        "ref_fingerprint_sha256": block.get("ref") and None,
        "oracle_source": block.get("oracle_source"),
        "binding": block.get("binding") or {},
        "candidate_attempt_id": a.attempt_id,
        "evidence_dir": _repo_relative(result_dir, root),
    }
    result["task_parity"]["ref_sha256"] = (block.get("binding") or {}).get("pt_reference_sha256")
    result["task_parity"]["onnx_sha256"] = (block.get("binding") or {}).get("onnx_sha256")
    result["task_parity"]["ref_fingerprint_sha256"] = block.get("ref_fingerprint_sha256")
    result["task_parity"]["cand_fingerprint_sha256"] = block.get("cand_fingerprint_sha256")
    result["task_verified"] = bool(block.get("status") == tor.PASS_TASK)


def _finalize(result: dict, work: Path, root: Path) -> dict:
    from . import contract as contract_mod

    model_path_ref = result.pop("_model_path", None)
    inputs_ref = result.pop("_inputs", None)

    try:
        result["code_provenance"] = contract_mod.code_state(root)
    except Exception as e:  # noqa: BLE001 - provenance failure must not hide the run
        result["code_provenance"] = {"error": f"{type(e).__name__}: {e}"}
    body = {k: v for k, v in result.items()
            if k not in ("outputs", "receipt_sha256", "readiness", "receipt",
                         "receipt_write_error")}
    body["captured_utc"] = _utc()
    receipt = {**body, "schema_version": "unified_run_v1"}
    text = json.dumps(receipt, sort_keys=True, default=_json_default)
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    result["receipt_sha256"] = sha
    receipt_path = work / f"receipt_{result['run_id']}.json"
    file_sha = None
    payload = (text + "\n").encode("utf-8")
    try:
        tmp = receipt_path.with_name(receipt_path.name + ".tmp")
        # binary write: text mode would translate LF to CRLF on Windows and the
        # published file_sha256 would no longer match the bytes on disk.
        with open(tmp, "wb") as fh:
            fh.write(payload)
        import os
        os.replace(tmp, receipt_path)
        file_sha = hashlib.sha256(payload).hexdigest()
    except OSError as e:
        result["receipt_write_error"] = str(e)
    result["receipt"] = {
        "path": receipt_path.name,
        "body_sha256": sha,
        "file_sha256": file_sha,
        "schema_version": "unified_run_v1",
        "written": file_sha is not None,
    }
    # The readiness layer is computed last, from the finalized evidence, and is
    # intentionally excluded from the receipt body (it is derived, not stored).
    result["readiness"] = {"gpu_full": bool(result.get("gpu_full")),
                           "task_verified": bool(result.get("task_verified")),
                           "evidence_verified": False,
                           "product_ready": False,
                           "code_clean": not bool(
                               (result.get("code_provenance") or {}).get("worktree_dirty")),
                           "evidence_grade": result.get("evidence_grade")}
    v = verify_run_result(result, work_dir=work, check_receipt=True,
                          model_path=model_path_ref, inputs=inputs_ref, deep=True)
    result["verifier_summary"] = {"verdict": v["verdict"],
                                  "failed": v["failed"],
                                  "unverified": v["unverified"]}
    result["readiness"] = _readiness(result, verifier_ok=(v["verdict"] == "RUNRESULT_VALID"))
    return result


def _json_default(o):
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    return str(o)


def _utc() -> str:
    from .receipts import utc_now_iso

    return utc_now_iso()


# --- independent verifier ----------------------------------------------------------
def _check_task_parity(r: dict) -> list[tuple[str, Any, str]]:
    """Re-derive the task claim; never trust task_verified on its own."""
    out: list[tuple[str, Any, str]] = []
    tp = r.get("task_parity") or {}
    status = tp.get("status")
    out.append(("task_status_known", status in TASK_STATUSES, str(status)))
    verified = bool(r.get("task_verified"))
    if status == "PASS_TASK":
        out.append(("task_pass_matches_flag", verified is True,
                    "task_verified=" + str(verified)))
        out.append(("task_pass_has_metrics", bool(tp.get("metrics")),
                    str(sorted((tp.get("metrics") or {}).keys()))))
        out.append(("task_pass_has_thresholds", bool(tp.get("thresholds")),
                    str(tp.get("thresholds"))))
        out.append(("task_pass_has_passed_true",
                    (r.get("task_oracle") or {}).get("passed") is True,
                    str((r.get("task_oracle") or {}).get("passed"))))
    elif status in ("FAIL_TASK", "TASK_ORACLE_BLOCKED", "NOT_RUN", "NOT_REQUESTED"):
        out.append(("task_flag_consistent_with_status", verified is False,
                    "status=" + str(status) + " task_verified=" + str(verified)))
    else:
        out.append(("task_flag_consistent_with_status", verified is False,
                    "status=" + str(status)))
    if status == "PASS_TASK":
        b = tp.get("binding") or {}
        out.append(("task_pass_binds_onnx", bool(b.get("onnx_sha256")),
                    str(b.get("onnx_sha256"))))
        out.append(("task_pass_binds_reference", bool(b.get("pt_reference_sha256")),
                    str(b.get("pt_reference_sha256"))))
        out.append(("task_pass_binds_input", bool(b.get("input_tensor_sha256")),
                    str(b.get("input_tensor_sha256"))))
    return out


def verify_run_result(r: dict, *, model_path=None, inputs=None,
                      work_dir=None, check_receipt: bool = True,
                      deep: bool = False) -> dict:
    """Independent re-check of a RunResult.

    Checks return True (verified), False (contradiction -> INVALID) or None
    (the verifier has no raw evidence -> UNVERIFIED). A result can therefore
    never be reported VALID on the strength of a producer-filled label alone.
    """
    checks: list[dict] = []

    def chk(name: str, ok, detail: str = "") -> None:
        checks.append({"check": name, "ok": None if ok is None else bool(ok),
                       "detail": str(detail)[:240]})

    for f in REQUIRED_RESULT_FIELDS:
        chk(f"field:{f}", f in r, "" if f in r else "missing")

    status = r.get("runtime_status")
    grade = r.get("evidence_grade", EVIDENCE_GRADE)
    if status in (GPU_FULL, GPU_FULL_DML):
        chk("target_nodes_positive", int(r.get("target_ep_profile_nodes") or 0) > 0)
        chk("cpu_nodes_zero", int(r.get("cpu_profile_nodes") or 0) == 0)
        ok, why = _parity_ok(r.get("numeric_parity") or {})
        chk("parity_strict", ok, why)
        ep = "amdgpu" if status == GPU_FULL else "dml"
        chk("binding_whitelisted",
            r.get("device_binding_confidence") in ALLOWED_BINDING[ep],
            r.get("device_binding_confidence"))
        chk("gpu_full_flag", r.get("gpu_full") is True)
        chk("gpu_not_claimed_as_task",
            r.get("task_verified") in (True, False), "task_verified is boolean")
    if status in (BLOCKED, FAIL):
        chk("blocked_not_claiming_gpu", r.get("gpu_full") is False,
            "status=" + str(status) + " gpu_full=" + str(r.get("gpu_full")))
        chk("blocked_not_claiming_task", r.get("task_verified") is False,
            "status=" + str(status) + " task_verified=" + str(r.get("task_verified")))
    if status in (GPU_FULL, GPU_FULL_DML, CPU_AUTHORIZED, BLOCKED, FAIL):
        chk("status_known", True)
    else:
        chk("status_known", False, str(status))
    if status == CPU_AUTHORIZED:
        chk("cpu_consent_true", r.get("cpu_user_authorized") is True)
        chk("cpu_not_claimed_as_gpu", r.get("gpu_full") is False)
    chk("attempted_eps_sane", all(e in ("amdgpu", "dml", "cpu")
                                  for e in (r.get("attempted_eps") or [])))
    sha = str(r.get("receipt_sha256") or "")
    chk("receipt_sha_64hex", len(sha) == 64 and all(c in "0123456789abcdef" for c in sha))
    ms = str(r.get("model_sha256") or "")
    chk("model_sha_64hex", len(ms) == 64 and all(c in "0123456789abcdef" for c in ms))

    # --- task layer ------------------------------------------------------------
    for name, ok, detail in _check_task_parity(r):
        chk(name, ok, detail)

    # --- attempt / artefact binding --------------------------------------------
    attempts = r.get("attempts") or []
    adopted_id = r.get("adopted_attempt_id")
    adopted_idx = r.get("adopted_attempt_index")
    if status in (GPU_FULL, GPU_FULL_DML, CPU_AUTHORIZED):
        if grade == EVIDENCE_GRADE:
            chk("adopted_attempt_present", bool(adopted_id), str(adopted_id))
            match = [a for a in attempts if a.get("attempt_id") == adopted_id]
            chk("adopted_attempt_in_trace", len(match) == 1, str(len(match)))
        else:
            match = ([attempts[adopted_idx]]
                     if isinstance(adopted_idx, int) and 0 <= adopted_idx < len(attempts)
                     else [])
            chk("adopted_attempt_in_trace", len(match) == 1,
                "injected executor index=" + str(adopted_idx))
        if match:
            a = match[0]
            chk("adopted_attempt_ok", a.get("ok") is True, str(a.get("ok")))
            chk("adopted_attempt_ep_matches", a.get("ep") == r.get("actual_ep"),
                str(a.get("ep")))
            if grade == EVIDENCE_GRADE:
                chk("adopted_attempt_id_present", bool(a.get("attempt_id")),
                    str(a.get("attempt_id")))
                chk("adopted_fragment_matches", a.get("fragment_matches_attempt") is True,
                    str(a.get("fragment_matches_attempt")))
                chk("adopted_outputs_hashed",
                    bool(a.get("outputs_sha256")) and
                    a.get("outputs_sha256") == r.get("adopted_outputs_sha256"),
                    str(a.get("outputs_sha256")))
                proc = a.get("process") or {}
                chk("adopted_process_evidence", bool(proc), str(sorted(proc.keys())))
                chk("adopted_no_survivors", proc.get("survivors") == [],
                    str(proc.get("survivors")))
                chk("adopted_input_binding",
                    (a.get("input_binding") or {}).get("ok") is True,
                    str((a.get("input_binding") or {}).get("ok")))
                chk("adopted_feed_is_the_requested_tensor",
                    bool(r.get("feed_fingerprint_sha256"))
                    and (a.get("input_binding") or {}).get("actual_sha256")
                    == r.get("feed_fingerprint_sha256"),
                    "worker=" + str((a.get("input_binding") or {}).get("actual_sha256"))
                    + " requested=" + str(r.get("feed_fingerprint_sha256")))
                phys = r.get("physical_gpu") or (a.get("device_binding") or {})
                chk("adopted_device_identity_present",
                    bool((phys or {}).get("device_id")),
                    str((phys or {}).get("device_id")))
                chk("adopted_device_did_match",
                    (phys or {}).get("did_match") is True,
                    str((phys or {}).get("did_match")))
    if grade != EVIDENCE_GRADE:
        chk("simulated_run_is_not_evidence", True,
            "grade=" + str(grade) + " - structural checks only")
    if grade == EVIDENCE_GRADE:
        for a in attempts:
            if a.get("ok"):
                chk("attempt_" + str(a.get("ep")) + "_no_survivors",
                    (a.get("process") or {}).get("survivors") == [],
                    str((a.get("process") or {}).get("survivors")))

    # --- raw-evidence re-derivation -------------------------------------------
    if model_path is not None and Path(str(model_path)).is_file():
        chk("model_sha_recomputed", model_sha256(model_path) == r.get("model_sha256"),
            "recomputed from the model file")
    elif deep:
        chk("model_sha_recomputed", None, "no model_path supplied to the verifier")
    if inputs is not None:
        chk("input_sha_recomputed",
            input_fingerprint(inputs)["combined_sha256"] == r.get("input_sha256"),
            "recomputed from the live tensors")
    elif deep:
        chk("input_sha_recomputed", None, "no inputs supplied to the verifier")

    if check_receipt and work_dir is not None:
        rec = r.get("receipt") or {}
        path = Path(work_dir) / str(rec.get("path") or "")
        if rec.get("written") and path.is_file():
            data = path.read_bytes()
            got = hashlib.sha256(data).hexdigest()
            chk("receipt_file_sha_matches", got == rec.get("file_sha256"),
                "file=" + got[:16])
            try:
                body = json.loads(data.decode("utf-8"))
                body_text = json.dumps(body, sort_keys=True, default=_json_default)
                chk("receipt_body_sha_matches",
                    hashlib.sha256(body_text.encode("utf-8")).hexdigest()
                    == r.get("receipt_sha256"), "recomputed from the receipt file")
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                chk("receipt_body_sha_matches", False, str(e))
        elif rec.get("written"):
            chk("receipt_file_sha_matches", False, "receipt file missing")
        else:
            chk("receipt_file_present", False,
                "the result declares no written receipt")

    # --- CPU disclosure --------------------------------------------------------
    cpu_ref = r.get("cpu_reference") or {}
    chk("cpu_reference_disclosed", "used" in cpu_ref, str(cpu_ref.get("used")))
    if r.get("actual_ep") == "cpu":
        chk("cpu_business_requires_consent", r.get("cpu_user_authorized") is True,
            str(r.get("cpu_user_authorized")))

    # --- readiness layering ----------------------------------------------------
    rd = r.get("readiness") or {}
    if rd:
        chk("readiness_gpu_matches", bool(rd.get("gpu_full")) == bool(r.get("gpu_full")))
        chk("readiness_task_matches",
            bool(rd.get("task_verified")) == bool(r.get("task_verified")))
        chk("readiness_product_needs_both",
            (not rd.get("product_ready"))
            or (bool(r.get("gpu_full")) and bool(r.get("task_verified"))))

    if inputs is not None:
        chk("feed_fingerprint_matches_inputs",
            r.get("feed_fingerprint_sha256") is not None
            and r.get("feed_fingerprint_sha256") == _feed_fingerprint(inputs),
            str(r.get("feed_fingerprint_sha256")))
    failed = [c for c in checks if c["ok"] is False]
    unverified = [c for c in checks if c["ok"] is None]
    if failed:
        verdict = "RUNRESULT_INVALID"
    elif unverified:
        verdict = "RUNRESULT_UNVERIFIED"
    elif grade != EVIDENCE_GRADE:
        # A simulated run is coherent but cannot attest real execution.
        verdict = "RUNRESULT_UNVERIFIED"
    else:
        verdict = "RUNRESULT_VALID"
    return {"verdict": verdict, "checks": checks,
            "failed": [c["check"] for c in failed],
            "unverified": [c["check"] for c in unverified],
            "evidence_grade": grade,
            "verification_scope": r.get("verification_scope", "FULL")}


def validate_run_result_schema(r: dict) -> dict:
    """JSON-Schema check of the persisted RunResult (if jsonschema is present)."""
    import json as _json

    root = Path(__file__).resolve().parents[2]
    schema_path = root / "schemas" / "unified_run.schema.json"
    if not schema_path.is_file():        return {"ok": None, "reason": "SCHEMA_MISSING"}
    try:
        import jsonschema
    except ImportError:
        return {"ok": None, "reason": "JSONSCHEMA_NOT_INSTALLED"}
    schema = _json.loads(schema_path.read_text(encoding="utf-8"))
    try:
        jsonschema.validate({k: v for k, v in r.items() if k != "outputs"}, schema)
    except jsonschema.ValidationError as e:
        return {"ok": False, "reason": "SCHEMA_INVALID", "detail": str(e)[:400]}
    return {"ok": True, "reason": None}