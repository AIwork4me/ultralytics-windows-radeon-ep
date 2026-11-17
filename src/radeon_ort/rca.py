"""AMDGPU-first RCA incident register (mission R1-E).

Every AMDGPU exception/warning/anomaly gets a durable incident record BEFORE
widespread GPU runs. JSONL is append-only (one JSON object per line); the
markdown register is re-rendered from the JSONL. No record is ever deleted or
silently mutated to success — updates append a new revision of the same issue id
with `rev` incremented, preserving the full history in the file.

Status enum (mission R1-E):
  NEW, REPRODUCED, ISOLATED, MITIGATED, NOT_REPRODUCED, BLOCKED_DEPENDENCY,
  RCA_NOT_ISOLATED, CLOSED_VERIFIED

`CLOSED_VERIFIED` requires a reproducible before/fix/after plus regression test
or an evidence-supported no-change closure (enforced by `can_close`).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC
from pathlib import Path

RCA_STATUSES = (
    "NEW", "REPRODUCED", "ISOLATED", "MITIGATED", "NOT_REPRODUCED",
    "BLOCKED_DEPENDENCY", "RCA_NOT_ISOLATED", "CLOSED_VERIFIED",
)
RCA_SEVERITIES = ("P0", "P1", "P2")
RCA_STAGES = (
    "registration", "catalog", "identity", "probe", "session-create", "compile",
    "run", "parity", "task", "profile", "teardown", "soak", "benchmark",
    "other",
)

# structured sub-blocks per mission R1-E: observation / hypotheses / experiments
# / evidence / causal_inference / mitigation / remaining_uncertainty


@dataclass
class RcaExperiment:
    hypothesis: str                 # what this experiment discriminates
    command: str                    # exact command / code path
    controlled_variable: str        # the ONE variable changed
    predicted_if_true: str          # observable predicted by the hypothesis
    actual_result: str
    receipts_sha: list[str] = field(default_factory=list)
    reproducible: bool | None = None
    repetitions: int = 0
    gpu_time_s: float | None = None
    falsified: bool | None = None   # True => hypothesis dead


@dataclass
class RcaIssue:
    issue_id: str                   # AMDGPU-RCA-###
    severity: str                   # P0/P1/P2
    status: str                     # RCA_STATUSES
    stage: str                      # RCA_STAGES
    title: str
    head: str                       # exact git head when first seen
    first_seen_utc: str
    last_seen_utc: str
    model: str | None = None        # model/task/onnx sha when applicable
    onnx_sha256: str | None = None
    input_sha256: str | None = None
    environment: dict = field(default_factory=dict)   # win build/driver/EP dll sha/ORT ver
    selected_ep_device: str | None = None             # candidate attestation if known
    exception: str | None = None                      # exact exception/HRESULT/HIP code
    stdout_excerpt: str | None = None
    stderr_excerpt: str | None = None
    crash_or_timeout: str | None = None
    pids: dict = field(default_factory=dict)          # parent/child pids
    run_receipt_path: str | None = None
    artifact_path: str | None = None
    impact: str | None = None                         # incl. whether GPU work happened
    gpu_work_happened: bool | None = None
    observation: str = ""
    hypotheses: list[str] = field(default_factory=list)
    experiments: list[dict] = field(default_factory=list)   # RcaExperiment dicts
    evidence: list[str] = field(default_factory=list)        # file paths (sanitized)
    causal_inference: str | None = None
    mitigation: str | None = None
    remaining_uncertainty: str | None = None
    next_discriminating_experiment: str | None = None
    confounders_considered: list[str] = field(default_factory=list)
    rev: int = 1
    history: list[dict] = field(default_factory=list)  # prior revisions (status changes)


def utc_now_iso() -> str:
    from datetime import datetime
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class RcaRegister:
    """Append-only JSONL register + markdown rendering."""

    def __init__(self, rca_dir: str | Path):
        self.dir = Path(rca_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.jsonl = self.dir / "AMDGPU_ISSUES.jsonl"

    def load(self) -> dict[str, dict]:
        issues: dict[str, dict] = {}
        if self.jsonl.is_file():
            for line in self.jsonl.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                prev = issues.get(rec["issue_id"])
                if prev is not None:
                    rec.setdefault("history", []).extend(prev.get("history", []))
                    hist_entry = {k: prev[k] for k in
                                  ("status", "last_seen_utc", "rev", "causal_inference",
                                   "mitigation") if k in prev}
                    rec["history"].append(hist_entry)
                issues[rec["issue_id"]] = rec
        return issues

    def append(self, issue: RcaIssue) -> Path:
        existing = self.load()
        prev = existing.get(issue.issue_id)
        if prev is not None:
            issue.rev = prev.get("rev", 1) + 1
            issue.history = prev.get("history", []) + [
                {k: prev[k] for k in ("status", "last_seen_utc", "rev",
                                      "causal_inference", "mitigation") if k in prev}]
        if issue.status not in RCA_STATUSES:
            raise ValueError(f"bad RCA status {issue.status!r}")
        if issue.severity not in RCA_SEVERITIES:
            raise ValueError(f"bad RCA severity {issue.severity!r}")
        if issue.status == "CLOSED_VERIFIED" and not self.can_close(issue):
            raise ValueError(
                "CLOSED_VERIFIED requires before/fix/after reproducibility or an "
                "evidence-supported no-change closure note in causal_inference")
        with self.jsonl.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(issue), sort_keys=False) + "\n")
        return self.jsonl

    @staticmethod
    def can_close(issue: RcaIssue) -> bool:
        has_before_after = bool(issue.experiments) and any(
            e.get("reproducible") for e in issue.experiments)
        no_change_closure = bool(issue.causal_inference) and (
            "no-change closure" in issue.causal_inference
            or "no change needed" in issue.causal_inference.lower())
        return has_before_after or no_change_closure

    def next_id(self) -> str:
        ids = [k for k in self.load() if k.startswith("AMDGPU-RCA-")]
        n = max((int(k.rsplit("-", 1)[1]) for k in ids), default=0) + 1
        return f"AMDGPU-RCA-{n:03d}"

    def render_markdown(self, head: str) -> Path:
        issues = self.load()
        lines = [
            "# AMDGPU RCA Register — mission GFX1151-R1", "",
            f"Head: `{head}` — rendered {utc_now_iso()} from `AMDGPU_ISSUES.jsonl` (append-only).", "",
            "| ID | Sev | Status | Stage | Title | Impact | GPU work? |",
            "|---|---|---|---|---|---|---|",
        ]
        for iid in sorted(issues):
            r = issues[iid]
            lines.append(
                f"| {iid} | {r['severity']} | {r['status']} | {r['stage']} "
                f"| [{r['title']}]({iid}.md) "
                f"| {(r.get('impact') or '')[:80]} | {r.get('gpu_work_happened')} |")
        lines += ["", f"{len(issues)} issue(s). Unresolved (not CLOSED_VERIFIED): "
                  f"{sum(1 for r in issues.values() if r['status'] != 'CLOSED_VERIFIED')}.", ""]
        out = self.dir / "AMDGPU_RCA_REGISTER.md"
        out.write_text("\n".join(lines), encoding="utf-8")
        return out

    def write_issue_doc(self, issue: RcaIssue) -> Path:
        d = asdict(issue)
        lines = [f"# {issue.issue_id} — {issue.title}", "",
                 f"- severity: {issue.severity}  status: **{issue.status}**  stage: {issue.stage}",
                 f"- first seen: {issue.first_seen_utc}  last seen: {issue.last_seen_utc}  head: `{issue.head}`",
                 f"- environment: `{json.dumps(issue.environment)}`",
                 f"- exception: `{issue.exception}`", ""]
        for key, label in (("observation", "Observation"), ("hypotheses", "Hypotheses"),
                           ("experiments", "Experiments"), ("evidence", "Evidence"),
                           ("causal_inference", "Causal inference"),
                           ("mitigation", "Mitigation"),
                           ("remaining_uncertainty", "Remaining uncertainty"),
                           ("next_discriminating_experiment", "Next discriminating experiment"),
                           ("confounders_considered", "Confounders considered")):
            v = d.get(key)
            if not v:
                continue
            lines += [f"## {label}", ""]
            if isinstance(v, list):
                for item in v:
                    lines += [f"- {json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else item}"]
            else:
                lines += [str(v)]
            lines.append("")
        p = self.dir / f"{issue.issue_id}.md"
        p.write_text("\n".join(lines), encoding="utf-8")
        return p
