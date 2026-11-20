"""Mission C: fail-closed AMDGPU EP device selection + harness input contract.

CPU-only. No ONNX Runtime, no GPU, no Windows ML: `onnxruntime` is replaced by
a stub module and `_ensure_amdgpu_registered` by a no-op, so these tests run on
the CPU CI runner and pin the *decision logic* that Experiment A showed was
broken (the `luid_pin_fail` case returned a different physical GPU).

Defect being pinned (src/radeon_ort/rca2a_ep.py, pre-fix):
    select_amdgpu_ep_device(target_luid=999999) on a host with EP devices whose
    metadata LUIDs were 111/222 returned a device anyway, with basis
    "Discrete=1 unique among AMDGPU EP devices (no LUID given)" — i.e. a pinned
    request silently fell back to another adapter *and* misreported the basis.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from radeon_ort import rca2a_ep
from radeon_ort.input_contract import validate_case_input_source


class _Inner:
    """Stands in for the ORT `device` attribute that carries `.metadata`."""

    def __init__(self, metadata: dict) -> None:
        self.metadata = metadata


class _EpDevice:
    def __init__(self, metadata: dict, *, ep_name: str = "AMDGPUExecutionProvider") -> None:
        self.ep_name = ep_name
        self.device = _Inner(metadata)


def _install(monkeypatch, devices: list) -> None:
    stub = types.ModuleType("onnxruntime")
    stub.get_ep_devices = lambda: devices  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "onnxruntime", stub)
    monkeypatch.setattr(rca2a_ep, "_ensure_amdgpu_registered", lambda: None)


def _two_amd(monkeypatch) -> None:
    """Mirror the live gfx1100 shape: two EP devices, one discrete."""
    _install(monkeypatch, [
        _EpDevice({"LUID": "111", "Discrete": "1"}),
        _EpDevice({"LUID": "222"}),
    ])


# --------------------------------------------------------------------------
# pinned selection: exactly one match, else FAIL CLOSED
# --------------------------------------------------------------------------

def test_explicit_luid_unique_match_is_selected(monkeypatch):
    _two_amd(monkeypatch)
    dev, sel = rca2a_ep.select_amdgpu_ep_device(222)
    assert dev is not None
    assert sel["basis"] == "LUID match with physical adapter"
    assert sel["luid"] == "222"
    assert dev.device.metadata["LUID"] == "222"


def test_explicit_luid_no_match_fails_closed(monkeypatch):
    """The P0: a pinned LUID that matches nothing must NOT select another GPU."""
    _two_amd(monkeypatch)
    dev, sel = rca2a_ep.select_amdgpu_ep_device(999999)
    assert dev is None, "pinned request must not fall back to a different adapter"
    assert sel["basis"].startswith("FAIL_CLOSED")
    assert sel["matched"] == 0
    assert sel["luid"] == "999999"
    # the pre-fix basis lied about the caller not supplying a LUID
    assert "no LUID given" not in sel["basis"]


def test_explicit_luid_matching_the_non_discrete_device_is_honoured(monkeypatch):
    """A pinned LUID wins even when it is not the Discrete=1 candidate."""
    _two_amd(monkeypatch)
    dev, sel = rca2a_ep.select_amdgpu_ep_device(222)
    assert dev is not None and dev.device.metadata.get("Discrete") != "1"
    assert sel["luid"] == "222"


def test_explicit_luid_multiple_matches_fails_closed(monkeypatch):
    """'zero-or-one' means ambiguous duplicate LUIDs are refused too."""
    _install(monkeypatch, [
        _EpDevice({"LUID": "777", "Discrete": "1"}),
        _EpDevice({"LUID": "777"}),
    ])
    dev, sel = rca2a_ep.select_amdgpu_ep_device(777)
    assert dev is None
    assert sel["matched"] == 2
    assert sel["basis"].startswith("FAIL_CLOSED")


def test_explicit_luid_accepts_int_and_str_forms(monkeypatch):
    _two_amd(monkeypatch)
    for pinned in (111, "111"):
        dev, sel = rca2a_ep.select_amdgpu_ep_device(pinned)
        assert dev is not None and sel["luid"] == "111"


def test_no_ep_devices_is_reported_not_raised(monkeypatch):
    _install(monkeypatch, [])
    dev, sel = rca2a_ep.select_amdgpu_ep_device(123)
    assert dev is None
    assert sel["basis"] == "no AMDGPU EP device registered"


def test_non_amdgpu_devices_are_ignored(monkeypatch):
    _install(monkeypatch, [
        _EpDevice({"LUID": "1"}, ep_name="DmlExecutionProvider"),
        _EpDevice({"LUID": "2", "Discrete": "1"}),
    ])
    dev, sel = rca2a_ep.select_amdgpu_ep_device(2)
    assert dev is not None
    assert sel["basis"] == "LUID match with physical adapter"
    assert sel["luid"] == "2"
    assert all("DML" not in str(c) for c in sel["candidates"])


# --------------------------------------------------------------------------
# unpinned selection keeps its documented, attested preference
# --------------------------------------------------------------------------

def test_unpinned_prefers_unique_discrete(monkeypatch):
    _two_amd(monkeypatch)
    dev, sel = rca2a_ep.select_amdgpu_ep_device()
    assert dev is not None
    assert dev.device.metadata["Discrete"] == "1"
    assert "Discrete=1 unique" in sel["basis"]


def test_unpinned_ambiguity_is_flagged(monkeypatch):
    _install(monkeypatch, [_EpDevice({"LUID": "1"}), _EpDevice({"LUID": "2"})])
    dev, sel = rca2a_ep.select_amdgpu_ep_device()
    assert dev is not None
    assert sel["basis"].startswith("AMBIGUOUS")


# --------------------------------------------------------------------------
# harness input-source contract (worker exit code 36, never a GPU failure)
# --------------------------------------------------------------------------

def _touch(tmp_path: Path, name: str) -> str:
    p = tmp_path / name
    p.write_bytes(b"x")
    return str(p)


def test_contract_requires_exactly_one_input_source(tmp_path):
    feeds = _touch(tmp_path, "feeds.npz")
    manifest = _touch(tmp_path, "manifest.json")

    assert validate_case_input_source({"feeds_npz": feeds}) is None
    assert validate_case_input_source({"input_manifest": manifest}) is None

    both = validate_case_input_source({"feeds_npz": feeds, "input_manifest": manifest})
    assert both is not None and "exactly one" in both

    neither = validate_case_input_source({})
    assert neither is not None and "declared=none" in neither


def test_contract_rejects_missing_bundle_files(tmp_path):
    missing = str(tmp_path / "does_not_exist.json")
    err = validate_case_input_source({"input_manifest": missing})
    assert err is not None and "does not exist" in err

    err = validate_case_input_source({"feeds_npz": missing})
    assert err is not None and "does not exist" in err


def test_contract_rejects_non_mapping():
    err = validate_case_input_source("not-a-case")  # type: ignore[arg-type]
    assert err is not None and "JSON object" in err


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
