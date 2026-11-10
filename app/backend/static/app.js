const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("show");
  window.setTimeout(() => element.classList.remove("show"), 3200);
}

async function api(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { message = (await response.json()).detail || message; } catch {}
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

function showJson(selector, value) {
  const element = $(selector);
  element.classList.remove("empty");
  element.textContent = JSON.stringify(value, null, 2);
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function formatBytes(size) {
  if (!Number.isFinite(size)) return "Unknown size";
  if (size < 1024) return `${size} B`;
  return `${(size / 1024).toFixed(1)} KB`;
}

function setLoading(container, message) {
  container.replaceChildren(element("div", "empty-state loading", message));
}

const sourcePages = ["documents", "chunks", "search"];
const panelPaths = {
  home: "/",
  "kb-configuration": "/knowledge-base/configuration",
  "kb-query": "/knowledge-base/query",
  "diy-agents": "/diy-agents",
  demos: "/demos",
};
const defaultTitle = document.title;
const exampleSearches = {
  origins: "fermentation log for the Madagascar lot",
  quality: "bean moisture test results on arrival",
  "logistics-retail": "temperature logger readings during shipment",
  policies: "recommended storage temperature for chocolate bars",
  documents: "CSV export requirements",
  "engineering-practices": "testing review rollout and monitoring standards",
};
const sourceState = { sources: [], key: null, documents: new Map(), loadId: 0 };

function panelPath(target) {
  if (sourcePages.includes(target) && sourceState.key) return `/sources/${sourceState.key}/${target}`;
  if (target === "diy-agents") return diyPath();
  return panelPaths[target] || "/";
}

function activatePanel(target, updateHistory = false) {
  const button = $(`.nav-item[data-target="${target}"]`);
  const panel = $(`#${target}`);
  if (!panel) return;
  $$(".nav-item, .panel").forEach((item) => item.classList.remove("active"));
  if (button) button.classList.add("active");
  panel.classList.add("active");
  document.title = target === "diy-agents" && diyState.selected ? diyTitle() : defaultTitle;
  if (updateHistory) window.history.pushState({ target }, "", panelPath(target));
}

function parseLocation() {
  const sourceMatch = window.location.pathname.match(/^\/sources\/([^/]+)\/([^/]+)$/);
  if (sourceMatch && sourcePages.includes(sourceMatch[2])) return { target: sourceMatch[2], key: sourceMatch[1] };
  const target = Object.entries(panelPaths).find(([, path]) => path === window.location.pathname)?.[0] || "home";
  return { target, key: null };
}

function activePanelId() {
  return $(".panel.active")?.id;
}

async function activatePanelFromLocation() {
  const { target, key } = parseLocation();
  activatePanel(target);
  if (key && key !== sourceState.key) await selectSource(key);
}

$$('.nav-item').forEach((button) => button.addEventListener("click", () => activatePanel(button.dataset.target, true)));
window.addEventListener("popstate", activatePanelFromLocation);

function renderDocumentList(documents) {
  const list = $("#document-list");
  list.replaceChildren();
  for (const document of documents) {
    const link = element("a", "document-row");
    link.href = document.url;
    link.target = "_blank";
    link.rel = "noopener";
    const identity = element("div", "document-identity");
    identity.append(element("span", "file-type", "PDF"), element("strong", "", document.name));
    const metadata = element("span", "document-meta", formatBytes(document.size));
    if (document.lastModified) metadata.textContent += ` · ${new Date(document.lastModified).toLocaleDateString()}`;
    link.append(identity, metadata, element("span", "open-mark", "↗"));
    list.append(link);
  }
  if (!documents.length) list.append(element("div", "empty-state", "No PDF documents found."));

  const select = $("#document-select");
  select.replaceChildren(new Option("Select a document", ""));
  for (const document of documents) select.add(new Option(document.name, document.name));
}

async function loadSourceDocuments(key) {
  const loadId = ++sourceState.loadId;
  setLoading($("#document-list"), "Loading documents...");
  $("#document-select").replaceChildren(new Option("Loading documents...", ""));
  try {
    if (!sourceState.documents.has(key)) {
      const result = await api(`/api/sources/${encodeURIComponent(key)}/documents`);
      sourceState.documents.set(key, result.documents || []);
    }
    if (loadId === sourceState.loadId) renderDocumentList(sourceState.documents.get(key));
  } catch (error) {
    if (loadId !== sourceState.loadId) return;
    $("#document-list").replaceChildren(element("div", "empty-state error", error.message));
    toast(error.message);
  }
}

async function selectSource(key) {
  const previousKey = sourceState.key;
  const source = sourceState.sources.find((item) => item.key === key) || sourceState.sources[0];
  if (!source) return;
  sourceState.key = source.key;
  $$(".source-select").forEach((select) => { select.value = source.key; });
  $$(".source-description").forEach((description) => { description.textContent = source.description; });

  const query = $("#search-query");
  if (!query.value.trim() || query.value === exampleSearches[previousKey]) query.value = exampleSearches[source.key] || "";
  $("#chunk-summary").textContent = "";
  $("#chunk-list").replaceChildren(element("div", "empty-state", "Select a document to inspect its chunks."));
  $("#search-summary").textContent = "";
  $("#search-results").replaceChildren(element("div", "empty-state", "Search results will appear here."));

  const panel = activePanelId();
  if (sourcePages.includes(panel) && window.location.pathname !== panelPath(panel)) {
    window.history.replaceState({ target: panel }, "", panelPath(panel));
  }
  await loadSourceDocuments(source.key);
}

async function loadSources() {
  try {
    const result = await api("/api/sources");
    sourceState.sources = result.sources;
    $$(".source-select").forEach((select) => {
      select.replaceChildren(...result.sources.map((source) => new Option(source.name, source.key)));
      select.addEventListener("change", (event) => selectSource(event.target.value));
    });
    await selectSource(parseLocation().key || result.sources[0]?.key);
  } catch (error) {
    $("#document-list").replaceChildren(element("div", "empty-state error", error.message));
    toast(error.message);
  }
}

$("#document-select").addEventListener("change", async (event) => {
  const documentName = event.target.value;
  const list = $("#chunk-list");
  if (!documentName) {
    $("#chunk-summary").textContent = "";
    list.replaceChildren(element("div", "empty-state", "Select a document to inspect its chunks."));
    return;
  }
  setLoading(list, "Loading ordered chunks...");
  try {
    const result = await api(
      `/api/sources/${encodeURIComponent(sourceState.key)}/chunks?document=${encodeURIComponent(documentName)}&limit=500`,
    );
    $("#chunk-summary").textContent = `${result.chunks.length} chunks · ${result.index}`;
    list.replaceChildren(...result.chunks.map((chunk, index) => renderChunk(chunk, index + 1)));
  } catch (error) {
    list.replaceChildren(element("div", "empty-state error", error.message));
    toast(error.message);
  }
});

function renderChunk(chunk, position, includeScore = false) {
  const article = element("article", "chunk-card");
  const heading = element("div", "chunk-heading");
  heading.append(element("span", "chunk-number", String(position).padStart(2, "0")));
  const title = element("div");
  title.append(element("h3", "", chunk.title || "Untitled chunk"));
  const pages = [chunk.page_number_from, chunk.page_number_to].filter((value) => value !== null && value !== undefined);
  title.append(element("span", "chunk-pages", pages.length ? `Pages ${pages.join("–")}` : "Page unavailable"));
  heading.append(title);
  if (includeScore) {
    const scores = element("div", "score-list");
    const rerankerScore = chunk["@search.reranker_score"];
    const searchScore = chunk["@search.score"];
    if (rerankerScore !== undefined) scores.append(element("span", "score", `Reranker ${Number(rerankerScore).toFixed(3)}`));
    if (searchScore !== undefined) scores.append(element("span", "score score-secondary", `Search ${Number(searchScore).toFixed(3)}`));
    if (rerankerScore === undefined && searchScore === undefined) scores.append(element("span", "score", "Match"));
    heading.append(scores);
  }
  article.append(heading, element("div", "chunk-content", chunk.chunk || "No chunk text."));
  const metadata = element("dl", "chunk-metadata");
  for (const [label, value] of [
    ["chunk_id", chunk.chunk_id],
    ["page_number_from", chunk.page_number_from],
    ["page_number_to", chunk.page_number_to],
    ["image_path", chunk.image_path],
  ]) {
    metadata.append(element("dt", "", label), element("dd", "", value ?? "—"));
  }
  article.append(metadata);
  return article;
}

$("#search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = $("#search-query").value.trim();
  const results = $("#search-results");
  if (!query || !sourceState.key) return;
  setLoading(results, "Running hybrid search...");
  try {
    const result = await api(`/api/sources/${encodeURIComponent(sourceState.key)}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, limit: 10 }),
    });
    $("#search-summary").textContent = `${result.matches.length} matches · ${result.index} · hybrid semantic + vector`;
    results.replaceChildren(...result.matches.map((chunk, index) => renderChunk(chunk, index + 1, true)));
    if (!result.matches.length) results.append(element("div", "empty-state", "No matching chunks."));
  } catch (error) {
    results.replaceChildren(element("div", "empty-state error", error.message));
    toast(error.message);
  }
});

function configDetails(title, value) {
  const details = element("details", "config-details");
  details.append(element("summary", "", title));
  const pre = element("pre");
  pre.textContent = JSON.stringify(value, null, 2);
  details.append(pre);
  return details;
}

function sourceFacts(source) {
  const facts = element("dl", "config-facts source-facts");
  const parameters = source.searchIndexParameters || source.search_index_parameters || {};
  for (const [label, value] of [
    ["Name", source.name],
    ["Kind", source.kind || source["@odata.type"] || "source"],
    ["Description", source.description],
    ["Search index", parameters.searchIndexName || parameters.search_index_name],
    ["Semantic configuration", parameters.semanticConfigurationName || parameters.semantic_configuration_name],
    ["Search fields", parameters.searchFields?.map((field) => field.name).join(", ")],
    ["Source data fields", parameters.sourceDataFields?.map((field) => field.name).join(", ")],
  ]) {
    if (!value) continue;
    facts.append(element("dt", "", label), element("dd", "", value));
  }
  return facts;
}

async function loadConfiguration() {
  const container = $("#kb-configuration-details");
  setLoading(container, "Loading live resource definition...");
  try {
    const [result, sourceList] = await Promise.all([api("/api/knowledge-base"), api("/api/sources")]);
    const sources = new Map(result.knowledgeSources.map((source) => [source.name, source]));
    const displayNames = new Map(sourceList.sources.map((source) => [source.knowledgeSource, source.name]));
    const kb = result.knowledgeBase;
    const section = element("section", "configuration-item");
    section.append(element("h3", "", kb.name));
    const facts = element("dl", "config-facts");
    const model = kb.models?.[0]?.azureOpenAIParameters || kb.models?.[0]?.azure_open_ai_parameters || {};
    for (const [label, value] of [
      ["Description", kb.description],
      ["Output mode", kb.outputMode || kb.output_mode || "default"],
      ["Reranker threshold", kb.rerankerThreshold ?? kb.reranker_threshold],
      ["Reasoning", kb.retrievalReasoningEffort?.kind || kb.retrieval_reasoning_effort?.kind || "default"],
      ["Retrieval instructions", kb.retrievalInstructions || kb.retrieval_instructions],
      ["Model deployment", model.deploymentId || model.deployment_id || model.modelName || model.model_name],
    ]) {
      if (!value) continue;
      facts.append(element("dt", "", label), element("dd", "", value));
    }
    section.append(facts);
    const references = kb.knowledgeSources || kb.knowledge_sources || [];
    const list = element("div", "source-list");
    list.append(element("h3", "", `Knowledge sources · ${references.length}`));
    for (const reference of references) {
      const source = sources.get(reference.name) || reference;
      const row = element("div", "source-row");
      row.append(element("h4", "", displayNames.get(source.name) || source.name));
      row.append(sourceFacts(source));
      list.append(row);
    }
    section.append(list);
    container.replaceChildren(section);
  } catch (error) {
    container.replaceChildren(element("div", "empty-state error", error.message));
    toast(error.message);
  }
}

function parseExtractedRecords(result) {
  const text = result.response?.flatMap((message) => message.content || []).find((item) => item.type === "text")?.text;
  if (!text) return [];
  try {
    const records = JSON.parse(text);
    return Array.isArray(records) ? records : [records];
  } catch {
    return [{ content: text }];
  }
}

function activityLabel(activity) {
  const label = ({
    modelQueryPlanning: "Query planning",
    searchIndex: "Index search",
    agenticReasoning: "Result re-ranking",
    modelAnswerSynthesis: "Answer synthesis",
    diyToolCall: "Agent calls tool",
    "diy:search": "Search",
    "diy:query_planning": "Query planning",
    "diy:parallel_search": "Parallel search",
    "diy:llm_reranking": "LLM reranking",
    "diy:reranking": "Reranking",
    "diy:reflection": "Reflection",
    "diy:complexity_routing": "Complexity routing",
    "diy:retrieval_decision": "Retrieval decision",
    "diy:semantic_ranking": "Semantic ranking",
  })[activity.type] || activity.type || "Activity";
  return activity.round ? `${label} · round ${activity.round}` : label;
}

function appendActivityFact(container, label, value) {
  if (value === undefined || value === null || value === "") return;
  const row = element("div", "activity-fact");
  row.append(element("strong", "", `${label}:`), element("span", "", String(value)));
  container.append(row);
}

function createTokenMeter(activity) {
  const input = Math.max(Number(activity.inputTokens) || 0, 0);
  const output = Math.max(Number(activity.outputTokens) || 0, 0);
  const reasoning = Math.max(Number(activity.reasoningTokens) || 0, 0);
  const total = input + output + reasoning;
  if (!total) return null;

  const meter = element("div", "token-meter");
  const heading = element("div", "token-meter-heading");
  heading.append(element("span", "", "Token usage"), element("strong", "", total.toLocaleString()));
  meter.append(heading);

  const track = element("div", "token-track");
  for (const [kind, value] of [["input", input], ["output", output], ["reasoning", reasoning]]) {
    if (!value) continue;
    const segment = element("span", `token-segment token-${kind}`);
    segment.style.width = `${(value / total) * 100}%`;
    segment.title = `${kind[0].toUpperCase()}${kind.slice(1)}: ${value.toLocaleString()} tokens`;
    track.append(segment);
  }
  meter.append(track);

  const legend = element("div", "token-legend");
  for (const [kind, label, value] of [
    ["input", "Input", input],
    ["output", "Output", output],
    ["reasoning", "Re-ranker", reasoning],
  ]) {
    if (!value) continue;
    const item = element("span", `token-key token-${kind}`);
    item.append(element("i"), document.createTextNode(`${label} ${value.toLocaleString()}`));
    legend.append(item);
  }
  meter.append(legend);
  return meter;
}

function createActivityDetails(activity) {
  const details = element("div", "activity-details");
  const tokenMeter = createTokenMeter(activity);
  if (tokenMeter) details.append(tokenMeter);

  if (activity.type === "searchIndex") {
    appendActivityFact(details, "Source", activity.knowledgeSourceName || "Search index");
    appendActivityFact(details, "Search", activity.searchIndexArguments?.search || "—");
    appendActivityFact(details, "Results", activity.count);
  } else if (activity.type === "modelQueryPlanning" || activity.type === "modelAnswerSynthesis") {
    appendActivityFact(details, "Model", activity.modelName);
  } else if (activity.type?.startsWith("diy")) {
    appendDiyActivityDetails(details, activity);
  }

  if (activity.error?.message) details.append(element("div", "activity-error", activity.error.message));
  if (!activity.type?.startsWith("diy")) details.append(configDetails("View step data", activity));
  return details;
}

function renderActivityLog(activities) {
  const table = element("div", "activity-table");
  table.setAttribute("role", "table");
  const header = element("div", "activity-table-header");
  header.setAttribute("role", "row");
  for (const label of ["Step", "Details", "Elapsed"]) {
    const cell = element("span", "", label);
    cell.setAttribute("role", "columnheader");
    header.append(cell);
  }
  table.append(header);

  activities.forEach((activity, index) => {
    const row = element("div", "activity-row");
    row.setAttribute("role", "row");
    const step = element("div", "activity-step-name");
    step.append(element("span", "activity-step-number", `Step ${index + 1}`));
    step.append(element("strong", "", activityLabel(activity)));
    const elapsed = element("div", "activity-elapsed", activity.elapsedMs === undefined ? "—" : `${activity.elapsedMs.toLocaleString()} ms`);
    row.append(step, createActivityDetails(activity), elapsed);
    table.append(row);
  });
  return table;
}

function formatReferenceContent(sourceData) {
  const content = sourceData?.chunk ?? sourceData?.content;
  if (content === undefined || content === null) return "";
  if (typeof content !== "string") return JSON.stringify(content, null, 2);
  try {
    return JSON.stringify(JSON.parse(content), null, 2);
  } catch {
    return content;
  }
}

function renderKnowledgeBaseResult(container, result) {
  container.replaceChildren();
  const records = parseExtractedRecords(result);
  const evidence = element("section", "kb-section");
  evidence.append(element("h3", "", `Extracted evidence · ${records.length}`));
  const evidenceList = element("div", "evidence-list");
  records.forEach((record, index) => {
    const item = element("article", "evidence-item");
    item.append(element("span", "evidence-index", String(index + 1).padStart(2, "0")));
    const body = element("div");
    body.append(element("h4", "", record.title || `Evidence ${index + 1}`));
    body.append(element("div", "evidence-content", record.content || JSON.stringify(record, null, 2)));
    item.append(body);
    evidenceList.append(item);
  });
  evidence.append(evidenceList);

  const activitySection = element("section", "kb-section");
  activitySection.append(element("h3", "", `Activity log · ${(result.activity || []).length} steps`));
  activitySection.append(renderActivityLog(result.activity || []));

  const references = element("section", "kb-section");
  references.append(element("h3", "", `References · ${(result.references || []).length}`));
  const referenceList = element("div", "reference-list");
  for (const reference of result.references || []) {
    const item = element("article", "reference-item");
    item.append(element("strong", "", reference.title || reference.type || "Reference"));
    item.append(element("span", "", `Source step ${reference.activitySource ?? "—"}`));
    if (reference.rerankerScore !== undefined) item.append(element("span", "score", `Score ${reference.rerankerScore.toFixed(3)}`));
    const content = formatReferenceContent(reference.sourceData);
    if (content) item.append(element("p", "reference-excerpt", content));
    referenceList.append(item);
  }
  references.append(referenceList);
  container.append(evidence, activitySection, references);
}

$("#kb-search").addEventListener("submit", async (event) => {
  event.preventDefault();
  const output = $("#kb-output");
  const question = $("textarea", event.currentTarget).value.trim();
  if (!question) return;
  setLoading(output, "Retrieving from all six knowledge sources...");
  try {
    const result = await api("/api/retrieve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    renderKnowledgeBaseResult(output, result);
  } catch (error) {
    output.replaceChildren(element("div", "empty-state error", error.message));
    toast(error.message);
  }
});

function appendSearchTable(container, searches) {
  if (!searches?.length) return;
  const table = element("div", "diy-search-table");
  for (const search of searches) {
    const row = element("div", "diy-search-row");
    const indexes = search.indexes ? search.indexes.join(", ") : search.index;
    row.append(element("span", "diy-source", indexes || "—"));
    row.append(element("span", "diy-query", typeof search === "string" ? search : search.query));
    if (search.result_count !== undefined) row.append(element("span", "diy-count", `${search.result_count} results`));
    table.append(row);
  }
  container.append(table);
}

function appendDiyActivityDetails(details, activity) {
  appendActivityFact(details, "Model", activity.model);
  appendActivityFact(details, "Reasoning effort", activity.reasoning_effort);
  switch (activity.type) {
    case "diyToolCall":
      appendActivityFact(details, "Tool", activity.name);
      for (const [name, value] of Object.entries(activity.arguments || {})) appendActivityFact(details, name, value);
      if (activity.error) details.append(element("div", "activity-error", activity.error));
      break;
    case "diy:search":
      appendActivityFact(details, "Index", activity.index);
      appendActivityFact(details, "Query", activity.query);
      appendActivityFact(details, "Results", activity.result_count);
      break;
    case "diy:query_planning":
      appendSearchTable(details, activity.searches || activity.queries);
      break;
    case "diy:parallel_search":
      appendActivityFact(details, "Searches", activity.searches?.length);
      appendSearchTable(details, activity.searches);
      break;
    case "diy:llm_reranking":
    case "diy:reranking":
      appendActivityFact(details, "Kept", `${activity.kept_count} of ${activity.candidate_count} results`);
      break;
    case "diy:reflection":
      appendActivityFact(details, "Sufficient", activity.sufficient ? "Yes" : "No");
      appendActivityFact(details, "Missing", activity.missing);
      appendSearchTable(details, activity.follow_up_searches);
      break;
    case "diy:complexity_routing":
      appendActivityFact(details, "Route", activity.route);
      appendActivityFact(details, "Agentic probability", activity.agentic_probability);
      appendActivityFact(details, "Reason", activity.reason);
      break;
    case "diy:retrieval_decision":
      appendActivityFact(details, "Decision", activity.decision);
      break;
    case "diy:semantic_ranking":
      appendActivityFact(details, "Ranking tokens", activity.ranking_tokens?.toLocaleString());
      break;
  }
}

function normalizeDiyActivity(toolCalls) {
  return toolCalls.flatMap((call) => [
    { type: "diyToolCall", name: call.name, arguments: call.arguments, error: call.error },
    ...(call.activity || []).map((entry) => ({
      ...entry,
      type: `diy:${entry.step}`,
      elapsedMs: entry.elapsed_ms,
      startedAtMs: entry.started_at_ms,
      inputTokens: entry.input_tokens,
      outputTokens: entry.output_tokens,
    })),
  ]);
}

function citationLink(name) {
  const link = element("a", "diy-citation", name);
  link.href = `/api/diy/documents/${encodeURIComponent(name)}`;
  link.target = "_blank";
  link.rel = "noopener";
  return link;
}

// Agents cite documents as [name.pdf], [a.pdf] [b.pdf], or [a.pdf; b.pdf].
function appendTextWithCitations(container, text) {
  let position = 0;
  for (const match of text.matchAll(/\[([^[\]]*\.pdf[^[\]]*)\]/gi)) {
    container.append(document.createTextNode(text.slice(position, match.index)));
    const names = match[1].split(/[;,]/).map((name) => name.trim()).filter(Boolean);
    container.append(document.createTextNode("["));
    names.forEach((name, index) => {
      if (index) container.append(document.createTextNode("; "));
      container.append(name.toLowerCase().endsWith(".pdf") ? citationLink(name) : document.createTextNode(name));
    });
    container.append(document.createTextNode("]"));
    position = match.index + match[0].length;
  }
  container.append(document.createTextNode(text.slice(position)));
}

function appendInlineMarkdown(container, text) {
  text.split(/(\*\*[^*]+\*\*)/).forEach((part) => {
    if (part.startsWith("**") && part.endsWith("**")) container.append(element("strong", "", part.slice(2, -2)));
    else if (part) appendTextWithCitations(container, part);
  });
}

function renderAnswer(text) {
  const answer = element("div", "diy-answer");
  for (const block of text.split(/\n{2,}/)) {
    const lines = block.split("\n").filter((line) => line.trim());
    if (!lines.length) continue;
    if (lines.every((line) => /^\s*[-*] /.test(line))) {
      const list = element("ul");
      for (const line of lines) {
        const item = element("li");
        appendInlineMarkdown(item, line.replace(/^\s*[-*] /, ""));
        list.append(item);
      }
      answer.append(list);
    } else {
      const paragraph = element("p");
      appendInlineMarkdown(paragraph, lines.join(" "));
      answer.append(paragraph);
    }
  }
  return answer;
}

function diyMetric(label, value) {
  const metric = element("div", "diy-metric");
  metric.append(element("span", "", label), element("strong", "", value));
  return metric;
}

function formatDuration(ms) {
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

// Gaps this long between retrieval steps are the agent's own model calls, not retrieval work.
const AGENT_GAP_MS = 250;

function waterfallSegments(result, activities) {
  const timed = activities.filter((activity) => Number.isFinite(activity.elapsedMs));
  const kindOf = (activity) => (activity.inputTokens ? "llm" : "search");
  const hasStarts = Number.isFinite(result.startedAtMs) && timed.every((activity) => Number.isFinite(activity.startedAtMs));
  if (!hasStarts) {
    let cursor = 0;
    return timed.map((activity) => {
      const segment = { label: activityLabel(activity), start: cursor, duration: activity.elapsedMs, kind: kindOf(activity) };
      cursor += activity.elapsedMs;
      return segment;
    });
  }

  const segments = [];
  let cursor = 0;
  timed.forEach((activity) => {
    const start = Math.max(0, activity.startedAtMs - result.startedAtMs);
    if (start - cursor > AGENT_GAP_MS) {
      const label = segments.length ? "Agent model: decides to search again" : "Agent model: decides to search";
      segments.push({ label, start: cursor, duration: start - cursor, kind: "agent" });
    }
    segments.push({ label: activityLabel(activity), start, duration: activity.elapsedMs, kind: kindOf(activity) });
    cursor = Math.max(cursor, start + activity.elapsedMs);
  });
  if (result.elapsedMs - cursor > AGENT_GAP_MS) {
    segments.push({ label: "Agent model: writes answer", start: cursor, duration: result.elapsedMs - cursor, kind: "agent" });
  }
  return segments;
}

function renderWaterfall(result, activities) {
  const segments = waterfallSegments(result, activities);
  if (!segments.length) return null;
  const span = Math.max(result.elapsedMs || 0, ...segments.map((segment) => segment.start + segment.duration));

  const chart = element("div", "waterfall");
  chart.setAttribute("aria-label", "Latency waterfall");
  const heading = element("div", "waterfall-heading");
  heading.append(element("strong", "", "Latency waterfall"), element("span", "", `${formatDuration(span)} total`));
  chart.append(heading);

  for (const segment of segments) {
    const row = element("div", `waterfall-row waterfall-${segment.kind}`);
    const track = element("div", "waterfall-track");
    const bar = element("span", "waterfall-bar");
    bar.style.left = `${(segment.start / span) * 100}%`;
    bar.style.width = `${(segment.duration / span) * 100}%`;
    bar.title = `${segment.label}: starts at ${formatDuration(segment.start)}, takes ${formatDuration(segment.duration)}`;
    track.append(bar);
    row.append(element("span", "waterfall-label", segment.label), track, element("span", "waterfall-duration", formatDuration(segment.duration)));
    chart.append(row);
  }

  const legend = element("div", "waterfall-legend");
  for (const [kind, label] of [["search", "Search"], ["llm", "Model call in retrieval"], ["agent", "Agent model"]]) {
    if (!segments.some((segment) => segment.kind === kind)) continue;
    const item = element("span", `waterfall-key waterfall-${kind}`);
    item.append(element("i"), document.createTextNode(label));
    legend.append(item);
  }
  chart.append(legend);
  return chart;
}

function renderDiyResult(container, result) {
  const activities = normalizeDiyActivity(result.toolCalls || []);
  const llmSteps = activities.filter((activity) => activity.inputTokens);
  const retrievalTokens = llmSteps.reduce((sum, activity) => sum + activity.inputTokens + (activity.outputTokens || 0), 0);
  const searchCount = activities.reduce((sum, activity) => {
    if (activity.type === "diy:search") return sum + 1;
    if (activity.type === "diy:parallel_search") return sum + (activity.searches?.length || 0);
    return sum;
  }, 0);
  const agentTokens = (result.agentUsage?.inputTokens || 0) + (result.agentUsage?.outputTokens || 0);

  const metrics = element("div", "diy-metrics");
  metrics.append(
    diyMetric("Total time", `${(result.elapsedMs / 1000).toFixed(1)} s`),
    diyMetric("Searches", String(searchCount)),
    diyMetric("Model calls in retrieval", String(llmSteps.length)),
    diyMetric("Retrieval tokens", retrievalTokens.toLocaleString()),
    diyMetric("Agent tokens", agentTokens.toLocaleString()),
  );

  const answer = element("section", "kb-section");
  answer.append(element("h3", "", "Answer"), renderAnswer(result.answer || "The agent returned no answer."));

  const activitySection = element("section", "kb-section");
  activitySection.append(element("h3", "", `Activity log · ${activities.length} steps`));
  const waterfall = renderWaterfall(result, activities);
  if (waterfall) activitySection.append(waterfall);
  activitySection.append(renderActivityLog(activities));

  const evidence = (result.toolCalls || []).flatMap((call) => call.evidence || []);
  const evidenceSection = element("section", "kb-section");
  evidenceSection.append(element("h3", "", `Evidence returned to the agent · ${evidence.length}`));
  const evidenceList = element("div", "reference-list");
  for (const item of evidence) {
    const row = element("article", "reference-item");
    const title = element("div", "diy-evidence-title");
    title.append(element("strong", "", item.title || "Untitled"));
    if (item.index) title.append(element("span", "resource-kind", item.index));
    row.append(title);
    const relevance = item.relevance ?? item.llm_relevance;
    if (relevance !== undefined) {
      const shown = Number.isInteger(relevance) ? relevance : Number(relevance).toFixed(1);
      row.append(element("span", "score", `Relevance ${shown}/3`));
    }
    if (item.reranker_score !== undefined) row.append(element("span", "score score-secondary", `Reranker ${Number(item.reranker_score).toFixed(2)}`));
    const content = element("details", "config-details diy-evidence-content");
    content.append(element("summary", "", "Show chunk"), element("p", "reference-excerpt", item.content || ""));
    row.append(content);
    evidenceList.append(row);
  }
  evidenceSection.append(evidenceList);

  container.replaceChildren(metrics, answer, activitySection, evidenceSection);
}

const diyState = { steps: [], selected: null, autorun: false };

// The DIY page URL carries ?agent=...&question=... so each demo can live in its own browser tab.
// Demo links add &run=1 to ask the question as soon as the tab loads.
function diyPath() {
  const params = new URLSearchParams();
  if (diyState.selected) params.set("agent", diyState.selected);
  const question = $("#diy-question").value.trim();
  if (question) params.set("question", question);
  const query = params.toString();
  return query ? `${panelPaths["diy-agents"]}?${query}` : panelPaths["diy-agents"];
}

function demoPath(step, example) {
  return `${panelPaths["diy-agents"]}?${new URLSearchParams({ agent: step.name, question: example.question, run: "1" })}`;
}

function diyTitle() {
  const index = diyState.steps.findIndex((step) => step.name === diyState.selected);
  if (index < 0) return defaultTitle;
  const step = diyState.steps[index];
  const example = step.examples.find((item) => item.question === $("#diy-question").value.trim());
  return [`${index + 1}`, step.title, example?.label].filter(Boolean).join(" · ");
}

function syncDiyUrl() {
  if (activePanelId() !== "diy-agents") return;
  document.title = diyTitle();
  const path = diyPath();
  if (`${window.location.pathname}${window.location.search}` !== path) {
    window.history.replaceState({ target: "diy-agents" }, "", path);
  }
}

function requestedDiyStep() {
  if (window.location.pathname !== panelPaths["diy-agents"]) return null;
  const params = new URLSearchParams(window.location.search);
  const question = params.get("question");
  if (question) $("#diy-question").value = question;
  diyState.autorun = params.get("run") === "1";
  return params.get("agent");
}

function selectDiyStep(name) {
  const step = diyState.steps.find((item) => item.name === name);
  if (!step) return;
  diyState.selected = name;
  $("#diy-agent").value = name;
  $("#diy-capability").textContent = step.capability;
  $("#diy-hint").hidden = step.running;
  const question = $("#diy-question");
  if (!question.value.trim()) question.value = step.examples[0]?.question || "";
  syncDiyUrl();
}

function renderDemoList(demos) {
  const list = element("ol", "demo-items");
  for (const demo of demos) {
    const step = diyState.steps.find((item) => item.name === demo.step);
    const link = element("a");
    link.href = demoPath(step, demo);
    link.target = "_blank";
    link.append(element("strong", "", demo.slide), element("em", "", `${step.title} · ${demo.label}`), element("span", "", demo.question));
    const item = element("li");
    item.append(link);
    list.append(item);
  }
  $("#demo-list").replaceChildren(list);
}

async function loadDiySteps() {
  const select = $("#diy-agent");
  try {
    const result = await api("/api/diy/agents");
    diyState.steps = result.steps;
    select.replaceChildren(...result.steps.map((step, index) => {
      const label = `${index + 1} · ${step.title}${step.running ? "" : " (not running)"}`;
      return new Option(label, step.name);
    }));
    renderDemoList(result.demos || []);
    const requested = diyState.selected ? null : requestedDiyStep();
    const initial = result.steps.find((step) => step.name === requested)?.name || result.steps[0]?.name;
    selectDiyStep(diyState.selected || initial);
    if (diyState.autorun && activePanelId() === "diy-agents") {
      diyState.autorun = false;
      $("#diy-form").requestSubmit();
    }
  } catch (error) {
    $("#diy-output").replaceChildren(element("div", "empty-state error", error.message));
  }
}

$("#diy-refresh").addEventListener("click", loadDiySteps);
$("#diy-agent").addEventListener("change", (event) => selectDiyStep(event.target.value));
$("#diy-question").addEventListener("input", syncDiyUrl);
$(".nav-item[data-target='diy-agents']").addEventListener("click", loadDiySteps);

$("#diy-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const output = $("#diy-output");
  const question = $("#diy-question").value.trim();
  const step = diyState.steps.find((item) => item.name === diyState.selected);
  if (!question || !step) return;
  setLoading(output, `Asking ${step.name}... multi-step agents can take a minute.`);
  try {
    const result = await api("/api/diy/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ step: step.name, question }),
    });
    renderDiyResult(output, result);
  } catch (error) {
    output.replaceChildren(element("div", "empty-state error", error.message));
    toast(error.message);
    loadDiySteps();
  }
});

activatePanel(parseLocation().target);
Promise.all([loadSources(), loadConfiguration(), loadDiySteps()]).catch((error) => toast(error.message));
