"""Parse ``knowledge_base/glossary.md`` into the ``glossary`` collection."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import structlog

from lr_bestsellers.exceptions import IngestionError
from lr_bestsellers.ingestion.protocols import RawDocument
from lr_bestsellers.store.protocols import COLLECTION_GLOSSARY
from lr_bestsellers.utils.chunking import count_tokens, header_prefix

log = structlog.get_logger(__name__)

_H2_RE: Final[re.Pattern[str]] = re.compile(r"^## +(.+)$", re.MULTILINE)


class GlossaryIngestionSource:
    """One Qdrant document per glossary H2 term.

    Args:
        glossary_path: Path to ``glossary.md``.
    """

    def __init__(self, glossary_path: Path) -> None:
        """Store the glossary file path.

        Args:
            glossary_path: Markdown glossary file.
        """
        self._path = glossary_path

    @property
    def name(self) -> str:
        """Return the source name ``glossary``."""
        return "glossary"

    @property
    def collection(self) -> str:
        """Return ``glossary``."""
        return str(COLLECTION_GLOSSARY)

    def load(self) -> list[RawDocument]:
        """Parse H2 terms into raw documents.

        Returns:
            One document per glossary term.

        Raises:
            IngestionError: When the file is missing or unreadable.
        """
        try:
            markdown = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            log.error("ingestion.glossary_read_failed", path=str(self._path), error=str(exc))
            raise IngestionError(f"Failed to read glossary at {self._path}") from exc

        matches = list(_H2_RE.finditer(markdown))
        documents: list[RawDocument] = []
        filename = self._path.name
        for i, match in enumerate(matches):
            term = match.group(1).strip()
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
            definition = markdown[start:end].strip()
            if not definition:
                continue
            prefix = header_prefix(filename, term, None)
            text = f"{prefix}\n{term}: {definition}"
            slug = re.sub(r"[^a-z0-9]+", "_", term.lower()).strip("_")
            documents.append(
                RawDocument(
                    point_id=f"glossary_{slug}",
                    text=text,
                    collection=self.collection,
                    parent_text=definition,
                    filename=filename,
                    section=term,
                    parent_id=f"glossary_{slug}",
                    token_count=count_tokens(text),
                )
            )
        log.info("ingestion.glossary_loaded", terms=len(documents))
        return documents
