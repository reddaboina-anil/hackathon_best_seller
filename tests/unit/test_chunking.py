"""Unit tests for ``ParentChildChunker``."""

from __future__ import annotations

from lr_bestsellers.utils.chunking import (
    ChunkerConfig,
    ParentChildChunker,
    count_tokens,
    header_prefix,
)

_FIXTURE_MD = """# Title

## Overview

Activation is the process of making a segment available on a destination.
It includes matching, digest creation, and delivery.

### FULL delivery

FULL delivery sends the complete current matched universe to the destination
and is used for first-time activation and full refreshes of the audience.

### INCREMENTAL delivery

INCREMENTAL delivery sends only net-new or dropped identifiers since the
previous successful digest so destinations stay in sync cheaply.

## Matching

SSA authorizes field_id and value_id pairs for a destination account.
"""


class TestHelpers:
    """Tests for header and token helpers."""

    def test_header_without_subsection(self) -> None:
        """Header omits subsection clause when none is provided."""
        assert header_prefix("activation.md", "Overview", None) == (
            "[Doc: activation.md | Section: Overview]"
        )

    def test_header_with_subsection(self) -> None:
        """Header includes subsection when provided."""
        assert header_prefix("activation.md", "Delivery Modes", "FULL") == (
            "[Doc: activation.md | Section: Delivery Modes | Subsection: FULL]"
        )

    def test_count_tokens_empty(self) -> None:
        """Empty string has zero tokens."""
        assert count_tokens("") == 0
        assert count_tokens("   ") == 0

    def test_count_tokens_words(self) -> None:
        """Token count follows whitespace split."""
        assert count_tokens("one two three") == 3


class TestParentChildChunker:
    """Tests for markdown splitting."""

    def test_h2_parents_created(self) -> None:
        """Each H2 becomes a parent chunk."""
        chunker = ParentChildChunker()
        parents = chunker.chunk_markdown(_FIXTURE_MD, "activation.md")
        sections = {p.section for p in parents}
        assert "Overview" in sections
        assert "Matching" in sections

    def test_child_header_injection(self) -> None:
        """Every child text starts with a provenance header."""
        chunker = ParentChildChunker()
        parents = chunker.chunk_markdown(_FIXTURE_MD, "activation.md")
        children = [c for p in parents for c in p.children]
        assert children
        for child in children:
            assert child.text.startswith("[Doc: activation.md |")
            assert child.filename == "activation.md"
            assert child.parent_id == child.chunk_id.rsplit("_", 1)[0]

    def test_subsection_children(self) -> None:
        """H3 headings are recorded on children."""
        chunker = ParentChildChunker()
        parents = chunker.chunk_markdown(_FIXTURE_MD, "activation.md")
        overview = next(p for p in parents if p.section == "Overview")
        subsections = {c.subsection for c in overview.children}
        assert "FULL delivery" in subsections or "INCREMENTAL delivery" in subsections

    def test_small_window_produces_multiple_children(self) -> None:
        """Tiny child_tokens force multiple windows on a long section."""
        chunker = ParentChildChunker(ChunkerConfig(child_tokens=20, overlap_tokens=4))
        long_body = "## Big\n\n" + " ".join(f"word{i}" for i in range(80))
        parents = chunker.chunk_markdown(long_body, "big.md")
        assert len(parents) == 1
        assert len(parents[0].children) > 1

    def test_no_h2_uses_overview(self) -> None:
        """Files without H2 still produce an Overview parent."""
        chunker = ParentChildChunker()
        parents = chunker.chunk_markdown("# Only h1\n\nSome body text here.", "plain.md")
        assert len(parents) == 1
        assert parents[0].section == "Overview"
        assert "Some body text here." in parents[0].children[0].text
