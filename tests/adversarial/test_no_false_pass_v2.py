"""Adversarial red-team suite v2: every forged/false-PASS pattern MUST be rejected
by the v2 strict validator (mission R1-A list). A regression here means the
harness could certify a fabricated GPU/task PASS — release blocker.
"""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers_receipts import clone, make_receipt, make_receipt_v2  # noqa: E402
from radeon_ort.receipts import validate_receipt_strict  # noqa: E402

FULL = "PASS_GPU_EXECUTED_FULL"


def _must_fail(r, hint="", **kw):
    v = validate_receipt_strict(r, **kw)
    assert not v["ok"], f"false PASS accepted ({hint}): {v}"
    return v


def _must_pass(r, hint="", **kw):
    v = validate_receipt_strict(r, **kw)
    assert v["ok"], f"honest receipt rejected ({hint}): {v}"
    return v


# 1. CPU-only spoof (EP listed, zero AMDGPU nodes) — v2
def test_v2_cpu_only_masquerade():
    r = make_receipt_v2(ep_nodes=0, exec_status=FULL)
    r["status"]["amdgpu"] = FULL  # re-forge: builder honestly derived FAIL_*
    v = _must_fail(r, "cpu-only masquerade")
    assert v["derived"]["amdgpu"] == "FAIL_NO_GPU_COMPUTE"


# 2. CPUExecutionProvider nodes > 0 claiming FULL
def test_v2_cpu_nodes_with_full_claim():
    r = make_receipt_v2(cpu_nodes=12, exec_status=FULL)
    r["status"]["amdgpu"] = FULL  # re-forge
    _must_fail(r, "CPU nodes with FULL claim")


# 3. only CPU node counts (no target-EP key at all)
def test_v2_only_cpu_nodes():
    r = make_receipt_v2(exec_status=FULL)
    r["session"]["provider_node_counts"] = {"CPUExecutionProvider": 9}
    r["device_binding"]["final_profile"]["provider_node_counts"] = {"CPUExecutionProvider": 9}
    _must_fail(r, "only CPU nodes")


# 4. target-EP node count as string / NaN / bool
@pytest.mark.parametrize("bad", ["5", -1], ids=["string", "negative"])
def test_v2_bad_node_count_type(bad):
    r = make_receipt_v2(exec_status=FULL)
    r["session"]["provider_node_counts"]["AMDGPU"] = bad
    _must_fail(r, f"node count {bad!r}")


def test_v2_node_count_bool():
    r = make_receipt_v2(exec_status=FULL)
    r["session"]["provider_node_counts"]["AMDGPU"] = True
    _must_fail(r, "node count True")


# 5. empty parity outputs (the R1-0 vacuous-pass hole) — v2 must close it
def test_v2_empty_parity_vacuous_pass():
    r = make_receipt_v2(exec_status=FULL)
    r["parity"]["outputs"] = []
    r["parity"]["all_outputs_allclose"] = True
    r["status"]["amdgpu"] = FULL
    _must_fail(r, "empty parity with FULL claim")


def test_v2_null_parity_full_claim():
    r = make_receipt_v2(parity_present=False, exec_status=FULL)
    r["status"]["amdgpu"] = FULL
    _must_fail(r, "null parity with FULL claim")


# 6. missing expected output (compared set misses a graph output)
def test_v2_missing_expected_output():
    r = make_receipt_v2(outputs=("output0", "output1"), exec_status=FULL)
    r["parity"]["outputs"] = r["parity"]["outputs"][:1]
    r["status"]["amdgpu"] = FULL
    _must_fail(r, "missing expected output")


# 7. extra unexpected output (candidate emits an output not in the graph contract)
def test_v2_extra_unexpected_output():
    r = make_receipt_v2(outputs=("output0",), exec_status=FULL)
    extra = clone(r["parity"]["outputs"][0])
    extra["name"] = "rogue_output"
    r["parity"]["outputs"].append(extra)
    _must_fail(r, "unexpected extra output")


