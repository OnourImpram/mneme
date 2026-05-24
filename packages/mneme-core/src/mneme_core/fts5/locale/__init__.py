"""Language-specific normalizers for FTS5 ingest and query.

Each locale module exports `normalize_<code>` and `normalize_<code>_for_fts`
functions that map an input string to a canonical form suitable for
SQLite FTS5 indexing.
"""
