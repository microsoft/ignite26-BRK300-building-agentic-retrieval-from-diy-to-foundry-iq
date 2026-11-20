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

from app.config import Settings
from app.main import app
from app.naming import KNOWLEDGE_SOURCE

expect.set_options(timeout=10_000)

SOURCE_DOCUMENTS = {
    "container": "cocoarynth",
    "documents": [
        {
            "name": "hold-notice-mg26-l02.pdf",
            "size": 31000,
            "lastModified": "2026-09-02T12:00:00+00:00",
            "url": "/api/source/documents/content/hold-notice-mg26-l02.pdf",
        },
        {
            "name": "origin-profile-ghana.pdf",
            "size": 28000,
            "lastModified": "2026-08-01T12:00:00+00:00",
            "url": "/api/source/documents/content/origin-profile-ghana.pdf",
        },
        {
            "name": "tasting-panel-september-2026.pdf",
            "size": 36000,
            "lastModified": "2026-09-30T12:00:00+00:00",
            "url": "/api/source/documents/content/tasting-panel-september-2026.pdf",
        },
    ],
}

QUALITY_CHUNKS = {
    "index": "cocoarynth-all-index",
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
            "knowledgeSourceName": "cocoarynth-all-source",
            "count": 1,
            "elapsedMs": 14,
            "searchIndexArguments": {"search": "Madagascar limited edition launch date"},
        },
        {
            "type": "searchIndex",
            "id": 2,
            "knowledgeSourceName": "cocoarynth-all-source",
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
    "index": "cocoarynth-all-index",
    "query": "bean moisture limit on receipt",
    "matches": [
        {
            **QUALITY_CHUNKS["chunks"][0],
            "@search.score": 0.91,
            "@search.reranker_score": 3.75,
        }
    ],
}


