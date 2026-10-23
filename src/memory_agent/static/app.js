"use strict";

(() => {
  const $ = (selector, context = document) => context.querySelector(selector);
  const $$ = (selector, context = document) => Array.from(context.querySelectorAll(selector));
  const state = {
    view: "overview", memories: [], proposals: [], events: [], stats: {}, learning: {},
    currentOnly: false, kind: "", proposalStatus: "pending", search: null,
    relevant: new Set(), benchmark: null, editing: null, connected: false,
  };
  const viewNames = { overview: "Overview", memories: "Memory library", evolution: "Evolution", activity: "Evidence & activity", benchmark: "Evaluation lab" };
  const kindIcons = { fact: "◇", preference: "♡", event: "◷", procedure: "↗", note: "▤" };
  const proposalIcons = { duplicate: "≋", deduplicate: "≋", contradiction: "⇄", temporal_conflict: "⇄", reclassify: "↗", reorganize: "⊞", split: "⊞" };
  const nice = value => String(value ?? "").replace(/[_-]+/g, " ").replace(/\b\w/g, c => c.toUpperCase());
  const shortId = value => String(value || "").slice(0, 12);
  const display = value => value === null || value === undefined || value === "" ? "—" : typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);

  function node(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined) result.textContent = String(text);
    return result;
  }

  function button(label, className, action) {
    const result = node("button", className, label);
    result.type = "button";
    if (action) result.addEventListener("click", () => action(result));
    return result;
  }

  function pill(text, kind = "") {
    const result = node("span", "pill", text);
    if (/^[a-z_]+$/.test(String(kind))) result.classList.add(kind);
    return result;
  }

  function date(value, includeTime = false) {
    if (!value) return "Not set";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return new Intl.DateTimeFormat(undefined, {
      month: "short", day: "numeric", year: "numeric", timeZone: "UTC",
      ...(includeTime ? { hour: "2-digit", minute: "2-digit" } : {}),
    }).format(parsed) + (includeTime ? " UTC" : "");
  }

  function relativeDate(value) {
    if (!value) return "";
    const milliseconds = Date.now() - new Date(value).getTime();
    if (!Number.isFinite(milliseconds)) return String(value);
    const minutes = Math.floor(milliseconds / 60000);
    if (minutes < 1) return "Just now";
    if (minutes < 60) return `${minutes} min ago`;
    if (minutes < 1440) return `${Math.floor(minutes / 60)} hr ago`;
    return date(value);
  }

  function toast(message, error = false) {
    const item = node("div", `toast${error ? " error" : ""}`, message);
    $("#toast-region").append(item);
    window.setTimeout(() => item.remove(), error ? 8500 : 5000);
  }

  async function request(path, body) {
    const response = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? { Accept: "application/json" } : { Accept: "application/json", "Content-Type": "application/json" },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      credentials: "same-origin",
    });
    let data;
    try { data = await response.json(); } catch { throw new Error(`The server returned an unreadable response (${response.status}).`); }
    if (!response.ok) throw new Error(typeof data.error === "string" ? data.error : data.detail || `Request failed (${response.status}).`);
    return data;
  }

  async function busy(control, work) {
    if (control && control.disabled) return;
    if (control) { control.disabled = true; control.setAttribute("aria-busy", "true"); }
    try { return await work(); }
    catch (error) { toast(error.message || "Something went wrong. Please try again.", true); }
    finally { if (control) { control.disabled = false; control.removeAttribute("aria-busy"); } }
  }

  function empty(title, message, action) {
    const box = node("div", "empty-state");
    box.append(node("span", "empty-symbol", "✳"), node("h3", "", title), node("p", "", message));
    if (action) box.append(button(action.label, "button button-small button-white", action.run));
    return box;
  }

  function currentMemory(memory) {
    const now = Date.now();
    return memory.status === "active" && (!memory.valid_from || new Date(memory.valid_from).getTime() <= now)
      && (!memory.valid_until || new Date(memory.valid_until).getTime() > now);
  }

  async function refresh() {
    const [overview, memories] = await Promise.all([
      request("/api/overview"), request("/api/memories?include_inactive=true"),
    ]);
    state.memories = Array.isArray(memories.memories) ? memories.memories : [];
    state.stats = overview.stats || {};
    state.learning = overview.learning || {};
    state.proposals = Array.isArray(overview.proposals) ? overview.proposals : (await request("/api/proposals")).proposals || [];
    state.events = Array.isArray(overview.events) ? overview.events : (await request("/api/events")).events || [];
    if (state.view === "activity") state.events = (await request("/api/events")).events || [];
    state.connected = true;
    $("#connection-label").replaceChildren(node("span", "status-dot"), document.createTextNode("Local & connected"));
    renderAll();
  }

  function navigate(view) {
    if (!viewNames[view]) view = "overview";
    state.view = view;
    $$(".view").forEach(item => { item.hidden = item.id !== `view-${view}`; });
    $$(".nav-item").forEach(item => {
      const active = item.dataset.view === view;
      item.classList.toggle("selected", active);
      if (active) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current");
    });
    $("#breadcrumb-view").textContent = viewNames[view];
    if (location.hash !== `#${view}`) history.replaceState(null, "", `#${view}`);
    if (view === "activity" && state.connected) request("/api/events").then(result => {
      state.events = result.events || []; renderEvents();
    }).catch(error => toast(error.message, true));
  }

  function renderAll() {
    $("#nav-memory-count").textContent = String(state.memories.length);
    $("#nav-proposal-count").textContent = String(state.proposals.filter(item => item.status === "pending").length);
    renderOverview(); renderMemories(); renderProposals(); renderEvents(); renderBenchmark();
  }

  function metric(label, value, detail, icon) {
    const card = node("div", "metric");
    const heading = node("div", "metric-label");
    heading.append(document.createTextNode(label), node("span", "metric-icon", icon));
    card.append(heading, node("strong", "metric-value", value), node("span", "metric-detail", detail));
    return card;
  }

  function renderOverview() {
    const current = state.memories.filter(currentMemory);
    const pending = state.proposals.filter(item => item.status === "pending");
    const sources = new Set(state.memories.map(item => item.source_id).filter(Boolean));
    const feedback = state.learning.feedback_count ?? state.learning.feedback_events ?? state.learning.updates ?? 0;
    $("#overview-metrics").replaceChildren(
      metric("Memories collected", state.memories.length, `${current.length} current · ${state.memories.length - current.length} historical or inactive`, "▤"),
      metric("Original sources", sources.size, "Evidence preserved alongside facts", "◇"),
      metric("Suggested improvements", pending.length, pending.length ? "Awaiting your review" : "Run a review to find opportunities", "✳"),
      metric("Feedback received", feedback, "Relevance feedback improves ranking", "↗"),
    );
    const topicMap = new Map();
    state.memories.forEach(item => topicMap.set(item.topic || "inbox", (topicMap.get(item.topic || "inbox") || 0) + 1));
    const topics = Array.from(topicMap).sort((a, b) => b[1] - a[1]).slice(0, 5);
    const topicList = $("#topic-list"); topicList.replaceChildren();
    if (!topics.length) topicList.append(empty("A clean page.", "Add your first memory or explore the synthetic demo to see the workspace come to life."));
    topics.forEach(([name, count]) => {
      const row = node("div", "topic-row");
      const bar = node("span", "topic-bar");
      const fill = node("span"); fill.style.width = `${Math.max(4, count / Math.max(1, state.memories.length) * 100)}%`; bar.append(fill);
      row.append(node("span", "topic-symbol", "▱"), node("span", "topic-name", name), bar, node("span", "topic-count", `${count} ${count === 1 ? "memory" : "memories"}`));
      topicList.append(row);
    });
    $("#review-count").textContent = `${pending.length} pending`;
    const list = $("#overview-proposals"); list.replaceChildren();
    if (!pending.length) list.append(empty("A moment of reflection.", "Let the agent look for repeated facts, conflicting information, and opportunities to organize.", { label: "Run a memory review", run: runReview }));
    pending.slice(0, 3).forEach(item => {
      const row = node("div", "proposal-preview");
      const copy = node("div", "proposal-preview-text");
      copy.append(node("strong", "", item.title || nice(item.kind)), node("p", "", `${nice(item.kind)} · ${item.changes?.length || 0} ${item.changes?.length === 1 ? "change" : "changes"}`));
      row.append(node("span", "proposal-preview-symbol", proposalIcons[item.kind] || "✳"), copy, button("Review ↗", "text-link", () => showProposal(item)));
      list.append(row);
    });
    renderEventList($("#recent-events"), state.events.slice(0, 4), true);
  }

  function renderMemories() {
    const target = $("#memory-list"); target.replaceChildren();
    let items = state.search ? state.search.results || [] : state.memories.map(memory => ({ memory }));
    if (state.currentOnly && !state.search) items = items.filter(item => currentMemory(item.memory));
    if (state.kind) items = items.filter(item => item.memory.kind === state.kind);
    $("#memory-result-count").textContent = `${items.length} ${state.search ? "results" : "memories"}`;
    $("#search-feedback").hidden = !state.search || !state.search.retrieval_id || !items.length;
    $("#all-memories-button").classList.toggle("selected", !state.currentOnly && !state.search);
    $("#current-memories-button").classList.toggle("selected", state.currentOnly && !state.search);
    if (!items.length) {
      const box = empty(state.search ? "No memories found." : "Make room for something useful.", state.search ? "Try a different query or remove the historical date. Recall only uses information available at the selected time." : "Capture facts, preferences, and moments you want your agent to carry forward.", state.search ? undefined : { label: "Add your first memory", run: () => openMemoryForm() });
      box.classList.add("empty-library"); target.append(box); return;
    }
    items.forEach(item => {
      const memory = item.memory;
      const card = node("article", "memory-card");
      const heading = node("div", "memory-card-top");
      const badges = node("div"); badges.append(pill(`${kindIcons[memory.kind] || "▤"} ${nice(memory.kind)}`, memory.kind));
      if (memory.status !== "active") { badges.append(document.createTextNode(" "), pill(nice(memory.status), memory.status)); }
      heading.append(badges, node("span", "memory-topic", memory.topic));
      card.append(heading, node("p", "memory-content", memory.content));
      if (memory.subject || memory.predicate || memory.value) {
        const fact = node("div", "memory-structured");
        fact.append(node("strong", "", memory.subject || ""), document.createTextNode(` / ${memory.predicate || ""} → ${display(memory.value)}`));
        card.append(fact);
      }
      const footer = node("div", "memory-card-footer");
      const timing = node("span", "memory-date", `From ${date(memory.valid_from)}`);
      if (memory.valid_until) timing.append(node("br"), document.createTextNode(`Until ${date(memory.valid_until)} (exclusive)`));
      const actions = node("div", "memory-card-actions");
      if (state.search) {
        const relevant = button(state.relevant.has(memory.id) ? "✓ Relevant" : "+ Relevant", "feedback-button", control => {
          if (state.relevant.has(memory.id)) state.relevant.delete(memory.id); else state.relevant.add(memory.id);
          control.setAttribute("aria-pressed", String(state.relevant.has(memory.id)));
          control.textContent = state.relevant.has(memory.id) ? "✓ Relevant" : "+ Relevant";
        });
        relevant.setAttribute("aria-pressed", String(state.relevant.has(memory.id)));
        relevant.setAttribute("aria-label", `Mark memory ${shortId(memory.id)} as relevant`);
        actions.append(relevant);
      }
      actions.append(button("View evidence ↗", "text-link", () => showMemory(memory)));
      footer.append(timing, actions); card.append(footer);
      if (Number.isFinite(item.score)) card.append(node("div", "score-line", `Retrieval score ${item.score.toFixed(3)} · ${formatSignals(item.signals)}`));
      target.append(card);
    });
  }

  function formatSignals(signals) {
    if (!signals || typeof signals !== "object") return "Ranked by the local retrieval engine";
    return Object.entries(signals).filter(([, value]) => typeof value === "number").map(([key, value]) => `${key.replace(/_/g, " ")} ${value.toFixed(2)}`).join(" · ") || "Ranked by the local retrieval engine";
  }

  async function search(control) {
    await busy(control, async () => {
      const query = $("#search-query").value.trim();
      if (!query) return;
      const body = { query, limit: 20 };
      const asOf = $("#search-date").value;
      if (asOf) body.as_of = asOf;
      state.search = await request("/api/search", body);
      state.relevant.clear(); renderMemories();
    });
  }

  function renderProposals() {
    const items = state.proposals.filter(item => state.proposalStatus === "all" || item.status === state.proposalStatus);
    const target = $("#proposal-list"); target.replaceChildren();
    $("#proposal-result-count").textContent = `${items.length} ${items.length === 1 ? "proposal" : "proposals"}`;
    if (!items.length) {
      target.append(empty(state.proposalStatus === "pending" ? "Everything has room to grow." : "No changes here yet.", state.proposalStatus === "pending" ? "Run a review to find evidence-backed opportunities. You decide which changes to make." : "Changes will appear here after you review and apply a proposal.")); return;
    }
    items.forEach(item => {
      const card = node("article", "proposal-card");
      const main = node("div", "proposal-main");
      const content = node("div", "proposal-content");
      const labels = node("div", "proposal-labels");
      labels.append(pill(nice(item.kind)), pill(`${nice(item.risk || "low")} risk`, item.risk || "low"), pill(nice(item.status), item.status));
      content.append(labels, node("h2", "proposal-title", item.title || nice(item.kind)), node("p", "proposal-reason", item.reason));
      main.append(node("div", "proposal-icon", proposalIcons[item.kind] || "✳"), content);
      const bottom = node("div", "proposal-bottom");
      const changes = item.changes || [];
      const actions = node("div", "proposal-actions");
      if (item.status === "pending") actions.append(button("Dismiss", "button button-small button-white", control => actOnProposal(item, "reject", control)));
      actions.append(button(item.status === "applied" ? "Inspect & undo ↗" : changes.length ? "Review changes ↗" : "Inspect conflict ↗", "button button-small button-primary", () => showProposal(item)));
      bottom.append(node("span", "proposal-meta", `${changes.length ? `${changes.length} ${changes.length === 1 ? "memory" : "memories"} affected` : "Needs a manual correction"} · ${date(item.created_at)}`), actions);
      card.append(main, bottom); target.append(card);
    });
  }

  async function runReview(control) {
    await busy(control, async () => {
      const response = await request("/api/review", {});
      await refresh(); navigate("evolution");
      state.proposalStatus = "pending";
      $$("#proposal-filters button").forEach(item => item.classList.toggle("selected", item.dataset.status === "pending"));
      renderProposals();
      const count = Array.isArray(response.proposals) ? response.proposals.filter(item => item.status === "pending").length : state.proposals.filter(item => item.status === "pending").length;
      toast(count ? `Review complete. ${count} ${count === 1 ? "proposal is" : "proposals are"} ready to inspect.` : "Review complete. No new improvements were identified.");
    });
  }

  async function actOnProposal(proposal, action, control) {
    await busy(control, async () => {
      await request(`/api/proposals/${encodeURIComponent(proposal.id)}/${action}`, {});
      $("#detail-dialog").close();
      state.search = null;
      await refresh();
      toast(action === "apply" ? "Change applied. The original evidence is preserved." : action === "undo" ? "Change undone. The previous memory state is restored." : "Proposal dismissed.");
    });
  }

  function showDetail(title, eyebrow) {
    $("#detail-title").textContent = title;
    $("#detail-eyebrow").textContent = eyebrow;
    $("#detail-content").replaceChildren();
    $("#detail-actions").replaceChildren(button("Close", "button button-white", () => $("#detail-dialog").close()));
    if (!$("#detail-dialog").open) $("#detail-dialog").showModal();
  }

  function fields(data) {
    const list = node("dl", "detail-data");
    Object.entries(data).forEach(([key, value]) => list.append(node("dt", "", key), node("dd", "", display(value))));
    return list;
  }

  async function showMemory(memory) {
    showDetail("The story behind the memory", "MEMORY & SOURCE EVIDENCE");
    const target = $("#detail-content");
    target.append(node("p", "detail-body-text", memory.content), fields({
      "Memory ID": memory.id, Kind: nice(memory.kind), Topic: memory.topic, Status: nice(memory.status),
      "Valid from": date(memory.valid_from, true), "Valid until": memory.valid_until ? `${date(memory.valid_until, true)} (exclusive)` : "Open ended",
      Confidence: memory.confidence, Importance: memory.importance, Version: memory.version,
      ...(memory.subject ? { Subject: memory.subject, Attribute: memory.predicate, Value: memory.value } : {}),
    }));
    const actions = $("#detail-actions");
    actions.append(button("Edit memory", "button button-primary", () => { $("#detail-dialog").close(); openMemoryForm(memory); }));
    const evidence = node("section", "detail-section");
    evidence.append(node("h3", "", "Original evidence"));
    const loading = node("p", "muted", "Loading source evidence…"); evidence.append(loading); target.append(evidence);
    if (!memory.source_id) { loading.textContent = "No source identifier was recorded for this memory."; return; }
    try {
      const result = await request(`/api/sources/${encodeURIComponent(memory.source_id)}`);
      const source = result.source || result;
      loading.remove();
      evidence.append(fields({ Source: source.label || source.source || "Source evidence", "Source ID": source.id || memory.source_id, Recorded: date(source.created_at, true) }), node("pre", "evidence-text", source.text ?? source.content ?? "No source text is available."));
    } catch (error) { loading.textContent = error.message; loading.classList.add("form-error"); }
  }

  function beforeFor(proposal, id) {
    const before = proposal.before || proposal.snapshots || [];
    if (Array.isArray(before)) return before.find(item => item.id === id) || {};
    return before[id] || {};
  }

  function showProposal(proposal) {
    showDetail(proposal.title || nice(proposal.kind), "INSPECT THE PROPOSED CHANGE");
    const target = $("#detail-content");
    target.append(node("p", "detail-body-text", proposal.reason), fields({ Kind: nice(proposal.kind), Risk: nice(proposal.risk || "low"), Status: nice(proposal.status), "Proposal ID": proposal.id }));
    const section = node("section", "detail-section");
    section.append(node("h3", "", "What will change"));
    const changes = proposal.changes || [];
    if (!changes.length) {
      section.append(node("p", "field-help", "This conflict cannot be resolved automatically. Inspect the source evidence, then edit the relevant memory’s content, dates, or active status in the memory library. Run a new review when the correction is complete."));
      const before = proposal.before || {};
      const conflicting = Array.isArray(before) ? before : Object.values(before);
      conflicting.forEach(snapshot => {
        const memory = state.memories.find(item => item.id === snapshot.id) || snapshot;
        const entry = node("div", "change-entry");
        entry.append(node("h3", "", `${memory.id} · ${nice(memory.status || "active")}`));
        const body = node("div", "change-fields");
        body.append(node("p", "detail-body-text", memory.content), fields({ Subject: memory.subject, Attribute: memory.predicate, Value: memory.value, "Valid from": date(memory.valid_from, true) }), button("Inspect source & correct ↗", "button button-small button-white", () => showMemory(memory)));
        entry.append(body); section.append(entry);
      });
      section.append(button("Open memory library →", "button button-small button-white", () => { $("#detail-dialog").close(); navigate("memories"); state.search = null; renderMemories(); }));
    }
    changes.forEach(change => {
      const item = node("div", "change-entry");
      const current = state.memories.find(memory => memory.id === change.id) || {};
      const before = { ...current, ...beforeFor(proposal, change.id) };
      item.append(node("h3", "", `${change.id} · expected version ${change.expected_version ?? "—"}`));
      const values = node("div", "change-fields");
      Object.entries(change.changes || {}).forEach(([key, value]) => {
        const row = node("div", "change-line");
        const transition = node("div");
        transition.append(node("div", "change-before", display(before[key])), node("div", "change-after", `→ ${display(value)}`));
        row.append(node("span", "change-key", nice(key)), transition); values.append(row);
      });
      if (current.content) values.prepend(node("p", "field-help", current.content));
      if (current.id) values.append(button("Inspect source ↗", "text-link", () => showMemory(current)));
      item.append(values); section.append(item);
    });
    target.append(section);
    const actions = $("#detail-actions");
    if (proposal.status === "pending") {
      actions.append(button("Dismiss", "button button-white", control => actOnProposal(proposal, "reject", control)));
      if (changes.length) actions.append(button("Apply this change", "button button-primary", control => actOnProposal(proposal, "apply", control)));
    } else if (proposal.status === "applied") {
      target.append(node("p", "field-help", "Undo restores the previous fields only if no subsequent edit has changed the affected memories. Evidence and audit history remain available."));
      actions.append(button("Undo this change", "button button-danger", control => actOnProposal(proposal, "undo", control)));
    }
  }

  function renderEventList(target, events, compact = false) {
    target.replaceChildren();
    if (!events.length) { target.append(empty("The story starts here.", "Added memories, approved changes, and reversals will leave a trace here.")); return; }
    events.forEach(event => {
      const row = node("div", "event-row");
      const type = event.event_type || event.type || event.kind || "Memory activity";
      const copy = node("div", "event-copy");
      copy.append(node("div", "event-title", event.title || nice(type)));
      const eventData = event.data || event.event_data || {};
      let parsed = eventData;
      if (typeof parsed === "string") { try { parsed = JSON.parse(parsed); } catch { parsed = { detail: parsed }; } }
      const memoryId = event.memory_id || parsed.memory_id || parsed.id;
      const memory = state.memories.find(item => item.id === memoryId);
      const detail = memory ? memory.content : event.reason || parsed.reason || (memoryId ? `Memory ${shortId(memoryId)}` : "A recorded change to your memory workspace");
      copy.append(node("div", "event-meta", detail));
      if (!compact && eventData && Object.keys(eventData).length) {
        copy.append(button("Inspect event ↗", "text-link", () => {
          showDetail("A recorded change", "AUDIT EVENT");
          $("#detail-content").append(fields({ Event: event.id, Type: nice(type), Recorded: date(event.created_at || event.timestamp, true) }), node("pre", "evidence-text", JSON.stringify(event, null, 2)));
        }));
      }
      row.append(node("span", "event-marker", type.includes("revert") || type.includes("undo") ? "↶" : type.includes("add") || type.includes("create") ? "+" : "◇"), copy, node("span", "event-time", relativeDate(event.created_at || event.timestamp)));
      target.append(row);
    });
  }

  function renderEvents() { renderEventList($("#activity-list"), state.events); }

  function openMemoryForm(memory = null) {
    state.editing = memory;
    const form = $("#memory-form"); form.reset();
    $("#memory-form-error").hidden = true;
    $(".modal-heading h2", form).textContent = memory ? "Edit a memory" : "Add a memory";
    $(".modal-heading .eyebrow", form).textContent = memory ? "A CONSIDERED CORRECTION" : "A NEW PIECE OF CONTEXT";
    $("button[type=submit]", form).textContent = memory ? "Save correction" : "Save memory";
    let statusLabel = $("#edit-status-field");
    if (!statusLabel) {
      statusLabel = node("label", "form-label", "Status"); statusLabel.id = "edit-status-field";
      const select = node("select"); select.name = "status";
      ["active", "archived"].forEach(value => { const option = node("option", "", nice(value)); option.value = value; select.append(option); });
      statusLabel.append(select); $(".advanced-fields", form).append(statusLabel);
    }
    statusLabel.hidden = !memory;
    form.elements.valid_from.required = Boolean(memory);
    form.elements.source.closest("label").hidden = Boolean(memory);
    $(".advanced-fields", form).open = Boolean(memory);
    if (memory) {
      ["content", "kind", "topic", "subject", "predicate", "value", "confidence", "importance", "status"].forEach(key => { if (form.elements[key]) form.elements[key].value = memory[key] ?? ""; });
      ["valid_from", "valid_until"].forEach(key => { form.elements[key].value = memory[key] ? String(memory[key]).slice(0, 10) : ""; });
    }
    $("#memory-dialog").showModal();
    window.setTimeout(() => form.elements.content.focus(), 0);
  }

  async function saveMemory(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const control = $("button[type=submit]", form);
    const errorBox = $("#memory-form-error"); errorBox.hidden = true;
    control.disabled = true; control.setAttribute("aria-busy", "true");
    try {
      const data = new FormData(form);
      const payload = {
        content: String(data.get("content") || "").trim(), kind: data.get("kind"), topic: String(data.get("topic") || "").trim(),
        confidence: Number(data.get("confidence")), importance: Number(data.get("importance")),
      };
      if (state.editing) {
        payload.status = data.get("status");
        ["subject", "predicate", "value"].forEach(key => { payload[key] = String(data.get(key) || "").trim() || null; });
        // Preserve the original timestamp when the displayed day has not changed.
        ["valid_from", "valid_until"].forEach(key => {
          const value = String(data.get(key) || "");
          if (value !== String(state.editing[key] || "").slice(0, 10)) payload[key] = value || null;
        });
        await request(`/api/memories/${encodeURIComponent(state.editing.id)}/update`, { expected_version: state.editing.version, changes: payload });
      } else {
        payload.source = String(data.get("source") || "dashboard").trim();
        ["subject", "predicate", "value", "valid_from", "valid_until"].forEach(key => { const value = String(data.get(key) || "").trim(); if (value) payload[key] = value; });
        await request("/api/memories", payload);
      }
      const wasEditing = Boolean(state.editing);
      $("#memory-dialog").close(); state.editing = null; state.search = null;
      await refresh(); toast(wasEditing ? "Correction saved. The original source evidence is unchanged." : "Memory added. A new piece of context, preserved.");
    } catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; }
    finally { control.disabled = false; control.removeAttribute("aria-busy"); }
  }

  function formatMetric(key, value) {
    if (typeof value !== "number") return display(value);
    if (/accuracy|recall|precision|mrr|hit_rate|success_rate|retention/.test(key) && value <= 1) return `${(value * 100).toFixed(1)}%`;
    if (/ms|latency|time/.test(key)) return `${value.toFixed(2)}${/ms/.test(key) ? " ms" : ""}`;
    return Number.isInteger(value) ? String(value) : value.toFixed(3);
  }

  function renderBenchmark() {
    const target = $("#benchmark-results"); target.replaceChildren();
    const report = state.benchmark;
    if (!report) {
      const panel = node("section", "panel");
      panel.append(empty("Let the results speak.", "Run a controlled comparison of retrieval strategies on synthetic memories with known answers. Your workspace memories are not part of this evaluation."));
      target.append(panel); return;
    }
    if (report.summary && typeof report.summary === "object") {
      const summary = node("div", "metrics-grid benchmark-summary");
      Object.entries(report.summary).filter(([, value]) => typeof value === "number").slice(0, 4).forEach(([key, value]) => summary.append(metric(nice(key), formatMetric(key, value), "Measured on the synthetic workload", "▥")));
      if (summary.children.length) target.append(summary);
    } else if (report.summary) target.append(node("p", "field-help", String(report.summary)));
    let strategies = report.strategies || report.results || [];
    if (!Array.isArray(strategies)) strategies = Object.entries(strategies).map(([name, values]) => ({ name, ...(typeof values === "object" ? values : { result: values }) }));
    strategies = strategies.map(item => ({ ...item, ...(item.metrics && typeof item.metrics === "object" ? item.metrics : {}) }));
    const panel = node("section", "panel");
    const heading = node("div", "panel-heading"); heading.append(node("h2", "", "Retrieval strategy comparison"), pill("Measured results")); panel.append(heading);
    if (strategies.length) {
      const preferred = ["top1_accuracy", "recall_at_3", "abstention_accuracy", "stale_hit_rate", "mean_latency_ms", "mean_context_tokens"];
      const keys = preferred.filter(key => strategies.some(item => typeof item[key] === "number"));
      if (!keys.length) keys.push(...[...new Set(strategies.flatMap(item => Object.keys(item).filter(key => typeof item[key] === "number")))].slice(0, 6));
      const wrap = node("div", "benchmark-table-wrap"); const table = node("table", "benchmark-table");
      const head = node("thead"); const hrow = node("tr");
      ["Strategy", ...keys.map(nice)].forEach(label => { const cell = node("th", "", label); cell.scope = "col"; hrow.append(cell); }); head.append(hrow);
      const body = node("tbody");
      strategies.forEach(item => {
        const row = node("tr"); row.append(node("td", "", nice(item.name || item.strategy || "Strategy")));
        keys.forEach(key => row.append(node("td", "", formatMetric(key, item[key])))); body.append(row);
      });
      table.append(head, body); wrap.append(table); panel.append(wrap);
    } else panel.append(node("pre", "benchmark-detail", JSON.stringify(report, null, 2)));
    const notes = report.notes || report.note || report.limitations || report.methodology || "Synthetic evaluation measures this dataset only. Latency varies with hardware and workload.";
    panel.append(node("div", "benchmark-note", Array.isArray(notes) ? notes.join(" ") : String(notes)));
    const raw = node("details", "detail-section"); raw.style.margin = "0"; raw.style.padding = "16px 23px";
    raw.append(node("summary", "muted", "Inspect the complete evaluation report"), node("pre", "benchmark-detail", JSON.stringify(report, null, 2))); panel.append(raw);
    target.append(panel);
  }

  $$(".nav-item").forEach(item => item.addEventListener("click", () => navigate(item.dataset.view)));
  $$("[data-navigate]").forEach(item => item.addEventListener("click", () => navigate(item.dataset.navigate)));
  $$("[data-action=add]").forEach(item => item.addEventListener("click", () => openMemoryForm()));
  $$("[data-close]").forEach(item => item.addEventListener("click", () => document.getElementById(item.dataset.close).close()));
  $$("dialog").forEach(dialog => dialog.addEventListener("click", event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  }));
  $(".brand").addEventListener("click", () => navigate("overview"));
  $("#refresh-button").addEventListener("click", event => busy(event.currentTarget, async () => { state.search = null; await refresh(); toast("Workspace refreshed."); }));
  $("#hero-review").addEventListener("click", event => runReview(event.currentTarget));
  $("#run-review").addEventListener("click", event => runReview(event.currentTarget));
  $("#seed-demo").addEventListener("click", event => busy(event.currentTarget, async () => { await request("/api/demo", {}); state.search = null; await refresh(); toast("Synthetic demo loaded. Run a review to see how memory evolves."); }));
  $("#search-form").addEventListener("submit", event => { event.preventDefault(); search($("button[type=submit]", event.currentTarget)); });
  $("#all-memories-button").addEventListener("click", () => { state.currentOnly = false; state.search = null; state.relevant.clear(); renderMemories(); });
  $("#current-memories-button").addEventListener("click", () => { state.currentOnly = true; state.search = null; state.relevant.clear(); renderMemories(); });
  $("#kind-filter").addEventListener("change", event => { state.kind = event.target.value; renderMemories(); });
  $("#save-feedback").addEventListener("click", event => busy(event.currentTarget, async () => {
    if (!state.search?.retrieval_id) return;
    const response = await request("/api/feedback", { retrieval_id: state.search.retrieval_id, relevant_ids: [...state.relevant] });
    state.learning = response.learning || response;
    $("#search-feedback").hidden = true;
    state.search.retrieval_id = null;
    renderOverview(); toast("Feedback saved. Future retrieval will use these relevance judgments.");
  }));
  $$("#proposal-filters button").forEach(item => item.addEventListener("click", () => {
    state.proposalStatus = item.dataset.status;
    $$("#proposal-filters button").forEach(other => other.classList.toggle("selected", item === other)); renderProposals();
  }));
  $("#memory-form").addEventListener("submit", saveMemory);
  $("#run-benchmark").addEventListener("click", event => busy(event.currentTarget, async () => { state.benchmark = await request("/api/benchmark", {}); renderBenchmark(); toast("Evaluation complete. The measured results are ready."); }));
  window.addEventListener("hashchange", () => navigate(location.hash.slice(1)));
  navigate(location.hash.slice(1) || "overview"); renderAll();
  refresh().catch(error => {
    $("#connection-label").textContent = "Connection unavailable";
    toast(`Could not connect to the local agent. ${error.message}`, true);
  });
})();
