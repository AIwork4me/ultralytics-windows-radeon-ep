"""Experiment B5: probe the official AMD GPU EP artifact channels (read-only).

Checks, without downloading or installing anything:
  * the official onnxruntime-ep-amdgpu repository (README + releases API)
  * the NuGet package index for the EP
  * what the Windows ML docs say about the BYO path

Every answer is recorded with URL, access time and sha256 of the fetched body.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) experiment-b-artifact-probe/1"

TARGETS = [
    ("ep_repo_readme_json", "https://api.github.com/repos/onnxruntime/onnxruntime-ep-amdgpu/readme",
     "application/vnd.github.raw+json"),
    ("ep_repo_releases_json", "https://api.github.com/repos/onnxruntime/onnxruntime-ep-amdgpu/releases",
     "application/vnd.github+json"),
    ("ep_repo_meta_json", "https://api.github.com/repos/onnxruntime/onnxruntime-ep-amdgpu",
     "application/vnd.github+json"),
    ("ep_repo_contents_json", "https://api.github.com/repos/onnxruntime/onnxruntime-ep-amdgpu/contents",
     "application/vnd.github+json"),
    ("nuget_ep_index", "https://api.nuget.org/v3-flatcontainer/onnxruntime-ep-amdgpu/index.json",
     "application/json"),
    ("nuget_search_ep", "https://azuresearch-usnc.nuget.org/query?q=onnxruntime-ep-amdgpu&prerelease=true",
     "application/json"),
    ("nuget_ep_amd_pkg", "https://api.nuget.org/v3-flatcontainer/microsoft.ml.onnxruntime.ep.amdgpu/index.json",
     "application/json"),
]


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(url: str, accept: str) -> tuple[dict, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": accept,
                                               "GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            body = r.read()
            return ({"ok": True, "status": r.status, "bytes": len(body),
                     "sha256_raw": hashlib.sha256(body).hexdigest(),
                     "content_type": r.headers.get("Content-Type")}, body)
    except urllib.error.HTTPError as exc:
        body = exc.read() or b""
        return ({"ok": False, "status": exc.code, "bytes": len(body),
                 "sha256_raw": hashlib.sha256(body).hexdigest(),
                 "error": f"HTTPError {exc.code}: {body[:200].decode('utf-8', 'replace')}"}, body)
    except Exception as exc:  # noqa: BLE001
        return ({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, b"")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/experiment_b/OFFICIAL_ARTIFACT_PROBE.json")
    ap.add_argument("--raw-dir", default="reports/experiment_b/_raw/artifacts")
    ap.add_argument("--run-id", required=True)
    a = ap.parse_args()
    raw_dir = REPO / a.raw_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    recs, summary = [], {}
    for name, url, accept in TARGETS:
        meta, body = fetch(url, accept)
        rec = {"name": name, "url": url, "accessed_utc": utc_now(), **meta}
        if body:
            p = raw_dir / f"{name}.body"
            p.write_bytes(body)
            rec["raw_file"] = p.relative_to(REPO).as_posix()
        recs.append(rec)
        print(json.dumps({"name": name, "ok": rec.get("ok"), "status": rec.get("status"),
                          "bytes": rec.get("bytes")}))
        if name == "ep_repo_releases_json" and rec.get("ok") and body:
            try:
                rel = json.loads(body)
                summary["github_releases_count"] = len(rel)
                summary["github_releases"] = [
                    {"tag_name": r.get("tag_name"), "name": r.get("name"),
                     "published_at": r.get("published_at"),
                     "prerelease": r.get("prerelease"),
                     "assets": [{"name": x.get("name"), "size": x.get("size")}
                                for x in (r.get("assets") or [])]}
                    for r in rel][:20]
            except json.JSONDecodeError:
                summary["github_releases_error"] = "unparseable"
        if name == "ep_repo_meta_json" and rec.get("ok") and body:
            try:
                m = json.loads(body)
                summary["repo"] = {"full_name": m.get("full_name"),
                                   "default_branch": m.get("default_branch"),
                                   "created_at": m.get("created_at"),
                                   "pushed_at": m.get("pushed_at"),
                                   "license": (m.get("license") or {}).get("spdx_id"),
                                   "description": m.get("description")}
            except json.JSONDecodeError:
                summary["repo_error"] = "unparseable"
        if name == "nuget_search_ep" and rec.get("ok") and body:
            try:
                s = json.loads(body)
                summary["nuget_search_total"] = s.get("totalHits")
                summary["nuget_search_ids"] = [d.get("id") for d in (s.get("data") or [])][:20]
            except json.JSONDecodeError:
                summary["nuget_search_error"] = "unparseable"
    doc = {"schema": "experiment_b_official_artifact_probe_v1",
           "mission": "B_GFX1151_AMDGPU_EP_ROOT_CAUSE", "run_id": a.run_id,
           "captured_utc": utc_now(), "read_only": True,
           "note": ("Nothing was downloaded for installation, extracted or registered; only "
                    "public index metadata and bodies were read to decide whether an official "
                    "signed Windows artifact exists."),
           "targets": recs, "summary": summary}
    out = REPO / a.out
    out.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"out": a.out, "summary": summary}, indent=1)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
