import asyncio
import json
import unittest
from unittest.mock import Mock

from azure.core.exceptions import ServiceRequestError
from fastapi.testclient import TestClient

from app.backend.main import app, azure_service_error


class RouteContractTests(unittest.TestCase):
    def test_routes_are_read_only_and_source_parameterized(self) -> None:
        routes = {
            (route.path, method)
            for route in app.routes
            for method in getattr(route, "methods", set())
        }

        self.assertIn(("/api/sources", "GET"), routes)
        self.assertIn(("/api/sources/{key}/documents", "GET"), routes)
        self.assertIn(("/api/sources/{key}/documents/content/{blob_name:path}", "GET"), routes)
        self.assertIn(("/api/sources/{key}/chunks", "GET"), routes)
        self.assertIn(("/api/sources/{key}/search", "POST"), routes)
        self.assertIn(("/api/knowledge-base", "GET"), routes)
        self.assertIn(("/api/retrieve", "POST"), routes)
        self.assertNotIn(("/api/documents", "GET"), routes)
        self.assertNotIn(("/api/engineering-practices", "GET"), routes)
        self.assertNotIn(("/api/knowledge-bases", "GET"), routes)
        self.assertFalse(any("mcp" in path for path, _ in routes))
        self.assertFalse(any(method == "DELETE" for _, method in routes))

    def test_azure_service_failures_return_actionable_503(self) -> None:
        response = asyncio.run(
            azure_service_error(None, ServiceRequestError("Search endpoint could not be resolved."))  # type: ignore[arg-type]
        )

        self.assertEqual(503, response.status_code)
        detail = json.loads(response.body)["detail"]
        self.assertIn("Verify FOUNDRY_IQ_SEARCH_ENDPOINT", detail)
        self.assertNotIn("Azure Search", detail)


class SourceRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.service = Mock()
        app.state.service = self.service

    def test_lists_six_sources(self) -> None:
        response = self.client.get("/api/sources")

        self.assertEqual(response.status_code, 200)
        sources = response.json()["sources"]
        self.assertEqual(len(sources), 6)
        self.assertEqual(
            sources[0],
            {
                "key": "origins",
                "name": "Farms & harvests",
                "description": sources[0]["description"],
                "index": "cocoarynth-origins-index",
                "knowledgeSource": "cocoarynth-origins-source",
            },
        )
        self.assertIn("partly in Spanish", sources[0]["description"])

    def test_unknown_source_returns_404(self) -> None:
        for method, path in (
            ("GET", "/api/sources/unknown/documents"),
            ("GET", "/api/sources/unknown/documents/content/a.pdf"),
            ("GET", "/api/sources/unknown/chunks?document=a.pdf"),
            ("POST", "/api/sources/unknown/search"),
            ("GET", "/sources/unknown/documents"),
        ):
            with self.subTest(path=path):
                response = self.client.request(method, path, json={"query": "moisture"})
                self.assertEqual(response.status_code, 404)
        self.assertEqual(self.service.method_calls, [])

    def test_source_routes_use_the_source_container_and_index(self) -> None:
        self.service.list_documents.return_value = {"documents": []}
        self.service.list_chunks.return_value = {"chunks": []}
        self.service.search_documents.return_value = {"matches": []}
        self.service.download_document.return_value = (b"%PDF", "application/pdf")

        self.client.get("/api/sources/quality/documents")
        self.client.get("/api/sources/documents/chunks?document=a.pdf&limit=1000")
        self.client.post("/api/sources/engineering-practices/search", json={"query": "testing"})
        response = self.client.get("/api/sources/quality/documents/content/hold.pdf")

        self.service.list_documents.assert_called_once_with(
            "cocoarynth-quality", "/api/sources/quality/documents/content"
        )
        self.service.list_chunks.assert_called_once_with("cocoarynth-documents-index", "a.pdf", 500)
        self.service.search_documents.assert_called_once_with(
            "cocoarynth-engineering-practices-index", "testing", 10
        )
        self.service.download_document.assert_called_once_with("hold.pdf", "cocoarynth-quality")
        self.assertEqual(response.headers["content-type"], "application/pdf")

    def test_retrieve_takes_only_a_question(self) -> None:
        self.service.retrieve.return_value = {"activity": []}

        response = self.client.post("/api/retrieve", json={"question": "Why was the launch late?"})

        self.assertEqual(response.status_code, 200)
        self.service.retrieve.assert_called_once_with("Why was the launch late?")


if __name__ == "__main__":
    unittest.main()
