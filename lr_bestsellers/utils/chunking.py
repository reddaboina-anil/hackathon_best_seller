"""Parent-child hierarchical chunking for ``knowledge_base/*.md`` files."""

from __future__ import annotations

import re
from typing import Final

from pydantic import BaseModel, Field

from lr_bestsellers.models.chunk import ChildChunk, ParentChunk

_H2_RE: Final[re.Pattern[str]] = re.compile(r"^## +(.+)$", re.MULTILINE)
_H3_RE: Final[re.Pattern[str]] = re.compile(r"^### +(.+)$", re.MULTILINE)
_DEFAULT_CHILD_TOKENS: Final[int] = 300
_DEFAULT_OVERLAP: Final[int] = 40


class ChunkerConfig(BaseModel):
    """Token-window settings for child splits.

    Attributes:
        child_tokens: Target child size in whitespace tokens.
        overlap_tokens: Overlap between consecutive children.
    """

    child_tokens: int = Field(_DEFAULT_CHILD_TOKENS, ge=20, le=2000)
    overlap_tokens: int = Field(_DEFAULT_OVERLAP, ge=0, le=500)


def count_tokens(text: str) -> int:
    """Approximate token count as whitespace-separated words.

    Args:
        text: Input string.

    Returns:
        At least 1 when ``text`` is non-empty; 0 when empty.
    """
    stripped = text.strip()
    if not stripped:
        return 0
    return max(1, len(stripped.split()))


def header_prefix(filename: str, section: str, subsection: str | None) -> str:
    """Build the provenance prefix injected into every child chunk.

    Args:
        filename: Source markdown filename.
        section: H2 heading.
        subsection: Optional H3 heading.

    Returns:
        Header line without a trailing newline.
    """
    if subsection:
        return f"[Doc: {filename} | Section: {section} | Subsection: {subsection}]"
    return f"[Doc: {filename} | Section: {section}]"


def _split_h2_sections(markdown: str) -> list[tuple[str, str]]:
    """Split markdown into (h2_title, body) pairs.

    Args:
        markdown: Full file contents.

    Returns:
        Sections; if no H2 exists, a single ``Overview`` section with the body
        after the optional H1.
    """
    matches = list(_H2_RE.finditer(markdown))
    if not matches:
        body = re.sub(r"^# .+\n+", "", markdown, count=1).strip()
        return [("Overview", body)]

    sections: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[start:end].strip()
        sections.append((title, body))
    return sections


def _split_h3(body: str) -> list[tuple[str | None, str]]:
    """Split an H2 body into optional H3 subsections.

    Args:
        body: Text under one H2 heading.

    Returns:
        List of ``(subsection_title_or_none, text)``.
    """
    matches = list(_H3_RE.finditer(body))
    if not matches:
        return [(None, body.strip())] if body.strip() else []

    parts: list[tuple[str | None, str]] = []
    preamble = body[: matches[0].start()].strip()
    if preamble:
        parts.append((None, preamble))
    for i, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        text = body[start:end].strip()
        if text:
            parts.append((title, text))
    return parts


def _window_tokens(tokens: list[str], size: int, overlap: int) -> list[list[str]]:
    """Slice a token list into overlapping windows.

    Args:
        tokens: Whitespace tokens.
        size: Window size.
        overlap: Overlap in tokens.

    Returns:
        Non-empty windows covering ``tokens``.
    """
    if not tokens:
        return []
    if size <= overlap:
        overlap = max(0, size // 4)
    step = max(1, size - overlap)
    windows: list[list[str]] = []
    start = 0
    while start < len(tokens):
        windows.append(tokens[start : start + size])
        if start + size >= len(tokens):
            break
        start += step
    return windows


class ParentChildChunker:
    """Split markdown into parent H2 sections and ~300-token children.

    Args:
        config: Optional window configuration.
    """

    def __init__(self, config: ChunkerConfig | None = None) -> None:
        """Store chunker configuration.

        Args:
            config: Window sizes; defaults to 300 / 40 tokens.
        """
        self._config = config or ChunkerConfig()

    def chunk_markdown(self, markdown: str, filename: str) -> list[ParentChunk]:
        r"""Produce parent chunks with populated children.

        Args:
            markdown: Full markdown file contents.
            filename: Source filename used in headers and ids.

        Returns:
            Parent chunks (possibly empty if the file has no body text).

        Example:
            >>> chunker = ParentChildChunker()
            >>> parents = chunker.chunk_markdown("## Hello\\n\\nWorld tokens here.", "x.md")
            >>> parents[0].section
            'Hello'
        """
        stem = filename.rsplit(".", 1)[0]
        parents: list[ParentChunk] = []
        for h2_index, (section, body) in enumerate(_split_h2_sections(markdown)):
            parent_id = f"{stem}_{h2_index}"
            children: list[ChildChunk] = []
            child_index = 0
            for subsection, text in _split_h3(body):
                tokens = text.split()
                windows = _window_tokens(
                    tokens,
                    self._config.child_tokens,
                    self._config.overlap_tokens,
                )
                if not windows and text.strip():
                    windows = [text.split()]
                for window in windows:
                    body_text = " ".join(window)
                    prefix = header_prefix(filename, section, subsection)
                    chunk_text = f"{prefix}\n{body_text}"
                    children.append(
                        ChildChunk(
                            chunk_id=f"{parent_id}_{child_index}",
                            parent_id=parent_id,
                            text=chunk_text,
                            filename=filename,
                            section=section,
                            subsection=subsection,
                            token_count=count_tokens(chunk_text),
                        )
                    )
                    child_index += 1
            if not children:
                continue
            parents.append(
                ParentChunk(
                    parent_id=parent_id,
                    text=body.strip(),
                    filename=filename,
                    section=section,
                    children=children,
                )
            )
        return parents
