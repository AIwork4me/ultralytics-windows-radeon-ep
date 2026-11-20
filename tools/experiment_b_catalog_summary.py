"""Experiment B1: canonicalise the three catalog-probe repeats into one report.

The three repeats are kept byte-exact under reports/experiment_b/_raw/; this
module writes the canonical CATALOG_PROBE.json / STANDALONE_CATALOG.json that the
mission names, embeds repeat #1 in full, and proves repeat stability by hashing
each body with the volatile capture timestamp masked out.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def masked_digest(obj: dict) -> str:
    """Hash the report with volatile timestamps masked (capture-time independent)."""
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    text = TS_RE.sub("<TS>", text)
    return hashlib.sha256(text.encode()).hexdigest()


def summarise(kind: str, runs: list[Path], codes: dict, run_id: str) -> dict:
    repeats, bodies = [], []
    for i, p in enumerate(runs, start=1):
        raw = p.read_bytes()
        obj = json.loads(raw.decode("utf-8"))
        bodies.append(obj)
        entry = {
            "run": i,
            "file": p.relative_to(REPO).as_posix(),
            "file_sha256": sha256_bytes(raw),
            "file_bytes": len(raw),
            "captured_utc": obj.get("captured_utc"),
            "exit_code": codes.get(f"{kind}_run{i}"),
            "stable_digest": obj.get("stable_digest"),
            "masked_digest": masked_digest(obj),
        }
        if kind == "runner":
            entry.update({
                "state": obj.get("state"),
                "verdict": obj.get("verdict"),
                "blocked_status": obj.get("blocked_status"),
                "catalog_provider_count": len((obj.get("ep_readiness") or {}).get("catalog_providers") or []),
                "create_hresult": ((obj.get("api_diagnostic") or {}).get("official") or {}).get("raw", {}).get("create_hresult"),
                "enum_hresult": ((obj.get("api_diagnostic") or {}).get("official") or {}).get("raw", {}).get("enum_hresult"),
            })
        else:
            entry.update({
                "state": obj.get("blocked_status"),
                "verdict": obj.get("blocked_status"),
                "catalog_provider_count": ((obj.get("catalog") or {}).get("provider_count")),
                "create_hresult": (((obj.get("catalog") or {}).get("raw_hresults") or {}).get("create_hresult")),
                "enum_hresult": (((obj.get("catalog") or {}).get("raw_hresults") or {}).get("enum_hresult")),
            })
        repeats.append(entry)
    masked = {r["masked_digest"] for r in repeats}
    stable = len(masked) == 1
    return {
        "schema": "experiment_b_catalog_probe_summary_v1",
        "mission": "B_GFX1151_AMDGPU_EP_ROOT_CAUSE",
        "run_id": run_id,
        "tool": ("runner.probe_amdgpu_ep" if kind == "runner"
                 else "repro/windowsml_amdgpu_catalog_probe.py"),
        "repeat_count": len(runs),
        "repeats": repeats,
        "stability": {
            "identical_masked_digest": stable,
            "distinct_masked_digests": sorted(masked),
            "identical_exit_codes": len({r["exit_code"] for r in repeats}) == 1,
            "identical_stable_digest": len({r["stable_digest"] for r in repeats}) == 1,
        },
        "verdict_label": ("B_CATALOG_EMPTY_REPRODUCED"
                          if stable and all((r.get("catalog_provider_count") == 0) for r in repeats)
                          else "B_CATALOG_UNSTABLE_OR_NONEMPTY"),
        "primary_report": bodies[0],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--raw-dir", default="reports/experiment_b/_raw")
    ap.add_argument("--out-catalog", default="reports/experiment_b/CATALOG_PROBE.json")
    ap.add_argument("--out-standalone", default="reports/experiment_b/STANDALONE_CATALOG.json")
    a = ap.parse_args()

    raw = REPO / a.raw_dir
    codes = json.loads((raw / "exitcodes.json").read_text(encoding="utf-8-sig"))
    runner_runs = [raw / f"CATALOG_PROBE_run{i}.json" for i in (1, 2, 3)]
    stand_runs = [raw / f"STANDALONE_run{i}.json" for i in (1, 2, 3)]
    for p in runner_runs + stand_runs:
        if not p.is_file():
            raise SystemExit(f"missing repeat: {p}")

    cat = summarise("runner", runner_runs, codes, a.run_id)
    std = summarise("standalone", stand_runs, codes, a.run_id)
    for path, doc in ((REPO / a.out_catalog, cat), (REPO / a.out_standalone, std)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        print(json.dumps({"out": path.relative_to(REPO).as_posix(),
                          "verdict": doc["verdict_label"],
                          "stable": doc["stability"]["identical_masked_digest"],
                          "exit_codes": [r["exit_code"] for r in doc["repeats"]]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
