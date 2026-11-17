"""Task parity comparator — GPU postprocessed vs PT reference (mission R1-D).

Executed in the export venv. Compares, per task, the EP postprocessed result
(onnx_postprocess.py output) against the PT oracle (pt_oracle_v2.py output),
both on the SAME preprocessed input tensor. Metrics and PASS thresholds are
predeclared (configs/thresholds.yaml, fixed before physical runs; passed here
as JSON because the export venv has no pyyaml).

Verdict semantics:
  passed=true                       -> PASS_TASK_PARITY evidence
  passed=false                      -> FAIL_TASK_PARITY (metrics kept)
  not_implemented_reason set        -> TASK_PARITY_NOT_IMPLEMENTED / BLOCKED

PT_APP_SMOKE is a different class of evidence and never passes through here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

# COCO 17-keypoint sigmas (COCO official; ultralytics uses the same in OKS)
COCO_SIGMAS = np.array([.26, .25, .25, .35, .35, .79, .79, .72, .72, .62,
                        .62, 1.07, 1.07, .87, .87, .89, .85], dtype=np.float64)

# task name -> configs/thresholds.yaml task_oracle.* key
YAML_TASK_KEY = {"detect": "detection", "segment": "segmentation", "semantic": "semantic",
                 "depth": "depth", "classify": "classify", "pose": "pose", "obb": "obb"}


def _iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(ix2 - ix1, 0) * max(iy2 - iy1, 0)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return float(inter / ua) if ua > 0 else 0.0


def _match_boxes(pred, ref, iou_thres):
    """Greedy class-aware matching by score; returns matches/FP/FN + mean IoU."""
    used, matches = set(), []
    for p in sorted(pred, key=lambda d: -d["score"]):
        best, best_iou = None, iou_thres
        for j, r in enumerate(ref):
            if j in used or r["cls"] != p["cls"]:
                continue
            v = _iou(p["box"], r["box"])
            if v >= best_iou:
                best, best_iou = j, v
        if best is not None:
            used.add(best)
            matches.append({"pred": p, "ref": ref[best], "iou": best_iou})
    fp = len([p for p in pred if p not in [m["pred"] for m in matches]])
    fn = len(ref) - len(used)
    prec = len(matches) / (len(matches) + fp) if (len(matches) + fp) else 1.0
    rec = len(matches) / (len(matches) + fn) if (len(matches) + fn) else 1.0
    return {"matches": matches, "fp": fp, "fn": fn, "precision": prec,
            "recall": rec, "iou_mean": float(np.mean([m["iou"] for m in matches]))
            if matches else float("nan")}


def _dets_from(j: dict) -> list[dict]:
    return [{"box": b, "score": s, "cls": c}
            for b, s, c in zip(j.get("boxes", []), j.get("scores", []),
                               j.get("classes", []))]


def _mask_iou(m1, m2) -> float:
    inter = np.logical_and(m1, m2).sum()
    union = np.logical_or(m1, m2).sum()
    return float(inter / union) if union else 1.0


def _rotated_iou(a, b) -> float:
    """Rotated-box IoU (xywhr) via cv2 (export venv)."""
    import cv2
    def rect(x):
        return ((float(x[0]), float(x[1])), (float(x[2]), float(x[3])), float(x[4]))
    try:
        ret, pts = cv2.rotatedRectangleIntersection(rect(a), rect(b))
    except cv2.error:
        return 0.0
    if ret == cv2.INTERSECT_NONE or pts is None:
        return 0.0
    inter = cv2.contourArea(pts.reshape(-1, 1, 2))
    a1, a2 = a[2] * a[3], b[2] * b[3]
    return float(inter / (a1 + a2 - inter)) if (a1 + a2 - inter) > 0 else 0.0


def _fingerprint(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str)
                          .encode()).hexdigest()[:32]


def compare(pt_json, pt_npz, cand_json, cand_npz, task: str, th: dict,
            imgsz: int) -> dict:
    """Returns the task_parity evidence block (without RCA/identity fields)."""
    metrics: dict = {"ref_entities": 0}
    passed = False
    reason = None
    t = ((th or {}).get("task_oracle") or {}).get(YAML_TASK_KEY.get(task, task), {})

    if task in ("detect", "segment", "pose"):
        pred, ref = _dets_from(cand_json), _dets_from(pt_json)
        m = _match_boxes(pred, ref, t.get("iou_thres", 0.5))
        metrics.update({"matched": len(m["matches"]), "fp": m["fp"], "fn": m["fn"],
                        "precision": m["precision"], "recall": m["recall"],
                        "iou_mean": m["iou_mean"], "ref_entities": len(ref)})
        ok_boxes = (m["precision"] >= t.get("min_precision", 0.90)
                    and m["recall"] >= t.get("min_recall", 0.90)
                    and (len(ref) == 0 or len(m["matches"]) >= t.get("min_matches", 1)))
        if len(ref) == 0 and len(pred) == 0:
            passed = True          # expected-empty agreement (needs negative control)
            reason = "both-empty agreement"
        elif task == "detect":
            passed = bool(ok_boxes)
        elif task == "segment":
            if ok_boxes and m["matches"]:
                pm, rm = pt_npz["masks"], cand_npz["masks"]
                ious = []
                for mm in m["matches"]:
                    i = _dets_from(cand_json).index(mm["pred"])
                    j = _dets_from(pt_json).index(mm["ref"])
                    if i < len(pm) and j < len(rm) and pm[i].shape == rm[j].shape:
                        ious.append(_mask_iou(pm[i] > 0, rm[j] > 0))
                metrics["mask_iou_mean"] = float(np.mean(ious)) if ious else float("nan")
                passed = metrics["mask_iou_mean"] >= t.get("min_mask_iou", 0.70)
            else:
                metrics["mask_iou_mean"] = float("nan")
        elif task == "pose":
            if ok_boxes and m["matches"]:
                pk, ck = pt_npz["keypoints"], cand_npz["keypoints"]
                oks_list = []
                for mm in m["matches"]:
                    i = _dets_from(cand_json).index(mm["pred"])
                    j = _dets_from(pt_json).index(mm["ref"])
                    if i < len(ck) and j < len(pk) and pk.shape[1:] == ck.shape[1:]:
                        kp, kr = ck[i], pk[j]
                        vis = kr[:, 2] > 0
                        if vis.sum():
                            d = ((kp[:, :2] - kr[:, :2]) ** 2).sum(1)
                            s = 2 * (imgsz * imgsz)
                            oks = np.exp(-d[vis] / (2 * s * COCO_SIGMAS[vis] ** 2))
                            oks_list.append(float(oks.mean()))
                metrics["oks_mean"] = float(np.mean(oks_list)) if oks_list else float("nan")
                passed = metrics["oks_mean"] >= t.get("min_oks", 0.80)
            else:
                metrics["oks_mean"] = float("nan")
    elif task == "classify":
        pv, cv = pt_npz["probs"].reshape(-1), cand_npz["probs"].reshape(-1)
        metrics.update({
            "ref_top1": int(pt_json["top1"]), "cand_top1": int(cand_json["top1"]),
            "top1_match": int(pt_json["top1"]) == int(cand_json["top1"]),
            "top5_overlap": len(set(pt_json["top5"]) & set(cand_json["top5"])),
            "score_drift": float(np.abs(pv - cv).max())})
        passed = (metrics["top1_match"]
                  if t.get("require_top1_match", True) else True)
    elif task == "obb":
        pr = cand_npz["obb_xywhr"].reshape(-1, 5)
        rr = pt_npz["obb_xywhr"].reshape(-1, 5)
        metrics["ref_entities"] = int(len(rr))
        if len(rr) == 0 and len(pr) == 0:
            passed, reason = True, "both-empty agreement"
        else:
            # pair by ROTATED IoU (xywhr); class-aware, greedy by score
            pmeta = list(zip(cand_json.get("scores", []), cand_json.get("classes", [])))
            rmeta = list(zip(pt_json.get("scores", []), pt_json.get("classes", [])))
            used, matches = set(), []
            for i in sorted(range(len(pr)), key=lambda k: -(pmeta[k][0] if k < len(pmeta) else 0)):
                if i >= len(pmeta):
                    continue
                s, c = pmeta[i]
                best, best_ri = None, t.get("iou_thres", 0.5)
                for j in range(len(rr)):
                    if j in used or j >= len(rmeta) or rmeta[j][1] != c:
                        continue
                    ri = _rotated_iou(pr[i], rr[j])
                    if ri >= best_ri:
                        best, best_ri = j, ri
                if best is not None:
                    used.add(best)
                    matches.append((i, best, best_ri))
            fp = len(pr) - len(matches)
            fn = len(rr) - len(used)
            rious = [ri for (_, _, ri) in matches]
            angles = [abs(float(pr[i][4] - rr[j][4])) * 180.0 / np.pi
                      for (i, j, _) in matches]
            prec = len(matches) / (len(matches) + fp) if (len(matches) + fp) else 1.0
            rec = len(matches) / (len(matches) + fn) if (len(matches) + fn) else 1.0
            metrics.update({"matched": len(matches), "fp": fp, "fn": fn,
                            "precision": prec, "recall": rec,
                            "rotated_iou_mean": float(np.mean(rious)) if rious else None,
                            "angle_absdiff_mean_deg": float(np.mean(angles)) if angles else None})
            passed = bool(prec >= t.get("min_precision", 0.90)
                          and rec >= t.get("min_recall", 0.90)
                          and (not rious or metrics["rotated_iou_mean"] >= t.get("min_rotated_iou", 0.70)))
    elif task == "semantic":
        pm = pt_npz["semantic"].astype(np.int64)
        cm = cand_npz["semantic"].astype(np.int64)
        if pm.shape != cm.shape:
            passed, reason = False, f"map shape mismatch {pm.shape} vs {cm.shape}"
            metrics["map_shape_mismatch"] = [list(pm.shape), list(cm.shape)]
        else:
            agree = float((pm == cm).mean())
            classes = [c for c in np.unique(pm) if c != 0]
            ious = []
            for c in classes:
                ious.append(_mask_iou(cm == c, pm == c))
            miou = float(np.mean(ious)) if ious else 1.0
            metrics.update({"pixel_agreement": agree, "miou": miou,
                            "ref_entities": len(classes), "classes_ref": [int(c) for c in classes]})
            passed = (agree >= t.get("min_pixel_agreement", 0.90)
                      and miou >= t.get("min_miou", 0.70))
    elif task == "depth":
        pd = pt_npz["depth"].astype(np.float64)
        cd = cand_npz["depth"].astype(np.float64)
        if pd.shape != cd.shape:
            passed, reason = False, f"map shape mismatch {pd.shape} vs {cd.shape}"
        else:
            valid = np.isfinite(pd) & np.isfinite(cd) & (pd > 0)
            d = cd[valid] - pd[valid]
            mae = float(np.abs(d).mean()) if d.size else float("nan")
            rel = mae / float(pd[valid].mean()) if d.size else float("nan")
            metrics.update({"mae": mae, "rmse": float(np.sqrt((d ** 2).mean())) if d.size else float("nan"),
                            "rel_mae": rel, "valid_px": int(valid.sum()),
                            "ref_mean_depth": float(pd[valid].mean()) if d.size else float("nan")})
            passed = rel <= t.get("max_rel_mae_vs_pt", 0.15)
    else:
        return {"oracle_kind": "TASK_PARITY", "task": task, "passed": False,
                "metrics": metrics, "thresholds_declared": t,
                "not_implemented_reason": f"no comparator for task {task}"}

    # JSON-safe metrics: NaN/Inf -> null (a metric that cannot be computed is
    # absent, never a forged number; receipts dump with allow_nan=False)
    metrics = {k: (None if isinstance(v, float) and not np.isfinite(v) else v)
               for k, v in metrics.items()}
    block = {
        "oracle_kind": "TASK_PARITY",
        "task": task,
        "ref_source": "PT_ULTRALYTICS",
        "cand_source": "EP_POST",
        "ref_fingerprint_sha256": _fingerprint(
            {k: v for k, v in pt_json.items() if k != "mask_shape"}),
        "cand_fingerprint_sha256": _fingerprint(
            {k: v for k, v in cand_json.items() if k != "mask_shape"}),
        "metrics": metrics,
        "thresholds_declared": t,
        "thresholds_source": "configs/thresholds.yaml",
        "passed": bool(passed),
        "not_implemented_reason": reason,
    }
    return block


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pt-json", required=True)
    ap.add_argument("--pt-npz", required=True)
    ap.add_argument("--cand-json", required=True)
    ap.add_argument("--cand-npz", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--imgsz", type=int, required=True)
    ap.add_argument("--thresholds-json", required=True)
    ap.add_argument("--cand-source", default="EP_POST")
    ap.add_argument("--image-sha256", default=None)
    ap.add_argument("--input-id", default=None)
    ap.add_argument("--negative", action="store_true",
                    help="mark this comparison as the negative-control run")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    pt_json = json.loads(Path(a.pt_json).read_text(encoding="utf-8"))
    cand_json = json.loads(Path(a.cand_json).read_text(encoding="utf-8"))
    th_all = json.loads(Path(a.thresholds_json).read_text(encoding="utf-8"))
    th = {"task_oracle": th_all.get("task_oracle", {}),
          "numeric_parity": th_all.get("numeric_parity", {})}
    block = compare(pt_json, np.load(a.pt_npz), cand_json, np.load(a.cand_npz),
                    a.task, th, a.imgsz)
    block["cand_source"] = a.cand_source
    if a.image_sha256:
        block["image_sha256"] = a.image_sha256
    if a.input_id:
        block["input_id"] = a.input_id
    if a.negative:
        ref_empty = block["metrics"].get("ref_entities", 0) == 0
        cand_empty = block["metrics"].get("matched", block["metrics"].get("ref_entities", 0)) == 0 \
            and block["metrics"].get("fp", 0) == 0
        block["negative_control"] = {
            "image_sha256": a.image_sha256,
            "ref_empty": bool(ref_empty),
            "cand_empty": bool(cand_empty),
            "discriminating": bool(ref_empty and cand_empty and block["passed"]),
        }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(block), encoding="utf-8")
    print(json.dumps({"ok": True, "task": a.task, "passed": block["passed"],
                      "metrics_keys": sorted(block["metrics"].keys())}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
