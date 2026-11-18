"""RCA2A-008 regression tests: dml_session must accept supervisor-prepared
SessionOptions (the rca2a_worker --ep dml path) without TypeError, and must
still enforce the DML-mandated session flags on the override object.
Pure CPU: InferenceSession is monkeypatched, no model is loaded."""
from __future__ import annotations

import sys
from pathlib import Path

import onnxruntime as ort
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from radeon_ort.dml_session import create_dml_session


class _FakeInferenceSession:
    def __init__(self, path, sess_options=None, providers=None,
                 provider_options=None):
        self.path = path
        self.so = sess_options
        self.providers = providers
        self.pov = provider_options


def test_create_dml_session_accepts_sess_options_override(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(ort, "InferenceSession", _FakeInferenceSession)
    so = ort.SessionOptions()
    so.enable_profiling = True
    so.profile_file_prefix = str(tmp_path / "worker_prefix")
    sess = create_dml_session("m.onnx", device_id=3,
                              sess_options_override=so)
    # the override object itself is used, not a fresh one
    assert sess.so is so
    # DML-mandated flags were enforced ON the override
    assert so.execution_mode == ort.ExecutionMode.ORT_SEQUENTIAL
    assert so.enable_mem_pattern is False
    assert so.log_severity_level == 3
    # provider wiring unchanged: DML first with the resolved adapter ordinal
    assert sess.providers[0] == "DmlExecutionProvider"
    assert sess.pov[0] == {"device_id": 3}


def test_create_dml_session_default_path_unchanged(monkeypatch):
    monkeypatch.setattr(ort, "InferenceSession", _FakeInferenceSession)
    sess = create_dml_session("m.onnx", device_id=0, enable_profiling=False)
    assert isinstance(sess.so, ort.SessionOptions)
    assert sess.so.enable_profiling is False
    assert sess.pov == [{"device_id": 0}, {}]
