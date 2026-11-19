"""python -m runner.redact_evidence — privacy + secret scan/rewrite for evidence.

Text artifacts are rewritten in place (JSON/JSONL/CSV/TXT/LOG/MD/YAML):
  * absolute Windows user paths -> %USERPROFILE%
  * ANSI colour escapes stripped
  * MAC / serial patterns redacted (radeon_ort.redaction)
Binary tensors are never touched here; they are relocated by
`runner.prepare_evidence` and listed as LOCAL_ONLY.

Exit: 0 clean/rewritten, 1 remaining leak or secret (fail-closed).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from runner import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "src"))

from radeon_ort import redaction  # noqa: E402

TEXT_EXT = {".json", ".jsonl", ".csv", ".txt", ".log", ".md", ".yaml", ".yml"}
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SECRET_PATTERNS = {
    "github_token": re.compile(r"\b(?:gh[opsu]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "password_assign": re.compile(r"(?i)\bpassword\s*[=:]\s*\S{4,}"),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True, action="append")
    ap.add_argument("--check", action="store_true", help="report only; do not rewrite")
    a = ap.parse_args()

    problems: list[str] = []
    changed = 0
    scanned = 0
    for root in map(Path, a.run_dir):
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in TEXT_EXT:
                continue
            scanned += 1
            text = p.read_text(encoding="utf-8", errors="replace")
            new = redaction.redact_text(ANSI_RE.sub("", text))
            for name, rx in SECRET_PATTERNS.items():
                if rx.search(new):
                    problems.append(f"{p.relative_to(root)}: secret pattern {name}")
            leaks = redaction.scan_for_leaks(new)
            for leak in leaks:
                problems.append(f"{p.relative_to(root)}: {leak}")
            if new != text and not a.check:
                p.write_text(new, encoding="utf-8", newline="")
                changed += 1
    print(f"[redact] scanned={scanned} rewritten={changed} problems={len(problems)}")
    for x in problems[:100]:
        print(f"[redact] FAIL {x}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
