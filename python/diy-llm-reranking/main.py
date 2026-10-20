"""DIY agentic retrieval, step 5: LLM reranking of the combined evidence from every search."""

import asyncio
import json
import logging
import os
import time
from typing import Annotated, Any, Literal

from agent_framework import Agent, Message, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework.openai import OpenAIChatOptions
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity.aio import AzureDeveloperCliCredential, ManagedIdentityCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import QueryType, VectorizableTextQuery
from pydantic import BaseModel, Field

logger = logging.getLogger("diy-llm-reranking")

PROJECT_ENDPOINT = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
MODEL_DEPLOYMENT_NAME = os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"]
SEARCH_ENDPOINT = os.environ["FOUNDRY_IQ_SEARCH_ENDPOINT"]
SOURCE_INDEXES = {
    "origins": "cocoarynth-origins-index",
    "quality": "cocoarynth-quality-index",
    "logistics_retail": "cocoarynth-logistics-retail-index",
    "policies": "cocoarynth-policies-index",
    "origin_passport_project": "cocoarynth-documents-index",
    "engineering_practices": "cocoarynth-engineering-practices-index",
}
SOURCE_DESCRIPTIONS = {
    "origins": "Farm and supplier records, partly in Spanish: origin profiles, farm inspections, "
    "collection tickets, fermentation logs, organic certification, harvest reports, and bean prices.",
    "quality": "Factory records: bean receiving and moisture tests, holds and releases, lab certificates, "
    "tasting panels, nonconformance reports, batch records, and retention samples.",
    "logistics_retail": "Shipping and sales records: bills of lading, depot handling logs, temperature loggers, "
    "carrier incidents, retailer emails and agreements, launch plans, price lists, and sales reviews.",
    "policies": "Company policies and reference: label claims, data sharing, storage and handling, "
    "sourcing principles, bean quality specification, product launch process, glossary, and org chart.",
    "origin_passport_project": "The Origin Passport software project: product requirements, architecture "
    "decisions, its traceability data-sharing policy, and support runbooks.",
    "engineering_practices": "Software engineering guidance for API design, React and TypeScript, "
    "frontend accessibility, and engineering culture.",
}
SourceName = Literal[
    "origins", "quality", "logistics_retail", "policies", "origin_passport_project", "engineering_practices"
]
MIN_RELEVANCE = 2
MAX_EVIDENCE = 8

credential = (
    ManagedIdentityCredential()
    if "FOUNDRY_HOSTING_ENVIRONMENT" in os.environ
    else AzureDeveloperCliCredential(tenant_id=os.environ["AZURE_TENANT_ID"], process_timeout=60)
)
search_clients = {
    source: SearchClient(endpoint=SEARCH_ENDPOINT, index_name=index_name, credential=credential)
    for source, index_name in SOURCE_INDEXES.items()
}
chat_client = FoundryChatClient(
    project_endpoint=PROJECT_ENDPOINT,
    model=MODEL_DEPLOYMENT_NAME,
    credential=credential,
)


class ActivityLog:
    """Records each retrieval step with its latency and token usage so the demo can show what happened."""

    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    def record(self, step: str, started: float, usage: Any = None, **details: Any) -> None:
        entry = {"step": step, "elapsed_ms": round((time.perf_counter() - started) * 1000), **details}
        if usage:
            entry["model"] = MODEL_DEPLOYMENT_NAME
            entry["input_tokens"] = usage.get("input_token_count")
            entry["output_tokens"] = usage.get("output_token_count")
        self.steps.append(entry)
        logger.info("%s", json.dumps(entry))


async def ask_llm(instructions: str, prompt: str, response_format: type[BaseModel]) -> tuple[Any, Any]:
    response = await chat_client.get_response(
        [Message("user", [prompt])],
        options={"instructions": instructions, "response_format": response_format, "store": False},
    )
    return response.value, response.usage_details


async def hybrid_search(source: str, query: str, top: int = 5) -> list[dict[str, Any]]:
    results = await search_clients[source].search(
        search_text=query,
        vector_queries=[VectorizableTextQuery(text=query, k_nearest_neighbors=50, fields="text_vector")],
        query_type=QueryType.SEMANTIC,
        semantic_configuration_name="semantic-configuration",
        top=top,
        select=["chunk_id", "title", "chunk"],
    )
    return [
        {
            "id": f"{source}/{result['chunk_id']}",
            "index": SOURCE_INDEXES[source],
            "title": result["title"],
            "content": result["chunk"],
            "reranker_score": result["@search.reranker_score"] or 0.0,
        }
        async for result in results
    ]


