"""Experiment B4 sub-probe: enumerate every available EP-catalog surface.

Read-only. Each surface is probed in its own process (the Windows App SDK
bootstrap can only be initialised once per process), and the result is one JSON
line so the caller can aggregate without losing a failing surface.
"""
from __future__ import annotations

import argparse
import importlib
import json
import traceback


def probe_projection(version: str | None) -> dict:
    out = {"surface": "wasdk_projection", "bootstrap_version": version}
    mod = importlib.import_module("winui3.microsoft.windows.ai.machinelearning")
    out["types"] = [a for a in ("ExecutionProviderCatalog", "ExecutionProvider",
                                "ExecutionProviderReadyResultState") if hasattr(mod, a)]
    ctx = None
    if version:
        bs = importlib.import_module(
            "winui3.microsoft.windows.applicationmodel.dynamicdependency.bootstrap")
        ctx = bs.initialize(version=version)
        out["bootstrap"] = "initialized"
    cat = mod.ExecutionProviderCatalog.get_default()
    provs = cat.find_all_providers()
    out["provider_count"] = len(provs)
    out["providers"] = [{
        "name": p.name,
        "ready_state": int(getattr(p.ready_state, "value", p.ready_state))
        if p.ready_state is not None else None,
        "certification": int(getattr(p.certification, "value", p.certification))
        if p.certification is not None else None,
        "library_path": p.library_path or "",
        "package_id": p.package_id or "",
    } for p in provs]
    del ctx
    return out


def probe_flat_c() -> dict:
    import importlib.metadata as md
    out = {"surface": "windowsml_flat_c", "version": md.version("windowsml")}
    wm = importlib.import_module("windowsml")
    with wm.EpCatalog() as cat:
        provs = cat.find_all_providers()
    out["provider_count"] = len(provs)
    out["providers"] = [{
        "name": p.name, "version": getattr(p, "version", None),
        "ready_state": int(getattr(p.ready_state, "value", p.ready_state)),
        "certification": int(getattr(p.certification, "value", p.certification)),
        "library_path": p.library_path or "",
        "package_family_name": p.package_family_name or "",
        "package_root_path": p.package_root_path or "",
    } for p in provs]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--surface", choices=["projection", "flat"], default="projection")
    ap.add_argument("--bootstrap-version", default="")
    a = ap.parse_args()
    try:
        if a.surface == "projection":
            res = probe_projection(a.bootstrap_version or None)
        else:
            res = probe_flat_c()
        res["ok"] = True
    except Exception as exc:  # noqa: BLE001 — a failing surface is the evidence
        res = {"surface": a.surface, "bootstrap_version": a.bootstrap_version or None,
               "ok": False, "error": f"{type(exc).__name__}: {exc}",
               "traceback": traceback.format_exc(limit=2)}
    print("SURFACE_JSON " + json.dumps(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