CONFIGURATION = {
    "knowledgeBase": {
        "name": "cocoarynth-kb-all",
        "description": "Cocoarynth chocolate company knowledge",
        "outputMode": "extractiveData",
        "rerankerThreshold": 2.0,
        "retrievalReasoningEffort": {"kind": "auto"},
        "models": [
            {
                "kind": "azureOpenAI",
                "azureOpenAIParameters": {
                    "deploymentId": "gpt-5.5",
                    "modelName": "gpt-5.5",
                },
            }
        ],
        "knowledgeSources": [{"name": KNOWLEDGE_SOURCE.knowledge_source}],
    },
    "knowledgeSources": [
        {
            "name": KNOWLEDGE_SOURCE.knowledge_source,
            "kind": "searchIndex",
            "description": f"{KNOWLEDGE_SOURCE.name}: {KNOWLEDGE_SOURCE.description}",
            "searchIndexParameters": {
                "searchIndexName": KNOWLEDGE_SOURCE.index,
                "semanticConfigurationName": "semantic-configuration",
                "searchFields": [{"name": "chunk"}],
                "sourceDataFields": [{"name": "title"}],
            },
        }
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
    ],
    "demos": [
        {
            "slide": "Reflection strategy",
            "step": "diy-reflection",
            "label": "Needs reflection: Pantry Lane farms",
            "question": "A Pantry Lane customer asked which farms grew the cacao.",
        },
        {
            "slide": "Baseline strategy",
            "step": "diy-baseline",
            "label": "Succeeds: tasting panel winner",
            "question": "Which origin won the tasting panel?",
        },
    ],
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
                {"step": "semantic_ranking", "ranking_tokens": 50421, "reasoning_effort": "minimal"},
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
        if document_error:
            json_response(route, {"detail": "Search inventory is unavailable."}, 503)
        else:
            json_response(route, SOURCE_DOCUMENTS)

    page.route(re.compile(r".*/api/source/documents$"), handle_documents)
    page.route(re.compile(r".*/api/source/chunks.*"), lambda route: json_response(route, QUALITY_CHUNKS))
    page.route(re.compile(r".*/api/source/search$"), lambda route: json_response(route, QUALITY_SEARCH_RESULTS))
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

    expect(page).to_have_title("Agentic retrieval explorer")
    expect(page.get_by_role("heading", name="Welcome")).to_be_visible()
    expect(page.locator("#home")).to_have_class(re.compile(r"\bactive\b"))
    brand = page.get_by_role("link", name="Cocoarynth home")
    expect(brand).to_contain_text("Cocoarynth")
    expect(brand.locator("img")).to_be_visible()
    expect(brand.locator("img")).to_have_attribute("src", re.compile(r"/assets/cocoarynth-emblem\.png"))
    expect(page.locator("aside")).to_be_hidden()
    expect(page.get_by_text("Foundry IQ connected")).to_have_count(0)
    expect(page.locator("#prefix")).to_have_count(0)
    expect(page.locator("#create-combined")).to_have_count(0)
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def test_header_brand_links_to_home(page: Page, live_server_url: str, live_smoke: bool) -> None:
    mock_app_apis(page, live_smoke)
    page.goto(f"{live_server_url}knowledge-base/query")

    page.get_by_role("link", name="Cocoarynth home").click()

    expect(page).to_have_url(re.compile(r"/$"))
    expect(page.locator("#home")).to_have_class(re.compile(r"\bactive\b"))


def test_home_links_to_source_knowledge_base_and_diy_pages(
    page: Page, live_server_url: str, live_smoke: bool
) -> None:
    mock_app_apis(page, live_smoke)
    page.goto(live_server_url)

    links = page.locator(".welcome-options h3 a")
    expect(links).to_have_text(["Knowledge source", "Knowledge base", "DIY agentic retrieval", "Demos"])
    links.filter(has_text="Knowledge base").click()
    expect(page.locator("#kb-configuration")).to_have_class(re.compile(r"\bactive\b"))
    page.go_back()
    links.filter(has_text="DIY agentic retrieval").click()
    expect(page.locator("#diy-agents")).to_have_class(re.compile(r"\bactive\b"))
    page.go_back()
    expect(page.get_by_text("MCP")).to_have_count(0)
    expect(page.get_by_text("Azure AI Search")).to_have_count(0)


def test_loads_source_inventory(page: Page, live_server_url: str, live_smoke: bool) -> None:
    mock_app_apis(page, live_smoke)
    page.goto(live_server_url)
    page.locator(".welcome-options").get_by_role("link", name="Knowledge source").click()

    expect(page).to_have_url(re.compile(r"/source/documents$"))
    expect(page.locator("#documents .source-name")).to_have_text(KNOWLEDGE_SOURCE.name)
    expect(page.locator("#documents .source-description")).to_have_text(KNOWLEDGE_SOURCE.description)
    expect(page.locator("#search .source-name")).to_have_text(KNOWLEDGE_SOURCE.name)
    if live_smoke:
        expect(page.locator("#document-list a").first).to_be_visible()
    else:
        expect(page.locator("#document-list")).to_contain_text("hold-notice-mg26-l02.pdf")
        expect(page.locator("#document-list")).to_contain_text("origin-profile-ghana.pdf")
    expect(page.locator("#document-list a").first).to_have_attribute(
        "href", re.compile(r"/api/source/documents/content/")
    )


def test_page_permalinks_and_browser_history(page: Page, live_server_url: str, live_smoke: bool) -> None:
    mock_app_apis(page, live_smoke)
    for path, panel_id in {
        "source/documents": "documents",
        "source/chunks": "chunks",
        "source/search": "search",
        "knowledge-base/configuration": "kb-configuration",
        "knowledge-base/query": "kb-query",
        "diy-agents": "diy-agents",
        "demos": "demos",
    }.items():
        page.goto(f"{live_server_url}{path}")
        expect(page.locator(f"#{panel_id}")).to_have_class(re.compile(r"\bactive\b"))
        expect(page).to_have_url(re.compile(rf"/{path}(\?agent=.*)?$"))

    for path in ("project-requirements", "combined/query", "sources/quality/documents", "source/mcp"):
        assert requests.get(f"{live_server_url}{path}", timeout=10).status_code == 404

    page.goto(f"{live_server_url}source/search")
    page.get_by_role("link", name="Cocoarynth home").click()
    expect(page.locator("#home")).to_have_class(re.compile(r"\bactive\b"))
    page.goto(f"{live_server_url}knowledge-base/query")
    expect(page.locator("#kb-query")).to_have_class(re.compile(r"\bactive\b"))
    expect(page).to_have_url(re.compile(r"/knowledge-base/query$"))

    page.go_back()
    page.go_back()
    expect(page.locator("#search")).to_have_class(re.compile(r"\bactive\b"))
    expect(page).to_have_url(re.compile(r"/source/search$"))


def test_loads_content_understanding_chunks(page: Page, live_server_url: str, live_smoke: bool) -> None:
    mock_app_apis(page, live_smoke)
    chunk_urls: list[str] = []
    page.on("request", lambda request: chunk_urls.append(request.url) if "/chunks?" in request.url else None)
    page.goto(f"{live_server_url}source/chunks")

    document_select = page.locator("#document-select")
    if live_smoke:
        expect(document_select.locator("option")).not_to_have_count(1)
        document_select.select_option(index=1)
        expect(page.locator("#chunk-list article").first).to_be_visible()
    else:
        document_select.select_option(label="hold-notice-mg26-l02.pdf")
        expect(page.locator("#chunk-list")).to_contain_text("Lot MG26-L02 held on arrival")
        assert any("/api/source/chunks?document=hold-notice-mg26-l02.pdf" in url for url in chunk_urls)
    expect(page.locator("#chunk-summary")).to_contain_text("cocoarynth-all-index")
    expect(page.locator("#chunk-list")).to_contain_text("page_number_from")


def test_runs_hybrid_source_search(page: Page, live_server_url: str, live_smoke: bool) -> None:
    mock_app_apis(page, live_smoke)
    search_requests: list[tuple[str, dict]] = []
    page.on(
        "request",
        lambda request: search_requests.append((request.url, request.post_data_json))
        if request.url.endswith("/api/source/search")
        else None,
    )
    page.goto(f"{live_server_url}source/search")

    expect(page.locator("#search-query")).to_have_value("bean moisture limit on receipt")
    page.locator("#search-form").get_by_role("button", name="Search", exact=True).click()

    if live_smoke:
        expect(page.locator("#search-results article").first).to_be_visible()
    else:
        expect(page.locator("#search-results")).to_contain_text("Lot MG26-L02 held on arrival")
        expect(page.locator("#search-results")).to_contain_text("Reranker 3.750")
        expect(page.locator("#search-results")).to_contain_text("Search 0.910")
    expect(page.locator("#search-summary")).to_contain_text("cocoarynth-all-index · hybrid semantic + vector")
    assert search_requests[0][1] == {"query": "bean moisture limit on receipt", "limit": 10}


def test_query_knowledge_base_workflow(page: Page, live_server_url: str, live_smoke: bool) -> None:
    if live_smoke:
        pytest.skip("Model-judged portal acceptance is covered by tests/live_smoke.py.")
    mock_app_apis(page, live_smoke)
    retrieval_payloads: list[dict] = []

    def handle_retrieval(route: Route) -> None:
        retrieval_payloads.append(route.request.post_data_json)
        json_response(route, RETRIEVAL)

    page.route(re.compile(r".*/api/retrieve$"), handle_retrieval)
    page.goto(f"{live_server_url}knowledge-base/query")

    question = page.locator("#kb-query textarea")
    expect(question).to_have_value(re.compile(r"^What happened at the estate that delayed the launch"))
    default_question = question.input_value()
    page.locator("#kb-query").get_by_role("button", name="Query knowledge base").click()

    output = page.locator("#kb-output")
    expect(output).to_contain_text("Lot MG26-L02 was held on arrival.")
    activity = output.locator(".activity-table")
    expect(activity).to_contain_text("cocoarynth-all-source")
    expect(activity).to_contain_text("Madagascar limited edition launch date")
    expect(activity).to_contain_text("Madagascar lot MG26-L02 hold")
    expect(activity).to_contain_text("Result re-ranking")
    references = output.locator(".reference-list")
    expect(references).to_contain_text("launch-plan-madagascar-nibs.pdf")
    expect(references).to_contain_text("hold-notice-mg26-l02.pdf")
    assert retrieval_payloads == [{"question": default_question}]


def test_displays_knowledge_base_configuration(page: Page, live_server_url: str, live_smoke: bool) -> None:
    mock_app_apis(page, live_smoke)
    page.goto(f"{live_server_url}knowledge-base/configuration")
    expect(page).to_have_url(re.compile(r"/knowledge-base/configuration$"))

    configuration = page.locator("#kb-configuration-details")
    expect(configuration).to_contain_text("cocoarynth-kb-all")
    expect(configuration.locator(".configuration-item")).to_have_count(1)
    expect(configuration).not_to_contain_text("Bearer")
    expect(configuration).not_to_contain_text("MCP")
    if not live_smoke:
        facts = configuration.locator(".config-facts").first
        expect(facts).to_contain_text("Reranker threshold2Reasoning")
        expect(facts).to_contain_text("Reasoningauto")
        expect(facts).to_contain_text("gpt-5.5")
    expect(configuration.locator(".source-list > h3")).to_have_text("Knowledge sources · 1")
    row = configuration.locator(".source-row")
    expect(row.locator("h4")).to_have_text(KNOWLEDGE_SOURCE.name)
    facts = row.locator(".source-facts")
    expect(facts).to_contain_text(KNOWLEDGE_SOURCE.knowledge_source)
    expect(facts).to_contain_text("KindsearchIndex")
    expect(facts).to_contain_text(KNOWLEDGE_SOURCE.index)
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
    page.goto(f"{live_server_url}diy-agents")
    expect(page).to_have_url(re.compile(r"/diy-agents\?agent=diy-baseline&question=Which\+origin"))

    agent = page.locator("#diy-agent")
    expect(agent).to_have_value("diy-baseline")
    expect(agent.locator("option")).to_have_text(["1 · Baseline (not running)", "2 · Reflection"])
    expect(page).to_have_title("1 · Baseline · Succeeds: tasting panel winner")
    expect(page.locator("#diy-hint")).to_be_visible()
    expect(page.locator("#diy-hint")).to_contain_text("python -m scripts.run_diy_agents")
    question = page.locator("#diy-question")
    expect(question).to_have_value("Which origin won the tasting panel?")
    expect(page.locator("#diy-example")).to_have_count(0)
    expect(page.locator("#diy-output")).to_contain_text("will appear here")

    agent.select_option("diy-reflection")
    expect(page.locator("#diy-hint")).to_be_hidden()
    expect(page.locator("#diy-capability")).to_contain_text("follow-up searches")
    expect(question).to_have_value("Which origin won the tasting panel?")
    expect(page).to_have_title("2 · Reflection")
    expect(page).to_have_url(re.compile(r"\?agent=diy-reflection&question=Which\+origin"))

    question.fill(DIY_AGENTS["steps"][1]["examples"][0]["question"])
    expect(page).to_have_title("2 · Reflection · Needs reflection: Pantry Lane farms")
    page.get_by_role("button", name="Ask", exact=True).click()

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
    expect(output.locator(".diy-metric").filter(has_text="Retrieval time")).to_contain_text("8.8 s")
    assert output.evaluate(
        "node => node.querySelector('.diy-answer').compareDocumentPosition(node.querySelector('.diy-metrics')) & Node.DOCUMENT_POSITION_FOLLOWING"
    )
    expect(output.locator(".diy-metric").filter(has_text="Searches")).to_contain_text("2")
    expect(output.locator(".diy-metric").filter(has_text="Model calls in retrieval")).to_contain_text("2")
    expect(output.locator(".diy-metric").filter(has_text="Retrieval tokens")).to_contain_text("5,510")
    expect(output.locator(".diy-metric > span")).to_have_text(["Retrieval time", "Searches", "Model calls in retrieval", "Retrieval tokens"])

    rows = output.locator(".activity-row")
    expect(rows).to_have_count(3)
    expect(rows.nth(0)).to_contain_text("Query planning")
    expect(rows.nth(0)).to_contain_text("cocoarynth-origins-index, cocoarynth-logistics-retail-index")
    expect(rows.nth(1).locator(".diy-source")).to_have_text(
        ["cocoarynth-origins-index", "cocoarynth-logistics-retail-index"]
    )
    expect(rows.nth(2).locator(".diy-source")).to_have_text("cocoarynth-quality-index")
    expect(rows.nth(0)).to_contain_text("Model:gpt-5.5")
    expect(rows.nth(0)).to_contain_text("Reasoning effort:medium")
    expect(rows.nth(1)).not_to_contain_text("Reasoning effort")
    expect(output.get_by_text("View step data")).to_have_count(0)
    expect(rows.nth(1)).to_contain_text("Parallel search · round 1")
    expect(rows.nth(1).locator(".diy-search-row")).to_have_count(2)
    expect(rows.nth(2)).to_contain_text("Reflection · round 1")
    expect(rows.nth(2)).to_contain_text("Sufficient:No")
    expect(rows.nth(2)).to_contain_text("Batch record EC26-0811 bean lots")
    expect(rows.nth(2).locator(".token-meter")).to_contain_text("5,000")
    expect(rows.nth(2).locator(".activity-elapsed")).to_have_text("5,900 ms")

    waterfall = output.locator(".waterfall")
    expect(waterfall).to_contain_text("8.8 s of retrieval")
    waterfall_rows = waterfall.locator(".waterfall-row")
    expect(waterfall_rows.locator(".waterfall-label")).to_have_text(
        ["Query planning", "Parallel search · round 1", "Reflection · round 1"]
    )
    expect(waterfall_rows.locator(".waterfall-duration")).to_have_text(["2.1 s", "800 ms", "5.9 s"])
    expect(waterfall_rows.nth(0)).to_have_class(re.compile(r"waterfall-llm"))
    expect(waterfall_rows.nth(1)).to_have_class(re.compile(r"waterfall-search"))
    bar_lefts = waterfall_rows.locator(".waterfall-bar").evaluate_all("bars => bars.map(bar => parseFloat(bar.style.left))")
    assert bar_lefts == sorted(bar_lefts) and bar_lefts[0] == 0
    assert abs(bar_lefts[1] - 2100 / 8800 * 100) < 0.01
    expect(waterfall.locator(".waterfall-key")).to_have_text(["Search", "Model call in retrieval"])
    assert waterfall.evaluate("node => node.compareDocumentPosition(document.querySelector('#diy-output .activity-table')) & Node.DOCUMENT_POSITION_FOLLOWING")

    evidence = output.locator(".reference-item")
    expect(output.locator(".kb-section > h3")).to_have_text(["Answer", "Retrieval process · 3 steps", "1 returned result"])
    expect(evidence).to_have_count(1)
    expect(evidence).to_contain_text("batch-record-ec26-0811.pdf")
    expect(evidence.locator(".resource-kind")).to_have_text("cocoarynth-quality-index")
    expect(evidence).to_contain_text("Relevance 3/3")
    expect(evidence).to_contain_text("Reranker 2.91")
    assert payloads == [{"step": "diy-reflection", "question": DIY_AGENTS["steps"][1]["examples"][0]["question"]}]
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def test_diy_agents_query_parameters_select_agent_and_question(
    page: Page, live_server_url: str, live_smoke: bool
) -> None:
    if live_smoke:
        pytest.skip("The DIY agents run locally and are not part of live smoke testing.")
    mock_app_apis(page, live_smoke)
    payloads: list[dict] = []
    page.route(
        re.compile(r".*/api/diy/ask$"),
        lambda route: payloads.append(route.request.post_data_json) or json_response(route, DIY_ANSWER),
    )
    page.goto(f"{live_server_url}diy-agents?agent=diy-reflection&question=Why+did+the+Ghana+bars+turn+grey%3F")

    expect(page.locator("#diy-agent")).to_have_value("diy-reflection")
    expect(page.locator("#diy-question")).to_have_value("Why did the Ghana bars turn grey?")
    expect(page).to_have_title("2 · Reflection · One round: grey Ghana bars")
    expect(page.locator("#diy-output")).to_contain_text("will appear here")
    assert payloads == []

    page.locator("#diy-question").fill("Which farms grew the cacao?")
    expect(page).to_have_url(re.compile(r"\?agent=diy-reflection&question=Which\+farms\+grew\+the\+cacao%3F$"))

    page.goto(f"{live_server_url}diy-agents?agent=unknown")
    expect(page.locator("#diy-agent")).to_have_value("diy-baseline")
    expect(page.locator("#diy-question")).to_have_value("Which origin won the tasting panel?")

    page.goto(f"{live_server_url}diy-agents?agent=diy-reflection&question=Why+did+the+Ghana+bars+turn+grey%3F&run=1")
    expect(page.locator("#diy-output .diy-answer strong")).to_have_text("EC26-0811")
    assert payloads == [{"step": "diy-reflection", "question": "Why did the Ghana bars turn grey?"}]
    expect(page).to_have_url(re.compile(r"\?agent=diy-reflection&question=Why\+did[^&]*$"))


def test_demos_page_links_each_example_in_a_new_tab(page: Page, live_server_url: str, live_smoke: bool) -> None:
    mock_app_apis(page, live_smoke)
    page.goto(f"{live_server_url}demos")

    expect(page.locator("#demos")).to_have_class(re.compile(r"\bactive\b"))
    if live_smoke:
        return
    links = page.locator("#demo-list a")
    expect(links.locator("strong")).to_have_text(["Reflection strategy", "Baseline strategy"])
    expect(links.nth(0)).to_contain_text("Reflection · Needs reflection: Pantry Lane farms")
    expect(links.nth(0)).to_have_attribute("target", "_blank")
    expect(links.nth(0)).to_have_attribute(
        "href", "/diy-agents?agent=diy-reflection&question=A+Pantry+Lane+customer+asked+which+farms+grew+the+cacao.&run=1"
    )


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

    page.get_by_role("button", name="Ask", exact=True).click()

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

    page.get_by_role("button", name="Ask", exact=True).click()

    rows = page.locator("#diy-output .activity-row")
    expect(rows.nth(0)).to_contain_text("Index:cocoarynth-quality-index")
    waterfall_rows = page.locator("#diy-output .waterfall-row")
    expect(waterfall_rows.locator(".waterfall-label")).to_have_text(["Search", "Parallel search", "Complexity routing", "Reranking"])
    expect(waterfall_rows.locator(".waterfall-agent")).to_have_count(0)
    search_rows = rows.nth(1).locator(".diy-search-row")
    expect(search_rows).to_have_count(2)
    expect(search_rows.locator(".diy-source")).to_have_text(["cocoarynth-quality-index", "cocoarynth-quality-index"])

    expect(rows.nth(2)).to_contain_text("Complexity routing")
    expect(rows.nth(2)).to_contain_text("Model:decision-1")
    expect(rows.nth(2)).to_contain_text("Agentic probability:0.97")
    expect(rows.nth(2)).not_to_contain_text("Reasoning effort")
    expect(rows.nth(3)).to_contain_text("Reranking")
    expect(rows.nth(3)).to_contain_text("Kept:4 of 10 results")
    expect(page.locator("#diy-output .reference-item")).to_contain_text("Relevance 2.4/3")
