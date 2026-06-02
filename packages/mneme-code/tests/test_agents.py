"""Tests for mneme_code.agents: ProceduralMemory parsing and markdown rendering."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from mneme_code.agents import ProceduralMemory, parse_agents_md, procedural_to_markdown

# ---------------------------------------------------------------------------
# Fixtures / constants
# ---------------------------------------------------------------------------

_SOURCE = "docs/AGENTS.md"

# A realistic two-section AGENTS.md with a private token in the second body.
_AGENTS_MD = """\
# Repo Conventions

Some preamble text that should be discarded.

## Setup

Run the following to bootstrap the environment:

```bash
pip install -e .
```

After that you are ready.

## Coding Rules

Never commit <private>secret-token-xyz</private> to version control.
Only use approved patterns.
"""

# ---------------------------------------------------------------------------
# parse_agents_md
# ---------------------------------------------------------------------------


class TestParseAgentsMd:
    def test_two_sections_returned(self) -> None:
        mems = parse_agents_md(_AGENTS_MD, source_path=_SOURCE)
        assert len(mems) == 2

    def test_titles_correct(self) -> None:
        mems = parse_agents_md(_AGENTS_MD, source_path=_SOURCE)
        assert mems[0].title == "Setup"
        assert mems[1].title == "Coding Rules"

    def test_command_captured_in_first_section(self) -> None:
        mems = parse_agents_md(_AGENTS_MD, source_path=_SOURCE)
        setup = mems[0]
        assert len(setup.commands) == 1
        assert "pip install -e ." in setup.commands[0]

    def test_second_section_has_no_commands(self) -> None:
        mems = parse_agents_md(_AGENTS_MD, source_path=_SOURCE)
        assert mems[1].commands == ()

    def test_redaction_removes_secret_from_content(self) -> None:
        mems = parse_agents_md(_AGENTS_MD, source_path=_SOURCE)
        assert "secret-token-xyz" not in mems[1].content

    def test_redaction_replacement_token_present(self) -> None:
        mems = parse_agents_md(_AGENTS_MD, source_path=_SOURCE)
        assert "[REDACTED]" in mems[1].content

    def test_content_hash_is_64_hex_chars(self) -> None:
        mems = parse_agents_md(_AGENTS_MD, source_path=_SOURCE)
        for mem in mems:
            assert len(mem.content_hash) == 64
            assert all(c in "0123456789abcdef" for c in mem.content_hash)

    def test_all_sections_share_same_content_hash(self) -> None:
        # content_hash is hash of the *full* file, so both records agree.
        mems = parse_agents_md(_AGENTS_MD, source_path=_SOURCE)
        assert mems[0].content_hash == mems[1].content_hash

    def test_deterministic_parse_twice_identical(self) -> None:
        a = parse_agents_md(_AGENTS_MD, source_path=_SOURCE)
        b = parse_agents_md(_AGENTS_MD, source_path=_SOURCE)
        assert a == b

    def test_memory_id_is_valid_uuid(self) -> None:
        import uuid

        mems = parse_agents_md(_AGENTS_MD, source_path=_SOURCE)
        for mem in mems:
            # Must not raise
            parsed = uuid.UUID(mem.memory_id)
            assert parsed.version == 5

    def test_source_path_stored_correctly(self) -> None:
        mems = parse_agents_md(_AGENTS_MD, source_path=_SOURCE)
        for mem in mems:
            assert mem.source_path == _SOURCE

    def test_trust_and_confidence_defaults(self) -> None:
        mems = parse_agents_md(_AGENTS_MD, source_path=_SOURCE)
        for mem in mems:
            assert mem.trust == "user"
            assert mem.confidence == "EXTRACTED"

    def test_document_order_preserved(self) -> None:
        mems = parse_agents_md(_AGENTS_MD, source_path=_SOURCE)
        # Setup appears before Coding Rules in the source.
        assert mems[0].title == "Setup"
        assert mems[1].title == "Coding Rules"

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_empty_string_returns_empty_list(self) -> None:
        assert parse_agents_md("", source_path=_SOURCE) == []

    def test_whitespace_only_returns_empty_list(self) -> None:
        assert parse_agents_md("   \n\n  ", source_path=_SOURCE) == []

    def test_no_h2_headings_returns_empty_list(self) -> None:
        # Only a level-1 heading and prose — no ## sections.
        text = "# Title\n\nSome content here.\n"
        result = parse_agents_md(text, source_path=_SOURCE)
        # Design choice: no ## sections → [] (documented in docstring).
        assert result == []

    def test_never_raises_on_garbage(self) -> None:
        # Pathological input must return [] not raise.
        result = parse_agents_md("\x00\x01\x02\xff" * 100, source_path=_SOURCE)
        assert isinstance(result, list)

    def test_section_with_no_body_has_empty_content(self) -> None:
        text = "## Empty Section\n\n## Another Section\n\nSome text.\n"
        mems = parse_agents_md(text, source_path=_SOURCE)
        assert len(mems) == 2
        assert mems[0].content == ""
        assert mems[0].commands == ()


# ---------------------------------------------------------------------------
# procedural_to_markdown
# ---------------------------------------------------------------------------


class TestProceduralToMarkdown:
    def _make_mem(self, title: str = "Setup", content: str = "Do stuff.") -> ProceduralMemory:
        mems = parse_agents_md(
            f"## {title}\n\n{content}\n",
            source_path=_SOURCE,
        )
        assert len(mems) == 1
        return mems[0]

    def _created(self) -> datetime:
        return datetime(2026, 6, 2, 12, 0, 0, tzinfo=UTC)

    def test_type_reference_in_frontmatter(self) -> None:
        md = procedural_to_markdown(self._make_mem(), created=self._created())
        assert "type: reference" in md

    def test_both_tags_present(self) -> None:
        md = procedural_to_markdown(self._make_mem(), created=self._created())
        assert "  - procedure" in md
        assert "  - agents-md" in md

    def test_created_is_utc_isoformat(self) -> None:
        md = procedural_to_markdown(self._make_mem(), created=self._created())
        assert "created: 2026-06-02T12:00:00.000000+00:00" in md

    def test_created_naive_normalised_to_utc(self) -> None:
        naive = datetime(2026, 6, 2, 12, 0, 0)  # no tzinfo
        md = procedural_to_markdown(self._make_mem(), created=naive)
        assert "created: 2026-06-02T12:00:00.000000+00:00" in md

    def test_created_offset_normalised_to_utc(self) -> None:
        # +03:00 → 2026-06-02T09:00:00Z
        offset = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone(timedelta(hours=3)))
        md = procedural_to_markdown(self._make_mem(), created=offset)
        assert "created: 2026-06-02T09:00:00.000000+00:00" in md

    def test_title_in_body(self) -> None:
        md = procedural_to_markdown(self._make_mem(title="Setup"), created=self._created())
        assert "# Setup" in md

    def test_content_in_body(self) -> None:
        md = procedural_to_markdown(
            self._make_mem(content="Run pip install."), created=self._created()
        )
        assert "Run pip install." in md

    def test_commands_section_rendered(self) -> None:
        mems = parse_agents_md(
            "## Deploy\n\nDo this:\n\n```bash\nmake deploy\n```\n",
            source_path=_SOURCE,
        )
        assert len(mems) == 1
        md = procedural_to_markdown(mems[0], created=self._created())
        assert "## Commands" in md
        assert "make deploy" in md

    def test_no_commands_section_when_empty(self) -> None:
        md = procedural_to_markdown(self._make_mem(), created=self._created())
        assert "## Commands" not in md

    def test_redacted_content_absent_from_markdown(self) -> None:
        mems = parse_agents_md(
            "## Rules\n\nDo not share <private>secret-token-xyz</private>.\n",
            source_path=_SOURCE,
        )
        assert len(mems) == 1
        md = procedural_to_markdown(mems[0], created=self._created())
        assert "secret-token-xyz" not in md
        assert "[REDACTED]" in md

    def test_source_path_json_encoded_in_frontmatter(self) -> None:
        mems = parse_agents_md("## X\n\nBody.\n", source_path='docs/path with "quotes".md')
        assert len(mems) == 1
        md = procedural_to_markdown(mems[0], created=self._created())
        # JSON encoding must be present so special chars don't break YAML.
        assert "source_path:" in md

    def test_memory_id_in_frontmatter(self) -> None:
        mem = self._make_mem()
        md = procedural_to_markdown(mem, created=self._created())
        assert mem.memory_id in md

    def test_content_hash_in_frontmatter(self) -> None:
        mem = self._make_mem()
        md = procedural_to_markdown(mem, created=self._created())
        assert mem.content_hash in md

    def test_markdown_ends_with_newline(self) -> None:
        md = procedural_to_markdown(self._make_mem(), created=self._created())
        assert md.endswith("\n")

    def test_fence_aware_heading_not_split(self) -> None:
        """A3: a '## ' line inside a fenced code block is code, not a heading,
        so it must not split a phantom section."""
        text = "## Setup\n\n```bash\n## install dependencies\npip install -e .\n```\n"
        mems = parse_agents_md(text, source_path="AGENTS.md")
        assert len(mems) == 1
        assert mems[0].title == "Setup"
        assert any("pip install -e ." in c for c in mems[0].commands)
