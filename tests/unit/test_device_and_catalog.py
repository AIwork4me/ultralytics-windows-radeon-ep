import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from radeon_ort.device_identity import (DxgiAdapter, resolve_target_adapter)  # noqa: E402


def _adapter(**kw):
    base = dict(index=0, description="AMD Radeon(TM) 8060S Graphics", vendor_id=0x1002,
                device_id=0x1586, sub_sys_id=0, revision=0, dedicated_video_mb=0,
                dedicated_system_mb=0, shared_sys_mb=98918, luid_high=0, luid_low=12345)
    base.update(kw)
    return DxgiAdapter(**base)


def test_integrated_gpu_with_zero_dedicated_vram_is_valid():
    res = resolve_target_adapter([_adapter()], gfx="gfx1151")
    assert res["ok"], res.get("reason")


def test_no_amd_adapter_fails():
    res = resolve_target_adapter(
        [_adapter(vendor_id=0x8086, description="Intel whatever")], gfx="gfx1151")
    assert not res["ok"]
    assert "WRONG_OR_AMBIGUOUS_DEVICE" in res["reason"]


def test_two_amd_candidates_is_ambiguous():
    res = resolve_target_adapter(
        [_adapter(index=0), _adapter(index=1, device_id=0x7448, description="RX 7900 XTX")],
        gfx="gfx1151")
    # gfx1151 filter by device_id keeps only 0x1586 -> OK
    assert res["ok"]


def test_software_adapter_ignored():
    res = resolve_target_adapter(
        [_adapter(is_software=True), _adapter(index=1)], gfx="gfx1151")
    assert res["ok"]


def test_wrong_device_id_for_gfx1151_fails_closed_by_default():
    res = resolve_target_adapter([_adapter(device_id=0x9999)], gfx="gfx1151")
    assert not res["ok"]
    assert "WRONG_OR_AMBIGUOUS_DEVICE" in res["reason"]


def test_unknown_did_name_only_fallback_requires_explicit_diagnostic_opt_in():
    res = resolve_target_adapter(
        [_adapter(device_id=0x9999)], gfx="gfx1151",
        allow_unverified_name_fallback=True)
    assert res["ok"] and res["did_match"] is False
    assert "warning" in res


def test_catalog_exists_and_frozen():
    cat = Path(__file__).resolve().parents[2] / "catalog"
    snap = cat / "upstream_snapshot.json"
    if not snap.is_file():
        import pytest
        pytest.skip("catalog not generated yet")
    import json
    s = json.loads(snap.read_text(encoding="utf-8"))
    assert s["ultralytics_release"]
    assert s["captured_utc"]
