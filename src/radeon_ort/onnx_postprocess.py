"""ONNX/EP output postprocess — vendor-faithful decode (mission R1-D).

Decodes the raw EP outputs (.npz from _ep_worker) using the PINNED
Ultralytics 8.4.215 decoding functions themselves (ultralytics.utils.nms.
non_max_suppression, ultralytics.utils.ops.process_mask/crop_mask), so the
GPU-side postprocess is by construction the same semantics the PT oracle used.

Executed in the export venv: python -I src/radeon_ort/onnx_postprocess.py ...

Convention (from ultralytics/utils/nms.py:13, verified 8.4.215):
  raw [1, 4+nc+extra, N]; conf 0.25; NMS IoU = PREDICT_IOU (0.5); class-aware
  via max_wh offset; max_det 300; extras (mask coeffs / keypoints / angle)
  appended after (x1,y1,x2,y2,conf,cls). rotated=True keeps xywh + angle last.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--imgsz", type=int, required=True)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-npz", required=True)
    a = ap.parse_args()

    import torch
    from ultralytics.utils import PREDICT_IOU
    from ultralytics.utils.nms import non_max_suppression

    z = np.load(a.npz)
    out: dict = {"task": a.task, "conf": a.conf, "imgsz": a.imgsz}
    heavy: dict = {}

    def _nms(raw: np.ndarray, nc: int, **kw):
        return non_max_suppression(torch.from_numpy(np.ascontiguousarray(raw)),
                                   conf_thres=a.conf, iou_thres=PREDICT_IOU,
                                   nc=nc, **kw)[0].cpu().numpy()

    if a.task == "detect":
        raw = z["output0"]                       # [1, 4+nc, N]
        nc = raw.shape[1] - 4
        dets = _nms(raw, nc)                     # (n, 6)
        out["boxes"] = dets[:, :4].tolist()
        out["scores"] = dets[:, 4].tolist()
        out["classes"] = dets[:, 5].astype(int).tolist()
    elif a.task == "segment":
        raw, proto = z["output0"], z["output1"]  # [1,4+nc+32,N], [1,32,160,160]
        ncoeff = proto.shape[1]
        nc = raw.shape[1] - 4 - ncoeff
        dets = _nms(raw, nc)                     # (n, 6+32)
        out["boxes"] = dets[:, :4].tolist()
        out["scores"] = dets[:, 4].tolist()
        out["classes"] = dets[:, 5].astype(int).tolist()
        from ultralytics.utils.ops import crop_mask, process_mask
        if len(dets):
            # vendor process_mask expects 3D protos (dim, mh, mw); ONNX emits [1,dim,mh,mw]
            proto3 = proto.reshape(proto.shape[-3], proto.shape[-2], proto.shape[-1])
            masks = process_mask(torch.from_numpy(proto3), torch.from_numpy(dets[:, 6:]),
                                 torch.from_numpy(dets[:, :4]), (a.imgsz, a.imgsz),
                                 upsample=True)
            masks = crop_mask(masks, torch.from_numpy(dets[:, :4]))
            heavy["masks"] = (masks.cpu().numpy() > 0.5).astype(np.uint8)
        else:
            heavy["masks"] = np.zeros((0, a.imgsz, a.imgsz), dtype=np.uint8)
        out["mask_shape"] = list(heavy["masks"].shape)
    elif a.task == "classify":
        vec = z["output0"].reshape(-1).astype(np.float32)  # [1, 1000]
        # Ultralytics classify export applies softmax IN-GRAPH (verified live:
        # output sums to 1.0, min>0). Normalize only if the graph emitted raw
        # logits; never double-softmax.
        s = float(vec.sum())
        if not (0.999 < s < 1.001 and float(vec.min()) >= 0.0):
            e = np.exp(vec - vec.max())
            vec = (e / e.sum()).astype(np.float32)
            out["softmax_applied_by_postprocess"] = True
        heavy["probs"] = vec
        top = np.argsort(vec)[::-1][:10]
        out["top1"] = int(top[0])
        out["top5"] = [int(i) for i in top[:5]]
        out["top5_scores"] = [float(vec[i]) for i in top[:5]]
    elif a.task == "pose":
        raw = z["output0"]                       # [1, 4+1+51, N]
        kdim = raw.shape[1] - 4 - 1
        dets = _nms(raw, nc=1)
        out["boxes"] = dets[:, :4].tolist()
        out["scores"] = dets[:, 4].tolist()
        out["classes"] = dets[:, 5].astype(int).tolist()
        kpts = dets[:, 6:].reshape(len(dets), -1, 3).astype(np.float32)
        heavy["keypoints"] = kpts
        out["kpt_shape"] = list(kpts.shape)
    elif a.task == "obb":
        raw = z["output0"]                       # [1, 4+1angle+nc, N]
        nc = raw.shape[1] - 5
        dets = _nms(raw, nc, rotated=True)       # (n, 6+1) xywh + angle last
        heavy["obb_xywhr"] = dets[:, :5].astype(np.float32)  # x,y,w,h,r
        out["scores"] = dets[:, 5].tolist()
        out["classes"] = dets[:, 6].astype(int).tolist()
        out["n"] = int(len(dets))
    elif a.task == "semantic":
        arr = z["output0"]                       # [1, H, W] uint8 (argmax in-graph)
        m = arr[0] if arr.ndim == 3 else arr
        heavy["semantic"] = m.astype(np.uint8)
        out["classes_present"] = np.unique(m).astype(int).tolist()
        out["map_shape"] = list(m.shape)
    elif a.task == "depth":
        arr = z["output0"]                       # [1, 1, H, W]
        m = arr.reshape(arr.shape[-2], arr.shape[-1]).astype(np.float32)
        heavy["depth"] = m
        out["depth_stats"] = {"min": float(m.min()), "max": float(m.max()),
                              "mean": float(m.mean())}
        out["map_shape"] = list(m.shape)
    else:
        out["error"] = f"unhandled task {a.task}"

    Path(a.out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out_json).write_text(json.dumps(out), encoding="utf-8")
    np.savez_compressed(a.out_npz, **heavy)
    print(json.dumps({"ok": True, "task": a.task,
                      "n": out.get("n", len(out.get("boxes", [])))}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
