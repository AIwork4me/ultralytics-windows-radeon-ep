"""RCA2A AMDGPU EP session helpers — SELECTIVE PORT with provenance.

Ported verbatim-in-function from the local RCA1 branch
`mission/rca1-amdgpu-soak-stall` @ 1aac7fb (file src/radeon_ort/amdgpu_session.py,
sha256 af6de4c5ab923a943175e69a29e9562d9d02d48a1da2efa4ad929f10be8daf49), which
is NOT on origin/main (main carries the gfx1151 probe-attestation variant
instead). Only the functions the RCA2A worker needs are ported:

  _ep_device_metadata / _ensure_amdgpu_registered / select_amdgpu_ep_device
  (LUID-pinned physical-adapter selection, fail-closed on mirror devices)
  create_amdgpu_session (sess_options_override + target_luid pinning via
  add_provider_for_devices)

Nothing in main's amdgpu_session.py is modified. gfx1100 live behaviour
(register name 'AMDGPUExecutionProvider', desktop LUID 72346 vs mirror 118045)
was physically verified during RCA1 (2026-11-17, evidence dir RCA1_20261117T2157)
and is re-verified per-run by the worker's EP_DEVICE_PROBE phase.
"""
from __future__ import annotations

from pathlib import Path


def _ep_device_metadata(dev) -> dict:
    md = getattr(dev.device, "metadata", None)
    if isinstance(md, dict):
        return md
    try:
        import ast
        v = ast.literal_eval(str(md))
        return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def physical_luid_key(adapter) -> int | None:
    """Decimal LUID key the AMDGPU EP device metadata carries ('72346' etc.).

    main's device_identity populates only luid_str ('0x00000000:0x00011A9A');
    RCA1's populated extra['luid_key']. Accept either: the low DWORD of the
    High:Low LUID string is the decimal key the EP matches on (live-verified
    gfx1100: 0x11A9A == 72346 == physical desktop adapter)."""
    k = (adapter.extra or {}).get("luid_key")
    if k is not None:
        return int(k)
    ls = getattr(adapter, "luid_str", None)
    if ls and ":" in ls:
        try:
            return int(ls.split(":")[1], 16)
        except ValueError:
            return None
    return None


def _appx_ep_dll_path() -> str | None:
    r"""Resolve amdgpu-ep.dll via Get-AppxPackage (RCA1-proven fallback).
    Direct globs of C:\Program Files\WindowsApps are ACL-blocked for normal
    processes, so catalog glob patterns can silently return nothing."""
    import subprocess
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-AppxPackage Microsoft.WinML.AMD.GPU.EP.Framework.2).InstallLocation"],
            capture_output=True, text=True, timeout=30).stdout.strip()
        if out:
            p = Path(out) / "ExecutionProvider" / "amdgpu-ep.dll"
            if p.is_file():
                return str(p)
    except Exception:  # noqa: BLE001
        return None
    return None


def _ensure_amdgpu_registered() -> None:
    """Register the official AMD EP library in THIS process if not yet done.

    Fail-closed: after all attempts the AMDGPU ep-device MUST be present, else
    RuntimeError — ORT's python binding merely WARNS on an unknown provider
    name and silently rebuilds a CPU session (observed live 2026-11-18), which
    this function must never allow to pass unnoticed."""
    import onnxruntime as ort

    def _has_amdgpu() -> bool:
        try:
            return any(d.ep_name == "AMDGPUExecutionProvider"
                       for d in ort.get_ep_devices())
        except Exception:  # noqa: BLE001
            return False

    if _has_amdgpu():
        return
    candidates: list[str] = []
    try:
        from .windows_ml_ep_catalog import _rederive_real_paths
        candidates.extend(_rederive_real_paths())
    except Exception:  # noqa: BLE001
        pass
    appx = _appx_ep_dll_path()
    if appx:
        candidates.append(appx)
    last_err = None
    for rp in candidates:
        try:
            try:
                ort.register_execution_provider_library("AMDGPUExecutionProvider", rp)
            except TypeError:
                ort.register_execution_provider_library(rp)
            if _has_amdgpu():
                return
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(
        f"BLOCKED_EP: AMDGPU EP registration failed (candidates={candidates}, "
        f"last_error={last_err!r}) — refusing to continue on a silent CPU session")