def combine_results(result_lists: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Merge results from every search, keeping each chunk once with its best score."""
    best: dict[str, dict[str, Any]] = {}
    for results in result_lists:
        for result in results:
            current = best.get(result["id"])
            if current is None or result["reranker_score"] > current["reranker_score"]:
                best[result["id"]] = result
    return sorted(best.values(), key=lambda result: result["reranker_score"], reverse=True)


class PlannedSearch(BaseModel):
    query: str = Field(description="A focused search query covering one information need.")
    sources: list[SourceName] = Field(description="Only the sources likely to contain the answer.")


class QueryPlan(BaseModel):
    searches: list[PlannedSearch] = Field(description="One to five planned searches.")


def describe_searches(searches: list[PlannedSearch]) -> list[dict[str, Any]]:
    """Show planned searches with full index names in the activity log."""
    return [
        {"query": search.query, "indexes": [SOURCE_INDEXES[source] for source in search.sources]}
        for search in searches
    ]


PLANNER_INSTRUCTIONS = f"""You plan searches over these knowledge sources:
{json.dumps(SOURCE_DESCRIPTIONS, indent=2)}
Split the user's question into the separate searches needed to answer every part of it.
Use a single search when the question has only one information need.
Send each search only to the sources likely to contain its answer."""


class RelevanceJudgment(BaseModel):
    id: str
    relevance: int = Field(
        description="0 = irrelevant, 1 = related background, 2 = partially answers, 3 = directly answers part of the question."
    )


class Ranking(BaseModel):
    judgments: list[RelevanceJudgment] = Field(description="One judgment for every search result.")


RERANKER_INSTRUCTIONS = """You judge how well each search result helps answer the user's question.
Judge every result on its own content, regardless of which search returned it."""


async def rerank(question: str, candidates: list[dict[str, Any]], activity: ActivityLog) -> list[dict[str, Any]]:
    started = time.perf_counter()
    prompt = json.dumps(
        {
            "question": question,
            "results": [
                {"id": candidate["id"], "title": candidate["title"], "content": candidate["content"]}
                for candidate in candidates
            ],
        }
    )
    ranking, usage = await ask_llm(RERANKER_INSTRUCTIONS, prompt, Ranking)
    relevance = {judgment.id: judgment.relevance for judgment in ranking.judgments}
    ranked = sorted(
        (
            {**candidate, "llm_relevance": relevance[candidate["id"]]}
            for candidate in candidates
            if relevance.get(candidate["id"], 0) >= MIN_RELEVANCE
        ),
        key=lambda candidate: candidate["llm_relevance"],
        reverse=True,
    )[:MAX_EVIDENCE]
    activity.record("llm_reranking", started, usage, candidate_count=len(candidates), kept_count=len(ranked))
    return ranked


@tool
async def search_knowledge(
    question: Annotated[str, Field(description="The user's full question, unchanged.")],
) -> dict[str, Any]:
    """Plan and run the searches needed to answer a question from Cocoarynth's company records."""
    activity = ActivityLog()

    started = time.perf_counter()
    plan, usage = await ask_llm(PLANNER_INSTRUCTIONS, question, QueryPlan)
    activity.record("query_planning", started, usage, searches=describe_searches(plan.searches))

    started = time.perf_counter()
    searches = [(source, planned.query) for planned in plan.searches for source in planned.sources]
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(hybrid_search(source, query)) for source, query in searches]
    result_lists = [task.result() for task in tasks]
    activity.record(
        "parallel_search",
        started,
        searches=[
            {"index": SOURCE_INDEXES[source], "query": query, "result_count": len(results)}
            for (source, query), results in zip(searches, result_lists)
        ],
    )

    evidence = await rerank(question, combine_results(result_lists), activity)
    return {"evidence": evidence, "activity": activity.steps}


async def main() -> None:
    agent = Agent(
        client=chat_client,
        name="diy-llm-reranking",
        instructions="""You answer questions about Cocoarynth Chocolate Works, a bean-to-bar
        chocolate maker, from its company records.
        Always call search_knowledge with the user's full question before answering,
        and answer only from the evidence it returns.
        Cite the title of each document you use in square brackets.
        If the evidence does not answer every part of the question, say what is missing.""",
        tools=[search_knowledge],
        default_options=OpenAIChatOptions(store=False),
    )
    server = ResponsesHostServer(agent)
    try:
        await server.run_async()
    finally:
        for search_client in search_clients.values():
            await search_client.close()
        await credential.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    logger.setLevel(logging.INFO)
    asyncio.run(main())
