import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx2


@dataclass(frozen=True)
class Example:
    label: str
    question: str


@dataclass(frozen=True)
class DiyStep:
    name: str
    title: str
    capability: str
    port: int
    examples: tuple[Example, ...]


TASTING_PANEL = Example(
    "Succeeds: tasting panel winner",
    "Which origin won Cocoarynth's September 2026 single-origin bar tasting panel, and what were its flavor notes?",
)
MADAGASCAR_PANEL = "What did the tasting panel think of the Madagascar bar, and why was its bean lot held on arrival?"
GHANA_BARS = (
    "Why did the Ghana bars sold at Larkspur Market turn grey and dusty-looking in July 2026, "
    "and was the batch itself bad?"
)
PRICIEST_BEANS = (
    "Which origin's beans cost Cocoarynth the most in 2026, and was it worth paying for them, "
    "judging by taste and sales?"
)
MADAGASCAR_LAUNCH = (
    "Why did the Madagascar limited-edition bar with cacao nibs launch on 1 October instead of 1 September, "
    "and what happened at the estate to start the delay?"
)
MULTI_SOURCE_EXAMPLES = (
    Example("Multi-source: grey Ghana bars", GHANA_BARS),
    Example("Multi-source: priciest beans", PRICIEST_BEANS),
)

DIY_STEPS = (
    DiyStep(
        "diy-baseline",
        "Baseline",
        "One hybrid search with semantic ranking over the quality index.",
        8091,
        (TASTING_PANEL, Example("Fails: Madagascar panel and hold", MADAGASCAR_PANEL)),
    ),
    DiyStep(
        "diy-query-decomposition",
        "Query decomposition",
        "An LLM splits the question into focused queries that run in parallel.",
        8092,
        (
            Example("Succeeds: Madagascar panel and hold", MADAGASCAR_PANEL),
            Example("Fails: grey Ghana bars", GHANA_BARS),
        ),
    ),
    DiyStep(
        "diy-multi-source-fanout",
        "Multi-source fan-out",
        "Every query runs against every index.",
        8093,
        MULTI_SOURCE_EXAMPLES,
    ),
    DiyStep(
        "diy-source-selection",
        "Source selection",
        "The planner sends each query only to the indexes likely to answer it.",
        8094,
        MULTI_SOURCE_EXAMPLES,
    ),
    DiyStep(
        "diy-llm-reranking",
        "LLM reranking",
        "A ranking model (Microsoft-Decision-1) scores the merged results and keeps the most relevant evidence.",
        8095,
        MULTI_SOURCE_EXAMPLES,
    ),
    DiyStep(
        "diy-reflection",
        "Reflection",
        "An LLM checks the evidence and plans follow-up searches until it is sufficient.",
        8096,
        (
            Example("Needs reflection: late Madagascar launch", MADAGASCAR_LAUNCH),
            Example(
                "One round: organic seal",
                "Can Cocoarynth print the organic seal on the Peru Bagua 70% bars packed in September 2026?",
            ),
        ),
    ),
    DiyStep(
        "diy-complexity-classifier",
        "Complexity classifier",
        "A classifier (Microsoft-Decision-1) routes simple questions to one direct search and complex ones to the agentic path.",
        8097,
        (
            Example("Simple: storage temperature", "What is the recommended storage temperature for chocolate bars?"),
            Example("Complex: late Madagascar launch", MADAGASCAR_LAUNCH),
        ),
    ),
)
DIY_STEPS_BY_NAME = {step.name: step for step in DIY_STEPS}
AGENT_TIMEOUT_SECONDS = 240
# Blob containers holding the PDFs that the DIY agents can cite, one per search index.
DOCUMENT_CONTAINERS = (
    "cocoarynth-quality",
    "cocoarynth-origins",
    "cocoarynth-logistics-retail",
    "cocoarynth-policies",
    "knowledge",
    "engineering-practices",
)


class DiyAgentUnavailableError(Exception):
    def __init__(self, step: DiyStep) -> None:
        super().__init__(f"{step.name} is not running on port {step.port}.")
        self.step = step


def agent_url(step: DiyStep) -> str:
    return f"{os.getenv('DIY_AGENT_HOST', 'http://127.0.0.1').rstrip('/')}:{step.port}"


async def is_running(client: httpx2.AsyncClient, step: DiyStep) -> bool:
    try:
        response = await client.get(f"{agent_url(step)}/readiness", timeout=1.5)
    except httpx2.HTTPError:
        return False
    return response.status_code == 200


async def list_steps() -> list[dict[str, Any]]:
    async with httpx2.AsyncClient() as client:
        running = await asyncio.gather(*(is_running(client, step) for step in DIY_STEPS))
    return [{**asdict(step), "running": is_up} for step, is_up in zip(DIY_STEPS, running)]


def _parse_json(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def summarize_response(response: dict[str, Any]) -> dict[str, Any]:
    """Pair each tool call with its output, and pull out the final answer and the agent's own token usage."""
    calls: dict[str, dict[str, Any]] = {}
    answer_parts: list[str] = []
    for item in response.get("output", []):
        item_type = item.get("type")
        if item_type == "function_call":
            calls[item["call_id"]] = {
                "name": item.get("name"),
                "arguments": _parse_json(item.get("arguments")),
                "activity": [],
                "evidence": [],
            }
        elif item_type == "function_call_output":
            call = calls.setdefault(
                item["call_id"], {"name": None, "arguments": None, "activity": [], "evidence": []}
            )
            output = _parse_json(item.get("output"))
            if isinstance(output, dict):
                call["activity"] = output.get("activity", [])
                call["evidence"] = output.get("evidence", [])
            else:
                call["error"] = str(output)
        elif item_type == "message":
            answer_parts += [
                part.get("text", "") for part in item.get("content", []) if part.get("type") == "output_text"
            ]
    usage = response.get("usage") or {}
    return {
        "answer": "\n\n".join(part for part in answer_parts if part),
        "toolCalls": list(calls.values()),
        "agentUsage": {
            "inputTokens": usage.get("input_tokens", 0),
            "outputTokens": usage.get("output_tokens", 0),
        },
    }


async def ask(step: DiyStep, question: str) -> dict[str, Any]:
    started_at_ms = round(time.time() * 1000)
    started = time.perf_counter()
    async with httpx2.AsyncClient(timeout=AGENT_TIMEOUT_SECONDS) as client:
        try:
            response = await client.post(f"{agent_url(step)}/responses", json={"input": question})
        except httpx2.ConnectError as error:
            raise DiyAgentUnavailableError(step) from error
    response.raise_for_status()
    return {
        "step": step.name,
        "startedAtMs": started_at_ms,
        "elapsedMs": round((time.perf_counter() - started) * 1000),
        **summarize_response(response.json()),
    }
