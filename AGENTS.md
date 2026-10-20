# Instructions for coding agents

Building agentic retrieval: From DIY to Foundry IQ
Why does agentic retrieval matter, and what does it take to build it yourself? In this session, we will first code an agent loop that decomposes a question, runs parallel searches, inspects evidence, and decides whether to search again. Then we'll see how Foundry IQ can replace that plumbing with a managed agentic knowledge base. Learn how we made the managed service cheaper, faster, and better, thanks to serverless execution, compression, fine-tuned task-specific models, and built-in ranking.

[outline.md](outline.md) is the source of truth for the session structure, demo architecture, and DIY capability progression. Follow it when building demos, slides, or portal features.

## Development Tips

### Start the portal locally

Use a provisioned Azure Developer CLI environment. The application loads the selected `azd` environment through `dotenv-azd`; do not assume a local `.env` file exists.

```shell
azd auth login
azd env select
.venv/bin/python -m uvicorn app.backend.main:app --reload --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. Existing shell environment variables override values loaded from `azd`.

For a clean restart, stop every listener on the development port before starting Uvicorn again:

```shell
for server_pid in $(lsof -tiTCP:8000 -sTCP:LISTEN); do kill "$server_pid"; done
.venv/bin/python -m uvicorn app.backend.main:app --reload --host 127.0.0.1 --port 8000
```

Use the actual port in both commands when working on another port, such as `49153`. With `--reload`, Python edits restart the worker automatically. Refresh the browser after static HTML, CSS, or JavaScript edits. When changing a referenced static asset, increment its query-string version in `app/backend/static/index.html` to prevent stale browser assets.

Useful checks:

```shell
curl --fail-with-body http://127.0.0.1:8000/health
curl --fail-with-body http://127.0.0.1:8000/api/knowledge-bases
```

### DIY retrieval agents

Each DIY step is a Microsoft Agent Framework hosted agent in its own folder under `python/`, registered as a `host: azure.ai.agent` service in `azure.yaml`:

`diy-baseline` -> `diy-query-decomposition` -> `diy-multi-source-fanout` -> `diy-source-selection` -> `diy-llm-reranking` -> `diy-reflection` -> `diy-complexity-classifier`

Each `main.py` is self-contained and cumulative: it repeats all the code from the previous step and adds one capability, so consecutive steps can be diffed on stage. Shared code (credentials, `ActivityLog`, `ask_llm`, `hybrid_search`, `combine_results`, agent instructions, `pyproject.toml`, `.agentignore`) is intentionally duplicated; `ActivityLog` starts in step 2, since the baseline records its single step inline. When you change shared code, apply the same change to every later step that contains it, then run `uv lock` in each folder whose `pyproject.toml` changed.

Run a step locally with the Foundry agent CLI. `azd ai agent run` expects the virtual environment in the step folder:

```shell
cd python/diy-baseline
uv venv && uv pip install uv
source .venv/bin/activate
azd ai agent run diy-baseline --no-client
```

`azd ai agent run` keeps running, so invoke the agent from a second terminal. The local server always listens on port 8088, so stop one step before starting the next. `azd ai agent invoke` reuses the agent's previous conversation by default, so the commands pass `--new-conversation`; without it, a repeated question can be answered from history without searching. Each step has a question that shows the capability it adds (M1–M7 are the mysteries in [data/cocoarynth/questions.yaml](data/cocoarynth/questions.yaml)):

```shell
# diy-baseline: one search is enough for a single-fact question (M6).
azd ai agent invoke diy-baseline --local --new-conversation "Which origin won Cocoarynth's September 2026 tasting panel, and what did the tasters notice about it?"

# diy-query-decomposition: two information needs in one index, searched in parallel. The baseline usually
# answers the tasting part from a small pre-release check and misses the September 2026 tasting panel.
azd ai agent invoke diy-query-decomposition --local --new-conversation "What did tasters think of the Madagascar beans, and why was the lot held on arrival?"

