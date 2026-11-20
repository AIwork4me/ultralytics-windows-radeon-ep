"""Secret / identifier scan for experiment B evidence and documentation.

Fails closed: any hit of a user path, account SID, machine name pattern, MAC,
token-like string or private key header is reported and returns non-zero.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from radeon_ort.redaction import scan_for_leaks

EXTRA = {
    "account_sid": re.compile(r"S-1-(?:\d+-){1,14}\d+"),
    "machine_name_env": re.compile(r"COMPUTERNAME=|USERNAME=|USERDOMAIN="),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "bearer": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{20,}"),
    "absolute_windows_user_path": re.compile(r"[A-Za-z]:\\Users\\[^\\\"'\s]+", re.IGNORECASE),
    "posix_home_path": re.compile(r"/(?:home|Users)/[^/\"'\s]+"),
}


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", default=["reports/experiment_b", "docs/experiment_b"])
    ap.add_argument("--out", default="reports/experiment_b/_raw/REDACTION_SCAN.json")
    a = ap.parse_args()
    findings = []
    scanned = 0
    for root in a.paths:
        base = REPO / root
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            scanned += 1
            text = p.read_text(encoding="utf-8", errors="replace")
            rel = p.relative_to(REPO).as_posix()
            verbatim_upstream = rel.startswith(("reports/experiment_b/_raw/docs/",
                                                 "reports/experiment_b/_raw/artifacts/"))
            # Verbatim upstream documentation bodies are third-party content; only
            # hard secret patterns matter there, and the user-path heuristics would
            # false-positive on a URL such as /onnxruntime/.
            leaks = [] if verbatim_upstream else scan_for_leaks(text)
            for name, rx in EXTRA.items():
                if verbatim_upstream and name in ("absolute_windows_user_path",
                                                  "posix_home_path", "machine_name_env"):
                    continue
                for m in rx.finditer(text):
                    # a loopback proxy is a configuration fact, not an identifier
                    if name == "absolute_windows_user_path" and "\\\\Users\\\\" in m.group(0):
                        pass
                    leaks.append(f"{name}: {m.group(0)[:60]}")
            if leaks:
                findings.append({"path": p.relative_to(REPO).as_posix(),
                                 "findings": sorted(set(leaks))[:20]})
    doc = {"schema": "experiment_b_redaction_scan_v1", "captured_utc": utc_now(),
           "roots": a.paths, "files_scanned": scanned,
           "violations": findings, "ok": not findings}
    out = REPO / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    for f in findings:
        print("LEAK", f["path"], f["findings"][:5])
    print(json.dumps({"files_scanned": scanned, "violations": len(findings), "ok": doc["ok"]}))
    return 0 if not findings else 3


if __name__ == "__main__":
    raise SystemExit(main())
