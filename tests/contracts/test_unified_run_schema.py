"""R7-D: the RunResult JSON Schema is enforced, not advisory."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from radeon_ort import unified_runtime as ur

FAKE = "tests.fixtures.fake_ep_worker"
SCHEMA = Path(__file__).resolve().parents[2] / "schemas" / "unified_run.schema.json"


def test_schema_file_is_valid_json():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["$schema"].endswith("draft-07/schema#")
    assert "runtime_status" in schema["properties"]


@pytest.fixture()
def result(tmp_path):
    m = tmp_path / "m.onnx"
    m.write_bytes(b"onnx")
    r = ur.predict(model_path=m,
                   inputs={"images": np.zeros((1, 3, 8, 8), dtype=np.float32)},
                   policy="prefer_amdgpu_allow_dml", target_gfx="gfx1151",
                   work_dir=tmp_path / "w", run_id="RUN_SCHEMA",
                   worker_module=FAKE, timeout_s=30)
    return r


def test_real_result_passes_schema(result):
    chk = ur.validate_run_result_schema(result)
    assert chk["ok"] is True, chk


def test_missing_required_field_fails_schema(result):
    bad = {k: v for k, v in result.items() if k != "readiness"}
    chk = ur.validate_run_result_schema(bad)
    assert chk["ok"] is False
    assert "readiness" in chk["detail"]


def test_bad_enum_fails_schema(result):
    bad = dict(result, runtime_status="TOTALLY_GPU")
    assert ur.validate_run_result_schema(bad)["ok"] is False


def test_bad_receipt_hash_fails_schema(result):
    bad = dict(result, receipt_sha256="not-a-hash")
    assert ur.validate_run_result_schema(bad)["ok"] is False


def test_gpu_flag_must_be_boolean(result):
    bad = dict(result, gpu_full="yes")
    assert ur.validate_run_result_schema(bad)["ok"] is False


def test_attempt_ep_must_be_known(result):
    bad = dict(result, attempts=[{"ep": "vulkan", "ok": True, "status": "FULL"}])
    assert ur.validate_run_result_schema(bad)["ok"] is False
