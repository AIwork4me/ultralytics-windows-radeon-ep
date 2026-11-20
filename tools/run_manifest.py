"""Write a run-directory evidence manifest (manifest.sha256) via the library.

Git-ignored files are excluded, matching the repository convention that a
manifest describes exactly the files a fresh checkout contains (the raw tensors
under inputs/ and *.raw.npz are git-ignored; their sha256 is recorded inside the
receipts and input manifests instead).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from radeon_ort.manifest import build_manifest  # noqa: E402


def git_ignored(run_dir: Path, rel_paths: list[str]) -> set[str]:
    if not rel_paths:
        return set()
    p = subprocess.run(["git", "check-ignore", "--stdin"], cwd=str(run_dir),
                       input="\n".join(rel_paths), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", check=False)
    return {ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--generator", default="tools/run_manifest.py")
    ap.add_argument("--include-ignored", action="store_true",
                    help="also list git-ignored raw payloads (not the repo convention)")
    a = ap.parse_args()
    run_dir = Path(a.run_dir).resolve()
    if not run_dir.is_dir():
        raise SystemExit(f"run dir not found: {run_dir}")

    rel = sorted(p.relative_to(run_dir).as_posix()
                 for p in run_dir.rglob("*") if p.is_file())
    skip = set() if a.include_ignored else git_ignored(run_dir, rel)
    manifest = build_manifest(run_dir, a.run_id, a.generator)
    out_name = "manifest.sha256"
    entries = [e for e in manifest["entries"]
               if e["path"] not in skip and e["path"] != out_name]
    manifest["entries"] = entries
    manifest["excluded_git_ignored"] = sorted(skip)
    manifest["excluded_note"] = (
        "git-ignored raw payloads are not listed: a fresh checkout does not contain "
        "them, and their hashes live in the receipts / input manifests")
    out = run_dir / out_name
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": out.as_posix(), "entries": len(entries),
                      "excluded": len(skip), "bytes": out.stat().st_size}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
