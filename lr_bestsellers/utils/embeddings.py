"""Embedding client protocol and a deterministic fake for tests."""

from __future__ import annotations

import hashlib
import time
from typing import Final, Protocol, runtime_checkable

import structlog

from lr_bestsellers.config import DEFAULT_EMBEDDING_MODEL
from lr_bestsellers.store.protocols import EMBEDDING_DIM

log = structlog.get_logger(__name__)

EMBED_BATCH_SIZE: Final[int] = 1000
"""Texts sent in one Gemini embedContent call (100 often resets the socket)."""

_EMBED_MAX_ATTEMPTS: Final[int] = 5
_EMBED_RETRY_BASE_SECONDS: Final[float] = 2.0


@runtime_checkable
class EmbedderProtocol(Protocol):
    """Produces dense vectors for documents and queries."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed many documents.

        Args:
            texts: Strings to embed.

        Returns:
            One 768-dim vector per input string.
        """
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string.

        Args:
            text: Query text.

        Returns:
            A 768-dim vector.
        """
        ...


class HashEmbedder:
    """Deterministic embedder used in unit tests (no API calls)."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed documents via SHA-256 projected into 768 floats.

        Args:
            texts: Strings to embed.

        Returns:
            Dense vectors.
        """
        return [_hash_vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        """Embed a query with the same hash projection as documents.

        Args:
            text: Query text.

        Returns:
            Dense vector.
        """
        return _hash_vector(text)


def _is_transient_embed_error(exc: BaseException) -> bool:
    """Return True when the embed failure is worth retrying.

    Args:
        exc: Raised API or transport error (and its ``__cause__`` chain).

    Returns:
        Whether the error looks like a dropped connection or timeout.
    """
    current: BaseException | None = exc
    needles = (
        "reset by peer",
        "connection reset",
        "connection aborted",
        "broken pipe",
        "temporarily unavailable",
        "timeout",
        "timed out",
        "503",
        "429",
    )
    while current is not None:
        if isinstance(current, ConnectionResetError | TimeoutError | ConnectionError):
            return True
        lowered = str(current).lower()
        if any(needle in lowered for needle in needles):
            return True
        current = current.__cause__
    return False


def _hash_vector(text: str) -> list[float]:
    """Map text to a stable unit-ish vector.

    Args:
        text: Input string.

    Returns:
        List of length ``EMBEDDING_DIM``.
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values: list[float] = []
    while len(values) < EMBEDDING_DIM:
        for byte in digest:
            values.append((byte / 255.0) * 2.0 - 1.0)
            if len(values) == EMBEDDING_DIM:
                break
        digest = hashlib.sha256(digest).digest()
    return values


class GoogleEmbedder:
    """Gemini embedding client wrapping LangChain Google embeddings.

    Uses the configured model with ``output_dimensionality=768`` so vectors
    stay compatible with existing Qdrant collections.

    Args:
        api_key: Gemini / Google AI Studio API key.
        model: Gemini embedding model id.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_EMBEDDING_MODEL,
    ) -> None:
        """Create the LangChain embeddings client.

        Args:
            api_key: API key string.
            model: Embedding model id (from ``Settings.embedding_model``).
        """
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        self._model = model
        self._client = GoogleGenerativeAIEmbeddings(
            model=model,
            google_api_key=api_key,
            output_dimensionality=EMBEDDING_DIM,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed documents via the Google API in small retried batches.

        Args:
            texts: Strings to embed.

        Returns:
            Dense vectors.

        Raises:
            EmbeddingError: When the API call fails after retries.
        """
        from lr_bestsellers.exceptions import EmbeddingError

        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            chunk = texts[start : start + EMBED_BATCH_SIZE]
            try:
                vectors.extend(self._embed_chunk(chunk))
            except Exception as exc:
                raise EmbeddingError(f"{self._model} embed_documents failed") from exc
        return vectors

    def _embed_chunk(self, texts: list[str]) -> list[list[float]]:
        """Embed one batch, retrying transient connection resets.

        Args:
            texts: A batch no larger than ``EMBED_BATCH_SIZE``.

        Returns:
            Dense vectors for ``texts``.

        Raises:
            Exception: Last failure after retries.
        """
        last_error: Exception | None = None
        for attempt in range(1, _EMBED_MAX_ATTEMPTS + 1):
            try:
                return self._client.embed_documents(texts, batch_size=len(texts))
            except Exception as exc:
                last_error = exc
                if not _is_transient_embed_error(exc) or attempt == _EMBED_MAX_ATTEMPTS:
                    raise
                delay = _EMBED_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
                log.warning(
                    "embed.retry",
                    model=self._model,
                    attempt=attempt,
                    delay_seconds=delay,
                    error=str(exc),
                )
                time.sleep(delay)
        assert last_error is not None
        raise last_error

    def embed_query(self, text: str) -> list[float]:
        """Embed a query via the Google API.

        Args:
            text: Query text.

        Returns:
            Dense vector.

        Raises:
            EmbeddingError: When the API call fails.
        """
        from lr_bestsellers.exceptions import EmbeddingError

        try:
            return self._client.embed_query(text)
        except Exception as exc:
            raise EmbeddingError(f"{self._model} embed_query failed") from exc
