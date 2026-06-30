"""Dense retrieval adapter seam — feature-flagged protocol and factory stub.

This module formalises the adapter contract for the dense retrieval backend
and provides a feature-flagged factory that selects the adapter at startup.

Default behaviour (flag OFF)
-----------------------------
When ``MNEME_DENSE_ADAPTER`` is unset or empty, :func:`load_dense_adapter`
returns :class:`HashingAdapter`, which wraps :func:`~mneme_core.retrieval.dense.hashing_embed`
with no extra dependencies, no network, and no model.  Behaviour is
identical to calling ``hashing_embed`` directly — zero change to the
shipped retrieval path.

Opt-in heavier adapter
-----------------------
Set ``MNEME_DENSE_ADAPTER`` to the fully-qualified dotted class path of a
:class:`DenseAdapter`-conforming implementation, e.g.::

    MNEME_DENSE_ADAPTER=mypackage.onnx_adapter.OnnxAdapter

The class is imported and instantiated with no arguments.  On any failure
the factory falls back to :class:`HashingAdapter` with a ``stderr`` warning
so startup is never broken by a misconfigured adapter.

Wiring to DenseBackend
-----------------------
Pass ``adapter.embed_fn`` and ``adapter.dim`` to
:class:`~mneme_core.retrieval.dense.DenseBackend` and
:func:`~mneme_core.retrieval.dense.build_dense_index`::

    adapter = load_dense_adapter()
    index = build_dense_index(db_path, embed_fn=adapter.embed_fn, dim=adapter.dim)
    backend = DenseBackend(index, embed_fn=adapter.embed_fn)

:func:`~mneme_core.retrieval.rrf.retrieve` already accepts
``dense_backend: RetrievalBackend | None``; no change is needed there.
"""

from __future__ import annotations

import importlib
import os
import sys
from typing import Protocol, runtime_checkable

from .dense import EmbedFn, hashing_embed

__all__ = [
    "DenseAdapter",
    "HashingAdapter",
    "load_dense_adapter",
]

# Environment variable that selects the adapter class (default OFF = unset).
_ENV_VAR = "MNEME_DENSE_ADAPTER"

# Default embedding dimension used by HashingAdapter / hashing_embed.
_DEFAULT_DIM = 256


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class DenseAdapter(Protocol):
    """Contract for a pluggable dense-vector embedding adapter.

    Implementors supply:

    * ``name`` — short string identifier (e.g. ``"hashing"``,
      ``"sentence-transformers"``).
    * ``dim`` — output vector dimension; must match the dimension used when
      the index was built.  Mixing dimensions produces silent garbage results,
      so callers should store ``dim`` in ``index_meta`` alongside
      ``normalization_profile`` and validate on load.
    * ``embed_fn`` — callable ``str -> tuple[float, ...]`` producing a
      **unit-norm** vector of length ``dim``.  Must be deterministic for a
      given ``(text, dim)`` pair.  Must never raise; callers treat any
      exception as a failed search leg, not a fatal error.

    This is a :func:`typing.runtime_checkable` Protocol, so
    ``isinstance(obj, DenseAdapter)`` works without ABC inheritance and
    can be used as a sanity check in :func:`load_dense_adapter`.
    """

    @property
    def name(self) -> str:
        """Short identifier, e.g. ``"hashing"`` or ``"sentence-transformers"``."""
        ...  # pragma: no cover

    @property
    def dim(self) -> int:
        """Output vector dimension (must match the built index)."""
        ...  # pragma: no cover

    @property
    def embed_fn(self) -> EmbedFn:
        """Embedding callable: ``str -> tuple[float, ...]`` (unit-norm)."""
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# Default adapter
# ---------------------------------------------------------------------------


class HashingAdapter:
    """Default adapter: wraps ``hashing_embed`` with no extra dependencies.

    Selected when ``MNEME_DENSE_ADAPTER`` is unset or empty.  Produces
    behaviour identical to the pre-stub retrieval path.  No network, no
    model, no I/O.
    """

    def __init__(self, dim: int = _DEFAULT_DIM) -> None:
        self._dim = dim

    @property
    def name(self) -> str:
        return "hashing"

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def embed_fn(self) -> EmbedFn:
        dim = self._dim

        def _embed(text: str) -> tuple[float, ...]:
            return hashing_embed(text, dim=dim)

        return _embed


# ---------------------------------------------------------------------------
# Feature-flagged factory
# ---------------------------------------------------------------------------


def load_dense_adapter() -> DenseAdapter:
    """Return the adapter selected by ``MNEME_DENSE_ADAPTER`` (default OFF).

    When ``MNEME_DENSE_ADAPTER`` is unset or empty :class:`HashingAdapter`
    is returned — behaviour identical to before this module existed.

    When the env var names a dotted class path (``module.ClassName``), the
    class is imported and instantiated with no arguments.  A
    ``runtime_checkable`` ``isinstance`` check ensures the class satisfies
    :class:`DenseAdapter`.  On any failure (``ImportError``, ``AttributeError``,
    failed ``isinstance``, raised ``__init__``) the function writes a
    diagnostic to ``stderr`` and returns :class:`HashingAdapter`.

    Returns:
        A :class:`DenseAdapter`-conforming instance.  Never raises.
    """
    class_path = os.environ.get(_ENV_VAR, "").strip()
    if not class_path:
        return HashingAdapter()

    try:
        module_path, class_name = class_path.rsplit(".", 1)
    except ValueError:
        sys.stderr.write(
            f"[mneme-core] {_ENV_VAR}={class_path!r} is not a dotted class path "
            f"(expected 'module.ClassName'); falling back to HashingAdapter.\n"
        )
        return HashingAdapter()

    try:
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        instance = cls()
        if not isinstance(instance, DenseAdapter):
            raise TypeError(
                f"{class_path!r} does not satisfy the DenseAdapter protocol "
                f"(missing .name, .dim, or .embed_fn)"
            )
        return instance
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(
            f"[mneme-core] {_ENV_VAR}={class_path!r} failed to load "
            f"({type(exc).__name__}: {exc}); falling back to HashingAdapter.\n"
        )
        return HashingAdapter()
