"""Telemetry writer with configurable PII redaction.

Provides bring-your-own-patterns PII redaction. The library ships a
small set of optional pattern constants users can compose, but no
patterns are enabled by default. Privacy posture is opt-in, not
opt-out, because false positives have a real productivity cost.
"""

from mneme_core.telemetry.writer import (
    COMMON_GENERIC_CREDENTIAL_PATTERNS,
    COMMON_PII_TURKISH_NATIONAL_ID,
    VALID_EVENT_CLASSES,
    PiiPattern,
    TelemetryConfig,
    redact_pii,
    write_event,
)

__all__ = [
    "COMMON_GENERIC_CREDENTIAL_PATTERNS",
    "COMMON_PII_TURKISH_NATIONAL_ID",
    "VALID_EVENT_CLASSES",
    "PiiPattern",
    "TelemetryConfig",
    "redact_pii",
    "write_event",
]
