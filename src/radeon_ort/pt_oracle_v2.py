"""PT application oracle v2 — the independent PyTorch reference (mission R1-D).

Runs the pinned Ultralytics (8.4.215) `.pt` model on the EXACT preprocessed
input tensor (.npy from the R1-B input bundle) so PT and ONNX/EP sides share
identical input bytes (apples-to-apples; no preprocessing divergence).

Executed in the export venv:  python -I src/radeon_ort/pt_oracle_v2.py ...

Outputs:
  --out-json : lightweight summary (boxes/scores/classes/topk/stats)
  --out-npz  : heavy arrays (masks, keypoints, probs vector, sem mask, depth)

Results are in INPUT-TENSOR coordinate space (both sides compare there).
PT running successfully is PT_APP_SMOKE evidence only; task parity is decided
by the comparator against the GPU postprocessed result.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pt", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--tensor-npy", required=True)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-npz", required=True)
    a = ap.parse_args()

    import torch
    from ultralytics import YOLO

    x = np.load(a.tensor_npy)
    t = torch.from_numpy(np.ascontiguousarray(x, dtype=np.float32))
    model = YOLO(a.pt)
    task = model.task or a.task
    r = model.predict(t, conf=a.conf, verbose=False, device="cpu")[0]

    out: dict = {"task": task, "conf": a.conf, "input_shape": list(x.shape)}
    heavy: dict = {}
    b = getattr(r, "boxes", None)
    if task == "detect":
        out["boxes"] = b.xyxy.cpu().numpy().tolist() if b is not None else []
        out["scores"] = b.conf.cpu().numpy().tolist() if b is not None else []
        out["classes"] = b.cls.cpu().numpy().astype(int).tolist() if b is not None else []
    elif task == "segment":
        out["boxes"] = b.xyxy.cpu().numpy().tolist() if b is not None else []
        out["scores"] = b.conf.cpu().numpy().tolist() if b is not None else []
        out["classes"] = b.cls.cpu().numpy().astype(int).tolist() if b is not None else []
        m = getattr(r, "masks", None)
        if m is not None and m.data is not None:
            heavy["masks"] = (m.data.cpu().numpy() > 0.5).astype(np.uint8)
            out["mask_shape"] = list(heavy["masks"].shape)
        else:
            heavy["masks"] = np.zeros((0,) + tuple(x.shape[2:]), dtype=np.uint8)
            out["mask_shape"] = list(heavy["masks"].shape)
    elif task == "classify":
        p = getattr(r, "probs", None)
        if p is not None:
            vec = p.data.cpu().numpy().astype(np.float32)
            heavy["probs"] = vec
            top = np.argsort(vec)[::-1][:10]
            out["top1"] = int(top[0])
            out["top5"] = [int(i) for i in top[:5]]
            out["top5_scores"] = [float(vec[i]) for i in top[:5]]
    elif task == "pose":
        out["boxes"] = b.xyxy.cpu().numpy().tolist() if b is not None else []
        out["scores"] = b.conf.cpu().numpy().tolist() if b is not None else []
        out["classes"] = b.cls.cpu().numpy().astype(int).tolist() if b is not None else []
        k = getattr(r, "keypoints", None)
        if k is not None and k.data is not None:
            heavy["keypoints"] = k.data.cpu().numpy().astype(np.float32)
            out["kpt_shape"] = list(heavy["keypoints"].shape)
    elif task == "obb":
        ob = getattr(r, "obb", None)
        if ob is not None and ob.xywhr is not None:
            heavy["obb_xywhr"] = ob.xywhr.cpu().numpy().astype(np.float32)
            out["scores"] = ob.conf.cpu().numpy().tolist() if ob.conf is not None else []
            out["classes"] = ob.cls.cpu().numpy().astype(int).tolist() if ob.cls is not None else []
            out["n"] = int(ob.xywhr.shape[0])
        else:
            heavy["obb_xywhr"] = np.zeros((0, 5), dtype=np.float32)
            out["scores"], out["classes"], out["n"] = [], [], 0
    elif task == "semantic":
        sm = getattr(r, "semantic_mask", None)
        arr = None
        if sm is not None:
            data = getattr(sm, "data", sm)
            arr = data.cpu().numpy() if hasattr(data, "cpu") else np.asarray(data)
        if arr is None:
            arr = np.zeros(x.shape[2:], dtype=np.uint8)
        heavy["semantic"] = arr.astype(np.uint8)
        out["classes_present"] = np.unique(arr).astype(int).tolist()
        out["map_shape"] = list(arr.shape)
    elif task == "depth":
        dm = getattr(r, "depth", None)
        arr = None
        if dm is not None:
            data = getattr(dm, "data", dm)
            arr = data.cpu().numpy() if hasattr(data, "cpu") else np.asarray(data)
        if arr is None:
            arr = np.full(x.shape[2:], float("nan"), dtype=np.float32)
        heavy["depth"] = arr.astype(np.float32)
        out["depth_stats"] = {"min": float(np.nanmin(arr)), "max": float(np.nanmax(arr)),
                              "mean": float(np.nanmean(arr))}
        out["map_shape"] = list(arr.shape)
    else:
        out["error"] = f"unhandled task {task}"

    Path(a.out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out_json).write_text(json.dumps(out), encoding="utf-8")
    np.savez_compressed(a.out_npz, **heavy)
    print(json.dumps({"ok": True, "task": task, "n": out.get("n", len(out.get("boxes", [])))}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
