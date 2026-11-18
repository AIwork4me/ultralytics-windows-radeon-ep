"""RCA2A receipt schema v1 — fail-closed validation of attempt receipts.

A receipt is only VALID when every hard fact is internally consistent:
expected env actually echoed by the worker, model/input SHA match the frozen
plan, the phase timeline is complete and ordered, and any cache-effect or
GPU-pass claim carries its own evidence inside the receipt. Anything unknown
or missing fails closed — an unverifiable claim is NEVER upgraded by guesswork.

Verdict labels (derived here, never trusted from the worker):
  RECEIPT_VALID / RECEIPT_INVALID_<REASON>
Cache-effect labels:
  CACHE_FILE_STATE_CHANGED  (observable file/entry changes — necessary, not sufficient)
  CACHE_CROSS_PROCESS_HIT   (changed in process A, and loaded/unchanged-content +
                             reduced-tuning evidence in process B)
  CACHE_NO_EFFECT_OBSERVED
GPU labels are the supervisor's; this module only checks their evidence fields.
"""
from __future__ import annotations

import math
import re
from typing import Any

SCHEMA_VERSION = "rca2a_receipt_v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA256_SEARCH = re.compile(r"[0-9a-f]{64}")  # unanchored, for DIRTY-<sha> forms

PHASE_ORDER = [
    "EP_DISCOVERY", "EP_DLL_REGISTER", "GPU_IDENTITY_RESOLVE", "EP_DEVICE_PROBE",
    "SESSION_CREATE", "FIRST_RUN", "WARMUP", "STEADY_RUN", "SESSION_RECREATE",
    "SESSION_TEARDOWN",
]

REQUIRED_TOP = [
    "kind", "attempt_id", "arm", "model", "ep", "classification", "child_exit_code",
    "onnx_sha256", "input_sha256", "plan_sha256", "code_git_sha", "main_base_sha",
    "worktree_dirty", "env_expected", "env_echoed", "timeline", "cache_verdict",
    "gpu_validation", "finished_utc",
]


class ReceiptError(ValueError):
    pass


def _err(reason: str, detail: str = "") -> ReceiptError:
    return ReceiptError(f"{reason}: {detail}" if detail else reason)


