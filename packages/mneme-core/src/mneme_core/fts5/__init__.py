"""FTS5 full-text search indexer and locale-aware tokenization."""

from mneme_core.fts5.locale.tr import normalize_tr, normalize_tr_for_fts

__all__ = ["normalize_tr", "normalize_tr_for_fts"]
