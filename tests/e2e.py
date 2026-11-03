import json
import os
import re
import socket
import time
from collections.abc import Generator
from contextlib import closing
from multiprocessing import Process
from unittest import mock

import pytest
import requests
import uvicorn
from playwright.sync_api import Page, Route, expect

from app.backend.config import Settings
from app.backend.main import app
from app.backend.naming import KNOWLEDGE_SOURCES, KnowledgeSourceSpec

expect.set_options(timeout=10_000)

QUALITY_OVERVIEW = {
    "container": "cocoarynth-quality",
    "documents": [
        {
            "name": "hold-notice-mg26-l02.pdf",
            "size": 31000,
            "lastModified": "2026-09-02T12:00:00+00:00",
            "url": "/api/sources/quality/documents/content/hold-notice-mg26-l02.pdf",
        },
        {
            "name": "tasting-panel-september-2026.pdf",
            "size": 36000,
            "lastModified": "2026-09-30T12:00:00+00:00",
            "url": "/api/sources/quality/documents/content/tasting-panel-september-2026.pdf",
        },
    ],
}

DOCUMENT_OVERVIEW = {
    "container": "knowledge",
    "documents": [
        {
            "name": "mapayca-markets-traceability-pilot-requirements.pdf",
            "size": 42000,
            "lastModified": "2026-09-16T12:00:00+00:00",
            "url": "/api/sources/documents/documents/content/mapayca-markets-traceability-pilot-requirements.pdf",
        },
        {
            "name": "origin-passport-support-runbook.pdf",
            "size": 38000,
            "lastModified": "2026-09-16T12:00:00+00:00",
            "url": "/api/sources/documents/documents/content/origin-passport-support-runbook.pdf",
        },
    ],
}

ORIGINS_OVERVIEW = {
    "container": "cocoarynth-origins",
    "documents": [
        {
            "name": "origin-profile-ghana.pdf",
            "size": 28000,
            "lastModified": "2026-08-01T12:00:00+00:00",
            "url": "/api/sources/origins/documents/content/origin-profile-ghana.pdf",
        }
    ],
}

QUALITY_CHUNKS = {
    "index": "cocoarynth-quality-index",
    "document": "hold-notice-mg26-l02.pdf",
    "chunks": [
        {
            "chunk_id": "quality-chunk-1",
            "title": "hold-notice-mg26-l02.pdf",
            "chunk": "Lot MG26-L02 held on arrival: moisture 8.6% at receipt.",
            "page_number_from": 1,
            "page_number_to": 1,
            "image_path": None,
        }
    ],
}

RETRIEVAL = {
    "response": [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        [{"ref_id": 0, "title": "Hold notice", "content": "Lot MG26-L02 was held on arrival."}]
                    ),
                }
            ],
        }
    ],
    "activity": [
        {
            "type": "modelQueryPlanning",
            "id": 0,
            "modelName": "gpt-5.5",
            "inputTokens": 640,
            "outputTokens": 32,
            "elapsedMs": 120,
        },
        {
            "type": "searchIndex",
            "id": 1,
            "knowledgeSourceName": "cocoarynth-logistics-retail-source",
            "count": 1,
            "elapsedMs": 14,
            "searchIndexArguments": {"search": "Madagascar limited edition launch date"},
        },
        {
            "type": "searchIndex",
            "id": 2,
            "knowledgeSourceName": "cocoarynth-quality-source",
            "count": 1,
            "elapsedMs": 11,
            "searchIndexArguments": {"search": "Madagascar lot MG26-L02 hold"},
        },
        {
            "type": "agenticReasoning",
            "id": 3,
            "reasoningTokens": 480,
            "retrievalReasoningEffort": {"kind": "low"},
        },
    ],
    "references": [
        {
            "type": "searchIndex",
            "id": "0",
            "activitySource": 1,
            "title": "launch-plan-madagascar-nibs.pdf",
            "rerankerScore": 3.9,
            "sourceData": {"chunk": "Launch moved from 1 September to 1 October."},
        },
        {
            "type": "searchIndex",
            "id": "1",
            "activitySource": 2,
            "title": "hold-notice-mg26-l02.pdf",
            "sourceData": {"chunk": "Lot MG26-L02 held on arrival: moisture 8.6% at receipt."},
        },
    ],
}

QUALITY_SEARCH_RESULTS = {
    "index": "cocoarynth-quality-index",
    "query": "bean moisture test results on arrival",
    "matches": [
        {
            **QUALITY_CHUNKS["chunks"][0],
            "@search.score": 0.91,
            "@search.reranker_score": 3.75,
        }
    ],
}

SOURCE_DOCUMENTS = {"quality": QUALITY_OVERVIEW, "documents": DOCUMENT_OVERVIEW, "origins": ORIGINS_OVERVIEW}
SOURCE_INDEXES = {source.key: source.index for source in KNOWLEDGE_SOURCES}


