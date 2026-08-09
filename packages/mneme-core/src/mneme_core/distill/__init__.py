"""Adaptive Context Layer: token-efficiency primitives shared by hooks,
retrieval, and injection.

Five components, each independently useful, designed to compose:

* ``shell_compress`` shrinks Bash tool output before storage and
  context injection. Whitespace runs, ANSI escapes, repeated adjacent
  lines, and long directory listings are all collapsed.
* ``injection_dedup`` tracks which content hashes the current session
  has already seen so retrieval does not re-inject the same memory.
* ``adaptive_topk`` chooses a retrieval count based on the current
  context window usage. Low usage gets a wide net, high usage gets a
  narrow one.
* ``compressed_format`` selects between three injection format levels
  (full, keypoints, ref) so re-injection of a known doc costs less
  than first-injection.
* ``audit`` is the CLI surface that reports per-session token spend
  with actionable recommendations.

The layer is mneme-native. It draws conceptual inspiration from
production shell-rewriter, context-budget, and dedup-cache patterns
proven elsewhere, but the architecture, API, and integration points
are owned by mneme. No external dependency is bundled.
"""

from .adaptive_topk import (
    DEFAULT_HIGH_USAGE_THRESHOLD,
    DEFAULT_LOW_USAGE_THRESHOLD,
    DEFAULT_MAX_K,
    DEFAULT_MIN_K,
    TopKPolicy,
    adaptive_topk,
)
from .compressed_format import (
    InjectionFormat,
    InjectionInput,
    render_injection,
    select_format,
)
from .injection_dedup import (
    InjectionTracker,
    has_injected,
    load_tracker,
    mark_injected,
    save_tracker,
)
from .shell_compress import (
    COMPRESS_MIN_BYTES,
    DEFAULT_MAX_CHARS,
    SHELL_OUTPUT_KEYS,
    SHELL_TOOL_NAME,
    CompressionStats,
    ShellCompressOpts,
    compress_shell_output,
    iter_compressible_outputs,
)

__all__ = [
    "COMPRESS_MIN_BYTES",
    "SHELL_OUTPUT_KEYS",
    "SHELL_TOOL_NAME",
    "DEFAULT_MAX_CHARS",
    "DEFAULT_HIGH_USAGE_THRESHOLD",
    "DEFAULT_LOW_USAGE_THRESHOLD",
    "DEFAULT_MAX_K",
    "DEFAULT_MIN_K",
    "CompressionStats",
    "InjectionFormat",
    "InjectionInput",
    "InjectionTracker",
    "ShellCompressOpts",
    "TopKPolicy",
    "adaptive_topk",
    "compress_shell_output",
    "iter_compressible_outputs",
    "has_injected",
    "load_tracker",
    "mark_injected",
    "render_injection",
    "save_tracker",
    "select_format",
]
