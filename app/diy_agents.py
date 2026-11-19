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
MADAGASCAR_ESTATE = "What happened at the estate that delayed the launch of the Madagascar limited-edition bar with cacao nibs?"
STORAGE_TEMPERATURE = "What is the recommended storage temperature for chocolate bars?"
# Agentic search issues a follow-up query for this one: the question never names the estate.
MOISTURE_HARVEST = "Who harvested the beans in the lot that was held for moisture, and what was the weather at that farm during harvest?"

# The steps the portal and scripts.run_diy_agents use.
DIY_STEPS = (
    DiyStep(
        "diy-baseline",
        "Baseline",
        "One hybrid search with semantic ranking over one index of Cocoarynth's records.",
        8091,
        (TASTING_PANEL, Example("Fails: Madagascar panel and hold", MADAGASCAR_PANEL)),
    ),
    DiyStep(
        "diy-query-planning",
        "Query planning",
        "An LLM splits the question into focused queries that run in parallel.",
        8092,
        (
            Example("Succeeds: Madagascar panel and hold", MADAGASCAR_PANEL),
            Example("Fails: Madagascar estate delay", MADAGASCAR_ESTATE),
        ),
    ),
    DiyStep(
        "diy-reflection",
        "Reflection",
        "An LLM checks the evidence and plans follow-up searches until it is sufficient.",
        8093,
        (Example("Succeeds: Madagascar estate delay", MADAGASCAR_ESTATE),),
    ),
    DiyStep(
        "diy-complexity-routing",
        "Complexity classifier",
        "A classifier (Microsoft-Decision-1) sends simple questions to one direct search, and the rest to reflection.",
        8094,
        (
            Example("Simple: storage temperature", STORAGE_TEMPERATURE),
            Example("Complex: Madagascar estate delay", MADAGASCAR_ESTATE),
        ),
    ),
    DiyStep(
        "foundry-iq-mr1-kb",
        "Foundry IQ: minimal + MR1",
        "One call to a Foundry IQ knowledge base with minimal effort: Microsoft Retriever 1 reviews each search and decides whether to search again.",
        8095,
        (Example("Trail: Madagascar estate delay", MADAGASCAR_ESTATE),),
    ),
    DiyStep(
        "foundry-iq-auto",
        "Foundry IQ: auto",
        "One call to a Foundry IQ knowledge base with auto effort, which decides how much retrieval each question needs.",
        8096,
        (
            Example("First search: storage temperature", STORAGE_TEMPERATURE),
            Example("MR1: Madagascar estate delay", MADAGASCAR_ESTATE),
            Example("LLM planner: tasting panel and bean hold", MADAGASCAR_PANEL),
        ),
    ),
    DiyStep(
        "foundry-iq-mr1-search",
        "Foundry IQ: agentic search",
        "One search call with query_type=\"agentic\" on one index, with no knowledge base: a retrieval model plans queries and reranks results.",
        8097,
        (
            Example("Trail: Madagascar estate delay", MADAGASCAR_ESTATE),
            Example("Follow-up query: moisture hold harvest", MOISTURE_HARVEST),
            Example("Simple: storage temperature", STORAGE_TEMPERATURE),
        ),
    ),
)
DIY_STEPS_BY_NAME = {step.name: step for step in DIY_STEPS}


@dataclass(frozen=True)
class Demo:
    slide: str
    step: str
    label: str
    question: str


def _demo(slide: str, step: str, question: str) -> Demo:
    example = next(example for example in DIY_STEPS_BY_NAME[step].examples if example.question == question)
    return Demo(slide, step, example.label, question)


# The talk's demos, in slide order. The slide titles match the deck's demo slides.
DEMOS = (
    _demo("Baseline approach", "diy-baseline", TASTING_PANEL.question),
    _demo("Baseline approach with compound question", "diy-baseline", MADAGASCAR_PANEL),
    _demo("Query planning approach", "diy-query-planning", MADAGASCAR_PANEL),
    _demo("Query planning approach with multi-hop question", "diy-query-planning", MADAGASCAR_ESTATE),
    _demo("Reflection approach", "diy-reflection", MADAGASCAR_ESTATE),
    _demo("Complexity classifier approach", "diy-complexity-routing", STORAGE_TEMPERATURE),
    _demo("Complexity classifier approach", "diy-complexity-routing", MADAGASCAR_ESTATE),
    _demo("Foundry IQ knowledge base", "foundry-iq-mr1-kb", MADAGASCAR_ESTATE),
    _demo("Foundry IQ agentic search (query_type=agentic)", "foundry-iq-mr1-search", MADAGASCAR_ESTATE),
    _demo("Foundry IQ agentic search with a follow-up query", "foundry-iq-mr1-search", MOISTURE_HARVEST),
    _demo("Foundry IQ Auto: simple question", "foundry-iq-auto", STORAGE_TEMPERATURE),
    _demo("Foundry IQ Auto: multi-hop question", "foundry-iq-auto", MADAGASCAR_ESTATE),
    _demo("Foundry IQ Auto: compound question", "foundry-iq-auto", MADAGASCAR_PANEL),
)
AGENT_TIMEOUT_SECONDS = 240


class DiyAgentFailedError(Exception):
    """The agent answered the request, but reported that it failed to produce a response."""

    def __init__(self, step: DiyStep, message: str) -> None:
        super().__init__(f"{step.name} failed: {message}")
        self.step = step


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
        # The agent server sometimes repeats a function call's arguments, as '{...}{...}', so read the first value.
        value, _ = json.JSONDecoder().raw_decode(text.strip())
    except json.JSONDecodeError:
        return text
    return value


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
    payload = response.json()
    if payload.get("status") == "failed":
        raise DiyAgentFailedError(step, (payload.get("error") or {}).get("message") or "no error message")
    summary = summarize_response(payload)
    # Agent Framework hands a failed tool call back to the model, which answers anyway; treat it as a failure.
    for call in summary["toolCalls"]:
        if "error" in call:
            raise DiyAgentFailedError(step, f"{call['name']} raised an exception ({call['error']}). See the agent's log.")
    return {
        "step": step.name,
        "startedAtMs": started_at_ms,
        "elapsedMs": round((time.perf_counter() - started) * 1000),
        **summary,
    }