def knowledge_source_definition(source: KnowledgeSourceSpec) -> dict:
    return {
        "name": source.knowledge_source,
        "kind": "searchIndex",
        "description": f"{source.name}: {source.description}",
        "searchIndexParameters": {
            "searchIndexName": source.index,
            "semanticConfigurationName": "semantic-configuration",
            "searchFields": [{"name": "chunk"}],
            "sourceDataFields": [{"name": "title"}],
        },
    }


CONFIGURATION = {
    "knowledgeBase": {
        "name": "cocoarynth-kb-all",
        "description": "Cocoarynth chocolate company knowledge",
        "outputMode": "extractiveData",
        "rerankerThreshold": 1.7,
        "retrievalReasoningEffort": {"kind": "low"},
        "retrievalInstructions": "Use farms & harvests for origin, farm, fermentation, certification, and bean price records.",
        "models": [
            {
                "kind": "azureOpenAI",
                "azureOpenAIParameters": {
                    "deploymentId": "gpt-5.5",
                    "modelName": "gpt-5.5",
                },
            }
        ],
        "knowledgeSources": [{"name": source.knowledge_source} for source in KNOWLEDGE_SOURCES],
    },
    "knowledgeSources": [
knowledge_source_definition(source) for source in KNOWLEDGE_SOURCES
    ],
}

DIY_AGENTS = {
    "steps": [
        {
            "name": "diy-baseline",
            "title": "Baseline",
            "capability": "One hybrid search with semantic ranking over the quality index.",
            "port": 8091,
            "examples": [
                {"label": "Succeeds: tasting panel winner", "question": "Which origin won the tasting panel?"},
                {"label": "Fails: Madagascar panel and hold", "question": "What did the tasting panel think of the bar?"},
            ],
            "running": False,
        },
        {
            "name": "diy-reflection",
            "title": "Reflection",
            "capability": "An LLM checks the evidence and plans follow-up searches until it is sufficient.",
            "port": 8096,
            "examples": [
                {"label": "Needs reflection: Pantry Lane farms", "question": "A Pantry Lane customer asked which farms grew the cacao."},
                {"label": "One round: grey Ghana bars", "question": "Why did the Ghana bars turn grey?"},
            ],
            "running": True,
        },
    ]
}

DIY_ANSWER = {
    "step": "diy-reflection",
    "startedAtMs": 1_000_000,
    "elapsedMs": 41_500,
    "answer": (
        "The wrapper lot **EC26-0811** traces to bean lot EC26-L07. [batch-record-ec26-0811.pdf]\n\n"
        "- Finca Las Brisas\n- Finca El Cedro [boletas-acopio-rio-claro-2026.pdf; data-sharing-policy.pdf]"
    ),
    "agentUsage": {"inputTokens": 3300, "outputTokens": 200},
    "toolCalls": [
        {
            "name": "search_knowledge",
            "arguments": {"question": "Which farms grew the cacao?"},
            "activity": [
                {
                    "step": "query_planning",
                    "elapsed_ms": 2100,
                    "started_at_ms": 1_003_000,
                    "model": "gpt-5.5",
                    "reasoning_effort": "medium",
                    "input_tokens": 450,
                    "output_tokens": 60,
                    "searches": [
                        {
                            "query": "Pantry Lane Ecuador wrapper farms",
                            "indexes": ["cocoarynth-origins-index", "cocoarynth-logistics-retail-index"],
                        }
                    ],
                },
                {
                    "step": "parallel_search",
                    "elapsed_ms": 800,
                    "started_at_ms": 1_005_100,
                    "round": 1,
                    "searches": [
                        {"index": "cocoarynth-origins-index", "query": "Pantry Lane Ecuador wrapper farms", "result_count": 5},
                        {
                            "index": "cocoarynth-logistics-retail-index",
                            "query": "Pantry Lane Ecuador wrapper farms",
                            "result_count": 5,
                        },
                    ],
                },
                {
                    "step": "reflection",
                    "elapsed_ms": 5900,
                    "started_at_ms": 1_005_900,
                    "round": 1,
                    "input_tokens": 4700,
                    "output_tokens": 300,
                    "sufficient": False,
                    "missing": "The batch record linking EC26-0811 to its bean lot.",
                    "follow_up_searches": [
                        {"query": "Batch record EC26-0811 bean lots", "indexes": ["cocoarynth-quality-index"]}
                    ],
                },
            ],
            "evidence": [
                {
                    "id": "quality/1",
                    "index": "cocoarynth-quality-index",
                    "title": "batch-record-ec26-0811.pdf",
                    "content": "Batch EC26-0811 used 250 kg of bean lot EC26-L07.",
                    "reranker_score": 2.91,
                    "relevance": 3,
                }
            ],
        }
    ],
}


