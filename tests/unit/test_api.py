"""Unit tests for the segments HTTP endpoint.

The agent branch is faked so no Gemini or BigQuery call is made; the catalog
branch reads a small fixture CSV written under ``tmp_path``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lr_bestsellers.api.app import create_app
from lr_bestsellers.api.dependencies import get_api_settings, get_catalog_repository
from lr_bestsellers.config import Settings
from lr_bestsellers.exceptions import (
    CatalogError,
    InputGuardrailError,
    OutputGuardrailError,
    RetrievalError,
)
from lr_bestsellers.models.query import QueryResponse, SourceCitation
from lr_bestsellers.store.csv_catalog import CsvCatalogRepository
from tests.unit.test_csv_catalog import make_row, write_csv

ENDPOINT = "/v1/segments"


def fake_answer(text: str, settings: Settings, caller_id: str) -> QueryResponse:
    """Return a canned agent response.

    Args:
        text: Question that was asked.
        settings: Injected settings (unused).
        caller_id: Rate-limit bucket key (unused).

    Returns:
        A deterministic ``QueryResponse``.
    """
    del settings, caller_id
    return QueryResponse(
        answer=f"Answer to {text} [Source: BigQuery]",
        sources=[SourceCitation(source="BigQuery", text="row", score=1.0)],
        sql_used="SELECT 1",
        confidence=0.9,
        intent="analytics",
    )


@pytest.fixture
def settings() -> Settings:
    """Return settings that never reach a real service.

    Returns:
        Placeholder ``Settings``.
    """
    return Settings(google_api_key="test-key", bigquery_project="test-project")


@pytest.fixture
def app(tmp_path: Path, settings: Settings) -> FastAPI:
    """Build an app wired to a 5-row fixture catalog.

    Args:
        tmp_path: pytest temporary directory.
        settings: Placeholder settings.

    Returns:
        Application with both dependencies overridden.
    """
    csv_path = write_csv(tmp_path / "segments.csv", [make_row(i) for i in range(5)])
    application = create_app()
    application.dependency_overrides[get_api_settings] = lambda: settings
    application.dependency_overrides[get_catalog_repository] = lambda: CsvCatalogRepository(
        csv_path
    )
    return application


@pytest.fixture
def client(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Return a test client with the agent pipeline faked out.

    Args:
        app: Application under test.
        monkeypatch: pytest monkeypatch fixture.

    Yields:
        A ``TestClient`` bound to ``app``.
    """
    monkeypatch.setattr("lr_bestsellers.api.routes.answer_query", fake_answer)
    with TestClient(app) as test_client:
        yield test_client


class TestBrowseBranch:
    """Requests without a ``query`` parameter page the CSV catalog."""

    def test_defaults_to_first_page(self, client: TestClient) -> None:
        """No parameters returns page 1 in catalog mode."""
        response = client.get(ENDPOINT)
        assert response.status_code == 200
        body = response.json()
        assert body["mode"] == "catalog"
        assert body["source"] == "segments.csv"
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["total_items"] == 5
        assert len(body["items"]) == 5

    def test_pagination(self, client: TestClient) -> None:
        """Page and page_size slice the catalog."""
        body = client.get(ENDPOINT, params={"page": 2, "page_size": 2}).json()
        assert [item["dms_segment_id"] for item in body["items"]] == [1002, 1003]
        assert body["pagination"]["total_pages"] == 3
        assert body["pagination"]["has_next"] is True
        assert body["pagination"]["has_previous"] is True

    def test_blank_query_browses(self, client: TestClient) -> None:
        """A blank query is treated as no query at all."""
        body = client.get(ENDPOINT, params={"query": "   "}).json()
        assert body["mode"] == "catalog"

    def test_row_shape(self, client: TestClient) -> None:
        """Catalog rows expose typed metrics and split platform lists."""
        item = client.get(ENDPOINT, params={"page_size": 1}).json()["items"][0]
        assert item["active_platform_names"] == ["Beeswax", "The Trade Desk", "Xandr"]
        assert item["impressions"] == 125000.5
        assert item["is_highly_used"] is False
        assert item["usage_start_date"] == "2026-07-26"

    def test_page_size_over_limit(self, client: TestClient) -> None:
        """page_size above the maximum is rejected by validation."""
        assert client.get(ENDPOINT, params={"page_size": 500}).status_code == 422

    def test_page_zero(self, client: TestClient) -> None:
        """Page must be 1-based."""
        assert client.get(ENDPOINT, params={"page": 0}).status_code == 422

    def test_catalog_unavailable(self, app: FastAPI, client: TestClient) -> None:
        """An unreadable dump surfaces as 503 with a stable error code."""
        app.dependency_overrides[get_catalog_repository] = lambda: CsvCatalogRepository(
            Path("does-not-exist.csv")
        )
        response = client.get(ENDPOINT)
        assert response.status_code == 503
        assert response.json()["error"] == "CATALOG_UNAVAILABLE"


