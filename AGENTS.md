# Instructions for coding agents

Building agentic retrieval: From DIY to Foundry IQ
Why does agentic retrieval matter, and what does it take to build it yourself? In this session, we will first code an agent loop that decomposes a question, runs parallel searches, inspects evidence, and decides whether to search again. Then we'll see how Foundry IQ can replace that plumbing with a managed agentic knowledge base. Learn how we made the managed service cheaper, faster, and better, thanks to serverless execution, compression, fine-tuned task-specific models, and built-in ranking.

[outline.md](outline.md) is the source of truth for the session structure, demo architecture, and DIY capability progression. Follow it when building demos, slides, or portal features.

## Naming

Say "Foundry IQ", never "Azure AI Search", in slides, speaker notes, docs, and UI text. Code identifiers that come from SDKs or Azure resource types, such as `SearchClient`, `azure-search-documents`, or `Microsoft.Search/searchServices`, keep their names.

On slides, write "Microsoft Retriever 1" in slide titles and panel headings, and `microsoft-retriever-1` in body text, diagram labels, tables, chart labels, captions, and footers. Don't write "MR1" on slides. Speaker notes use the spoken name, "Microsoft Retriever 1".

## Slide guidelines

Slides follow this year's Ignite theme, even where it differs from the Cocoarynth brand used in the portal and documents.

- Fonts: Segoe UI Semibold for headings, Segoe UI for body text. Don't add bold or italics to headings; bold in body text is fine for emphasis, such as marking keyword matches.
- Text color: black (`#000000`) for headings and body text.
- Colors: use the Microsoft brand colors below. Type and background combinations must meet a 4.5:1 contrast ratio, and color must not be the only way information is conveyed.
- Cocoarynth logos and document screenshots can appear as imagery.

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

- **Switch the Foundry IQ demo to Microsoft Retriever 1 (MR1).** The "Foundry IQ knowledge base" demo slide and comparison 1 show real MR1 responses from an MR1-enabled preview search service (minimal effort, MR1, reranker threshold 2.0, one index of all Cocoarynth records), rendered in the portal with source names shortened. Comparison 2 uses auto effort; the repo's auto agent now uses one index there (`lab673-brk300-kb-all-auto-mr1`). Our subscription's `cocoarynth-kb-all` uses auto reasoning effort, which falls back to an LLM planner when the first search isn't enough. Once MR1 is enabled on our subscription, switch `cocoarynth-kb-all` to MR1 and re-capture the demo live. Send the question as a search intent: with chat messages, auto skipped MR1 and went straight to the LLM planner.
- **Keep the reranker threshold at 2.0 for MR1 demos.** At 1.5 or lower, the first search returns plenty of loosely related chunks and MR1 finishes without searching again, missing the estate's harvest report. At 2.25 or higher, too little evidence survives. The Foundry IQ team has the failing cases.
- **Confirm what's public about MR1 before Ignite:** its name and pricing ($0.10 per million input tokens, output free). The MR1 slide and the cost slide use both. Don't document MR1's or the planner's loop caps anywhere.
- **Refresh the MR1 eval numbers before Ignite.** "Microsoft Retriever 1 finds more of the evidence" and "Auto with Microsoft Retriever 1: about 6× faster" show pre-production benchmark results (559 questions; finish points from 8,005 auto requests), not Cocoarynth data. Replace them with release numbers once they're published. "Microsoft Retriever 1 improves search performance" stacks search quality over token count; the token chart uses the orchestrating-agent numbers from the MSR1 blog draft.
- **Re-capture "DIY vs. auto: cost per question" on our environment.** All three rows come from a lab measurement: copies of the six sources on the `magottei-s1m-wecus-serverless` search service, with gpt-6-luna (`magottei-swedencentral-foundry`) as both the DIY chat model and the knowledge base's LLM planner, Decision-1 for DIY routing, and Foundry IQ auto with microsoft-retriever-1 at reranker threshold 2.0. Each cell averages 4 runs in two sets ten minutes apart; no request was throttled, and every run found the expected evidence. With the same model on both sides, DIY is cheaper on the compound question ($2.90 vs. $4.70 per 1K: DIY usually runs 2 semantic queries, auto ranks the results of about 20 searches). DIY times vary with its reflection rounds (compound 6.4–10.6 s, multi-hop 13.1–17.7 s). Costs exclude serverless compute and storage. The storage stats on "Foundry IQ Auto with Microsoft Retriever 1" come from the same runs. Re-run the three questions with `cocoarynth-kb-all` (switched to MR1) and the DIY steps on our environment before Ignite. The repo now uses one index, and on the preview service the compound question ran 5 searches with one index instead of 24 with six sources, so its auto cost (and the "about 20 searches" note) will drop. Model calls (0, 3, 2) and times matched the slide.

