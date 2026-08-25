"""Deterministic sparse (BM25-style bag-of-words) encoder for hybrid search."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Final

from pydantic import BaseModel, Field

_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+")
_SPARSE_MODULUS: Final[int] = 2_097_151
"""Prime modulus for token → index hashing (fits Qdrant sparse indices)."""


class SparseVector(BaseModel):
    """A sparse bag-of-words vector.

    Attributes:
        indices: Hashed token indices (unique, ascending).
        values: TF-IDF-style weights aligned with ``indices``.
    """

    indices: list[int] = Field(default_factory=list)
    values: list[float] = Field(default_factory=list)


def tokenize(text: str) -> list[str]:
    """Split ``text`` into lowercase alphanumeric tokens.

    Args:
        text: Raw document or query text.

    Returns:
        Token list (may be empty).
    """
    return _TOKEN_RE.findall(text.lower())


def _token_index(token: str) -> int:
    """Map a token to a stable non-negative sparse index.

    Args:
        token: Lowercase token.

    Returns:
        Index in ``[0, _SPARSE_MODULUS)``.
    """
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % _SPARSE_MODULUS


def text_to_sparse(text: str) -> SparseVector:
    """Encode ``text`` as a log-TF sparse vector.

    Args:
        text: Document or query string.

    Returns:
        Sparse vector with unique sorted indices. Empty text yields empty vectors.
    """
    tokens = tokenize(text)
    if not tokens:
        return SparseVector()

    tf: dict[int, int] = {}
    for token in tokens:
        idx = _token_index(token)
        tf[idx] = tf.get(idx, 0) + 1

    indices = sorted(tf)
    values = [1.0 + math.log(tf[i]) for i in indices]
    return SparseVector(indices=indices, values=values)


def sparse_dot(left: SparseVector, right: SparseVector) -> float:
    """Compute the dot product of two sparse vectors.

    Args:
        left: First vector.
        right: Second vector.

    Returns:
        Sum of ``value_i * value_j`` over shared indices.
    """
    right_map = dict(zip(right.indices, right.values, strict=True))
    score = 0.0
    for idx, val in zip(left.indices, left.values, strict=True):
        other = right_map.get(idx)
        if other is not None:
            score += val * other
    return score
