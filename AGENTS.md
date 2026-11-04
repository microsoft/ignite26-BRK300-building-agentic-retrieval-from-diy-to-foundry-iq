# Instructions for coding agents

Building agentic retrieval: From DIY to Foundry IQ
Why does agentic retrieval matter, and what does it take to build it yourself? In this session, we will first code an agent loop that decomposes a question, runs parallel searches, inspects evidence, and decides whether to search again. Then we'll see how Foundry IQ can replace that plumbing with a managed agentic knowledge base. Learn how we made the managed service cheaper, faster, and better, thanks to serverless execution, compression, fine-tuned task-specific models, and built-in ranking.

[outline.md](outline.md) is the source of truth for the session structure, demo architecture, and DIY capability progression. Follow it when building demos, slides, or portal features.

## Naming

Say "Foundry IQ", never "Azure AI Search", in slides, speaker notes, docs, and UI text. Code identifiers that come from SDKs or Azure resource types, such as `SearchClient`, `azure-search-documents`, or `Microsoft.Search/searchServices`, keep their names.

## Slide guidelines

Slides follow this year's Ignite theme, even where it differs from the Cocoarynth brand used in the portal and documents.

- Fonts: Segoe UI Semibold for headings, Segoe UI for body text. Don't add bold or italics to headings; bold in body text is fine for emphasis, such as marking keyword matches.
- Text color: black (`#000000`) for headings and body text.
- Colors: use the Microsoft brand colors below. Type and background combinations must meet a 4.5:1 contrast ratio, and color must not be the only way information is conveyed.
- Cocoarynth logos and document screenshots can appear as imagery.
- No code on slides. Keep slides high-level and architectural, since the audience includes both Python and C# developers. Link each DIY step's slide to its code instead.

| Family | Light | Mid | Dark |
|---|---|---|---|
| Whites | Pure White `#FFFFFF` | Off White `#F4F3F5` | Warm White `#FFF8F3` |
| Warm grays | Warm Light Gray `#E8E6DF` | Mid Gray `#D7D2CB` | Warm Gray `#8C8279` |
| Cold grays | Light Gray `#D9D9D6` | Cool Gray `#B1B3B3` | Dark Gray `#454142` |
| Browns | Light Brown `#E1D3C7` | Brown `#BF9474` | Dark Brown `#5C4738` |
| Yellows | Light Yellow `#FFE399` | Yellow `#FFB900` | Dark Yellow `#7F5A1A` |
| Oranges | Light Orange `#FFA38B` | Orange `#FF5C39` | Dark Orange `#73391D` |
| Reds | Light Red `#FFB3BB` | Red `#F4364C` | Dark Red `#73262F` |
| Magentas | Light Magenta `#D59ED7` | Magenta `#C03BC4` | Dark Magenta `#702573` |
| Purples | Light Purple `#C5B4E3` | Purple `#8661C5` | Dark Purple `#463668` |
| Blues | Light Blue `#8DC8E8` | Blue `#0078D4` | Dark Blue `#2A446F` |
| Teals | Light Teal `#B9DCD2` | Teal `#49C5B1` | Dark Teal `#225B62` |
| Greens | Light Green `#D4EC8E` | Green `#8DE971` | Dark Green `#07641D` |
| Blacks | Blue Black `#091F2C` | Pure Black `#000000` | Brown Black `#291817` |

When previewing generated decks, don't script PowerPoint against its active presentation: the user may have other decks open. Use `qlmanage -t` or LibreOffice instead, or ask first.

## Open items for the deck

Track deck TODOs here, not in speaker notes.

- **Switch the Foundry IQ demo to Microsoft Retriever 1 (MR1).** The "Foundry IQ knowledge base" demo slide and comparison 1 show real MR1 responses from an MR1-enabled preview search service (minimal effort, MR1, reranker threshold 2.0, one index of all Cocoarynth records), rendered in the portal with source names shortened. Comparison 2 uses auto effort on the six sources there. Our subscription's `cocoarynth-kb-all` still uses low reasoning effort with an LLM planner. Once MR1 is enabled on our subscription, switch `cocoarynth-kb-all` to MR1 and re-capture the demo live. Send the question as a search intent: with chat messages, auto skipped MR1 and went straight to the LLM planner.
- **Keep the reranker threshold at 2.0 for MR1 demos.** At 1.5 or lower, the first search returns plenty of loosely related chunks and MR1 finishes without searching again, missing the estate's harvest report. At 2.25 or higher, too little evidence survives. The Foundry IQ team has the failing cases.
- **Confirm what's public about MR1 before Ignite:** its name, the two-follow-up limit, and pricing ($0.10 per million input tokens, output free). The MR1 slide and the cost slide use all three.

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

The portal covers one knowledge base, `cocoarynth-kb-all`, and its six knowledge sources, one per index that the DIY agents search: Farms & harvests, Quality checks, Shipping & sales, Policies, Origin Passport, and Engineering. `KNOWLEDGE_SOURCES` in `app/backend/naming.py` is the single definition of each source's URL key, display name, description, index, Blob container, and knowledge source name; the backend routes, `scripts/preingest.py`, and the DIY document proxy all derive from it. The navigation has three groups:

- **Knowledge sources** (`/sources/{key}/documents`, `/sources/{key}/chunks`, `/sources/{key}/search`): browse a source's PDFs, view a document's chunks, or search its index. A source selector on each page switches all three pages and the URL. The matching APIs are `GET /api/sources`, `GET /api/sources/{key}/documents`, `GET /api/sources/{key}/documents/content/{blob}`, `GET /api/sources/{key}/chunks?document=...`, and `POST /api/sources/{key}/search`.
- **Knowledge base** (`/knowledge-base/configuration`, `/knowledge-base/query`): the live knowledge base definition (`GET /api/knowledge-base`) and a query page that retrieves from all six sources with the activity log (`POST /api/retrieve`).
- **DIY agentic retrieval** (`/diy-agents`), described below.

