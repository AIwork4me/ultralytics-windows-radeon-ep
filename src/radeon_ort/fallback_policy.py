"""Fallback policy engine — subprocess-isolated EP attempts with fail-closed CLI exit.

Each EP attempt runs in its own subprocess (fresh ORT state, fresh session), so an
AMDGPU wrapper-level CPU retry can never leak into the DML attempt or the oracle.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

EXIT_CODES = {
    "AMDGPU_FAILED": 31,
    "DML_FAILED": 32,
    "CPU_ONLY_USER_AUTHORIZED": 33,
    "BLOCKED_EP": 34,
    "BLOCKED_IDENTITY": 35,
    "OK": 0,
}


@dataclass
class EpAttempt:
    ep: str                    # 'amdgpu' | 'dml' | 'cpu'
    exit_code: int
    stdout_tail: str = ""
    stderr_tail: str = ""
    receipt_fragment: dict | None = None


def run_ep_subprocess(python_exe: str | None, script: str | Path, *,
                      case_json: str | Path, out_json: str | Path,
                      timeout_s: int = 1800) -> EpAttempt:
    """Run one EP attempt in a fresh interpreter; capture everything."""
    cmd = [python_exe or sys.executable, "-I", str(script),
           "--case", str(case_json), "--out", str(out_json)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s,
                           encoding="utf-8", errors="replace")
        frag = None
        if Path(out_json).is_file():
            try:
                frag = json.loads(Path(out_json).read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                frag = None
        return EpAttempt(ep=(frag or {}).get("session", {}).get("ep", "?").lower(),
                          exit_code=r.returncode,
                          stdout_tail=r.stdout[-4000:], stderr_tail=r.stderr[-4000:],
                          receipt_fragment=frag)
    except subprocess.TimeoutExpired as e:
        return EpAttempt(ep="?", exit_code=124, stderr_tail=f"TIMEOUT after {timeout_s}s",
                         receipt_fragment=None)
