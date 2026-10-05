# Instructions for coding agents

Building agentic retrieval: From DIY to Foundry IQ
Why does agentic retrieval matter, and what does it take to build it yourself? In this session, we will first code an agent loop that decomposes a question, runs parallel searches, inspects evidence, and decides whether to search again. Then we'll see how Foundry IQ can replace that plumbing with a managed agentic knowledge base. Learn how we made the managed service cheaper, faster, and better, thanks to serverless execution, compression, fine-tuned task-specific models, and built-in ranking.

base: Azure AI Search
All the databases do vector search, but we have the best costs and models.

DIY version

* MAF agent that has a local tool vs MCP server with /retrieve_tool
* Have a nice UI that visualizes the process (re-create activity log)
* Version 0: No LLM- just search with the input query - vector
* Version 0.5: Hybrid baby!
* Verson 1: Uses LLM for query rewriting to hybrid search
* Version 2: Uses asyncio.taskgroup (and C# equiv) to make parallel calls to multiple indexes / remote sources
* Version 2.5: Adds source selection via LLM
* Version 3: Auto- add the LLM-based classifier for simple vs complex queries

Slide vs demo approach:

* Nice UI that visualizes
* Frontend that queries
* Slide with super-simplified code

Foundry IQ version

* Latency comparison - even version 1

The burden of maintenance

Advantages of Foundry IQ:
* LLM for everything is expensive!
* Own model for query planning
* Own model for re-ranking
* Own model that can decide on the level of agentic retrieval needed

Which questions require agentic turns?
If you use FoundryIQ==auto, dont have to worry about a classifier because it will just work.