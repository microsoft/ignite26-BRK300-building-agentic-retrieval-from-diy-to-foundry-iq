"""DIY agentic retrieval, step 4: a classifier routes simple questions to one search, and the rest to reflection."""

import asyncio
import json
import logging
import os
import time
from typing import Annotated, Any, Literal

import httpx
from agent_framework import Agent, FunctionInvocationConfiguration, Message, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework.openai import OpenAIChatOptions
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity.aio import AzureDeveloperCliCredential, ManagedIdentityCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import QueryType, VectorizableTextQuery
from pydantic import BaseModel, Field

logger = logging.getLogger("diy-complexity-routing")

PROJECT_ENDPOINT = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
MODEL_DEPLOYMENT_NAME = os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"]
SEARCH_ENDPOINT = os.environ["FOUNDRY_IQ_SEARCH_ENDPOINT"]
DECISION_DEPLOYMENT_NAME = os.environ["FOUNDRY_DECISION_DEPLOYMENT_NAME"]
# Microsoft-Decision-1 is called on the Foundry resource endpoint, not the project endpoint.
DECISION_URL = PROJECT_ENDPOINT.split("/api/projects/")[0] + "/providers/microsoft/v1/systemone"
INDEX_NAME = "cocoarynth-all-index"
MAX_ROUNDS = 3
# "decision" routes with Microsoft-Decision-1; "chat" asks the chat model, which also writes a reason.
ROUTER: Literal["decision", "chat"] = "decision"
# With ROUTE_CRITERIA below, Decision-1 gave three simple questions p(agentic) <= 0.12 and four compound or
# multi-hop questions >= 0.95. Criteria about "follow-up lookups" scored the estate question only 0.32.
AGENTIC_PROBABILITY_THRESHOLD = 0.8

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
decision_client = httpx.AsyncClient(timeout=60)


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


async def ask_decision_model(state: Any, questions: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ask Microsoft-Decision-1 typed questions about a state. It returns numbers, not generated text."""
    token = await credential.get_token("https://cognitiveservices.azure.com/.default")
    for attempt in range(4):
        response = await decision_client.post(
            DECISION_URL,
            json={"model": DECISION_DEPLOYMENT_NAME, "state": state, "questions": questions},
            headers={"Authorization": f"Bearer {token.token}"},
        )
        if response.status_code != 429 or attempt == 3:
            break
        await asyncio.sleep(float(response.headers.get("retry-after", 2**attempt)))
    response.raise_for_status()
    payload = response.json()
    return payload["answers"], {
        "model": DECISION_DEPLOYMENT_NAME,
        "input_tokens": payload["usage"]["input_tokens"],
        "output_tokens": payload["usage"]["output_tokens"],
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


async def run_searches(queries: list[str], activity: ActivityLog, round_number: int) -> list[list[dict[str, Any]]]:
    started = time.perf_counter()
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(hybrid_search(query)) for query in queries]
    result_lists = [task.result() for task in tasks]
    activity.record(
        "parallel_search",
        started,
        round=round_number,
        searches=[
            {"index": INDEX_NAME, "query": query, "result_count": len(results)}
            for query, results in zip(queries, result_lists)
        ],
    )
    return result_lists


class Reflection(BaseModel):
    sufficient: bool = Field(description="True if the evidence answers every part of the question.")
    missing: str = Field(description="What information is still missing, or an empty string.")
    follow_up_queries: list[str] = Field(description="Search queries for the missing information, if any.")


REFLECTION_INSTRUCTIONS = """You check whether search evidence from Cocoarynth's company records fully answers
the user's question.
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
        follow_up_searches=[
            {"query": query, "indexes": [INDEX_NAME]} for query in reflection.follow_up_queries
        ],
    )
    return reflection


class Route(BaseModel):
    route: Literal["direct", "agentic"]
    reason: str = Field(description="One short sentence explaining the choice.")


ROUTE_CRITERIA = {
    "direct": "One document, such as a policy, report, or record, states the whole answer directly.",
    "agentic": "The answer must be pieced together from several documents: it has several parts, asks why or "
    "what caused something, or traces one record to another.",
}

ROUTER_INSTRUCTIONS = f"""You decide how much retrieval a question about Cocoarynth's company records needs.
Choose "direct" when: {ROUTE_CRITERIA["direct"]}
Choose "agentic" when: {ROUTE_CRITERIA["agentic"]}"""


async def route_question(question: str, activity: ActivityLog) -> str:
    started = time.perf_counter()
    if ROUTER == "decision":
        answers, llm_call = await ask_decision_model(
            {"question": question},
            {
                "route": {
                    "type": "choice",
                    "instructions": "How much retrieval does this question need?",
                    "criteria": ROUTE_CRITERIA,
                }
            },
        )
        agentic_probability = answers["route"]["probabilities"]["agentic"]
        route = "agentic" if agentic_probability > AGENTIC_PROBABILITY_THRESHOLD else "direct"
        activity.record(
            "complexity_routing", started, llm_call, route=route, agentic_probability=round(agentic_probability, 2)
        )
    else:
        result, llm_call = await ask_llm(ROUTER_INSTRUCTIONS, question, Route)
        route = result.route
        activity.record("complexity_routing", started, llm_call, route=route, reason=result.reason)
    return route


async def direct_retrieval(question: str, activity: ActivityLog) -> list[dict[str, Any]]:
    """Search once with the question as written, like the baseline, with no extra model calls."""
    started = time.perf_counter()
    results = await hybrid_search(question)
    activity.record("search", started, index=INDEX_NAME, query=question, result_count=len(results))
    return results


async def agentic_retrieval(question: str, activity: ActivityLog) -> list[dict[str, Any]]:
    """Plan, search, and reflect until the evidence is sufficient or the round limit is reached."""
    started = time.perf_counter()
    plan, llm_call = await ask_llm(PLANNER_INSTRUCTIONS, question, QueryPlan)
    activity.record("query_planning", started, llm_call, queries=plan.queries)

    queries = plan.queries
    searched: list[str] = []
    result_lists: list[list[dict[str, Any]]] = []
    for round_number in range(1, MAX_ROUNDS + 1):
        result_lists += await run_searches(queries, activity, round_number)
        searched += queries
        evidence = combine_results(result_lists)

        if round_number == MAX_ROUNDS:
            break
        reflection = await reflect(question, evidence, searched, activity, round_number)
        if reflection.sufficient or not reflection.follow_up_queries:
            break
        queries = reflection.follow_up_queries

    return evidence


@tool
async def search_knowledge(
    question: Annotated[str, Field(description="The user's full question, unchanged.")],
) -> dict[str, Any]:
    """Plan and run the searches needed to answer a question from Cocoarynth's company records."""
    activity = ActivityLog()

    route = await route_question(question, activity)
    if route == "direct":
        evidence = await direct_retrieval(question, activity)
    else:
        evidence = await agentic_retrieval(question, activity)
    return {"evidence": evidence, "activity": activity.steps}


async def main() -> None:
    agent = Agent(
        client=chat_client,
        name="diy-complexity-routing",
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
        await decision_client.aclose()
        await credential.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    logger.setLevel(logging.INFO)
    asyncio.run(main())