`scripts/preingest.py` creates the six knowledge sources and the knowledge base with `FoundryIqService.create_knowledge_base`, and deletes the older `cocoarynth-kb-docs` and `cocoarynth-kb-engineering-practices` knowledge bases.

Useful checks:

```shell
curl --fail-with-body http://127.0.0.1:8000/health
curl --fail-with-body http://127.0.0.1:8000/api/sources
curl --fail-with-body http://127.0.0.1:8000/api/knowledge-base
```

### DIY retrieval agents

Each DIY step is a Microsoft Agent Framework hosted agent in its own folder under `python/`, registered as a `host: azure.ai.agent` service in `azure.yaml`:

`diy-baseline` -> `diy-query-decomposition` -> `diy-reflection` -> `diy-multi-source-fanout` -> `diy-source-selection` -> `diy-llm-reranking` -> `diy-complexity-classifier`

Steps 1–3 search one index, `cocoarynth-all-index`, which holds every Cocoarynth company record. `scripts/preingest.py` builds it by copying the chunks of the four Cocoarynth indexes. Steps 4–7 search the six separate indexes.

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
azd ai agent invoke diy-baseline --local --new-conversation "Which origin won Cocoarynth's September 2026 single-origin bar tasting panel, and what were its flavor notes?"

# diy-query-decomposition: two information needs in one index, searched in parallel. The baseline's one search
# returns only hold records, so it misses the September 2026 tasting panel's notes on the bar.
azd ai agent invoke diy-query-decomposition --local --new-conversation "What did the tasting panel think of the Madagascar bar, and why was its bean lot held on arrival?"

# diy-reflection: round 1 finds the hold notice for lot MG26-L02 from Tsaravola Estate, and follow-up rounds use
# those names to find the estate's harvest report (M5). diy-query-decomposition says the estate cause is missing.
azd ai agent invoke diy-reflection --local --new-conversation "What happened at the estate that delayed the launch of the Madagascar limited-edition bar with cacao nibs?"

# diy-multi-source-fanout, diy-source-selection, diy-llm-reranking: evidence spread across indexes (M1).
azd ai agent invoke diy-multi-source-fanout --local --new-conversation "Why did the Ghana bars sold at Larkspur Market turn grey and dusty-looking in July 2026, and was the batch itself bad?"
azd ai agent invoke diy-source-selection --local --new-conversation "Why did the Ghana bars sold at Larkspur Market turn grey and dusty-looking in July 2026, and was the batch itself bad?"
azd ai agent invoke diy-llm-reranking --local --new-conversation "Why did the Ghana bars sold at Larkspur Market turn grey and dusty-looking in July 2026, and was the batch itself bad?"

# diy-complexity-classifier: a simple question takes the direct route, M5 takes the agentic route.
azd ai agent invoke diy-complexity-classifier --local --new-conversation "What is the recommended storage temperature for chocolate bars?"
azd ai agent invoke diy-complexity-classifier --local --new-conversation "What happened at the estate that delayed the launch of the Madagascar limited-edition bar with cacao nibs?"
```

Each step logs its activity to the `azd ai agent run` terminal as `INFO:<step-name>:{...}` lines, mixed in with telemetry output.

#### Ask the agents from the portal

The portal's **DIY agentic retrieval** page (`/diy-agents`) asks any step a question and shows its answer, a latency waterfall (each retrieval step plus the agent's own model calls before and after retrieval, placed by the start times the agents record), the activity log (each tool call, planned and parallel searches by index, reranking, reflection rounds, routing, with timings and token usage), and the evidence it returned. Start all eight agents (the seven DIY steps and the Foundry IQ agent) with one command from the repository root, then start the portal as described above and open <http://127.0.0.1:8000/diy-agents>:

```shell
.venv/bin/python -m scripts.run_diy_agents
```

Each step listens on its own port (8091–8098), so all eight can run at once. Pass step names to start only some of them, for example `.venv/bin/python -m scripts.run_diy_agents diy-baseline diy-reflection`. The script creates a missing step `.venv`, prints only each agent's activity log, startup, and error lines, and stops every agent on Ctrl+C. The step list, ports, and example questions live in `app/backend/diy_agents.py`; add new steps there too. Each step has a few examples with short labels such as "Succeeds: tasting panel winner" or "Fails: Madagascar estate delay", which the page offers in a dropdown. The page only reaches agents running on the same machine as the portal, so it doesn't work in the deployed portal.

The Foundry IQ agent (`python/foundry-iq`, port 8098) is the same agent, but `search_knowledge` makes one retrieve call to a Foundry IQ knowledge base and reports the knowledge base's activity log as its own steps. It uses `cocoarynth-kb-all` by default. To point it at another search service, such as one with Microsoft Retriever 1 enabled, set `FOUNDRY_IQ_KB_ENDPOINT`, `FOUNDRY_IQ_KB_API_KEY`, and `FOUNDRY_IQ_KNOWLEDGE_BASE` in the shell before starting it; keep the key in an ignored `.env.*` file.

Steps 6–7 rerank with Microsoft-Decision-1 (the `decision-1` deployment, `FOUNDRY_DECISION_DEPLOYMENT_NAME`), and step 7 also routes with it. Each step keeps the chat-model version behind a constant at the top of `main.py`: set `RERANKER = "chat"` or `ROUTER = "chat"` to switch back. The thresholds next to those constants (`DECISION_MIN_RELEVANCE`, `AGENTIC_PROBABILITY_THRESHOLD`) were measured against the chat model's decisions; re-measure them if you change the prompts or data.

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
