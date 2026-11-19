# Hardware claim matrix (fail-closed)

A GPU PASS is only claimable for the gfx architecture that produced the live
receipts on this host. Code-level DID pinning is not a substitute for hardware
evidence.

| gfx | Host hardware present? | Code path | GPU PASS claim allowed? |
| --- | --- | --- | --- |
| gfx1151 | Yes (Radeon 8060S) | DID-pinned, verified | Only from fresh gfx1151 receipts |
| gfx1100 | No | DID-pinned; wrong target fails closed | **No** — no hardware evidence |
| gfx1201 | No | DID-pinned; wrong target fails closed | **No** — no hardware evidence |

Current live probe (`HOST_IDENTITY_MATRIX.json`):

- requesting gfx1151 selects DID `0x1586` and verifies;
- requesting gfx1100 returns `WRONG_OR_AMBIGUOUS_DEVICE` and creates no session;
- requesting gfx1201 returns the same failure.

Tests cover the same selection logic synthetically (`0x7448/0x744C` → gfx1100,
`0x7550/0x7551` → gfx1201, ambiguity and wrong-target failures), but synthetic
tests are **not** a GPU PASS. A gfx1100/gfx1201 claim requires running the matrix
on that hardware under a new run id and contract.

No CPU CI, name-only fallback, DXGI LUID, or `get_providers()` output can replace
that hardware evidence.