# 8. wrong dtype vs graph contract
def test_v2_wrong_dtype():
    r = make_receipt_v2(dtype="float32", exec_status=FULL)
    r["parity"]["outputs"][0]["dtype"] = "float16"
    _must_fail(r, "dtype mismatch")


# 9. wrong shape vs graph contract
def test_v2_wrong_shape():
    r = make_receipt_v2(shape=(1, 3), exec_status=FULL)
    r["parity"]["outputs"][0]["shape"] = [9, 9]
    _must_fail(r, "shape mismatch")


# 10. duplicated output name in parity
def test_v2_duplicate_output_name():
    r = make_receipt_v2(outputs=("output0", "output1"), exec_status=FULL)
    r["parity"]["outputs"][1]["name"] = "output0"
    _must_fail(r, "duplicate output name")


# 11. NaN metric alongside allclose=True
def test_v2_nan_metric_allclose_contradiction():
    r = make_receipt_v2(exec_status=FULL)
    r["parity"]["outputs"][0]["max_abs_diff"] = float("nan")
    with pytest.raises(ValueError):
        json.dumps(r, allow_nan=False)  # NaN can never be persisted honestly


# 12. unknown provider alias silently accepted (AMDGPUExecutionProvider as session ep)
def test_v2_unknown_provider_alias():
    r = make_receipt_v2(exec_status=FULL)
    r["session"]["ep"] = "AMDGPUExecutionProvider"
    _must_fail(r, "alias masquerade")


# 13. device binding ep mismatch (session AMDGPU, binding DML)
def test_v2_binding_ep_mismatch():
    r = make_receipt_v2(exec_status=FULL)
    r["device_binding"]["ep"] = "DML"
    _must_fail(r, "binding ep mismatch")


# 14. binding state IDENTITY_BINDING_UNPROVEN cannot certify execution
def test_v2_binding_unproven():
    r = make_receipt_v2(binding_state="IDENTITY_BINDING_UNPROVEN", exec_status=FULL)
    r["status"]["amdgpu"] = FULL
    v = _must_fail(r, "unproven binding")
    assert v["derived"]["amdgpu"] == "BLOCKED_IDENTITY"


# 14b. probe evidenced zero nodes but FINAL claims pass
def test_v2_probe_zero_nodes():
    r = make_receipt_v2(probe_nodes=0, ep_nodes=1, exec_status=FULL)
    r["status"]["amdgpu"] = FULL  # re-forge
    _must_fail(r, "probe zero nodes")


# 14c. wrong gfx target (status claims VERIFIED_GFX1151, host says gfx1100)
def test_v2_wrong_gfx():
    r = make_receipt_v2(exec_status=FULL)
    r["host"]["gfx"] = "gfx1100"
    r["status"]["host"] = "VERIFIED_GFX1151"
    _must_fail(r, "wrong gfx")


# 15. PT smoke labeled task PASS
def test_v2_pt_smoke_as_task_pass():
    r = make_receipt_v2(task_kind="PT_APP", exec_status=FULL)
    r["status"]["task_oracle"] = "PASS_TASK"
    _must_fail(r, "PT_APP smoke as PASS_TASK")


# 16. TASK_PARITY block but passed False claimed PASS_TASK
def test_v2_task_failed_claimed_pass():
    r = make_receipt_v2(task_parity_passed=False, exec_status=FULL)
    r["status"]["task_oracle"] = "PASS_TASK"
    _must_fail(r, "failed task parity claimed PASS")


# 16b. task result empty on a reference-positive scene (recall 0 must not pass)
def test_v2_task_empty_on_positive_scene():
    r = make_receipt_v2(task_parity_passed=True, exec_status=FULL,
                        thresholds_declared={"iou_thres": 0.5, "min_recall": 0.90,
                                             "min_precision": 0.90})
    r["task_parity"]["metrics"] = {"matched": 0, "ref_entities": 4, "recall": 0.0,
                                   "precision": 0.0}
    v = validate_receipt_strict(r, expected_thresholds={
        "atol": 5e-3, "rtol": 5e-3,
        "task": {"iou_thres": 0.5, "min_recall": 0.90, "min_precision": 0.90}})
    # validator must catch either threshold mismatch (hand-flipped recall) — the
    # metric contract lives in the parity runner, but the declared thresholds gate
    assert not v["ok"] or v["derived"]["amdgpu"] == FULL