def wait_for_server_ready(url: str, timeout: float = 10.0, check_interval: float = 0.2) -> None:
    error = "Server did not become ready."
    for _ in range(int(timeout / check_interval)):
        try:
            response = requests.get(f"{url}health", timeout=1)
            response.raise_for_status()
        except requests.RequestException as exc:
            error = str(exc)
            time.sleep(check_interval)
        else:
            return
    raise RuntimeError(error)


@pytest.fixture(scope="session")
def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return server_socket.getsockname()[1]


def run_server(port: int, live_smoke: bool) -> None:
    if live_smoke:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")
        return

    with mock.patch.dict(
        os.environ,
        {
            "FOUNDRY_IQ_SEARCH_ENDPOINT": "https://example.search.windows.net",
            "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
            "AZURE_OPENAI_CHAT_DEPLOYMENT": "gpt-5.5",
            "AZURE_OPENAI_CHAT_MODEL": "gpt-5.5",
            "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": "text-embedding-3-large",
            "AZURE_OPENAI_EMBEDDING_MODEL": "text-embedding-3-large",
            "AZURE_STORAGE_ACCOUNT_NAME": "examplestorage",
        },
        clear=True,
    ):
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")


@pytest.fixture(scope="session")
def live_server_url(
    free_port: int, live_smoke: bool, smoke_base_url: str | None
) -> Generator[str, None, None]:
    if smoke_base_url:
        url = f"{smoke_base_url.rstrip('/')}/"
        wait_for_server_ready(url, timeout=60)
        yield url
        return

    if live_smoke:
        try:
            settings = Settings.from_environment()
        except KeyError as exc:
            pytest.exit(
                f"Live smoke tests require a provisioned azd environment; missing {exc.args[0]}.",
                returncode=1,
            )
        if "example." in settings.search_endpoint:
            pytest.exit("Live smoke tests cannot use a placeholder Search endpoint.", returncode=1)

    process = Process(target=run_server, args=(free_port, live_smoke), daemon=True)
    process.start()
    url = f"http://127.0.0.1:{free_port}/"
    try:
        wait_for_server_ready(url)
        yield url
    finally:
        process.kill()
        process.join(timeout=5)


def json_response(route: Route, body: dict, status: int = 200) -> None:
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def mock_app_apis(page: Page, live_smoke: bool, document_error: bool = False) -> None:
    if live_smoke:
        return

    def handle_documents(route: Route) -> None:
        key = route.request.url.split("/api/sources/")[1].split("/")[0]
        if document_error:
            json_response(route, {"detail": "Search inventory is unavailable."}, 503)
        else:
            json_response(route, SOURCE_DOCUMENTS.get(key, {"container": key, "documents": []}))

    def handle_chunks(route: Route) -> None:
        key = route.request.url.split("/api/sources/")[1].split("/")[0]
        json_response(route, {**QUALITY_CHUNKS, "index": SOURCE_INDEXES[key]})

    def handle_search(route: Route) -> None:
        key = route.request.url.split("/api/sources/")[1].split("/")[0]
        json_response(route, {**QUALITY_SEARCH_RESULTS, "index": SOURCE_INDEXES[key]})

    page.route(re.compile(r".*/api/sources/[^/]+/documents$"), handle_documents)
    page.route(re.compile(r".*/api/sources/[^/]+/chunks.*"), handle_chunks)
    page.route(re.compile(r".*/api/sources/[^/]+/search$"), handle_search)
    page.route(re.compile(r".*/api/knowledge-base$"), lambda route: json_response(route, CONFIGURATION))
    page.route(re.compile(r".*/api/diy/agents$"), lambda route: json_response(route, DIY_AGENTS))
    page.route(re.compile(r".*/api/diy/ask$"), lambda route: json_response(route, DIY_ANSWER))


@pytest.mark.parametrize("viewport", [(390, 844), (768, 1024), (1280, 800)])
def test_home_is_responsive(
    page: Page, live_server_url: str, live_smoke: bool, viewport: tuple[int, int]
) -> None:
    mock_app_apis(page, live_smoke)
    page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    page.goto(live_server_url)

    expect(page).to_have_title("Foundry IQ workshop portal")
    expect(page.get_by_role("heading", name="Workshop portal")).to_be_visible()
    expect(page.get_by_role("heading", name="Welcome")).to_be_visible()
    expect(page.locator("#home")).to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator(".nav-item.active")).to_have_count(0)
    expect(page.get_by_role("img", name="Cocoarynth")).to_be_visible()
    expect(page.get_by_role("img", name="Cocoarynth")).to_have_attribute(
        "src", re.compile(r"/assets/cocoarynth-mark\.png")
    )
    expect(page.locator("#prefix")).to_have_count(0)
    expect(page.locator("#create-combined")).to_have_count(0)
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def test_header_brand_links_to_home(page: Page, live_server_url: str, live_smoke: bool) -> None:
    mock_app_apis(page, live_smoke)
    page.goto(f"{live_server_url}knowledge-base/query")

    page.get_by_role("link", name="Workshop portal home").click()

    expect(page).to_have_url(re.compile(r"/$"))
    expect(page.locator("#home")).to_have_class(re.compile(r"\bactive\b"))


