"""python -m runner.analyze_profiles --run-dir <dir> — summarize profile node attribution."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from radeon_ort.profiler_audit import audit_profile


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    a = ap.parse_args()
    out = {}
    for p in sorted(Path(a.run_dir).glob("**/*.json")):
        if "profile" not in p.name.lower() and "trace" not in p.name.lower():
            continue
        try:
            out[p.name] = audit_profile(p)
        except Exception as e:  # noqa: BLE001
            out[p.name] = {"error": str(e)}
    for name, v in out.items():
        print(f"{name}: counts={v.get('provider_node_counts')} fused={v.get('fused_event_count')}")
    (Path(a.run_dir) / "PROFILE_SUMMARY.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
