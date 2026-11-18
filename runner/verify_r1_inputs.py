"""python -m runner.verify_r1_inputs --run-dir <dir>

Gate B formal check: every fragment (CPU reference, AMDGPU, DML, negative runs)
of the same case consumed the SAME input_id and tensor bytes (sha256), and the
manifests' tensor_sha256 matches. Cross-process equality is implied: each
fragment comes from its own subprocess. Writes R1_INPUT_IDENTITY.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    a = ap.parse_args()
    run_dir = Path(a.run_dir)
    raw = run_dir / "raw"
    frags = sorted(raw.glob("frag_*.json"))
    cases: dict[str, list[dict]] = {}
    problems: list[str] = []
    for fp in frags:
        try:
            f = json.loads(fp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            problems.append(f"{fp.name}: unparseable fragment")
            continue
        name = fp.stem[len("frag_"):]
        case_key = name.rsplit("_", 1)[0] if name.rsplit("_", 1)[-1] in ("cpu", "amdgpu", "dml") else name
        cases.setdefault(case_key, []).append({
            "fragment": fp.name,
            "input_id": f.get("input_id"),
            "tensor_sha256": f.get("input_tensor_sha256"),
        })
    identity: dict = {"cases": {}, "ok": True}
    for case_key, entries in sorted(cases.items()):
        ids = {e["input_id"] for e in entries}
        shas = {e["tensor_sha256"] for e in entries}
        ok = len(ids) == 1 and len(shas) == 1 and None not in ids and None not in shas
        identity["cases"][case_key] = {"ok": ok, "fragments": entries,
                                       "input_id": sorted(ids), "tensor_sha256": sorted(shas)}
        if not ok:
            identity["ok"] = False
            problems.append(f"{case_key}: input identity differs across fragments "
                            f"(ids={ids} shas={shas})")
        else:
            # cross-check against the input manifest
            mid = list(ids)[0]
            mpath = run_dir / "inputs" / f"manifest_{mid[:16]}.json"
            if mpath.is_file():
                m = json.loads(mpath.read_text(encoding="utf-8"))
                want = m.get("tensor_sha256")
                if isinstance(want, dict):
                    want = next(iter(want.values()))
                if want != list(shas)[0]:
                    identity["ok"] = False
                    problems.append(f"{case_key}: fragment tensor sha != manifest sha")
    out = run_dir / "R1_INPUT_IDENTITY.json"
    out.write_text(json.dumps({"ok": identity["ok"] and not problems,
                               "problems": problems,
                               "cases": identity["cases"]}, indent=2), encoding="utf-8")
    for p in problems:
        print("FAIL", p)
    print(f"[inputs] {'ALL_OK' if not problems else f'{len(problems)} PROBLEM(S)'} "
          f"({len(cases)} cases, {len(frags)} fragments) -> {out}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
