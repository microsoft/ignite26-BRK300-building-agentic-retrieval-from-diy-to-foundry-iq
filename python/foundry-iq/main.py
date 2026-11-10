"""Agentic retrieval with Foundry IQ: the same agent as the DIY steps, but search_knowledge is one knowledge base call.

This agent's knowledge base uses minimal reasoning effort with Microsoft Retriever 1 as its only model.
"""

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from agent_framework import Agent, FunctionInvocationConfiguration, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework.openai import OpenAIChatOptions
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity.aio import AzureDeveloperCliCredential, ManagedIdentityCredential
from pydantic import Field

logger = logging.getLogger("foundry-iq")

PROJECT_ENDPOINT = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
MODEL_DEPLOYMENT_NAME = os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"]
# Microsoft Retriever 1 (MR1) is only enabled on a preview search service for now. scripts.run_diy_agents
# sets the FOUNDRY_IQ_KB_* settings from .env.mr1; without them the agent uses the project's own service.
SEARCH_ENDPOINT = os.environ.get("FOUNDRY_IQ_KB_ENDPOINT") or os.environ["FOUNDRY_IQ_SEARCH_ENDPOINT"]
SEARCH_API_KEY = os.environ.get("FOUNDRY_IQ_KB_API_KEY")
# Minimal effort, models: [Microsoft Retriever 1], one index of every Cocoarynth record.
KNOWLEDGE_BASE = os.environ.get("FOUNDRY_IQ_KNOWLEDGE_BASE", "lab673-brk300-kb-all-minimal-mr1")
API_VERSION = os.environ.get("FOUNDRY_IQ_KB_API_VERSION", "2026-11-01-preview")
# Keep only chunks the semantic ranker scores as relevant (0-4 scale), so the retrieval model
# can tell when evidence is missing and search again.
RERANKER_THRESHOLD = 2.0

credential = (
    ManagedIdentityCredential()
    if "FOUNDRY_HOSTING_ENVIRONMENT" in os.environ
    else AzureDeveloperCliCredential(tenant_id=os.environ["AZURE_TENANT_ID"], process_timeout=60)
)
chat_client = FoundryChatClient(
    project_endpoint=PROJECT_ENDPOINT,
    model=MODEL_DEPLOYMENT_NAME,
    credential=credential,
    # Allow one search_knowledge call per question, so the agent can't add searches of its own.
    function_invocation_configuration=FunctionInvocationConfiguration(max_function_calls=1),
)
search_http = httpx.AsyncClient(base_url=SEARCH_ENDPOINT.rstrip("/"), params={"api-version": API_VERSION}, timeout=120)


async def search_headers() -> dict[str, str]:
    if SEARCH_API_KEY:
        return {"api-key": SEARCH_API_KEY}
    token = await credential.get_token("https://search.azure.com/.default")
    return {"Authorization": f"Bearer {token.token}"}


async def knowledge_sources() -> list[str]:
    response = await search_http.get(f"/knowledgebases('{KNOWLEDGE_BASE}')", headers=await search_headers())
    response.raise_for_status()
    return [source["name"] for source in response.json()["knowledgeSources"]]


def parse_time(value: str) -> float:
    # The service reports seven fractional digits; Python parses up to six.
    return datetime.fromisoformat(value[:26].rstrip("Z")).replace(tzinfo=UTC).timestamp()


def describe_activity(activity: list[dict[str, Any]], sent_at: float, received_at: float) -> list[dict[str, Any]]:
    """Convert the knowledge base's activity log to the steps the DIY agents report."""
    timed = [entry for entry in activity if entry.get("startedAt") and entry.get("completedAt")]
    if not timed:
        return []
    first, last = min(parse_time(e["startedAt"]) for e in timed), max(parse_time(e["completedAt"]) for e in timed)
    # Place the service's timeline inside the request, splitting the network time before and after it.
    offset = sent_at + ((received_at - sent_at) - (last - first)) / 2 - first
    steps = []
    for i, entry in enumerate(activity):
        started = parse_time(entry["startedAt"]) if entry.get("startedAt") else None
        timing = {}
        if started is not None and entry.get("completedAt"):
            timing = {
                "elapsed_ms": round((parse_time(entry["completedAt"]) - started) * 1000),
                "started_at_ms": round((started + offset) * 1000),
            }
        if entry["type"] == "searchIndex":
            steps.append(
                {
                    "step": "search",
                    **timing,
                    "index": entry.get("knowledgeSourceName"),
                    "query": (entry.get("searchIndexArguments") or {}).get("search"),
                    "result_count": entry.get("count"),
                }
            )
        elif entry["type"] == "modelQueryPlanning":
            searches_again = any(later["type"] == "searchIndex" for later in activity[i + 1 :])
            steps.append(
                {
                    "step": "retrieval_decision",
                    **timing,
                    "model": (entry.get("model") or {}).get("modelName"),
                    "decision": "search again" if searches_again else "finish",
                    "input_tokens": entry.get("inputTokens"),
                    "output_tokens": entry.get("outputTokens"),
                }
            )
        elif entry["type"] == "agenticReasoning":
            steps.append(
                {
                    "step": "semantic_ranking",
                    "ranking_tokens": entry.get("reasoningTokens"),
                    "reasoning_effort": (entry.get("retrievalReasoningEffort") or {}).get("kind"),
                }
            )
    for step in steps:
        logger.info("%s", json.dumps(step))
    return steps


@tool
async def search_knowledge(
    question: Annotated[str, Field(description="The user's full question, unchanged.")],
) -> dict[str, Any]:
    """Retrieve evidence from Cocoarynth's company records with a Foundry IQ knowledge base."""
    body = {
        "intents": [{"type": "semantic", "search": question}],
        "includeActivity": True,
        "outputMode": "extractiveData",
        "knowledgeSourceParams": [
            {
                "knowledgeSourceName": name,
                "kind": "searchIndex",
                "includeReferences": True,
                "rerankerThreshold": RERANKER_THRESHOLD,
            }
            for name in await knowledge_sources()
        ],
    }
    sent_at = time.time()
    response = await search_http.post(
        f"/knowledgebases('{KNOWLEDGE_BASE}')/retrieve", json=body, headers=await search_headers()
    )
    received_at = time.time()
    response.raise_for_status()
    result = response.json()
    extracted = json.loads(result["response"][0]["content"][0]["text"]) if result.get("response") else []
    scores = {str(ref["id"]): ref.get("rerankerScore") for ref in result.get("references", [])}
    evidence = [
        {
            "id": str(item["ref_id"]),
            "title": item["title"],
            "content": item["content"],
            "reranker_score": scores.get(str(item["ref_id"])),
        }
        for item in extracted
    ]
    return {"evidence": evidence, "activity": describe_activity(result.get("activity", []), sent_at, received_at)}


async def main() -> None:
    agent = Agent(
        client=chat_client,
        name="foundry-iq",
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
        await search_http.aclose()
        await credential.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    logger.setLevel(logging.INFO)
    asyncio.run(main())