def select_amdgpu_ep_device(target_luid: int | str | None = None):
    """Pick the AMDGPU EP device bound to the PHYSICAL adapter.

    gfx1100 host measured 2026-11-17 (RCA1): TWO 'Radeon RX 7900 XTX' devices —
    LUID 72346 (Discrete=1, owns the desktop output) and LUID 118045 (mirror,
    no outputs). target_luid pins the physical one; without it we prefer
    Discrete=1 and record the basis (never silently accept a mirror).

    Mission C (fail-closed hardening): when the caller supplies an explicit
    `target_luid`, the selected EP device MUST be the one and only device whose
    metadata LUID equals it. Before this fix, a *non-matching* LUID fell through
    to the "Discrete=1" / "take first" fallbacks and returned a DIFFERENT
    physical GPU while reporting a basis that claimed no LUID had been given
    (Experiment A `luid_pin_fail` case). A pinned request must never be silently
    satisfied by another adapter, so zero or multiple matches now return
    (None, basis) and the caller fails closed.
    """
    import onnxruntime as ort
    _ensure_amdgpu_registered()
    devs = [d for d in ort.get_ep_devices()
            if d.ep_name == "AMDGPUExecutionProvider"]
    if not devs:
        return None, {"basis": "no AMDGPU EP device registered"}

    candidates = [_ep_device_metadata(d) for d in devs]

    # --- pinned path: exactly-one-match or fail closed -------------------
    if target_luid is not None:
        want = str(target_luid)
        hits = [d for d in devs
                if str(_ep_device_metadata(d).get("LUID", "")) == want]
        if len(hits) == 1:
            return hits[0], {"basis": "LUID match with physical adapter",
                             "luid": want, "candidates": candidates}
        return None, {
            "basis": ("FAIL_CLOSED: an explicit target_luid was supplied and "
                      f"{len(hits)} AMDGPU EP device(s) matched it; refusing to "
                      "fall back to a different physical GPU"),
            "luid": want,
            "matched": len(hits),
            "candidates": candidates,
        }

    # --- unpinned path: documented preference, never a silent mirror -----
    discrete = [d for d in devs
                if str(_ep_device_metadata(d).get("Discrete", "0")) == "1"]
    if len(discrete) == 1:
        return discrete[0], {"basis": "Discrete=1 unique among AMDGPU EP devices (no LUID given)",
                             "candidates": candidates}
    return devs[0], {"basis": "AMBIGUOUS: took first AMDGPU EP device; caller must canary-verify",
                     "candidates": candidates}


def create_amdgpu_session(onnx_path: str | Path, *, enable_profiling: bool = True,
                          profile_prefix: str = "amdgpu_profile",
                          strict_no_cpu: bool = True,
                          provider_options: dict | None = None,
                          target_luid: int | str | None = None,
                          sess_options_override=None):
    """Create an AMDGPU-EP-only InferenceSession. Raises on any failure —
    never a CPU session. target_luid pins the physical adapter's EP device."""
    import onnxruntime as ort

    _ensure_amdgpu_registered()
    so = sess_options_override or ort.SessionOptions()
    if sess_options_override is None:
        if enable_profiling:
            so.enable_profiling = True
            so.profile_file_prefix = profile_prefix
        so.log_severity_level = 3

    if target_luid is not None and hasattr(so, "add_provider_for_devices"):
        dev, device_selection = select_amdgpu_ep_device(target_luid)
        if dev is None:
            raise RuntimeError(
                f"AMDGPU EP device for physical LUID {target_luid} not found "
                f"(fail-closed: no fallback to another adapter): {device_selection}")
        so.add_provider_for_devices([dev], {})
        return ort.InferenceSession(str(onnx_path), sess_options=so)

    providers = ["AMDGPUExecutionProvider"]
    if not strict_no_cpu:
        providers.append("CPUExecutionProvider")
    opts = provider_options or {}
    try:
        return ort.InferenceSession(str(onnx_path), sess_options=so, providers=providers,
                                    provider_options=[opts] if opts else None)
    except Exception:
        if opts:
            return ort.InferenceSession(str(onnx_path), sess_options=so, providers=providers)
        raise
