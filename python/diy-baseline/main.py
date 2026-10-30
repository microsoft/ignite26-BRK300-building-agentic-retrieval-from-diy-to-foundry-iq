"""DIY agentic retrieval, step 1: one hybrid search with semantic ranking."""

import asyncio
import json
import logging
import os
import time
from typing import Annotated, Any

from agent_framework import Agent, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework.openai import OpenAIChatOptions
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity.aio import AzureDeveloperCliCredential, ManagedIdentityCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import QueryType, VectorizableTextQuery
from pydantic import Field

logger = logging.getLogger("diy-baseline")

PROJECT_ENDPOINT = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
MODEL_DEPLOYMENT_NAME = os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"]
SEARCH_ENDPOINT = os.environ["FOUNDRY_IQ_SEARCH_ENDPOINT"]
INDEX_NAME = "cocoarynth-quality-index"

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
)


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


@tool
async def search_knowledge(
    query: Annotated[str, Field(description="A search query for Cocoarynth's quality records.")],
) -> dict[str, Any]:
    """Search Cocoarynth's quality records with hybrid search and semantic ranking."""
    started = time.perf_counter()
    results = await hybrid_search(query)
    elapsed = time.perf_counter() - started
    activity = {
        "step": "search",
        "elapsed_ms": round(elapsed * 1000),
        "started_at_ms": round((time.time() - elapsed) * 1000),
        "index": INDEX_NAME,
        "query": query,
        "result_count": len(results),
    }
    logger.info("%s", json.dumps(activity))
    return {"evidence": results, "activity": [activity]}


async def main() -> None:
    agent = Agent(
        client=chat_client,
        name="diy-baseline",
        instructions="""You answer questions about Cocoarynth Chocolate Works, a bean-to-bar chocolate maker.
        Always call search_knowledge before answering, and answer only from the evidence it returns.
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