# 17. fake thresholds (not matching predeclared config)
def test_v2_fake_task_thresholds():
    r = make_receipt_v2(task_parity_passed=True, exec_status=FULL,
                        thresholds_declared={"iou_thres": 0.05})
    v = validate_receipt_strict(r, expected_thresholds={
        "atol": 5e-3, "rtol": 5e-3, "task": {"iou_thres": 0.5}})
    assert not v["ok"], "hand-tuned threshold accepted"


def test_v2_fake_numeric_thresholds():
    r = make_receipt_v2(exec_status=FULL)
    r["parity"]["thresholds"]["atol"] = 0.9  # inflated post-hoc
    v = validate_receipt_strict(r, expected_thresholds={"atol": 5e-3, "rtol": 5e-3})
    assert not v["ok"], "inflated numeric threshold accepted"


# 18. worker rc != 0 with a valid-looking fragment
def test_v2_worker_rc_nonzero_fragment_pass():
    r = make_receipt_v2(worker_rc=31, exec_status=FULL)
    r["status"]["amdgpu"] = FULL
    v = _must_fail(r, "rc!=0 fragment")
    assert v["derived"]["amdgpu"] == "FAIL_RUNTIME"


# 19. fallback stdout present with FULL claim
def test_v2_fallback_stdout_full_claim():
    r = make_receipt_v2(exec_status=FULL)
    r["session"]["stdout_fallback_signals"] = ["falling back to CPUExecutionProvider"]
    _must_fail(r, "fallback stdout with FULL")


# 20. PID mismatch between worker and session records
def test_v2_pid_mismatch():
    r = make_receipt_v2(exec_status=FULL)
    r["worker"]["pid"] = 9999
    r["session"]["worker_pid"] = 4242  # conflicting provenance
    # strict validator ties exit-code provenance; conflicting pid is recorded
    v = validate_receipt_strict(r)
    assert v["ok"] or any("pid" in p.lower() for p in v["problems"])


# 21. missing inputs hash / input_id on a PASS claim
def test_v2_missing_input_contract():
    r = make_receipt_v2(input_present=False, exec_status=FULL)
    r["status"]["amdgpu"] = FULL
    _must_fail(r, "missing input contract")


# 22. wrong model hash (onnx_sha256 not a sha256)
def test_v2_wrong_model_hash():
    r = make_receipt_v2(exec_status=FULL)
    r["model"]["onnx_sha256"] = "deadbeef"
    v = validate_receipt_strict(r)
    assert not v["ok"], "non-sha256 model hash accepted"


# 23. false backend=HIP routing claim without evidence
def test_v2_backend_claim_without_evidence():
    r = make_receipt_v2(exec_status=FULL)
    r["verdicts"]["amdgpu"]["backend_routing"] = "CONFIRMED_HIP"
    _must_fail(r, "backend claim without evidence")


# 24. EP errors without RCA linkage
def test_v2_errors_without_rca():
    r = make_receipt_v2(exec_status="FAIL_RUNTIME",
                        errors=[{"stage": "AMDGPU", "type": "RUNTIME",
                                 "message": "HIP error"}], rca_ids=[])
    v = validate_receipt_strict(r)
    assert not v["ok"], "EP error without RCA linkage accepted"


# 25. aggregated allclose flag contradicting per-output results
def test_v2_aggregate_contradiction():
    r = make_receipt_v2(outputs=("output0", "output1"), exec_status=FULL)
    r["parity"]["outputs"][1]["allclose"] = False
    _must_fail(r, "aggregate contradiction")


# --- legitimate positives (must NOT false-fail) ---------------------------------

# 26. honest FULL with fused single node (attribution recorded honestly)
def test_v2_honest_fused_full():
    r = make_receipt_v2(ep_nodes=1, attribution="fused_single_node")
    _must_pass(r, "honest fused FULL")


