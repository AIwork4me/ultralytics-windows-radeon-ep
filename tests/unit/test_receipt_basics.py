import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers_receipts import make_receipt  # noqa: E402
from radeon_ort.receipts import validate_receipt_strict  # noqa: E402


def test_valid_full_pass_receipt_passes():
    v = validate_receipt_strict(make_receipt())
    assert v["ok"], v["problems"]
    assert v["derived"]["amdgpu"] == "PASS_GPU_EXECUTED_FULL"


def test_partial_when_cpu_nodes_present():
    r = make_receipt(cpu_nodes=3, amdgpu="PASS_GPU_EXECUTED_PARTIAL")
    v = validate_receipt_strict(r)
    assert v["ok"], v["problems"]
    assert v["derived"]["amdgpu"] == "PASS_GPU_EXECUTED_PARTIAL"


def test_full_claim_with_cpu_nodes_is_forged():
    r = make_receipt(cpu_nodes=3)  # claims FULL but CPU nodes exist
    v = validate_receipt_strict(r)
    assert not v["ok"]
    assert any("FULL claim but CPU nodes" in p for p in v["problems"])


def test_zero_ep_nodes_derives_fail_no_gpu_compute():
    r = make_receipt(amdgpu_nodes=0, amdgpu="FAIL_NO_GPU_COMPUTE")
    v = validate_receipt_strict(r)
    assert v["ok"], v["problems"]
    r2 = make_receipt(amdgpu_nodes=0, amdgpu="PASS_GPU_EXECUTED_FULL")
    v2 = validate_receipt_strict(r2)
    assert not v2["ok"]
    assert v2["derived"]["amdgpu"] == "FAIL_NO_GPU_COMPUTE"


def test_unverified_identity_blocks():
    r = make_receipt(verified=False, amdgpu="BLOCKED_IDENTITY")
    v = validate_receipt_strict(r)
    assert v["ok"], v["problems"]


def test_parity_mismatch_derives_fail_parity():
    r = make_receipt(parity_allclose=False, amdgpu="FAIL_GPU_PARITY")
    v = validate_receipt_strict(r)
    assert v["ok"], v["problems"]
    r2 = make_receipt(parity_allclose=False, amdgpu="PASS_GPU_EXECUTED_FULL")
    v2 = validate_receipt_strict(r2)
    assert v2["derived"]["amdgpu"] == "FAIL_GPU_PARITY"
    assert not v2["ok"]


def test_task_fail_beats_pass():
    r = make_receipt(task_passed=False, amdgpu="FAIL_TASK")
    v = validate_receipt_strict(r)
    assert v["ok"], v["problems"]
    r2 = make_receipt(task_passed=False, amdgpu="PASS_GPU_EXECUTED_FULL")
    v2 = validate_receipt_strict(r2)
    assert v2["derived"]["amdgpu"] == "FAIL_TASK"


def test_runtime_error_derives_fail_runtime():
    r = make_receipt(errors=[{"stage": "AMDGPU", "type": "RUNTIME", "message": "EP init fail"}],
                     amdgpu="FAIL_RUNTIME")
    v = validate_receipt_strict(r)
    assert v["ok"], v["problems"]
    r2 = make_receipt(errors=[{"stage": "AMDGPU", "type": "RUNTIME", "message": "boom"}],
                      amdgpu="PASS_GPU_EXECUTED_FULL")
    v2 = validate_receipt_strict(r2)
    assert v2["derived"]["amdgpu"] == "FAIL_RUNTIME"
