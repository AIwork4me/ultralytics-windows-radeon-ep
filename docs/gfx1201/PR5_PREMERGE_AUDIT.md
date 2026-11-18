# PR #5 pre-merge audit — corrections & limits

## Exact scope
- This PR fixes the unsafe gfx architecture selection and documents that its
  executing host was gfx1100, not gfx1201. No gfx1201 GPU PASS is claimed.
- The initial PR #5 introduced canary and evidence-freezer scripts referring
  to the PR #6-only unified_identity module. Forward fixes remove those imports.
- New canary requires per-gfx DID and LUID matching; GPU numeric validation
  now uses per-output, per-element tolerance. No new physical GPU run is claimed
  after changing those scripts. Existing gfx1100 canary results are historical.
- Evidence-freezer takes a unique --run-id; old run directories are not
  overwritten, and CSV is written as CSV rather than JSON-encoded text.
- The frozen MIN_REPRODUCE.txt is historically outdated: see
  docs/gfx1201/PR5_INDEPENDENT_REPRODUCE.md for current standalone commands.

## Honest unresolved boundaries
- All gfx1201 seven-task rows and soak remain NOT_RUN until a physical gfx1201
  GPU is independently verified. A DID lookup does not establish full GPU binding.
- DML's DXGI ordinal is corroboration only, **not** an independently returned
  EP LUID; do not imply a strong DML→LUID mapping without further evidence.
- Historical HOST_FINGERPRINT.json includes PnP instance suffixes; its original
  commit is immutable and remains in Git history. New generator sanitizes these
  strings; historical exposure requires separate owner privacy assessment.
- The old run bundle's SHA256 manifest remains frozen and unchanged.
  No attempt was made to retroactively mint new hardware evidence.
- PR #6 is a distinct stacked draft. It must have its own P0 hardening;
  merging #5 cannot certify the unified runtime's subprocess fallback/Task Oracle.

## Merge gate
At exact HEAD: pass windows-cpu-ci + secret-scan; no hidden leaked assets;
verify 10 historical files against manifest, ownership of base/head and
branch protection; only normal merge, expected-head guard, post-merge checks.
