# BRK300: Building Agentic Retrieval From DIY to Foundry IQ

## Session narrative

Start with a strong, simple retrieval baseline. Then progressively introduce harder questions that expose distinct failure modes. Each failure motivates exactly one new capability, allowing the audience to understand why agentic retrieval exists before seeing how Foundry IQ provides those capabilities as a managed experience.

The core framing:

> We're not DIYing search. We're DIYing the agentic orchestration around search.

### Demo architecture

- A Microsoft Agent Framework (MAF) agent calls retrieval through a local function tool. Build it in Python and C# with full parity.
- The DIY tool grows as each failure adds a capability. The Foundry IQ tool calls the SDK `retrieve()` with `include_activity=True`.
- The payoff is swapping the DIY tool for the Foundry IQ tool: the agent stays the same, and the tool body shrinks from the full orchestration to one SDK call.
- Don't use the knowledge base MCP endpoint. It doesn't return the activity log, and MCP portability isn't a goal of this session.
- The portal visualizes each retrieval step in an activity log, for both DIY and Foundry IQ.
- Label DIY steps by capability (for example, "query planning"), not by version number.

---

## 1. Start with strong retrieval

### Baseline

**Question -> Foundry IQ (hybrid + semantic ranker) -> results -> answer generation**

- Use a direct Foundry IQ search call as the baseline rather than presenting simple RAG as DIY.
- Start at hybrid search with semantic ranking. Skip a vector-only step: the session isn't about rebuilding search or decomposing hybrid search into its internals.
- Skip a separate single-query rewriting step. Most agents already rewrite the user's question into one search query before calling a tool.
- Establish that a single retrieval works well when the question maps cleanly to one search.
- Keep this section short.

### Demo question

A simple, single-fact question that one hybrid search answers well. Reuse it in section 4.

### Key point

A strong individual search is useful, but some questions cannot be answered by one search, no matter how good that search is.

---

## 2. Failure #1: The question is compound

### Show the failure

Introduce a question with multiple information needs, all answerable from one source.

A single search query fails to retrieve sufficient evidence for every part of the question.

### What this motivates

**Query planning**

### DIY: query planning

**Question -> LLM query planner -> Query A + Query B + Query C -> parallel searches -> combine results**