def validate_receipt(r: dict[str, Any], *, plan: dict[str, Any] | None = None) -> dict:
    """Validate one receipt; returns {"verdict": ..., "checks": [...]}. Raises nothing."""
    checks: list[dict] = []

    def chk(name: str, ok: bool, detail: str = "") -> bool:
        checks.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})
        return bool(ok)

    def finish(verdict: str) -> dict:
        return {"verdict": verdict, "checks": checks}

    if not isinstance(r, dict):
        return finish("RECEIPT_INVALID_NOT_DICT")
    if r.get("kind") != "rca2a_attempt_receipt":
        return finish("RECEIPT_INVALID_KIND")
    missing = [k for k in REQUIRED_TOP if k not in r]
    if missing:
        return finish("RECEIPT_INVALID_MISSING_FIELDS")
    if r.get("schema_version") != SCHEMA_VERSION:
        return finish("RECEIPT_INVALID_SCHEMA_VERSION")

    for f in ("onnx_sha256", "input_sha256", "plan_sha256", "code_git_sha", "main_base_sha"):
        v = r.get(f)
        if not isinstance(v, str) or not SHA256_RE.match(v):
            # code_git_sha may be "DIRTY-<sha>" — still must carry a full sha
            m = SHA256_SEARCH.search(str(v or ""))
            if f != "code_git_sha" or not m:
                chk(f"sha:{f}", False, v)
                return finish(f"RECEIPT_INVALID_SHA_{f.upper()}")

    # env: every key the arm promises must appear in the worker's own echo
    exp = r.get("env_expected") or {}
    echo = r.get("env_echoed") or {}
    for k, v in exp.items():
        if echo.get(k) != v:
            chk("env_echo_matches_expected", False, f"{k} expected {v!r} echoed {echo.get(k)!r}")
            return finish("RECEIPT_INVALID_ENV_NOT_ECHOED")
    unknown_env = sorted(set(echo) - set(exp) -
                         {k for k in echo if k.startswith(("MIGRAPHX_", "ORT_MIGRAPHX_", "MIOPEN_"))})
    if unknown_env:
        chk("env_no_unplanned_keys", False, unknown_env)
        return finish("RECEIPT_INVALID_ENV_UNDECLARED_KEYS")

    # timeline completeness and ordering
    tl = r.get("timeline") or []
    names = [t.get("phase") for t in tl if isinstance(t, dict)]
    idx = {p: i for i, p in enumerate(PHASE_ORDER)}
    canonical = []
    for name in names:
        base = name[:-6] if isinstance(name, str) and name.endswith("_BEGIN") else name
        if base not in idx:
            return finish("RECEIPT_INVALID_TIMELINE_PHASE")
        canonical.append(base)
    if not canonical:
        return finish("RECEIPT_INVALID_TIMELINE_EMPTY")
    seq = [idx[name] for name in canonical]
    if seq != sorted(seq):
        return finish("RECEIPT_INVALID_TIMELINE_ORDER")
    if "SESSION_CREATE" not in canonical:
        return finish("RECEIPT_INVALID_NO_SESSION_CREATE")

    # classification sanity
    if r.get("child_exit_code") not in (0, 41, 42, 44, 45):
        return finish("RECEIPT_INVALID_EXIT_CODE")

    # cache verdict must not overclaim: any *hit/effect* label requires snapshots
    cv = r.get("cache_verdict") or {}
    label = cv.get("label", "")
    if label in ("CACHE_FILE_STATE_CHANGED", "CACHE_CROSS_PROCESS_HIT"):
        snaps = cv.get("snapshots") or []
        if len(snaps) < 2:
            return finish("RECEIPT_INVALID_CACHE_CLAIM_WITHOUT_SNAPSHOTS")
        if label == "CACHE_CROSS_PROCESS_HIT" and not cv.get("second_process_evidence"):
            return finish("RECEIPT_INVALID_CROSS_PROCESS_CLAIM_WITHOUT_EVIDENCE")
    if label == "CACHE_NO_EFFECT_OBSERVED" and cv.get("switch_recognized") is None:
        return finish("RECEIPT_INVALID_NO_EFFECT_WITHOUT_RECOGNITION_PROOF")

    # plan cross-check (same-sha cross-condition mixing is forbidden)
    if plan is not None:
        if r.get("plan_sha256") != plan.get("plan_sha256"):
            return finish("RECEIPT_INVALID_PLAN_SHA_MISMATCH")
        arm = plan.get("arms", {}).get(r.get("arm"))
        if arm is None:
            return finish("RECEIPT_INVALID_ARM_NOT_IN_PLAN")
        models = arm.get("models")
        if not isinstance(models, list) or r.get("model") not in models:
            return finish("RECEIPT_INVALID_ARM_MODEL_MISMATCH")
        case = (plan.get("cases") or {}).get(r["model"])
        if not isinstance(case, dict):
            return finish("RECEIPT_INVALID_PLAN_CASE_MISSING")
        for field in ("onnx_sha256", "input_sha256"):
            if not SHA256_RE.fullmatch(str(case.get(field, ""))):
                return finish("RECEIPT_INVALID_PLAN_CASE_SHA_MISSING")
            if r.get(field) != case[field]:
                return finish("RECEIPT_INVALID_" + field.upper() + "_MISMATCH")
        if arm.get("ep") != r.get("ep"):
            return finish("RECEIPT_INVALID_ARM_EP_MISMATCH")
        if (arm.get("env") or {}) != exp:
            return finish("RECEIPT_INVALID_ARM_ENV_MISMATCH")

    # Published supervisor receipts use checks[] plus numeric summary fields.
    gv = r.get("gpu_validation") or {}
    if gv.get("verdict") == "GPU_PASS_VALIDATED":
        if r.get("classification") != "CLEAN_COMPLETE" or r.get("child_exit_code") != 0:
            return finish("RECEIPT_INVALID_GPU_PASS_STATE")
        raw_checks = gv.get("checks")
        if not isinstance(raw_checks, list) or not raw_checks:
            return finish("RECEIPT_INVALID_GPU_PASS_WITHOUT_CHECKS")
        if any(not isinstance(c, dict) or not isinstance(c.get("check"), str)
               for c in raw_checks):
            return finish("RECEIPT_INVALID_GPU_CHECK_MALFORMED")
        names_seen = [c["check"] for c in raw_checks]
        if len(names_seen) != len(set(names_seen)):
            return finish("RECEIPT_INVALID_GPU_CHECK_DUPLICATE")
        evidence = {c["check"]: c.get("ok") is True for c in raw_checks}
        n = gv.get("profile_ep_node_count")
        if not evidence.get("profile_ep_nodes_positive") or not isinstance(n, int) \
                or isinstance(n, bool) or n <= 0:
            return finish("RECEIPT_INVALID_GPU_PASS_WITHOUT_PROFILE_NODES")
        delta = gv.get("supervisor_parity_max_abs")
        if not evidence.get("supervisor_independent_parity") \
                or not isinstance(delta, (int, float)) or isinstance(delta, bool) \
                or not math.isfinite(delta) or delta < 0:
            return finish("RECEIPT_INVALID_GPU_PASS_WITHOUT_PARITY")
        if r.get("ep") == "amdgpu":
            required = ("ep_device_probe_event", "luid_match_basis",
                        "amdgpu_provider_present", "output_names_match")
        elif r.get("ep") == "dml":
            required = ("gpu_identity_event", "physical_radeon_adapter",
                        "dml_provider_present", "output_names_match")
        else:
            return finish("RECEIPT_INVALID_GPU_EP_UNSUPPORTED")
        if any(not evidence.get(k) for k in required):
            return finish("RECEIPT_INVALID_GPU_PASS_WITHOUT_IDENTITY_OR_OUTPUTS")
        if not all(evidence.values()):
            return finish("RECEIPT_INVALID_GPU_CHECK_FAILED")

    ok = all(c["ok"] for c in checks)
    return finish("RECEIPT_VALID" if ok else "RECEIPT_INVALID_CHECKS_FAILED")


def summarize_verdicts(receipts: list[dict]) -> dict:
    out: dict[str, int] = {}
    for r in receipts:
        v = validate_receipt(r).get("verdict", "RECEIPT_INVALID_EXCEPTION")
        out[v] = out.get(v, 0) + 1
    return out
