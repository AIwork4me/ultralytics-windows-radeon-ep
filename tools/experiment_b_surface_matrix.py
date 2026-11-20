"""Experiment B4: run every catalog surface in its own process and tabulate.

Read-only. The Windows App SDK bootstrap is process-wide, so each surface runs
as a child process; a surface that crashes or refuses is recorded with its exit
code instead of being silently dropped.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from radeon_ort.redaction import redact_text

PY = Path(sys.executable)


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_one(args: list[str]) -> dict:
    cmd = [str(PY), "tools/experiment_b_catalog_surfaces.py", *args]
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO),
                       encoding="utf-8", errors="replace", timeout=180, check=False)
    payload = None
    for ln in (p.stdout or "").splitlines():
        if ln.startswith("SURFACE_JSON "):
            payload = json.loads(ln[len("SURFACE_JSON "):])
    return {
        "argv": args,
        "exit_code": p.returncode,
        "parsed": payload,
        "stdout_tail": redact_text((p.stdout or "")[-400:]),
        "stderr_tail": redact_text((p.stderr or "")[-600:]),
        "terminated_without_payload": payload is None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out", default="reports/experiment_b/CATALOG_SURFACE_MATRIX.json")
    a = ap.parse_args()
    surfaces = [
        ("wasdk_projection__no_bootstrap", ["--surface", "projection"]),
        ("wasdk_projection__bootstrap_1_7", ["--surface", "projection", "--bootstrap-version", "1.7"]),
        ("wasdk_projection__bootstrap_1_8", ["--surface", "projection", "--bootstrap-version", "1.8"]),
        ("wasdk_projection__bootstrap_2_3", ["--surface", "projection", "--bootstrap-version", "2.3"]),
        ("wasdk_projection__bootstrap_2_6", ["--surface", "projection", "--bootstrap-version", "2.6"]),
        ("wasdk_projection__bootstrap_2_7", ["--surface", "projection", "--bootstrap-version", "2.7"]),
        ("windowsml_flat_c_2_5_96", ["--surface", "flat"]),
    ]
    rows = []
    for name, argv in surfaces:
        r = run_one(argv)
        parsed = r.get("parsed") or {}
        rows.append({
            "surface": name,
            "exit_code": r["exit_code"],
            "ok": parsed.get("ok"),
            "provider_count": parsed.get("provider_count"),
            "providers": parsed.get("providers"),
            "error": parsed.get("error"),
            "terminated_without_payload": r["terminated_without_payload"],
            "stderr_tail": r["stderr_tail"] if not parsed.get("ok") else "",
        })
        print(json.dumps({"surface": name, "exit": r["exit_code"],
                          "providers": parsed.get("provider_count"),
                          "ok": parsed.get("ok"),
                          "err": (parsed.get("error") or "")[:120]}))
    usable = [r for r in rows if r["ok"]]
    doc = {
        "schema": "experiment_b_catalog_surface_matrix_v1",
        "mission": "B_GFX1151_AMDGPU_EP_ROOT_CAUSE",
        "run_id": a.run_id,
        "captured_utc": utc_now(),
        "read_only": True,
        "surfaces_probed": len(rows),
        "surfaces_usable": len(usable),
        "surfaces_with_any_provider": [r["surface"] for r in usable if (r["provider_count"] or 0) > 0],
        "conclusion": ("NO_CATALOG_SURFACE_ON_THIS_HOST_LISTS_ANY_EXECUTION_PROVIDER"
                       if usable and all((r["provider_count"] or 0) == 0 for r in usable)
                       else "AT_LEAST_ONE_SURFACE_LISTS_A_PROVIDER"),
        "rows": rows,
    }
    out = REPO / a.out
    out.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"out": a.out, "conclusion": doc["conclusion"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
