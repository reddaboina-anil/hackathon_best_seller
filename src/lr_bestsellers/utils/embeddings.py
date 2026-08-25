"""Embedding client protocol and a deterministic fake for tests."""

from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable

from lr_bestsellers.store.protocols import EMBEDDING_DIM


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
    """``text-embedding-004`` client wrapping LangChain Google embeddings.

    Args:
        api_key: Gemini / Google AI Studio API key.
    """

    def __init__(self, api_key: str) -> None:
        """Create the LangChain embeddings client.

        Args:
            api_key: API key string.
        """
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        self._client = GoogleGenerativeAIEmbeddings(
            model="models/text-embedding-004",
            google_api_key=api_key,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed documents via the Google API.

        Args:
            texts: Strings to embed.

        Returns:
            Dense vectors.

        Raises:
            EmbeddingError: When the API call fails.
        """
        from lr_bestsellers.exceptions import EmbeddingError

        if not texts:
            return []
        try:
            return self._client.embed_documents(texts)
        except Exception as exc:
            raise EmbeddingError("text-embedding-004 embed_documents failed") from exc

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
            raise EmbeddingError("text-embedding-004 embed_query failed") from exc