## Development Tips

### Start the portal locally

Use a provisioned Azure Developer CLI environment. The application loads the selected `azd` environment through `dotenv-azd`; do not assume a local `.env` file exists.

```shell
azd auth login
azd env select
.venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. Existing shell environment variables override values loaded from `azd`.

For a clean restart, stop every listener on the development port before starting Uvicorn again:

```shell
for server_pid in $(lsof -tiTCP:8000 -sTCP:LISTEN); do kill "$server_pid"; done
.venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Use the actual port in both commands when working on another port, such as `49153`. With `--reload`, Python edits restart the worker automatically. Refresh the browser after static HTML, CSS, or JavaScript edits. When changing a referenced static asset, increment its query-string version in `app/static/index.html` to prevent stale browser assets.

The portal covers one knowledge base, `cocoarynth-kb-all`, and its one knowledge source, `cocoarynth-all-source`, over the index the DIY agents search, `cocoarynth-all-index`. `KNOWLEDGE_SOURCE` in `app/naming.py` is the single definition of the source's display name, description, index, Blob container, and knowledge source name; the backend routes, `scripts/preingest.py`, and the DIY document proxy all derive from it. The sidebar navigation is hidden for the talk (`<aside hidden>` in `index.html`); the home page links to each area. The areas are:

- **Knowledge source** (`/source/documents`, `/source/chunks`, `/source/search`): browse the PDFs, view a document's chunks, or search the index. The matching APIs are `GET /api/source`, `GET /api/source/documents`, `GET /api/source/documents/content/{blob}`, `GET /api/source/chunks?document=...`, and `POST /api/source/search`.
- **Knowledge base** (`/knowledge-base/configuration`, `/knowledge-base/query`): the live knowledge base definition (`GET /api/knowledge-base`) and a query page that retrieves with the activity log (`POST /api/retrieve`).
- **DIY agentic retrieval** (`/diy-agents`) and **Demos** (`/demos`), described below.

`scripts/preingest.py` creates the knowledge source and the knowledge base with `FoundryIqService.create_knowledge_base`. Resources from the older six-source layout (per-source indexes, knowledge sources, Blob containers, and the `cocoarynth-kb-docs` and `cocoarynth-kb-engineering-practices` knowledge bases) are left in place on existing deployments.

Useful checks:

```shell
curl --fail-with-body http://127.0.0.1:8000/health
curl --fail-with-body http://127.0.0.1:8000/api/sources
curl --fail-with-body http://127.0.0.1:8000/api/knowledge-base
```

### DIY retrieval agents

Each DIY step is a Microsoft Agent Framework hosted agent in its own folder under `python/`, registered as a `host: azure.ai.agent` service in `azure.yaml`:

`diy-baseline` -> `diy-query-planning` -> `diy-reflection` -> `diy-complexity-routing`

These four steps are the talk's DIY story. They search one index, `cocoarynth-all-index`, which holds every Cocoarynth company record. `scripts/preingest.py` uploads all 55 Cocoarynth PDFs to one Blob container, `cocoarynth`, and ingests them into that index.

The talk doesn't cover multi-source retrieval (see "Not covered" in [outline.md](outline.md)), so the repository has no multi-source steps.

Each `main.py` is self-contained and cumulative: it repeats all the code from the previous step and adds one capability, so consecutive steps can be diffed on stage. Shared code (credentials, `ActivityLog`, `ask_llm`, `hybrid_search`, `combine_results`, agent instructions, `pyproject.toml`, `.agentignore`) is intentionally duplicated; `ActivityLog` starts in step 2, since the baseline records its single step inline. When you change shared code, apply the same change to every later step that contains it, then run `uv lock` in each folder whose `pyproject.toml` changed.

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

