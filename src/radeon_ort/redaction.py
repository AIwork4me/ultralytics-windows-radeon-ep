"""Redaction of private, machine-identifying strings from evidence artifacts.

Rules:
- replace absolute Windows user paths (C:\\Users\\<name>\\...) with %USERPROFILE%\\...
- drop machine name, domain, serials, MACs
- fail closed: redaction_scan returns a list of violations; reports must be empty
  before evidence is committed.
"""

from __future__ import annotations

import re
from pathlib import Path

USER_PATH_RE = re.compile(r"[A-Za-z]:\\Users\\([^\\\"']+)", re.IGNORECASE)
# JSON-escaped paths contain two literal backslashes (C:\\Users\\name).
USER_PATH_ESCAPED_RE = re.compile(r"[A-Za-z]:\\\\Users\\\\([^\\\\\"']+)", re.IGNORECASE)
USER_POSIX_RE = re.compile(r"(?:[A-Za-z]:)?/(?:Users|home)/([^/\"']+)", re.IGNORECASE)
MACHINE_HINTS = {
    "computername": "COMPUTERNAME=<redacted>",
    "hostname": "hostname=<redacted>",
}
MAC_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
GUIDISH_SERIAL_RE = re.compile(r"\bSERIAL[A-Z_]*=?[0-9A-Za-z]{6,}\b", re.IGNORECASE)
USERPROFILE_ENV_RE = re.compile(r"%USERPROFILE%[^\\\"]*", re.IGNORECASE)

KNOWN_PUBLIC_USERNAMES = {"rocm", "public", "user", "administrator", "wdagutilityaccount"}


def redact_text(text: str) -> str:
    text = USER_PATH_RE.sub(r"%USERPROFILE%\\", text)
    text = USER_PATH_ESCAPED_RE.sub(r"%USERPROFILE%\\\\", text)
    text = USER_POSIX_RE.sub(r"%USERPROFILE%/", text)
    text = MAC_RE.sub("<MAC-redacted>", text)
    text = GUIDISH_SERIAL_RE.sub("<serial-redacted>", text)
    return text


def redact_path(path: str | Path) -> str:
    """Return a redacted, forward-slash, non-identifying path string."""
    s = str(path).replace("\\", "/")
    s = USER_POSIX_RE.sub(r"%USERPROFILE%/", s)
    return s


def scan_for_leaks(text: str) -> list[str]:
    """Return human-readable leak descriptions found in text (empty == clean)."""
    leaks: list[str] = []
    for m in USER_PATH_RE.finditer(text):
        if m.group(1).lower() not in KNOWN_PUBLIC_USERNAMES:
            leaks.append(f"absolute user path leaks username '{m.group(1)}'")
    for m in USER_PATH_ESCAPED_RE.finditer(text):
        if m.group(1).lower() not in KNOWN_PUBLIC_USERNAMES:
            leaks.append(f"JSON-escaped user path leaks username '{m.group(1)}'")
    for m in USER_POSIX_RE.finditer(text):
        if m.group(1).lower() not in KNOWN_PUBLIC_USERNAMES:
            leaks.append(f"posix user path leaks username '{m.group(1)}'")
    if MAC_RE.search(text):
        leaks.append("MAC address pattern present")
    low = text.lower()
    for hint in MACHINE_HINTS:
        if hint + "=" in low:
            leaks.append(f"machine identifier '{hint}=' present")
    return leaks


def scan_file(path: str | Path) -> list[str]:
    return scan_for_leaks(Path(path).read_text(encoding="utf-8", errors="replace"))
