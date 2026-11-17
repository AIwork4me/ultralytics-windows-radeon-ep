"""python -m runner.generate_catalog — freeze the official Ultralytics model catalog.

Requires network once; writes catalog/upstream_snapshot.json (pinned) and
catalog/model_catalog.yaml + .csv. Manual review happens in a separate diff.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.request
from pathlib import Path

from runner import REPO_ROOT

# Frozen snapshot reference: pin the ultralytics release we validate against.
# 8.3.0+ is required for YOLO26-era API (nms=, quantize= export args).
SNAPSHOT_TEMPLATE = {
    "schema_version": 1,
    "captured_utc": None,
    "ultralytics_release": None,
    "ultralytics_commit_sha": None,
    "docs_models_url": "https://docs.ultralytics.com/models/",
    "docs_onnx_url": "https://docs.ultralytics.com/integrations/onnx/",
    "docs_access_utc": None,
}

# Task-default imgsz per official docs (query model metadata at export time too).
TASK_DEFAULT_IMGSZ = {
    "detect": 640, "segment": 640, "classify": 224, "pose": 640, "obb": 1024,
    "depth": 768, "semantic": 640,
}

# Official checkpoint suffix per task (ultralytics naming; detect has no suffix).
TASK_SUFFIX = {
    "detect": "", "segment": "-seg", "classify": "-cls", "pose": "-pose",
    "obb": "-obb", "depth": "-depth", "semantic": "-semantic",
}


def ckpt_name(stem: str, task: str) -> str:
    return f"{stem}{TASK_SUFFIX[task]}.pt"

YOLO26_SIZES = ["n", "s", "m", "l", "x", "k", "kx"]  # 2026-11 official families incl. K-series
CLASSIC_SIZES = ["n", "s", "m", "l", "x"]


def _entry(family, task, ckpt, tier, *, size=None, access="public",
           export_capability="expected", eligibility="eligible", reason=None,
           gpu_effort="BASELINE_REQUIRED", owner=None, required_components=None):
    return {
        "family": family, "task": task, "checkpoint": ckpt, "size": size,
        "dataset_head": None, "source_url": None, "access": access, "license": "AGPL-3.0 (Ultralytics)",
        "export_capability": export_capability, "required_components": required_components or [],
        "imgsz_default": TASK_DEFAULT_IMGSZ.get(task), "opset_default": 17,
        "nms_mode": "raw", "quantize": "fp32", "batch": 1, "dynamic": False,
        "expected_outputs": [], "oracle_policy": "cpu_ort+pt_app",
        "gpu_effort": gpu_effort, "tier": tier, "eligibility": eligibility,
        "eligibility_reason": reason, "owner": owner,
    }


def build_catalog(tier_a_checkpoints: list[str]) -> list[dict]:
    entries: list[dict] = []

    # YOLO26 — the 2026 flagship: seven tasks
    for task in ("detect", "segment", "semantic", "depth", "classify", "pose", "obb"):
        for sz in YOLO26_SIZES:
            ckpt = ckpt_name(f"yolo26{sz}", task)
            tier = "A" if ckpt in tier_a_checkpoints else "B"
            effort = "BASELINE_REQUIRED" if sz in ("n", "s") else "RESOURCE_GATED"
            entries.append(_entry("YOLO26", task, ckpt, tier, size=sz, gpu_effort=effort))

    # YOLO11 / YOLOv8
    for fam, sizes in (("YOLO11", CLASSIC_SIZES), ("YOLOv8", CLASSIC_SIZES)):
        for task in ("detect", "segment", "classify", "pose", "obb"):
            for sz in sizes:
                stem = "yolo11" if fam == "YOLO11" else "yolov8"
                ckpt = ckpt_name(f"{stem}{sz}", task)
                tier = "A" if ckpt in tier_a_checkpoints else "B"
                effort = "BASELINE_REQUIRED" if sz in ("n", "s") else "RESOURCE_GATED"
                entries.append(_entry(fam, task, ckpt, tier, size=sz, gpu_effort=effort))

    # YOLO12 — detection checkpoints only in official state (2026-11)
    for sz in CLASSIC_SIZES:
        ckpt = f"yolo12{sz}.pt"
        tier = "A" if ckpt in tier_a_checkpoints else "B"
        entries.append(_entry("YOLO12", "detect", ckpt, tier, size=sz,
                              gpu_effort="BASELINE_REQUIRED" if sz in ("n", "s") else "RESOURCE_GATED"))
    entries.append(_entry("YOLO12", "segment", "yolo12s-seg.pt", "B", size="s",
                          eligibility="asset_blocked",
                          reason="no official segment checkpoint published for YOLO12 (docs 2026-11); verify live",
                          gpu_effort="NOT_APPLICABLE"))

    # YOLOv10 / YOLOv9 detect (+v9 seg)
    for fam, tasks in (("YOLOv10", ("detect",)), ("YOLOv9", ("detect", "segment"))):
        for task in tasks:
            for sz in CLASSIC_SIZES:
                stem = "yolov10" if fam == "YOLOv10" else "yolov9"
                entries.append(_entry(fam, task, ckpt_name(f"{stem}{sz}", task), "B", size=sz,
                                      gpu_effort="BASELINE_REQUIRED" if sz in ("n", "s") else "RESOURCE_GATED"))

    # Legacy: YOLOv7 (predict-compatible ONNX), YOLOv5u, YOLOv3u
    entries.append(_entry("YOLOv7", "detect", "yolov7.pt", "B", export_capability="expected",
                          eligibility="asset_blocked",
                          reason="native PT import unsupported in ultralytics; ONNX from third-party export would lack provenance"))
    for sz in CLASSIC_SIZES:
        entries.append(_entry("YOLOv5u", "detect", f"yolov5{sz}u.pt", "B", size=sz,
                              gpu_effort="OPTIONAL_STRESS"))
    entries.append(_entry("YOLOv3u", "detect", "yolov3u.pt", "B", gpu_effort="OPTIONAL_STRESS"))
    # YOLOv6: no Ultralytics-hosted PT weights
    entries.append(_entry("YOLOv6", "detect", "yolov6n.pt", "B", eligibility="asset_blocked",
                          reason="no Ultralytics-hosted PT weights (docs); third-party hosting not licensed/verified",
                          gpu_effort="NOT_APPLICABLE"))

    # RT-DETR / YOLO-NAS
    for ckpt in ("rtdetr-l.pt", "rtdetr-x.pt", "rtdetr-resnet50.pt", "rtdetr-resnet101.pt"):
        entries.append(_entry("RT-DETR", "detect", ckpt, "B",
                              gpu_effort="RESOURCE_GATED" if "r" in ckpt else "OPTIONAL_STRESS"))
    entries.append(_entry("YOLO-NAS", "detect", "yolo_nas_s.pt", "B", eligibility="asset_blocked",
                          reason="requires super-gradients package; Ultralytics export path not official",
                          gpu_effort="NOT_APPLICABLE"))

    # Promptable / open-vocab
    entries.append(_entry("YOLOE", "detect", "yoloe-v8s-seg.pt", "B", export_capability="multi_component",
                          required_components=["image encoder", "text/visual prompt encoder", "decoder"],
                          gpu_effort="OPTIONAL_STRESS"))
    entries.append(_entry("YOLO-World", "detect", "yolov8s-worldv2.pt", "B",
                          export_capability="multi_component",
                          required_components=["text encoder (CLIP)", "detector"],
                          gpu_effort="OPTIONAL_STRESS"))

    # SAM family — PREDICT_ONLY, HF gating varies
    for fam, ckpt, access, reason in (
        ("SAM3", "sam3.pt", "gated_hf", "SAM3.1/SAM3 weights behind HF license/access; multi-component export"),
        ("SAM2", "sam2.1_b.pt", "public", None),
        ("SAM", "sam_b.pt", "public", None),
        ("MobileSAM", "mobile_sam.pt", "public", None),
        ("FastSAM", "FastSAM-s.pt", "public", None),
    ):
        entries.append(_entry(fam, "segment", ckpt, "B", access=access,
                              export_capability="predict_only",
                              eligibility="eligible" if access == "public" else "license_restricted",
                              reason=reason, gpu_effort="OPTIONAL_STRESS"))

    # YOLOv4: officially unsupported
    entries.append(_entry("YOLOv4", "detect", "yolov4.pt", "B", eligibility="not_applicable",
                          reason="officially unsupported reference (docs)", gpu_effort="NOT_APPLICABLE"))
    # Tracking is an application pipeline, not an ONNX model task
    entries.append(_entry("Tracking", "track", "yolo26n-track-pipeline", "B", eligibility="not_applicable",
                          reason="tracking = application pipeline over predictions; validated separately after task inference",
                          gpu_effort="NOT_APPLICABLE", export_capability="unsupported"))

    for e in entries:
        if e["checkpoint"] in tier_a_checkpoints and e["eligibility"] == "eligible":
            e["tier"] = "A"
    return entries


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ultralytics-release", default="8.3.280")
    ap.add_argument("--tier-a", default="yolo26n.pt,yolo26n-seg.pt,yolo26n-semantic.pt,"
                                        "yolo26n-depth.pt,yolo26n-cls.pt,yolo26n-pose.pt,yolo26n-obb.pt,"
                                        "yolo11n.pt,yolov8n.pt")
    a = ap.parse_args()

    tier_a = [x.strip() for x in a.tier_a.split(",")]

    # pin the immutable commit SHA for the release tag (dereference annotated tags)
    sha = None
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/ultralytics/ultralytics/git/ref/tags/v{a.ultralytics_release}",
            headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            ref = json.load(r)
        sha = ref["object"]["sha"]
        if ref["object"].get("type") == "tag":  # annotated: deref to commit
            req2 = urllib.request.Request(ref["object"]["url"],
                                          headers={"Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req2, timeout=30) as r2:
                sha = json.load(r2)["object"]["sha"]
    except Exception as e:  # noqa: BLE001
        print(f"[catalog] WARN could not pin tag SHA: {e}")

    from radeon_ort.receipts import utc_now_iso
    snap = dict(SNAPSHOT_TEMPLATE)
    snap.update({"captured_utc": utc_now_iso(), "ultralytics_release": a.ultralytics_release,
                 "ultralytics_commit_sha": sha, "docs_access_utc": utc_now_iso()})

    cat_path = REPO_ROOT / "catalog"
    cat_path.mkdir(exist_ok=True)
    (cat_path / "upstream_snapshot.json").write_text(json.dumps(snap, indent=2), encoding="utf-8")

    entries = build_catalog(tier_a)
    # YAML by hand (avoid yaml dependency ordering issues in catalog review diffs)
    (cat_path / "model_catalog.yaml").write_text(_to_yaml(entries), encoding="utf-8")
    with open(cat_path / "model_catalog.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(entries[0].keys()))
        w.writeheader()
        w.writerows(entries)
    json_entries = {e["checkpoint"]: e for e in entries}
    (cat_path / "model_catalog.json").write_text(json.dumps(
        {"schema_version": 1, "snapshot": snap, "entries": json_entries}, indent=2), encoding="utf-8")
    print(f"[catalog] {len(entries)} entries; snapshot pinned release={a.ultralytics_release} sha={sha}")
    return 0


def _to_yaml(entries: list[dict]) -> str:
    lines = ["# generated by runner.generate_catalog — manual review in separate diff",
             f"entry_count: {len(entries)}", "entries:"]
    for e in entries:
        lines.append(f"  - checkpoint: {e['checkpoint']}")
        for k, v in e.items():
            if k == "checkpoint":
                continue
            if v is None:
                lines.append(f"      {k}: null")
            elif isinstance(v, bool):
                lines.append(f"      {k}: {'true' if v else 'false'}")
            elif isinstance(v, (int, float)):
                lines.append(f"      {k}: {v}")
            elif isinstance(v, list):
                lines.append(f"      {k}: [{', '.join(str(x) for x in v)}]")
            else:
                lines.append(f"      {k}: {json.dumps(v, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
