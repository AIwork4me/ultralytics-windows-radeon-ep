"""python -m runner.dml_binding_probe — strengthen DML device-binding evidence.

DML exposes only an adapter ordinal (no LUID/PCI identity) on this build. This
probe records that limitation and adds a negative control: a session requested
with an invalid `device_id` must fail (or lose the DML provider) rather than
silently execute on another device. That proves the ordinal is enforced, while
still never claiming an EP-reported LUID.

Read-only except for one tiny canary session/run; writes only the requested JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runner import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--gfx", default=None)
    a = ap.parse_args()

    from radeon_ort import profiler_audit
    from radeon_ort.amdgpu_session import canary_model_path
    from radeon_ort.device_identity import (canary_inputs, enum_dxgi_adapters,
                                            infer_gfx_from_adapters,
                                            resolve_target_adapter)
    from radeon_ort.dml_session import (create_dml_session,
                                        resolve_dml_device_id_for_adapter)

    adapters = enum_dxgi_adapters()
    gfx = a.gfx
    if gfx is None:
        inf = infer_gfx_from_adapters(adapters)
        if not inf.get("ok"):
            out = {"verdict": "BLOCKED_IDENTITY", "error": inf.get("reason")}
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
            print(json.dumps(out))
            return 2
        gfx = inf["gfx"]
    target = resolve_target_adapter(adapters, gfx=gfx)
    if not target.get("ok"):
        out = {"verdict": "BLOCKED_IDENTITY", "error": target.get("reason")}
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(json.dumps(out))
        return 2
    adapter = target["adapter"]
    dml_id = resolve_dml_device_id_for_adapter(adapter.index)

    import onnxruntime as ort
    ep_devices = [
        {"ep_name": d.ep_name, "ep_vendor": d.ep_vendor,
         "ep_options": dict(d.ep_options or {}),
         "ep_metadata": dict(d.ep_metadata or {})}
        for d in ort.get_ep_devices() if d.ep_name == "DmlExecutionProvider"
    ]

    tmp = Path(a.out).parent
    model = canary_model_path(tmp)
    feeds = canary_inputs()
    result: dict = {
        "schema_version": 1,
        "gfx": gfx,
        "dxgi": {"index": adapter.index, "luid": adapter.luid_str,
                 "vendor_id": f"0x{adapter.vendor_id:04X}",
                 "device_id": f"0x{adapter.device_id:04X}",
                 "note": "host-side DXGI enumeration; NOT EP-reported"},
        "dml_ep_devices": ep_devices,
        "selected_device_id": dml_id,
        "valid_session": None,
        "invalid_device_attempts": [],
        "limitation": "DML exposes only an ordinal; no EP-reported LUID/PCI id",
        "verdict": "UNKNOWN",
    }
    try:
        sess = create_dml_session(str(model), device_id=dml_id,
                                  enable_profiling=True,
                                  profile_prefix=str(tmp / "dml_binding_valid"))
        sess.run(None, feeds)
        counts = profiler_audit.audit_profile(sess.end_profiling())["provider_node_counts"]
        result["valid_session"] = {
            "created": True, "providers": list(sess.get_providers()),
            "provider_node_counts": counts,
            "dml_nodes": counts.get("DmlExecutionProvider", 0),
        }
    except Exception as e:  # noqa: BLE001
        result["valid_session"] = {"created": False,
                                   "error": f"{type(e).__name__}: {e}"}

    for bad in sorted({dml_id + 1, dml_id + 7, 99}):
        entry = {"attempted_device_id": bad, "failed": False, "error": None,
                 "providers": None, "kind": "out_of_range"}
        if bad == dml_id + 1:
            entry["kind"] = "adjacent_enumeration"
        try:
            s = create_dml_session(str(model), device_id=bad,
                                   enable_profiling=False)
            entry["providers"] = list(s.get_providers())
            # A session that still lists DML for an invalid ordinal means the
            # ordinal was not enforced; record it honestly.
            entry["failed"] = False
        except Exception as e:  # noqa: BLE001
            entry["failed"] = True
            entry["error"] = f"{type(e).__name__}: {e}"[:500]
        result["invalid_device_attempts"].append(entry)

    valid_ok = bool((result["valid_session"] or {}).get("dml_nodes", 0) > 0)
    out_of_range = [x for x in result["invalid_device_attempts"]
                    if x["kind"] == "out_of_range"]
    adjacent = [x for x in result["invalid_device_attempts"]
                if x["kind"] == "adjacent_enumeration"]
    invalid_ok = bool(out_of_range) and all(x["failed"] for x in out_of_range)
    result["negative_control_enforced"] = invalid_ok
    result["adjacent_ordinal_accepted"] = any(not x["failed"] for x in adjacent)
    result["verdict"] = (
        "EP_DEVICE_SELECTED_ORDINAL_ENFORCED"
        if valid_ok and invalid_ok else
        "EP_DEVICE_ORDINAL_NOT_ENFORCED" if valid_ok else
        "BLOCKED_DML_SESSION")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": result["verdict"],
                      "valid_nodes": (result["valid_session"] or {}).get("dml_nodes"),
                      "invalid_attempts": result["invalid_device_attempts"]},
                     indent=2))
    return 0 if result["verdict"] == "EP_DEVICE_SELECTED_ORDINAL_ENFORCED" else 1


if __name__ == "__main__":
    sys.exit(main())
