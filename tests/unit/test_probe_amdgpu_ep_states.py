"""AMDGPU EP read-only probe state machine (R3-C).

The five states must be distinguishable, and a missing binding must never be
reported as a successful empty catalog.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runner import probe_amdgpu_ep as probe


def _official(api_ok=True, providers=(), installed=True):
    return {"engine": "windowsml_flat_c", "installed": installed,
            "version": "2.5.96" if installed else None, "api_ok": api_ok,
            "providers": list(providers), "errors": []}


def _legacy(api_ok=False, providers=()):
    return {"engine": "wasdk_projection", "installed": False, "api_ok": api_ok,
            "providers": list(providers), "errors": []}


def _ort(devices=()):
    return {"importable": True, "version": "1.25.2", "providers": [],
            "ep_devices": list(devices), "amd_devices": list(devices),
            "registration": None}


def test_not_installed_when_no_bindings_and_no_artifact():
    state = probe.classify(official=_official(api_ok=False, installed=False),
                           legacy=_legacy(), ort=_ort([]), dlls=[])
    assert state == probe.NOT_INSTALLED


def test_catalog_empty_when_api_answers_with_zero_providers():
    state = probe.classify(official=_official(), legacy=_legacy(), ort=_ort([]),
                           dlls=[])
    assert state == probe.CATALOG_EMPTY


def test_ep_not_ready_when_catalog_offers_amdgpu_but_nothing_registered():
    offered = [{"name": "AMDGPUExecutionProvider", "ready_state": 2}]
    state = probe.classify(official=_official(providers=offered),
                           legacy=_legacy(), ort=_ort([]), dlls=[])
    assert state == probe.EP_NOT_READY


def test_ep_not_ready_when_dll_exists_without_registration():
    state = probe.classify(official=_official(), legacy=_legacy(), ort=_ort([]),
                           dlls=[{"path": "x", "sha256": "a" * 64}])
    assert state == probe.EP_NOT_READY


def test_ep_registered_when_ort_sees_amd_device():
    dev = [{"ep_name": "AMDGPUExecutionProvider"}]
    state = probe.classify(official=_official(), legacy=_legacy(),
                           ort=_ort(dev), dlls=[])
    assert state == probe.EP_REGISTERED


def test_gpu_executed_requires_zero_cpu_nodes():
    dev = [{"ep_name": "AMDGPUExecutionProvider"}]
    assert probe.classify(official=_official(), legacy=_legacy(), ort=_ort(dev),
                          dlls=[], canary={"executed_full": True}) == probe.GPU_EXECUTED
    assert probe.classify(official=_official(), legacy=_legacy(), ort=_ort(dev),
                          dlls=[], canary={"executed_full": False}) == probe.EP_REGISTERED


def test_blocked_states_exit_two(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "system_state", lambda: {"packages": [], "os": {}})
    monkeypatch.setattr(probe, "_official_catalog", lambda: _official())
    monkeypatch.setattr(probe, "_legacy_projection", lambda: _legacy())
    monkeypatch.setattr(probe, "_ort_state", lambda dlls: _ort([]))
    monkeypatch.setattr(probe.windows_ml_ep_catalog, "scan_amdgpu_ep_dll",
                        lambda: {"dlls": []})
    out = tmp_path / "probe.json"
    rc = probe.main(["--out", str(out), "--gfx", "gfx1151"])
    assert rc == 2
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["state"] == probe.CATALOG_EMPTY
    assert report["verdict"] == "BLOCKED_EP"
    assert report["api_diagnostic"]["official"]["api_ok"] is True
    assert any(d["name"] == "python_windowsml" for d in report["system_dependencies"])


def test_registered_state_exits_zero_and_reports_readiness(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "system_state", lambda: {"packages": [], "os": {}})
    monkeypatch.setattr(probe, "_official_catalog", lambda: _official())
    monkeypatch.setattr(probe, "_legacy_projection", lambda: _legacy())
    monkeypatch.setattr(probe, "_ort_state",
                        lambda dlls: _ort([{"ep_name": "AMDGPUExecutionProvider"}]))
    monkeypatch.setattr(probe.windows_ml_ep_catalog, "scan_amdgpu_ep_dll",
                        lambda: {"dlls": []})
    out = tmp_path / "probe.json"
    assert probe.main(["--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["state"] == probe.EP_REGISTERED
    assert report["blocked_status"] is None


def test_error_codes_are_surfaced(tmp_path, monkeypatch):
    bad = _official(api_ok=False, installed=True)
    bad["errors"] = [{"stage": "catalog_enumerate", "hresult": "0x80070490",
                      "message": "OSError: element not found"}]
    monkeypatch.setattr(probe, "system_state", lambda: {"packages": [], "os": {}})
    monkeypatch.setattr(probe, "_official_catalog", lambda: bad)
    monkeypatch.setattr(probe, "_legacy_projection", lambda: _legacy())
    monkeypatch.setattr(probe, "_ort_state", lambda dlls: _ort([]))
    monkeypatch.setattr(probe.windows_ml_ep_catalog, "scan_amdgpu_ep_dll",
                        lambda: {"dlls": []})
    out = tmp_path / "probe.json"
    assert probe.main(["--out", str(out)]) == 2
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["state"] == probe.NOT_INSTALLED
    assert report["error_codes"][0]["hresult"] == "0x80070490"


# --- R10: verified reference facts, idempotency digest, --diff -------------


def _report(*, state=probe.CATALOG_EMPTY, providers=(), legacy_providers=(),
            create_hr="0x00000000", enum_hr="0x00000000", wm="2.5.96",
            ort_version="1.25.2", shared="WindowsWorkload.WinMLShared.7.0 1.2610.1050.0",
            driver="32.0.22018.5", build="26200", captured="2026-11-19T13:50:33Z",
            dlls=(), packages=(), ort_devices=("CPUExecutionProvider",),
            verdict="BLOCKED_EP"):
    """A minimal probe report with the same shape main() produces."""
    return {
        "schema_version": 3,
        "captured_utc": captured,
        "gfx_requested": "gfx1151",
        "state": state,
        "verdict": verdict,
        "blocked_status": "BLOCKED_EXTERNAL",
        "api_diagnostic": {
            "official": {"engine": "windowsml_flat_c", "installed": True,
                         "version": wm, "api_ok": True,
                         "providers": [dict(p) for p in providers],
                         "errors": [], "raw": {"create_hresult": create_hr,
                                               "enum_hresult": enum_hr,
                                               "provider_count": len(providers)}},
            "legacy_projection": {"engine": "wasdk_projection", "installed": True,
                                  "api_ok": True,
                                  "providers": [dict(p) for p in legacy_providers],
                                  "errors": [], "version": "2.3.0"},
        },
        "ep_readiness": {
            "catalog_providers": [dict(p) for p in providers],
            "legacy_projection_providers": [dict(p) for p in legacy_providers],
            "amd_gpu_ep_dlls": [dict(d) for d in dlls],
            "amd_gpu_ep_packages": [dict(p) for p in packages],
            "ort_providers": ["CPUExecutionProvider"],
            "ort_ep_devices": [{"ep_name": n} for n in ort_devices],
            "ort_registration": None,
            "canary": None,
        },
        "system": {"os": {"build": build},
                   "video": [{"Name": "AMD Radeon(TM) 8060S Graphics",
                              "DriverVersion": driver}],
                   "packages": [], "amd_ep_packages": [], "dlls": []},
        "system_dependencies": [
            {"name": "python_windowsml", "observed": wm, "status": "OK"},
            {"name": "onnxruntime_windowsml", "observed": ort_version, "status": "OK"},
            {"name": "windows_ml_shared_runtime", "observed": shared, "status": "OK"},
            {"name": "amd_gpu_ep_package_or_dll", "status": "MISSING"},
        ],
        "error_codes": [],
        "ep_reference": probe.EP_REFERENCE,
        "recommended_actions": [],
    }


def test_ep_reference_records_verified_official_requirements():
    amd = probe.EP_REFERENCE["AMDGPUExecutionProvider"]
    assert "2.5.96" in amd["min_windows_ml_version"]
    assert "24.12.1" in amd["documented_requirement"]
    assert "26100" in amd["documented_os"]
    assert amd["source"] == probe.EP_DOC_SOURCE
    assert amd["source_accessed_utc"].endswith("Z")
    assert len(amd["source_sha256"]) == 64
    assert int(amd["source_sha256"], 16) >= 0


def test_ep_reference_marks_migraphx_deprecated_for_2x():
    mgx = probe.EP_REFERENCE["MIGraphXExecutionProvider"]
    assert "Deprecated" in mgx["status"]
    assert "25.10.13.09" in mgx["documented_requirement"]
    # the driver minimum belongs to MIGraphX, never to AMDGPU
    assert "25.10.13.09" not in probe.EP_REFERENCE["AMDGPUExecutionProvider"][
        "documented_requirement"]


def test_ep_reference_keeps_the_superseded_r3d_claim_as_a_note():
    note = probe.EP_REFERENCE_SUPERSEDES
    assert "NOT listed" in note["superseded_claim"]
    assert "docs" in note["stale_source"]
    assert note["stale_source_ms_date"] == "2026-04-28"


def test_stable_digest_ignores_only_the_capture_timestamp():
    a = _report(captured="2026-11-19T13:50:33Z")
    b = _report(captured="2026-11-19T14:59:59Z")
    assert probe.stable_digest(a) == probe.stable_digest(b)
    c = _report(providers=[{"name": "AMDGPUExecutionProvider", "ready_state": 2}])
    assert probe.stable_digest(c) != probe.stable_digest(a)


def test_diff_reports_no_change_for_equal_observations():
    d = probe.diff_reports(_report(captured="2026-11-19T13:50:33Z"),
                           _report(captured="2026-11-19T21:00:00Z"))
    assert d["ok"] and d["identical"] is True
    assert d["catalog"]["added"] == [] and d["catalog"]["removed"] == []
    assert d["summary"] == ["no observable change (same state, catalog, HRESULTs and versions)"]


def test_diff_reports_new_provider_count_hresult_and_version_drift():
    prev = _report()
    cur = _report(providers=[{"name": "AMDGPUExecutionProvider", "ready_state": 2}],
                  create_hr="0x80070490", wm="2.5.99", driver="32.0.23000.1")
    d = probe.diff_reports(prev, cur)
    assert d["identical"] is False
    assert d["catalog"]["provider_count"] == {"previous": 0, "current": 1}
    assert d["catalog"]["added"] == ["AMDGPUExecutionProvider:2"]
    assert d["hresult"]["changed"] is True
    assert d["version_drift"]["amd_driver"] == ["32.0.22018.5", "32.0.23000.1"]
    assert d["version_drift"]["python_windowsml"] == ["2.5.96", "2.5.99"]
    joined = " | ".join(d["summary"])
    assert "catalog provider added: AMDGPUExecutionProvider:2" in joined
    assert "HRESULT change" in joined
    assert "version drift python_windowsml: 2.5.96 -> 2.5.99" in joined


def test_diff_reports_removed_provider_and_new_ort_device():
    prev = _report(providers=[{"name": "MIGraphXExecutionProvider", "ready_state": 2}])
    cur = _report(ort_devices=("CPUExecutionProvider", "AMDGPUExecutionProvider"))
    d = probe.diff_reports(prev, cur)
    assert d["catalog"]["removed"] == ["MIGraphXExecutionProvider:2"]
    assert d["ort"]["devices_added"] == ["AMDGPUExecutionProvider"]


def test_update_state_records_acquisition_channel_without_installing():
    pytest = __import__("pytest")
    pytest.importorskip("winreg")
    state = probe.update_state(windowsml_version="2.5.96")
    assert state["available"] is True
    assert state["ep_acquisition_channel"]["channel"] == "microsoft_store"
    assert state["ep_acquisition_channel"]["windows_update_pause_is_a_documented_cause"] is False
    old = probe.update_state(windowsml_version="2.3.0")
    assert old["ep_acquisition_channel"]["channel"] == "windows_update"
    assert old["ep_acquisition_channel"]["windows_update_pause_is_a_documented_cause"] is True
    assert "any_servicing_reboot_key" in state["pending_reboot"]


def test_main_records_update_state_digest_and_diff(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "system_state", lambda: {"packages": [], "os": {}})
    monkeypatch.setattr(probe, "_official_catalog", lambda: _official())
    monkeypatch.setattr(probe, "_legacy_projection", lambda: _legacy())
    monkeypatch.setattr(probe, "_ort_state", lambda dlls: _ort([]))
    monkeypatch.setattr(probe.windows_ml_ep_catalog, "scan_amdgpu_ep_dll",
                        lambda: {"dlls": []})
    first = tmp_path / "first.json"
    assert probe.main(["--out", str(first), "--gfx", "gfx1151"]) == 2
    out = tmp_path / "second.json"
    assert probe.main(["--out", str(out), "--gfx", "gfx1151",
                       "--diff", str(first)]) == 2
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["schema_version"] == 3
    assert report["stable_digest"] == probe.stable_digest(report)
    assert report["diff_vs_previous"]["ok"] is True
    assert report["diff_vs_previous"]["identical"] is True
    assert report["diff_vs_previous"]["summary"] == [
        "no observable change (same state, catalog, HRESULTs and versions)"]
    assert "update_state" in report
    assert report["ep_reference_sources"]["published_page"] == probe.EP_DOC_SOURCE


def test_unreadable_diff_input_never_changes_the_blocked_exit_code(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "system_state", lambda: {"packages": [], "os": {}})
    monkeypatch.setattr(probe, "_official_catalog", lambda: _official())
    monkeypatch.setattr(probe, "_legacy_projection", lambda: _legacy())
    monkeypatch.setattr(probe, "_ort_state", lambda dlls: _ort([]))
    monkeypatch.setattr(probe.windows_ml_ep_catalog, "scan_amdgpu_ep_dll",
                        lambda: {"dlls": []})
    out = tmp_path / "probe.json"
    rc = probe.main(["--out", str(out), "--diff", str(tmp_path / "missing.json")])
    assert rc == 2
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["state"] == probe.CATALOG_EMPTY
    assert report["diff_vs_previous"]["ok"] is False
    assert "FileNotFoundError" in report["diff_vs_previous"]["error"]
