"""Vault contract: configuration, frontmatter spec, atomic writes.

The vault is the user-owned source of truth for mneme. Everything else
(FTS5 index, dense embeddings, knowledge graph) is derived and can be
rebuilt from the vault alone (constraint C1).
"""

from mneme_core.vault.atomic_write import atomic_write_text
from mneme_core.vault.config import VaultConfig, VaultNotFoundError
from mneme_core.vault.frontmatter import Frontmatter, parse, serialize

__all__ = [
    "Frontmatter",
    "VaultConfig",
    "VaultNotFoundError",
    "atomic_write_text",
    "parse",
    "serialize",
]
