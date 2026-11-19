"""Unified Windows Radeon inference entrypoint (R6).

predict() is the single developer-facing API for AMDGPU / DirectML / CPU on
Windows ML 2.x. It never guesses:

  * the target gfx is never defaulted (gfx1100 is not a fallback);
  * a GPU result requires positive target-EP profiler nodes, zero CPU nodes,
    by-name numeric parity against an independent CPU ORT run, and a device
    binding from a small whitelist of labels (not "any non-UNPROVEN string");
  * CPU execution happens only with explicit `allow_cpu=True` under a policy
    that permits it, and is reported as CPU_ONLY_USER_AUTHORIZED, never as GPU;
  * GPU FULL and TASK VERIFIED are separate booleans, so a numeric-only pass
    cannot masquerade as a validated task;
  * every attempt runs in its own subprocess with a wall-clock timeout and a
    private log, so a wedged EP cannot hang the caller.

Policies (R6-C):

  require_amdgpu            AMDGPU only; anything else is refused
  prefer_amdgpu_allow_dml   AMDGPU first, independent DML attempt if allowed
  gpu_first_allow_cpu       AMDGPU -> DML -> CPU (CPU only with allow_cpu=True)
  cpu_opt_in                direct CPU execution (explicit consent only)
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

# Only these labels may support a GPU FULL claim, and only for the EP listed.
ALLOWED_BINDING = {
    "amdgpu": frozenset({CONFIRMED}),
    "dml": frozenset({ORDINAL_MAPPING}),
}

REQUIRED_RESULT_FIELDS = (
    "requested_policy", "actual_ep", "attempted_eps", "physical_gpu",
    "device_binding_confidence", "model_sha256", "input_sha256",
    "target_ep_profile_nodes", "cpu_profile_nodes", "numeric_parity",
    "task_parity", "fallback_reason", "runtime_status", "code_provenance",
    "receipt_sha256",
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
    device_identity: dict = field(default_factory=dict)
    provider_list: list = field(default_factory=list)
    outputs: dict | None = None
    exit_code: int | None = None
    log_path: str | None = None


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


def validate_args(*, model_path, inputs, policy, allow_cpu, target_gfx) -> None:
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
    if target_gfx is not None:
        from .device_identity import GFX_DEVICE_IDS

        if target_gfx not in GFX_DEVICE_IDS:
            raise ContractError(
                f"UNSUPPORTED_GFX_TARGET: {target_gfx}; supported: "
                f"{sorted(GFX_DEVICE_IDS)}")


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
        device_identity={k: v for k, v in (frag.get("host") or {}).items()},
        provider_list=list(session.get("requested_providers") or []),
        outputs=None,
        exit_code=exit_code,
        log_path=log_path,
    )


def _blank_result(policy: str, allow_cpu: bool) -> dict:
    return {
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
    }


def predict(model_path, inputs, *, policy="require_amdgpu", target_gfx=None,
            target_gpu=None, allow_cpu=False, require_full_gpu=True, task=None,
            timeout_s=900, winml_python=None, work_dir=None, run_id=None,
            executor: Callable[..., Attempt] | None = None,
            repo_root: str | Path | None = None) -> dict[str, Any]:
    """Run one model under an explicit execution policy. See module docstring."""
    validate_args(model_path=model_path, inputs=inputs, policy=policy,
                  allow_cpu=allow_cpu, target_gfx=target_gfx)
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
    result["task_parity"] = ({"status": "NOT_RUN",
                              "reason": "unified API does not run the PT task "
                                        "oracle; use runner.run_matrix for a "
                                        "TASK_VERIFIED claim"}
                             if task else {"status": "NOT_REQUESTED"})
    run_id = run_id or ("UNIFIED_" + str(int(__import__("time").time())))
    result["run_id"] = run_id
    work = Path(work_dir) if work_dir else root / "reports" / "unified" / run_id
    work.mkdir(parents=True, exist_ok=True)

    if executor is None:
        from .unified_executors import WorkerExecutor

        executor = WorkerExecutor(
            gfx=target_gfx, winml_python=winml_python, work_dir=work,
            timeout_s=timeout_s, repo_root=root).execute

    def attempt(ep: str, reference: dict | None = None) -> Attempt:
        result["attempted_eps"].append(ep)
        try:
            a = executor(ep=ep, model_path=str(model_path), inputs=inputs,
                         reference=reference)
        except Exception as e:  # noqa: BLE001 - executor failure is an attempt failure
            a = Attempt(ep=ep, error=f"EXECUTOR_EXCEPTION: {type(e).__name__}: {e}")
        result["attempts"].append({
            "ep": a.ep, "ok": a.ok, "status": a.status, "error": a.error,
            "target_nodes": a.target_nodes, "cpu_nodes": a.cpu_nodes,
            "binding": a.binding_confidence, "exit_code": a.exit_code,
            "log": a.log_path,
        })
        return a

    def adopt(a: Attempt, status: str) -> None:
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
        })

    if policy == "cpu_opt_in":
        c = attempt("cpu")
        if c.ok:
            adopt(c, CPU_AUTHORIZED)
            result["actual_ep"] = "cpu"
            result["cpu_user_authorized"] = True
        else:
            result["fallback_reason"] = "CPU_FAILED: " + str(c.error)
            result["runtime_status"] = FAIL
        return _finalize(result, work, root)

    amd = attempt("amdgpu")
    full, why = evaluate_attempt(amd, ep="amdgpu", require_full_gpu=require_full_gpu)
    if full:
        adopt(amd, GPU_FULL)
        return _finalize(result, work, root)
    amd_reason = f"AMDGPU_FAILED: {why}"

    if policy == "require_amdgpu":
        result["fallback_reason"] = amd_reason
        result["runtime_status"] = BLOCKED if amd.status == "BLOCKED" else FAIL
        return _finalize(result, work, root)

    dml = attempt("dml", reference=None)
    dfull, dwhy = evaluate_attempt(dml, ep="dml", require_full_gpu=require_full_gpu)
    if dfull:
        adopt(dml, GPU_FULL_DML)
        result["fallback_reason"] = amd_reason
        return _finalize(result, work, root)
    dml_reason = f"DML_FAILED: {dwhy}"

    if policy == "prefer_amdgpu_allow_dml":
        result["fallback_reason"] = amd_reason + "; " + dml_reason
        result["runtime_status"] = BLOCKED
        return _finalize(result, work, root)

    # gpu_first_allow_cpu
    if allow_cpu:
        c = attempt("cpu")
        if c.ok:
            adopt(c, CPU_AUTHORIZED)
            result["actual_ep"] = "cpu"
            result["cpu_user_authorized"] = True
            result["fallback_reason"] = amd_reason + "; " + dml_reason
            return _finalize(result, work, root)
        cpu_reason = "CPU_FAILED: " + str(c.error)
    else:
        cpu_reason = "CPU_NOT_AUTHORIZED: allow_cpu=False"
    result["fallback_reason"] = f"{amd_reason}; {dml_reason}; {cpu_reason}"
    result["runtime_status"] = BLOCKED
    return _finalize(result, work, root)


def _finalize(result: dict, work: Path, root: Path) -> dict:
    from . import contract as contract_mod

    try:
        result["code_provenance"] = contract_mod.code_state(root)
    except Exception as e:  # noqa: BLE001 - provenance failure must not hide the run
        result["code_provenance"] = {"error": f"{type(e).__name__}: {e}"}
    body = {k: v for k, v in result.items() if k not in ("outputs", "receipt_sha256")}
    body["captured_utc"] = _utc()
    receipt = {**body, "schema_version": "unified_run_v1"}
    text = json.dumps(receipt, sort_keys=True, default=_json_default)
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    result["receipt_sha256"] = sha
    try:
        (work / f"receipt_{result['run_id']}.json").write_text(
            text + "\n", encoding="utf-8")
    except OSError as e:
        result["receipt_write_error"] = str(e)
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


def verify_run_result(r: dict) -> dict:
    """Independent re-check of the RunResult invariants (verifier side)."""
    checks: list[dict] = []

    def chk(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})

    for f in REQUIRED_RESULT_FIELDS:
        chk(f"field:{f}", f in r)
    status = r.get("runtime_status")
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
    if status == CPU_AUTHORIZED:
        chk("cpu_consent_true", r.get("cpu_user_authorized") is True)
        chk("cpu_not_claimed_as_gpu", r.get("gpu_full") is False)
    chk("attempted_eps_sane", all(e in ("amdgpu", "dml", "cpu")
                                  for e in (r.get("attempted_eps") or [])))
    sha = str(r.get("receipt_sha256") or "")
    chk("receipt_sha_64hex", len(sha) == 64 and all(c in "0123456789abcdef" for c in sha))
    ms = str(r.get("model_sha256") or "")
    chk("model_sha_64hex", len(ms) == 64 and all(c in "0123456789abcdef" for c in ms))
    ok = all(c["ok"] for c in checks)
    return {"verdict": "RUNRESULT_VALID" if ok else "RUNRESULT_INVALID",
            "checks": checks}


