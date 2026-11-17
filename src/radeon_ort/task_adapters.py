"""Per-task preprocessing/postprocessing adapters matched to each model's output contract.

Output contracts differ by nms_mode:
- raw detect: [1,84,8400] style (concat) -> decode + NMS needed
- embedded_nms: already NMS'd boxes
- nms_free_head: end2end outputs, scores only
- classify: [1, num_classes] logits
- segment: proto + coeffs after decode
- pose: kpt branches; obb: rotated boxes; depth: dense map; semantic: per-pixel classes
"""

from __future__ import annotations

import numpy as np


def letterbox_image(img: np.ndarray, out_sz: int, pad_color: int = 114) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """Classic YOLO letterbox: resize keeping AR, pad to square. Returns (tensor-ready HWC, params)."""
    import cv2
    h, w = img.shape[:2]
    r = min(out_sz / h, out_sz / w)
    nh, nw = round(h * r), round(w * r)
    dh, dw = out_sz - nh, out_sz - nw
    top, bottom = dh // 2, dh - dh // 2
    left, right = dw // 2, dw - dw // 2
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    out = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(pad_color,) * 3)
    return out, (r, top, left)


def preprocess_bgr(img: np.ndarray, imgsz: int) -> np.ndarray:
    """BGR uint8 HWC -> RGB float32 NCHW normalized 0..1."""
    out, _ = letterbox_image(img, imgsz)
    out = cv2_rgb(out)
    x = out.astype(np.float32) / 255.0
    x = x.transpose(2, 0, 1)[None]
    return np.ascontiguousarray(x)


def cv2_rgb(img: np.ndarray) -> np.ndarray:
    import cv2
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def decode_raw_detect(output: np.ndarray, conf_thres: float = 0.25, iou_thres: float = 0.45,
                      num_anchors_axis: int = 2) -> list[dict]:
    """Decode raw detection output [1, 4+nc, N] (axis 2 = anchors) to boxes.

    Returns [{'box': [x1,y1,x2,y2], 'score', 'cls'}...] in input-image pixels.
    """
    pred = output[0] if output.ndim == 3 else output
    if pred.ndim == 2 and pred.shape[0] < pred.shape[1] and pred.shape[1] > 100:
        # raw contract [4+nc, N] -> [N, 4+nc]
        pred = pred.T
    boxes = pred[:, :4]
    scores_all = pred[:, 4:]
    cls_ids = scores_all.argmax(1)
    confs = scores_all.max(1)
    keep = confs > conf_thres
    boxes, confs, cls_ids = boxes[keep], confs[keep], cls_ids[keep]
    # cxcywh -> xyxy
    xy = np.empty_like(boxes)
    xy[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
    xy[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
    xy[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
    xy[:, 3] = boxes[:, 1] + boxes[:, 3] / 2
    final = _nms(xy, confs, iou_thres)
    return [{"box": xy[i].tolist(), "score": float(confs[i]), "cls": int(cls_ids[i])}
            for i in final]


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_thres: float) -> list[int]:
    return _nms_numpy(boxes, scores, iou_thres)


def _nms_numpy(boxes: np.ndarray, scores: np.ndarray, iou_thres: float) -> list[int]:
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(x2 - x1, 0) * np.maximum(y2 - y1, 0)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(xx2 - xx1, 0) * np.maximum(yy2 - yy1, 0)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= iou_thres]
    return keep


def decode_segment(raw_out: np.ndarray, proto: np.ndarray, num_classes: int = 80,
                   conf_thres: float = 0.25) -> list[dict]:
    """Decode raw segment output: pred[:, :4] box, [:, 4:4+nc] class, tail = mask coeffs."""
    dets = decode_raw_detect(raw_out, conf_thres=conf_thres)
    pred = raw_out[0] if raw_out.ndim == 3 else raw_out
    if pred.ndim == 2 and pred.shape[0] < pred.shape[1]:
        pred = pred.T
    ncoeff = proto.shape[0] if proto.ndim == 2 else proto.shape[1]
    coeffs_all = pred[:, 4 + num_classes: 4 + num_classes + ncoeff]
    for j, d in enumerate(dets):
        if j < coeffs_all.shape[0]:
            d["mask_coeffs"] = coeffs_all[j].tolist()
    return dets


def softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max())
    return e / e.sum()


def classify_topk(logits: np.ndarray, k: int = 5) -> dict:
    p = softmax(np.asarray(logits).reshape(-1))
    idx = p.argsort()[::-1][:k]
    return {"top1": int(idx[0]), "topk": [int(i) for i in idx], "probs_topk": [float(p[i]) for i in idx]}
