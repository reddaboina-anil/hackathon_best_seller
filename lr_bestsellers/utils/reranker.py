"""Lexical reranker used as a stand-in for a cross-encoder.

Production deployments may swap this class for a true cross-encoder; the
public method signatures stay stable. Scoring combines the existing retrieval
score with query-document token overlap so unit tests need no GPU model.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from lr_bestsellers.models.chunk import SearchResult
from lr_bestsellers.store.sparse import tokenize


class RerankRequest(BaseModel):
    """Input to :meth:`CrossEncoderReranker.rerank`.

    Attributes:
        query: Original user query.
        results: Candidate hits (typically top-10 hybrid results).
        top_k: Number of hits to keep.
    """

    query: str = Field(..., min_length=1)
    results: list[SearchResult]
    top_k: int = Field(3, ge=1, le=50)


class CrossEncoderReranker:
    """Rerank ``SearchResult`` lists with overlap-aware scoring."""

    def rerank(self, request: RerankRequest) -> list[SearchResult]:
        """Return the top-k results sorted by combined score.

        Args:
            request: Query, candidates, and ``top_k``.

        Returns:
            New ``SearchResult`` instances (original objects are not mutated)
            with scores clipped to ``[0, 1]``.
        """
        query_tokens = set(tokenize(request.query))
        scored: list[tuple[float, SearchResult]] = []
        for result in request.results:
            doc_tokens = set(tokenize(result.chunk.text))
            if query_tokens:
                overlap = len(query_tokens & doc_tokens) / len(query_tokens)
            else:
                overlap = 0.0
            combined = min(1.0, 0.5 * result.score + 0.5 * overlap)
            scored.append((combined, result))
        scored.sort(key=lambda item: item[0], reverse=True)
        out: list[SearchResult] = []
        for combined, result in scored[: request.top_k]:
            out.append(result.model_copy(update={"score": combined}))
        return out
