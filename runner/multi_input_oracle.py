"""python -m runner.multi_input_oracle — multi-input task-oracle smoke.

The PR #8 task-oracle evidence used one image per task. This tool runs the same
PT-vs-EP task parity over several deterministic image variants (original,
horizontal flip, +10% brightness, 90% center crop) and records per-input metrics
plus an aggregate. It is not a dataset-level accuracy claim; it closes the
"one image only" harness limitation and makes the input count explicit.

Inputs and images are hash-bound in the output. Uses one GPU process per case.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runner import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "src"))

from radeon_ort.manifest import sha256_file  # noqa: E402
from radeon_ort.receipts import utc_now_iso  # noqa: E402

TIER_A = ["yolo26n.pt", "yolo26n-seg.pt", "yolo26n-sem.pt", "yolo26n-depth.pt",
          "yolo26n-cls.pt", "yolo26n-pose.pt", "yolo26n-obb.pt"]


def _make_variants(export_python: str, source: Path, out_dir: Path) -> list[dict]:
    from runner.r1_pipeline import _py
    out_dir.mkdir(parents=True, exist_ok=True)
    script = (
        "import sys, json, hashlib\n"
        "from pathlib import Path\n"
        "import cv2, numpy as np\n"
        "src, outdir = sys.argv[1], sys.argv[2]\n"
        "img = cv2.imread(src, cv2.IMREAD_COLOR)\n"
        "assert img is not None, 'decode failed'\n"
        "h, w = img.shape[:2]\n"
        "variants = {}\n"
        "variants['orig'] = img\n"
        "variants['flip'] = cv2.flip(img, 1)\n"
        "variants['bright'] = np.clip(img.astype(np.int16) * 1.10, 0, 255).astype(np.uint8)\n"
        "ch, cw = int(h*0.9), int(w*0.9)\n"
        "y0, x0 = (h-ch)//2, (w-cw)//2\n"
        "variants['crop90'] = img[y0:y0+ch, x0:x0+cw]\n"
        "variants_out = {}\n"
        "for name, im in variants.items():\n"
        "    p = Path(outdir) / f'{Path(src).stem}_{name}.jpg'\n"
        "    cv2.imwrite(str(p), im)\n"
        "    variants_out[name] = {'path': str(p), 'sha256': hashlib.sha256(open(p,'rb').read()).hexdigest()}\n"
        "print(json.dumps(variants_out))\n")
    r = _py(export_python, "-c", [script, str(source), str(out_dir)], timeout_s=300)
    if r["rc"] != 0:
        raise RuntimeError(f"variant generation failed: {r['stderr'][-500:]}")
    line = [ln for ln in r["stdout"].splitlines() if ln.startswith("{")][-1]
    data = json.loads(line)
    return [{"name": k, **v} for k, v in data.items()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--models", default=",".join(TIER_A))
    ap.add_argument("--image", default=str(REPO_ROOT / "data" / "fixtures" / "bus.jpg"))
    ap.add_argument("--obb-image", default=str(REPO_ROOT / "data" / "fixtures" / "dota_p1053.jpg"))
    ap.add_argument("--gfx", default=None)
    ap.add_argument("--ep", choices=["dml", "amdgpu"], default="dml")
    ap.add_argument("--timeout-s", type=int, default=900)
    a = ap.parse_args()

    from runner.r1_pipeline import _host_block, build_bundle, run_worker

    host = _host_block(a.gfx)
    if not host.get("verified_device_identity"):
        print(f"[multi] FAIL: {host.get('identity_method')}")
        return 2
    gfx = host["gfx"]
    out = Path(a.out)
    run_dir = out.parent
    raw = run_dir / "raw_multi"
    inputs_dir = run_dir / "inputs_multi"
    images_dir = run_dir / "images_multi"
    raw.mkdir(parents=True, exist_ok=True)
    inputs_dir.mkdir(parents=True, exist_ok=True)
    winml_python = str(REPO_ROOT / ".venv-winml" / "Scripts" / "python.exe")
    export_python = str(REPO_ROOT / ".venv-export" / "Scripts" / "python.exe")
    import yaml
    thresholds = yaml.safe_load((REPO_ROOT / "configs" / "thresholds.yaml").read_text(encoding="utf-8"))
    th_json = run_dir / "thresholds.json"
    th_json.write_text(json.dumps(thresholds), encoding="utf-8")

    catalog = json.loads((REPO_ROOT / "catalog" / "model_catalog.json").read_text(encoding="utf-8"))
    results = []
    for model in [m.strip() for m in a.models.split(",") if m.strip()]:
        entry = catalog["entries"].get(model)
        if not entry:
            continue
        task = entry["task"]
        imgsz = entry.get("imgsz_default") or 640
        onnx = REPO_ROOT / "models" / "onnx" / f"{Path(model).stem}.raw_b1_fp32_static.onnx"
        pt = REPO_ROOT / "models" / "pt" / model
        if not onnx.is_file() or not pt.is_file():
            results.append({"model": model, "task": task, "status": "ASSET_MISSING"})
            continue
        onnx_sha = sha256_file(onnx)
        source = Path(a.obb_image if task == "obb" else a.image)
        variants = _make_variants(export_python, source, images_dir)
        for v in variants:
            image = Path(v["path"])
            bundle = build_bundle(export_python, image, imgsz, task,
                                  Path(model).stem, onnx_sha, inputs_dir,
                                  f"multi_{v['name']}")
            case_key = f"{Path(model).stem}__{task}__{v['name']}"
            pt_json = raw / f"pt_{case_key}.json"
            pt_npz = raw / f"pt_{case_key}.npz"
            if not pt_json.is_file():
                from runner.r1_pipeline import _py
                r = _py(export_python, str(REPO_ROOT / "src" / "radeon_ort" / "pt_oracle_v2.py"),
                        ["--pt", str(pt), "--task", task,
                         "--tensor-npy", str(inputs_dir / bundle["npy_file"]),
                         "--out-json", str(pt_json), "--out-npz", str(pt_npz)],
                        timeout_s=900, log_path=raw / f"log_pt_{case_key}.txt")
                if r["rc"] != 0:
                    results.append({"model": model, "task": task, "variant": v["name"],
                                    "status": "PT_ORACLE_FAILED", "error": r["stderr"][-300:]})
                    continue
            cand_npz = raw / f"cand_{case_key}.npz"
            frag, rc = run_worker(
                winml_python, a.ep,
                {"ep": a.ep, "gfx": gfx, "onnx_path": str(onnx),
                 "input_manifest": str(inputs_dir / f"manifest_{bundle['input_id'][:16]}.json"),
                 "input_name": "images",
                 "profile_prefix": str(raw / f"prof_{case_key}"),
                 "outputs_npz": str(cand_npz)},
                raw / f"case_{case_key}.json", raw / f"frag_{case_key}.json",
                raw / f"log_{case_key}.txt", a.timeout_s)
            if frag is None or not cand_npz.is_file():
                results.append({"model": model, "task": task, "variant": v["name"],
                                "status": "EP_FAILED", "rc": rc})
                continue
            pp_json = raw / f"post_{case_key}.json"
            pp_npz = raw / f"post_{case_key}.npz"
            from runner.r1_pipeline import _py
            r = _py(export_python, str(REPO_ROOT / "src" / "radeon_ort" / "onnx_postprocess.py"),
                    ["--npz", str(cand_npz), "--task", task, "--imgsz", str(imgsz),
                     "--out-json", str(pp_json), "--out-npz", str(pp_npz)],
                    timeout_s=600, log_path=raw / f"log_post_{case_key}.txt")
            if r["rc"] != 0:
                results.append({"model": model, "task": task, "variant": v["name"],
                                "status": "POSTPROCESS_FAILED"})
                continue
            tp_json = raw / f"tp_{case_key}.json"
            r2 = _py(export_python, str(REPO_ROOT / "src" / "radeon_ort" / "task_parity.py"),
                     ["--pt-json", str(pt_json), "--pt-npz", str(pt_npz),
                      "--cand-json", str(pp_json), "--cand-npz", str(pp_npz),
                      "--task", task, "--imgsz", str(imgsz),
                      "--thresholds-json", str(th_json),
                      "--cand-source", "AMDGPU_POST" if a.ep == "amdgpu" else "DML_POST",
                      "--image-sha256", bundle["image_sha256"],
                      "--input-id", bundle["input_id"],
                      "--out", str(tp_json)],
                     timeout_s=600, log_path=raw / f"log_tp_{case_key}.txt")
            if r2["rc"] != 0:
                results.append({"model": model, "task": task, "variant": v["name"],
                                "status": "TASK_PARITY_FAILED_RC", "error": r2["stderr"][-300:]})
                continue
            tp = json.loads(tp_json.read_text(encoding="utf-8"))
            results.append({
                "model": model, "task": task, "variant": v["name"],
                "input_id": bundle["input_id"], "image_sha256": bundle["image_sha256"],
                "onnx_sha256": onnx_sha,
                "status": "PASS" if tp.get("passed") else "FAIL",
                "metrics": tp.get("metrics"),
                "oracle_kind": tp.get("oracle_kind"),
                "dml_nodes": (frag.get("session") or {}).get("provider_node_counts", {}).get("DmlExecutionProvider"),
            })
            print(f"[multi] {case_key}: {results[-1]['status']}")

    per_task = {}
    for r in results:
        per_task.setdefault(r["task"], []).append(r)
    aggregate = {}
    for task, rows in per_task.items():
        ok = [r for r in rows if r.get("status") == "PASS"]
        aggregate[task] = {
            "inputs": len(rows),
            "passed": len(ok),
            "all_passed": len(ok) == len(rows) and len(rows) > 0,
            "variants": [r.get("variant") for r in rows],
        }
    out.write_text(json.dumps({
        "schema_version": 1,
        "captured_utc": utc_now_iso(),
        "gfx": gfx,
        "ep": a.ep,
        "models": [m.strip() for m in a.models.split(",") if m.strip()],
        "variants_per_model": 4,
        "scope": "multi-input task-oracle smoke; NOT dataset-level accuracy",
        "host_identity": {k: host.get(k) for k in ("gfx", "host_id", "vendor_id", "device_id")},
        "aggregate": aggregate,
        "results": results,
    }, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(aggregate, indent=2))
    return 0 if all(v["all_passed"] for v in aggregate.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
