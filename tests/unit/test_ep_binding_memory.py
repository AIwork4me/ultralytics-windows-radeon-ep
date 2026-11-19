"""P1-A/P1-B regressions: EP-reported identity vs indirect DXGI evidence and
GPU memory provenance. A host-side DXGI LUID must never be promoted to an
EP-reported LUID, and WMI AdapterRAM must never be read as VRAM capacity.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers_receipts import make_receipt_v2  # noqa: E402
from radeon_ort.device_identity import (DxgiAdapter, infer_gfx_from_adapters,  # noqa: E402
                                        memory_provenance, resolve_target_adapter)
from radeon_ort.receipts import (combine_v2_to_status, derive_verdicts,  # noqa: E402
                                 validate_receipt_strict)


def _adapter(**kw):
    base = dict(index=0, description="AMD Radeon(TM) 8060S Graphics",
                vendor_id=0x1002, device_id=0x1586, sub_sys_id=0, revision=0,
                dedicated_video_mb=32589, dedicated_system_mb=0,
                shared_sys_mb=78459, luid_high=0, luid_low=0x154E4)
    base.update(kw)
    return DxgiAdapter(**base)


def test_gfx_inference_is_did_based_not_name_based():
    assert infer_gfx_from_adapters([_adapter()])["gfx"] == "gfx1151"
    assert infer_gfx_from_adapters([_adapter(device_id=0x744C,
                                             description="RX 7900 XTX")])["gfx"] == "gfx1100"
    assert infer_gfx_from_adapters([_adapter(device_id=0x7550,
                                             description="RX 9070")])["gfx"] == "gfx1201"
    bad = infer_gfx_from_adapters([_adapter(device_id=0x9999,
                                            description="Fake Radeon 8060S")])
    assert not bad["ok"] and "UNSUPPORTED_GFX_TARGET" in bad["reason"]


def test_ambiguous_gfx_fails_closed():
    res = infer_gfx_from_adapters([
        _adapter(index=0, device_id=0x1586),
        _adapter(index=1, device_id=0x744C, description="RX 7900 XTX")])
    assert not res["ok"] and "AMBIGUOUS_GFX_TARGET" in res["reason"]


def test_gfx1201_never_silently_selects_gfx1151_adapter():
    res = resolve_target_adapter([_adapter(device_id=0x1586)], gfx="gfx1201")
    assert not res["ok"]
    assert "WRONG_OR_AMBIGUOUS_DEVICE" in res["reason"]


def test_dxgi_only_corroboration_cannot_be_physical_identity():
    r = make_receipt_v2(ep="dml", ep_nodes=1)
    r["device_binding"]["state"] = "PHYSICAL_GPU_IDENTITY_CORROBORATED"
    r["device_binding"].setdefault("dxgi_corroboration", {})["luid"] = "0x0:0x11746"
    v = derive_verdicts(r)
    assert v["identity"] == "IDENTITY_BINDING_UNPROVEN"
    # The forged label (status still FULL) must be rejected, not silently
    # downgraded into an honest receipt by the test.
    r["verdicts"] = v
    assert not validate_receipt_strict(r)["ok"]


def test_ep_reported_luid_can_corroborate_when_it_matches_dxgi():
    r = make_receipt_v2(ep="dml", ep_nodes=1)
    r["device_binding"]["state"] = "PHYSICAL_GPU_IDENTITY_CORROBORATED"
    r["device_binding"]["dxgi_corroboration"] = {"luid": "0x0:0x11746"}
    r["device_binding"]["ep_reported_identity"] = {
        "source": "fake-ep-metadata", "exposes_luid": True,
        "luid": "0x0:0x11746", "reported": {}}
    v = derive_verdicts(r)
    assert v["identity"] == "PHYSICAL_GPU_IDENTITY_CORROBORATED"
    r["verdicts"] = v
    for ep in ("amdgpu", "dml"):
        if r["status"][ep] != "NOT_RUN":
            r["status"][ep] = combine_v2_to_status(ep, v[ep])
    assert validate_receipt_strict(r)["ok"]


def test_memory_sources_are_labelled_and_wmi_saturation_is_flagged():
    a = _adapter()
    m = memory_provenance(a, wmi_adapter_ram_bytes=4293918720)
    assert m["values"]["dedicated_video_mb"] == 32589
    assert m["values"]["shared_system_mb"] == 78459
    assert m["wmi_adapter_ram_saturated"] is True
    assert "uint32" in m["sources"]["wmi_adapter_ram_bytes"]["meaning"]
    assert "NOT independent physical VRAM" in m["sources"]["dedicated_video_mb"]["meaning"]
    assert m["measurement_status"].startswith("DXGI_STATIC_ENUMERATION_ONLY")
    # The three numbers are different measurement calibers, never aliases.
    assert m["values"]["dedicated_video_mb"] != m["values"]["shared_system_mb"]


def test_host_status_label_follows_live_gfx_not_a_hardcoded_arch():
    from runner.r1_pipeline import _host_status
    for gfx in ("gfx1100", "gfx1151", "gfx1201"):
        assert _host_status({"gfx": gfx, "verified_device_identity": True}) == \
            "VERIFIED_" + gfx.upper()
    assert _host_status({"gfx": "gfx1201", "verified_device_identity": False}) == "BLOCKED_ENV"
    assert _host_status({"gfx": "unknown", "verified_device_identity": True}) == "BLOCKED_ENV"
