"""DIY agentic retrieval, step 2: LLM query decomposition with parallel searches."""

import asyncio
import json
import logging
import os
import time
from typing import Annotated, Any

from agent_framework import Agent, FunctionInvocationConfiguration, Message, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework.openai import OpenAIChatOptions
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity.aio import AzureDeveloperCliCredential, ManagedIdentityCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import QueryType, VectorizableTextQuery
from pydantic import BaseModel, Field

logger = logging.getLogger("diy-query-decomposition")

PROJECT_ENDPOINT = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
MODEL_DEPLOYMENT_NAME = os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"]
SEARCH_ENDPOINT = os.environ["FOUNDRY_IQ_SEARCH_ENDPOINT"]
INDEX_NAME = "cocoarynth-all-index"

credential = (
    ManagedIdentityCredential()
    if "FOUNDRY_HOSTING_ENVIRONMENT" in os.environ
    else AzureDeveloperCliCredential(tenant_id=os.environ["AZURE_TENANT_ID"], process_timeout=60)
)
search_client = SearchClient(endpoint=SEARCH_ENDPOINT, index_name=INDEX_NAME, credential=credential)
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
    instructions: str, prompt: str, response_format: type[BaseModel]
) -> tuple[Any, dict[str, Any]]:
    response = await chat_client.get_response(
        [Message("user", [prompt])],
        options={"instructions": instructions, "response_format": response_format, "store": False},
    )
    usage = response.usage_details or {}
    reported_reasoning = getattr(response.raw_representation, "reasoning", None)
    return response.value, {
        "model": MODEL_DEPLOYMENT_NAME,
        "reasoning_effort": reported_reasoning.effort if reported_reasoning else None,
        "input_tokens": usage.get("input_token_count"),
        "output_tokens": usage.get("output_token_count"),
    }


async def hybrid_search(query: str, top: int = 5) -> list[dict[str, Any]]:
    results = await search_client.search(
        search_text=query,
        vector_queries=[VectorizableTextQuery(text=query, k_nearest_neighbors=50, fields="text_vector")],
        query_type=QueryType.SEMANTIC,
        semantic_configuration_name="semantic-configuration",
        top=top,
        select=["chunk_id", "title", "chunk"],
    )
    return [
        {
            "id": result["chunk_id"],
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


class QueryPlan(BaseModel):
    queries: list[str] = Field(description="One to five focused search queries, each covering one information need.")


PLANNER_INSTRUCTIONS = """You plan searches over Cocoarynth's company records.
Split the user's question into the separate searches needed to answer every part of it.
Use a single query when the question has only one information need."""


@tool
async def search_knowledge(
    question: Annotated[str, Field(description="The user's full question, unchanged.")],
) -> dict[str, Any]:
    """Plan and run the searches needed to answer a question from Cocoarynth's company records."""
    activity = ActivityLog()

    started = time.perf_counter()
    plan, llm_call = await ask_llm(PLANNER_INSTRUCTIONS, question, QueryPlan)
    activity.record("query_planning", started, llm_call, queries=plan.queries)

    started = time.perf_counter()
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(hybrid_search(query)) for query in plan.queries]
    result_lists = [task.result() for task in tasks]
    activity.record(
        "parallel_search",
        started,
        searches=[
            {"index": INDEX_NAME, "query": query, "result_count": len(results)}
            for query, results in zip(plan.queries, result_lists)
        ],
    )

    evidence = combine_results(result_lists)
    return {"evidence": evidence, "activity": activity.steps}


async def main() -> None:
    agent = Agent(
        client=chat_client,
        name="diy-query-decomposition",
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
        await search_client.close()
        await credential.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    logger.setLevel(logging.INFO)
    asyncio.run(main())
