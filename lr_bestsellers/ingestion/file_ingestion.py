"""File-based ingestion of ``knowledge_base/*.md`` into ``domain_knowledge``."""

from __future__ import annotations

from pathlib import Path

import structlog

from lr_bestsellers.exceptions import IngestionError
from lr_bestsellers.ingestion.protocols import RawDocument
from lr_bestsellers.store.protocols import COLLECTION_DOMAIN_KNOWLEDGE
from lr_bestsellers.utils.chunking import ParentChildChunker, count_tokens

log = structlog.get_logger(__name__)

_SKIP_FILES = frozenset({"glossary.md"})


class FileIngestionSource:
    """Read markdown files, parent-child chunk them, emit raw documents.

    Args:
        knowledge_base_dir: Directory containing ``*.md`` files.
        chunker: Optional chunker (defaults to ``ParentChildChunker``).
        only_file: If set, ingest only this filename or path's name.
    """

    def __init__(
        self,
        knowledge_base_dir: Path,
        chunker: ParentChildChunker | None = None,
        only_file: str | None = None,
    ) -> None:
        """Store paths and chunker.

        Args:
            knowledge_base_dir: ``knowledge_base/`` directory.
            chunker: Markdown chunker.
            only_file: Optional single filename filter.
        """
        self._dir = knowledge_base_dir
        self._chunker = chunker or ParentChildChunker()
        self._only_file = Path(only_file).name if only_file else None

    @property
    def name(self) -> str:
        """Return the source name ``files``."""
        return "files"

    @property
    def collection(self) -> str:
        """Return ``domain_knowledge``."""
        return str(COLLECTION_DOMAIN_KNOWLEDGE)

    def load(self) -> list[RawDocument]:
        """Load and chunk markdown files (excluding ``glossary.md``).

        Returns:
            Child chunks as raw documents.

        Raises:
            IngestionError: When the directory is missing or a file cannot be read.
        """
        if not self._dir.is_dir():
            raise IngestionError(f"knowledge_base directory not found: {self._dir}")
        documents: list[RawDocument] = []
        for path in sorted(self._dir.glob("*.md")):
            if path.name in _SKIP_FILES:
                continue
            if self._only_file is not None and path.name != self._only_file:
                continue
            try:
                markdown = path.read_text(encoding="utf-8")
            except OSError as exc:
                log.error("ingestion.file_read_failed", path=str(path), error=str(exc))
                raise IngestionError(f"Failed to read {path}") from exc
            parents = self._chunker.chunk_markdown(markdown, path.name)
            for parent in parents:
                for child in parent.children:
                    documents.append(
                        RawDocument(
                            point_id=child.chunk_id,
                            text=child.text,
                            collection=self.collection,
                            parent_text=parent.text,
                            filename=child.filename,
                            section=child.section,
                            subsection=child.subsection,
                            parent_id=child.parent_id,
                            token_count=child.token_count or count_tokens(child.text),
                        )
                    )
            log.info("ingestion.file_chunked", file=path.name, parents=len(parents))
        return documents