- Keep this step intentionally limited to a single source/index.
- Use the LLM to determine which searches are needed.
- Execute those searches in parallel (`asyncio.TaskGroup` in Python, `Task.WhenAll` in C#).
- Combine the retrieved evidence before answer generation.

### Key point

> Query planning solves complexity within the question.

The audience should now understand why agentic retrieval may need to turn one complex user intent into multiple targeted retrievals.

---

## 3. Failure #2: The question is multi-hop

### Show the failure

Ask a multi-hop question: the first round of results reveals something (a term, entity, or reference) that a follow-up search must look up. A single planning pass can't anticipate it.

Contrast it with section 2: a compound question's searches are independent and run in parallel, while a multi-hop question's next search depends on what the last one found.

### What this motivates

**Reflection and iteration**

### DIY: reflect and search again

**Evidence -> LLM reflection: is this sufficient? -> if not, plan follow-up queries -> search again -> stop**

- The LLM inspects the evidence and decides whether to search again.
- The application owns the stopping rule: maximum iterations plus token and latency budgets.
- The activity log shows each round: queries, results, and the reflection decision.

### Key point

> Reflection turns a pipeline into a loop, and loops need stopping rules.

---

## 4. New problem: we're doing all this for every question

Return to the simple question from section 1 that the original Foundry IQ search call answered perfectly well.

Running the entire agentic pipeline would add unnecessary work.

### What this motivates

**Simple-vs-agentic routing**

### DIY: complexity classifier

**Question -> classifier -> direct retrieval OR agentic retrieval**

- Build it on top of the reflection step, on the same single index (`python/diy-complexity-routing`).
- The direct route is the baseline's one search with the question as written. The agentic route is query planning plus reflection.
- The classifier is Microsoft-Decision-1, which returns a probability instead of generated text. The application owns its criteria and threshold, measured by hand.

### Key point

Not every question requires the expensive path, which creates yet another decision, model call, and prompt that the DIY implementation must own.

---

## 5. Foundry IQ on one index: Microsoft Retriever 1

### Same index, managed

The DIY steps searched one index, like most apps do. Show what Foundry IQ does with the same index.

- A seed search with the question as written, then Microsoft Retriever 1 (MR1), a small task-specific model, reads the evidence and returns "search" or "finish". It replaces the DIY query planner and reflection prompts.
- Demo the same questions on a one-index knowledge base with minimal reasoning effort and MR1 as its only model.
- Comparison 1: DIY baseline, query planning, and reflection vs. Foundry IQ with MR1: latency, model calls, tokens, and cost per question.

### Key point

> On one index, a task-specific model does the planning and reflection we wrote prompts for.

---

## 6. Foundry IQ auto

### Auto replaces the classifier

Contrast explicitly with section 4: with Foundry IQ's auto reasoning effort, the DIY complexity classifier goes away. Foundry IQ decides how much agentic retrieval each question needs.

- Auto starts with a search with the question as written. If the results are strong enough, it returns with no model calls; otherwise MR1 takes over, and auto can escalate to an LLM query planner.
- Demo the simple question on a knowledge base with auto effort, with MR1 and an Azure OpenAI model.
- Comparison 2: DIY complexity classifier vs. Foundry IQ auto.

---

## 7. Now replace the orchestration with Foundry IQ

### Look what it took

Open with one slide that shows the whole DIY stack before deleting it: the final architecture, marked with what the application now owns (the prompts, models, thresholds, and round limits). This replaces a separate recap section; the cost of the pipeline was already shown in section 4's setup.

> We built agentic retrieval. Look what it took.

The point is not that DIY is impossible. Building it ourselves exposed the machinery and helped us understand why each capability exists.

### Swap the tool

Swap the DIY tool for the Foundry IQ tool. The agent stays the same, and the tool body becomes one SDK `retrieve()` call.

Revisit the same demo questions using Foundry IQ, with the activity log showing what it did.

Show how the managed retrieval experience handles the capabilities that were progressively added to the DIY implementation:

- query planning
- multiple retrievals
- ranking
- iteration
- auto behavior for simple versus more complex retrieval needs

### Payoff

> We just built all of this so we could understand what agentic retrieval actually does. Now let's delete most of it.

Contrast the amount of application-side orchestration with the DIY implementation.

---

## 8. Agentic retrieval under the hood

Now that the audience understands the problems each capability solves, explain the conceptual retrieval flow in more depth.

Topics to cover:

- initial retrieval
- query planning
- planned retrievals
- retrieving across appropriate sources
- deciding whether additional retrieval is necessary
- ranking the final evidence
- auto behavior for straightforward versus complex questions

Connect each capability back to the failure that motivated it earlier in the session.

---

## 9. DIY vs. Foundry IQ

Synthesize the comparison around what developers must own.

### DIY

- build and maintain orchestration
- make multiple general-purpose model calls: using a general-purpose LLM for every step is expensive
- write and maintain a prompt for every model call
- implement routing and planning
- manage multi-source retrieval
- merge and rank evidence
- decide when retrieval is complete
- own latency and token tradeoffs
- evaluate the full retrieval pipeline

### Foundry IQ

- fine-tuned, task-specific models for query planning and reranking instead of general-purpose LLM calls
- its own model to decide how much agentic retrieval each question needs (auto)
- serverless execution
- compression
- simpler application integration: one SDK call
- less application-side orchestration to maintain

### Latency and cost

Show pre-measured numbers on a slide for the DIY pipeline versus Foundry IQ on the same questions, including the DIY query planning step alone:

- latency
- LLM calls and tokens per question
- estimated cost per question

### Foundry IQ positioning

> All the databases do vector search, but Foundry IQ has the best costs and models.

The comparison should not be "DIY bad, product good." The DIY implementation is the teaching device that makes the managed capabilities understandable.

---

## 10. Takeaway

> Understand the machinery, then decide how much of it you actually want to own.

The progression of the session is:

1. One strong retrieval works.
2. A compound question breaks it -> add query planning.
3. A multi-hop question breaks it -> add reflection and iteration.
4. Simple questions don't need the whole pipeline -> add a complexity classifier.
5. Foundry IQ on one index: Microsoft Retriever 1 replaces planning and reflection. Comparison 1.
6. Foundry IQ auto replaces the classifier. Comparison 2.
7. Look at what we've accumulated, then replace the DIY orchestration with Foundry IQ.

---

## Not covered: multi-source retrieval

The talk stays on one index. We're specifically not covering multi-source fan-out, source selection, or LLM reranking across sources, for two reasons:

- Time: the single-index story (query planning, reflection, complexity routing) already fills the DIY half.
- Microsoft Retriever 1 doesn't do multi-source selection, so these steps have no MR1 counterpart to compare against.

The code stays in `python/diy-multi-source-fanout`, `python/diy-source-selection`, `python/diy-llm-reranking`, and `python/diy-complexity-classifier` (the classifier built on those steps), and the deck keeps their slides as an appendix. The portal and `scripts.run_diy_agents` don't list them. The original plan for these sections follows.

### Failure: The evidence lives across sources

#### Show the next failure

The query planner is now asking the right questions, but the required evidence is not all available in the same source.

Some subqueries need Source A, while others require Source B.

#### What this motivates

**Multi-source retrieval**

#### DIY: multi-source fan-out

**Question -> query planner -> queries -> parallel searches across every source -> combine results**

- Send every planned query to every source.
- The application now owns a connection, query format, and result shape for each source.
- Combine the evidence across sources.

#### Key point

> Multi-source retrieval solves distribution of knowledge.

This is a separate problem from query planning and should be introduced as a separate failure.

---

### Failure: Not every source is relevant

#### Show the failure

Ask a question whose answer lives in only one source. Fanning out to every source adds latency and cost, and pulls in off-topic results that can distract answer generation.

#### What this motivates

**Source selection**

#### DIY: source selection

**Question -> query planner -> queries + target sources -> parallel searches on selected sources -> combine results**

Now the application needs to determine:

- which sources are relevant
- which query should go to which source
- how to describe each source well enough for the LLM to choose

#### Key point

> Source selection decides where to look, not just what to ask.

Introduce it after fan-out so the audience sees the cost it removes.

---

### Failure: Now we have a pile of evidence

Multiple queries across multiple sources produce many candidate results. Scores from different queries and sources aren't directly comparable, and the best evidence gets buried or overflows the context budget.

#### What this motivates

**Ranking across the combined result set**

#### DIY: LLM reranking

**Combined results -> LLM reranker -> top evidence -> answer generation**

The DIY system now needs to:

- merge results from different queries and sources
- use an LLM to judge each result's relevance to the original question
- pass the strongest evidence to answer generation

#### Key point

Ranking is another model call, with more latency, more tokens, and another prompt to maintain.

It also needs tuning to be usable. The first version took about 20 seconds per call: the model reasoned before judging, and copied back every long chunk ID in its output. Labeling candidates A, B, C… and turning reasoning off brought it to about 2 seconds with the same judgments. That's the kind of tuning a DIY system has to discover and maintain itself.

A specialized model is another DIY option. Microsoft-Decision-1 returns typed scores instead of text, and reranked in about 1 second instead of 2–3. But it scored chunks that answer only one part of a compound question too low, so follow-up results got dropped. Scoring follow-ups against what reflection says is still missing, and reserving them evidence slots, fixed that. Same lesson: every model swap needs its own evaluation and tuning.

---

## Open items

- Pick a demo question for each failure. Some questions may cover more than one failure. Start from the Cocoarynth mysteries in [data/cocoarynth/questions.yaml](data/cocoarynth/questions.yaml), which list the expected answer and the documents that support each fact. Several span multiple indexes. M5 reliably needs a second round of searches, so it suits the reflection step; with six indexes to plan across, M2 is usually answered in one round.
- Decide live demo versus slides for each section.
- Measure latency, tokens, and cost for the latency and cost slide.
