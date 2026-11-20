"""Experiment B3: verify the live official Windows ML / AMDGPU EP documentation.

Read-only network fetch (no authentication, no repository writes outside
reports/experiment_b/_raw/docs). For every URL it records the access time, HTTP
status, byte size and sha256 of the raw body, the page's own "last updated"
marker, and which officially-relevant statements are present or absent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) experiment-b-doc-verify/1"

PAGES = [
    ("supported_execution_providers",
     "https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/supported-execution-providers"),
    ("supported_execution_providers_md",
     "https://raw.githubusercontent.com/MicrosoftDocs/windows-ai-docs/main/docs/new-windows-ml/supported-execution-providers.md"),
    ("initialize_execution_providers",
     "https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/initialize-execution-providers"),
    ("register_execution_providers",
     "https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/register-execution-providers"),
    ("execution_provider_errors",
     "https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/execution-provider-errors"),
    ("bring_your_own_eps",
     "https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/bring-your-own-eps"),
    ("ep_releases_wiki_html",
     "https://github.com/microsoft/WindowsML/wiki/Windows-ML-Execution-Provider-Releases"),
    ("ep_releases_wiki_md",
     "https://raw.githubusercontent.com/wiki/microsoft/WindowsML/Windows-ML-Execution-Provider-Releases.md"),
]

# Statements the root-cause decision depends on. Each is (label, regex, kind).
PROBES = [
    ("amdgpu_ep_name", r"AMDGPUExecutionProvider", "ep"),
    ("windowsml_min_2_5_96", r"2\.5\.96", "version"),
    ("adrenalin_24_12_1", r"24\.12\.1", "driver"),
    ("adrenalin_25_6_3", r"25\.6\.3", "driver"),
    ("migraphx_deprecated", r"[Mm]igraphx[^.]{0,80}deprecat|deprecat[^.]{0,80}[Mm]igraphx", "deprecation"),
    ("gfx1151", r"gfx1151|gfx\s*1151", "arch"),
    ("gfx1100", r"gfx1100|gfx\s*1100", "arch"),
    ("strix_halo", r"Strix\s*Halo|Strix", "arch"),
    ("did_0x1586", r"0x1586|1586", "arch"),
    ("rdna3_5_or_rdna", r"RDNA\s*3|RDNA3", "arch"),
    ("ep_msix_1_8_65_0", r"1\.8\.65\.0", "package"),
    ("ep_msix_2_2611_67_0", r"2\.2611\.67\.0", "package"),
    ("microsoft_store_channel", r"Microsoft Store", "channel"),
    ("windows_update_channel", r"Windows Update", "channel"),
    ("byo_build_from_source", r"built from source|build from source", "byo"),
    ("onnxruntime_ep_amdgpu_pkg", r"onnxruntime-ep-amdgpu", "byo"),
    ("hp_oem_driver", r"\bHP\b|OEM driver", "oem"),
    ("windows_11_24h2_26100", r"24H2|26100", "os"),
]


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(url: str, timeout: int = 45) -> tuple[dict, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            meta = {"ok": True, "status": r.status,
                    "content_type": r.headers.get("Content-Type"),
                    "last_modified": r.headers.get("Last-Modified"),
                    "etag": r.headers.get("ETag"),
                    "bytes": len(body),
                    "sha256_raw": hashlib.sha256(body).hexdigest()}
            return meta, body
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, b""


def text_of(body: bytes) -> str:
    """HTML-stripped, whitespace-collapsed text (the stable comparison artifact)."""
    import html as _html
    t = body.decode("utf-8", errors="replace")
    t = re.sub(r"(?is)<script.*?</script>", " ", t)
    t = re.sub(r"(?is)<style.*?</style>", " ", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = _html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def r10_index() -> dict:
    p = REPO / "reports" / "mission_r10" / "DOC_CITATIONS.json"
    if not p.is_file():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {c.get("url"): c for c in doc.get("citations") or []}


def page_marker(text: str) -> dict:
    out = {}
    m = re.search(r'<meta\s+name="ms\.date"\s+content="([^"]+)"', text, re.IGNORECASE)
    if m:
        out["meta_ms_date"] = m.group(1)
    m = re.search(r'name="ms\.date"\s+content="([^"]+)"', text, re.IGNORECASE)
    if m:
        out["meta_ms_date"] = m.group(1)
    m = re.search(r"Last updated on\s+([0-9]{4}-[0-9]{2}-[0-9]{2})", text)
    if m:
        out["last_updated_on"] = m.group(1)
    m = re.search(r"^ms\.date:\s*(\S+)$", text, re.MULTILINE)
    if m:
        out["front_matter_ms_date"] = m.group(1)
    m = re.search(r"<title>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    if m:
        out["title"] = re.sub(r"\s+", " ", m.group(1)).strip()[:200]
    return out


def probe_page(text: str) -> dict:
    hits = {}
    for label, pattern, kind in PROBES:
        found = re.findall(pattern, text)
        hits[label] = {"kind": kind, "count": len(found),
                       "present": bool(found)}
    amd_rows = []
    for m in re.finditer(r"AMD[^<\n]{0,80}", text):
        s = m.group(0).strip()
        if s and s not in amd_rows:
            amd_rows.append(s[:120])
        if len(amd_rows) >= 25:
            break
    hits["_amd_context_strings"] = amd_rows
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/experiment_b/OFFICIAL_DOC_CITATIONS.json")
    ap.add_argument("--raw-dir", default="reports/experiment_b/_raw/docs")
    ap.add_argument("--run-id", required=True)
    a = ap.parse_args()

    R10 = r10_index()
    raw_dir = REPO / a.raw_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for name, url in PAGES:
        meta, body = fetch(url)
        rec = {"name": name, "url": url, "accessed_utc": utc_now(), **meta}
        if body:
            suffix = ".md" if (".md" in url or "wiki" in name and "md" in name) else ".html"
            p = raw_dir / f"{name}{suffix}"
            p.write_bytes(body)
            rec["raw_file"] = p.relative_to(REPO).as_posix()
            text = body.decode("utf-8", errors="replace")
            plain = text_of(body)
            rec["bytes_text"] = len(plain)
            rec["sha256_text"] = hashlib.sha256(plain.encode()).hexdigest()
            rec["markers"] = page_marker(text)
            rec["statements"] = probe_page(text)
            # second fetch for byte stability (documentation pages are not static
            # assets; a differing body on refetch is recorded, not hidden)
            meta2, body2 = fetch(url)
            rec["refetch"] = {"ok": meta2.get("ok"),
                              "sha256_raw": meta2.get("sha256_raw"),
                              "byte_stable": meta2.get("sha256_raw") == meta.get("sha256_raw"),
                              "sha256_text": (hashlib.sha256(text_of(body2).encode()).hexdigest()
                                              if body2 else None),
                              "text_stable": (bool(body2) and
                                              hashlib.sha256(text_of(body2).encode()).hexdigest()
                                              == rec["sha256_text"])}
            r10 = R10.get(url)
            if r10:
                same_raw = r10.get("sha256_body") == meta.get("sha256_raw")
                is_markdown = url.endswith(".md") or "raw.githubusercontent.com" in url
                rec["mission_r10"] = {
                    "accessed_utc": r10.get("accessed_utc"),
                    "sha256_body_then": r10.get("sha256_body"),
                    "sha256_body_now": meta.get("sha256_raw"),
                    "raw_body_identical_to_r10": same_raw,
                    "comparison_note": (
                        "markdown sources are byte-comparable across tools and are the "
                        "authoritative content hash"
                        if is_markdown else
                        "Learn HTML embeds per-request dynamic chrome (asset nonces), so a "
                        "raw-body difference is expected and is NOT a content change; the "
                        "content hash of record for these pages is the main-branch markdown"),
                    "text_hash_comparable": False,
                    "text_hash_reason": ("sha256_text depends on the extractor; R10 and B use "
                                         "different extractors, so text hashes are not compared"),
                }
        records.append(rec)
        print(json.dumps({"name": name, "ok": rec.get("ok"),
                          "bytes": rec.get("bytes"), "sha": (rec.get("sha256_raw") or "")[:16],
                          "stable": rec.get("refetch", {}).get("byte_stable"),
                          "markers": rec.get("markers")}))

    doc = {
        "schema": "experiment_b_official_doc_citations_v1",
        "mission": "B_GFX1151_AMDGPU_EP_ROOT_CAUSE",
        "run_id": a.run_id,
        "captured_utc": utc_now(),
        "method": ("raw HTTPS GET, no auth, no cookies; sha256 over the raw response body; "
                   "each page fetched twice to record byte stability"),
        "probe_notes": {
            "presence_is_not_proof": ("a statement being present in a fetched page is a "
                                      "citation input, not a device capability proof"),
        },
        "pages": records,
    }
    out = REPO / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"out": a.out, "pages": len(records)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
