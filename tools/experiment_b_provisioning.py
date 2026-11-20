"""Experiment B5: record the conditional official provisioning attempt.

Nothing is installed. The point of this module is to prove, machine-readably,
that neither documented official channel can be addressed on this host, and to
record the preconditions the repository's Owner-Authorized Provisioning
Exception would require if it could be.

Read-only: it enumerates the catalog and introspects the binding's API surface.
It never calls ensure_ready on anything and never creates an ORT session.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata as md
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(rel: str) -> dict:
    return json.loads((REPO / rel).read_text(encoding="utf-8"))


def api_surface() -> dict:
    out: dict = {}
    try:
        wm = importlib.import_module("windowsml")
        out["windowsml_version"] = md.version("windowsml")
        out["module_members"] = sorted(m for m in dir(wm) if not m.startswith("_"))
        for cls_name in ("EpCatalog", "EpProvider"):
            cls = getattr(wm, cls_name, None)
            if cls is None:
                continue
            out[f"{cls_name}_methods"] = sorted(
                m for m in dir(cls) if not m.startswith("_"))
            sigs = {}
            for m in out[f"{cls_name}_methods"]:
                try:
                    sigs[m] = str(inspect.signature(getattr(cls, m)))
                except (TypeError, ValueError):
                    sigs[m] = None
            out[f"{cls_name}_signatures"] = sigs
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def catalog_lookup() -> dict:
    import windowsml as wm
    with wm.EpCatalog() as cat:
        provs = cat.find_all_providers()
    names = [p.name for p in provs]
    target = [p for p in provs if "amdgpu" in (p.name or "").lower()]
    return {
        "provider_count": len(provs),
        "provider_names": names,
        "amdgpu_provider_found": bool(target),
        "amdgpu_provider": ({"name": target[0].name,
                             "ready_state": int(getattr(target[0].ready_state, "value",
                                                        target[0].ready_state)),
                             "library_path": target[0].library_path or ""}
                            if target else None),
        "ensure_ready_addressed": False,
        "ensure_ready_reason": ("ensure_ready is an instance method on a provider returned "
                                "by find_all_providers(); with zero providers there is no "
                                "object to address, so no download/install was attempted"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out", default="reports/experiment_b/PROVISIONING_ATTEMPT.json")
    a = ap.parse_args()

    pre = load("reports/experiment_b/PRE_EXPERIMENT_SNAPSHOT.json")
    surfaces = load("reports/experiment_b/CATALOG_SURFACE_MATRIX.json")
    art = load("reports/experiment_b/OFFICIAL_ARTIFACT_PROBE.json")
    lookup = catalog_lookup()
    surface_api = api_surface()

    host = {
        "named_host": "HP ZBook Ultra G1a 14 (Ryzen AI MAX+ PRO 395 / Radeon 8060S)",
        "host_id": (pre.get("gpu") or {}).get("host_fingerprint", {}).get("host_id"),
        "gfx": "gfx1151",
        "device_id": "0x1586",
        "os_build": ((pre.get("os") or {}).get("windows") or {}).get("build"),
        "elevated": False,
    }

    doc = {
        "schema": "experiment_b_provisioning_attempt_v1",
        "mission": "B_GFX1151_AMDGPU_EP_ROOT_CAUSE",
        "run_id": a.run_id,
        "captured_utc": utc_now(),
        "read_only": True,
        "system_components_installed_or_changed": 0,
        "authorization": {
            "policy": "AGENTS.md Owner-Authorized Provisioning Exception",
            "scope": ("one named physical validation host; one component per step; "
                      "read-only probe before/after; rollback path recorded"),
            "owner_authorized_components": [
                ("pip install windowsml==2.5.96 (Python binding only, no system "
                 "component) - already performed in mission R4/A1, not repeated here"),
            ],
            "not_authorized_here": [
                "installing an MSIX signed by Microsoft without a matching catalog entry",
                "replacing or upgrading the HP OEM display driver",
                "copying a DLL out of C:/Program Files/WindowsApps",
                "disabling Defender / Secure Boot / driver signature enforcement",
            ],
            "host": host,
        },
        "path_1_official_catalog": {
            "documented_flow": ("ExecutionProviderCatalog.find_all_providers() -> find "
                                "AMDGPUExecutionProvider -> ensure_ready_async() -> "
                                "register the returned library_path with ORT"),
            "observed": lookup,
            "catalog_surfaces_probed": surfaces.get("surfaces_probed"),
            "surfaces_with_any_provider": surfaces.get("surfaces_with_any_provider"),
            "outcome": "NOT_ADDRESSABLE_CATALOG_EMPTY",
            "conclusion": ("the catalog does not list AMDGPUExecutionProvider on any surface, "
                           "so the official dynamic-install path cannot be started"),
        },
        "path_2_official_signed_byo": {
            "documented_flow": ("obtain an official signed Windows artifact for this device and "
                               "ORT ABI, verify publisher and hash, register amdgpu-ep.dll"),
            "artifact_probe": {
                "repo": art.get("summary", {}).get("repo"),
                "github_releases": art.get("summary", {}).get("github_releases_count"),
                "github_release_assets": art.get("summary", {}).get("github_releases"),
                "nuget_search_total": art.get("summary", {}).get("nuget_search_total"),
                "nuget_package_404": [t["url"] for t in art.get("targets", [])
                                      if not t.get("ok") and t.get("status") == 404],
            },
            "binding_api_surface": surface_api,
            "outcome": "NO_OFFICIAL_REDISTRIBUTABLE_ARTIFACT",
            "conclusion": ("the official repository publishes zero releases and zero packages, "
                           "and the official BYO page states the AMDGPU EP must be built from "
                           "source; building it would require a ROCm toolchain and ORT source "
                           "on this host, which is out of scope for a read-only mission and "
                           "would not resolve the catalog-side blocker"),
        },
        "forbidden_actions_not_taken": [
            {"action": "copy amdgpu-ep.dll from another machine's WindowsApps",
             "reason": "AGENTS.md rule 2/6 and the mission's BYO prohibition; a copied binary "
                       "cannot be attested as official for this host"},
            {"action": "install the 1.8.65.0 MSIX from an unverified source",
             "reason": "no signed artifact is published and the package identity for this "
                       "device generation is not offered"},
            {"action": "update the HP OEM display driver",
             "reason": "no evidence ties the empty catalog to the driver, and a driver change "
                       "would destroy the working DML baseline (rule 6)"},
            {"action": "disable Defender / Secure Boot / signature enforcement",
             "reason": "explicitly forbidden by AGENTS.md"},
        ],
        "provisioning_exception_checklist": [
            {"item": "named physical host", "recorded": True, "value": host["named_host"]},
            {"item": "exact official component and version", "recorded": False,
             "value": "NONE - no component was installed"},
            {"item": "official download source", "recorded": False,
             "value": "NONE AVAILABLE (0 releases, no NuGet package)"},
            {"item": "signature and hash", "recorded": False, "value": "N/A"},
            {"item": "supported dependencies", "recorded": True,
             "value": "Windows ML 2.5.96 + WindowsWorkload.WinMLShared.7.0 1.2610.1050.0 present"},
            {"item": "pre-change state", "recorded": True,
             "value": "reports/experiment_b/PRE_EXPERIMENT_SNAPSHOT.json"},
            {"item": "rollback path", "recorded": False, "value": "N/A (nothing installed)"},
            {"item": "administrator rights", "recorded": True, "value": "not elevated"},
            {"item": "isolated environment", "recorded": True,
             "value": ".venv-winml (user scope); no system-wide change"},
            {"item": "post-change state", "recorded": True,
             "value": "reports/experiment_b/_raw/POST_EXPERIMENT_SNAPSHOT.json"},
        ],
        "verdict": {
            "label": "B_BLOCKED_OFFICIAL_ARTIFACT_UNAVAILABLE",
            "statement": ("no catalog entry to install and no official redistributable to bring; "
                          "the system was left unchanged"),
            "escalation_targets": ["Microsoft (Windows ML EP catalog / Store targeting)",
                                   "AMD (AMDGPU EP gfx1151 support statement)"],
        },
    }
    out = REPO / a.out
    out.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"out": a.out, "verdict": doc["verdict"]["label"],
                      "catalog_providers": lookup["provider_count"],
                      "amdgpu_found": lookup["amdgpu_provider_found"],
                      "api_members": surface_api.get("EpCatalog_methods")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
