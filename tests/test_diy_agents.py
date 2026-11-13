import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx2
from azure.core.exceptions import ResourceNotFoundError
from fastapi import HTTPException

from app.diy_agents import (
    DIY_STEPS,
    DIY_STEPS_BY_NAME,
    DiyAgentFailedError,
    DiyAgentUnavailableError,
    ask,
    summarize_response,
)
from app.main import (
    DiyAskRequest,
    app,
    diy_agent_error,
    diy_agent_unavailable,
    diy_ask,
    diy_document,
    find_cited_document,
)

AGENT_RESPONSE = {
    "output": [
        {"type": "reasoning", "summary": []},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "search_knowledge",
            "arguments": json.dumps({"question": "Which origin won?"}),
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": json.dumps(
                {
                    "evidence": [{"id": "quality/1", "title": "sensory-panel-report-2026-09.pdf", "content": "Madagascar"}],
                    "activity": [{"step": "query_planning", "elapsed_ms": 1200, "input_tokens": 130}],
                }
            ),
        },
        {"type": "message", "content": [{"type": "output_text", "text": "Madagascar won."}]},
    ],
    "usage": {"input_tokens": 3300, "output_tokens": 110},
}


class SummarizeResponseTests(unittest.TestCase):
    def test_pairs_tool_calls_with_their_activity_and_evidence(self) -> None:
        summary = summarize_response(AGENT_RESPONSE)

        self.assertEqual("Madagascar won.", summary["answer"])
        self.assertEqual({"inputTokens": 3300, "outputTokens": 110}, summary["agentUsage"])
        [call] = summary["toolCalls"]
        self.assertEqual("search_knowledge", call["name"])
        self.assertEqual({"question": "Which origin won?"}, call["arguments"])
        self.assertEqual("query_planning", call["activity"][0]["step"])
        self.assertEqual("sensory-panel-report-2026-09.pdf", call["evidence"][0]["title"])

    def test_reads_the_first_copy_of_repeated_arguments(self) -> None:
        arguments = '{"question": "Which origin won?"}{"question": "Which origin won?"}'
        response = {
            "output": [
                {"type": "function_call", "call_id": "c", "name": "search_knowledge", "arguments": arguments},
            ]
        }

        [call] = summarize_response(response)["toolCalls"]

        self.assertEqual({"question": "Which origin won?"}, call["arguments"])

    def test_keeps_tool_errors_that_are_not_json(self) -> None:
        response = {
            "output": [
                {"type": "function_call", "call_id": "c", "name": "search_knowledge", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "c", "output": "Error: function failed."},
            ]
        }

        [call] = summarize_response(response)["toolCalls"]

        self.assertEqual("Error: function failed.", call["error"])
        self.assertEqual([], call["activity"])

    def test_every_step_has_labeled_examples(self) -> None:
        for step in DIY_STEPS:
            with self.subTest(step=step.name):
                self.assertGreaterEqual(len(step.examples), 1)
                labels = [example.label for example in step.examples]
                self.assertEqual(len(labels), len(set(labels)))
                self.assertTrue(all(len(label) <= 45 for label in labels))

    def test_every_step_has_its_own_port(self) -> None:
        ports = [step.port for step in DIY_STEPS]

        self.assertEqual(7, len(DIY_STEPS))
        self.assertEqual(len(ports), len(set(ports)))


class AskTests(unittest.TestCase):
    def test_connection_failure_reports_the_step_as_unavailable(self) -> None:
        def refuse(request: httpx2.Request) -> httpx2.Response:
            raise httpx2.ConnectError("refused", request=request)

        original = httpx2.AsyncClient

        def client_with_transport(*args, **kwargs):
            return original(*args, transport=httpx2.MockTransport(refuse), **kwargs)

        with (
            patch("app.diy_agents.httpx2.AsyncClient", side_effect=client_with_transport),
            self.assertRaises(DiyAgentUnavailableError),
        ):
            asyncio.run(ask(DIY_STEPS[0], "Which origin won?"))

    def test_posts_question_to_the_step_port(self) -> None:
        requests: list[httpx2.Request] = []

        def respond(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            return httpx2.Response(200, json=AGENT_RESPONSE)

        original = httpx2.AsyncClient

        def client_with_transport(*args, **kwargs):
            return original(*args, transport=httpx2.MockTransport(respond), **kwargs)

        step = DIY_STEPS_BY_NAME["diy-reflection"]
        with patch("app.diy_agents.httpx2.AsyncClient", side_effect=client_with_transport):
            result = asyncio.run(ask(step, "Which farms grew the cacao?"))

        self.assertEqual(f"http://127.0.0.1:{step.port}/responses", str(requests[0].url))
        self.assertEqual({"input": "Which farms grew the cacao?"}, json.loads(requests[0].content))
        self.assertEqual("diy-reflection", result["step"])
        self.assertIsInstance(result["startedAtMs"], int)
        self.assertGreaterEqual(result["elapsedMs"], 0)
        self.assertEqual("Madagascar won.", result["answer"])

    def test_failed_response_raises_with_the_agent_error(self) -> None:
        failed = {"status": "failed", "error": {"code": "server_error", "message": "Error code: 401"}, "output": []}
        original = httpx2.AsyncClient

        def client_with_transport(*args, **kwargs):
            return original(*args, transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=failed)), **kwargs)

        with (
            patch("app.diy_agents.httpx2.AsyncClient", side_effect=client_with_transport),
            self.assertRaisesRegex(DiyAgentFailedError, "diy-baseline failed: Error code: 401"),
        ):
            asyncio.run(ask(DIY_STEPS[0], "Which origin won?"))