def test_navigation_has_sources_knowledge_base_and_diy_groups(
    page: Page, live_server_url: str, live_smoke: bool
) -> None:
    mock_app_apis(page, live_smoke)
    page.goto(live_server_url)

    expect(page.locator(".nav-label")).to_have_text(["Knowledge sources", "Knowledge base", "DIY agentic retrieval"])
    expect(page.locator(".nav-group").nth(0).locator(".nav-item")).to_have_text(
        ["Browse documents", "View document chunks", "Search the index"]
    )
    expect(page.locator(".nav-group-kb .nav-item")).to_have_text(["Configuration", "Query knowledge base"])
    expect(page.locator(".nav-group-kb")).to_have_css("border-top-style", "solid")
    expect(page.get_by_text("MCP")).to_have_count(0)
    expect(page.get_by_text("Azure AI Search")).to_have_count(0)


def test_loads_source_inventory(page: Page, live_server_url: str, live_smoke: bool) -> None:
    mock_app_apis(page, live_smoke)
    page.goto(f"{live_server_url}sources/quality/documents")

    select = page.locator("#documents-source")
    expect(select.locator("option")).to_have_text([source.name for source in KNOWLEDGE_SOURCES])
    expect(select).to_have_value("quality")
    expect(page.locator("#documents .source-description")).to_contain_text("tasting panels")
    if live_smoke:
        expect(page.locator("#document-list a").first).to_be_visible()
    else:
        expect(page.locator("#document-list")).to_contain_text("hold-notice-mg26-l02.pdf")
    expect(page.locator("#document-list a").first).to_have_attribute(
        "href", re.compile(r"/api/sources/quality/documents/content/")
    )


def test_defaults_to_first_source(page: Page, live_server_url: str, live_smoke: bool) -> None:
    mock_app_apis(page, live_smoke)
    page.goto(live_server_url)
    page.locator(".nav-group").nth(0).get_by_role("button", name="Browse documents", exact=True).click()

    expect(page).to_have_url(re.compile(r"/sources/origins/documents$"))
    expect(page.locator("#documents-source")).to_have_value("origins")
    if not live_smoke:
        expect(page.locator("#document-list")).to_contain_text("origin-profile-ghana.pdf")


def test_page_permalinks_and_browser_history(page: Page, live_server_url: str, live_smoke: bool) -> None:
    mock_app_apis(page, live_smoke)
    for path, panel_id in {
        "sources/origins/documents": "documents",
        "sources/quality/chunks": "chunks",
        "sources/engineering-practices/search": "search",
        "knowledge-base/configuration": "kb-configuration",
        "knowledge-base/query": "kb-query",
        "diy-agents": "diy-agents",
    }.items():
        page.goto(f"{live_server_url}{path}")
        expect(page.locator(f"#{panel_id}")).to_have_class(re.compile(r"\bactive\b"))
        expect(page).to_have_url(re.compile(rf"/{path}$"))

    for path in ("project-requirements", "combined/query", "sources/unknown/documents", "sources/quality/mcp"):
        assert requests.get(f"{live_server_url}{path}", timeout=10).status_code == 404

    page.goto(f"{live_server_url}sources/policies/search")
    expect(page.locator("#search-source")).to_have_value("policies")

    page.locator(".nav-group-kb").get_by_role("button", name="Query knowledge base", exact=True).click()
    expect(page.locator("#kb-query")).to_have_class(re.compile(r"\bactive\b"))
    expect(page).to_have_url(re.compile(r"/knowledge-base/query$"))

    page.go_back()
    expect(page.locator("#search")).to_have_class(re.compile(r"\bactive\b"))
    expect(page).to_have_url(re.compile(r"/sources/policies/search$"))


def test_selected_source_persists_across_source_pages(
    page: Page, live_server_url: str, live_smoke: bool
) -> None:
    mock_app_apis(page, live_smoke)
    page.goto(f"{live_server_url}sources/origins/documents")
    sources = page.locator(".nav-group").nth(0)

    page.locator("#documents-source").select_option(label="Origin Passport")
    expect(page).to_have_url(re.compile(r"/sources/documents/documents$"))
    if not live_smoke:
        expect(page.locator("#document-list")).to_contain_text("mapayca-markets-traceability-pilot-requirements.pdf")

    sources.get_by_role("button", name="View document chunks", exact=True).click()
    expect(page).to_have_url(re.compile(r"/sources/documents/chunks$"))
    expect(page.locator("#chunks-source")).to_have_value("documents")

    sources.get_by_role("button", name="Search the index", exact=True).click()
    expect(page).to_have_url(re.compile(r"/sources/documents/search$"))
    expect(page.locator("#search-source")).to_have_value("documents")
    expect(page.locator("#search-query")).to_have_value("CSV export requirements")

    page.locator("#search-source").select_option(label="Engineering")
    expect(page).to_have_url(re.compile(r"/sources/engineering-practices/search$"))
    expect(page.locator("#search-query")).to_have_value("testing review rollout and monitoring standards")
    expect(page.locator("#documents-source")).to_have_value("engineering-practices")


