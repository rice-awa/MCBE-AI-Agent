/**
 * Agent Trace Audit Workbench
 * Consumes GET /api/config, /api/health, /api/traces, /api/traces/{id}
 * No sample data; journal content inserted via textContent only.
 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const els = {
    journalPath: $("journal-path"),
    auditMode: $("audit-mode"),
    traceEnabled: $("trace-enabled"),
    healthSummary: $("health-summary"),
    malformedBadge: $("malformed-badge"),
    btnRefresh: $("btn-refresh"),
    filterForm: $("filter-form"),
    filterStatus: $("filter-status"),
    filterPlayer: $("filter-player"),
    filterLimit: $("filter-limit"),
    btnClearFilters: $("btn-clear-filters"),
    listCount: $("list-count"),
    listRegion: $("trace-list-region"),
    listLoading: $("list-loading"),
    listEmpty: $("list-empty"),
    listError: $("list-error"),
    listErrorText: $("list-error-text"),
    btnRetryList: $("btn-retry-list"),
    traceList: $("trace-list"),
    timelineSubtitle: $("timeline-subtitle"),
    timelineSummary: $("timeline-summary"),
    timelineRegion: $("timeline-region"),
    timelineEmpty: $("timeline-empty"),
    timelineLoading: $("timeline-loading"),
    timelineError: $("timeline-error"),
    timelineErrorText: $("timeline-error-text"),
    btnRetryDetail: $("btn-retry-detail"),
    timeline: $("timeline"),
    inspectorSubtitle: $("inspector-subtitle"),
    inspectorEmpty: $("inspector-empty"),
    inspectorContent: $("inspector-content"),
    btnCopyEvent: $("btn-copy-event"),
    toast: $("toast"),
  };

  /** @type {{status: string, player: string, limit: number}} */
  const state = {
    status: "",
    player: "",
    limit: 50,
    selectedTraceId: null,
    selectedEventId: null,
    /** @type {object|null} */
    detail: null,
    /** @type {object|null} */
    config: null,
    listAbort: null,
    detailAbort: null,
  };

  let toastTimer = null;

  // —— URL helpers ——

  function readUrlState() {
    const params = new URLSearchParams(window.location.search);
    state.status = params.get("status") || "";
    state.player = params.get("player") || "";
    const lim = parseInt(params.get("limit") || "50", 10);
    state.limit = Number.isFinite(lim) && lim > 0 ? Math.min(lim, 500) : 50;
    const hash = (window.location.hash || "").replace(/^#/, "").trim();
    state.selectedTraceId = hash || null;
  }

  function writeUrlState({ replace = false } = {}) {
    const params = new URLSearchParams();
    if (state.status) params.set("status", state.status);
    if (state.player) params.set("player", state.player);
    if (state.limit && state.limit !== 50) params.set("limit", String(state.limit));
    const qs = params.toString();
    const hash = state.selectedTraceId ? `#${state.selectedTraceId}` : "";
    const next = `${window.location.pathname}${qs ? `?${qs}` : ""}${hash}`;
    const method = replace ? "replaceState" : "pushState";
    window.history[method](null, "", next);
  }

  function syncFiltersToForm() {
    els.filterStatus.value = state.status;
    els.filterPlayer.value = state.player;
    els.filterLimit.value = String(state.limit);
  }

  // —— Safe DOM ——

  function clearChildren(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function text(node, value) {
    node.textContent = value == null ? "" : String(value);
  }

  function el(tag, className, textContent) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (textContent != null) node.textContent = String(textContent);
    return node;
  }

  function showToast(message) {
    text(els.toast, message);
    els.toast.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      els.toast.hidden = true;
    }, 2200);
  }

  // —— Formatters ——

  function formatTs(value) {
    if (!value) return "—";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, "Z");
  }

  function formatDuration(ms) {
    if (ms == null || ms === "") return "—";
    const n = Number(ms);
    if (!Number.isFinite(n)) return String(ms);
    if (n < 1000) return `${Math.round(n)} ms`;
    if (n < 60000) return `${(n / 1000).toFixed(2)} s`;
    return `${(n / 60000).toFixed(2)} min`;
  }

  function relativeMs(startTs, eventTs) {
    if (!startTs || !eventTs) return null;
    const a = new Date(startTs).getTime();
    const b = new Date(eventTs).getTime();
    if (Number.isNaN(a) || Number.isNaN(b)) return null;
    return Math.max(0, b - a);
  }

  function attrOf(event, key) {
    const attrs = event && event.attributes;
    if (attrs && typeof attrs === "object" && key in attrs) return attrs[key];
    return event ? event[key] : undefined;
  }

  function statusClass(status) {
    const s = String(status || "").toLowerCase();
    if (s === "completed" || s === "ok" || s === "success" || s === "approved") return "ok";
    if (
      s === "failed" ||
      s === "error" ||
      s === "cancelled" ||
      s === "denied" ||
      s === "rejected" ||
      s === "expired"
    ) {
      return "failed";
    }
    if (s === "abandoned" || s === "suspended" || s === "warn" || s === "warning") return "warn";
    if (s === "started" || s === "running" || s === "info" || s === "requested") return "info";
    return "";
  }

  function prettyJson(value) {
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }

  // —— Fetch ——

  async function fetchJson(url, controller) {
    const res = await fetch(url, {
      method: "GET",
      headers: { Accept: "application/json" },
      signal: controller ? controller.signal : undefined,
      cache: "no-store",
    });
    if (!res.ok) {
      let detail = "";
      try {
        const body = await res.json();
        detail = body && (body.error || body.message) ? String(body.error || body.message) : "";
      } catch {
        /* ignore */
      }
      const err = new Error(detail || `HTTP ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  // —— Config / health ——

  async function loadConfigAndHealth() {
    try {
      const [config, health] = await Promise.all([
        fetchJson("/api/config"),
        fetchJson("/api/health"),
      ]);
      state.config = config;
      renderConfig(config);
      renderHealth(health);
    } catch (err) {
      text(els.auditMode, "审计模式：未知");
      text(els.traceEnabled, "采集：未知");
      text(els.healthSummary, `健康：错误 (${err.message || err})`);
      els.healthSummary.classList.add("pill--danger");
    }
  }

  function renderConfig(config) {
    const include = Boolean(config.agent_trace_include_content);
    const enabled = Boolean(config.agent_trace_enabled);
    text(els.auditMode, `审计模式：${include ? "完整内容" : "元数据"}`);
    els.auditMode.classList.toggle("pill--accent", include);
    els.auditMode.classList.toggle("pill--muted", !include);

    text(els.traceEnabled, `采集：${enabled ? "开启" : "关闭"}`);
    els.traceEnabled.classList.toggle("pill--ok", enabled);
    els.traceEnabled.classList.toggle("pill--muted", !enabled);

    const path = config.journal_path || config.agent_trace_path || "—";
    text(els.journalPath, path);
    els.journalPath.title = path;
  }

  function renderHealth(health) {
    const parsed = health.parsed_lines != null ? health.parsed_lines : "—";
    const malformed = Number(health.malformed_lines || 0);
    const traces = health.trace_count != null ? health.trace_count : health.traces;
    const parts = [`解析 ${parsed}`];
    if (traces != null) parts.push(`traces ${traces}`);
    text(els.healthSummary, `健康：${parts.join(" · ")}`);
    els.healthSummary.classList.remove("pill--danger");

    if (malformed > 0) {
      text(els.malformedBadge, `畸形行：${malformed}`);
      els.malformedBadge.classList.remove("is-hidden");
    } else {
      els.malformedBadge.classList.add("is-hidden");
    }
  }

  // —— List ——

  function setListBusy(busy) {
    els.listRegion.setAttribute("aria-busy", busy ? "true" : "false");
  }

  async function loadList() {
    if (state.listAbort) state.listAbort.abort();
    const controller = new AbortController();
    state.listAbort = controller;

    els.listLoading.hidden = false;
    els.listEmpty.hidden = true;
    els.listError.hidden = true;
    els.traceList.hidden = true;
    setListBusy(true);

    const params = new URLSearchParams();
    if (state.status) params.set("status", state.status);
    if (state.player) params.set("player", state.player);
    params.set("limit", String(state.limit || 50));

    try {
      const data = await fetchJson(`/api/traces?${params.toString()}`, controller);
      if (controller.signal.aborted) return;
      renderList(data.traces || [], data.count);
    } catch (err) {
      if (err.name === "AbortError") return;
      els.listLoading.hidden = true;
      els.listEmpty.hidden = true;
      els.traceList.hidden = true;
      els.listError.hidden = false;
      text(els.listErrorText, `加载列表失败：${err.message || err}`);
      text(els.listCount, "—");
    } finally {
      if (state.listAbort === controller) {
        setListBusy(false);
        els.listLoading.hidden = true;
      }
    }
  }

  function renderList(traces, count) {
    clearChildren(els.traceList);
    const n = count != null ? count : traces.length;
    text(els.listCount, `${n} 条`);

    if (!traces.length) {
      els.listEmpty.hidden = false;
      els.listError.hidden = true;
      els.traceList.hidden = true;
      return;
    }

    els.listEmpty.hidden = true;
    els.listError.hidden = true;
    els.traceList.hidden = false;

    for (const summary of traces) {
      const li = el("li");
      const btn = el("button", "trace-list__item");
      btn.type = "button";
      btn.setAttribute("role", "option");
      const tid = summary.trace_id || "";
      btn.dataset.traceId = tid;
      btn.setAttribute("aria-selected", tid === state.selectedTraceId ? "true" : "false");

      const idRow = el("div", "trace-list__id", tid || "(no id)");
      const chip = el(
        "span",
        `status-chip status-chip--${String(summary.status || "unknown").toLowerCase()}`,
        summary.status || "unknown"
      );

      const row = el("div", "trace-list__row");
      row.appendChild(el("span", "trace-list__player", summary.player_name || "—"));
      const metaParts = [];
      if (summary.event_count != null) metaParts.push(`${summary.event_count} ev`);
      if (summary.attempt_count != null && summary.attempt_count > 1) {
        metaParts.push(`${summary.attempt_count} att`);
      }
      if (summary.duration_ms != null) metaParts.push(formatDuration(summary.duration_ms));
      metaParts.push(formatTs(summary.ended_at || summary.started_at).slice(0, 19));
      row.appendChild(el("span", "trace-list__meta", metaParts.join(" · ")));

      btn.appendChild(idRow);
      btn.appendChild(chip);
      btn.appendChild(row);

      btn.addEventListener("click", () => {
        selectTrace(tid, { push: true });
      });

      li.appendChild(btn);
      els.traceList.appendChild(li);
    }
  }

  function markListSelection() {
    const items = els.traceList.querySelectorAll(".trace-list__item");
    items.forEach((item) => {
      const selected = item.dataset.traceId === state.selectedTraceId;
      item.setAttribute("aria-selected", selected ? "true" : "false");
    });
  }

  // —— Detail / timeline ——

  function setTimelineBusy(busy) {
    els.timelineRegion.setAttribute("aria-busy", busy ? "true" : "false");
  }

  function selectTrace(traceId, { push = false } = {}) {
    state.selectedTraceId = traceId || null;
    state.selectedEventId = null;
    state.detail = null;
    writeUrlState({ replace: !push });
    markListSelection();
    if (!traceId) {
      showTimelineEmpty();
      showInspectorEmpty();
      return;
    }
    loadDetail(traceId);
  }

  function showTimelineEmpty() {
    els.timelineEmpty.hidden = false;
    els.timelineLoading.hidden = true;
    els.timelineError.hidden = true;
    els.timeline.hidden = true;
    clearChildren(els.timeline);
    text(els.timelineSubtitle, "选择左侧 trace");
    els.timelineSummary.hidden = true;
    clearChildren(els.timelineSummary);
  }

  async function loadDetail(traceId) {
    if (state.detailAbort) state.detailAbort.abort();
    const controller = new AbortController();
    state.detailAbort = controller;

    els.timelineEmpty.hidden = true;
    els.timelineError.hidden = true;
    els.timeline.hidden = true;
    els.timelineLoading.hidden = false;
    setTimelineBusy(true);
    text(els.timelineSubtitle, traceId);
    showInspectorEmpty();

    try {
      const data = await fetchJson(`/api/traces/${encodeURIComponent(traceId)}`, controller);
      if (controller.signal.aborted) return;
      state.detail = data;
      renderDetail(data);
    } catch (err) {
      if (err.name === "AbortError") return;
      els.timelineLoading.hidden = true;
      els.timeline.hidden = true;
      els.timelineEmpty.hidden = true;
      els.timelineError.hidden = false;
      const msg =
        err.status === 404
          ? `未找到 trace：${traceId}`
          : `加载详情失败：${err.message || err}`;
      text(els.timelineErrorText, msg);
      els.timelineSummary.hidden = true;
    } finally {
      if (state.detailAbort === controller) {
        setTimelineBusy(false);
        els.timelineLoading.hidden = true;
      }
    }
  }

  function renderDetail(data) {
    const summary = data.summary || {};
    const events = Array.isArray(data.events) ? data.events : [];

    text(
      els.timelineSubtitle,
      `${summary.trace_id || state.selectedTraceId} · ${summary.status || "—"}`
    );

    clearChildren(els.timelineSummary);
    const chips = [
      ["player", summary.player_name || "—"],
      ["events", String(summary.event_count != null ? summary.event_count : events.length)],
      ["attempts", String(summary.attempt_count != null ? summary.attempt_count : "—")],
      ["duration", formatDuration(summary.duration_ms)],
    ];
    for (const [label, value] of chips) {
      const pill = el("span", "pill", `${label}: ${value}`);
      els.timelineSummary.appendChild(pill);
    }
    if (summary.conversation_id) {
      els.timelineSummary.appendChild(
        el("span", "pill pill--muted", `conv: ${summary.conversation_id}`)
      );
    }
    els.timelineSummary.hidden = false;

    clearChildren(els.timeline);
    if (!events.length) {
      els.timelineEmpty.hidden = false;
      text(els.timelineEmpty.querySelector("p"), "该 trace 没有事件。");
      els.timeline.hidden = true;
      return;
    }

    els.timelineEmpty.hidden = true;
    els.timelineError.hidden = true;
    els.timeline.hidden = false;

    const startTs = summary.started_at || (events[0] && events[0].timestamp);

    events.forEach((event, index) => {
      const item = el("button", "timeline__item");
      item.type = "button";
      const eventId = event.event_id || `seq-${event.sequence}-${index}`;
      item.dataset.eventId = eventId;
      item.dataset.eventIndex = String(index);

      const tone = statusClass(event.status) || statusClass(event.event_name);
      if (tone) item.classList.add(`timeline__item--${tone}`);

      const selected =
        state.selectedEventId != null
          ? state.selectedEventId === eventId
          : index === 0;
      item.setAttribute("aria-selected", selected ? "true" : "false");

      const dot = el("span", "timeline__dot");
      dot.setAttribute("aria-hidden", "true");

      const body = el("div", "timeline__body");
      const top = el("div", "timeline__top");
      top.appendChild(el("span", "timeline__name", event.event_name || "(unnamed)"));
      top.appendChild(el("span", "timeline__status", event.status || "—"));

      const detail = el("div", "timeline__detail");
      const rel = relativeMs(startTs, event.timestamp);
      const bits = [];
      if (event.sequence != null) bits.push(`#${event.sequence}`);
      if (rel != null) bits.push(`+${formatDuration(rel)}`);
      if (event.duration_ms != null) bits.push(`dur ${formatDuration(event.duration_ms)}`);

      const toolName = attrOf(event, "tool_name");
      const provider = attrOf(event, "provider");
      const modelName = attrOf(event, "model_name");
      const decision = attrOf(event, "decision");
      const attemptId = event.attempt_id;

      if (toolName) bits.push(`tool:${toolName}`);
      if (provider || modelName) {
        bits.push([provider, modelName].filter(Boolean).join("/"));
      }
      if (decision) bits.push(`decision:${decision}`);
      if (attemptId) bits.push(`attempt:${shortId(attemptId)}`);

      for (const b of bits) {
        detail.appendChild(el("span", null, b));
      }

      body.appendChild(top);
      body.appendChild(detail);
      item.appendChild(dot);
      item.appendChild(body);

      item.addEventListener("click", () => {
        selectEvent(eventId, index);
      });

      els.timeline.appendChild(item);
    });

    // Auto-select first event (or keep selection if still present)
    let pick = 0;
    if (state.selectedEventId) {
      const found = events.findIndex(
        (e, i) => (e.event_id || `seq-${e.sequence}-${i}`) === state.selectedEventId
      );
      if (found >= 0) pick = found;
    }
    const pickId =
      events[pick].event_id || `seq-${events[pick].sequence}-${pick}`;
    selectEvent(pickId, pick, { skipScroll: true });
  }

  function shortId(id) {
    const s = String(id);
    return s.length > 12 ? `${s.slice(0, 8)}…` : s;
  }

  function selectEvent(eventId, index, { skipScroll = false } = {}) {
    state.selectedEventId = eventId;
    const items = els.timeline.querySelectorAll(".timeline__item");
    items.forEach((item) => {
      const selected = item.dataset.eventId === eventId;
      item.setAttribute("aria-selected", selected ? "true" : "false");
      if (selected && !skipScroll) {
        item.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    });

    const detail = state.detail;
    if (!detail || !Array.isArray(detail.events)) {
      showInspectorEmpty();
      return;
    }
    const event =
      typeof index === "number" && detail.events[index]
        ? detail.events[index]
        : detail.events.find(
            (e, i) => (e.event_id || `seq-${e.sequence}-${i}`) === eventId
          );
    if (!event) {
      showInspectorEmpty();
      return;
    }
    renderInspector(event, detail);
  }

  // —— Inspector ——

  function showInspectorEmpty() {
    els.inspectorEmpty.hidden = false;
    els.inspectorContent.hidden = true;
    clearChildren(els.inspectorContent);
    els.btnCopyEvent.hidden = true;
    text(els.inspectorSubtitle, "选择时间线中的事件");
  }

  function addKvSection(parent, title, pairs) {
    const section = el("section", "inspector__section");
    const header = el("div", "inspector__section-header");
    header.appendChild(el("h3", "inspector__section-title", title));
    section.appendChild(header);

    const dl = el("dl", "inspector__kv");
    for (const [k, v] of pairs) {
      if (v === undefined) continue;
      const row = el("div");
      row.appendChild(el("dt", null, k));
      const dd = el("dd");
      if (v === null) text(dd, "null");
      else if (typeof v === "object") text(dd, prettyJson(v));
      else text(dd, String(v));
      row.appendChild(dd);
      dl.appendChild(row);
    }
    section.appendChild(dl);
    parent.appendChild(section);
  }

  function addPreSection(parent, title, content, { copyLabel } = {}) {
    const section = el("section", "inspector__section");
    const header = el("div", "inspector__section-header");
    header.appendChild(el("h3", "inspector__section-title", title));
    if (copyLabel) {
      const btn = el("button", "btn btn--ghost btn--icon", copyLabel);
      btn.type = "button";
      btn.title = `复制 ${title}`;
      btn.setAttribute("aria-label", `复制 ${title}`);
      btn.addEventListener("click", () => copyText(content));
      header.appendChild(btn);
    }
    section.appendChild(header);
    const pre = el("pre");
    // Safe insertion — textContent / createTextNode only for journal content
    text(pre, content);
    section.appendChild(pre);
    parent.appendChild(section);
  }

  function renderInspector(event, detail) {
    els.inspectorEmpty.hidden = true;
    els.inspectorContent.hidden = false;
    clearChildren(els.inspectorContent);

    const name = event.event_name || "(unnamed)";
    text(els.inspectorSubtitle, name);
    els.btnCopyEvent.hidden = false;

    const identityPairs = [
      ["event_name", event.event_name],
      ["event_id", event.event_id],
      ["status", event.status],
      ["sequence", event.sequence],
      ["timestamp", event.timestamp],
      ["duration_ms", event.duration_ms],
      ["trace_id", event.trace_id],
      ["run_id", event.run_id],
      ["attempt_id", event.attempt_id],
      ["span_id", event.span_id],
      ["parent_span_id", event.parent_span_id],
      ["tool_call_id", event.tool_call_id],
    ];
    addKvSection(els.inspectorContent, "事件", identityPairs);

    const attrs = event.attributes && typeof event.attributes === "object" ? event.attributes : null;
    if (attrs && Object.keys(attrs).length) {
      const attrPairs = Object.keys(attrs)
        .sort()
        .map((k) => [k, attrs[k]]);
      addKvSection(els.inspectorContent, "Attributes", attrPairs);
    }

    // Usage: prefer payload (content mode), fall back to attributes (metadata mode).
    const usageVal =
      event.payload && event.payload.usage != null
        ? event.payload.usage
        : attrs && attrs.usage != null
          ? attrs.usage
          : null;
    if (usageVal != null) {
      addPreSection(els.inspectorContent, "Usage", prettyJson(usageVal), {
        copyLabel: "复制",
      });
    }

    // Payload: only when present (content-gated on server)
    if (Object.prototype.hasOwnProperty.call(event, "payload") && event.payload != null) {
      const payloadText =
        typeof event.payload === "string" ? event.payload : prettyJson(event.payload);
      addPreSection(els.inspectorContent, "Payload", payloadText, { copyLabel: "复制" });

      // Highlight common content keys for readability
      if (typeof event.payload === "object") {
        const p = event.payload;
        if (p.user_message != null) {
          addPreSection(els.inspectorContent, "User message", stringifyContent(p.user_message), {
            copyLabel: "复制",
          });
        }
        if (p.messages != null) {
          addPreSection(els.inspectorContent, "LLM messages", prettyJson(p.messages), {
            copyLabel: "复制",
          });
        }
        if (p.tool_args != null || p.parameters != null) {
          addPreSection(
            els.inspectorContent,
            "Tool args",
            prettyJson(p.tool_args != null ? p.tool_args : p.parameters),
            { copyLabel: "复制" }
          );
        }
        if (p.tool_result != null || p.result != null) {
          addPreSection(
            els.inspectorContent,
            "Tool result",
            stringifyContent(p.tool_result != null ? p.tool_result : p.result),
            { copyLabel: "复制" }
          );
        }
        if (p.final_response != null || p.response != null) {
          addPreSection(
            els.inspectorContent,
            "Final response",
            stringifyContent(p.final_response != null ? p.final_response : p.response),
            { copyLabel: "复制" }
          );
        }
      }
    } else {
      const note = el("p", "inspector__note inspector__note--warn");
      const include =
        state.config && state.config.agent_trace_include_content
          ? true
          : false;
      text(
        note,
        include
          ? "此事件无 payload（元数据事件或写入时未附带正文）。"
          : "当前审计模式为「元数据」：服务端未提供完整正文 payload。完整内容需在配置中开启 agent_trace_include_content（页面无法切换）。"
      );
      els.inspectorContent.appendChild(note);
    }

    // Related aggregates for context
    appendRelated(els.inspectorContent, event, detail);

    // Full event JSON
    addPreSection(els.inspectorContent, "Raw event JSON", prettyJson(event), {
      copyLabel: "复制",
    });
  }

  function stringifyContent(value) {
    if (value == null) return "";
    if (typeof value === "string") return value;
    return prettyJson(value);
  }

  function appendRelated(parent, event, detail) {
    const name = String(event.event_name || "");
    if (name.startsWith("tool.") || name.startsWith("policy.")) {
      const tools = Array.isArray(detail.tools) ? detail.tools : [];
      const tcid = event.tool_call_id || attrOf(event, "tool_call_id");
      const match = tools.find((t) => t.tool_call_id && t.tool_call_id === tcid);
      if (match) {
        const pairs = [
          ["tool_name", match.tool_name],
          ["tool_call_id", match.tool_call_id],
          ["status", match.status],
          ["execution_status", match.execution_status],
          ["duration_ms", match.duration_ms],
        ];
        addKvSection(parent, "Tool 聚合", pairs);
        if (match.tool_args != null) {
          addPreSection(parent, "Tool args (聚合)", prettyJson(match.tool_args), {
            copyLabel: "复制",
          });
        }
        if (match.tool_result != null) {
          addPreSection(parent, "Tool result (聚合)", stringifyContent(match.tool_result), {
            copyLabel: "复制",
          });
        }
      }
    }
    if (name.startsWith("approval.")) {
      const approvals = Array.isArray(detail.approvals) ? detail.approvals : [];
      const related = approvals.filter((a) => a.event_id === event.event_id);
      if (related.length) {
        const a = related[0];
        addKvSection(parent, "Approval", [
          ["decision", a.decision],
          ["reason", a.reason],
          ["tool_call_id", a.tool_call_id],
        ]);
      }
    }
    if (name.startsWith("model.")) {
      const models = Array.isArray(detail.models) ? detail.models : [];
      const m = models.find((x) => x.event_id === event.event_id);
      if (m) {
        addKvSection(parent, "Model", [
          ["provider", m.provider],
          ["model_name", m.model_name],
          ["finish_reason", m.finish_reason],
          ["duration_ms", m.duration_ms],
        ]);
      }
    }
    if (name.startsWith("delivery.")) {
      const delivery = Array.isArray(detail.delivery) ? detail.delivery : [];
      const d = delivery.find((x) => x.event_id === event.event_id);
      if (d) {
        addKvSection(parent, "Delivery", [
          ["target", d.target],
          ["delivery_type", d.delivery_type],
          ["chunk_type", d.chunk_type],
          ["chunk_count", d.chunk_count],
          ["byte_count", d.byte_count],
          ["duration_ms", d.duration_ms],
        ]);
      }
    }
  }

  async function copyText(value) {
    const textValue = value == null ? "" : String(value);
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(textValue);
      } else {
        const ta = document.createElement("textarea");
        ta.value = textValue;
        ta.setAttribute("readonly", "");
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      showToast("已复制到剪贴板");
    } catch {
      showToast("复制失败");
    }
  }

  // —— Events wiring ——

  function onFilterSubmit(ev) {
    ev.preventDefault();
    state.status = els.filterStatus.value || "";
    state.player = (els.filterPlayer.value || "").trim();
    const lim = parseInt(els.filterLimit.value || "50", 10);
    state.limit = Number.isFinite(lim) && lim > 0 ? Math.min(lim, 500) : 50;
    writeUrlState({ replace: true });
    loadList();
  }

  function onClearFilters() {
    state.status = "";
    state.player = "";
    state.limit = 50;
    syncFiltersToForm();
    writeUrlState({ replace: true });
    loadList();
  }

  function onHashOrPop() {
    const prev = state.selectedTraceId;
    readUrlState();
    syncFiltersToForm();
    if (state.selectedTraceId !== prev) {
      if (state.selectedTraceId) {
        loadDetail(state.selectedTraceId);
        markListSelection();
      } else {
        selectTrace(null, { push: false });
      }
    }
  }

  function init() {
    readUrlState();
    syncFiltersToForm();

    els.filterForm.addEventListener("submit", onFilterSubmit);
    els.btnClearFilters.addEventListener("click", onClearFilters);
    els.btnRefresh.addEventListener("click", () => {
      loadConfigAndHealth();
      loadList();
      if (state.selectedTraceId) loadDetail(state.selectedTraceId);
    });
    els.btnRetryList.addEventListener("click", () => loadList());
    els.btnRetryDetail.addEventListener("click", () => {
      if (state.selectedTraceId) loadDetail(state.selectedTraceId);
    });
    els.btnCopyEvent.addEventListener("click", () => {
      if (!state.detail || !state.selectedEventId) return;
      const events = state.detail.events || [];
      const event = events.find(
        (e, i) => (e.event_id || `seq-${e.sequence}-${i}`) === state.selectedEventId
      );
      if (event) copyText(prettyJson(event));
    });

    window.addEventListener("popstate", onHashOrPop);
    window.addEventListener("hashchange", onHashOrPop);

    loadConfigAndHealth();
    loadList().then(() => {
      if (state.selectedTraceId) {
        markListSelection();
        loadDetail(state.selectedTraceId);
      } else {
        showTimelineEmpty();
        showInspectorEmpty();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
