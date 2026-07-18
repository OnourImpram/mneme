# Lessons

## 2026-07-18

- Correction: Do not reframe repository hardening and release verification as a separate cyber-security exercise.
- Rule: Report only the concrete repository defect, affected behavior, test evidence, and release impact. Avoid dramatic domain labels when the task is ordinary code and release engineering.
- Correction: A branch-filtered workflow poll waited on an empty result even though the dispatched run IDs existed and had completed.
- Rule: Persist dispatched GitHub Actions run IDs and query those IDs directly. Treat an empty poll result as an immediate observer error, not as a still-running workflow.
- Correction: The Stop implementation used a 0.5-second session-log lock budget while the installer contract documented five seconds, allowing normal Windows write bursts to lose session blocks.
- Rule: Contract-test production timeout values against their outer hook ceiling. Use a short monkeypatched deadline for fail-soft unit tests instead of weakening the production concurrency budget.
