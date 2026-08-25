"""Unit tests for Pydantic models in lr_bestsellers.models.

All tests are pure in-memory validations — no I/O, no external services.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lr_bestsellers.models.chunk import ChildChunk, ParentChunk, SearchResult
from lr_bestsellers.models.query import QueryRequest, QueryResponse, SourceCitation
from lr_bestsellers.models.segment import SegmentDocument

# ---------------------------------------------------------------------------
# SourceCitation
# ---------------------------------------------------------------------------


class TestSourceCitation:
    """Tests for SourceCitation validation."""

    def test_valid(self) -> None:
        """SourceCitation accepts valid inputs."""
        citation = SourceCitation(source="activation.md", text="Some text.", score=0.87)
        assert citation.source == "activation.md"
        assert citation.score == 0.87

    def test_score_lower_bound(self) -> None:
        """Score of 0.0 is accepted."""
        citation = SourceCitation(source="bq", text="row", score=0.0)
        assert citation.score == 0.0

    def test_score_upper_bound(self) -> None:
        """Score of 1.0 is accepted (SQL results always get 1.0)."""
        citation = SourceCitation(source="BigQuery", text="result", score=1.0)
        assert citation.score == 1.0

    def test_score_out_of_range(self) -> None:
        """Score > 1.0 raises ValidationError."""
        with pytest.raises(ValidationError):
            SourceCitation(source="x", text="y", score=1.1)

    def test_score_negative(self) -> None:
        """Negative score raises ValidationError."""
        with pytest.raises(ValidationError):
            SourceCitation(source="x", text="y", score=-0.1)


# ---------------------------------------------------------------------------
# QueryRequest
# ---------------------------------------------------------------------------


class TestQueryRequest:
    """Tests for QueryRequest validation."""

    def test_minimal_valid(self) -> None:
        """Minimal QueryRequest with only text field."""
        req = QueryRequest(text="What is activation?")
        assert req.text == "What is activation?"
        assert req.max_results == 10
        assert req.similarity_threshold == 0.65
        assert req.caller_id == "default"

    def test_custom_fields(self) -> None:
        """QueryRequest with all fields set explicitly."""
        req = QueryRequest(
            text="Top segments by reach",
            max_results=5,
            similarity_threshold=0.70,
            caller_id="test-caller",
        )
        assert req.max_results == 5
        assert req.similarity_threshold == 0.70
        assert req.caller_id == "test-caller"

    def test_text_too_short(self) -> None:
        """Empty text raises ValidationError (min_length=1)."""
        with pytest.raises(ValidationError):
            QueryRequest(text="")

    def test_text_too_long(self) -> None:
        """Text exceeding 2000 characters raises ValidationError."""
        with pytest.raises(ValidationError):
            QueryRequest(text="x" * 2001)

    def test_max_results_zero(self) -> None:
        """max_results=0 raises ValidationError (ge=1)."""
        with pytest.raises(ValidationError):
            QueryRequest(text="hello", max_results=0)

    def test_max_results_over_limit(self) -> None:
        """max_results=101 raises ValidationError (le=100)."""
        with pytest.raises(ValidationError):
            QueryRequest(text="hello", max_results=101)

    def test_threshold_boundary_values(self) -> None:
        """Threshold of exactly 0.0 and 1.0 are valid."""
        req_low = QueryRequest(text="q", similarity_threshold=0.0)
        req_high = QueryRequest(text="q", similarity_threshold=1.0)
        assert req_low.similarity_threshold == 0.0
        assert req_high.similarity_threshold == 1.0

    def test_threshold_invalid(self) -> None:
        """Threshold > 1.0 raises ValidationError."""
        with pytest.raises(ValidationError):
            QueryRequest(text="q", similarity_threshold=1.5)


# ---------------------------------------------------------------------------
# QueryResponse
# ---------------------------------------------------------------------------


class TestQueryResponse:
    """Tests for QueryResponse validation."""

    def test_valid_minimal(self) -> None:
        """Minimal valid QueryResponse."""
        resp = QueryResponse(
            answer="The top segment is X.",
            confidence=0.9,
            intent="analytics",
        )
        assert resp.sources == []
        assert resp.sql_used is None

    def test_valid_with_sources_and_sql(self) -> None:
        """QueryResponse with sources and SQL populated."""
        citation = SourceCitation(source="BigQuery", text="row data", score=1.0)
        resp = QueryResponse(
            answer="Result: Y [Source: BigQuery].",
            sources=[citation],
            sql_used="SELECT * FROM segments LIMIT 10",
            confidence=0.95,
            intent="analytics",
        )
        assert len(resp.sources) == 1
        assert resp.sql_used is not None

    def test_invalid_intent(self) -> None:
        """Unknown intent value raises ValidationError."""
        with pytest.raises(ValidationError):
            QueryResponse(
                answer="x",
                confidence=0.5,
                intent="unknown_intent",  # type: ignore[arg-type]
            )

    def test_all_valid_intents(self) -> None:
        """All Literal intent values are accepted."""
        for intent in ("analytics", "conceptual", "lookup", "mixed", "vague"):
            resp = QueryResponse(answer="x", confidence=0.5, intent=intent)  # type: ignore[arg-type]
            assert resp.intent == intent

    def test_confidence_out_of_range(self) -> None:
        """Confidence > 1.0 raises ValidationError."""
        with pytest.raises(ValidationError):
            QueryResponse(answer="x", confidence=1.1, intent="conceptual")


# ---------------------------------------------------------------------------
# SegmentDocument
# ---------------------------------------------------------------------------


class TestSegmentDocument:
    """Tests for SegmentDocument validation and helpers."""

    def _make(self, **kwargs: str) -> SegmentDocument:
        defaults = {
            "dms_segment_id": "99001",
            "seller_customer_id": "seller_xyz",
            "name": "Auto Intenders",
            "description": "Users showing intent to buy a vehicle.",
        }
        defaults.update(kwargs)
        return SegmentDocument(**defaults)  # type: ignore[arg-type]

    def test_valid(self) -> None:
        """SegmentDocument created with required fields."""
        doc = self._make()
        assert doc.segment_type == "syndicated"

    def test_to_embedding_text(self) -> None:
        """to_embedding_text() produces expected multi-line string."""
        doc = self._make(
            dms_segment_id="1",
            name="Auto Intenders",
            description="Intent to buy.",
        )
        expected = "Segment: Auto Intenders\nID: 1\nDescription: Intent to buy.\nType: Syndicated"
        assert doc.to_embedding_text() == expected

    def test_segment_type_is_always_syndicated(self) -> None:
        """segment_type field defaults to 'syndicated' and cannot be changed to an invalid value."""
        doc = self._make()
        assert doc.segment_type == "syndicated"


# ---------------------------------------------------------------------------
# ChildChunk
# ---------------------------------------------------------------------------


class TestChildChunk:
    """Tests for ChildChunk validation."""

    def _make(self, **kwargs: object) -> ChildChunk:
        defaults: dict[str, object] = {
            "chunk_id": "act_0_0",
            "parent_id": "act_0",
            "text": "[Doc: activation.md | Section: Overview] Activation is...",
            "filename": "activation.md",
            "section": "Overview",
            "token_count": 280,
        }
        defaults.update(kwargs)
        return ChildChunk(**defaults)  # type: ignore[arg-type]

    def test_valid_no_embedding(self) -> None:
        """ChildChunk valid without embedding (pre-ingest state)."""
        chunk = self._make()
        assert chunk.embedding is None
        assert chunk.subsection is None

    def test_valid_with_embedding(self) -> None:
        """ChildChunk valid with a 768-dim embedding vector."""
        embedding = [0.1] * 768
        chunk = self._make(embedding=embedding)
        assert len(chunk.embedding) == 768  # type: ignore[arg-type]

    def test_token_count_zero_invalid(self) -> None:
        """token_count=0 raises ValidationError (ge=1)."""
        with pytest.raises(ValidationError):
            self._make(token_count=0)


# ---------------------------------------------------------------------------
# ParentChunk
# ---------------------------------------------------------------------------


class TestParentChunk:
    """Tests for ParentChunk validation."""

    def test_valid_no_children(self) -> None:
        """ParentChunk initialised with empty children list."""
        parent = ParentChunk(
            parent_id="act_0",
            text="Full section text...",
            filename="activation.md",
            section="Overview",
        )
        assert parent.children == []

    def test_with_children(self) -> None:
        """ParentChunk with pre-populated children."""
        child = ChildChunk(
            chunk_id="act_0_0",
            parent_id="act_0",
            text="chunk text",
            filename="activation.md",
            section="Overview",
            token_count=100,
        )
        parent = ParentChunk(
            parent_id="act_0",
            text="Full section...",
            filename="activation.md",
            section="Overview",
            children=[child],
        )
        assert len(parent.children) == 1


# ---------------------------------------------------------------------------
# SearchResult
# ---------------------------------------------------------------------------


class TestSearchResult:
    """Tests for SearchResult validation."""

    def _make_chunk(self) -> ChildChunk:
        return ChildChunk(
            chunk_id="c1",
            parent_id="p1",
            text="some text",
            filename="glossary.md",
            section="Terms",
            token_count=50,
        )

    def test_valid_no_parent_text(self) -> None:
        """SearchResult valid without parent_text."""
        result = SearchResult(
            chunk=self._make_chunk(),
            score=0.82,
            collection="domain_knowledge",
        )
        assert result.parent_text is None

    def test_valid_with_parent_text(self) -> None:
        """SearchResult with parent_text populated."""
        result = SearchResult(
            chunk=self._make_chunk(),
            score=0.90,
            collection="glossary",
            parent_text="Parent section context...",
        )
        assert result.parent_text == "Parent section context..."

    def test_score_out_of_range(self) -> None:
        """Score > 1.0 raises ValidationError."""
        with pytest.raises(ValidationError):
            SearchResult(
                chunk=self._make_chunk(),
                score=1.5,
                collection="glossary",
            )
