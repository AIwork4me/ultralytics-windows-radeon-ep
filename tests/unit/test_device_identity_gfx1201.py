"""gfx1201 (RX 9070 XT / Navi 48) device-identity gating — RED-FIRST tests for
the B0 mapping defect (mission WINDOWS-RX9070XT-OVERNIGHT).

Defects encoded here (must FAIL on the pre-fix code and PASS after):
  1. KNOWN_AMD_DEVICE_IDS mislabels 0x7480 as gfx1201 (it is not a Navi 48 ID;
     pci.ids: 1002:7550 = Navi 48 [Radeon RX 9070/9070 XT/9070 GRE]).
  2. resolve_target_adapter selects ANY non-software AMD adapter for non-gfx1151
     targets — a gfx=gfx1201 request silently succeeds on a gfx1100-only host.

Sources: pci.ids database (1002:7550 Navi 48 family; 1002:7448/744C Navi 31;
1002:1586 Radeon 8060S per dxdiag on the reference host). 0x7551 is included as
a reported Navi 48 variant, hardware-unverified — the resolver never grants
PHYSICAL_BINDING_CONFIRMED from a table entry alone.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from radeon_ort.device_identity import (  # noqa: E402
    AMD_VENDOR_ID, DxgiAdapter, KNOWN_AMD_DEVICE_IDS, resolve_target_adapter,
)


def _adapter(**kw):
    base = dict(index=0, description="AMD Radeon RX 7900 XTX", vendor_id=AMD_VENDOR_ID,
                device_id=0x744C, sub_sys_id=0, revision=0, dedicated_video_mb=24564,
                dedicated_system_mb=0, shared_sys_mb=32768, luid_high=0, luid_low=72346)
    base.update(kw)
    return DxgiAdapter(**base)


def _xtx_host():
    """This mission host's DXGI shape: one physical RX 7900 XTX (gfx1100)."""
    return [_adapter()]


def _xt_host():
    return [_adapter(device_id=0x7448, description="AMD Radeon RX 7900 XT")]


def _navi48_host(device_id=0x7550):
    return [_adapter(device_id=device_id,
                     description="AMD Radeon RX 9070 XT")]


# --- table correctness -------------------------------------------------------

def test_7480_is_not_labeled_gfx1201():
    label = KNOWN_AMD_DEVICE_IDS.get(0x7480, "")
    assert "gfx1201" not in label, (
        f"0x7480 must not claim gfx1201 (got {label!r}); Navi 48 is 0x7550/0x7551")


def test_navi48_ids_in_table():
    for did in (0x7550, 0x7551):
        assert did in KNOWN_AMD_DEVICE_IDS, hex(did)
        assert "gfx1201" in KNOWN_AMD_DEVICE_IDS[did]


def test_navi31_ids_in_table():
    for did in (0x7448, 0x744C):
        assert did in KNOWN_AMD_DEVICE_IDS
        assert "gfx1100" in KNOWN_AMD_DEVICE_IDS[did]


# --- gfx1201 selection on wrong hardware must fail closed --------------------

def test_gfx1201_request_on_gfx1100_host_fails():
    res = resolve_target_adapter(_xtx_host(), gfx="gfx1201")
    assert not res["ok"], res
    assert "WRONG_OR_AMBIGUOUS_DEVICE" in res["reason"]
    # the failure must NAME what was actually found (visible, not silent)
    assert "744C" in res["reason"] or "gfx1100" in res["reason"]


def test_gfx1201_request_on_xt_host_fails():
    res = resolve_target_adapter(_xt_host(), gfx="gfx1201")
    assert not res["ok"]


def test_gfx1201_resolves_on_navi48_host():
    for did in (0x7550, 0x7551):
        res = resolve_target_adapter(_navi48_host(did), gfx="gfx1201")
        assert res["ok"], res.get("reason")
        assert res["adapter"].device_id == did
        assert res.get("did_match") is True


# --- gfx1100 must no longer blanket-select every AMD adapter -----------------

def test_gfx1100_resolves_by_exact_did_on_xtx_host():
    res = resolve_target_adapter(_xtx_host(), gfx="gfx1100")
    assert res["ok"], res.get("reason")
    assert res["adapter"].device_id == 0x744C
    assert res.get("did_match") is True


def test_gfx1100_request_on_navi48_host_fails():
    res = resolve_target_adapter(_navi48_host(), gfx="gfx1100")
    assert not res["ok"]
    assert "7550" in res["reason"] or "gfx1201" in res["reason"]


# --- multi-AMD-host determinism ----------------------------------------------

def test_dual_amd_dgpu_host_pins_by_did():
    host = [_adapter(index=0),
            _adapter(index=1, device_id=0x7550,
                     description="AMD Radeon RX 9070 XT", luid_low=999)]
    res1201 = resolve_target_adapter(host, gfx="gfx1201")
    assert res1201["ok"] and res1201["adapter"].index == 1
    res1100 = resolve_target_adapter(host, gfx="gfx1100")
    assert res1100["ok"] and res1100["adapter"].index == 0


def test_unknown_gfx_target_fails_with_supported_list():
    res = resolve_target_adapter(_xtx_host(), gfx="gfx9999")
    assert not res["ok"]
    assert "gfx1100" in res["reason"] and "gfx1201" in res["reason"]


def test_software_amd_excluded_from_gfx1201():
    res = resolve_target_adapter(
        [_adapter(is_software=True, device_id=0x7550,
                  description="AMD Radeon RX 9070 XT")], gfx="gfx1201")
    assert not res["ok"]


def test_duplicate_same_silicon_enumeration_dedup_still_ok():
    res = resolve_target_adapter(
        [_adapter(index=0), _adapter(index=1, luid_low=118045)], gfx="gfx1100")
    assert res["ok"]
    assert res["adapter"].index == 0
    assert res.get("dedup_note")


def test_zero_luid_flagged_not_silent():
    res = resolve_target_adapter(
        [_adapter(luid_high=0, luid_low=0)], gfx="gfx1100")
    assert res["ok"]
    assert res.get("luid_valid") is False


def test_gfx1151_name_fallback_still_works():
    res = resolve_target_adapter(
        [_adapter(device_id=0x9999,
                  description="AMD Radeon(TM) 8060S Graphics")], gfx="gfx1151",
        allow_unverified_name_fallback=True)
    assert res["ok"]
    assert res.get("did_match") is False


def test_gfx1151_generic_radeon_needle_must_not_match_other_cards():
    # the generic 'Radeon' needle must NOT let a gfx1151 request silently
    # select a 7900 XTX (or any non-8060S) adapter — live-discovered residual
    # hole on the physical gfx1100 host, 2026-11-18
    res = resolve_target_adapter(
        [_adapter(device_id=0x744C, description="AMD Radeon RX 7900 XTX")],
        gfx="gfx1151")
    assert not res["ok"], res


def test_name_only_8060s_without_known_did_is_blocked_by_default():
    # A device name can be spoofed; a gfx1151 GPU_FULL claim must be DID-pinned.
    res = resolve_target_adapter(
        [_adapter(device_id=0x9999,
                  description="AMD Radeon(TM) 8060S Graphics")], gfx="gfx1151")
    assert not res["ok"] and "WRONG_OR_AMBIGUOUS_DEVICE" in res["reason"]
