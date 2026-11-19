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