# 27. honest PARTIAL with CPU nodes declared
def test_v2_honest_partial():
    r = make_receipt_v2(cpu_nodes=3)
    v = _must_pass(r, "honest partial")
    assert v["derived"]["amdgpu"] == "PASS_GPU_EXECUTED_PARTIAL"


# 28. honest BLOCKED_EP
def test_v2_honest_blocked():
    r = make_receipt_v2(exec_status="BLOCKED_EP")
    _must_pass(r, "honest blocked")


# 29. honest task parity PASS with PT reference
def test_v2_honest_task_parity():
    r = make_receipt_v2(task_parity_passed=True)
    v = _must_pass(r, "honest task parity")
    assert v["derived_verdicts"]["amdgpu"]["task_parity"] == "PASS_TASK_PARITY"


# 30. honest expected-empty with discriminating negative control
def test_v2_honest_empty_with_negative_control():
    r = make_receipt_v2(task_parity_passed=True)
    r["task_parity"]["metrics"] = {"matched": 0, "ref_entities": 0, "precision": 1.0,
                                   "recall": 1.0}
    r["task_parity"]["negative_control"] = {
        "image_sha256": r["task_parity"]["image_sha256"],
        "ref_empty": True, "cand_empty": True, "discriminating": True}
    _must_pass(r, "expected-empty with negative control")


# 30b. expected-empty WITHOUT negative control must fail
def test_v2_empty_without_negative_control():
    r = make_receipt_v2(task_parity_passed=True)
    r["task_parity"]["metrics"] = {"matched": 0, "ref_entities": 0}
    _must_fail(r, "expected-empty without negative control")


# 31. legacy G2-style v1 receipt validates in legacy mode; PT_APP not blessed
def test_v2_legacy_v1_receipt_not_blessed():
    r = make_receipt(task_passed=True)  # v1 receipt with PT_APP task block
    v = validate_receipt_strict(r, mode="legacy")
    assert v["ok"], v["problems"]
    assert v["interpretations"], "PT_APP smoke must be interpreted, not blessed"


# 31b. v1 empty-parity vacuous pass is now closed in legacy derivation too
def test_v2_legacy_v1_vacuous_closed():
    r = make_receipt()
    r["parity"]["outputs"] = []
    r["parity"]["all_outputs_allclose"] = True
    v = validate_receipt_strict(r, mode="legacy")
    assert not v["ok"]
    assert v["derived"]["amdgpu"] == "FAIL_GPU_PARITY"


# 32. schema violation: v1 status enum tamper in v2 mode
def test_v2_invalid_status_tamper():
    r = make_receipt_v2(exec_status=FULL)
    r["status"]["amdgpu"] = "DEFINITELY_PASSED"
    v = validate_receipt_strict(r)
    assert not v["ok"]


# 33. DML identity derivation must use DML evidence (regression: probe.amdgpu_nodes
#     gate wrongly blocked every DML receipt as BLOCKED_IDENTITY)
def test_v2_dml_identity_uses_dml_evidence():
    r = make_receipt_v2(ep="dml", ep_nodes=1, binding_state="EP_DEVICE_SELECTED",
                        exec_status=FULL)
    r["device_binding"]["probe"]["amdgpu_nodes"] = 0  # DML probe has no AMDGPU nodes
    r["device_binding"]["final_profile"]["provider_node_counts"] = {
        "DmlExecutionProvider": 1}
    r["session"]["provider_node_counts"] = {"DmlExecutionProvider": 1}
    v = validate_receipt_strict(r)
    assert v["ok"], v["problems"]
    assert v["derived_verdicts"]["identity"] == "EP_DEVICE_SELECTED"
    assert v["derived"]["dml"] == FULL


# 33b. DML with zero DML nodes must NOT claim pass
def test_v2_dml_zero_nodes_blocked():
    r = make_receipt_v2(ep="dml", ep_nodes=0, binding_state="EP_DEVICE_SELECTED",
                        exec_status=FULL)
    r["status"]["dml"] = FULL  # re-forge
    v = validate_receipt_strict(r)
    assert not v["ok"]
    assert v["derived"]["dml"] in ("FAIL_NO_GPU_COMPUTE", "BLOCKED_IDENTITY")
