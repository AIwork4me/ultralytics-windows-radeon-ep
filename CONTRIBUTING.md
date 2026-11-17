# Contributing

Thanks for improving Radeon EP validation for Windows!

## Ground rules

- Evidence discipline comes first — read `AGENTS.md`. GPU claims need receipts;
  receipts need profiler evidence; the validator is adversarially tested.
- Run before opening a PR:
  - `python -m pytest tests/unit tests/contracts tests/adversarial -q` (all green)
  - `python -m runner.verify_receipts --run-dir <your-run>` (if you add evidence)
- Hardware results: attach receipts + manifest under `reports/<gfx>/<run_id>/`
  from YOUR hardware; gfx columns for other hosts stay `NOT_RUN`.
- Never commit weights/datasets/secrets/absolute paths.
- DCO-style sign-off: `git commit -s`. PR template checklist is mandatory.

## Adding a model family

1. Update `runner/generate_catalog.py` (never hand-edit generated catalog).
2. Regenerate catalog, review diff separately.
3. Add/adjust task adapter + oracle with unit tests.
4. Add eligibility reasons honestly (asset_blocked / license_restricted / NA).
