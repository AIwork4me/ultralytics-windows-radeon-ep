"""Provider policy + session-option construction for AMDGPU / DML / CPU sessions.

Policies (mission P1.4):
- require_amdgpu: AMDGPU only; any CPU fallback = visible FAIL.
- prefer_amdgpu_allow_dml: AMDGPU first; on failure a NEW subprocess retries DML on
  the same physical GPU; the receipt records the original failure, never masks it.
- cpu_opt_in: only with explicit user flag; labels CPU_ONLY_USER_AUTHORIZED.
"""

from __future__ import annotations

from dataclasses import dataclass

POLICIES = ("require_amdgpu", "prefer_amdgpu_allow_dml", "cpu_opt_in")


@dataclass
class ProviderPolicy:
    mode: str = "require_amdgpu"

    def __post_init__(self):
        if self.mode not in POLICIES:
            raise ValueError(f"unknown policy {self.mode!r}; expected one of {POLICIES}")

    def providers_for(self, ep: str) -> list[str]:
        if self.mode == "cpu_opt_in":
            return ["CPUExecutionProvider"]
        if ep == "amdgpu":
            return ["AMDGPU"]  # strict: no CPU entry => create fails if EP unavailable
        if ep == "dml":
            return ["DmlExecutionProvider", "CPUExecutionProvider"]
        raise ValueError(ep)

    def allows_dml_fallback(self) -> bool:
        return self.mode == "prefer_amdgpu_allow_dml"

    def label(self) -> str:
        if self.mode == "cpu_opt_in":
            return "CPU_ONLY_USER_AUTHORIZED"
        return self.mode


def session_options_common(enable_profiling: bool = True, profile_path: str = "ort_profile.json"):
    import onnxruntime as ort
    so = ort.SessionOptions()
    if enable_profiling:
        so.enable_profiling = True
        so.profile_file_prefix = profile_path
    so.log_severity_level = 3
    so.log_verbosity_level = 0
    return so


def capture_stderr_fallback_signals(stderr_text: str) -> list[str]:
    """Patterns that indicate an EP wrapper silently fell back to CPU."""
    signals = []
    pats = {
        "fall back": "fallback phrase 'fall back'",
        "falling back": "fallback phrase 'falling back'",
        "failed to initialize": "EP init failure",
        "using cpu": "switched to CPU",
        "cpu execution provider will be used": "cpu provider substitution",
        "amdgpu": "amdgpu-related warning",
        "directml": "directml-related warning",
    }
    low = (stderr_text or "").lower()
    for pat, label in pats.items():
        if pat in low:
            signals.append(label)
    return signals
