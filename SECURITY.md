# Security Policy

## Reporting

Open a private security advisory (GitHub Security tab) or contact the owner.
Please do not open public issues with exploit details.

## Scope notes specific to this repo

- Self-hosted GPU runners must never execute unreviewed public PR code; only
  `workflow_dispatch` on reviewed SHAs (see `.github/workflows/manual-trusted-gpu.yml`).
- Evidence artifacts are scanned for secrets/private paths before commit
  (`redaction` module + CI secret scan).
- Model weights are untrusted binaries: kept out of git, loaded only by pinned
  tooling, never executed as code.
