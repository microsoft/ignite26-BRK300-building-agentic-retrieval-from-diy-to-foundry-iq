"""DIY agentic retrieval, step 5: LLM source selection, so each query only searches relevant sources."""

import asyncio
import json
import logging
import os
import time
from typing import Annotated, Any, Literal

from agent_framework import Agent, FunctionInvocationConfiguration, Message, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework.openai import OpenAIChatOptions
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity.aio import AzureDeveloperCliCredential, ManagedIdentityCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import QueryType, VectorizableTextQuery
from pydantic import BaseModel, Field

logger = logging.getLogger("diy-source-selection")

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
    # Allow one search_knowledge call per question, so the agent can't add searches of its own.
    function_invocation_configuration=FunctionInvocationConfiguration(max_function_calls=1),
)


class ActivityLog:
    """Records each retrieval step with its timing and LLM usage so the demo can show what happened."""

    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    def record(self, step: str, started: float, llm_call: dict[str, Any] | None = None, **details: Any) -> None:
        elapsed = time.perf_counter() - started
        entry = {
            "step": step,
            "elapsed_ms": round(elapsed * 1000),
            "started_at_ms": round((time.time() - elapsed) * 1000),
            **details,
            **(llm_call or {}),
        }
        self.steps.append(entry)
        logger.info("%s", json.dumps(entry))


async def ask_llm(
    instructions: str,
    prompt: str,
    response_format: type[BaseModel],
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = None,
) -> tuple[Any, dict[str, Any]]:
    options = OpenAIChatOptions(instructions=instructions, response_format=response_format, store=False)
    if reasoning_effort:
        options["reasoning"] = {"effort": reasoning_effort}
    response = await chat_client.get_response([Message("user", [prompt])], options=options)
    usage = response.usage_details or {}
    reported_reasoning = getattr(response.raw_representation, "reasoning", None)
    return response.value, {
        "model": MODEL_DEPLOYMENT_NAME,
        "reasoning_effort": reported_reasoning.effort if reported_reasoning else None,
        "input_tokens": usage.get("input_token_count"),
        "output_tokens": usage.get("output_token_count"),
    }


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


class Reflection(BaseModel):
    sufficient: bool = Field(description="True if the evidence answers every part of the question.")
    missing: str = Field(description="What information is still missing, or an empty string.")
    follow_up_searches: list[PlannedSearch] = Field(description="Searches for the missing information, if any.")


REFLECTION_INSTRUCTIONS = f"""You check whether search evidence fully answers the user's question.
The knowledge sources are:
{json.dumps(SOURCE_DESCRIPTIONS, indent=2)}
If information is missing, plan follow-up searches for it.
Use specific names, terms, or references found in the evidence to target those searches.
Word each follow-up query the way the missing document would describe it, not like the evidence you already have.
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
    # Low effort measured best: "none" missed multi-hop gaps (a lot code that needs another lookup),
    # while medium and high were no more accurate and took up to twice as long.
    reflection, llm_call = await ask_llm(REFLECTION_INSTRUCTIONS, prompt, Reflection, reasoning_effort="low")
    activity.record(
        "reflection",
        started,
        llm_call,
        round=round_number,
        sufficient=reflection.sufficient,
        missing=reflection.missing,
        follow_up_searches=describe_searches(reflection.follow_up_searches),
    )
    return reflection


@tool
async def search_knowledge(
    question: Annotated[str, Field(description="The user's full question, unchanged.")],
) -> dict[str, Any]:
    """Plan and run the searches needed to answer a question from Cocoarynth's company records."""
    activity = ActivityLog()

    started = time.perf_counter()
    plan, llm_call = await ask_llm(PLANNER_INSTRUCTIONS, question, QueryPlan)
    activity.record("query_planning", started, llm_call, searches=describe_searches(plan.searches))

    planned_searches = plan.searches
    searched: list[str] = []
    result_lists: list[list[dict[str, Any]]] = []
    for round_number in range(1, MAX_ROUNDS + 1):
        result_lists += await run_searches(planned_searches, activity, round_number)
        searched += [planned.query for planned in planned_searches]
        evidence = combine_results(result_lists)

        if round_number == MAX_ROUNDS:
            break
        reflection = await reflect(question, evidence, searched, activity, round_number)
        if reflection.sufficient or not reflection.follow_up_searches:
            break
        planned_searches = reflection.follow_up_searches

    return {"evidence": evidence, "activity": activity.steps}


async def main() -> None:
    agent = Agent(
        client=chat_client,
        name="diy-source-selection",
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