The portal's **DIY agentic retrieval** page (`/diy-agents`) asks any step a question and shows its answer, a latency waterfall (each retrieval step plus the agent's own model calls before and after retrieval, placed by the start times the agents record), the activity log (each tool call, planned and parallel searches by index, reranking, reflection rounds, routing, with timings and token usage), and the evidence it returned. Start all seven agents (the four DIY steps and the three Foundry IQ agents) with one command from the repository root, then start the portal as described above and open <http://127.0.0.1:8000/diy-agents>:

```shell
.venv/bin/python -m scripts.run_diy_agents
```

Each step listens on its own port (8091–8097), so all seven can run at once. Pass step names to start only some of them, for example `.venv/bin/python -m scripts.run_diy_agents diy-baseline diy-reflection`. The script creates a missing step `.venv`, prints only each agent's activity log, startup, and error lines, and stops every agent on Ctrl+C. The step list, ports, and example questions live in `app/diy_agents.py`; add new steps there too. Each step has one or two examples with short labels such as "Succeeds: tasting panel winner" or "Fails: Madagascar estate delay". `DEMOS` in the same file lists the talk's demos in the deck's slide order, each with its slide title; keep it in sync when demo slides move.

The page has an agent dropdown, the question, and **Ask agent**. Its URL tracks the selected agent and question as `?agent=<step-name>&question=<text>`, and adding `&run=1` asks the question as soon as the page loads. The tab title names the step and, when the question matches one, the example label, such as "3 · Reflection · Succeeds: Madagascar estate delay". To open every demo in its own running tab, in slide order, start the portal and the agents, then run:

```shell
.venv/bin/python -m scripts.open_demo_tabs
```

Pass step names to open only their demos, `--no-run` to load the questions without asking them, or `--base-url` for another port. The **Demos** page (`/demos`) lists the same demos, and each link opens one in a new tab. The DIY page only reaches agents running on the same machine as the portal, so it doesn't work in the deployed portal.

The Foundry IQ agents are the same agent as the DIY steps, but `search_knowledge` makes one call to Foundry IQ. The first two make a retrieve call to a knowledge base and report its activity log as their own steps, and differ only in the knowledge base they call:

- `python/foundry-iq-mr1-kb` (port 8095) uses `lab673-brk300-kb-all-minimal-mr1`: minimal reasoning effort, with Microsoft Retriever 1 as its only model, over one index of every Cocoarynth record.
- `python/foundry-iq-auto` (port 8096) uses `lab673-brk300-kb-all-auto-mr1`: auto reasoning effort, with Microsoft Retriever 1 and an Azure OpenAI model (gpt-5.6-luna), over the same one index. The older six-source `lab673-brk300-kb-auto-mr1` is still on the preview service.
- `python/foundry-iq-mr1-search` (port 8097) skips the knowledge base: `search_knowledge` makes one `SearchClient.search` call with `query_type="agentic"` on the `lab673-brk300-all` index (`FOUNDRY_IQ_SEARCH_INDEX`), and reports the subqueries, stop reason and token usage from `debug="agentic"`. Agentic search has no reranker threshold, so on the estate question it stops after the first round and misses the harvest report.

Both knowledge bases live on an MR1-enabled preview search service, since MR1 isn't enabled on our subscription yet. `scripts.run_diy_agents` reads that service's endpoint and admin key from the ignored `.env.mr1` file (`AZURE_SEARCH_PREVIEW_ENDPOINT`, `AZURE_SEARCH_PREVIEW_ADMIN_KEY`) and passes them as `FOUNDRY_IQ_KB_ENDPOINT` and `FOUNDRY_IQ_KB_API_KEY`. To use another service or knowledge base, set `FOUNDRY_IQ_KB_ENDPOINT`, `FOUNDRY_IQ_KB_API_KEY`, and `FOUNDRY_IQ_KNOWLEDGE_BASE` in the shell before starting the agents.

Both agents call the knowledge base with a nightly build of `azure-search-documents` (12.1.0a20261027002, pinned by URL in their `pyproject.toml`), because MR1 needs API version `2026-11-01-preview` and the latest released SDK only supports up to `2026-08-01-preview`. Our own search service doesn't accept `2026-11-01-preview` yet, so these agents only work against the preview service until it does. Switch to a released SDK once one supports that API version.

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
