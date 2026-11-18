# RCA2A P0/P1 validation corrections — immutable-evidence addendum

Scope: CPU-only receipt validator, supervisor oracle, statistics and tests.
No GPU rerun is claimed. The existing RCA2A evidence tree and manifest remain unchanged.

## Corrected contracts

- The worker emits SESSION_CREATE_BEGIN. The strict schema now canonicalizes the known *_BEGIN phase markers and rejects unknown or out-of-order phases.
- GPU_PASS is checked against the real gpu_validation.checks[] structure, including EP identity, providers, output names, parity and positive profile node count; flattened flags no longer pass.
- The supervisor independently requires nonempty reference and profile artifacts. Per-output per-element tolerance is abs(actual-reference) <= atol+rtol*abs(reference); no global scale from another output. Missing outputs, NaN/Inf and empty tensors fail closed.
- Local plan verification resolves %CACHE% / %CACHE_MXR% and binds model, ONNX SHA and exact input SHA from frozen cases.
- Soak minimum is >=900.0 seconds of effective steady inference, plus clean GPU proof and at least one session recreation. Boundary tests: 869, 899, 900 and 901 seconds.

## Evidence provenance and limitations

The published receipts still record their original physically validated code_git_sha (ef3faec9...). They are historical facts and were not edited to make the new validator pass.
The original G4_STATISTICS.json was computed by the older >=870-second predicate, so its historical numbers are not silently rewritten. The two successful published physical soaks each exceeded 900 seconds, but CPU CI is not new GPU validation.
Public/redacted plan placeholder paths can differ from locally resolved absolute paths. Strict environment-to-plan matching requires the unredacted local plan, not invented equality of placeholders.
Review tests/unit/test_rca2a_r11_contracts.py, including actual published receipt fixtures; run the usual windows-cpu-ci and secret-scan at this branch's exact HEAD.

## Release gate

Keep PR #4 Draft. Do not merge, mark Ready, or publish upstream issues without owner approval. Historical 95 GB catalog and RCA1 local-only evidence are untouched.

## Additional historical DML discrepancy (do not overwrite receipt)

The E8_DML_yolo26n_rep3_pub2 receipt reports GPU_PASS_VALIDATED from its
independent profile and numerical parity, but it also declares a synthetic
%CACHE% environment key that was not echoed. Its complete receipt is therefore
RECEIPT_INVALID_ENV_NOT_ECHOED. Treat DML as an auxiliary historical control
with incomplete environment attestation, not a strictly verified receipt.
Only a new immutable DML attempt with a corrected plan can close this evidence
issue. AMDGPU-first core cold-start findings are not based on that DML receipt.
