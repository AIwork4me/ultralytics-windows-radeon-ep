"""R1-B: deterministic, comparable inputs.

Mission gate B: cross-process SHA match and per-provider identical input;
no task pass from random inputs. These tests run CPU-only (numpy); the
real-image decode determinism (cv2) is exercised by the hardware pipeline.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from radeon_ort.input_contract import (  # noqa: E402
    canonical_config, input_id_from_canonical, make_input_id, stress_tensor,
    tensor_sha256, build_input_bundle, load_input_bundle)

BASE = dict(model_name="yolo26n", onnx_sha256="a" * 64, task="detect", imgsz=640,
            image_sha256="b" * 64, export_params={"nms_mode": "raw"}, variant="real_image_v1")


def test_input_id_stable_across_dict_key_order():
    c1 = canonical_config(**BASE)
    c2 = canonical_config(**{k: BASE[k] for k in reversed(BASE)})
    assert c1 == c2
    assert input_id_from_canonical(c1) == input_id_from_canonical(c2)


def test_input_id_excludes_ep_pid_seed_timestamp():
    iid, _ = make_input_id(**BASE)
    # excluded fields must not appear anywhere in the canonical form
    _, canonical = make_input_id(**BASE)
    for forbidden in ("ep", "pid", "hashseed", "timestamp", "PYTHONHASHSEED"):
        assert forbidden.lower() not in canonical.lower()
    # same semantic config -> same id
    iid2, _ = make_input_id(**BASE)
    assert iid == iid2


def test_input_id_changes_with_every_declared_field():
    ref, _ = make_input_id(**BASE)
    variants = [
        {**BASE, "model_name": "yolo26s"},
        {**BASE, "onnx_sha256": "c" * 64},
        {**BASE, "task": "segment"},
        {**BASE, "imgsz": 224},
        {**BASE, "image_sha256": "d" * 64},
        {**BASE, "export_params": {"nms_mode": "embedded_nms"}},
        {**BASE, "variant": "real_image_v2"},
    ]
    for v in variants:
        assert make_input_id(**v)[0] != ref


def test_stress_tensor_deterministic_and_distinct():
    iid, _ = make_input_id(**{**BASE, "variant": "stress_v1"})
    a = stress_tensor(iid, (1, 3, 64, 64))
    b = stress_tensor(iid, (1, 3, 64, 64))
    assert a.dtype == b.dtype and (a == b).all()
    iid2, _ = make_input_id(**{**BASE, "variant": "stress_v2"})
    c = stress_tensor(iid2, (1, 3, 64, 64))
    assert not (a == c).all()


def test_tensor_sha256_binding():
    import numpy as np
    x = np.linspace(0, 1, 24, dtype=np.float32).reshape(1, 3, 2, 4)
    assert tensor_sha256(x) == tensor_sha256(x.copy())
    y = x.copy()
    y[0, 0, 0, 0] = np.float32(0.5)
    assert tensor_sha256(x) != tensor_sha256(y)


_SNIPPET = r"""
import sys
sys.path.insert(0, r"{src}")
import os
from radeon_ort.input_contract import make_input_id, stress_tensor, tensor_sha256
iid, _ = make_input_id(model_name="yolo26n", onnx_sha256="a"*64, task="detect",
                       imgsz=640, image_sha256="b"*64,
                       export_params={{"nms_mode": "raw"}}, variant="xproc")
t = stress_tensor(iid, (1, 3, 32, 32))
print(iid, tensor_sha256(t))
"""


def _run_xproc(seed_env: dict | None):
    src = str(Path(__file__).resolve().parents[2] / "src")
    env = dict(os.environ)
    env.pop("PYTHONHASHSEED", None)
    if seed_env:
        env.update(seed_env)
    r = subprocess.run([sys.executable, "-I", "-c", _SNIPPET.format(src=src)],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip().split()


def test_cross_process_sha_match_hashseed_0_vs_random():
    iid0, sha0 = _run_xproc({"PYTHONHASHSEED": "0"})
    iid1, sha1 = _run_xproc({"PYTHONHASHSEED": "12345"})
    assert iid0 == iid1, "input_id leaked process state"
    assert sha0 == sha1, "stress tensor bytes differ across processes"


def test_bundle_roundtrip_and_mutation_detection(tmp_path):
    import numpy as np
    # synth "image" via direct tensor save (cv2-free path for unit test)
    tensor = np.linspace(0, 1, 3 * 16 * 16, dtype=np.float32).reshape(1, 3, 16, 16)
    iid, canonical = make_input_id(**BASE)
    npy = tmp_path / f"input_{iid[:16]}.npy"
    np.save(npy, tensor)
    manifest = {
        "input_id": iid, "canonical_config": canonical, "kind": "real_image",
        "image_path_name": "x.jpg", "image_sha256": "b" * 64, "imgsz": 640,
        "task": "detect", "model_name": "yolo26n", "onnx_sha256": "a" * 64,
        "variant": "real_image_v1", "tensor_dtype": str(tensor.dtype),
        "tensor_shape": list(tensor.shape), "tensor_sha256": tensor_sha256(tensor),
        "npy_file": npy.name, "decoded_image_sha256": "0" * 64, "preprocess": {},
        "pt_equivalence": "unit",
    }
    (tmp_path / f"manifest_{iid[:16]}.json").write_text(
        __import__("json").dumps(manifest), encoding="utf-8")
    m, arr = load_input_bundle(tmp_path / f"manifest_{iid[:16]}.json")
    assert m["input_id"] == iid and (arr == tensor).all()
    # mutate the persisted bytes: any EP loading this bundle must refuse it
    arr[0, 0, 0, 0] = np.float32(9.0)
    np.save(npy, arr)
    with pytest.raises(ValueError, match="mutated"):
        load_input_bundle(tmp_path / f"manifest_{iid[:16]}.json")
