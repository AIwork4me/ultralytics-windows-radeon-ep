"""Contract tamper matrix (R3-B): every untrusted receipt must fail the audit.

Covers the ten tamper classes named by the mission: thresholds, schema, harness
python, ONNX, input tensor, EP, device DID, forged PASS, extra receipt, missing
receipt, commit HEAD, dirty/untracked harness code. No GPU is involved.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers_receipts import make_receipt_v2

from radeon_ort import contract as c

TH = {"numeric_parity": {"fp32": {"atol": 5e-3, "rtol": 5e-3}},
      "task_oracle": {"detection": {"iou_thres": 0.5, "min_matches": 1}}}
ENV = {"python_executable_name": "python.exe", "python_version": "3.12.10",
       "os_build": "10.0.26200", "ort_version": "1.25.2",
       "ort_distribution": "onnxruntime-windowsml",
       "ort_providers": ["CPUExecutionProvider", "DmlExecutionProvider"],
       "windowsml_version": "2.5.96", "probe_error": None}
DEV = {"gfx": "gfx1151", "host_id": "host_test", "vendor_id": "0x1002",
       "device_id": "0x1586", "physical_gpu_name": "AMD Radeon 8060S Graphics",
       "luid": "0x0:0x11746", "identity_method": "test", "did_match": True}
MODELS = [{"checkpoint": "yolo26n.pt", "task": "detect", "imgsz": 640,
           "pt_sha256": "1" * 64, "onnx_sha256": "2" * 64,
           "asset_available": True, "gfx": "gfx1151"}]
IMAGES = {"primary": {"name": "bus.jpg", "sha256": "3" * 64}}
OPTIONS = {"benchmark": False, "with_negative": False}
HOST = {"gfx": "gfx1151", "host_id": "host_test", "vendor_id": "0x1002",
        "device_id": "0x1586", "verified_device_identity": True,
        "device_luid": "0x0:0x11746"}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@example.invalid",
                    "-c", "user.name=test", *args],
                   cwd=str(repo), check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:  # pragma: no cover - CI has git
        pytest.skip("git not available")
    root = tmp_path / "repo"
    for d in ("runner", "src/radeon_ort", "configs", "schemas", "reports"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "runner" / "run_matrix.py").write_text("print('v1')\n", encoding="utf-8")
    (root / "src" / "radeon_ort" / "receipts.py").write_text("V = 1\n", encoding="utf-8")
    (root / "configs" / "thresholds.yaml").write_text("atol: 0.005\n", encoding="utf-8")
    (root / "schemas" / "receipt_v2.json").write_text('{"v": 1}\n', encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return root


def _contract(root: Path, *, code=None, environment=ENV, eps=("dml",)):
    return c.build_run_contract(
        run_id="RUN_TEST", gfx="gfx1151", eps=list(eps), models=MODELS,
        cases=["yolo26n__detect"], thresholds=TH,
        code=code or c.code_state(root), device_identity=DEV, images=IMAGES,
        options=OPTIONS, environment=environment,
        created_utc="2026-11-19T00:00:00Z")


def _receipt(contract: dict, *, ep="dml", case="yolo26n__detect"):
    rec = make_receipt_v2(ep=ep, ep_nodes=1)
    rec["case_id"] = case + "__" + ep
    rec["run_id"] = "RUN_TEST"
    rec["contract"] = c.build_receipt_contract(
        contract, ep=ep, model=rec["model"], input_block=rec["input"])
    return rec


# ---------------------------------------------------------------- hash scope
def test_harness_hash_covers_configs_and_schemas(repo):
    base = c.harness_sha256(repo)
    (repo / "configs" / "thresholds.yaml").write_text("atol: 0.5\n", encoding="utf-8")
    assert c.harness_sha256(repo) != base, "threshold change must move the hash"
    base2 = c.harness_sha256(repo)
    (repo / "schemas" / "receipt_v2.json").write_text('{"v": 2}\n', encoding="utf-8")
    assert c.harness_sha256(repo) != base2, "schema change must move the hash"


def test_harness_hash_excludes_reports_and_caches(repo):
    base = c.harness_sha256(repo)
    (repo / "reports" / "freeze.json").write_text("{}", encoding="utf-8")
    (repo / ".pytest_cache").mkdir(exist_ok=True)
    (repo / ".pytest_cache" / "state.json").write_text("{}", encoding="utf-8")
    (repo / "configs" / "__pycache__").mkdir(exist_ok=True)
    (repo / "configs" / "__pycache__" / "x.pyc").write_bytes(b"junk")
    assert c.harness_sha256(repo) == base


def test_untracked_harness_file_is_dirty(repo):
    assert c.code_state(repo)["worktree_dirty"] is False
    (repo / "runner" / "new_uncommitted.py").write_text("print('x')\n", encoding="utf-8")
    state = c.code_state(repo)
    assert state["worktree_dirty"] is True
    assert state["harness_file_count"] >= 5


def test_tracked_harness_edit_is_dirty(repo):
    (repo / "src" / "radeon_ort" / "receipts.py").write_text("V = 2\n", encoding="utf-8")
    assert c.code_state(repo)["worktree_dirty"] is True


# ------------------------------------------------------------- contract drift
def test_commit_head_change_is_refused(repo):
    contract = _contract(repo)
    (repo / "runner" / "run_matrix.py").write_text("print('v2')\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "v2")
    problems = c.verify_run_contract(
        contract, repo_root=repo, run_dir=repo / "RUN_TEST", thresholds=TH,
        host_block=HOST, models=["yolo26n.pt"], eps=["dml"], images=IMAGES,
        options=OPTIONS, environment=ENV)
    assert any("CODE_SHA_CHANGED" in p for p in problems)
    assert any("HARNESS_CONTENT_CHANGED" in p for p in problems)


def test_environment_change_is_refused(repo):
    contract = _contract(repo)
    moved = dict(ENV, ort_version="1.30.1")
    problems = c.verify_run_contract(
        contract, repo_root=repo, run_dir=repo / "RUN_TEST", thresholds=TH,
        host_block=HOST, models=["yolo26n.pt"], eps=["dml"], images=IMAGES,
        options=OPTIONS, environment=moved)
    assert any("ENVIRONMENT_CHANGED" in p for p in problems)


def test_unbound_environment_is_refused(repo):
    contract = _contract(repo, environment={})
    problems = c.verify_run_contract(
        contract, repo_root=repo, run_dir=repo / "RUN_TEST", thresholds=TH,
        host_block=HOST, models=["yolo26n.pt"], eps=["dml"], images=IMAGES,
        options=OPTIONS, environment=ENV)
    assert any("ENVIRONMENT_UNBOUND" in p for p in problems)


def test_dirty_contract_cannot_resume(repo):
    state = dict(c.code_state(repo), worktree_dirty=True)
    contract = _contract(repo, code=state)
    problems = c.verify_run_contract(
        contract, repo_root=repo, run_dir=repo / "RUN_TEST", thresholds=TH,
        host_block=HOST, models=["yolo26n.pt"], eps=["dml"], images=IMAGES,
        options=OPTIONS, environment=ENV)
    assert any("CODE_STATE_DIRTY" in p for p in problems)


# --------------------------------------------------------------- receipt drift
def test_receipt_ep_swap_is_refused(repo):
    contract = _contract(repo, eps=("amdgpu",))
    rec = _receipt(contract, ep="amdgpu")
    rec["contract"]["ep"] = "dml"
    assert any("RECEIPT_EP_MISMATCH" in p
               for p in c.validate_receipt_contract(rec, contract))


def test_receipt_device_did_swap_is_refused(repo):
    contract = _contract(repo)
    rec = _receipt(contract)
    assert c.validate_receipt_contract(rec, contract) == []
    rec["contract"]["device_identity"] = dict(DEV, device_id="0x7550")
    assert any("RECEIPT_DEVICE_MISMATCH" in p
               for p in c.validate_receipt_contract(rec, contract))


def test_receipt_onnx_and_input_swap_are_refused(repo):
    contract = _contract(repo)
    rec = _receipt(contract)
    bad_model = copy.deepcopy(rec)
    bad_model["model"]["onnx_sha256"] = "f" * 64
    assert any("MODEL_ONNX" in p
               for p in c.validate_receipt_contract(bad_model, contract))
    bad_input = copy.deepcopy(rec)
    bad_input["input"]["tensor_sha256"] = {"images": "e" * 64}
    assert any("INPUT_TENSOR" in p
               for p in c.validate_receipt_contract(bad_input, contract))


def test_receipt_environment_swap_is_refused(repo):
    contract = _contract(repo)
    rec = _receipt(contract)
    rec["contract"]["environment"] = dict(ENV, ort_version="9.9.9")
    assert any("RECEIPT_ENVIRONMENT_MISMATCH" in p
               for p in c.validate_receipt_contract(rec, contract))


# ------------------------------------------------------------------- audits
def _audit_run(tmp_path: Path, receipts: list[dict], *, eps=("dml",)):
    from runner.run_matrix import _audit

    run = tmp_path / "RUN_TEST"
    (run / "receipts").mkdir(parents=True)
    contract = c.build_run_contract(
        run_id="RUN_TEST", gfx="gfx1151", eps=list(eps), models=MODELS,
        cases=["yolo26n__detect"], thresholds=TH,
        code={"git_head_sha": "a" * 40, "worktree_dirty": False,
              "harness_sha256": "b" * 64},
        device_identity=DEV, images=IMAGES, options=OPTIONS, environment=ENV,
        created_utc="2026-11-19T00:00:00Z")
    c.write_thresholds_snapshot(run, TH)
    c.write_run_contract(run, contract)
    for rec in receipts:
        (run / "receipts" / f"receipt_{rec['case_id']}.json").write_text(
            json.dumps(rec), encoding="utf-8")
    return _audit(run, contract, run_id="RUN_TEST")


def _audit_receipt(eps=("dml",)):
    contract = c.build_run_contract(
        run_id="RUN_TEST", gfx="gfx1151", eps=list(eps), models=MODELS,
        cases=["yolo26n__detect"], thresholds=TH,
        code={"git_head_sha": "a" * 40, "worktree_dirty": False,
              "harness_sha256": "b" * 64},
        device_identity=DEV, images=IMAGES, options=OPTIONS, environment=ENV,
        created_utc="2026-11-19T00:00:00Z")
    return contract, _receipt(contract, ep="dml")


def test_audit_accepts_honest_receipt(tmp_path):
    _, rec = _audit_receipt()
    rc, audit = _audit_run(tmp_path, [rec])
    assert rc == 0, audit
    assert audit["counts"]["PASS_FULL"] == 1


def test_audit_detects_extra_receipt(tmp_path):
    _, rec = _audit_receipt()
    extra = copy.deepcopy(rec)
    extra["case_id"] = "yolo26n__pose__dml"
    rc, audit = _audit_run(tmp_path, [rec, extra])
    assert rc == 1, audit
    assert any("unexpected receipt" in p for p in audit["problems"])


def test_audit_detects_missing_receipt_as_blocked(tmp_path):
    rc, audit = _audit_run(tmp_path, [])
    assert rc == 2, audit
    assert audit["counts"]["NOT_RUN"] == 1


def test_audit_detects_forged_pass(tmp_path):
    _, rec = _audit_receipt()
    rec["session"]["provider_node_counts"] = {"DmlExecutionProvider": 0}
    rec["device_binding"]["final_profile"]["provider_node_counts"] = {
        "DmlExecutionProvider": 0}
    rec["status"]["dml"] = "PASS_GPU_EXECUTED_FULL"
    rc, audit = _audit_run(tmp_path, [rec])
    assert rc == 1, audit
    assert any("strict validation failed" in p for p in audit["problems"])


def test_audit_detects_receipt_from_other_run(tmp_path):
    _, rec = _audit_receipt()
    rec["run_id"] = "RUN_OTHER"
    rc, audit = _audit_run(tmp_path, [rec])
    assert rc == 1, audit
    assert any("run_id=" in p for p in audit["problems"])
