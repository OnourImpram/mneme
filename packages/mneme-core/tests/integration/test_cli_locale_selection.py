"""``index rebuild`` must actually install the normalizers its locale names.

WHY THIS EXISTS
Three defects shipped together in 4.0, all the same shape: a normalizer was
defined, registered in ``_NORMALIZER_PROFILE``, mirrored on the TypeScript
side and unit-tested — and no CLI path ever reached it. Configuration is not
execution, and only an end-to-end assertion on the *written index* can tell
the two apart.

1. ``--locale en`` — the DEFAULT — fell through to ``_identity``. The index
   recorded ``normalization_profile = 'identity'``, which 4.0's locale gate
   refuses outright, so the documented rebuild command produced an index that
   answered ``INDEX_STALE_OR_LOCALE_MISMATCH`` to every query.
2. ``normalize_for_fts`` was never passed for either locale, so document
   bodies were stored unnormalized while queries were normalized. Measured:
   a body containing ``KIYASLAMA`` did not match ``"kıyaslama"``. Titles and
   paths were unaffected, which is why retrieval still looked healthy.
3. ``doctor``'s ``_KNOWN_PROFILES`` omitted ``en-unicode``, so a correctly
   built English index was reported as an unexpected value by its own doctor.

WHAT IS PINNED
The profile and language written into ``index_meta``, the normalization of
the body actually stored in ``documents_fts``, and doctor's verdict on each
profile. Every positive assertion has a negative control beside it: the
pre-fix configuration is rebuilt explicitly and shown to fail the same
assertion, because a gate that has never been seen to fail proves nothing.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from click.testing import CliRunner

from mneme_core.cli import cli
from mneme_core.fts5 import indexer as fts5_indexer
from mneme_core.fts5.locale.tr import normalize_tr

#: Chosen for the dotted/dotless axis: FTS5's unicode61 tokenizer already
#: folds ASCII case on its own, so a fixture like "HELLO" would match
#: "hello" even with no normalizer at all and would prove nothing. The
#: Turkish capital I is the one character whose fold only the locale
#: normalizer performs.
_TR_BODY = "---\ntitle: Kayit\n---\n\nKIYASLAMA yapildi.\n"
_EN_BODY = "---\ntitle: Record\n---\n\nThe API returned İstanbul.\n"


def _vault(tmp_path: Path, name: str, body: str) -> Path:
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    (root / ".mneme").mkdir(exist_ok=True)
    (root / "note.md").write_text(body, encoding="utf-8")
    return root


def _rebuild(root: Path, locale: str) -> dict:
    result = CliRunner().invoke(
        cli, ["index", "rebuild", "--vault", str(root), "--locale", locale]
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _meta(root: Path) -> dict[str, str]:
    conn = sqlite3.connect(root / ".mneme" / "fts5.sqlite")
    try:
        return dict(conn.execute("SELECT key, value FROM index_meta").fetchall())
    finally:
        conn.close()


def _fts_body(root: Path) -> str:
    conn = sqlite3.connect(root / ".mneme" / "fts5.sqlite")
    try:
        return conn.execute("SELECT content FROM documents_fts").fetchone()[0].strip()
    finally:
        conn.close()


def _matches(root: Path, query: str) -> int:
    conn = sqlite3.connect(root / ".mneme" / "fts5.sqlite")
    try:
        return conn.execute(
            "SELECT count(*) FROM documents_fts WHERE documents_fts MATCH ?", (query,)
        ).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. --locale en must select the English normalizers
# ---------------------------------------------------------------------------

def test_locale_en_records_the_en_unicode_profile(tmp_path: Path) -> None:
    """The default locale must produce an index the locale gate accepts."""
    root = _vault(tmp_path, "en", _EN_BODY)
    _rebuild(root, "en")

    meta = _meta(root)
    assert meta["normalization_profile"] == "en-unicode"
    assert meta["index_language"] == "en"


def test_locale_en_does_not_apply_the_turkish_fold(tmp_path: Path) -> None:
    """English must keep ``I`` as ``i``; the Turkish rule would give ``ı``."""
    root = _vault(tmp_path, "en-fold", _EN_BODY)
    _rebuild(root, "en")

    body = _fts_body(root)
    assert "api" in body
    assert "apı" not in body, "Turkish fold leaked into the English profile"
    # U+0130 folds to a plain 'i' without the combining dot that str.lower()
    # would introduce — the length invariant the snippet builder relies on.
    assert "istanbul" in body


def test_negative_control_the_pre_fix_config_recorded_identity(
    tmp_path: Path,
) -> None:
    """The shipped defect, rebuilt on purpose, to show these assertions bite.

    This is exactly what ``index rebuild`` constructed before the fix: no
    normalizer at all for any locale other than 'tr'.
    """
    root = _vault(tmp_path, "pre-fix", _EN_BODY)
    db = root / ".mneme" / "fts5.sqlite"
    conn = fts5_indexer.connect(db)
    try:
        fts5_indexer.ensure_schema(conn)
        fts5_indexer.index_vault(
            conn,
            fts5_indexer.IndexerConfig(
                vault_root=root,
                db_path=db,
                normalize=fts5_indexer._identity,
            ),
        )
    finally:
        conn.close()

    # 'identity' is precisely the value 4.0's locale gate refuses to serve.
    assert _meta(root)["normalization_profile"] == "identity"


# ---------------------------------------------------------------------------
# 2. --locale tr keeps its existing contract
# ---------------------------------------------------------------------------

def test_locale_tr_records_the_turkish_profiles(tmp_path: Path) -> None:
    """Turkish regression guard: the live vault's profile must not move."""
    root = _vault(tmp_path, "tr", _TR_BODY)
    _rebuild(root, "tr")

    meta = _meta(root)
    assert meta["normalization_profile"] == "tr-cldr"
    assert meta["ascii_normalization_profile"] == "tr-ascii-fold"
    assert meta["index_language"] == "tr"