def test_loads_content_understanding_chunks(page: Page, live_server_url: str, live_smoke: bool) -> None:
    mock_app_apis(page, live_smoke)
    chunk_urls: list[str] = []
    page.on("request", lambda request: chunk_urls.append(request.url) if "/chunks?" in request.url else None)
    page.goto(f"{live_server_url}sources/quality/chunks")

    document_select = page.locator("#document-select")
    if live_smoke:
        expect(document_select.locator("option")).not_to_have_count(1)
        document_select.select_option(index=1)
        expect(page.locator("#chunk-list article").first).to_be_visible()
    else:
        document_select.select_option(label="hold-notice-mg26-l02.pdf")
        expect(page.locator("#chunk-list")).to_contain_text("Lot MG26-L02 held on arrival")
        assert any("/api/sources/quality/chunks?document=hold-notice-mg26-l02.pdf" in url for url in chunk_urls)
    expect(page.locator("#chunk-summary")).to_contain_text("cocoarynth-quality-index")
    expect(page.locator("#chunk-list")).to_contain_text("page_number_from")


def test_runs_hybrid_source_search(page: Page, live_server_url: str, live_smoke: bool) -> None:
    mock_app_apis(page, live_smoke)
    search_requests: list[tuple[str, dict]] = []
    page.on(
        "request",
        lambda request: search_requests.append((request.url, request.post_data_json))
        if "/api/sources/" in request.url and request.url.endswith("/search")
        else None,
    )
    page.goto(f"{live_server_url}sources/quality/search")

    expect(page.locator("#search-query")).to_have_value("bean moisture test results on arrival")
    page.locator("#search-form").get_by_role("button", name="Search", exact=True).click()

    if live_smoke:
        expect(page.locator("#search-results article").first).to_be_visible()
    else:
        expect(page.locator("#search-results")).to_contain_text("Lot MG26-L02 held on arrival")
        expect(page.locator("#search-results")).to_contain_text("Reranker 3.750")
        expect(page.locator("#search-results")).to_contain_text("Search 0.910")
    expect(page.locator("#search-summary")).to_contain_text("cocoarynth-quality-index · hybrid semantic + vector")
    assert search_requests[0][0].endswith("/api/sources/quality/search")
    assert search_requests[0][1] == {"query": "bean moisture test results on arrival", "limit": 10}


def test_query_knowledge_base_workflow(page: Page, live_server_url: str, live_smoke: bool) -> None:
    if live_smoke:
        pytest.skip("Model-judged workshop acceptance is covered by tests/live_smoke.py.")
    mock_app_apis(page, live_smoke)
    retrieval_payloads: list[dict] = []

    def handle_retrieval(route: Route) -> None:
        retrieval_payloads.append(route.request.post_data_json)
        json_response(route, RETRIEVAL)

    page.route(re.compile(r".*/api/retrieve$"), handle_retrieval)
    page.goto(live_server_url)
    page.locator(".nav-group-kb").get_by_role("button", name="Query knowledge base", exact=True).click()

    question = page.locator("#kb-query textarea")
    expect(question).to_have_value(re.compile(r"^Why did the Madagascar limited-edition bar with cacao nibs launch"))
    default_question = question.input_value()
    page.locator("#kb-query").get_by_role("button", name="Query knowledge base").click()

    output = page.locator("#kb-output")
    expect(output).to_contain_text("Lot MG26-L02 was held on arrival.")
    activity = output.locator(".activity-table")
    expect(activity).to_contain_text("cocoarynth-logistics-retail-source")
    expect(activity).to_contain_text("Madagascar limited edition launch date")
    expect(activity).to_contain_text("cocoarynth-quality-source")
    expect(activity).to_contain_text("Result re-ranking")
    references = output.locator(".reference-list")
    expect(references).to_contain_text("launch-plan-madagascar-nibs.pdf")
    expect(references).to_contain_text("hold-notice-mg26-l02.pdf")
    assert retrieval_payloads == [{"question": default_question}]


