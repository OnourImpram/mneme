"""Compression pipeline: staging capture + background AI compression.

Two layers, kept separate by design:

1. **Staging (Phase B)**. Deterministic, on the critical path. Tool
   events are captured to JSONL with privacy redaction and a rolling
   size cap. Never calls an LLM. This is what every install profile
   gets out of the box.

2. **Background compression (Phase F)**. Opt-in, off the critical
   path, calls an LLM provider to compress staged events into
   vault-quality markdown with a 4-D quality rubric. Honors a
   configurable monthly cost cap. Disabled by default.

The two layers communicate through the staging JSONL files only. The
Stop hook never blocks on compression.
"""

from .config import (
    DEFAULT_COST_CAP_USD_MONTHLY,
    DEFAULT_MAX_PAYLOAD_BYTES,
    DEFAULT_MAX_TOKENS,
    SCHEMA_VERSION,
    CompressionConfig,
    ensure_default_config,
    read_config,
    write_config,
)
from .ledger import (
    CapCheck,
    LedgerCorruptError,
    append_cost,
    check_cap,
    month_to_date_spend,
    rolling_30d_spend,
)
from .llm import (
    DEFAULT_MODEL,
    DEFAULT_PRICING_PER_MTOK,
    AnthropicProvider,
    CompressionResult,
    LlmCallSpec,
    LlmProvider,
    LlmProviderError,
    cost_for_usage,
)
from .pipeline import (
    DEFAULT_OBSERVATION_MARKER,
    PipelineConfig,
    RunReport,
    load_prompt,
    pipeline_config_from_vault,
    run_compression,
)
from .staging import (
    DEFAULT_CAPTURE_TOOLS,
    DEFAULT_SIZE_CAP_BYTES,
    StagingConfig,
    capture_event,
    compute_content_hash,
    enforce_size_cap,
    redact_private,
)

__all__ = [
    # Phase B staging
    "DEFAULT_CAPTURE_TOOLS",
    "DEFAULT_SIZE_CAP_BYTES",
    "StagingConfig",
    "capture_event",
    "compute_content_hash",
    "enforce_size_cap",
    "redact_private",
    # Phase F config
    "CompressionConfig",
    "DEFAULT_COST_CAP_USD_MONTHLY",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MAX_PAYLOAD_BYTES",
    "SCHEMA_VERSION",
    "ensure_default_config",
    "read_config",
    "write_config",
    # Phase F ledger
    "CapCheck",
    "LedgerCorruptError",
    "append_cost",
    "check_cap",
    "month_to_date_spend",
    "rolling_30d_spend",
    # Phase F llm
    "DEFAULT_MODEL",
    "DEFAULT_PRICING_PER_MTOK",
    "AnthropicProvider",
    "CompressionResult",
    "LlmCallSpec",
    "LlmProvider",
    "LlmProviderError",
    "cost_for_usage",
    # Phase F pipeline
    "DEFAULT_OBSERVATION_MARKER",
    "PipelineConfig",
    "RunReport",
    "load_prompt",
    "pipeline_config_from_vault",
    "run_compression",
]