# ---------------------------------------------------------------------------
# 3. Bodies must be normalized, not only titles
# ---------------------------------------------------------------------------

def test_body_is_normalized_under_the_turkish_locale(tmp_path: Path) -> None:
    """Measured defect: the body was stored raw while queries were folded."""
    root = _vault(tmp_path, "tr-body", _TR_BODY)
    _rebuild(root, "tr")

    assert _fts_body(root) == "kıyaslama yapildi."
    assert _matches(root, '"kıyaslama"') == 1


def test_negative_control_an_unnormalized_body_misses_the_folded_query(
    tmp_path: Path,
) -> None:
    """Without ``normalize_for_fts`` the same query returns nothing.

    Without this control the assertion above could be satisfied by FTS5's
    own unicode61 folding rather than by our normalizer, and the test would
    pass while the defect stayed live.
    """
    root = _vault(tmp_path, "tr-body-raw", _TR_BODY)
    db = root / ".mneme" / "fts5.sqlite"
    conn = fts5_indexer.connect(db)
    try:
        fts5_indexer.ensure_schema(conn)
        fts5_indexer.index_vault(
            conn,
            fts5_indexer.IndexerConfig(
                vault_root=root,
                db_path=db,
                normalize=normalize_tr,  # title folded, body deliberately not
            ),
        )
    finally:
        conn.close()

    assert _fts_body(root) == "KIYASLAMA yapildi."
    assert _matches(root, '"kıyaslama"') == 0


# ---------------------------------------------------------------------------
# 4. doctor must recognise every profile the indexer can write
# ---------------------------------------------------------------------------

def _doctor(root: Path) -> dict:
    result = CliRunner().invoke(cli, ["doctor", "--vault", str(root)])
    assert result.exit_code in (0, 1), result.output
    data = json.loads(result.output)
    return next(c for c in data["checks"] if c["name"] == "locale_profile")


def test_doctor_accepts_an_english_index(tmp_path: Path) -> None:
    root = _vault(tmp_path, "en-doctor", _EN_BODY)
    _rebuild(root, "en")

    check = _doctor(root)
    assert check["status"] == "ok", check
    assert "en-unicode" in check["detail"]


def test_negative_control_doctor_still_warns_on_an_unknown_profile(
    tmp_path: Path,
) -> None:
    """Widening the set must not turn the check into an unconditional pass."""
    root = _vault(tmp_path, "en-doctor-bad", _EN_BODY)
    _rebuild(root, "en")
    conn = sqlite3.connect(root / ".mneme" / "fts5.sqlite")
    try:
        conn.execute(
            "UPDATE index_meta SET value='klingon-fold'"
            " WHERE key='normalization_profile'"
        )
        conn.commit()
    finally:
        conn.close()

    check = _doctor(root)
    assert check["status"] == "warn", check
    assert "klingon-fold" in check["detail"]
