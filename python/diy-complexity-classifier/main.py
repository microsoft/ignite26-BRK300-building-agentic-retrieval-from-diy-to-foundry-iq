"""DIY agentic retrieval, step 7: an LLM classifier routes simple questions to direct retrieval."""

import asyncio
import json
import logging
import os
import time
from typing import Annotated, Any, Literal, get_args

from agent_framework import Agent, Message, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework.openai import OpenAIChatOptions
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity.aio import AzureDeveloperCliCredential, ManagedIdentityCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import QueryType, VectorizableTextQuery
from pydantic import BaseModel, Field

logger = logging.getLogger("diy-complexity-classifier")

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
MAX_ROUNDS = 3

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


async def run_searches(
    planned_searches: list[PlannedSearch], activity: ActivityLog, round_number: int
) -> list[list[dict[str, Any]]]:
    started = time.perf_counter()
    searches = [(source, planned.query) for planned in planned_searches for source in planned.sources]
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(hybrid_search(source, query)) for source, query in searches]
    result_lists = [task.result() for task in tasks]
    activity.record(
        "parallel_search",
        started,
        round=round_number,
        searches=[
            {"index": SOURCE_INDEXES[source], "query": query, "result_count": len(results)}
            for (source, query), results in zip(searches, result_lists)
        ],
    )
    return result_lists


class RelevanceJudgment(BaseModel):
    id: str
    relevance: int = Field(
        description="0 = irrelevant, 1 = related background, 2 = partially answers, 3 = directly answers part of the question."
    )


class Ranking(BaseModel):
    judgments: list[RelevanceJudgment] = Field(description="One judgment for every search result.")


RERANKER_INSTRUCTIONS = """You judge how well each search result helps answer the user's question.
Judge every result on its own content, regardless of which search returned it."""


async def rerank(
    question: str, candidates: list[dict[str, Any]], activity: ActivityLog, round_number: int
) -> list[dict[str, Any]]:
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
    activity.record(
        "llm_reranking", started, usage, round=round_number, candidate_count=len(candidates), kept_count=len(ranked)
    )
    return ranked


class Reflection(BaseModel):
    sufficient: bool = Field(description="True if the evidence answers every part of the question.")
    missing: str = Field(description="What information is still missing, or an empty string.")
    follow_up_searches: list[PlannedSearch] = Field(description="Searches for the missing information, if any.")


REFLECTION_INSTRUCTIONS = f"""You check whether search evidence fully answers the user's question.
The knowledge sources are:
{json.dumps(SOURCE_DESCRIPTIONS, indent=2)}
If information is missing, plan follow-up searches for it.
Use specific names, terms, or references found in the evidence to target those searches.
Do not repeat searches that have already run."""


async def reflect(
    question: str, evidence: list[dict[str, Any]], searched: list[str], activity: ActivityLog, round_number: int
) -> Reflection:
    started = time.perf_counter()
    prompt = json.dumps(
        {
            "question": question,
            "searches_already_run": searched,
            "evidence": [{"title": item["title"], "content": item["content"]} for item in evidence],
        }
    )
    reflection, usage = await ask_llm(REFLECTION_INSTRUCTIONS, prompt, Reflection)
    activity.record(
        "reflection",
        started,
        usage,
        round=round_number,
        sufficient=reflection.sufficient,
        missing=reflection.missing,
        follow_up_searches=describe_searches(reflection.follow_up_searches),
    )
    return reflection


class Route(BaseModel):
    route: Literal["direct", "agentic"]
    reason: str = Field(description="One short sentence explaining the choice.")


ROUTER_INSTRUCTIONS = f"""You decide how much retrieval a question needs. The knowledge sources are:
{json.dumps(SOURCE_DESCRIPTIONS, indent=2)}
Choose "direct" when one search with the question as written is likely to find the whole answer.
Choose "agentic" when the question has several parts, spans several sources, or needs follow-up lookups."""


async def direct_retrieval(question: str, activity: ActivityLog) -> list[dict[str, Any]]:
    """Search every source once with the question as written, with no extra model calls."""
    direct_search = PlannedSearch(query=question, sources=list(get_args(SourceName)))
    result_lists = await run_searches([direct_search], activity, round_number=1)
    return combine_results(result_lists)[:MAX_EVIDENCE]


async def agentic_retrieval(question: str, activity: ActivityLog) -> list[dict[str, Any]]:
    """Plan, search, rerank, and reflect until the evidence is sufficient or the round limit is reached."""
    started = time.perf_counter()
    plan, usage = await ask_llm(PLANNER_INSTRUCTIONS, question, QueryPlan)
    activity.record("query_planning", started, usage, searches=describe_searches(plan.searches))

    planned_searches = plan.searches
    searched: list[str] = []
    evidence: list[dict[str, Any]] = []
    for round_number in range(1, MAX_ROUNDS + 1):
        result_lists = await run_searches(planned_searches, activity, round_number)
        searched += [planned.query for planned in planned_searches]

        known_ids = {item["id"] for item in evidence}
        new_candidates = [result for result in combine_results(result_lists) if result["id"] not in known_ids]
        if new_candidates:
            evidence += await rerank(question, new_candidates, activity, round_number)
            evidence = sorted(evidence, key=lambda item: item["llm_relevance"], reverse=True)[:MAX_EVIDENCE]

        if round_number == MAX_ROUNDS:
            break
        reflection = await reflect(question, evidence, searched, activity, round_number)
        if reflection.sufficient or not reflection.follow_up_searches:
            break
        planned_searches = reflection.follow_up_searches

    return evidence


@tool
async def search_knowledge(
    question: Annotated[str, Field(description="The user's full question, unchanged.")],
) -> dict[str, Any]:
    """Plan and run the searches needed to answer a question from Cocoarynth's company records."""
    activity = ActivityLog()

    started = time.perf_counter()
    route, usage = await ask_llm(ROUTER_INSTRUCTIONS, question, Route)
    activity.record("complexity_routing", started, usage, route=route.route, reason=route.reason)

    if route.route == "direct":
        evidence = await direct_retrieval(question, activity)
    else:
        evidence = await agentic_retrieval(question, activity)
    return {"evidence": evidence, "activity": activity.steps}


async def main() -> None:
    agent = Agent(
        client=chat_client,
        name="diy-complexity-classifier",
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
