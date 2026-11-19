"""Synthetic cross-architecture boundaries for Missions R8 + R9 (CPU-only).

These tests never touch a GPU. They build ``DxgiAdapter`` objects and receipts to
freeze the fail-closed rules that make a gfx1100/gfx1201 handoff honest:

1. the authoritative DID table (gfx1100 = 0x7448/0x744C, gfx1201 = 0x7550/0x7551)
   and positive DID resolution for both simulated hosts;
2. wrong-target refusal (requesting gfx1100 on a gfx1151/gfx1201 host and the
   symmetric gfx1201 cases);
3. ambiguity: two supported gfx, or two distinct cards of the same gfx, never
   select one silently;
4. missing EP / wrong provider / unauthorized CPU fallback refusal;
5. cross-arch cache/receipt/contract pollution is refused with a named reason;
6. forged gfx/DID/status receipt identity is rejected.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from helpers_receipts import make_receipt_v2

from radeon_ort import contract as contract_mod
from radeon_ort import cross_host
from radeon_ort.device_identity import (
    AMD_VENDOR_ID,
    GFX_DEVICE_IDS,
    DxgiAdapter,
    infer_gfx_from_adapters,
    resolve_target_adapter,
)
from radeon_ort.receipts import validate_receipt_strict
from radeon_ort.unified_runtime import (
    ContractError,
    validate_args,
    verify_run_result,
)

# --- synthetic adapters ---------------------------------------------------------


def _adapter(**kw) -> DxgiAdapter:
    base = {
        "index": 0,
        "description": "AMD Radeon(TM) 8060S Graphics",
        "vendor_id": AMD_VENDOR_ID,
        "device_id": 0x1586,
        "sub_sys_id": 0,
        "revision": 0,
        "dedicated_video_mb": 0,
        "dedicated_system_mb": 0,
        "shared_sys_mb": 98304,
        "luid_high": 0,
        "luid_low": 0x154E4,
    }
    base.update(kw)
    return DxgiAdapter(**base)


def _host_gfx1151() -> list[DxgiAdapter]:
    return [_adapter()]


def _host_gfx1100(did: int = 0x7448) -> list[DxgiAdapter]:
    return [_adapter(index=0, device_id=did, luid_low=0x12000,
                     description="AMD Radeon RX 7900 XTX" if did == 0x744C
                     else "AMD Radeon RX 7900 XT")]


def _host_gfx1201(did: int = 0x7550) -> list[DxgiAdapter]:
    return [_adapter(index=0, device_id=did, luid_low=0x22000,
                     description="AMD Radeon RX 9070 XT")]


# --- 1. DID table + positive resolution ----------------------------------------


def test_authoritative_did_table_is_not_copied_across_gfx():
    assert GFX_DEVICE_IDS["gfx1100"] == {0x7448, 0x744C}
    assert GFX_DEVICE_IDS["gfx1151"] == {0x1586}
    assert GFX_DEVICE_IDS["gfx1201"] == {0x7550, 0x7551}
    # the R9-A copy-paste bug: 0x7448/0x744C are NOT gfx1201
    assert not (GFX_DEVICE_IDS["gfx1201"] & {0x7448, 0x744C})
    assert not (GFX_DEVICE_IDS["gfx1100"] & {0x7550, 0x7551})


@pytest.mark.parametrize("did", [0x7448, 0x744C])
def test_simulated_gfx1100_did_resolves_positively(did):
    resolved = resolve_target_adapter(_host_gfx1100(did), gfx="gfx1100")
    assert resolved["ok"], resolved.get("reason")
    assert resolved["did_match"] is True
    assert resolved["adapter"].device_id == did
    gate = cross_host.gate_physical_target(_host_gfx1100(did), "gfx1100")
    assert gate["status"] == "PASS"
    assert gate["reason"] is None
    assert gate["selected_device_id"] == f"0x{did:04X}"


@pytest.mark.parametrize("did", [0x7550, 0x7551])
def test_simulated_gfx1201_did_resolves_positively(did):
    resolved = resolve_target_adapter(_host_gfx1201(did), gfx="gfx1201")
    assert resolved["ok"], resolved.get("reason")
    assert resolved["did_match"] is True
    assert resolved["adapter"].device_id == did
    gate = cross_host.gate_physical_target(_host_gfx1201(did), "gfx1201")
    assert gate["status"] == "PASS"
    assert gate["selected_device_id"] == f"0x{did:04X}"


# --- 2. wrong-target refusal ----------------------------------------------------


def test_requesting_gfx1100_on_other_hosts_fails_closed():
    for host in (_host_gfx1151(), _host_gfx1201()):
        resolved = resolve_target_adapter(host, gfx="gfx1100")
        assert not resolved["ok"]
        assert "WRONG_OR_AMBIGUOUS_DEVICE" in resolved["reason"]
        gate = cross_host.gate_physical_target(host, "gfx1100")
        assert gate["status"] == "BLOCKED"
        assert gate["reason"] == cross_host.WRONG_PHYSICAL_DEVICE
        assert gate["selected_device_id"] is None


def test_requesting_gfx1201_on_other_hosts_fails_closed():
    for host in (_host_gfx1151(), _host_gfx1100()):
        resolved = resolve_target_adapter(host, gfx="gfx1201")
        assert not resolved["ok"]
        gate = cross_host.gate_physical_target(host, "gfx1201")
        assert gate["status"] == "BLOCKED"
        assert gate["reason"] == cross_host.WRONG_PHYSICAL_DEVICE
        assert gate["selected_device_id"] is None


# --- 3. ambiguity ---------------------------------------------------------------


def test_two_supported_gfx_present_is_ambiguous_and_never_picked():
    adapters = [_adapter(), _adapter(index=1, device_id=0x7550, luid_low=0x99,
                                     description="AMD Radeon RX 9070 XT")]
    inferred = infer_gfx_from_adapters(adapters)
    assert not inferred["ok"]
    assert "AMBIGUOUS_GFX_TARGET" in inferred["reason"]
    for requested in ("gfx1151", "gfx1201"):
        gate = cross_host.gate_physical_target(adapters, requested)
        assert gate["status"] == "BLOCKED"
        assert gate["reason"] == cross_host.AMBIGUOUS_GFX_TARGET
        assert gate["selected_device_id"] is None
        assert gate["selected_luid"] is None


def test_two_distinct_cards_of_same_gfx_are_ambiguous():
    adapters = [
        _adapter(device_id=0x7448, description="AMD Radeon RX 7900 XT", luid_low=0x11),
        _adapter(index=1, device_id=0x744C, description="AMD Radeon RX 7900 XTX",
                 luid_low=0x22),
    ]
    gate = cross_host.gate_physical_target(adapters, "gfx1100")
    assert gate["status"] == "BLOCKED"
    assert gate["reason"] == cross_host.AMBIGUOUS_GFX_TARGET
    assert gate["selected_device_id"] is None


def test_no_amd_or_software_amd_is_unsupported():
    intel = [_adapter(vendor_id=0x8086, device_id=0x46A6, description="Intel Iris Xe")]
    assert cross_host.gate_physical_target(intel, "gfx1100")["reason"] == (
        cross_host.UNSUPPORTED_GFX_TARGET)
    software = [_adapter(is_software=True)]
    assert cross_host.gate_physical_target(software, "gfx1100")["reason"] == (
        cross_host.UNSUPPORTED_GFX_TARGET)


# --- 4. EP / provider / CPU fallback refusal -----------------------------------


def test_missing_ep_is_blocked_without_cpu_fallback():
    for policy in ("gpu-first", "amdgpu-only", "dml-only"):
        reason = cross_host.ep_readiness_refusal(
            policy, amdgpu_ready=False, dml_ready=False)
        assert reason is not None
        assert reason.startswith("BLOCKED_EP")
    assert cross_host.ep_readiness_refusal(
        "dml-only", amdgpu_ready=False, dml_ready=True) is None
    amdgpu_only = cross_host.ep_readiness_refusal(
        "amdgpu-only", amdgpu_ready=False, dml_ready=True,
        amdgpu_state="NOT_INSTALLED")
    assert "no DML/CPU fallback" in amdgpu_only


def test_canary_with_wrong_provider_or_fake_pass_label_is_refused():
    assert cross_host.canary_stage_status({
        "status": "GPU_PASS_VALIDATED",
        "profile": {"target_ep_nodes": 1, "cpu_nodes": 0},
        "parity": {"allclose": True},
    })[0] == "PASS"
    fake_pass = cross_host.canary_stage_status({
        "status": "GPU_PASS_VALIDATED",
        "profile": {"target_ep_nodes": 0, "cpu_nodes": 0},
        "parity": {"allclose": True},
    })
    assert fake_pass[0] == "FAIL"
    assert "WITHOUT_GPU_EVIDENCE" in fake_pass[1]
    wrong_provider = cross_host.canary_stage_status({
        "status": "FAIL_RUNTIME",
        "reason": "AMDGPUExecutionProvider not found",
    })
    assert wrong_provider[0] == "BLOCKED"
    silent_cpu = cross_host.canary_stage_status({
        "status": "FAIL_RUNTIME",
        "reason": "silent CPU fallback blocked (providers=[CPUExecutionProvider])",
    })
    assert silent_cpu[0] == "FAIL"


def test_unauthorized_cpu_arguments_are_contract_errors(tmp_path):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"not-a-real-model")
    base = {"model_path": model, "inputs": {"images": 1}, "target_gfx": "gfx1100"}
    with pytest.raises(ContractError, match="allow_cpu=True is only valid"):
        validate_args(policy="require_amdgpu", allow_cpu=True, **base)
    with pytest.raises(ContractError, match="cpu_opt_in"):
        validate_args(policy="cpu_opt_in", allow_cpu=False, **base)
    with pytest.raises(ContractError, match="UNSUPPORTED_GFX_TARGET"):
        validate_args(policy="require_amdgpu", allow_cpu=False,
                      model_path=model, inputs={"images": 1}, target_gfx="gfx9999")


def _valid_gpu_result() -> dict:
    attempt = {
        "attempt_id": "att-1",
        "ep": "amdgpu",
        "ok": True,
        "fragment_matches_attempt": True,
        "outputs_sha256": "d" * 64,
        "process": {"survivors": []},
        "input_binding": {"ok": True, "actual_sha256": "e" * 64},
        "device_binding": {"dxgi_corroboration": {"device_id": "0x7448",
                                                  "did_match": True}},
    }
    return {
        "run_id": "GFX1100_UNIT",
        "requested_policy": "prefer_amdgpu_allow_dml",
        "actual_ep": "amdgpu",
        "attempted_eps": ["amdgpu"],
        "physical_gpu": {"device_id": "0x7448", "vendor_id": "0x1002",
                         "did_match": True},
        "device_binding_confidence": "PHYSICAL_BINDING_CONFIRMED",
        "model_sha256": "a" * 64,
        "input_sha256": "b" * 64,
        "input_fingerprint": {},
        "target_gfx": "gfx1100",
        "target_ep_profile_nodes": 1,
        "cpu_profile_nodes": 0,
        "numeric_parity": {
            "by_name_matching": True,
            "all_outputs_allclose": True,
            "outputs": [{"name": "y", "allclose": True, "finite": True,
                         "shape": [1], "dtype": "float32"}],
        },
        "task_parity": {"status": "NOT_RUN"},
        "task_verified": False,
        "fallback_reason": None,
        "runtime_status": "GPU_FULL",
        "code_provenance": {},
        "receipt_sha256": "c" * 64,
        "gpu_full": True,
        "attempts": [attempt],
        "adopted_attempt_id": "att-1",
        "adopted_attempt_index": 0,
        "adopted_outputs_sha256": "d" * 64,
        "feed_fingerprint_sha256": "e" * 64,
        "readiness": {},
        "evidence_grade": "EVIDENCE",
        "cpu_reference": {"used": False},
        "cpu_user_authorized": False,
    }


def test_gpu_claim_with_cpu_nodes_or_wrong_binding_is_invalid():
    base = _valid_gpu_result()
    assert verify_run_result(base)["verdict"] == "RUNRESULT_VALID"
    assert verify_run_result({**base, "cpu_profile_nodes": 1})["verdict"] == (
        "RUNRESULT_INVALID")
    assert verify_run_result({**base, "device_binding_confidence": "UNPROVEN"})[
        "verdict"] == "RUNRESULT_INVALID"
    cpu = {**base, "runtime_status": "CPU_ONLY_USER_AUTHORIZED", "actual_ep": "cpu",
           "gpu_full": False, "cpu_user_authorized": False}
    verdict = verify_run_result(cpu)["verdict"]
    assert verdict == "RUNRESULT_INVALID"
    checks = {c["check"]: c["ok"] for c in verify_run_result(cpu)["checks"]}
    assert checks["cpu_consent_true"] is False


# --- 5. cross-arch receipt / contract pollution --------------------------------

TH = {"numeric_parity": {"fp32": {"atol": 5e-3, "rtol": 5e-3}},
      "task_oracle": {"detection": {"iou_thres": 0.5, "min_matches": 1}}}
CODE = {"git_head_sha": "a" * 40, "worktree_dirty": False,
        "harness_sha256": "b" * 64}
ENV = {"python_executable_name": "python.exe", "python_version": "3.12.10",
       "os_build": "10.0.26200", "ort_version": "1.25.2",
       "ort_distribution": "onnxruntime-windowsml",
       "ort_providers": ["DmlExecutionProvider"], "windowsml_version": "2.5.96",
       "probe_error": None}
MODELS = [{"checkpoint": "yolo26n.pt", "task": "detect", "imgsz": 640,
           "pt_sha256": "1" * 64, "onnx_sha256": "2" * 64,
           "asset_available": True}]


def _contract(gfx: str = "gfx1100", device_id: str = "0x7448",
              name: str = "AMD Radeon RX 7900 XTX", run_id: str = "RUN_X") -> dict:
    identity = {
        "gfx": gfx,
        "host_id": "host_test",
        "vendor_id": "0x1002",
        "device_id": device_id,
        "physical_gpu_name": name,
        "luid": "0x0:0x1",
        "identity_method": "test",
        "did_match": True,
    }
    return contract_mod.build_run_contract(
        run_id=run_id, gfx=gfx, eps=["dml"], models=MODELS, cases=["yolo26n__detect"],
        thresholds=TH, code=CODE, device_identity=identity, images={}, options={},
        environment=ENV, created_utc="2026-11-19T00:00:00Z")


def _receipt_with_contract(contract: dict) -> dict:
    receipt = make_receipt_v2(ep="dml", ep_nodes=1)
    receipt["run_id"] = contract["run_id"]
    receipt["case_id"] = "yolo26n__detect__dml"
    receipt["contract"] = contract_mod.build_receipt_contract(
        contract, ep="dml", model=receipt["model"], input_block=receipt["input"])
    return receipt


def test_receipt_from_other_gfx_cannot_be_accepted_under_this_contract():
    contract_1100 = _contract("gfx1100")
    contract_1151 = _contract("gfx1151", device_id="0x1586",
                              name="AMD Radeon 8060S Graphics")
    receipt = _receipt_with_contract(contract_1151)
    problems = contract_mod.validate_receipt_contract(receipt, contract_1100)
    assert any("RECEIPT_DEVICE_MISMATCH" in p for p in problems)
    guard = cross_host.receipt_architecture_problems(receipt, contract_1100)
    assert any(guard_reason.startswith(cross_host.CROSS_ARCH_RECEIPT_REFUSED)
               for guard_reason in guard)


def test_run_contract_from_other_gfx_is_refused_on_this_host():
    contract_1201 = _contract("gfx1201", device_id="0x7550",
                              name="AMD Radeon RX 9070 XT")
    live_1151 = cross_host.gate_physical_target(_host_gfx1151(), "gfx1151")
    assert live_1151["status"] == "PASS"
    problems = cross_host.run_contract_architecture_problems(contract_1201, live_1151)
    assert any(p.startswith(cross_host.CROSS_ARCH_CONTRACT_REFUSED) for p in problems)


def test_run_contract_with_wrong_did_for_its_gfx_is_refused():
    contract = _contract("gfx1100", device_id="0x1586")
    problems = cross_host.run_contract_architecture_problems(contract)
    assert any(p.startswith(cross_host.CONTRACT_DEVICE_ID_MISMATCH) for p in problems)


def test_run_contract_tamper_is_detected(tmp_path):
    run_dir = tmp_path / "RUN_X"
    contract = _contract("gfx1100")
    contract_mod.write_run_contract(run_dir, contract)
    path = run_dir / contract_mod.RUN_CONTRACT_NAME
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["device_identity"]["gfx"] = "gfx1201"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(contract_mod.ContractError, match="TAMPERED"):
        contract_mod.load_run_contract(run_dir)


def test_cross_arch_run_id_is_refused():
    problem = cross_host.run_id_architecture_problem("GFX1151_R6_CANARY", "gfx1100")
    assert problem.startswith(cross_host.RUN_ID_CROSS_ARCH_TAG)
    assert cross_host.run_id_architecture_problem("GFX1100_OK", "gfx1100") is None
    assert cross_host.run_id_architecture_problem("a/b", "gfx1100").startswith(
        cross_host.RUN_ID_INVALID)


# --- 6. receipt identity forgery ------------------------------------------------


def test_forged_host_label_is_rejected_by_strict_verifier():
    receipt = make_receipt_v2(ep="dml", ep_nodes=1)
    receipt["status"]["host"] = "VERIFIED_GFX1100"  # host.gfx is gfx1151
    verdict = validate_receipt_strict(receipt)
    assert not verdict["ok"]
    assert any("contradicts host.gfx" in p for p in verdict["problems"])


def test_forged_gfx_and_did_are_rejected_by_cross_host_guard():
    receipt = make_receipt_v2(ep="dml", ep_nodes=1)
    receipt["host"]["gfx"] = "gfx1100"
    receipt["host"]["dxgi_evidence"]["device_id"] = "0x1586"
    receipt["status"]["host"] = "VERIFIED_GFX1100"
    problems = cross_host.receipt_architecture_problems(receipt)
    assert any(p.startswith(cross_host.RECEIPT_DEVICE_ID_MISMATCH) for p in problems)


def test_cross_host_matrix_keys_cells_by_receipt_gfx():
    amd = make_receipt_v2(ep="amdgpu", ep_nodes=1)
    amd["host"]["gfx"] = "gfx1100"
    amd["host"]["dxgi_evidence"]["device_id"] = "0x7448"
    amd["status"]["host"] = "VERIFIED_GFX1100"
    dml = make_receipt_v2(ep="dml", ep_nodes=1)
    matrix = cross_host.build_cross_host_matrix([amd, dml])
    assert matrix["hosts"] == ["gfx1100", "gfx1151"]
    assert len(matrix["rows"]) == 1
    row = matrix["rows"][0]
    assert row["results"]["gfx1100"]["amdgpu"].startswith("PASS_GPU")
    assert row["results"]["gfx1151"]["dml"].startswith("PASS_GPU")


def test_cross_host_matrix_skips_unknown_or_not_run_hosts():
    unknown = make_receipt_v2(ep="dml", ep_nodes=1)
    unknown["host"]["gfx"] = "gfx9999"
    not_run = make_receipt_v2(ep="dml", ep_nodes=0, exec_status="NOT_RUN")
    matrix = cross_host.build_cross_host_matrix([unknown, not_run])
    assert matrix["hosts"] == []
    assert matrix["rows"] == []


def test_forged_status_with_zero_gpu_nodes_is_rejected():
    receipt = make_receipt_v2(ep="dml", ep_nodes=1)
    receipt["session"]["provider_node_counts"] = {"DmlExecutionProvider": 0}
    receipt["device_binding"]["final_profile"]["provider_node_counts"] = {
        "DmlExecutionProvider": 0}
    receipt["status"]["dml"] = "PASS_GPU_EXECUTED_FULL"
    verdict = validate_receipt_strict(receipt)
    assert not verdict["ok"]
    joined = " | ".join(verdict["problems"])
    assert "forged or stale" in joined