def test_displays_knowledge_base_configuration(page: Page, live_server_url: str, live_smoke: bool) -> None:
    mock_app_apis(page, live_smoke)
    page.goto(live_server_url)
    page.locator(".nav-group-kb").get_by_role("button", name="Configuration", exact=True).click()
    expect(page).to_have_url(re.compile(r"/knowledge-base/configuration$"))

    configuration = page.locator("#kb-configuration-details")
    expect(configuration).to_contain_text("cocoarynth-kb-all")
    expect(configuration.locator(".configuration-item")).to_have_count(1)
    expect(configuration).not_to_contain_text("Bearer")
    expect(configuration).not_to_contain_text("MCP")
    if not live_smoke:
        facts = configuration.locator(".config-facts").first
        expect(facts).to_contain_text("Reranker threshold1.7")
        expect(facts).to_contain_text("Reasoninglow")
        expect(facts).to_contain_text("Use farms & harvests")
        expect(facts).to_contain_text("gpt-5.5")
    expect(configuration.locator(".source-list > h3")).to_have_text("Knowledge sources · 6")
    rows = configuration.locator(".source-row")
    expect(rows.locator("h4")).to_have_text([source.name for source in KNOWLEDGE_SOURCES])
    for index, source in enumerate(KNOWLEDGE_SOURCES):
        facts = rows.nth(index).locator(".source-facts")
        expect(facts).to_contain_text(source.knowledge_source)
        expect(facts).to_contain_text("KindsearchIndex")
        expect(facts).to_contain_text(source.index)
        expect(facts).to_contain_text("semantic-configuration")


def test_retrieves_from_knowledge_base(page: Page, live_server_url: str, live_smoke: bool) -> None:
    payloads: list[dict] = []
    mock_app_apis(page, live_smoke)

    def handle_retrieval(route: Route) -> None:
        payloads.append(route.request.post_data_json)
        json_response(route, RETRIEVAL)

    if live_smoke:
        page.set_default_timeout(180_000)
        page.on(
            "request",
            lambda request: payloads.append(request.post_data_json)
            if request.url.endswith("/api/retrieve")
            else None,
        )
    else:
        page.route(re.compile(r".*/api/retrieve$"), handle_retrieval)
    page.goto(f"{live_server_url}knowledge-base/query")
    page.locator("#kb-query").get_by_role("button", name="Query knowledge base", exact=True).click()
    if live_smoke:
        expect(page.locator("#kb-output article").first).to_be_visible()
    else:
        expect(page.locator("#kb-output")).to_contain_text("Lot MG26-L02 was held on arrival.")
        expect(page.locator("#kb-output .reference-list")).to_contain_text("Launch moved from 1 September to 1 October.")

    assert len(payloads) == 1
    assert set(payloads[0]) == {"question"}


def test_inventory_error_is_visible(page: Page, live_server_url: str, live_smoke: bool) -> None:
    if live_smoke:
        pytest.skip("The intentional mocked failure is not part of live smoke testing.")
    mock_app_apis(page, live_smoke, document_error=True)
    page.goto(live_server_url)

    expect(page.locator("#toast")).to_have_text("Search inventory is unavailable.")
    expect(page.locator("#toast")).to_have_class(re.compile(r"\bshow\b"))


