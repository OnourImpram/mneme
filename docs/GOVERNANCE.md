# Governance

How decisions get made and how the project survives the loss of any single maintainer.

## Current Status

- **Project lead**: Onour Impram ([TheGoatPsy](https://github.com/TheGoatPsy)).
- **Year 1 model**: Benevolent Dictator. Sole maintainer, sole release authority. Decisions logged as ADR entries in `docs/ARCHITECTURE.md`.
- **Co-maintainer recruitment**: still open as of the 3.x line. Until one lands, the interim release rule below applies.

## Decision Process

1. **Trivial changes** (typo fixes, dependency bumps, doc clarifications) can be merged by any maintainer after one approving review.
2. **Substantive changes** (new features, API changes, hook behavior, retrieval algorithm changes) require an Architecture Decision Record in `docs/ARCHITECTURE.md` and a pull request that links to it.
3. **Breaking changes** require a deprecation notice in `CHANGELOG.md` for at least one minor release before removal.

## Release Authority

- Patch releases (`v1.0.x`): any maintainer with publish credentials.
- Minor releases (`v1.x.0`): project lead approval.
- Major releases (e.g. `v3.0.0`): project lead approval plus at least one co-maintainer concurrence. **Interim rule while the project has a single maintainer**: the concurrence requirement is replaced by a public intent announcement (issue or discussion) at least 7 days before the tag, and the decision is recorded as an ADR in `docs/ARCHITECTURE.md` (ADR-015 documents the 3.0.0 relicense precedent).

## Issue Service Level

- **P0** (crash, data loss): 24-hour acknowledgment, 48-hour hot-fix.
- **P1** (significant regression, blocker): 7-day response.
- **P2** (minor bug, feature gap): 14-day response.
- **P3** (enhancement request): triaged monthly.
- **Stale**: issues without activity for 60 days are auto-closed with a comment inviting reopening.

## Co-Maintainer Compensation

If GitHub Sponsors revenue accrues, co-maintainers receive a documented share. Public bylined credit appears in `README.md` acknowledgments and in release notes. Release authority is delegated case by case as trust builds.

## Bus Factor

The project lead acknowledges that single-maintainer infrastructure is a known adoption friction point. The co-maintainer recruitment plan and the simplicity of the v1.0 API surface are deliberate mitigations.

If the project lead becomes unable to maintain mneme for more than 90 days without a co-maintainer in place, the GitHub organization access is preserved through the GitHub recovery process and the project enters caretaker mode. Caretaker maintainers can publish security patches but cannot ship breaking changes.

## Conflict Resolution

Disagreements between maintainers are resolved by the project lead in year 1. From v2.0 onward, conflicts go to a vote of all co-maintainers with the project lead breaking ties.

## License Changes

From the 3.0.0 release onward mneme is licensed under the Apache License 2.0 (see ADR-015). The MIT commitment for the published 1.x and 2.x artifacts is permanent: everything released under MIT remains available under MIT.

The 3.0 relicensing decision was taken on 2026-06-10 by unanimous agreement of all maintainers (sole maintainer at the time of decision) and announced publicly ahead of the 3.0.0 release, satisfying the notice requirement below.

Any future relicensing requires unanimous agreement of all co-maintainers and at least 30 days of public notice.
