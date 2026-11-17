"""Task-level oracles: metric functions + PT-application oracle orchestration.

Oracle 1 (CPU ORT numeric parity) lives in cpu_reference.compare_outputs.
Oracle 2 (PT application semantics) runs in the export venv via run_pt_oracle.
Metric implementations here are pure numpy, unit-tested, threshold-driven from
configs/thresholds.yaml (fixed BEFORE seeing GPU outputs).
"""

from __future__ import annotations

import numpy as np


def iou_xyxy(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(ix2 - ix1, 0) * max(iy2 - iy1, 0)
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return float(inter / ua) if ua > 0 else 0.0


def detection_match(pred: list[dict], ref: list[dict], *, iou_thres: float = 0.5,
                    score_thres: float = 0.25) -> dict:
    """Greedy IoU+class matching; returns precision/recall/matched pairs."""
    tp, fp, fn = 0, 0, 0
    used = set()
    matches = []
    for p in sorted(pred, key=lambda d: -d["score"]):
        if p["score"] < score_thres:
            continue
        best, best_iou = None, iou_thres
        for j, r in enumerate(ref):
            if j in used or r["cls"] != p["cls"]:
                continue
            v = iou_xyxy(p["box"], r["box"])
            if v >= best_iou:
                best, best_iou = j, v
        if best is not None:
            used.add(best)
            tp += 1
            matches.append({"pred": p, "ref": ref[best], "iou": best_iou})
        else:
            fp += 1
    fn = len([r for j, r in enumerate(ref) if j not in used])
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": prec, "recall": rec, "matches": matches}


def mask_iou(m1: np.ndarray, m2: np.ndarray) -> float:
    inter = np.logical_and(m1, m2).sum()
    union = np.logical_or(m1, m2).sum()
    return float(inter / union) if union else 1.0


def semantic_miou(pred: np.ndarray, gt: np.ndarray, num_classes: int) -> float:
    ious = []
    for c in range(num_classes):
        p, g = pred == c, gt == c
        if g.sum() == 0 and p.sum() == 0:
            continue
        ious.append(mask_iou(p, g))
    return float(np.mean(ious)) if ious else float("nan")


def depth_errors(pred: np.ndarray, ref: np.ndarray, valid: np.ndarray | None = None) -> dict:
    if valid is None:
        valid = np.isfinite(ref) & np.isfinite(pred) & (ref > 0)
    d = pred[valid] - ref[valid]
    return {"mae": float(np.abs(d).mean()) if d.size else float("nan"),
            "rmse": float(np.sqrt((d ** 2).mean())) if d.size else float("nan"),
            "valid_px": int(valid.sum())}


def pose_keypoint_oks(pred_kpts, ref_kpts, *, per_kpt_sigma=None, imgsz=640) -> float:
    pred_kpts = np.asarray(pred_kpts, dtype=np.float64)
    ref_kpts = np.asarray(ref_kpts, dtype=np.float64)
    if per_kpt_sigma is None:
        per_kpt_sigma = np.full(pred_kpts.shape[0], 0.053)
    s = 2 * imgsz * imgsz  # object scale proxy when bbox unavailable
    dist = ((pred_kpts[:, :2] - ref_kpts[:, :2]) ** 2).sum(1)
    vis = ref_kpts[:, 2] > 0 if ref_kpts.shape[1] > 2 else np.ones(len(ref_kpts), bool)
    if vis.sum() == 0:
        return float("nan")
    oks = np.exp(-dist[vis] / (2 * s * per_kpt_sigma[vis] ** 2))
    return float(oks.mean())


def obb_rotated_iou(poly1: np.ndarray, poly2: np.ndarray) -> float:
    """Rotated-box IoU via cv2.rotatedRectangleIntersection when available."""
    import cv2
    r1 = cv2.minAreaRect(poly1.reshape(-1, 1, 2).astype(np.float32))
    r2 = cv2.minAreaRect(poly2.reshape(-1, 1, 2).astype(np.float32))
    try:
        ret, pts = cv2.rotatedRectangleIntersection(r1, r2)
    except cv2.error:
        return 0.0
    if ret == cv2.INTERSECT_NONE or pts is None:
        return 0.0
    inter = cv2.contourArea(pts.reshape(-1, 1, 2))
    a1, a2 = r1[1][0] * r1[1][1], r2[1][0] * r2[1][1]
    return float(inter / (a1 + a2 - inter)) if (a1 + a2 - inter) > 0 else 0.0


PT_ORACLE_SCRIPT = r'''
import argparse, json
import numpy as np

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pt", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--imgsz", type=int, default=None)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    from ultralytics import YOLO
    kw = {"conf": a.conf, "verbose": False}
    if a.imgsz:
        kw["imgsz"] = a.imgsz
    m = YOLO(a.pt)
    r = m.predict(a.image, **kw)[0]
    def arr(x):
        return np.asarray(x).tolist() if x is not None else None
    out = {
        "task": getattr(r, "task", m.task),
        "imgsz_used": int(getattr(r, "orig_shape", (0, 0))[0]) if not a.imgsz else a.imgsz,
        "boxes": arr(getattr(r, "boxes", None).xyxy.cpu().numpy() if getattr(r, "boxes", None) is not None else None),
        "scores": arr(getattr(r, "boxes", None).conf.cpu().numpy() if getattr(r, "boxes", None) is not None else None),
        "classes": arr(getattr(r, "boxes", None).cls.cpu().numpy().astype(int) if getattr(r, "boxes", None) is not None else None),
        "probs": arr(getattr(r, "probs", None).data.cpu().numpy() if getattr(r, "probs", None) is not None else None),
        "keypoints": arr(getattr(r, "keypoints", None).data.cpu().numpy() if getattr(r, "keypoints", None) is not None else None),
        "masks_present": getattr(r, "masks", None) is not None,
    }
    with open(a.out, "w") as f:
        json.dump(out, f)
    print(json.dumps({"ok": True, "task": out["task"]}))

if __name__ == "__main__":
    main()
'''


def run_pt_oracle(export_python: str, pt_path: str, image: str, *,
                  imgsz: int | None = None, conf: float = 0.25,
                  out_json: str, timeout_s: int = 900) -> dict:
    import subprocess
    cmd = [export_python, "-I", "-c", PT_ORACLE_SCRIPT, "--pt", pt_path, "--image", image,
           "--conf", str(conf), "--out", out_json]
    if imgsz:
        cmd += ["--imgsz", str(imgsz)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s,
                       encoding="utf-8", errors="replace")
    payload = None
    if __import__("pathlib").Path(out_json).is_file():
        import json
        payload = json.loads(__import__("pathlib").Path(out_json).read_text(encoding="utf-8"))
    return {"returncode": r.returncode, "payload": payload,
            "stdout_tail": (r.stdout or "")[-2000:], "stderr_tail": (r.stderr or "")[-2000:]}
