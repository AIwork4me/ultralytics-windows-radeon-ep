"""unified_runtime — policy engine + RunResult assembly (CONTRACTS.md).

predict() never launches CPU implicitly; CPU requires an explicit user consent
flag (policy='cpu_opt_in' + allow_cpu=True). GPU claims require positive
evidence from the executor: target-EP profiler nodes > 0, CPU nodes == 0,
by-name elementwise parity, and a non-UNPROVEN device binding. Anything less
is refused or downgraded — never silently accepted.

Executors are injectable: tests drive the policy engine with fake attempts;
the real hardware path reuses the rca2a EP session helpers.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

POLICIES = ("require_amdgpu", "prefer_amdgpu_allow_dml", "cpu_opt_in")

GPU_FULL = "GPU_FULL"
GPU_FULL_DML = "GPU_FULL_DML"
PARTIAL_CPU = "PARTIAL_CPU"
CPU_AUTHORIZED = "CPU_ONLY_USER_AUTHORIZED"
FAIL = "FAIL"
BLOCKED = "BLOCKED"


class ContractError(ValueError):
    """Bad or contradictory user arguments (exit 2)."""


@dataclass
class AttemptResult:
    ep: str                                        # "amdgpu" | "dml" | "cpu"
    ok: bool = False
    status: str = FAIL                             # FULL | PARTIAL | FAIL | BLOCKED
    target_ep_profile_nodes: int = 0
    cpu_profile_nodes: int = 0
    numeric_oracle: dict = field(default_factory=dict)
    task_oracle: dict = field(default_factory=dict)
    device_identity: dict = field(default_factory=dict)
    device_binding_confidence: str = "UNPROVEN"
    actual_provider: str | None = None
    ep_version: str | None = None
    ep_dll_sha: str | None = None
    outputs: dict | None = None
    error: str | None = None


def _tobytes(v: Any) -> bytes:
    if hasattr(v, "tobytes"):
        return v.tobytes()
    if isinstance(v, bytes):
        return v
    if isinstance(v, (list, tuple)):
        return b"".join(_tobytes(x) for x in v)
    raise ContractError(f"inputs[{v!r}] is not a tensor-like")


def _sha_inputs(inputs: dict) -> str:
    blob = b"".join(_tobytes(inputs[k]) for k in sorted(inputs))
    return hashlib.sha256(blob).hexdigest()


def _sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_args(model_path, inputs, policy, allow_cpu, require_full_gpu,
                  target_gfx) -> None:
    if not isinstance(model_path, (str, Path)) or not Path(model_path).is_file():
        raise ContractError(f"model_path must be an existing file: {model_path!r}")
    if not isinstance(inputs, dict) or not inputs:
        raise ContractError("inputs must be a non-empty dict of tensors")
    if policy not in POLICIES:
        raise ContractError(f"policy must be one of {POLICIES}, got {policy!r}")
    if allow_cpu and policy != "cpu_opt_in":
        raise ContractError("allow_cpu=True is only meaningful with policy="
                            "'cpu_opt_in'; consent is not additive sugar")
    if policy == "cpu_opt_in" and not allow_cpu:
        raise ContractError("policy='cpu_opt_in' requires the explicit user "
                            "flag allow_cpu=True")
    if not isinstance(require_full_gpu, bool):
        raise ContractError("require_full_gpu must be a bool")
    if target_gfx is not None:
        from .device_identity import GFX_DEVICE_IDS
        if target_gfx not in GFX_DEVICE_IDS:
            raise ContractError(
                f"UNSUPPORTED_GFX_TARGET: {target_gfx}; supported: "
                f"{sorted(GFX_DEVICE_IDS)}")


def _attempt_is_gpu_full(a: AttemptResult, ep: str) -> bool:
    conf = a.device_binding_confidence or "UNPROVEN"
    return (a.ep == ep and a.ok and a.status == "FULL"
            and a.target_ep_profile_nodes > 0 and a.cpu_profile_nodes == 0
            and bool(a.numeric_oracle.get("allclose"))
            and "UNPROVEN" not in conf)


def predict(model_path, inputs, *, policy="require_amdgpu",
            target_gpu=None, target_gfx=None, allow_cpu=False,
            require_full_gpu=True, task=None, timeout_s=1200,
            executor: Callable[[str, dict], AttemptResult] | None = None
            ) -> dict[str, Any]:
    """Unified GPU-first entrypoint. See docs/unified/CONTRACTS.md."""
    validate_args(model_path, inputs, policy, allow_cpu, require_full_gpu,
                  target_gfx)
    if executor is None:
        from .unified_executors import InProcessExecutor
        executor = InProcessExecutor().execute

    onnx_sha = _sha_file(Path(model_path))
    input_sha = _sha_inputs(inputs)
    result: dict[str, Any] = {
        "requested_policy": policy,
        "attempted_eps": [],
        "actual_ep": None,
        "actual_provider": None,
        "status": FAIL,
        "cpu_user_authorized": bool(allow_cpu),
        "device_identity": {},
        "device_binding_confidence": "UNPROVEN",
        "ep_version": None,
        "ep_dll_sha": None,
        "onnx_sha256": onnx_sha,
        "input_sha256": input_sha,
        "target_ep_profile_nodes": 0,
        "cpu_profile_nodes": 0,
        "numeric_oracle": {},
        "task_oracle": {"status": "NOT_RUN", "reason": "no task requested"}
        if task is None else {"status": "NOT_RUN"},
        "fallback_reason": None,
        "cache_status": "CACHE_NOT_VALIDATED",
        "provenance_head": None,
        "source_receipt_sha": None,
        "outputs": None,
    }
    ctx = {"model_path": str(model_path), "inputs": inputs, "target_gfx": target_gfx,
           "target_gpu": target_gpu, "task": task, "timeout_s": timeout_s}

    def run_ep(ep: str) -> AttemptResult:
        result["attempted_eps"].append(ep)
        try:
            return executor(ep, ctx)
        except Exception as e:  # noqa: BLE001
            # executor contract violation / watchdog kill: recorded, fail-closed
            return AttemptResult(ep=ep, error=f"{type(e).__name__}: {e}")

    if policy == "cpu_opt_in":
        a = run_ep("cpu")
        if a.ok:
            result.update({"actual_ep": "cpu", "status": CPU_AUTHORIZED,
                           "outputs": a.outputs,
                           "cpu_user_authorized": True})
        else:
            result["fallback_reason"] = f"cpu_attempt_failed: {a.error}"
        return result

    a = run_ep("amdgpu")
    if _attempt_is_gpu_full(a, "amdgpu"):
        result.update({"actual_ep": "amdgpu", "status": GPU_FULL,
                       "actual_provider": a.actual_provider,
                       "device_identity": a.device_identity,
                       "device_binding_confidence": a.device_binding_confidence,
                       "ep_version": a.ep_version, "ep_dll_sha": a.ep_dll_sha,
                       "target_ep_profile_nodes": a.target_ep_profile_nodes,
                       "cpu_profile_nodes": a.cpu_profile_nodes,
                       "numeric_oracle": a.numeric_oracle,
                       "task_oracle": a.task_oracle or result["task_oracle"],
                       "outputs": a.outputs})
        return result

    amdgpu_failure = f"AMDGPU_FAILED: {a.error or a.status}"

    if policy == "prefer_amdgpu_allow_dml":
        d = run_ep("dml")
        if _attempt_is_gpu_full(d, "dml"):
            result.update({"actual_ep": "dml", "status": GPU_FULL_DML,
                           "actual_provider": d.actual_provider,
                           "device_identity": d.device_identity,
                           "device_binding_confidence": d.device_binding_confidence,
                           "ep_version": d.ep_version, "ep_dll_sha": d.ep_dll_sha,
                           "target_ep_profile_nodes": d.target_ep_profile_nodes,
                           "cpu_profile_nodes": d.cpu_profile_nodes,
                           "numeric_oracle": d.numeric_oracle,
                           "task_oracle": d.task_oracle or result["task_oracle"],
                           "outputs": d.outputs,
                           "fallback_reason": amdgpu_failure})
            return result
        if d.status == "PARTIAL" and not require_full_gpu:
            # explicit opt-down: reported honestly, never as GPU_FULL
            result.update({"actual_ep": "dml", "status": PARTIAL_CPU,
                           "target_ep_profile_nodes": d.target_ep_profile_nodes,
                           "cpu_profile_nodes": d.cpu_profile_nodes,
                           "outputs": d.outputs,
                           "fallback_reason": amdgpu_failure
                           + f"; dml_partial: {d.error or ''}"})
            return result
        result["fallback_reason"] = amdgpu_failure + f"; DML_FAILED: {d.error or d.status}"
        return result

    if a.status == "PARTIAL":
        # honest label either way; under require_full_gpu it is a REFUSAL
        result.update({"actual_ep": "amdgpu", "status": PARTIAL_CPU,
                       "target_ep_profile_nodes": a.target_ep_profile_nodes,
                       "cpu_profile_nodes": a.cpu_profile_nodes,
                       "outputs": a.outputs if not require_full_gpu else None,
                       "fallback_reason": (
                           "partial_cpu_reported_honestly" if not require_full_gpu
                           else "refused: require_full_gpu=True but CPU nodes>0")})
        return result
    if a.status == "BLOCKED":
        result["status"] = BLOCKED
        result["fallback_reason"] = a.error or "blocked"
        return result
    result["fallback_reason"] = amdgpu_failure
    return result


def verify_run_result(r: dict) -> dict:
    """Independent re-check of RunResult invariants (verifier-side)."""
    checks: list[dict] = []

    def chk(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})

    if r.get("status") in (GPU_FULL, GPU_FULL_DML):
        chk("target_nodes_positive", r.get("target_ep_profile_nodes", 0) > 0)
        chk("cpu_nodes_zero", r.get("cpu_profile_nodes", -1) == 0)
        chk("parity_allclose", r.get("numeric_oracle", {}).get("allclose") is True)
        chk("binding_not_unproven", r.get("device_binding_confidence") not in
            (None, "UNPROVEN"))
    if r.get("status") == CPU_AUTHORIZED:
        chk("cpu_consent_true", r.get("cpu_user_authorized") is True)
    if r.get("status") == PARTIAL_CPU:
        chk("partial_discloses_cpu_nodes", r.get("cpu_profile_nodes", 0) > 0)
    chk("attempted_eps_ordered",
        r.get("attempted_eps") in ([], ["amdgpu"], ["cpu"], ["amdgpu", "dml"]))
    if r.get("actual_ep") == "dml":
        chk("dml_discloses_amdgpu_failure",
            "AMDGPU_FAILED" in str(r.get("fallback_reason") or ""))
    sha = r.get("onnx_sha256", "")
    chk("onnx_sha_is_64hex", len(sha) == 64 and all(c in "0123456789abcdef" for c in sha))
    ok = all(c["ok"] for c in checks)
    return {"verdict": "RUNRESULT_VALID" if ok else "RUNRESULT_INVALID", "checks": checks}