class TestAskBranch:
    """Requests with a ``query`` parameter run the guarded agent pipeline."""

    def test_returns_agent_answer(self, client: TestClient) -> None:
        """A question returns agent mode with citations and SQL."""
        response = client.get(ENDPOINT, params={"query": "Top segments by impressions?"})
        assert response.status_code == 200
        body = response.json()
        assert body["mode"] == "agent"
        assert body["query"] == "Top segments by impressions?"
        assert body["result"]["intent"] == "analytics"
        assert body["result"]["sql_used"] == "SELECT 1"
        assert body["result"]["sources"][0]["source"] == "BigQuery"

    def test_query_is_trimmed(self, client: TestClient) -> None:
        """Surrounding whitespace is stripped before the guardrails run."""
        body = client.get(ENDPOINT, params={"query": "  What is popularity_score?  "}).json()
        assert body["query"] == "What is popularity_score?"

    def test_pagination_params_ignored(self, client: TestClient) -> None:
        """Pagination parameters do not change the agent branch."""
        response = client.get(ENDPOINT, params={"query": "anything", "page": 3})
        assert response.json()["mode"] == "agent"

    def test_query_too_long(self, client: TestClient) -> None:
        """A query beyond the 2000-character cap is rejected by validation."""
        assert client.get(ENDPOINT, params={"query": "x" * 2001}).status_code == 422

    def test_input_guardrail_rejection(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An input guardrail failure maps to 400 with the guardrail code."""

        def reject(text: str, settings: Settings, caller_id: str) -> QueryResponse:
            del text, settings, caller_id
            raise InputGuardrailError("Query contains PII", code="PII_DETECTED")

        monkeypatch.setattr("lr_bestsellers.api.routes.answer_query", reject)
        response = client.get(ENDPOINT, params={"query": "who is a@b.com"})
        assert response.status_code == 400
        assert response.json()["error"] == "PII_DETECTED"

    def test_output_guardrail_rejection(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An output guardrail failure maps to 502 with the guardrail code."""

        def reject(text: str, settings: Settings, caller_id: str) -> QueryResponse:
            del text, settings, caller_id
            raise OutputGuardrailError("Uncited claim", code="MISSING_CITATION")

        monkeypatch.setattr("lr_bestsellers.api.routes.answer_query", reject)
        response = client.get(ENDPOINT, params={"query": "top segments"})
        assert response.status_code == 502
        assert response.json()["error"] == "MISSING_CITATION"

    def test_domain_error(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """Any other domain failure maps to 500 with PIPELINE_ERROR."""

        def boom(text: str, settings: Settings, caller_id: str) -> QueryResponse:
            del text, settings, caller_id
            raise RetrievalError("Qdrant unreachable")

        monkeypatch.setattr("lr_bestsellers.api.routes.answer_query", boom)
        response = client.get(ENDPOINT, params={"query": "top segments"})
        assert response.status_code == 500
        assert response.json()["error"] == "PIPELINE_ERROR"

    def test_catalog_error_from_agent_branch(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CatalogError keeps its 503 mapping wherever it is raised."""

        def boom(text: str, settings: Settings, caller_id: str) -> QueryResponse:
            del text, settings, caller_id
            raise CatalogError("dump missing")

        monkeypatch.setattr("lr_bestsellers.api.routes.answer_query", boom)
        assert client.get(ENDPOINT, params={"query": "top segments"}).status_code == 503


class TestOpenApiSchema:
    """The generated OpenAPI document describes both response shapes."""

    def test_schema_is_served(self, client: TestClient) -> None:
        """/openapi.json exposes the endpoint and its query parameters."""
        schema = client.get("/openapi.json").json()
        operation = schema["paths"][ENDPOINT]["get"]
        params = {param["name"] for param in operation["parameters"]}
        assert {"query", "page", "page_size", "X-Caller-Id"} <= params

    def test_response_is_a_discriminated_union(self, client: TestClient) -> None:
        """The 200 response documents both AgentAnswer and CatalogPage."""
        schema = client.get("/openapi.json").json()
        content = schema["paths"][ENDPOINT]["get"]["responses"]["200"]["content"]
        payload = content["application/json"]["schema"]
        refs = {option["$ref"].rsplit("/", 1)[-1] for option in payload["oneOf"]}
        assert refs == {"AgentAnswer", "CatalogPage"}

    def test_error_responses_documented(self, client: TestClient) -> None:
        """Guardrail and catalog failures are part of the contract."""
        responses = client.get("/openapi.json").json()["paths"][ENDPOINT]["get"]["responses"]
        assert {"400", "500", "502", "503"} <= set(responses)

    def test_docs_available(self, client: TestClient) -> None:
        """Swagger UI is served for interactive exploration."""
        assert client.get("/docs").status_code == 200