def test_diy_agents_page_shows_answer_and_activity_log(
    page: Page, live_server_url: str, live_smoke: bool
) -> None:
    if live_smoke:
        pytest.skip("The DIY agents run locally and are not part of live smoke testing.")
    mock_app_apis(page, live_smoke)
    payloads: list[dict] = []

    def handle_ask(route: Route) -> None:
        payloads.append(route.request.post_data_json)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(DIY_ANSWER))

    page.route(re.compile(r".*/api/diy/ask$"), handle_ask)
    page.goto(live_server_url)
    page.locator(".nav-group-diy").get_by_role("button", name="Ask a DIY agent", exact=True).click()
    expect(page).to_have_url(re.compile(r"/diy-agents$"))

    baseline = page.locator('.diy-step[data-step="diy-baseline"]')
    expect(baseline).to_have_attribute("aria-pressed", "true")
    expect(baseline).to_contain_text("Not running")
    expect(page.locator("#diy-hint")).to_be_visible()
    expect(page.locator("#diy-hint")).to_contain_text("python -m scripts.run_diy_agents")
    expect(page.locator("#diy-question")).to_have_value("Which origin won the tasting panel?")
    example = page.locator("#diy-example")
    expect(example.locator("option")).to_have_text(["Succeeds: tasting panel winner", "Fails: Madagascar panel and hold"])
    example.select_option(label="Fails: Madagascar panel and hold")
    expect(page.locator("#diy-question")).to_have_value("What did the tasting panel think of the bar?")

    page.locator('.diy-step[data-step="diy-reflection"]').click()
    expect(page.locator("#diy-hint")).to_be_hidden()
    expect(page.locator("#diy-capability")).to_contain_text("follow-up searches")
    question = page.locator("#diy-question")
    expect(question).to_have_value(re.compile(r"Pantry Lane"))

    expect(example.locator("option")).to_have_text(["Needs reflection: Pantry Lane farms", "One round: grey Ghana bars"])
    example.select_option(label="One round: grey Ghana bars")
    expect(question).to_have_value("Why did the Ghana bars turn grey?")
    example.select_option(label="Needs reflection: Pantry Lane farms")
    expect(question).to_have_value(re.compile(r"Pantry Lane"))

    question.fill("Which farms grew the cacao?")
    page.locator('.diy-step[data-step="diy-baseline"]').click()
    expect(page.locator("#diy-question")).to_have_value("Which farms grew the cacao?")
    page.locator('.diy-step[data-step="diy-reflection"]').click()
    example.select_option(label="Needs reflection: Pantry Lane farms")

    page.get_by_role("button", name="Ask agent").click()

    output = page.locator("#diy-output")
    expect(output.locator(".diy-answer strong")).to_have_text("EC26-0811")
    expect(output.locator(".diy-answer li")).to_have_count(2)
    citations = output.locator(".diy-answer a.diy-citation")
    expect(citations).to_have_count(3)
    expect(citations.nth(0)).to_have_text("batch-record-ec26-0811.pdf")
    expect(citations.nth(0)).to_have_attribute("href", "/api/diy/documents/batch-record-ec26-0811.pdf")
    expect(citations.nth(0)).to_have_attribute("target", "_blank")
    expect(citations.nth(2)).to_have_text("data-sharing-policy.pdf")
    expect(output.locator(".diy-answer li").nth(1)).to_contain_text(
        "Finca El Cedro [boletas-acopio-rio-claro-2026.pdf; data-sharing-policy.pdf]"
    )
    expect(output.locator(".diy-metric").filter(has_text="Total time")).to_contain_text("41.5 s")
    expect(output.locator(".diy-metric").filter(has_text="Searches")).to_contain_text("2")
    expect(output.locator(".diy-metric").filter(has_text="Model calls in retrieval")).to_contain_text("2")
    expect(output.locator(".diy-metric").filter(has_text="Retrieval tokens")).to_contain_text("5,510")
    expect(output.locator(".diy-metric").filter(has_text="Agent tokens")).to_contain_text("3,500")

    rows = output.locator(".activity-row")
    expect(rows).to_have_count(4)
    expect(rows.nth(0)).to_contain_text("Agent calls tool")
    expect(rows.nth(0)).to_contain_text("search_knowledge")
    expect(rows.nth(1)).to_contain_text("Query planning")
    expect(rows.nth(1)).to_contain_text("cocoarynth-origins-index, cocoarynth-logistics-retail-index")
    expect(rows.nth(2).locator(".diy-source")).to_have_text(
        ["cocoarynth-origins-index", "cocoarynth-logistics-retail-index"]
    )
    expect(rows.nth(3).locator(".diy-source")).to_have_text("cocoarynth-quality-index")
    expect(rows.nth(1)).to_contain_text("Model:gpt-5.5")
    expect(rows.nth(1)).to_contain_text("Reasoning effort:medium")
    expect(rows.nth(2)).not_to_contain_text("Reasoning effort")
    expect(output.get_by_text("View step data")).to_have_count(0)
    expect(rows.nth(2)).to_contain_text("Parallel search · round 1")
    expect(rows.nth(2).locator(".diy-search-row")).to_have_count(2)
    expect(rows.nth(3)).to_contain_text("Reflection · round 1")
    expect(rows.nth(3)).to_contain_text("Sufficient:No")
    expect(rows.nth(3)).to_contain_text("Batch record EC26-0811 bean lots")
    expect(rows.nth(3).locator(".token-meter")).to_contain_text("5,000")
    expect(rows.nth(3).locator(".activity-elapsed")).to_have_text("5,900 ms")

    waterfall = output.locator(".waterfall")
    expect(waterfall).to_contain_text("41.5 s total")
    waterfall_rows = waterfall.locator(".waterfall-row")
    expect(waterfall_rows.locator(".waterfall-label")).to_have_text(
        [
            "Agent model: decides to search",
            "Query planning",
            "Parallel search · round 1",
            "Reflection · round 1",
            "Agent model: writes answer",
        ]
    )
    expect(waterfall_rows.locator(".waterfall-duration")).to_have_text(["3.0 s", "2.1 s", "800 ms", "5.9 s", "29.7 s"])
    expect(waterfall_rows.nth(0)).to_have_class(re.compile(r"waterfall-agent"))
    expect(waterfall_rows.nth(1)).to_have_class(re.compile(r"waterfall-llm"))
    expect(waterfall_rows.nth(2)).to_have_class(re.compile(r"waterfall-search"))
    bar_lefts = waterfall_rows.locator(".waterfall-bar").evaluate_all("bars => bars.map(bar => parseFloat(bar.style.left))")
    assert bar_lefts == sorted(bar_lefts) and bar_lefts[0] == 0
    assert abs(bar_lefts[1] - 3000 / 41500 * 100) < 0.01
    expect(waterfall.locator(".waterfall-key")).to_have_text(["Search", "Model call in retrieval", "Agent model"])
    assert waterfall.evaluate("node => node.compareDocumentPosition(document.querySelector('#diy-output .activity-table')) & Node.DOCUMENT_POSITION_FOLLOWING")

    evidence = output.locator(".reference-item")
    expect(evidence).to_have_count(1)
    expect(evidence).to_contain_text("batch-record-ec26-0811.pdf")
    expect(evidence.locator(".resource-kind")).to_have_text("cocoarynth-quality-index")
    expect(evidence).to_contain_text("Relevance 3/3")
    expect(evidence).to_contain_text("Reranker 2.91")
    assert payloads == [{"step": "diy-reflection", "question": DIY_AGENTS["steps"][1]["examples"][0]["question"]}]
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def test_diy_agent_error_is_visible(page: Page, live_server_url: str, live_smoke: bool) -> None:
    if live_smoke:
        pytest.skip("The intentional mocked failure is not part of live smoke testing.")
    mock_app_apis(page, live_smoke)
    detail = "diy-baseline is not running on port 8091. Start the DIY agents locally with: python -m scripts.run_diy_agents"
    page.route(
        re.compile(r".*/api/diy/ask$"),
        lambda route: route.fulfill(status=503, content_type="application/json", body=json.dumps({"detail": detail})),
    )
    page.goto(f"{live_server_url}diy-agents")

    page.get_by_role("button", name="Ask agent").click()

    expect(page.locator("#diy-output")).to_contain_text("diy-baseline is not running on port 8091")


