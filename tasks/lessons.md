# Lessons

## 2026-07-18

- Correction: Do not reframe repository hardening and release verification as a separate cyber-security exercise.
- Rule: Report only the concrete repository defect, affected behavior, test evidence, and release impact. Avoid dramatic domain labels when the task is ordinary code and release engineering.
- Correction: A branch-filtered workflow poll waited on an empty result even though the dispatched run IDs existed and had completed.
- Rule: Persist dispatched GitHub Actions run IDs and query those IDs directly. Treat an empty poll result as an immediate observer error, not as a still-running workflow.
- Correction: The Stop implementation used a 0.5-second session-log lock budget while the installer contract documented five seconds, allowing normal Windows write bursts to lose session blocks.
- Rule: Contract-test production timeout values against their outer hook ceiling. Use a short monkeypatched deadline for fail-soft unit tests instead of weakening the production concurrency budget.

## 2026-08-12

- Failed assumption: SHA-1 inside UUIDv5 was initially treated as a likely static-analysis false positive because the value looked like a non-security identifier.
- Rule: Before dismissing a weak-cryptography alert on an identifier, trace every authorization, deduplication, journal, and apply decision that consumes it. If identifier aliasing can cross a trust gate, replace the primitive and prove cross-language parity instead of suppressing the alert.
- Failed verification: Running source tests against a globally installed package produced misleading failures and stale proposal IDs.
- Rule: Print the imported module path before Python verification. For multi-package repositories, use an isolated editable environment so coverage and tests execute the checkout under review.
