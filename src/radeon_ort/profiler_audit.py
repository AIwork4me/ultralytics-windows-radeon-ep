"""ORT profiler JSON audit: provider node attribution + CPU fallback detection.

Reads the profile produced by SessionOptions.enable_profiling, counts op
executions attributed per provider (fused EP nodes appear as provider 'AMDGPU' /
'Dml' / 'CPU' in the 'args' of event 'fence_before'/'fence_after' or op events).
Attribute derivation is documented per ORT version; we scan BOTH:
  - 'Node' events with args.provider
  - fused kernel events whose provider field names the EP
Zero target-EP events after a successful run == FAIL_NO_GPU_COMPUTE evidence.
"""

from __future__ import annotations

import json
from pathlib import Path


def audit_profile(profile_path: str | Path) -> dict:
    data = json.loads(Path(profile_path).read_text(encoding="utf-8", errors="replace"))
    counts: dict[str, int] = {}
    fused_events = 0
    op_samples: list[str] = []
    for ev in data:
        cat = ev.get("cat", "")
        args = ev.get("args") or {}
        provider = args.get("provider")
        name = ev.get("name", "")
        if cat in ("Node", "NodeExecution") and provider:
            counts[provider] = counts.get(provider, 0) + 1
            if len(op_samples) < 40:
                op_samples.append(f"{name}@{provider}")
        elif cat == "Kernel" and provider or "fused" in name.lower() and provider:
            counts[provider] = counts.get(provider, 0) + 1
            fused_events += 1
        elif cat in ("fence_before", "fence_after"):
            continue
    return {
        "provider_node_counts": counts,
        "fused_event_count": fused_events,
        "op_samples": op_samples,
        "total_events": len(data),
    }


def provider_counts_from_session(session, profile_path_hint: str | Path | None = None) -> dict:
    """End a profiling session (session.end_profiling() writes the JSON) then audit."""
    p = session.end_profiling()
    return audit_profile(p if isinstance(p, str) else profile_path_hint)


def no_gpu_compute(audit: dict, ep_name: str) -> bool:
    return (audit.get("provider_node_counts", {}).get(ep_name, 0) == 0)