# diy-multi-source-fanout, diy-source-selection, diy-llm-reranking: evidence spread across indexes (M1).
azd ai agent invoke diy-multi-source-fanout --local --new-conversation "Why did the Ghana bars sold at Larkspur Market turn grey and dusty-looking in July 2026, and was the batch itself bad?"
azd ai agent invoke diy-source-selection --local --new-conversation "Why did the Ghana bars sold at Larkspur Market turn grey and dusty-looking in July 2026, and was the batch itself bad?"
azd ai agent invoke diy-llm-reranking --local --new-conversation "Why did the Ghana bars sold at Larkspur Market turn grey and dusty-looking in July 2026, and was the batch itself bad?"

# diy-reflection: round 1 finds the held lot MG26-L02, and round 2 uses that lot code to find what happened
# at the estate (M5). diy-llm-reranking answers the same question but says the estate cause is missing.
azd ai agent invoke diy-reflection --local --new-conversation "Why did the Madagascar limited-edition bar with cacao nibs launch on 1 October instead of 1 September, and what happened at the estate to start the delay?"

# diy-complexity-classifier: a simple question takes the direct route, M5 takes the agentic route.
azd ai agent invoke diy-complexity-classifier --local --new-conversation "What is the recommended storage temperature for chocolate bars?"
azd ai agent invoke diy-complexity-classifier --local --new-conversation "Why did the Madagascar limited-edition bar with cacao nibs launch on 1 October instead of 1 September, and what happened at the estate to start the delay?"
```

Each step logs its activity to the `azd ai agent run` terminal as `INFO:<step-name>:{...}` lines, mixed in with telemetry output.

#### Ask the agents from the portal

The portal's **DIY agentic retrieval** page (`/diy-agents`) asks any step a question and shows its answer, activity log (each tool call, planned and parallel searches by source, reranking, reflection rounds, routing, with timings and token usage), and the evidence it returned. Start all seven agents with one command from the repository root, then start the portal as described above and open <http://127.0.0.1:8000/diy-agents>:

```shell
.venv/bin/python -m scripts.run_diy_agents
```

Each step listens on its own port (8091–8097), so all seven can run at once. Pass step names to start only some of them, for example `.venv/bin/python -m scripts.run_diy_agents diy-baseline diy-reflection`. The script creates a missing step `.venv`, prints only each agent's activity log, startup, and error lines, and stops every agent on Ctrl+C. The step list, ports, and example questions live in `app/backend/diy_agents.py`; add new steps there too. Each step has a few examples with short labels such as "Succeeds: tasting panel winner" or "Fails: grey Ghana bars", which the page offers in a dropdown. The page only reaches agents running on the same machine as the portal, so it doesn't work in the deployed portal.

Lint all steps with `uvx ruff check python`.

Open `brk300.code-workspace` in VS Code (**File > Open Workspace from File**). Each step folder is its own workspace folder, and each step's `pyproject.toml` sets `[tool.ty.environment] python = "./.venv"`, so ty resolves imports from that folder's `.venv` instead of whatever interpreter VS Code selected. When adding a step, copy that setting, and add its folder to `brk300.code-workspace` and to `python-envs.pythonProjects` in `.vscode/settings.json`.

### Tests

Install development and browser dependencies once:

```shell
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
```

Run fast backend tests while developing:

```shell
.venv/bin/python -m pytest tests/test_*.py -q
```

Run deterministic portal E2E tests:

```shell
.venv/bin/python -m pytest tests/e2e.py -q
```

These tests start an isolated Uvicorn server and mock Azure-dependent browser APIs. They do not require provisioned Azure resources. During iteration, use `-k` to target a workflow:

```shell
.venv/bin/python -m pytest tests/e2e.py -q -k 'part3_portal_workflow or part4_portal_workflow'
```

Run the same browser suite against real services from the selected provisioned `azd` environment:

```shell
.venv/bin/python -m pytest tests/e2e.py --live-smoke -v
```

Run model-judged portal acceptance against a deployed revision:

```shell
.venv/bin/python -m pytest tests/live_smoke.py --smoke-base-url "https://<app-hostname>" -v
```

### Test selection

- Start with the narrowest relevant unit test or a focused `tests/e2e.py -k` expression.
- Run all deterministic E2E tests before finishing portal changes.
- Use `--live-smoke` only to verify integration with provisioned Azure resources.
- Use `tests/live_smoke.py` after deployment to verify the deployed portal and retrieval quality.
