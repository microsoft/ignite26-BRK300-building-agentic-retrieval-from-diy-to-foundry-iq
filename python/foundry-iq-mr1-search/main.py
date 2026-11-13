"""Agentic retrieval with Foundry IQ agentic search: the same agent as the DIY steps, but search_knowledge is one
search call with query_type="agentic" on one index, with no knowledge base.
"""

import asyncio
import json
import logging
import os
import time
from typing import Annotated, Any

from agent_framework import Agent, FunctionInvocationConfiguration, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework.openai import OpenAIChatOptions
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.core.credentials import AzureKeyCredential
from azure.identity.aio import AzureDeveloperCliCredential, ManagedIdentityCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import AgenticSearchOptions
from pydantic import Field

logger = logging.getLogger("foundry-iq-mr1-search")

PROJECT_ENDPOINT = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
MODEL_DEPLOYMENT_NAME = os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"]
# Set FOUNDRY_IQ_KB_ENDPOINT and FOUNDRY_IQ_KB_API_KEY to use another search service, such as one with
# Microsoft Retriever 1 enabled; without them the agent uses the project's own service.
SEARCH_ENDPOINT = os.environ.get("FOUNDRY_IQ_KB_ENDPOINT") or os.environ["FOUNDRY_IQ_SEARCH_ENDPOINT"]
SEARCH_API_KEY = os.environ.get("FOUNDRY_IQ_KB_API_KEY")
# The index of every Cocoarynth record, the same one the DIY agents search.
INDEX_NAME = os.environ.get("FOUNDRY_IQ_SEARCH_INDEX", "cocoarynth-all-index")

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
search_credential = AzureKeyCredential(SEARCH_API_KEY) if SEARCH_API_KEY else credential
search_client = SearchClient(SEARCH_ENDPOINT, INDEX_NAME, search_credential)


@tool
async def search_knowledge(
    question: Annotated[str, Field(description="The user's full question, unchanged.")],
) -> dict[str, Any]:
    """Retrieve evidence from Cocoarynth's company records with Foundry IQ agentic search."""
    sent_at = time.time()
    results = await search_client.search(
        search_text=question,
        # Agentic search: a retrieval model plans queries and reranks results within this one index.
        query_type="agentic",
        semantic_configuration_name="semantic-configuration",
        agentic_options=AgenticSearchOptions(max_runtime_in_seconds=30),
        # Return the subqueries, the stop reason and token usage, so we can show what happened.
        debug="agentic",
        top=10,
        select=["chunk_id", "title", "chunk"],
    )
    documents = [document async for document in results]
    received_at = time.time()
    debug_info = await results.get_debug_info()
    agentic = debug_info.agentic if debug_info else None
    evidence = [
        {
            "id": document["chunk_id"],
            "title": document["title"],
            "content": document["chunk"],
            "reranker_score": document["@search.reranker_score"],
        }
        for document in documents
    ]
    step = {
        "step": "agentic_search",
        "elapsed_ms": round((received_at - sent_at) * 1000),
        "started_at_ms": round(sent_at * 1000),
        "index": INDEX_NAME,
        "query": question,
        "result_count": len(documents),
        "sub_queries": [
            {"iteration": sub_query.iteration, "query": sub_query.search} for sub_query in (agentic.sub_queries or [])
        ]
        if agentic
        else [],
        "termination_reason": agentic.termination_reason if agentic else None,
        "planning_tokens": agentic.usage.agentic_planning_tokens if agentic and agentic.usage else None,
        "ranking_tokens": agentic.usage.reasoning_tokens if agentic and agentic.usage else None,
    }
    logger.info("%s", json.dumps(step))
    return {"evidence": evidence, "activity": [step]}


async def main() -> None:
    agent = Agent(
        client=chat_client,
        name="foundry-iq-mr1-search",
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
