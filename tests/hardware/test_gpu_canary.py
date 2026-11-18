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
    from radeon_ort.amdgpu_session import (canary_model_path,
                                           create_amdgpu_session,
                                           ensure_amdgpu_registered)
    from radeon_ort.device_identity import canary_inputs

    ensure_amdgpu_registered()  # hardware tests must be self-sufficient (R1-0 finding)
    model = canary_model_path(tmp_path)
    feeds = canary_inputs()
    result = create_amdgpu_session(model, profile_prefix=str(tmp_path / "prof"),
                                   feeds_for_validation=feeds)
    sess = result.session
    out = sess.run(None, {"a": feeds["a"], "b": feeds["b"]})
    assert out[0].shape == (256, 256)
    att = result.attestation
    assert att["state"] == "EP_DEVICE_SELECTED"
    assert att["probe"]["amdgpu_nodes"] > 0
    # the FINAL rebuilt session is revalidated inside create (its profile is
    # consumed there); assert the final attribution evidence from the attestation
    final_counts = (att.get("final_profile") or {}).get("provider_node_counts") or {}
    assert final_counts.get("AMDGPU", 0) > 0, "no GPU compute evidenced on final session"


@pytest.mark.skipif(not _has_amd_gpu(), reason="no gfx1151 on this host")
def test_dml_canary_executes_on_gpu(tmp_path):
    import numpy as np
    from radeon_ort.amdgpu_session import canary_model_path
    from radeon_ort.dml_session import create_dml_session
    from radeon_ort import profiler_audit
    from radeon_ort.device_identity import enum_dxgi_adapters, resolve_target_adapter

    res = resolve_target_adapter(enum_dxgi_adapters(), gfx="gfx1151")
    assert res["ok"]
    from radeon_ort.device_identity import canary_inputs
    model = canary_model_path(tmp_path)
    feeds = canary_inputs()
    sess = create_dml_session(model, device_id=res["adapter"].index,
                              profile_prefix=str(tmp_path / "dmlprof"))
    out = sess.run(None, {"a": feeds["a"], "b": feeds["b"]})
    assert out[0].shape == (256, 256)
    audit = profiler_audit.audit_profile(sess.end_profiling())
    # ORT profiler key is the real provider name (DmlExecutionProvider, not "Dml")
    assert audit["provider_node_counts"].get("DmlExecutionProvider", 0) > 0, "no DML compute evidenced"


@pytest.mark.skipif(not _has_amd_gpu(), reason="no gfx1151 on this host")
def test_amdgpu_parity_against_cpu(tmp_path):
    import numpy as np
    from radeon_ort.amdgpu_session import (canary_model_path, create_amdgpu_session,
                                           ensure_amdgpu_registered)
    from radeon_ort.cpu_reference import compare_outputs, create_cpu_session
    from radeon_ort.device_identity import canary_inputs

    ensure_amdgpu_registered()
    model = canary_model_path(tmp_path)
    feeds = canary_inputs()
    cpu = create_cpu_session(model)
    ref = {o.name: v for o, v in zip(cpu.get_outputs(), cpu.run(None, feeds))}
    result = create_amdgpu_session(model, feeds_for_validation=feeds)
    gpu = result.session
    outs = gpu.run(None, feeds)
    cand = {o.name: v for o, v in zip(gpu.get_outputs(), outs)}
    r = compare_outputs(ref, cand, atol=2e-4, rtol=2e-4)
    assert r["all_outputs_allclose"], r
