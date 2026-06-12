"""Localized compression-prompt preset resolution (compress-<lang>.md)."""

from __future__ import annotations

from pathlib import Path

from mneme_core.compression.config import CompressionConfig, read_config
from mneme_core.compression.pipeline import load_prompt


class TestLoadPromptLocale:
    def test_default_is_english(self) -> None:
        prompt = load_prompt()
        assert "Compression Rubric" in prompt

    def test_turkish_preset_selected(self) -> None:
        prompt = load_prompt(language="tr")
        assert "Sıkıştırma Rubriği" in prompt

    def test_unknown_language_falls_back_to_english(self) -> None:
        prompt = load_prompt(language="xx")
        assert "Compression Rubric" in prompt

    def test_explicit_path_override_wins(self, tmp_path: Path) -> None:
        override = tmp_path / "custom.md"
        override.write_text("CUSTOM PROMPT", encoding="utf-8")
        assert load_prompt(override, language="tr") == "CUSTOM PROMPT"

    def test_structural_tokens_identical_across_presets(self) -> None:
        """The output contract is language-independent: every structural
        token the pipeline and indexer rely on must appear verbatim in
        every shipped preset."""
        en = load_prompt(language="en")
        tr = load_prompt(language="tr")
        for token in (
            "type: compressed",
            "source_session_id",
            "compression_score",
            "content_hash",
            "confidentiality: internal",
            "schema_version: 1",
        ):
            assert token in en
            assert token in tr


class TestCompressionConfigLanguage:
    def test_default_language_en(self, tmp_path: Path) -> None:
        cfg = read_config(tmp_path / "missing.json")
        assert cfg.language == "en"

    def test_language_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "compression.json"
        path.write_text('{"enabled": false, "language": "tr"}', encoding="utf-8")
        cfg = read_config(path)
        assert cfg.language == "tr"
        assert isinstance(cfg, CompressionConfig)
