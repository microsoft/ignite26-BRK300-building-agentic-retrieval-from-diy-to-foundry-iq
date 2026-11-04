"""DIY agentic retrieval, step 7: a classifier routes simple questions to direct retrieval."""

import asyncio
import json
import logging
import os
import string
import time
from typing import Annotated, Any, Literal, get_args

import httpx
from agent_framework import Agent, FunctionInvocationConfiguration, Message, tool
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
DECISION_DEPLOYMENT_NAME = os.environ["FOUNDRY_DECISION_DEPLOYMENT_NAME"]
# Microsoft-Decision-1 is called on the Foundry resource endpoint, not the project endpoint.
DECISION_URL = PROJECT_ENDPOINT.split("/api/projects/")[0] + "/providers/microsoft/v1/systemone"
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
# The direct route skips reranking, so it passes fewer chunks to keep lower-ranked noise out of the answer.
DIRECT_MAX_EVIDENCE = 5
MAX_ROUNDS = 3

# "decision" scores relevance with Microsoft-Decision-1; "chat" asks the chat model. Decision-1 measured
# about 3x faster and kept nearly the same chunks.
RERANKER: Literal["decision", "chat"] = "decision"
# Decision-1 returns a probability-weighted score from 0 to 3. A threshold of 1.5 best matched the chat
# model's "2 or higher" decisions: it kept 88% of the same chunks, and 93% of what it kept matched.
DECISION_MIN_RELEVANCE = 1.5
# "decision" routes with Microsoft-Decision-1; "chat" asks the chat model, which also writes a reason.
ROUTER: Literal["decision", "chat"] = "decision"
# Decision-1 gave one simple question p(agentic) = 0.62; every multi-part question scored at least 0.96.
AGENTIC_PROBABILITY_THRESHOLD = 0.8

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
Judge every result on its own content, regardless of which search returned it.
If still_missing is given, judge how well each result supplies that missing information."""


def short_labels(count: int) -> list[str]:
    """A, B, ..., Z, AA, AB, ...: short IDs the reranker can copy back quickly and accurately."""
    labels = []
    for index in range(1, count + 1):
        label = ""
        while index:
            index, remainder = divmod(index - 1, 26)
            label = string.ascii_uppercase[remainder] + label
        labels.append(label)
    return labels


RELEVANCE_LEVELS = [
    "Irrelevant to the question",
    "Related background only",
    "Partially answers the question",
    "Directly answers part of the question",
]


async def score_with_decision_model(
    question: str, labels: list[str], candidates: list[dict[str, Any]], missing: str | None
) -> tuple[dict[str, float], dict[str, Any]]:
    state: dict[str, Any] = {
        "question": question,
        "results": {
            label: {"title": candidate["title"], "content": candidate["content"]}
            for label, candidate in zip(labels, candidates)
        },
    }
    instructions = "How well does search result {label} help answer the question?"
    if missing:
        # Follow-up results target one gap. Scored against the whole question, they look only partly relevant.
        state["still_missing"] = missing
        instructions = "How well does search result {label} supply what is still missing from the answer?"
    questions = {
        f"relevance_{label}": {
            "type": "score",
            "instructions": instructions.format(label=label),
            "criteria": RELEVANCE_LEVELS,
        }
        for label in labels
    }
    answers, llm_call = await ask_decision_model(state, questions)
    return {label: answers[f"relevance_{label}"]["score"] for label in labels}, llm_call


async def score_with_chat_model(
    question: str, labels: list[str], candidates: list[dict[str, Any]], missing: str | None
) -> tuple[dict[str, float], dict[str, Any]]:
    prompt = json.dumps(
        {
            "question": question,
            "still_missing": missing,
            "results": [
                {"id": label, "title": candidate["title"], "content": candidate["content"]}
                for label, candidate in zip(labels, candidates)
            ],
        }
    )
    ranking, llm_call = await ask_llm(RERANKER_INSTRUCTIONS, prompt, Ranking, reasoning_effort="none")
    return {judgment.id: judgment.relevance for judgment in ranking.judgments}, llm_call


async def rerank(
    question: str,
    candidates: list[dict[str, Any]],
    activity: ActivityLog,
    round_number: int,
    missing: str | None = None,
) -> list[dict[str, Any]]:
    started = time.perf_counter()
    # Chunk IDs are long, and copying them back is slow output. Label candidates A, B, C... instead.
    labels = short_labels(len(candidates))
    if RERANKER == "decision":
        relevance, llm_call = await score_with_decision_model(question, labels, candidates, missing)
        min_relevance = DECISION_MIN_RELEVANCE
    else:
        relevance, llm_call = await score_with_chat_model(question, labels, candidates, missing)
        min_relevance = MIN_RELEVANCE
    ranked = sorted(
        (
            {**candidate, "relevance": relevance[label]}
            for label, candidate in zip(labels, candidates)
            if relevance.get(label, 0) >= min_relevance
        ),
        key=lambda candidate: candidate["relevance"],
        reverse=True,
    )[:MAX_EVIDENCE]
    activity.record(
        "reranking", started, llm_call, round=round_number, candidate_count=len(candidates), kept_count=len(ranked)
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


class Route(BaseModel):
    route: Literal["direct", "agentic"]
    reason: str = Field(description="One short sentence explaining the choice.")


ROUTER_INSTRUCTIONS = f"""You decide how much retrieval a question needs. The knowledge sources are:
{json.dumps(SOURCE_DESCRIPTIONS, indent=2)}
Choose "direct" when one search with the question as written is likely to find the whole answer.
Choose "agentic" when the question has several parts, spans several sources, or needs follow-up lookups."""


ROUTE_CRITERIA = {
    "direct": "One search with the question as written is likely to find the whole answer.",
    "agentic": "The question has several parts, spans several sources, or needs follow-up lookups.",
}


async def route_question(question: str, activity: ActivityLog) -> str:
    started = time.perf_counter()
    if ROUTER == "decision":
        answers, llm_call = await ask_decision_model(
            {"question": question, "knowledge_sources": SOURCE_DESCRIPTIONS},
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
    """Search every source once with the question as written, with no extra model calls."""
    direct_search = PlannedSearch(query=question, sources=list(get_args(SourceName)))
    result_lists = await run_searches([direct_search], activity, round_number=1)
    return combine_results(result_lists)[:DIRECT_MAX_EVIDENCE]


async def agentic_retrieval(question: str, activity: ActivityLog) -> list[dict[str, Any]]:
    """Plan, search, rerank, and reflect until the evidence is sufficient or the round limit is reached."""
    started = time.perf_counter()
    plan, llm_call = await ask_llm(PLANNER_INSTRUCTIONS, question, QueryPlan)
    activity.record("query_planning", started, llm_call, searches=describe_searches(plan.searches))

    planned_searches = plan.searches
    searched: list[str] = []
    evidence: list[dict[str, Any]] = []
    missing: str | None = None
    for round_number in range(1, MAX_ROUNDS + 1):
        result_lists = await run_searches(planned_searches, activity, round_number)
        searched += [planned.query for planned in planned_searches]

        known_ids = {item["id"] for item in evidence}
        new_candidates = [result for result in combine_results(result_lists) if result["id"] not in known_ids]
        if new_candidates:
            ranked = await rerank(question, new_candidates, activity, round_number, missing)
            # Follow-up results fill a gap the earlier evidence left, so they get up to half the slots
            # instead of competing on score with evidence for other parts of the question.
            follow_up = ranked[: MAX_EVIDENCE // 2] if evidence else ranked
            evidence = follow_up + evidence[: MAX_EVIDENCE - len(follow_up)]

        if round_number == MAX_ROUNDS:
            break
        reflection = await reflect(question, evidence, searched, activity, round_number)
        if reflection.sufficient or not reflection.follow_up_searches:
            break
        planned_searches = reflection.follow_up_searches
        missing = reflection.missing

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
        await decision_client.aclose()
        await credential.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    logger.setLevel(logging.INFO)
    asyncio.run(main())
