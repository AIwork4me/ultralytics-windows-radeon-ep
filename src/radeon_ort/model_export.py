"""Ultralytics .pt -> ONNX export orchestration (runs in the export venv subprocess).

Export semantics pinned (2026-11 docs): nms=None raw, nms=True embedded NMS,
nms=False NMS-free head where allowed; quantize=16/8 replaces deprecated half/int8.
Task defaults differ: classify 224, depth 768, OBB 1024, else often 640 — the
exporter queries the model's own defaults rather than assuming 640.
"""

from __future__ import annotations

EXPORT_SCRIPT = r'''
import argparse, hashlib, json, sys
from pathlib import Path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--imgsz", type=int, default=None)
    ap.add_argument("--dynamic", action="store_true")
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--nms", type=str, default="None")  # None|True|False
    ap.add_argument("--quantize", type=str, default="None")  # None|16|8
    ap.add_argument("--opset", type=int, default=None)
    ap.add_argument("--simplify", action="store_true")
    a = ap.parse_args()

    import ultralytics
    from ultralytics import YOLO
    kwargs = {"format": "onnx", "dynamic": a.dynamic, "batch": a.batch}
    if a.imgsz: kwargs["imgsz"] = a.imgsz
    if a.opset: kwargs["opset"] = a.opset
    if a.nms != "None": kwargs["nms"] = {"True": True, "False": False}[a.nms]
    if a.quantize != "None": kwargs["quantize"] = int(a.quantize)
    if a.simplify: kwargs["simplify"] = True

    model = YOLO(a.pt)
    # official task default imgsz when caller did not pin one
    if a.imgsz is None:
        try:
            kwargs.setdefault("imgsz", int(model.overrides.get("imgsz", 640)))
        except Exception:
            kwargs.setdefault("imgsz", 640)
    res = model.export(**kwargs)
    out = Path(res if isinstance(res, str) else res[0])
    # copy to requested destination unless already there
    if out.resolve() != Path(a.out).resolve():
        import shutil
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out, a.out)
    h = hashlib.sha256()
    with open(a.out, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    import onnx
    m = onnx.load(str(a.out))
    onnx.checker.check_model(m)
    print(json.dumps({
        "ok": True,
        "onnx": str(a.out),
        "sha256": h.hexdigest(),
        "ultralytics_version": ultralytics.__version__,
        "export_args": {k: v for k, v in kwargs.items()},
        "input_names": [i.name for i in m.graph.input],
        "output_names": [o.name for o in m.graph.output],
        "opset": (m.opset_import[-1].version if m.opset_import else None),
    }))

if __name__ == "__main__":
    main()
'''


def run_export(export_python: str, pt_path: str, out_path: str, *,
               imgsz: int | None = None, dynamic: bool = False, batch: int = 1,
               nms: str = "None", quantize: str = "None", opset: int | None = None,
               timeout_s: int = 1200) -> dict:
    import json
    import subprocess
    cmd = [export_python, "-I", "-c", EXPORT_SCRIPT, "--pt", pt_path, "--out", out_path]
    if imgsz:
        cmd += ["--imgsz", str(imgsz)]
    if dynamic:
        cmd += ["--dynamic"]
    cmd += ["--batch", str(batch), "--nms", nms, "--quantize", quantize]
    if opset:
        cmd += ["--opset", str(opset)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s,
                       encoding="utf-8", errors="replace")
    payload = None
    for line in reversed((r.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and '"ok": true' in line:
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    return {"returncode": r.returncode, "payload": payload,
            "stdout_tail": (r.stdout or "")[-4000:], "stderr_tail": (r.stderr or "")[-4000:]}
