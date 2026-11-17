"""Hardware tests — run only on the physical 8060S host (deselected in CPU CI).

pytest -m hardware
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

pytestmark = pytest.mark.hardware


def _has_amd_gpu():
    try:
        from radeon_ort.device_identity import enum_dxgi_adapters, resolve_target_adapter
        return resolve_target_adapter(enum_dxgi_adapters(), gfx="gfx1151")["ok"]
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _has_amd_gpu(), reason="no gfx1151 on this host")
def test_amdgpu_canary_executes_on_gpu(tmp_path):
    from radeon_ort.amdgpu_session import canary_model_path
    from radeon_ort.device_identity import canary_inputs
    from radeon_ort import profiler_audit
    from radeon_ort.amdgpu_session import create_amdgpu_session

    model = canary_model_path(tmp_path)
    feeds = canary_inputs()
    sess = create_amdgpu_session(model, profile_prefix=str(tmp_path / "prof"))
    out = sess.run(None, {"a": feeds["a"], "b": feeds["b"]})
    assert out[0].shape == (256, 256)
    audit = profiler_audit.audit_profile(sess.end_profiling())
    assert audit["provider_node_counts"].get("AMDGPU", 0) > 0, "no GPU compute evidenced"


@pytest.mark.skipif(not _has_amd_gpu(), reason="no gfx1151 on this host")
def test_dml_canary_executes_on_gpu(tmp_path):
    import numpy as np
    from radeon_ort.amdgpu_session import canary_model_path
    from radeon_ort.dml_session import create_dml_session
    from radeon_ort import profiler_audit
    from radeon_ort.device_identity import enum_dxgi_adapters, resolve_target_adapter

    res = resolve_target_adapter(enum_dxgi_adapters(), gfx="gfx1151")
    assert res["ok"]
    model = canary_model_path(tmp_path)
    feeds = canary_inputs()
    sess = create_dml_session(model, device_id=res["adapter"].index,
                              profile_prefix=str(tmp_path / "dmlprof"))
    out = sess.run(None, {"a": feeds["a"], "b": feeds["b"]})
    assert out[0].shape == (256, 256)
    audit = profiler_audit.audit_profile(sess.end_profiling())
    assert audit["provider_node_counts"].get("Dml", 0) > 0, "no DML compute evidenced"


@pytest.mark.skipif(not _has_amd_gpu(), reason="no gfx1151 on this host")
def test_amdgpu_parity_against_cpu(tmp_path):
    import numpy as np
    from radeon_ort.amdgpu_session import canary_model_path, create_amdgpu_session
    from radeon_ort.cpu_reference import compare_outputs, create_cpu_session
    from radeon_ort.device_identity import canary_inputs

    model = canary_model_path(tmp_path)
    feeds = canary_inputs()
    cpu = create_cpu_session(model)
    ref = {o.name: v for o, v in zip(cpu.get_outputs(), cpu.run(None, feeds))}
    gpu = create_amdgpu_session(model)
    outs = gpu.run(None, feeds)
    cand = {o.name: v for o, v in zip(gpu.get_outputs(), outs)}
    r = compare_outputs(ref, cand, atol=2e-4, rtol=2e-4)
    assert r["all_outputs_allclose"], r
