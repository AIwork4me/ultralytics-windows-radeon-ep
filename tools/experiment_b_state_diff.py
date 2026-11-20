"""Experiment B: before/after system-state diff for the read-only guarantee.

Compares the pre- and post-experiment snapshots on the fields an installation
would have changed, and reports SYSTEM_STATE_UNCHANGED only when every compared
field is identical. Volatile fields (timestamps, memory counters, event counts)
are listed explicitly as excluded so the claim stays honest.
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXP = REPO / "reports" / "experiment_b"


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def pick(snap: dict) -> dict:
    git = snap.get("git") or {}
    gpu = snap.get("gpu") or {}
    res = gpu.get("resolve") or {}
    rt = snap.get("runtime") or {}
    appx = snap.get("appx") or {}
    cat = snap.get("catalog_preflight") or {}
    return {
        "git_head": git.get("head_sha"),
        "git_branch": git.get("branch"),
        "os_build": ((snap.get("os") or {}).get("windows") or {}).get("build"),
        "os_ubr": ((snap.get("os") or {}).get("windows") or {}).get("ubr"),
        "device_id": res.get("selected_device_id_hex"),
        "dxgi_luid": res.get("selected_luid"),
        "driver_version": (((snap.get("driver") or {}).get("records") or [{}])[0]
                           .get("data") or {}).get("DriverVersion"),
        "windowsml_python": (rt.get("packages") or {}).get("windowsml"),
        "onnxruntime_windowsml": (rt.get("packages") or {}).get("onnxruntime-windowsml"),
        "ort_available_providers": ((rt.get("ort") or {}).get("available_providers")),
        "ort_ep_devices": [d.get("ep_name") for d in ((rt.get("ort") or {}).get("ep_devices") or [])],
        "appx_relevant_count": len(appx.get("current_user") or []),
        "appx_amdgpu_ep_current_user": appx.get("amdgpu_ep_packages_current_user"),
        "appx_amdgpu_ep_machine": appx.get("amdgpu_ep_packages_machine"),
        "appx_machine_total": appx.get("machine_allusers_total"),
        "ep_dll_count": len(((snap.get("ep_dll") or {}).get("scan") or {}).get("dlls") or []),
        "catalog_providers_flat_c": len(((cat.get("windowsml") or {}).get("providers")) or []),
        "catalog_providers_projection": len(cat.get("catalog_providers") or []),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--before", default="reports/experiment_b/PRE_EXPERIMENT_SNAPSHOT.json")
    ap.add_argument("--after", default="reports/experiment_b/_raw/POST_EXPERIMENT_SNAPSHOT.json")
    ap.add_argument("--out", default="reports/experiment_b/POST_EXPERIMENT_STATE_DIFF.json")
    a = ap.parse_args()
    before = pick(load(REPO / a.before))
    after = pick(load(REPO / a.after))
    rows = []
    for k in before:
        same = before[k] == after[k]
        rows.append({"field": k, "before": before[k], "after": after[k], "unchanged": same})
    changed = [r["field"] for r in rows if not r["unchanged"]]
    doc = {
        "schema": "experiment_b_state_diff_v1",
        "mission": "B_GFX1151_AMDGPU_EP_ROOT_CAUSE",
        "run_id": a.run_id,
        "captured_utc": utc_now(),
        "inputs": {"before": a.before, "after": a.after},
        "compared_fields": len(rows),
        "changed_fields": changed,
        "rows": rows,
        "excluded_volatile_fields": ["captured_utc", "captured_local", "install_date",
                                     "last_boot", "memory counters", "process counts",
                                     "PendingFileRenameOperations"],
        "verdict": ("SYSTEM_STATE_UNCHANGED" if not changed
                    else "SYSTEM_STATE_CHANGED_SEE_ROWS"),
    }
    out = REPO / a.out
    out.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"out": a.out, "verdict": doc["verdict"],
                      "changed": changed, "fields": len(rows)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
