"""R7-C adversarial: input/output reuse, staleness and attempt identity.

Every case plants the failure mode the R7-C brief calls out - a stale input, a
stale output, a foreign fragment, a reused run_id - and asserts that the new
result cannot be redeemed by it. The stub worker implements the same contract
as runner._ep_worker, so these tests need no GPU and no ONNX Runtime.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from radeon_ort import unified_runtime as ur
from radeon_ort.unified_executors import WorkerExecutor

FAKE = "tests.fixtures.fake_ep_worker"


def _model(tmp_path: Path, name: str = "m.onnx", payload: bytes = b"onnx-A") -> Path:
    p = tmp_path / name
    p.write_bytes(payload)
    return p


def _inputs(value: float = 0.0, shape=(1, 3, 8, 8), dtype=np.float32):
    return {"images": np.full(shape, value, dtype=dtype)}


def _predict(tmp_path, *, run_id="RUN_A", model=None, inputs=None,
             policy="prefer_amdgpu_allow_dml", work=None, **kw):
    return ur.predict(
        model_path=model or _model(tmp_path), inputs=inputs or _inputs(),
        policy=policy, target_gfx="gfx1151",
        work_dir=work or (tmp_path / "work"), run_id=run_id,
        worker_module=FAKE, timeout_s=30, **kw)


def test_feeds_are_content_addressed_per_fingerprint(tmp_path):
    ex = WorkerExecutor(gfx="gfx1151", winml_python=sys.executable,
                        work_dir=tmp_path / "w", repo_root=Path.cwd(),
                        worker_module=FAKE)
    a, fa = ex._feeds_path(_inputs(0.0))
    b, fb = ex._feeds_path(_inputs(0.0))
    _c, fc = ex._feeds_path(_inputs(1.0))
    assert fa == fb and a == b
    assert fa != fc
    # shape and dtype are both bound: shape through the feed fingerprint, and
    # dtype through the RunResult's input_fingerprint (the feed bundle is
    # normalised to float32 on purpose, and that normalisation is disclosed).
    _, fe = ex._feeds_path(_inputs(0.0, shape=(1, 3, 8, 9)))
    assert len({fa, fc, fe}) == 3
    a32 = ur.input_fingerprint(_inputs(0.0, dtype=np.float32))
    a64 = ur.input_fingerprint(_inputs(0.0, dtype=np.float64))
    assert a32["combined_sha256"] != a64["combined_sha256"]


def test_planted_legacy_output_file_is_never_read(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    legacy = work / "out_dml.npz"
    np.savez(legacy, out0=np.full((1, 3), 999.0, dtype=np.float32))
    before = legacy.read_bytes()
    r = _predict(tmp_path, work=work)
    assert r["runtime_status"] == ur.GPU_FULL_DML
    assert legacy.read_bytes() == before            # untouched
    out = r["outputs"]["out0"]
    assert float(out.max()) == 0.0                  # not the planted 999s


def test_attempt_outputs_live_in_a_private_directory(tmp_path):
    work = tmp_path / "work"
    r = _predict(tmp_path, work=work)
    dml = next(a for a in r["attempts"] if a["ep"] == "dml")
    assert dml["attempt_dir"].endswith(dml["attempt_id"])
    assert Path(dml["outputs_path"]).parent == Path(dml["attempt_dir"])
    assert not (work / "out_dml.npz").exists()
    assert not (work / "inputs.npz").exists()


def test_reused_run_id_is_refused_before_any_execution(tmp_path):
    _predict(tmp_path, run_id="RUN_FROZEN")
    with pytest.raises(ur.ContractError, match="RUN_ID_ALREADY_FROZEN"):
        _predict(tmp_path, run_id="RUN_FROZEN", inputs=_inputs(2.0))


def test_reuse_run_id_has_no_overwrite_escape_hatch(tmp_path):
    first = _predict(tmp_path, run_id="RUN_REUSE")
    before = (tmp_path / "work" / ("receipt_%s.json" % first["run_id"])).read_bytes()
    # the parameter was removed on purpose: there is no overwrite path at all
    with pytest.raises(TypeError):
        _predict(tmp_path, run_id="RUN_REUSE", inputs=_inputs(3.0),
                 allow_reuse_run_id=True)
    with pytest.raises(ur.ContractError, match="RUN_ID_ALREADY_FROZEN"):
        _predict(tmp_path, run_id="RUN_REUSE", inputs=_inputs(3.0))
    after = (tmp_path / "work" / ("receipt_%s.json" % first["run_id"])).read_bytes()
    assert before == after, "a frozen receipt must never be rewritten"


def test_run_id_cannot_escape_the_workspace(tmp_path):
    # an empty/None run_id legitimately means "generate one"; a *supplied*
    # value must be a single safe path component
    for bad in ("..", "../evil", "a/b", "C:\\abs", "x", ".hidden", "a" * 200):
        with pytest.raises(ur.ContractError, match="INVALID_RUN_ID"):
            _predict(tmp_path, run_id=bad)


def test_different_onnx_same_work_dir_cannot_share_attempts(tmp_path):
    work = tmp_path / "work"
    a = _predict(tmp_path, work=work, run_id="RUN_M1",
                 model=_model(tmp_path, "a.onnx", b"onnx-A"))
    b = _predict(tmp_path, work=work, run_id="RUN_M2",
                 model=_model(tmp_path, "b.onnx", b"onnx-B"))
    assert a["model_sha256"] != b["model_sha256"]
    ids_a = {x["attempt_id"] for x in a["attempts"]}
    ids_b = {x["attempt_id"] for x in b["attempts"]}
    assert ids_a.isdisjoint(ids_b)
    assert len(list((work / "attempts").iterdir())) >= 4


def test_foreign_fragment_attempt_id_is_rejected(tmp_path):
    """A fragment that echoes someone else's attempt id is refused outright."""
    ex = WorkerExecutor(gfx="gfx1151", winml_python=sys.executable,
                        work_dir=tmp_path / "w", repo_root=Path.cwd(),
                        worker_module=FAKE,
                        worker_case_extra={"fake_mode": {"dml": "wrong_attempt",
                                                         "cpu": "ok"}})
    a = ex.execute(ep="dml", model_path=str(_model(tmp_path)),
                   inputs=_inputs())
    assert a.ok is False
    assert a.fragment_matches_attempt is False
    assert a.error and a.error.startswith("ATTEMPT_MISMATCH")


