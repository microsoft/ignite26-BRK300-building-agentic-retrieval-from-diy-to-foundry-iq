<a name="start-building"></a>

<p align="center">
<img src="img/banner-ignite-26.png" alt="Microsoft Ignite 2026" width="100%"/>
</p>

# [Microsoft Ignite 2026](https://ignite.microsoft.com)

## 🔥BRK300: Building agentic retrieval: From DIY to Foundry IQ

### Session description

Why does agentic retrieval matter, and what does it take to build it yourself? In this session, we will first code an agent loop that decomposes a question, runs parallel searches, inspects evidence, and decides whether to search again. Then we'll see how Foundry IQ can replace that plumbing with a managed agentic knowledge base. Learn how we made the managed service cheaper, faster, and better, thanks to serverless execution, compression, fine-tuned task-specific models, and built-in ranking.

### 🚀 Getting started

This repository contains everything shown in the session:

- [`python/`](python/): the retrieval agents, each a Microsoft Agent Framework hosted agent in its own folder. The four DIY steps build on each other: `diy-baseline` → `diy-query-planning` → `diy-reflection` → `diy-complexity-routing`. Each step's `main.py` repeats the previous step and adds one capability, so you can diff consecutive steps. The `foundry-iq-mr1-kb`, `foundry-iq-auto`, and `foundry-iq-mr1-search` agents replace the DIY retrieval code with one call to Foundry IQ.
- [`app/`](app/): a FastAPI portal for browsing the knowledge source, querying the Foundry IQ knowledge base, and asking the agents questions.
- [`data/`](data/README.md): the Cocoarynth sample corpus, 55 generated PDFs from a fictional chocolate maker, plus demo questions.
- [`infra/`](infra/) and [`azure.yaml`](azure.yaml): Bicep infrastructure and Azure Developer CLI configuration.

#### Prerequisites

