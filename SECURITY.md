# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in mneme, please do not open a public GitHub issue. Instead, report it privately via one of the following channels:

1. GitHub Security Advisories at https://github.com/TheGoatPsy/mneme/security/advisories/new
2. Email: onuribram@outlook.com (put "mneme security" in the subject line)

Please include:

- A description of the vulnerability and its potential impact.
- Steps to reproduce the issue.
- Any proof-of-concept code or screenshots.
- Your name and affiliation if you would like public credit after the fix is published.

## Response Timeline

- Acknowledgment within 48 hours.
- Initial assessment within 7 days.
- Fix release target depending on severity.
  - Critical: 7 days.
  - High: 14 days.
  - Medium: 30 days.
  - Low: next scheduled release.

## Supported Versions

Only the latest minor release of mneme receives security patches. Older versions should upgrade.

## Out of Scope

- Third-party dependencies (please report upstream).
- Self-hosted Neo4j or LEANN runtime issues unless triggered by mneme code.
- Vulnerabilities in user-supplied vault content.
- Issues that require physical access to the machine running mneme.

## Disclosure Policy

Coordinated disclosure: we will work with you on a public disclosure date once a fix is available. Default window is 90 days from initial report. Credit will be given in `CHANGELOG.md` and the GitHub Security Advisory unless you request anonymity.
