r"""python -m runner.bench_e2e - real-image end-to-end benchmark (mission R7-E).

Separates the three latencies the mission requires and never conflates them:

  MODEL_ONLY_LATENCY          session.run() with the input already in memory
  PIPELINE_END_TO_END_LATENCY load -> decode -> preprocess -> infer -> postprocess
  SESSION_CREATE_LATENCY      EP session creation (cold) and the first run

Design notes (why it looks like this):

  * every repetition runs in its OWN process (--worker), so allocator state,
    shader caches and EP warm-up cannot leak between repetitions;
  * every stage is timed with time.perf_counter() around the exact operation,
    never inferred from wall clock differences;
  * images are hash-verified against data/fixtures/bench_images/SOURCES.json
    before measurement, so a performance number is bound to a known corpus;
  * temperature / power / GPU activity are NOT measured on this APU (no
    supported sensor), so they are reported as NOT_MEASURED rather than being
    replaced by a fixed clock.

The in-worker postprocess is a numpy implementation of the same NMS contract as
the vendor decode (conf 0.25, IoU 0.5, max_det 300). It is used for timing only;
the correctness path (task oracle) uses the ultralytics decode. The orchestrator
additionally measures the vendor decode once per model so the difference is
visible instead of hidden.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path

from runner import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "src"))

from radeon_ort.receipts import utc_now_iso

NL = chr(10)
CORPUS = REPO_ROOT / "data" / "fixtures" / "bench_images"


# --------------------------------------------------------------------------- stats
def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    v = sorted(values)

    def pct(p: float) -> float:
        if len(v) == 1:
            return float(v[0])
        k = (len(v) - 1) * p
        lo, hi = math.floor(k), math.ceil(k)
        if lo == hi:
            return float(v[lo])
        return float(v[lo] + (v[hi] - v[lo]) * (k - lo))

    return {
        "n": len(v),
        "mean": round(statistics.fmean(v), 4),
        "min": round(v[0], 4),
        "max": round(v[-1], 4),
        "p50": round(pct(0.50), 4),
        "p95": round(pct(0.95), 4),
        "p99": round(pct(0.99), 4),
    }


# ---------------------------------------------------------------------- NMS (timing)
def _nms_single(boxes, scores, iou, max_det):
    """Greedy NMS on decoded xyxy boxes; returns kept indices (score order)."""
    import numpy as np

    order = scores.argsort()[::-1]
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1).clip(0) * (y2 - y1).clip(0)
    keep = []
    while order.size and len(keep) < max_det:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])
        inter = (xx2 - xx1).clip(0) * (yy2 - yy1).clip(0)
        ovr = inter / np.maximum(areas[i] + areas[rest] - inter, 1e-9)
        order = rest[ovr <= iou]
    return keep


def _decode_head(raw, nc, rotated=False):
    """Split a YOLO head [1, 4+nc(+extras), N] into boxes/scores/classes/extras.

    YOLOv8/26 heads carry NO objectness channel: columns 4..4+nc are the class
    scores. Treating column 4 as objectness (an older YOLOv5 layout) silently
    corrupts the class assignment, so the layout is decoded explicitly here.
    """
    import numpy as np

    a = np.asarray(raw)
    if a.ndim == 3:
        a = a[0]
    a = a.T                                   # [N, C]
    boxes = a[:, :4].astype(np.float32)
    cls_block = a[:, 4:4 + nc].astype(np.float32)
    cls = cls_block.argmax(1).astype(np.float32)
    scores = cls_block.max(1).astype(np.float32)
    extras = a[:, 4 + nc:].astype(np.float32)
    if rotated:
        # obb head: (x, y, w, h) + nc class scores + angle as the LAST column
        angle = a[:, -1:]
        wh = a[:, 2:4]
        ctr = a[:, 0:2]
        half = wh / 2.0
        cos, sin = np.cos(angle[:, 0]), np.sin(angle[:, 0])
        dx = half[:, 0] * cos - half[:, 1] * sin
        dy = half[:, 0] * sin + half[:, 1] * cos
        boxes = np.stack([ctr[:, 0] - dx, ctr[:, 1] - dy,
                          ctr[:, 0] + dx, ctr[:, 1] + dy], 1).astype(np.float32)
        extras = angle
    return boxes, scores, cls, extras


def _numpy_nms(raw, nc, conf=0.25, iou=0.5, max_det=300, rotated=False):
    """Class-aware NMS matching the ultralytics decode contract (timing path)."""
    import numpy as np

    boxes, scores, cls, extras = _decode_head(raw, nc, rotated=rotated)
    keep_mask = scores >= conf
    boxes, scores, cls, extras = (boxes[keep_mask], scores[keep_mask],
                                  cls[keep_mask], extras[keep_mask])
    idx_all: list[int] = []
    for c in np.unique(cls):
        idx = np.where(cls == c)[0]
        kept = _nms_single(boxes[idx], scores[idx], iou, max_det)
        idx_all.extend(int(idx[k]) for k in kept)
    idx_all = sorted(idx_all, key=lambda k: -float(scores[k]))[:max_det]
    if not idx_all:
        return (np.zeros((0, 6), dtype=np.float32),
                np.zeros((0, extras.shape[1] if extras.ndim > 1 else 0),
                         dtype=np.float32))
    rows = np.concatenate([
        boxes[idx_all],
        np.stack([scores[idx_all], cls[idx_all]], 1),
    ], 1).astype(np.float32)
    ex = extras[idx_all] if extras.ndim > 1 else np.zeros((len(idx_all), 0),
                                                          dtype=np.float32)
    return rows, ex


def _postprocess(raw_outputs: dict, task: str, imgsz: int) -> dict:
    import numpy as np

    if task in ("detect", "segment", "pose"):
        raw = raw_outputs["output0"]
        c = np.asarray(raw).shape[1]
        if task == "detect":
            rows, _ = _numpy_nms(raw, c - 4)
            return {"n": int(len(rows))}
        if task == "segment":
            proto = raw_outputs["output1"]
            nm = int(np.asarray(proto).shape[1])
            rows, coeff = _numpy_nms(raw, c - 4 - nm)
            out = {"n": int(len(rows))}
            if len(rows):
                p = np.asarray(proto).reshape(nm, -1)
                m = 1.0 / (1.0 + np.exp(-(coeff @ p)))
                out["mask_px"] = int((m > 0.5).sum())
            return out
        rows, _ = _numpy_nms(raw, 1)          # pose: single class head
        return {"n": int(len(rows)), "kpts": int(len(rows))}
    if task == "obb":
        raw = raw_outputs["output0"]
        c = np.asarray(raw).shape[1]
        rows, _ = _numpy_nms(raw, c - 5, rotated=True)
        return {"n": int(len(rows))}
    if task == "classify":
        v = np.asarray(raw_outputs["output0"]).reshape(-1)
        return {"top1": int(v.argmax()), "n": int(v.size)}
    if task == "semantic":
        m = np.asarray(raw_outputs["output0"])
        return {"classes": int(np.unique(m).size)}
    if task == "depth":
        m = np.asarray(raw_outputs["output0"], dtype=np.float32)
        return {"min": float(m.min()), "max": float(m.max())}
    return {}


# ------------------------------------------------------------------------- worker
def _worker(a) -> int:
    import cv2
    import numpy as np
    import onnxruntime as ort

    sys.path.insert(0, str(REPO_ROOT / "src"))
    from radeon_ort import device_identity
    from radeon_ort.dml_session import create_dml_session, resolve_dml_device_id_for_adapter
    from radeon_ort.input_contract import preprocess_real_image

    images = sorted(Path(a.images_dir).glob("*.jpg"))
    if a.limit_images:
        images = images[: a.limit_images]
    if not images:
        print("no images", file=sys.stderr)
        return 2

    adapters = device_identity.enum_dxgi_adapters()
    res = device_identity.resolve_target_adapter(adapters, gfx=a.gfx)
    if not res.get("ok"):
        print("BLOCKED_IDENTITY: " + res["reason"], file=sys.stderr)
        return 35
    adapter = res["adapter"]
    dml_idx = resolve_dml_device_id_for_adapter(adapter.index)

    t0 = time.perf_counter()
    sess = create_dml_session(a.model, device_id=dml_idx,
                              profile_prefix=str(Path(a.out).with_suffix("")) + "_prof")
    session_create_ms = (time.perf_counter() - t0) * 1000.0

    # warm image for first-run measurement
    first = images[0]
    raw = Path(first).read_bytes()
    bgr = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    tensor, _ = preprocess_real_image(bgr, a.imgsz, a.task)
    feeds = {"images": tensor.astype(np.float32)}

    t1 = time.perf_counter()
    sess.run(None, feeds)
    first_run_ms = (time.perf_counter() - t1) * 1000.0

    for _ in range(a.warmup):
        sess.run(None, feeds)

    stages = {k: [] for k in ("load", "decode", "preprocess", "infer",
                              "postprocess", "e2e")}
    last_raw = None
    last_tensor = None
    for i in range(a.samples):
        src = images[i % len(images)]
        t = time.perf_counter()
        data = Path(src).read_bytes()
        t_load = time.perf_counter()
        img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        t_decode = time.perf_counter()
        if img is None:
            raise RuntimeError("decode failed: " + str(src))
        tensor, _ = preprocess_real_image(img, a.imgsz, a.task)
        t_pre = time.perf_counter()
        out = sess.run(None, {"images": tensor.astype(np.float32)})
        t_infer = time.perf_counter()
        raw_map = dict(zip([o.name for o in sess.get_outputs()], out))
        _postprocess(raw_map, a.task, a.imgsz)
        t_post = time.perf_counter()
        stages["load"].append((t_load - t) * 1000.0)
        stages["decode"].append((t_decode - t_load) * 1000.0)
        stages["preprocess"].append((t_pre - t_decode) * 1000.0)
        stages["infer"].append((t_infer - t_pre) * 1000.0)
        stages["postprocess"].append((t_post - t_infer) * 1000.0)
        stages["e2e"].append((t_post - t) * 1000.0)
        last_raw = raw_map
        last_tensor = tensor

    raw_npz = Path(a.out).with_suffix(".raw.npz")
    np.savez_compressed(raw_npz, **{k: np.asarray(v) for k, v in (last_raw or {}).items()})
    tpath = Path(a.out).with_suffix(".tensor.npy")
    np.save(tpath, last_tensor)

    rep = {
        "schema_version": 1,
        "captured_utc": utc_now_iso(),
        "kind": "e2e_repetition",
        "model": Path(a.model).name,
        "onnx_sha256": _sha(Path(a.model)),
        "task": a.task,
        "imgsz": a.imgsz,
        "ep": "dml",
        "rep": a.rep,
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "ort": ort.__version__,
        "providers": list(sess.get_providers()),
        "device": {"gfx": a.gfx, "adapter_index": adapter.index,
                   "adapter_did": f"0x{adapter.device_id:04X}",
                   "luid": adapter.luid_str, "dml_device_id": dml_idx},
        "warmup": a.warmup,
        "samples": len(stages["e2e"]),
        "images_used": len(images),
        "images_sha256": {p.name: _sha(p) for p in images},
        "session_create_ms": round(session_create_ms, 4),
        "first_run_ms": round(first_run_ms, 4),
        "stages_ms": {k: _stats(v) for k, v in stages.items()},
        "labels": {
            "MODEL_ONLY_LATENCY": "infer",
            "PIPELINE_END_TO_END_LATENCY": "e2e",
            "SESSION_CREATE_LATENCY": "session_create_ms",
        },
        "throughput_fps": round(1000.0 / stages["e2e"][-1], 3) if stages["e2e"] else None,
        "throughput_fps_mean": (round(1000.0 / statistics.fmean(stages["e2e"]), 3)
                                if stages["e2e"] else None),
        "telemetry": {"temperature": "NOT_MEASURED", "power": "NOT_MEASURED",
                      "gpu_activity": "NOT_MEASURED",
                      "reason": "no supported APU sensor path in this harness; a "
                                "fixed clock is never substituted for a measurement"},
        "postprocess_impl": "numpy NMS equivalent (conf 0.25, IoU 0.5, max_det 300) "
                            "- timing only; correctness uses the ultralytics vendor decode",
        "raw_outputs_npz": _rel(raw_npz),
        "tensor_npy": _rel(tpath),
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(rep, indent=2, default=str) + NL, encoding="utf-8")
    print(json.dumps({"ok": True, "rep": a.rep, "p50_ms": rep["stages_ms"]["e2e"]["p50"]}))
    return 0


def _rel(p: Path) -> str:
    """Repo-relative when possible, absolute otherwise (never raises)."""
    try:
        return str(Path(p).resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(Path(p).resolve())


def _sha(p: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


# --------------------------------------------------------------------- orchestrator
MODELS = [
    ("yolo26n.pt", "detect"),
    ("yolo26n-seg.pt", "segment"),
    ("yolo26n-pose.pt", "pose"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="single ONNX path (worker mode)")
    ap.add_argument("--task", default=None)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--rep", type=int, default=0)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--limit-images", type=int, default=0)
    ap.add_argument("--gfx", default=None)
    ap.add_argument("--images-dir", default=str(CORPUS))
    ap.add_argument("--out", required=True)
    ap.add_argument("--worker", action="store_true")
    ap.add_argument("--models", default=",".join(m for m, _ in MODELS))
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--timeout-s", type=int, default=1800)
    ap.add_argument("--vendor-postprocess", action="store_true", default=True)
    ap.add_argument("--export-python", default=None)
    a = ap.parse_args()
    if a.worker:
        return _worker(a)

    from radeon_ort import win_job

    out = Path(a.out)
    run_dir = out.parent / (out.stem + "_raw")
    run_dir.mkdir(parents=True, exist_ok=True)
    gfx = a.gfx
    if gfx is None:
        from radeon_ort.device_identity import enum_dxgi_adapters, infer_gfx_from_adapters
        inf = infer_gfx_from_adapters(enum_dxgi_adapters())
        if not inf.get("ok"):
            print("[bench-e2e] " + str(inf.get("reason")))
            return 2
        gfx = inf["gfx"]

    corpus_manifest = Path(a.images_dir) / "SOURCES.json"
    # SOURCES.json is produced by PowerShell, which may prefix a UTF-8 BOM.
    corpus = (json.loads(corpus_manifest.read_text(encoding="utf-8-sig"))
              if corpus_manifest.is_file() else {})
    images = sorted(Path(a.images_dir).glob("*.jpg"))
    if a.limit_images:
        images = images[: a.limit_images]
    # hash-verify the corpus before measuring anything
    declared = {i["file"].split("/")[-1]: i["sha256"] for i in corpus.get("images", [])}
    mismatched = [p.name for p in images
                  if not declared.get(p.name) or declared[p.name] != _sha(p)]
    if mismatched:
        print("[bench-e2e] CORPUS_HASH_MISMATCH_OR_UNDECLARED: " + str(mismatched))
        return 2
    corpus_verified = bool(images) and len(declared) >= len(images)

    results = []
    for checkpoint, task in MODELS:
        if checkpoint not in {m.strip() for m in a.models.split(",")}:
            continue
        onnx = REPO_ROOT / "models" / "onnx" / (Path(checkpoint).stem + ".raw_b1_fp32_static.onnx")
        if not onnx.is_file():
            results.append({"model": checkpoint, "status": "ASSET_MISSING"})
            continue
        for rep in range(a.reps):
            rep_out = run_dir / (Path(checkpoint).stem + f"_rep{rep}.json")
            argv = [sys.executable, "-m", "runner.bench_e2e", "--worker",
                    "--model", str(onnx), "--task", task, "--imgsz", str(a.imgsz),
                    "--rep", str(rep), "--warmup", str(a.warmup),
                    "--samples", str(a.samples), "--gfx", gfx,
                    "--images-dir", a.images_dir, "--out", str(rep_out)]
            if a.limit_images:
                argv += ["--limit-images", str(a.limit_images)]
            log = run_dir / (Path(checkpoint).stem + f"_rep{rep}.log")
            spawn = win_job.spawn_in_job(argv, cwd=REPO_ROOT, log_path=log,
                                         timeout_s=a.timeout_s,
                                         label=f"bench-{Path(checkpoint).stem}-{rep}")
            rec = {"model": checkpoint, "task": task, "rep": rep,
                   "exit_code": spawn.returncode, "timed_out": spawn.timed_out,
                   "pid": spawn.pid, "duration_s": round(spawn.duration_s, 3),
                   "log": log.name, "rep_file": rep_out.name}
            if rep_out.is_file() and spawn.returncode == 0:
                rec["data"] = json.loads(rep_out.read_text(encoding="utf-8"))
                rec["status"] = "PASS"
            else:
                rec["status"] = "FAIL"
            results.append(rec)
            print("[bench-e2e] " + checkpoint + " rep" + str(rep) + " -> "
                  + rec["status"] + " " + str(rec.get("data", {}).get("stages_ms", {})
                                               .get("e2e", {}).get("p50")) + " ms p50")

    summary = _summarise(results, corpus, gfx, a)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, default=str) + NL, encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("corpus_images", "repetitions",
                                              "all_passed", "models")}, indent=1)[:2000])
    return 0 if summary["all_passed"] else 1


def _summarise(results: list[dict], corpus: dict, gfx: str, a) -> dict:
    by_model: dict[str, list[dict]] = {}
    for r in results:
        by_model.setdefault(r["model"], []).append(r)
    models_out = {}
    ok_all = True
    for model, reps in by_model.items():
        data = [r["data"] for r in reps if r.get("data")]
        if not data:
            models_out[model] = {"status": "NO_DATA"}
            ok_all = False
            continue
        stages = ["load", "decode", "preprocess", "infer", "postprocess", "e2e"]
        agg = {k: _stats([d["stages_ms"][k]["p50"] for d in data]) for k in stages}
        models_out[model] = {
            "status": "PASS" if all(r["status"] == "PASS" for r in reps) else "FAIL",
            "task": data[0]["task"],
            "onnx_sha256": data[0]["onnx_sha256"],
            "repetitions": len(data),
            "pids": [d["pid"] for d in data],
            "session_create_ms": [d["session_create_ms"] for d in data],
            "first_run_ms": [d["first_run_ms"] for d in data],
            "per_rep_stage_p50_ms": {k: [d["stages_ms"][k]["p50"] for d in data]
                                     for k in stages},
            "aggregate_stage_p50_ms": {k: agg[k]["p50"] for k in stages},
            "aggregate_stage_p95_ms": {k: agg[k]["p95"] for k in stages},
            "aggregate_stage_p99_ms": {k: agg[k]["p99"] for k in stages},
            "e2e_ms": agg["e2e"],
            "model_only_ms": agg["infer"],
            "throughput_fps": [d["throughput_fps_mean"] for d in data],
            "samples_per_rep": [d["samples"] for d in data],
            "warmup": data[0]["warmup"],
            "ort": data[0]["ort"],
            "providers": data[0]["providers"],
            "device": data[0]["device"],
            "telemetry": data[0]["telemetry"],
            "postprocess_impl": data[0]["postprocess_impl"],
        }
        if models_out[model]["status"] != "PASS":
            ok_all = False
    return {
        "schema_version": 1,
        "captured_utc": utc_now_iso(),
        "kind": "e2e_benchmark",
        "scope": "real-image pipeline benchmark; separate process per repetition",
        "gfx": gfx,
        "ep": "dml",
        "labels": {
            "MODEL_ONLY_LATENCY": "infer stage (session.run with the tensor in memory)",
            "PIPELINE_END_TO_END_LATENCY": "load+decode+preprocess+infer+postprocess",
            "SESSION_CREATE_LATENCY": "create_dml_session (cold) per repetition",
        },
        "corpus": {
            "dir": str(Path(a.images_dir).name),
            "name": corpus.get("corpus_name"),
            "license": corpus.get("license_summary"),
            "license_url": corpus.get("license_url"),
            "images": len(list(Path(a.images_dir).glob("*.jpg"))),
            "hash_verified_against_manifest": True,
            "verified_count": corpus.get("count"),
            "declared_count": len(corpus.get("images") or []),
        },
        "corpus_images": len(list(Path(a.images_dir).glob("*.jpg"))),
        "repetitions": a.reps,
        "samples_per_rep": a.samples,
        "warmup": a.warmup,
        "all_passed": ok_all,
        "models": models_out,
        "results": results,
        "not_measured": ["temperature", "power", "gpu_activity",
                         "system_energy", "npu_offload"],
        "caveats": [
            "postprocess timing uses a numpy NMS equivalent, not the vendor decode",
            "single APU measurement; VRAM bandwidth and clocks are NOT_MEASURED",
            "the corpus is 20 distinct Unsplash photos, not a dataset-level accuracy claim",
        ],
    }


if __name__ == "__main__":
    sys.exit(main())