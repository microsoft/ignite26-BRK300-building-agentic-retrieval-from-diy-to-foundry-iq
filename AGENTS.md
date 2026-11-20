# Instructions for coding agents

This repo supports this Ignite 2026 session:

Title:
Building agentic retrieval: From DIY to Foundry IQ

Abstract:
Why does agentic retrieval matter, and what does it take to build it yourself? In this session, we will first code an agent loop that decomposes a question, runs parallel searches, inspects evidence, and decides whether to search again. Then we'll see how Foundry IQ can replace that plumbing with a managed agentic knowledge base. Learn how we made the managed service cheaper, faster, and better, thanks to serverless execution, compression, fine-tuned task-specific models, and built-in ranking.

## Naming

Say "Foundry IQ", never "Azure AI Search", in docs and UI text. Code identifiers that come from SDKs or Azure resource types, such as `SearchClient`, `azure-search-documents`, or `Microsoft.Search/searchServices`, keep their names.

## Development Tips

### Start the portal locally

Use a provisioned Azure Developer CLI environment. The application loads the selected `azd` environment through `dotenv-azd`; do not assume a local `.env` file exists.

```shell
azd auth login
azd env select
.venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. Existing shell environment variables override values loaded from `azd`.

The local portal and agents don't need the deployed `web` Container App. To provision only what they need, skipping the app, its user-assigned managed identity, and its role assignments, run `azd env set DEPLOY_WEB_APP false` before `azd provision`.

For a clean restart, stop every listener on the development port before starting Uvicorn again:

```shell
for server_pid in $(lsof -tiTCP:8000 -sTCP:LISTEN); do kill "$server_pid"; done
.venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Use the actual port in both commands when working on another port, such as `49153`. With `--reload`, Python edits restart the worker automatically. Refresh the browser after static HTML, CSS, or JavaScript edits. When changing a referenced static asset, increment its query-string version in `app/static/index.html` to prevent stale browser assets.

The portal covers one knowledge base, `cocoarynth-kb-all`, and its one knowledge source, `cocoarynth-all-source`, over the index the DIY agents search, `cocoarynth-all-index`. `KNOWLEDGE_SOURCE` in `app/naming.py` is the single definition of the source's display name, description, index, Blob container, and knowledge source name; the backend routes, `scripts/preingest.py`, and the DIY document proxy all derive from it. The areas are:

- **Knowledge source** (`/source/documents`, `/source/chunks`, `/source/search`): browse the PDFs, view a document's chunks, or search the index. The matching APIs are `GET /api/source`, `GET /api/source/documents`, `GET /api/source/documents/content/{blob}`, `GET /api/source/chunks?document=...`, and `POST /api/source/search`.
- **Knowledge base** (`/knowledge-base/configuration`, `/knowledge-base/query`): the live knowledge base definition (`GET /api/knowledge-base`) and a query page that retrieves with the activity log (`POST /api/retrieve`).
- **DIY agentic retrieval** (`/diy-agents`) and **Demos** (`/demos`), described below.

`scripts/preingest.py` creates the knowledge source and the knowledge base with `FoundryIqService.create_knowledge_base`.

Useful checks:

```shell
curl --fail-with-body http://127.0.0.1:8000/health
curl --fail-with-body http://127.0.0.1:8000/api/sources
curl --fail-with-body http://127.0.0.1:8000/api/knowledge-base
```

### DIY retrieval agents

Each DIY step is a Microsoft Agent Framework hosted agent in its own folder under `python/`, registered as a `host: azure.ai.agent` service in `azure.yaml`:

`diy-baseline` -> `diy-query-planning` -> `diy-reflection` -> `diy-complexity-routing`

They search one index, `cocoarynth-all-index`, which holds every Cocoarynth company record. `scripts/preingest.py` uploads all 55 Cocoarynth PDFs to one Blob container, `cocoarynth`, and ingests them into that index.

Each `main.py` is self-contained and cumulative: it repeats all the code from the previous step and adds one capability, so you can diff consecutive steps. Shared code (credentials, `ActivityLog`, `ask_llm`, `hybrid_search`, `combine_results`, agent instructions, `pyproject.toml`, `.agentignore`) is intentionally duplicated; `ActivityLog` starts in step 2, since the baseline records its single step inline. When you change shared code, apply the same change to every later step that contains it, then run `uv lock` in each folder whose `pyproject.toml` changed.

Run a step locally with the Foundry agent CLI. `azd ai agent run` expects the virtual environment in the step folder:

```shell
cd python/diy-baseline
uv venv && uv pip install uv
source .venv/bin/activate
TZ=UTC azd ai agent run diy-baseline --no-client
```

`TZ=UTC` works around an `azure-identity` bug: `AzureDeveloperCliCredential` reads azd's UTC token expiry as local time, so outside UTC an agent keeps using its expired token and gets 401 errors about an hour after it starts. `scripts.run_diy_agents` and the local portal set it for you.

`azd ai agent run` keeps running, so invoke the agent from a second terminal. The local server always listens on port 8088, so stop one step before starting the next. `azd ai agent invoke` reuses the agent's previous conversation by default, so the commands pass `--new-conversation`; without it, a repeated question can be answered from history without searching. Each step has a question that shows the capability it adds (M1–M7 are the mysteries in [data/cocoarynth/questions.yaml](data/cocoarynth/questions.yaml)):

