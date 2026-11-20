"""Experiment B: redact the deployment-event dump before it becomes evidence.

The raw AppXDeployment event log contains account SIDs and per-user install
paths. The repository may not commit machine-identifying data, so this module
keeps only the events the report actually cites, masks SIDs and user paths, and
replaces the raw capture with the redacted subset.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from radeon_ort.redaction import redact_text, scan_for_leaks

SID_RE = re.compile(r"S-1-(?:\d+-){1,14}\d+")
KEEP_RE = re.compile(r"VitisAI|WinMLShared|AMD\.GPU|AMDGPU|amdgpu|EP\.AMD", re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="reports/experiment_b/_raw/b4_appx_events.json")
    ap.add_argument("--out", default="reports/experiment_b/_raw/b4_appx_events_redacted.json")
    ap.add_argument("--max-events", type=int, default=80)
    ap.add_argument("--keep-raw", action="store_true")
    a = ap.parse_args()

    raw_path = REPO / a.raw
    doc = json.loads(raw_path.read_text(encoding="utf-8-sig"))
    all_events = [e for e in (doc.get("events") or []) if e.get("message")]
    total = len(all_events)
    keyword_counts = {
        "VitisAI": sum(1 for e in all_events if "VitisAI" in e["message"]),
        "AMD_GPU_EP": sum(1 for e in all_events
                          if "AMD.GPU" in e["message"] or "AMDGPU" in e["message"]),
        "amdgpu_lower": sum(1 for e in all_events if "amdgpu" in e["message"].lower()),
        "WinMLShared": sum(1 for e in all_events if "WinMLShared" in e["message"]),
    }
    vitis_times = sorted(e["time"] for e in all_events if "VitisAI" in e["message"])
    kept, masked_sids, masked_paths = [], 0, 0
    for e in doc.get("events") or []:
        msg = e.get("message") or ""
        if not msg or not KEEP_RE.search(msg):
            continue
        if SID_RE.search(msg):
            masked_sids += len(SID_RE.findall(msg))
            msg = SID_RE.sub("<SID-redacted>", msg)
        red = redact_text(msg)
        if red != msg:
            masked_paths += 1
        kept.append({"time": e.get("time"), "id": e.get("id"),
                     "level": e.get("level"), "message": red[:400]})
        if len(kept) >= a.max_events:
            break
    out = {
        "schema": "experiment_b_appx_events_redacted_v1",
        "captured_utc": utc_now(),
        "source": ("Microsoft-Windows-AppXDeploymentServer/Operational and "
                   "Microsoft-Windows-AppXDeployment/Operational"),
        "raw_event_count": total,
        "keyword_counts_over_full_dump": keyword_counts,
        "vitisai_registration_first": (vitis_times[0] if vitis_times else None),
        "vitisai_registration_last": (vitis_times[-1] if vitis_times else None),
        "filter": KEEP_RE.pattern,
        "kept_events": len(kept),
        "redactions": {"sids_masked": masked_sids, "paths_masked": masked_paths},
        "note": ("the raw dump is not committed: it carries account SIDs and per-user "
                 "install paths; the counts in MSIX_INVENTORY.json are derived from the "
                 "full dump, and the rows below are the events the report cites"),
        "events": kept,
    }
    body = json.dumps(out, indent=1) + "\n"
    leaks = scan_for_leaks(body) + (["SID remains in body"] if SID_RE.search(body) else [])
    out["redaction_scan"] = {"ok": not leaks, "leaks": leaks}
    body = json.dumps(out, indent=1) + "\n"
    dst = REPO / a.out
    dst.write_text(body, encoding="utf-8")
    if not a.keep_raw:
        raw_path.unlink()
    print(json.dumps({"out": a.out, "raw_events": total, "kept": len(kept),
                      "sids_masked": masked_sids, "leaks": leaks,
                      "sha256": hashlib.sha256(body.encode()).hexdigest()[:16]}, indent=1))
    return 0 if not leaks else 3


if __name__ == "__main__":
    raise SystemExit(main())
