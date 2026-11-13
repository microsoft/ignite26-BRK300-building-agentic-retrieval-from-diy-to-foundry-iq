import asyncio
import json
import unittest
from unittest.mock import Mock

from azure.core.exceptions import ServiceRequestError
from fastapi.testclient import TestClient

from app.main import app, azure_service_error


class RouteContractTests(unittest.TestCase):
    def test_routes_are_read_only(self) -> None:
        routes = {
            (route.path, method)
            for route in app.routes
            for method in getattr(route, "methods", set())
        }

        self.assertIn(("/api/source", "GET"), routes)
        self.assertIn(("/api/source/documents", "GET"), routes)
        self.assertIn(("/api/source/documents/content/{blob_name:path}", "GET"), routes)
        self.assertIn(("/api/source/chunks", "GET"), routes)
        self.assertIn(("/api/source/search", "POST"), routes)
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

    def test_describes_the_knowledge_source(self) -> None:
        response = self.client.get("/api/source")

        self.assertEqual(response.status_code, 200)
        source = response.json()
        self.assertEqual(source["name"], "Cocoarynth records")
        self.assertEqual(source["index"], "cocoarynth-all-index")
        self.assertEqual(source["knowledgeSource"], "cocoarynth-all-source")

    def test_unknown_pages_and_non_pdf_documents_return_404(self) -> None:
        for path in ("/source/unknown", "/api/source/documents/content/notes.txt", "/api/diy/documents/notes.txt"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.service.method_calls, [])

    def test_source_routes_use_the_source_container_and_index(self) -> None:
        self.service.list_documents.return_value = {"documents": []}
        self.service.list_chunks.return_value = {"chunks": []}
        self.service.search_documents.return_value = {"matches": []}
        self.service.download_document.return_value = (b"%PDF", "application/pdf")

        self.assertEqual(self.client.get("/source/search").status_code, 200)
        self.client.get("/api/source/documents")
        self.client.get("/api/source/chunks?document=a.pdf&limit=1000")
        self.client.post("/api/source/search", json={"query": "moisture"})
        response = self.client.get("/api/source/documents/content/hold.pdf")

        self.service.list_documents.assert_called_once_with("cocoarynth", "/api/source/documents/content")
        self.service.list_chunks.assert_called_once_with("cocoarynth-all-index", "a.pdf", 500)
        self.service.search_documents.assert_called_once_with("cocoarynth-all-index", "moisture", 10)
        self.service.download_document.assert_called_once_with("hold.pdf", "cocoarynth")
        self.assertEqual(response.headers["content-type"], "application/pdf")

    def test_retrieve_takes_only_a_question(self) -> None:
        self.service.retrieve.return_value = {"activity": []}

        response = self.client.post("/api/retrieve", json={"question": "Why was the launch late?"})

        self.assertEqual(response.status_code, 200)
        self.service.retrieve.assert_called_once_with("Why was the launch late?")


if __name__ == "__main__":
    unittest.main()
