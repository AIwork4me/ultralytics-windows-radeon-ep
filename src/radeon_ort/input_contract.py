"""Deterministic, comparable inputs (mission R1-B).

The input_id is SHA256 over a CANONICAL JSON config of exactly:
  model identity + ONNX sha256 + task + image sha256 + imgsz + export params +
  test variant.
It deliberately EXCLUDES: EP name, process id, PYTHONHASHSEED, timestamp, and
host-only paths — so the same case yields byte-identical inputs across
processes, hash seeds, and providers (CPU/AMDGPU/DML).

Real-image task parity consumes ONE persisted tensor bundle (.npy + manifest)
generated in the export venv (cv2 available); every EP worker loads the same
bytes and re-derives the tensor sha256 at load time (mutation detection).

Random *stress* inputs remain a separate verdict class: deterministic given the
input_id, never sufficient for task correctness.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def canonical_config(*, model_name: str, onnx_sha256: str, task: str,
                     imgsz: int, image_sha256: str | None, export_params: dict,
                     variant: str) -> str:
    """Canonical JSON: sorted keys, no whitespace, no excluded fields."""
    cfg = {
        "model_name": str(model_name),
        "onnx_sha256": str(onnx_sha256),
        "task": str(task),
        "imgsz": int(imgsz),
        "image_sha256": str(image_sha256) if image_sha256 else None,
        "export_params": export_params or {},
        "variant": str(variant),
    }
    return json.dumps(cfg, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def input_id_from_canonical(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def make_input_id(**kwargs) -> tuple[str, str]:
    c = canonical_config(**kwargs)
    return input_id_from_canonical(c), c


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tensor_sha256(arr) -> str:
    import numpy as np
    a = np.ascontiguousarray(arr)
    h = hashlib.sha256()
    h.update(f"{a.dtype}|{a.shape}|".encode())
    h.update(a.tobytes())
    return h.hexdigest()


def stress_tensor(input_id: str, shape) -> "np.ndarray":
    """Deterministic pseudo-random stress tensor derived ONLY from input_id.

    The seed is the first 16 hex chars of input_id (stable across processes and
    PYTHONHASHSEED values). Never used for task-parity verdicts.
    """
    import numpy as np
    seed = int(input_id[:16], 16)
    rng = np.random.default_rng(seed)
    return rng.random(shape, dtype=np.float32)


# --- real-image preprocessing (export venv; cv2 available) ----------------------


def letterbox_params(h: int, w: int, out_sz: int) -> dict:
    r = min(out_sz / h, out_sz / w)
    nh, nw = round(h * r), round(w * r)
    dh, dw = out_sz - nh, out_sz - nw
    top, bottom = dh // 2, dh - dh // 2
    left, right = dw // 2, dw - dw // 2
    return {"ratio": r, "new_h": nh, "new_w": nw, "top": top, "left": left,
            "bottom": bottom, "right": right}


def preprocess_real_image(bgr: "np.ndarray", imgsz: int, task: str) -> tuple["np.ndarray", dict]:
    """Task-appropriate deterministic preprocessing, ultralytics-equivalent semantics.

    - detect/segment/pose/obb/semantic/depth: LetterBox(imgsz) — AR-preserving
      resize (INTER_LINEAR) + 114-pad, BGR->RGB, /255, CHW, NCHW float32.
    - classify: Resize shorter side to imgsz + center crop (matches ultralytics
      classify_transforms: RGB, /255 — no letterbox padding).
    """
    import cv2
    import numpy as np

    h, w = bgr.shape[:2]
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    if task == "classify":
        # ultralytics classify: Resize(size) (shorter side) then CenterCrop(size)
        scale = imgsz / min(h, w)
        nh, nw = round(h * scale), round(w * scale)
        resized = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
        top = max((nh - imgsz) // 2, 0)
        left = max((nw - imgsz) // 2, 0)
        crop = resized[top:top + imgsz, left:left + imgsz]
        params = {"method": "resize_short+centercrop", "resize_h": nh, "resize_w": nw,
                  "crop_top": top, "crop_left": left, "imgsz": imgsz}
        out = crop
    else:
        p = letterbox_params(h, w, imgsz)
        resized = cv2.resize(rgb, (p["new_w"], p["new_h"]), interpolation=cv2.INTER_LINEAR)
        out = cv2.copyMakeBorder(resized, p["top"], p["bottom"], p["left"], p["right"],
                                 cv2.BORDER_CONSTANT, value=(114, 114, 114))
        params = {"method": "letterbox", **p, "imgsz": imgsz}
    x = out.astype(np.float32) / 255.0
    x = x.transpose(2, 0, 1)[None]
    return np.ascontiguousarray(x), params


def inverse_letterbox_box(box_xyxy, params: dict, orig_h: int, orig_w: int):
    """Map a box from letterboxed input pixels back to original image pixels."""
    x1, y1, x2, y2 = box_xyxy
    r, left, top = params["ratio"], params["left"], params["top"]
    return [(x1 - left) / r, (y1 - top) / r, (x2 - left) / r, (y2 - top) / r]


def build_input_bundle(*, image_path: str | Path, imgsz: int, task: str, model_name: str,
                       onnx_sha256: str, variant: str, out_dir: str | Path,
                       export_params: dict | None = None) -> dict:
    """Create the persisted input bundle (.npy + manifest). Runs in export venv."""
    import numpy as np

    image_path = Path(image_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    import cv2
    bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(f"cannot decode image: {image_path}")
    img_sha = file_sha256(image_path)
    input_id, canonical = make_input_id(
        model_name=model_name, onnx_sha256=onnx_sha256, task=task, imgsz=imgsz,
        image_sha256=img_sha, export_params=export_params or {}, variant=variant)
    tensor, params = preprocess_real_image(bgr, imgsz, task)
    npy_path = out_dir / f"input_{input_id[:16]}.npy"
    np.save(npy_path, tensor)
    manifest = {
        "input_id": input_id,
        "canonical_config": canonical,
        "kind": "real_image",
        "image_path_name": image_path.name,   # name only — no private paths
        "image_sha256": img_sha,
        "imgsz": imgsz,
        "task": task,
        "model_name": model_name,
        "onnx_sha256": onnx_sha256,
        "variant": variant,
        "tensor_dtype": str(tensor.dtype),
        "tensor_shape": list(tensor.shape),
        "tensor_sha256": tensor_sha256(tensor),
        "npy_file": npy_path.name,
        "decoded_image_sha256": tensor_sha256(bgr),  # decode determinism evidence
        "preprocess": params,
        "pt_equivalence": (
            "same decoded image; ultralytics-semantics preprocessing "
            "(letterbox/centercrop as per task); PT oracle preprocesses the same "
            "image file with its own pipeline — comparison thresholds absorb "
            "resampling-level differences"),
    }
    (out_dir / f"manifest_{input_id[:16]}.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def load_input_bundle(manifest_path: str | Path, npy_dir: str | Path | None = None) -> tuple[dict, "np.ndarray"]:
    """Load and VERIFY a bundle: tensor sha must match the manifest (any EP)."""
    import numpy as np

    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    base = npy_dir if npy_dir is not None else Path(manifest_path).parent
    arr = np.load(Path(base) / manifest["npy_file"])
    got = tensor_sha256(arr)
    if got != manifest["tensor_sha256"]:
        raise ValueError(f"input tensor mutated: sha {got} != manifest "
                         f"{manifest['tensor_sha256']}")
    return manifest, arr


def validate_case_input_source(case: dict) -> str | None:
    """Return a contract-violation message, or None when the case is well formed.

    Mission C: a case must declare EXACTLY ONE input source — an inline
    `feeds_npz` bundle (multi-input canary) or an `input_manifest` (persisted
    single-input bundle) — and a declared manifest must exist. Before this
    validator the worker raised an uncaught KeyError for neither/both, so a
    mis-wired harness looked exactly like an EP or session failure (rc != 0).

    Pure function: no ONNX Runtime, no numpy import, safe for CPU CI.
    """
    if not isinstance(case, dict):
        return f"case must be a JSON object, got {type(case).__name__}"
    declared = [k for k in ("feeds_npz", "input_manifest") if case.get(k)]
    if len(declared) != 1:
        return ("case must declare exactly one of feeds_npz|input_manifest "
                f"(declared={declared or 'none'})")
    if declared[0] == "input_manifest" and not Path(case["input_manifest"]).is_file():
        return f"input_manifest does not exist: {case['input_manifest']}"
    if declared[0] == "feeds_npz" and not Path(case["feeds_npz"]).is_file():
        return f"feeds_npz does not exist: {case['feeds_npz']}"
    return None