def test_simulation_hook_cannot_redirect_inputs(tmp_path):
    """worker_case_extra must not be able to swap the tensor or the model."""
    with pytest.raises(ValueError, match="refused keys"):
        WorkerExecutor(gfx="gfx1151", winml_python=sys.executable,
                       work_dir=tmp_path / "w", repo_root=Path.cwd(),
                       worker_module=FAKE,
                       worker_case_extra={"feeds_npz": "x.npz",
                                          "expected_feed_sha256": "0" * 64})


def test_simulated_worker_is_never_evidence_grade(tmp_path):
    r = _predict(tmp_path, run_id="RUN_SIM")
    assert r["evidence_grade"] == ur.INJECTED_GRADE
    assert r["verification_scope"] == "SIMULATED_WORKER"
    assert r["readiness"]["evidence_verified"] is False


def test_failed_worker_keeps_partial_artifacts_labelled(tmp_path):
    work = tmp_path / "work"
    r = ur.predict(model_path=_model(tmp_path), inputs=_inputs(),
                   policy="require_amdgpu", target_gfx="gfx1151",
                   work_dir=work, run_id="RUN_FAIL", worker_module=FAKE,
                   worker_case_extra={"fake_mode": {"amdgpu": "fail", "cpu": "ok"}},
                   timeout_s=30)
    assert r["runtime_status"] in (ur.BLOCKED, ur.FAIL)
    amd = next(a for a in r["attempts"] if a["ep"] == "amdgpu")
    assert amd["ok"] is False
    assert amd["exit_code"] == 31
    assert amd["partial_artifacts"], "a failed attempt must keep its artefacts"
    assert any(p.endswith("fragment.json") for p in amd["partial_artifacts"])
    assert amd["process"]["survivors"] == []


def test_timeout_marks_attempt_blocked_and_leaves_no_survivors(tmp_path):
    work = tmp_path / "work"
    r = ur.predict(model_path=_model(tmp_path), inputs=_inputs(),
                   policy="require_amdgpu", target_gfx="gfx1151",
                   work_dir=work, run_id="RUN_TIMEOUT", worker_module=FAKE,
                   worker_case_extra={"fake_mode": {"amdgpu": "hang", "cpu": "ok"}},
                   timeout_s=1)
    assert r["runtime_status"] == ur.BLOCKED
    amd = next(a for a in r["attempts"] if a["ep"] == "amdgpu")
    assert "TIMEOUT_1s" in (amd["error"] or ""), amd["error"]
    assert amd["process"]["timed_out"] is True
    assert amd["process"]["terminated_by_job"] is True
    assert amd["process"]["survivors"] == []
    assert amd["partial_artifacts"]


def test_concurrent_calls_do_not_share_attempt_directories(tmp_path):
    work = tmp_path / "work"
    results: list[dict] = []
    errors: list[BaseException] = []

    def go(i: int):
        try:
            results.append(ur.predict(
                model_path=_model(tmp_path), inputs=_inputs(float(i)),
                policy="prefer_amdgpu_allow_dml", target_gfx="gfx1151",
                work_dir=work, run_id="RUN_C%d" % i, worker_module=FAKE,
                timeout_s=30))
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(results) == 3
    dirs = [Path(a["attempt_dir"]) for r in results for a in r["attempts"]]
    assert len({str(d) for d in dirs}) == len(dirs)


def test_tampered_expected_feed_hash_is_refused(tmp_path):
    ex = WorkerExecutor(gfx="gfx1151", winml_python=sys.executable,
                        work_dir=tmp_path / "w", repo_root=Path.cwd(),
                        worker_module=FAKE)
    feeds, _fp = ex._feeds_path(_inputs())
    a = ex._run_worker("dml", str(_model(tmp_path)), reference=None,
                       feeds=feeds, feed_fp="0" * 64)
    assert a.ok is False
    assert a.error == "worker_exit=3"
    assert (a.input_binding or {}).get("ok") is False