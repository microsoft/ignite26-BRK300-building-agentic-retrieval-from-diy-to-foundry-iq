from collections.abc import Callable, Generator

import pytest
import requests
from azure.identity import AzureDeveloperCliCredential, get_bearer_token_provider
from openai import OpenAI
from playwright.sync_api import Page, expect

from app.config import Settings
from app.naming import KNOWLEDGE_SOURCES

expect.set_options(timeout=180_000)

KNOWLEDGE_BASE_QUESTION = (
    "What happened at the estate that delayed the launch of the Madagascar limited-edition bar with cacao nibs?"
)
AZURE_OPENAI_SCOPE = "https://cognitiveservices.azure.com/.default"
Judge = Callable[[str, str, str, str], str]


@pytest.fixture(scope="session")
def live_portal_url(pytestconfig: pytest.Config) -> str:
    base_url = pytestconfig.getoption("--smoke-base-url")
    if not base_url:
        pytest.skip("Run with --smoke-base-url pointing to the deployed portal.")
    url = f"{base_url.rstrip('/')}/"
    response = requests.get(f"{url}health", timeout=30)
    response.raise_for_status()
    return url


@pytest.fixture(scope="session")
def azure_openai_judge() -> Generator[Judge, None, None]:
    settings = Settings.from_environment()
    credential = AzureDeveloperCliCredential()
    client = OpenAI(
        base_url=f"{settings.openai_endpoint}/openai/v1/",
        api_key=get_bearer_token_provider(credential, AZURE_OPENAI_SCOPE),
    )

    def judge(question: str, answer: str, references: str, rubric: str) -> str:
        response = client.responses.create(
            model=settings.chat_deployment,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You judge an extractive retrieval system acceptance test. The system returns evidence, "
                        "not a synthesized answer. Evaluate whether the supplied evidence and references are "
                        "relevant and sufficient to answer the question. Do not fail because the evidence is not "
                        "rewritten as a direct prose answer. Start with PASS or FAIL, then give one concise reason."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Question:\n{question}\n\nRubric:\n{rubric}\n\n"
                        f"Retrieved evidence:\n{answer}\n\nDisplayed references:\n{references}"
                    ),
                },
            ],
            max_output_tokens=300,
            timeout=180,
        )
        output_text = response.output_text.strip()
        if not output_text:
            raise AssertionError(f"Azure OpenAI judge returned no text: {response.model_dump_json()}")
        return output_text

    yield judge
    client.close()
    credential.close()


def test_sources_live_workflow(page: Page, live_portal_url: str) -> None:
    page.goto(f"{live_portal_url}sources/documents/documents")
    sources = page.locator(".nav-group").filter(has_text="Knowledge sources")

    expect(page.locator("#document-list")).to_contain_text(
        "mapayca-markets-traceability-pilot-requirements.pdf"
    )

    sources.get_by_role("button", name="View document chunks", exact=True).click()
    page.locator("#document-select").select_option(
        label="mapayca-markets-traceability-pilot-requirements.pdf"
    )
    expect(page.locator("#chunk-list article").first).to_be_visible()
    expect(page.locator("#chunk-list")).to_contain_text("page_number_from")

    sources.get_by_role("button", name="Search the index", exact=True).click()
    page.locator("#search-source").select_option(label="Quality checks")
    page.locator("#search-form").get_by_role("button", name="Search").click()
    expect(page.locator("#search-summary")).to_contain_text("cocoarynth-quality-index")
    expect(page.locator("#search-results article").first).to_be_visible()


def test_knowledge_base_live_workflow(page: Page, live_portal_url: str, azure_openai_judge: Judge) -> None:
    page.goto(live_portal_url)
    knowledge_base = page.locator(".nav-group").filter(has_text="Knowledge base")
    knowledge_base.get_by_role("button", name="Configuration", exact=True).click()

    configuration = page.locator("#kb-configuration-details")
    expect(configuration).to_contain_text("cocoarynth-kb-all")
    expect(configuration.locator(".source-row")).to_have_count(len(KNOWLEDGE_SOURCES))
    for source in KNOWLEDGE_SOURCES:
        facts = configuration.locator(".source-row").filter(has_text=source.knowledge_source).locator(".source-facts")
        expect(facts).to_contain_text("KindsearchIndex")
        expect(facts).to_contain_text(source.index)

    knowledge_base.get_by_role("button", name="Query knowledge base", exact=True).click()
    page.locator("#kb-query textarea").fill(KNOWLEDGE_BASE_QUESTION)
    page.locator("#kb-query").get_by_role("button", name="Query knowledge base").click()

    output = page.locator("#kb-output")
    references = output.locator(".reference-list")
    activity = output.locator(".activity-table")
    expect(output.locator("article").first).to_be_visible()
    expect(references.locator("article").first).to_be_visible()
    expect(activity).to_contain_text("Index search")

    verdict = azure_openai_judge(
        KNOWLEDGE_BASE_QUESTION,
        output.inner_text(),
        references.inner_text(),
        (
            "PASS only if the retrieved evidence explains what happened at Tsaravola Estate (rain that cut drying "
            "short) and links it to the delayed Madagascar limited-edition launch, with at least one relevant reference."
        ),
    )
    assert verdict.upper().startswith("PASS"), verdict