class DiyRouteTests(unittest.TestCase):
    def test_routes_are_registered(self) -> None:
        routes = {(route.path, method) for route in app.routes for method in getattr(route, "methods", set())}

        self.assertIn(("/api/diy/agents", "GET"), routes)
        self.assertIn(("/api/diy/ask", "POST"), routes)
        self.assertIn(("/diy-agents", "GET"), routes)
        self.assertIn(("/demos", "GET"), routes)

    def test_unknown_step_returns_404(self) -> None:
        with self.assertRaises(HTTPException) as context:
            asyncio.run(diy_ask(DiyAskRequest(step="diy-unknown", question="Which origin won?")))

        self.assertEqual(404, context.exception.status_code)

    @patch("app.main.ask_diy_agent", new_callable=AsyncMock)
    def test_ask_route_calls_the_selected_step(self, ask_agent: AsyncMock) -> None:
        ask_agent.return_value = {"answer": "ok"}

        result = asyncio.run(diy_ask(DiyAskRequest(step="diy-baseline", question="Which origin won?")))

        self.assertEqual({"answer": "ok"}, result)
        ask_agent.assert_awaited_once_with(DIY_STEPS_BY_NAME["diy-baseline"], "Which origin won?")

    def test_unavailable_agent_explains_how_to_start_it(self) -> None:
        response = asyncio.run(
            diy_agent_unavailable(None, DiyAgentUnavailableError(DIY_STEPS[0]))  # type: ignore[arg-type]
        )

        self.assertEqual(503, response.status_code)
        self.assertIn("python -m scripts.run_diy_agents", json.loads(response.body)["detail"])

    def test_agent_timeout_returns_502(self) -> None:
        response = asyncio.run(diy_agent_error(None, httpx2.ReadTimeout("slow")))  # type: ignore[arg-type]

        self.assertEqual(502, response.status_code)
        self.assertIn("too long", json.loads(response.body)["detail"])


class CitedDocumentTests(unittest.TestCase):
    def test_missing_document_returns_404(self) -> None:
        service = Mock()
        service.download_document.side_effect = ResourceNotFoundError("missing")

        with self.assertRaises(HTTPException) as context:
            find_cited_document(service, "missing.pdf")

        self.assertEqual(404, context.exception.status_code)
        service.download_document.assert_called_once_with("missing.pdf", "cocoarynth")

    def test_rejects_paths_and_non_pdf_names(self) -> None:
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(service=Mock())))
        for name in ("notes.txt", "../secret.pdf", "folder/file.pdf"):
            with self.subTest(name=name), self.assertRaises(HTTPException) as context:
                asyncio.run(diy_document(name, request))  # type: ignore[arg-type]
            self.assertEqual(404, context.exception.status_code)
        request.app.state.service.download_document.assert_not_called()

    def test_serves_the_pdf_inline(self) -> None:
        service = Mock()
        service.download_document.return_value = (b"%PDF", "application/pdf")
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(service=service)))

        response = asyncio.run(diy_document("data-sharing-policy.pdf", request))  # type: ignore[arg-type]

        self.assertEqual("application/pdf", response.media_type)
        self.assertEqual('inline; filename="data-sharing-policy.pdf"', response.headers["content-disposition"])


if __name__ == "__main__":
    unittest.main()


class OpenDemoTabsTests(unittest.TestCase):
    def test_demo_urls_follow_slide_order_and_run(self) -> None:
        from urllib.parse import parse_qs, urlsplit

        from app.diy_agents import DEMOS
        from scripts.open_demo_tabs import demo_urls

        urls = demo_urls("http://127.0.0.1:8000/", [], run=True)

        expected = [(demo.step, demo.question) for demo in DEMOS]
        self.assertEqual("Foundry IQ knowledge base", DEMOS[7].slide)
        self.assertEqual("foundry-iq-mr1-kb", DEMOS[7].step)
        self.assertEqual("foundry-iq-auto", DEMOS[-1].step)
        parsed = [parse_qs(urlsplit(url).query) for url in urls]
        self.assertEqual(expected, [(query["agent"][0], query["question"][0]) for query in parsed])
        self.assertTrue(all(url.startswith("http://127.0.0.1:8000/diy-agents?") for url in urls))
        self.assertTrue(all(query["run"] == ["1"] for query in parsed))

    def test_demo_urls_filter_steps_and_skip_run(self) -> None:
        from scripts.open_demo_tabs import demo_urls

        urls = demo_urls("http://localhost:8000", ["diy-baseline"], run=False)

        self.assertEqual(2, len(urls))
        self.assertTrue(all("agent=diy-baseline" in url and "run=" not in url for url in urls))
