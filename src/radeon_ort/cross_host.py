"""Cross-host (gfx1100 / gfx1201) physical-device gate and cross-arch guards.

The harness that produced gfx1151 evidence cannot attest another architecture.
A receipt, RUN_CONTRACT, model artefact, cache entry or soak output is only
meaningful for the gfx/DID that produced it, so reuse across gfx must fail
closed rather than be silently relabelled.

This module centralises the rules used by ``runner.cross_host_validate`` and the
synthetic boundary tests:

* the requested gfx must equal the live DXGI-derived gfx
  (``WRONG_PHYSICAL_DEVICE`` / ``AMBIGUOUS_GFX_TARGET`` / ``UNSUPPORTED_GFX_TARGET``);
* a receipt or run contract whose device identity names another gfx is refused
  (``CROSS_ARCH_RECEIPT_REFUSED`` / ``CROSS_ARCH_CONTRACT_REFUSED``);
* a receipt that forges its host label or its selected DID for that label is
  refused (``RECEIPT_HOST_LABEL_FORGED`` / ``RECEIPT_DEVICE_ID_MISMATCH``);
* a run id that carries another architecture's tag is refused.

Nothing here creates an ONNX session, enumerates a GPU session or executes GPU
work: every function is a pure decision over supplied adapter/receipt data.
"""

from __future__ import annotations

from .device_identity import (
    AMD_VENDOR_ID,
    GFX_DEVICE_IDS,
    DxgiAdapter,
    infer_gfx_from_adapters,
    resolve_target_adapter,
)

# --- refusal reason codes ------------------------------------------------------

WRONG_PHYSICAL_DEVICE = "WRONG_PHYSICAL_DEVICE"
AMBIGUOUS_GFX_TARGET = "AMBIGUOUS_GFX_TARGET"
UNSUPPORTED_GFX_TARGET = "UNSUPPORTED_GFX_TARGET"
CROSS_ARCH_RECEIPT_REFUSED = "CROSS_ARCH_RECEIPT_REFUSED"
CROSS_ARCH_CONTRACT_REFUSED = "CROSS_ARCH_CONTRACT_REFUSED"
RECEIPT_HOST_LABEL_FORGED = "RECEIPT_HOST_LABEL_FORGED"
RECEIPT_DEVICE_ID_MISMATCH = "RECEIPT_DEVICE_ID_MISMATCH"
CONTRACT_DEVICE_ID_MISMATCH = "CONTRACT_DEVICE_ID_MISMATCH"
RUN_ID_CROSS_ARCH_TAG = "RUN_ID_CROSS_ARCH_TAG"
RUN_ID_INVALID = "RUN_ID_INVALID"

SUPPORTED_GFX = tuple(sorted(GFX_DEVICE_IDS))

# Exact final-verdict vocabulary. No other suffix may be emitted.
VERDICT_KINDS = (
    "CROSS_HOST_HARNESS_READY",
    "PHYSICAL_PENDING",
    "PHYSICAL_GO",
    "CONDITIONAL_GO",
    "BLOCKED_EP",
    "NO_GO",
)

# Stage status vocabulary for the one-command entrypoint.
STAGE_STATUSES = frozenset({"PASS", "FAIL", "BLOCKED", "NOT_RUN", "SKIPPED"})

HOST_STATUS_BY_GFX = {f"VERIFIED_{g.upper()}": g for g in SUPPORTED_GFX}


def gfx_verdict(gfx: str, kind: str) -> str:
    """Return the exact ``<GFX>_<KIND>`` verdict string.

    Fail-closed: an unknown gfx or an unknown verdict kind raises instead of
    emitting an ad-hoc label that a consumer might mistake for a claim.
    """
    if gfx not in GFX_DEVICE_IDS:
        raise ValueError(f"unsupported gfx {gfx!r}; supported: {SUPPORTED_GFX}")
    if kind not in VERDICT_KINDS:
        raise ValueError(f"unknown verdict kind {kind!r}; allowed: {VERDICT_KINDS}")
    return f"{gfx.upper()}_{kind}"


