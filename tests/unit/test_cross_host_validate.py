"""Unit tests for runner.cross_host_validate (Missions R8 + R9).

Everything here is CPU-only and synthetic. The tests freeze:

* the policy -> EP/run_matrix mapping;
* the ``--run-id`` contract (required unless ``--dry-run``, no cross-arch tag);
* the HARD GATE: on a simulated gfx1151 host a gfx1100/gfx1201 request refuses
  with WRONG_PHYSICAL_DEVICE, exit 34, and creates no per-gfx evidence;
* the dry-run contract: a valid plan exits 0 even when the gate would refuse,
  and the report never contains an absolute user path;
* the exact layered verdict vocabulary.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from radeon_ort import cross_host
from radeon_ort.device_identity import AMD_VENDOR_ID, DxgiAdapter
from runner import REPO_ROOT
from runner import cross_host_validate as chv


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


def _plan(gfx: str = "gfx1100", policy: str = "gpu-first"):
    run_id = f"{gfx.upper()}_UNIT"
    return chv.build_plan(chv.parse_args(["--gfx", gfx, "--policy", policy,
                                          "--run-id", run_id]))


def _passing_statuses() -> dict[str, str]:
    return {
        "detect_host": "PASS",
        "check_dependencies": "PASS",
        "ep_probe": "PASS",
        "device_canary": "PASS",
        "tier_a": "PASS",
        "multi_input_oracle": "PASS",
        "benchmarks": "PASS",
        "soak": "PASS",
        "evidence_freeze": "PASS",
    }


def _gate_ok(gfx: str = "gfx1100") -> dict:
    return {"status": "PASS", "requested_gfx": gfx, "live_gfx": gfx}


# --- CLI / plan contract --------------------------------------------------------


def test_policy_ep_mapping():
    assert chv.ep_scope("gpu-first") == ("amdgpu", "dml")
    assert chv.ep_scope("amdgpu-only") == ("amdgpu",)
    assert chv.ep_scope("dml-only") == ("dml",)
    assert chv.run_matrix_ep("gpu-first") == "both"
    assert chv.run_matrix_ep("amdgpu-only") == "amdgpu"
    assert chv.run_matrix_ep("dml-only") == "dml"


def test_run_id_required_unless_dry_run():
    with pytest.raises(SystemExit):
        chv.parse_args(["--gfx", "gfx1100"])
    ns = chv.parse_args(["--gfx", "gfx1100", "--dry-run"])
    assert ns.run_id is None
    assert chv.build_plan(ns).run_id == "<RUN_ID>"


def test_cross_arch_run_id_tag_is_refused():
    with pytest.raises(SystemExit):
        chv.parse_args(["--gfx", "gfx1100", "--run-id", "GFX1151_REUSED"])
    with pytest.raises(SystemExit):
        chv.parse_args(["--gfx", "gfx1201", "--run-id", "GFX1100_REUSED"])
    # Plain ids stay valid; the tag is only rejected when it names another gfx.
    assert chv.parse_args(["--gfx", "gfx1100", "--run-id", "X"]).run_id == "X"


def test_unknown_gfx_is_rejected_by_argparse():
    with pytest.raises(SystemExit):
        chv.parse_args(["--gfx", "gfx9999", "--dry-run"])


def test_build_plan_resolves_relative_paths_against_repo_root():
    ns = chv.parse_args(["--gfx", "gfx1100", "--dry-run", "--report-root", "reports",
                         "--catalog", "catalog/model_catalog.json",
                         "--winml-python", "python.exe"])
    plan = chv.build_plan(ns)
    assert plan.report_root == REPO_ROOT / "reports"
    assert plan.catalog == REPO_ROOT / "catalog" / "model_catalog.json"
    assert plan.winml_python == REPO_ROOT / "python.exe"


# --- hard gate ------------------------------------------------------------------


def test_gate_refuses_gfx1100_on_simulated_gfx1151_host():
    plan = _plan("gfx1100")
    gate = cross_host.gate_physical_target([_adapter()], plan.gfx)
    assert gate["status"] == "BLOCKED"
    assert gate["reason"] == cross_host.WRONG_PHYSICAL_DEVICE
    assert gate["live_gfx"] == "gfx1151"
    assert gate["selected_device_id"] is None


def test_gate_refuses_gfx1201_on_simulated_gfx1151_host():
    gate = cross_host.gate_physical_target([_adapter()], "gfx1201")
    assert gate["status"] == "BLOCKED"
    assert gate["reason"] == cross_host.WRONG_PHYSICAL_DEVICE
    assert "0x7550" in " ".join(gate["expected_device_ids"])
    assert gate["observed_device_ids"] == ["0x1586"]


def test_execute_gate_mismatch_exits_34_and_writes_no_gfx_evidence(tmp_path, monkeypatch, capsys):
    plan_calls: list[tuple] = []

    def _boom(*args, **kwargs):
        plan_calls.append((args, kwargs))
        raise AssertionError("no subprocess may run after a gate refusal")

    monkeypatch.setattr(chv, "enum_dxgi_adapters", lambda: [_adapter()])
    monkeypatch.setattr(chv, "run_command", _boom)
    report_root = tmp_path / "reports"
    rc = chv.main(["--gfx", "gfx1100", "--tier", "smoke", "--policy", "dml-only",
                   "--run-id", "X", "--report-root", str(report_root)])
    assert rc == 34
    assert plan_calls == []
    assert not (report_root / "gfx1100").exists()
    # the refusal report is namespaced by gfx so two architectures cannot
    # overwrite each other's refusal evidence
    report_path = report_root / "_blocked" / "X.gfx1100.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["final_verdict"] == "GFX1100_CROSS_HOST_HARNESS_READY"
    assert report["exit_code"] == 34
    assert report["physical_gate"]["reason"] == "WRONG_PHYSICAL_DEVICE"
    statuses = {s["stage"]: s["status"] for s in report["stages"]}
    assert statuses["device_canary"] == "BLOCKED"
    assert statuses["tier_a"] == "BLOCKED"
    assert statuses["evidence_freeze"] == "SKIPPED"
    out = capsys.readouterr().out
    assert "HARD GATE REFUSED" in out
    assert "no evidence directory was created" in out


def test_dry_run_gfx1100_on_gfx1151_host_is_valid_and_states_refusal(tmp_path, monkeypatch, capsys):
    def _stub_checks(plan, gate, dxgi_error, *, probe_clis):
        assert probe_clis is True
        return [{"check": "stub", "ok": True, "detail": "stub"}]

    monkeypatch.setattr(chv, "enum_dxgi_adapters", lambda: [_adapter()])
    monkeypatch.setattr(chv, "plan_checks", _stub_checks)
    out_path = tmp_path / "dry.json"
    report_root = tmp_path / "reports"
    rc = chv.main(["--gfx", "gfx1100", "--dry-run", "--report-root", str(report_root),
                   "--json-out", str(out_path)])
    assert rc == 0
    assert not (report_root / "gfx1100").exists()
    report = json.loads(out_path.read_text(encoding="utf-8"))
    assert report["mode"] == "dry-run"
    assert report["final_verdict"] == "GFX1100_CROSS_HOST_HARNESS_READY"
    assert report["physical_gate"]["reason"] == "WRONG_PHYSICAL_DEVICE"
    assert [s["stage"] for s in report["stages"]] == list(chv.STAGES)
    printed = capsys.readouterr().out
    assert "WOULD REFUSE" in printed
    assert "no ONNX session, no GPU work" in printed
    assert "runner.run_matrix" in printed
    text = out_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in text
    assert "C:\\Users\\" not in text


def test_dry_run_reports_invalid_plan_when_a_required_check_fails(tmp_path, monkeypatch):
    def _failing_checks(plan, gate, dxgi_error, *, probe_clis):
        return [{"check": "bench_onnx", "ok": False, "detail": "missing"}]

    monkeypatch.setattr(chv, "enum_dxgi_adapters", lambda: [_adapter()])
    monkeypatch.setattr(chv, "plan_checks", _failing_checks)
    out_path = tmp_path / "dry.json"
    rc = chv.main(["--gfx", "gfx1100", "--dry-run", "--json-out", str(out_path)])
    assert rc == 1
    assert json.loads(out_path.read_text(encoding="utf-8"))["exit_code"] == 1


def test_ep_probe_classification_respects_policy():
    payload = {
        "state": "CATALOG_EMPTY",
        "ep_readiness": {"ort_providers": ["DmlExecutionProvider", "CPUExecutionProvider"]},
    }
    dml_only = chv.build_plan(chv.parse_args(
        ["--gfx", "gfx1100", "--policy", "dml-only", "--run-id", "GFX1100_D"]))
    info = chv._ep_probe_classify(dml_only, payload, 2)
    assert info["status"] == "PASS"
    assert info["dml_ready"] is True
    assert info["amdgpu_ready"] is False

    gpu_first = chv.build_plan(chv.parse_args(
        ["--gfx", "gfx1100", "--policy", "gpu-first", "--run-id", "GFX1100_G"]))
    assert chv._ep_probe_classify(gpu_first, payload, 2)["status"] == "PASS"

    amdgpu_only = chv.build_plan(chv.parse_args(
        ["--gfx", "gfx1100", "--policy", "amdgpu-only", "--run-id", "GFX1100_A"]))
    blocked = chv._ep_probe_classify(amdgpu_only, payload, 2)
    assert blocked["status"] == "BLOCKED"
    assert "no DML/CPU fallback" in blocked["reason"]

    assert chv._ep_probe_classify(dml_only, None, 1)["status"] == "FAIL"


def test_find_bench_input_prefers_detect_manifest(tmp_path):
    plan = _plan("gfx1100")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    npy = inputs / "image_input.npy"
    npy.write_bytes(b"tensor")
    (inputs / "manifest_deadbeef.json").write_text(json.dumps({
        "model_name": "yolo26n", "task": "detect", "npy_file": npy.name,
    }), encoding="utf-8")
    (inputs / "manifest_other.json").write_text(json.dumps({
        "model_name": "yolo26n-seg", "task": "segment", "npy_file": "other.npy",
    }), encoding="utf-8")
    assert chv._find_bench_input(plan, tmp_path) == npy


def test_find_bench_input_honours_explicit_override(tmp_path):
    plan = chv.build_plan(chv.parse_args(
        ["--gfx", "gfx1100", "--run-id", "GFX1100_B",
         "--bench-input-npy", str(tmp_path / "explicit.npy")]))
    explicit = tmp_path / "explicit.npy"
    explicit.write_bytes(b"tensor")
    assert chv._find_bench_input(plan, tmp_path) == explicit


def test_freeze_evidence_is_skipped_without_receipts(tmp_path):
    plan = _plan("gfx1100")
    (tmp_path / "receipts").mkdir()
    assert chv._freeze_evidence(plan, tmp_path, _gate_ok()) is None


# --- verdict decision -----------------------------------------------------------


def test_verdict_vocabulary_is_exact():
    for kind in cross_host.VERDICT_KINDS:
        assert cross_host.gfx_verdict("gfx1100", kind) == f"GFX1100_{kind}"
    with pytest.raises(ValueError):
        cross_host.gfx_verdict("gfx1100", "AMDGPU_AND_DML_CORE_GO")
    with pytest.raises(ValueError):
        cross_host.gfx_verdict("gfx9999", "PHYSICAL_GO")


def test_decide_verdict_layers():
    plan = _plan("gfx1100", "gpu-first")
    ok = _gate_ok()
    all_pass = _passing_statuses()
    assert chv._decide_verdict(plan, gate=ok, dry_run=True, stage_statuses={},
                               ep_results={}) == "GFX1100_CROSS_HOST_HARNESS_READY"
    assert chv._decide_verdict(plan, gate={"status": "BLOCKED"}, dry_run=False,
                               stage_statuses=all_pass,
                               ep_results={}) == "GFX1100_CROSS_HOST_HARNESS_READY"
    assert chv._decide_verdict(plan, gate=ok, dry_run=False, stage_statuses=all_pass,
                               ep_results={"amdgpu": "PASS", "dml": "PASS"}) == (
        "GFX1100_PHYSICAL_GO")
    conditional = dict(all_pass, tier_a="BLOCKED", device_canary="BLOCKED")
    assert chv._decide_verdict(plan, gate=ok, dry_run=False, stage_statuses=conditional,
                               ep_results={"amdgpu": "BLOCKED_EP", "dml": "PASS"}) == (
        "GFX1100_CONDITIONAL_GO")
    blocked = dict(all_pass, device_canary="SKIPPED", tier_a="SKIPPED",
                   multi_input_oracle="SKIPPED", benchmarks="SKIPPED", soak="SKIPPED")
    assert chv._decide_verdict(plan, gate=ok, dry_run=False, stage_statuses=blocked,
                               ep_results={"amdgpu": "BLOCKED_EP", "dml": "BLOCKED_EP"}) == (
        "GFX1100_BLOCKED_EP")
    assert chv._decide_verdict(plan, gate=ok, dry_run=False,
                               stage_statuses=dict(all_pass, benchmarks="FAIL"),
                               ep_results={"amdgpu": "PASS", "dml": "PASS"}) == (
        "GFX1100_NO_GO")
    pending = dict(all_pass, device_canary="NOT_RUN", tier_a="NOT_RUN",
                   multi_input_oracle="NOT_RUN", benchmarks="NOT_RUN", soak="NOT_RUN")
    assert chv._decide_verdict(plan, gate=ok, dry_run=False, stage_statuses=pending,
                               ep_results={}) == "GFX1100_PHYSICAL_PENDING"


def test_exit_codes_follow_verdict():
    assert chv._exit_code("GFX1100_PHYSICAL_GO") == 0
    assert chv._exit_code("GFX1100_NO_GO") == 1
    for verdict in ("GFX1100_CROSS_HOST_HARNESS_READY", "GFX1100_PHYSICAL_PENDING",
                    "GFX1100_CONDITIONAL_GO", "GFX1100_BLOCKED_EP"):
        assert chv._exit_code(verdict) == 2


# --- command builders -----------------------------------------------------------


def test_documented_flags_match_runner_cli_modules():
    """Every module the entrypoint shells out to must be in CLI_MODULES."""
    for module in ("probe_amdgpu_ep", "unified_canary", "run_matrix",
                   "multi_input_oracle", "bench_repeats", "soak",
                   "redact_evidence", "finalize_run", "verify_receipts",
                   "build_compatibility_matrix"):
        assert module in chv.CLI_MODULES
        assert (ROOT / "runner" / f"{module}.py").is_file()


def test_tier_a_command_for_dml_only_and_smoke():
    plan = chv.build_plan(chv.parse_args(
        ["--gfx", "gfx1201", "--tier", "smoke", "--policy", "dml-only",
         "--run-id", "GFX1201_SMOKE"]))
    argv = chv.cmd_tier_a(plan)
    text = " ".join(argv)
    assert "--gfx gfx1201" in text
    assert "--ep dml" in text
    assert "--limit 1" in text
    assert "--with-negative" not in text
    assert "--winml-python" in text and "--export-python" in text

    core = chv.build_plan(chv.parse_args(
        ["--gfx", "gfx1201", "--tier", "core", "--policy", "gpu-first",
         "--run-id", "GFX1201_CORE"]))
    core_argv = chv.cmd_tier_a(core)
    assert "--tier core" in " ".join(core_argv)
    assert "--with-negative" in core_argv
    assert "--limit" not in core_argv


def test_evidence_freeze_command_order_is_redact_finalize_compat_verify():
    plan = _plan("gfx1100")
    commands = chv.planned_commands(plan, plan.run_dir)["evidence_freeze"]
    joined = [" ".join(c) for c in commands]
    assert "runner.redact_evidence" in joined[0]
    assert "runner.finalize_run" in joined[1]
    assert "runner.build_compatibility_matrix" in joined[2]
    assert "runner.verify_receipts" in joined[3]
    assert "--check" in joined[4]

def test_skip_soak_cannot_reach_physical_go():
    plan = _plan()
    stages = _passing_statuses()
    stages["soak"] = "SKIPPED"
    verdict = chv._decide_verdict(plan, gate={"status": "PASS"}, dry_run=False,
                                  stage_statuses=stages,
                                  ep_results={"amdgpu": "PASS", "dml": "PASS"})
    assert verdict == "GFX1100_CONDITIONAL_GO", verdict
    stages["soak"] = "PASS"
    verdict = chv._decide_verdict(plan, gate={"status": "PASS"}, dry_run=False,
                                  stage_statuses=stages,
                                  ep_results={"amdgpu": "PASS", "dml": "PASS"})
    assert verdict == "GFX1100_PHYSICAL_GO", verdict


def test_run_id_dot_paths_and_reserved_names_are_refused():
    from radeon_ort import cross_host

    for bad in (".", "..", "CON", "nul", "a..b/", " x", "x "):
        assert cross_host.run_id_architecture_problem(bad, "gfx1100") is not None, bad
    assert cross_host.run_id_architecture_problem("GFX1100_R8_OK", "gfx1100") is None