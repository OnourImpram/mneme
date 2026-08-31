"""A refusal has to say why, and it has to say it without quoting the document.

WHY THIS EXISTS
The indexer logs which file it dropped, which answered half of the question a
count could not. The other half stayed unanswered: every skip said only
"frontmatter YAML is malformed", so diagnosing 31 refused files still meant
writing a script to re-parse them by hand. The parser already knows what it
expected and where it stopped; the error was throwing that away.

The privacy half is the reason this is not simply ``str(exc)``. PyYAML's
``MarkedYAMLError.__str__`` embeds a snippet of the offending source line, and
a frontmatter line can carry private content — the same content the indexer
redacts before storing. So the detail is rebuilt from ``problem`` and
``problem_mark`` alone, and the negative control below asserts that the
document's own text never appears in the message.
"""

from __future__ import annotations

import pytest

from mneme_core.scope import DocumentScopeError, classify_markdown_scope, stamp_markdown_scope

# A real vault failure mode: Obsidian's wikilink list is not YAML. ``[[a]], [[b]]``
# parses as a flow sequence followed by a stray comma.
WIKILINK_DOC = """---
tarih: 2026-06-04
ilgili: [[Gizli-Dosya-Adi]], [[Ikinci-Gizli-Ad]]
---

body
"""

WELL_FORMED_DOC = """---
tarih: 2026-06-04
ilgili: "[[Gizli-Dosya-Adi]], [[Ikinci-Gizli-Ad]]"
---

body
"""


def test_the_message_names_the_parser_problem_and_the_line() -> None:
    with pytest.raises(DocumentScopeError) as excinfo:
        classify_markdown_scope(WIKILINK_DOC)

    message = str(excinfo.value)
    assert "frontmatter YAML is malformed" in message
    # The two facts that make it actionable.
    assert "frontmatter line 2" in message, message
    assert message != "frontmatter YAML is malformed", "no detail was added"


def test_negative_control_the_message_never_quotes_the_document() -> None:
    """``str(exc)`` would leak the line; this asserts we did not use it."""
    with pytest.raises(DocumentScopeError) as excinfo:
        classify_markdown_scope(WIKILINK_DOC)

    message = str(excinfo.value)
    assert "Gizli-Dosya-Adi" not in message
    assert "Ikinci-Gizli-Ad" not in message
    assert "ilgili" not in message
    # And prove the leak was actually possible: the discarded string does carry it.
    assert "Gizli-Dosya-Adi" in str(excinfo.value.__cause__)


def test_negative_control_well_formed_frontmatter_still_passes() -> None:
    """An error path that rejected everything would satisfy the tests above."""
    assert classify_markdown_scope(WELL_FORMED_DOC).has_frontmatter is True


def test_the_stamping_path_reports_the_same_detail() -> None:
    """Both raise sites matter; only fixing one leaves the write path mute."""
    with pytest.raises(DocumentScopeError) as excinfo:
        stamp_markdown_scope(WIKILINK_DOC, "default")

    assert "frontmatter line 2" in str(excinfo.value)


def test_an_unmarked_yaml_error_still_produces_a_message() -> None:
    """``problem``/``problem_mark`` are optional on the base YAMLError."""
    import yaml

    from mneme_core.scope import _malformed_frontmatter

    assert str(_malformed_frontmatter(yaml.YAMLError("bare"))) == (
        "frontmatter YAML is malformed"
    )