def make_stage_record(stage: str, status: str, *, reason: str | None = None,
                      detail: str | None = None,
                      commands: list[dict] | None = None,
                      extra: dict | None = None) -> dict:
    """One structured stage record for the cross-host JSON report."""
    if status not in STAGE_STATUSES:
        raise ValueError(f"unknown stage status {status!r}; allowed: {sorted(STAGE_STATUSES)}")
    record = {
        "stage": stage,
        "status": status,
        "reason": reason,
        "detail": detail,
        "commands": commands or [],
    }
    if extra:
        record.update(extra)
    return record


def _parse_device_id(value) -> int | None:
    """Parse ``0x1586`` / ``1586`` into an int, or return None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text, 16)
    except ValueError:
        return None


def _observed_amd(adapters: list[DxgiAdapter]) -> list[DxgiAdapter]:
    return [a for a in adapters if a.vendor_id == AMD_VENDOR_ID and not a.is_software]


def _adapter_summary(adapters: list[DxgiAdapter]) -> list[dict]:
    return [
        {
            "index": a.index,
            "description": a.description,
            "vendor_id": f"0x{a.vendor_id:04X}",
            "device_id": f"0x{a.device_id:04X}",
            "is_software": bool(a.is_software),
            "luid_corroboration_only": a.luid_str,
        }
        for a in adapters
    ]


def gate_physical_target(adapters: list[DxgiAdapter], requested_gfx: str) -> dict:
    """HARD GATE: the requested architecture must equal the live AMD adapter.

    The comparison is DID-pinned through ``infer_gfx_from_adapters``: a host
    with one catalogued AMD gfx yields exactly one target, a host with more than
    one supported gfx is ambiguous (never pick one), and a requested gfx that
    does not match the live one is ``WRONG_PHYSICAL_DEVICE``. Only a PASS here
    may be followed by any GPU-executing stage.
    """
    result: dict = {
        "requested_gfx": requested_gfx,
        "status": "BLOCKED",
        "reason": UNSUPPORTED_GFX_TARGET,
        "detail": "",
        "live_gfx": None,
        "expected_device_ids": (
            [f"0x{d:04X}" for d in sorted(GFX_DEVICE_IDS[requested_gfx])]
            if requested_gfx in GFX_DEVICE_IDS else []
        ),
        "observed_device_ids": [],
        "observed_adapters": _adapter_summary(adapters),
        "selected_device_id": None,
        "selected_luid": None,
        "did_match": None,
    }
    if requested_gfx not in GFX_DEVICE_IDS:
        result["detail"] = (f"requested gfx {requested_gfx!r} is not catalogued; "
                            f"supported: {list(SUPPORTED_GFX)}")
        return result

    amd = _observed_amd(adapters)
    result["observed_device_ids"] = sorted({f"0x{a.device_id:04X}" for a in amd})
    if not amd:
        result["detail"] = "no non-software AMD adapter was enumerated"
        return result

    inferred = infer_gfx_from_adapters(adapters)
    if not inferred.get("ok"):
        reason_text = str(inferred.get("reason") or "")
        result["reason"] = (AMBIGUOUS_GFX_TARGET if AMBIGUOUS_GFX_TARGET in reason_text
                            else UNSUPPORTED_GFX_TARGET)
        result["detail"] = reason_text
        return result

    result["live_gfx"] = inferred["gfx"]
    if inferred["gfx"] != requested_gfx:
        result["reason"] = WRONG_PHYSICAL_DEVICE
        result["detail"] = (
            f"requested {requested_gfx} (DID {result['expected_device_ids']}) but live "
            f"DXGI-derived gfx is {inferred['gfx']} "
            f"(observed DID {result['observed_device_ids']}); refusing before any GPU work"
        )
        return result

    resolved = resolve_target_adapter(adapters, gfx=requested_gfx)
    if not resolved.get("ok"):
        why = str(resolved.get("reason") or "")
        result["reason"] = (AMBIGUOUS_GFX_TARGET if "distinct AMD candidates" in why
                            else WRONG_PHYSICAL_DEVICE)
        result["detail"] = why
        return result

    adapter = resolved["adapter"]
    result.update({
        "status": "PASS",
        "reason": None,
        "detail": (f"{requested_gfx} verified by DID 0x{adapter.device_id:04X}; "
                   "LUID is session corroboration only"),
        "selected_device_id": f"0x{adapter.device_id:04X}",
        "selected_luid": adapter.luid_str,
        "did_match": bool(resolved.get("did_match")),
    })
    return result


def _selected_receipt_device_ids(receipt: dict) -> set[int]:
    """Selected DID(s) recorded by the receipt (never every enumerated adapter).

    A multi-GPU host may enumerate another supported adapter; only the adapter
    the receipt selected is evidence for its host label.
    """
    host = receipt.get("host") or {}
    dxgi = host.get("dxgi_evidence") or {}
    candidates = [host.get("device_id"), dxgi.get("device_id")]
    if isinstance(dxgi.get("selected_device_id"), (str, int)):
        candidates.append(dxgi["selected_device_id"])
    out = {_parse_device_id(v) for v in candidates}
    return {d for d in out if d is not None}


def receipt_architecture_problems(receipt: dict, run_contract: dict | None = None) -> list[str]:
    """Refusals for a receipt that belongs to another architecture or forges its id.

    This complements ``radeon_ort.contract.validate_receipt_contract`` (which
    compares the whole device-identity block): the checks here name the specific
    cross-arch or DID-forgery failure so a reviewer can see why evidence was
    not accepted.
    """
    problems: list[str] = []
    host = receipt.get("host") or {}
    gfx = host.get("gfx")
    status_host = str((receipt.get("status") or {}).get("host") or "")
    claimed_gfx = HOST_STATUS_BY_GFX.get(status_host)
    if claimed_gfx is not None and claimed_gfx != gfx:
        problems.append(
            f"{RECEIPT_HOST_LABEL_FORGED}: status.host={status_host!r} but "
            f"host.gfx={gfx!r}"
        )
    if gfx not in GFX_DEVICE_IDS:
        problems.append(f"{UNSUPPORTED_GFX_TARGET}: receipt host.gfx={gfx!r} is not catalogued")
        return problems

    allowed = GFX_DEVICE_IDS[gfx]
    observed = _selected_receipt_device_ids(receipt)
    forged = sorted(d for d in observed if d not in allowed)
    if forged:
        problems.append(
            f"{RECEIPT_DEVICE_ID_MISMATCH}: host.gfx={gfx} expects DID "
            f"{[f'0x{d:04X}' for d in sorted(allowed)]} but receipt selected "
            f"{[f'0x{d:04X}' for d in forged]}"
        )

    if run_contract is not None:
        contract_gfx = run_contract.get("gfx")
        if contract_gfx != gfx:
            problems.append(
                f"{CROSS_ARCH_RECEIPT_REFUSED}: receipt gfx={gfx!r} but run contract "
                f"gfx={contract_gfx!r}"
            )
        contract_identity = run_contract.get("device_identity") or {}
        receipt_contract = receipt.get("contract") or {}
        receipt_identity = receipt_contract.get("device_identity") or {}
        if receipt_identity:
            rc_gfx = receipt_identity.get("gfx")
            if rc_gfx is not None and rc_gfx != gfx:
                problems.append(
                    f"{CROSS_ARCH_RECEIPT_REFUSED}: receipt contract device_identity.gfx="
                    f"{rc_gfx!r} but host.gfx={gfx!r}"
                )
            if (contract_identity and rc_gfx is not None
                    and rc_gfx != contract_identity.get("gfx")):
                problems.append(
                    f"{CROSS_ARCH_RECEIPT_REFUSED}: receipt contract gfx={rc_gfx!r} but "
                    f"run contract gfx={contract_identity.get('gfx')!r}"
                )
    return problems


def run_contract_architecture_problems(contract: dict,
                                       live_gate: dict | None = None) -> list[str]:
    """Refusals for a RUN_CONTRACT produced for another gfx/host.

    A contract records the device identity that produced its receipts. Reusing
    it on another architecture must never be accepted, even when every hash in
    the contract is internally consistent.
    """
    problems: list[str] = []
    gfx = contract.get("gfx")
    identity = contract.get("device_identity") or {}
    if gfx not in GFX_DEVICE_IDS:
        problems.append(f"{UNSUPPORTED_GFX_TARGET}: run contract gfx={gfx!r} is not catalogued")
        return problems
    if identity.get("gfx") != gfx:
        problems.append(
            f"{CROSS_ARCH_CONTRACT_REFUSED}: run contract gfx={gfx!r} but "
            f"device_identity.gfx={identity.get('gfx')!r}"
        )
    did = _parse_device_id(identity.get("device_id"))
    if did is None:
        problems.append(
            f"{CONTRACT_DEVICE_ID_MISMATCH}: run contract has no parseable "
            f"device_identity.device_id={identity.get('device_id')!r}"
        )
    elif did not in GFX_DEVICE_IDS[gfx]:
        problems.append(
            f"{CONTRACT_DEVICE_ID_MISMATCH}: run contract gfx={gfx} expects DID "
            f"{[f'0x{d:04X}' for d in sorted(GFX_DEVICE_IDS[gfx])]} but records "
            f"0x{did:04X}"
        )
    if live_gate is not None:
        if live_gate.get("status") != "PASS":
            problems.append(
                f"{CROSS_ARCH_CONTRACT_REFUSED}: live physical gate did not pass "
                f"({live_gate.get('reason')}: {live_gate.get('detail')})"
            )
        elif (live_gate.get("requested_gfx") != gfx
              or live_gate.get("live_gfx") != gfx):
            problems.append(
                f"{CROSS_ARCH_CONTRACT_REFUSED}: run contract gfx={gfx!r} but live gate "
                f"requested={live_gate.get('requested_gfx')!r} "
                f"live={live_gate.get('live_gfx')!r}"
            )
    return problems


def run_id_architecture_problem(run_id: str, gfx: str) -> str | None:
    """Refuse a run id that embeds another architecture's tag or a path.

    A new run id is mandatory for a new architecture; a recycled ``GFX1151_*``
    name under ``--gfx gfx1100`` is a cross-arch cache/receipt pollution signal.
    """
    if gfx not in GFX_DEVICE_IDS:
        return f"{UNSUPPORTED_GFX_TARGET}: {gfx!r}"
    text = str(run_id or "")
    if (not text or len(text) > 120
            or any(ch in text for ch in ("/", "\\", ":", "*", "?", '"', "<", ">", "|"))):
        return f"{RUN_ID_INVALID}: {run_id!r} must be a 1-120 char file-name component"
    # "." / ".." and Windows reserved device names are valid file-name components
    # syntactically but resolve somewhere else or are unusable; refuse them.
    import re as _re
    if text in (".", "..") or text != text.strip() or text.endswith("."):
        return f"{RUN_ID_INVALID}: {run_id!r} must not be a dot path or end with '.'"
    if not _re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$", text):
        return (f"{RUN_ID_INVALID}: {run_id!r} must start alphanumeric and use only "
                "[A-Za-z0-9._-]")
    if text.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL"}:
        return f"{RUN_ID_INVALID}: {run_id!r} is a reserved Windows device name"
    lowered = text.lower()
    for other in SUPPORTED_GFX:
        if other != gfx and other in lowered:
            return (f"{RUN_ID_CROSS_ARCH_TAG}: run id {run_id!r} contains {other}; "
                    f"create a new {gfx.upper()}_* run id")
    return None


def ep_readiness_refusal(policy: str, *, amdgpu_ready: bool, dml_ready: bool,
                         amdgpu_state: str | None = None) -> str | None:
    """Return a refusal reason when the policy cannot be satisfied without CPU.

    CPU execution is never an implicit fallback in this harness, so a policy
    that needs a GPU EP but has none is ``BLOCKED_EP``, not a quiet CPU run.
    """
    if policy == "dml-only":
        if dml_ready:
            return None
        return ("BLOCKED_EP: DmlExecutionProvider is unavailable; no CPU fallback "
                "is authorized")
    if policy == "amdgpu-only":
        if amdgpu_ready:
            return None
        return (f"BLOCKED_EP: AMDGPU EP is not ready ({amdgpu_state or 'unknown'}); "
                "no DML/CPU fallback under amdgpu-only")
    if policy == "gpu-first":
        if amdgpu_ready or dml_ready:
            return None
        return ("BLOCKED_EP: neither AMDGPU nor DML is ready; no CPU fallback is "
                "authorized")
    raise ValueError(f"unknown policy {policy!r}")


def build_cross_host_matrix(receipts: list[dict]) -> dict:
    """Per-checkpoint x per-gfx execution matrix derived from receipt identity.

    ``runner.build_compatibility_matrix`` predates the cross-host missions and
    renders fixed gfx1151/gfx1100/gfx1201 columns with only gfx1151 populated.
    This cross-host-specific view keys every cell off the receipt's own
    ``host.gfx``, so a gfx1100 receipt appears under gfx1100 rather than
    disappearing into a NOT_RUN column. It is a summary of receipts, never a
    substitute for strict receipt validation.
    """
    hosts: set[str] = set()
    by_checkpoint: dict[str, dict] = {}
    for receipt in receipts:
        host = receipt.get("host") or {}
        gfx = host.get("gfx")
        model = receipt.get("model") or {}
        checkpoint = model.get("checkpoint")
        status = receipt.get("status") or {}
        active_ep = "amdgpu" if status.get("amdgpu") not in (None, "NOT_RUN") else "dml"
        value = status.get(active_ep)
        if gfx not in GFX_DEVICE_IDS or not checkpoint or value in (None, "NOT_RUN"):
            continue
        hosts.add(gfx)
        row = by_checkpoint.setdefault(checkpoint, {
            "checkpoint": checkpoint,
            "family": model.get("family"),
            "task": model.get("task"),
            "results": {},
        })
        row["results"].setdefault(gfx, {})[active_ep] = value
    ordered_hosts = [g for g in SUPPORTED_GFX if g in hosts]
    return {
        "schema_version": 1,
        "hosts": ordered_hosts,
        "rows": [by_checkpoint[k] for k in sorted(by_checkpoint)],
        "note": ("cross-host view keyed by receipt host.gfx; strict validation is "
                 "still performed by runner.verify_receipts"),
    }


def canary_stage_status(canary: dict) -> tuple[str, str | None]:
    """Classify a ``runner.unified_canary`` result without trusting its label.

    A ``GPU_PASS_VALIDATED`` label is only accepted when the profile shows a
    positive target-EP node count, zero CPU compute nodes and numeric parity.
    """
    status = str(canary.get("status") or "")
    profile = canary.get("profile") or {}
    nodes = int(profile.get("target_ep_nodes") or 0)
    cpu_nodes = int(profile.get("cpu_nodes") or 0)
    parity_ok = bool((canary.get("parity") or {}).get("allclose"))
    reason = str(canary.get("reason") or "")

    if status == "GPU_PASS_VALIDATED":
        if nodes > 0 and cpu_nodes == 0 and parity_ok:
            return "PASS", None
        return ("FAIL", ("CANARY_PASS_LABEL_WITHOUT_GPU_EVIDENCE: "
                         f"nodes={nodes} cpu_nodes={cpu_nodes} parity={parity_ok}"))
    if status == "GPU_PASS_INVALIDATED":
        return "FAIL", f"CANARY_INVALIDATED: {reason or 'parity/node check failed'}"
    if status == "BLOCKED":
        return "BLOCKED", f"BLOCKED_IDENTITY: {reason or 'device identity not proven'}"
    if status == "FAIL_RUNTIME":
        lowered = reason.lower()
        if "silent cpu" in lowered or "fallback" in lowered:
            return "FAIL", f"FAIL_RUNTIME: {reason}"
        blocked_markers = ("not found", "not registered", "absent", "unavailable",
                           "not installed", "no ep", "dll")
        if any(marker in lowered for marker in blocked_markers):
            return "BLOCKED", f"BLOCKED_EP: {reason}"
        return "FAIL", f"FAIL_RUNTIME: {reason}"
    return "FAIL", f"UNKNOWN_CANARY_STATUS: {status!r}"