- An [Azure subscription](https://azure.microsoft.com/free/)
- [Azure Developer CLI](https://aka.ms/azure-dev/install), with the AI agents extension for the `azd ai agent` commands:

    ```shell
    azd extension install azure.ai.agents
    ```

- [Python 3.12 or later](https://www.python.org/downloads/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [Git](https://git-scm.com/downloads)

> [!IMPORTANT]
> The resources created by `azd up` incur costs as soon as they're provisioned. Run `azd down` when you're done to delete them.

#### Steps

1. Clone this repository and open it in a terminal.
2. Sign in and provision the Azure resources. The `postprovision` hook runs `scripts/preingest.py`, which uploads the Cocoarynth PDFs, ingests them into the `cocoarynth-all-index` index, and creates the `cocoarynth-kb-all` knowledge base:

    ```shell
    azd auth login
    azd up
    ```

3. Create a virtual environment at the repository root and install the portal and development dependencies:

    ```shell
    python3 -m venv .venv
    .venv/bin/pip install -r requirements-dev.txt
    ```

4. Run one DIY step locally. `azd ai agent run` expects the virtual environment in the step folder:

    ```shell
    cd python/diy-baseline
    uv venv && uv pip install uv
    source .venv/bin/activate
    TZ=UTC azd ai agent run diy-baseline --no-client
    ```

    From a second terminal, ask it a question:

    ```shell
    azd ai agent invoke diy-baseline --local --new-conversation "Which origin won Cocoarynth's September 2026 single-origin bar tasting panel, and what were its flavor notes?"
    ```

    Stop the step before starting the next one, since every step listens on the same local port. [AGENTS.md](AGENTS.md) has a question for each step that shows the capability it adds.

5. To compare all the agents side by side, start them together from the repository root, then start the portal in another terminal and open <http://127.0.0.1:8000/diy-agents>:

    ```shell
    .venv/bin/python -m scripts.run_diy_agents
    ```

    ```shell
    .venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
    ```

The Foundry IQ agents call knowledge bases that use Microsoft Retriever 1, which needs a search service in a supported region with paid Knowledge Retrieval. The infrastructure creates a Serverless search service in West Central US by default, separate from the models' region; to use another region or a dedicated tier, run `azd env set AZURE_SEARCH_LOCATION <region>` or `azd env set AZURE_SEARCH_SKU standard` before `azd provision`. To point them at your own service and knowledge base, set `FOUNDRY_IQ_KB_ENDPOINT`, `FOUNDRY_IQ_KB_API_KEY`, and `FOUNDRY_IQ_KNOWLEDGE_BASE` before starting the agents. See [AGENTS.md](AGENTS.md) for details and for running the tests.

### 🎯 Learning outcomes

By the end of this session, you will be able to:

- Build a DIY agentic retrieval loop with Microsoft Agent Framework that decomposes a question, runs parallel hybrid searches, and reflects on the evidence to decide whether to search again.
- Route questions by complexity, sending simple questions to a single search and multi-hop questions to the agentic loop.
- Replace DIY retrieval plumbing with a Foundry IQ knowledge base, and compare the cost, latency, and answer quality of DIY and managed agentic retrieval.

### 💻 Technologies used

- Foundry IQ (knowledge sources, knowledge bases, and agentic retrieval)
- Microsoft Foundry hosted agents
- Microsoft Agent Framework (Python)
- Microsoft Retriever 1
- Microsoft-Decision-1
- Azure OpenAI models: gpt-5.5 and text-embedding-3-large
- Azure Content Understanding
- Azure Blob Storage
- Azure Developer CLI and Bicep

### 📚 Continue your learning

Pick your next step based on your learning style:

| Resource | What you'll get |
|----------|-----------------|
| **[Get started with Foundry IQ](https://aka.ms/FoundryIQ)** | Overview of building agentic knowledge bases with Foundry IQ |
| **[Microsoft Retriever 1 evaluations](https://aka.ms/FoundryIQ-Retriever1-Evals)** | Blog post about microsoft-retriever-1 and how it was evaluated |
| **[Ignite 2026 Next Steps](https://aka.ms/ignite26-next-steps)** | Additional session repos and materials from Microsoft Ignite 2026 |
| **[Microsoft AI Skills Navigator](https://aka.ms/ignite26-asn)** | Discover AI skills, learning paths, and resources for your next step |
| **[Microsoft Learn](https://aka.ms/ignite26-learn)** | Official documentation and guided learning paths on these topics |
| **[Microsoft Foundry Community](https://aka.ms/MicrosoftFoundryDiscord-Ignite26)** | Connect with other learners and experts in our Discord community |

### 🌟 Microsoft Learn MCP Server

The Microsoft Learn MCP Server gives your AI agent direct access to Microsoft's official documentation — grounded, up-to-date answers about the topics in this session.

**GitHub Copilot CLI** — Install with:

```shell
copilot plugin install microsoftdocs/mcp
```

**VS Code** — One-click install:  
[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_Microsoft_Learn_MCP-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=microsoft-learn&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Flearn.microsoft.com%2Fapi%2Fmcp%22%7D)

For more information, visit the [Learn MCP Server repo](https://aka.ms/learnmcp).

### 👥 Content owners

<table>
<tr>
    <td align="center"><a href="http://github.com/pamelafox">
        <img src="https://github.com/pamelafox.png" width="100px;" alt="Pamela Fox"/><br />
        <sub><b>Pamela Fox</b></sub></a><br />
            <a href="https://github.com/pamelafox" title="talk">📢</a>
    </td>
    <td align="center"><a href="http://github.com/mattgotteiner">
        <img src="https://github.com/mattgotteiner.png" width="100px;" alt="Matt Gotteiner"/><br />
        <sub><b>Matt Gotteiner</b></sub></a><br />
            <a href="https://github.com/mattgotteiner" title="talk">📢</a>
    </td>
</tr></table>

### ⚖️ Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general). Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.

Any use of third-party trademarks or logos are subject to those third-party's policies.
