"""RCA2A cache-key computation — what a cache entry must be bound to.

A compiled artifact or tuning solution may only be reused when the key covers
everything that can change the compiled result: model bytes, input signature,
EP DLL bytes, driver, GPU arch, precision/shape and the effective compile
flags. Cross-arch reuse (gfx1100 vs gfx1151/gfx1201) is structurally
impossible because gpu_arch is part of the key, not a runtime comparison.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

KEY_FORMAT_VERSION = "rca2a_cache_key_v1"

# fields that MUST be present — anything absent fails closed (KeyError by design)
REQUIRED_FIELDS = (
    "model_sha256",      # exact ONNX bytes
    "input_signature",   # shapes+dtypes of all feeds (ordered)
    "ep_dll_sha256",     # amdgpu-ep.dll bytes
    "mgx_dll_sha256",    # migraphx_backend/migraphx_gpu identity
    "driver_version",    # AMD driver
    "gpu_arch",          # gfx1100 (never mixed with gfx1151/gfx1201)
    "precision",         # fp32/fp16...
    "ep_version",        # MSIX package version
    "ort_version",
    "effective_flags",   # sorted dict of tuning/cache knobs in effect
)


def normalize_flags(flags: dict[str, Any] | None) -> dict[str, Any]:
    """Canonical flag dict: drop Nones, stringify values, sort keys."""
    out: dict[str, str] = {}
    for k in sorted(flags or {}):
        v = (flags or {})[k]
        if v is None:
            continue
        out[k] = v if isinstance(v, str) else json.dumps(v, sort_keys=True)
    return out


def compute_cache_key(**fields: Any) -> str:
    missing = [f for f in REQUIRED_FIELDS if f not in fields or fields[f] in (None, "")]
    if missing:
        raise KeyError(f"cache key missing required fields: {missing}")
    if not str(fields["gpu_arch"]).startswith("gfx"):
        raise ValueError(f"gpu_arch must be a gfx target, got {fields['gpu_arch']!r}")
    payload = {
        "format": KEY_FORMAT_VERSION,
        **{f: (normalize_flags(fields[f]) if f == "effective_flags" else str(fields[f]))
           for f in REQUIRED_FIELDS},
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def input_signature(feeds: dict[str, Any]) -> str:
    """Ordered 'name:dtype:shape;...' signature from feed tensors."""
    import numpy as np
    parts = []
    for name in sorted(feeds):
        t = np.asarray(feeds[name])
        parts.append(f"{name}:{t.dtype}:{list(t.shape)}")
    return ";".join(parts)


def describe_invalidation(old_key: str, new_key: str, old_fields: dict,
                          new_fields: dict) -> dict:
    """Human-readable diff of which key field(s) changed (cache invalidation audit)."""
    changed = {}
    for f in REQUIRED_FIELDS:
        a = old_fields.get(f)
        b = new_fields.get(f)
        av = normalize_flags(a) if f == "effective_flags" and a else a
        bv = normalize_flags(b) if f == "effective_flags" and b else b
        if av != bv:
            changed[f] = {"old": av, "new": bv}
    return {"old_key": old_key, "new_key": new_key, "keys_equal": old_key == new_key,
            "changed_fields": changed}