def test_diy_single_index_steps_show_the_index(page: Page, live_server_url: str, live_smoke: bool) -> None:
    if live_smoke:
        pytest.skip("The DIY agents run locally and are not part of live smoke testing.")
    mock_app_apis(page, live_smoke)
    answer = {
        **DIY_ANSWER,
        "toolCalls": [
            {
                "name": "search_knowledge",
                "arguments": {"query": "Madagascar tasters"},
                "activity": [
                    {
                        "step": "search",
                        "elapsed_ms": 820,
                        "index": "cocoarynth-quality-index",
                        "query": "Madagascar tasters",
                        "result_count": 5,
                    },
                    {
                        "step": "parallel_search",
                        "elapsed_ms": 840,
                        "searches": [
                            {"index": "cocoarynth-quality-index", "query": "Madagascar tasting panel", "result_count": 5},
                            {"index": "cocoarynth-quality-index", "query": "Madagascar lot hold", "result_count": 5},
                        ],
                    },
                    {
                        "step": "complexity_routing",
                        "elapsed_ms": 470,
                        "model": "decision-1",
                        "input_tokens": 300,
                        "output_tokens": 1,
                        "route": "agentic",
                        "agentic_probability": 0.97,
                    },
                    {
                        "step": "reranking",
                        "elapsed_ms": 900,
                        "model": "decision-1",
                        "input_tokens": 12000,
                        "output_tokens": 20,
                        "candidate_count": 10,
                        "kept_count": 4,
                    },
                ],
                "evidence": [
                    {
                        "id": "quality/1",
                        "index": "cocoarynth-quality-index",
                        "title": "hold-notice-mg26-l02.pdf",
                        "content": "Moisture 8.6% at receipt.",
                        "relevance": 2.4137,
                    }
                ],
            }
        ],
    }
    page.route(
        re.compile(r".*/api/diy/ask$"),
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(answer)),
    )
    page.goto(f"{live_server_url}diy-agents")

    page.get_by_role("button", name="Ask agent").click()

    rows = page.locator("#diy-output .activity-row")
    expect(rows.nth(1)).to_contain_text("Index:cocoarynth-quality-index")
    waterfall_rows = page.locator("#diy-output .waterfall-row")
    expect(waterfall_rows.locator(".waterfall-label")).to_have_text(["Search", "Parallel search", "Complexity routing", "Reranking"])
    expect(waterfall_rows.locator(".waterfall-agent")).to_have_count(0)
    search_rows = rows.nth(2).locator(".diy-search-row")
    expect(search_rows).to_have_count(2)
    expect(search_rows.locator(".diy-source")).to_have_text(["cocoarynth-quality-index", "cocoarynth-quality-index"])

    expect(rows.nth(3)).to_contain_text("Complexity routing")
    expect(rows.nth(3)).to_contain_text("Model:decision-1")
    expect(rows.nth(3)).to_contain_text("Agentic probability:0.97")
    expect(rows.nth(3)).not_to_contain_text("Reasoning effort")
    expect(rows.nth(4)).to_contain_text("Reranking")
    expect(rows.nth(4)).to_contain_text("Kept:4 of 10 results")
    expect(page.locator("#diy-output .reference-item")).to_contain_text("Relevance 2.4/3")