```shell
# diy-baseline: one search is enough for a single-fact question (M6).
azd ai agent invoke diy-baseline --local --new-conversation "Which origin won Cocoarynth's September 2026 single-origin bar tasting panel, and what were its flavor notes?"

# diy-query-planning: two information needs in one index, searched in parallel. The baseline's one search
# returns only hold records, so it misses the September 2026 tasting panel's notes on the bar.
azd ai agent invoke diy-query-planning --local --new-conversation "What did the tasting panel think of the Madagascar bar, and why was its bean lot held on arrival?"

# diy-reflection: round 1 finds the hold notice for lot MG26-L02 from Tsaravola Estate, and follow-up rounds use
# those names to find the estate's harvest report (M5). diy-query-planning says the estate cause is missing.
azd ai agent invoke diy-reflection --local --new-conversation "What happened at the estate that delayed the launch of the Madagascar limited-edition bar with cacao nibs?"

# diy-complexity-routing: a simple question takes the direct route (one search), M5 takes the agentic route.
azd ai agent invoke diy-complexity-routing --local --new-conversation "What is the recommended storage temperature for chocolate bars?"
azd ai agent invoke diy-complexity-routing --local --new-conversation "What happened at the estate that delayed the launch of the Madagascar limited-edition bar with cacao nibs?"
```

Each step logs its activity to the `azd ai agent run` terminal as `INFO:<step-name>:{...}` lines, mixed in with telemetry output.

#### Ask the agents from the portal

The portal's **DIY agentic retrieval** page (`/diy-agents`) asks any step a question and shows its answer; the retrieval process, with retrieval time and token metrics, a latency waterfall (each retrieval step, from the first to the last, placed by the start times the agents record; it leaves out the agent's own model calls before and after retrieval, and shows a gap between steps as the agent deciding to search again), and each step (planned and parallel searches by index, reranking, reflection rounds, routing, with timings and token usage); and the returned results. If a tool call fails, the page shows the error instead of the agent's answer. Start all seven agents (the four DIY steps and the three Foundry IQ agents) with one command from the repository root, then start the portal as described above and open <http://127.0.0.1:8000/diy-agents>:

```shell
.venv/bin/python -m scripts.run_diy_agents
```

Each step listens on its own port (8091–8097), so all seven can run at once. Pass step names to start only some of them, for example `.venv/bin/python -m scripts.run_diy_agents diy-baseline diy-reflection`. The script creates a missing step `.venv`, prints only each agent's activity log, startup, and error lines, and stops every agent on Ctrl+C. The step list, ports, and example questions live in `app/diy_agents.py`; add new steps there too. Each step has one or two examples with short labels such as "Succeeds: tasting panel winner" or "Fails: Madagascar estate delay".

The page has an agent dropdown, the question, and **Ask agent**. Its URL tracks the selected agent and question as `?agent=<step-name>&question=<text>`. The **Demos** page (`/demos`) links to each step's example questions. The DIY page only reaches agents running on the same machine as the portal, so it doesn't work in the deployed portal.

The Foundry IQ agents are the same agent as the DIY steps, but `search_knowledge` makes one call to Foundry IQ:

- `python/foundry-iq-mr1-kb` (port 8095) makes a retrieve call to a knowledge base with minimal reasoning effort and Microsoft Retriever 1, and reports the knowledge base's activity log as its own steps.
- `python/foundry-iq-auto` (port 8096) makes a retrieve call to a knowledge base with auto reasoning effort, Microsoft Retriever 1, and an Azure OpenAI model.
- `python/foundry-iq-mr1-search` (port 8097) skips the knowledge base: `search_knowledge` makes one `SearchClient.search` call with `query_type="agentic"` on an index (`FOUNDRY_IQ_SEARCH_INDEX`), and reports the subqueries, stop reason, and token usage from `debug="agentic"`.

By default, the knowledge base agents call `cocoarynth-kb-all` and the search agent queries `cocoarynth-all-index`, on the project's search service. Microsoft Retriever 1 and agentic search need a search service in a supported region with paid Knowledge Retrieval, plus `azure-search-documents` 12.2.0b1 or later. The infrastructure turns on Knowledge Retrieval and, by default, creates a Serverless search service (`AZURE_SEARCH_SKU`) in West Central US (`AZURE_SEARCH_LOCATION`), while the models stay in `AZURE_LOCATION`. Serverless provisions in seconds instead of about 15 minutes for a dedicated tier. A search service can't change region or move to or from Serverless, so changing either setting creates a new service, and `azd provision` re-runs ingestion into it. To point them at another service, set `FOUNDRY_IQ_KB_ENDPOINT`, `FOUNDRY_IQ_KB_API_KEY`, and `FOUNDRY_IQ_KNOWLEDGE_BASE` or `FOUNDRY_IQ_SEARCH_INDEX` in the shell before starting the agents. `scripts.run_diy_agents` can also read them from an ignored `.env.mr1` file; see `FOUNDRY_IQ_TARGETS` in that script.

`diy-complexity-routing` routes with Microsoft-Decision-1 (the `decision-1` deployment, `FOUNDRY_DECISION_DEPLOYMENT_NAME`); set `ROUTER = "chat"` at the top of its `main.py` to route with the chat model instead. Its `ROUTE_CRITERIA` and `AGENTIC_PROBABILITY_THRESHOLD` were measured on simple and multi-hop demo questions; re-measure them if you change the criteria or data.

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
.venv/bin/python -m pytest tests/e2e.py -q -k 'source or knowledge_base'
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
