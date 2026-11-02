"use strict";

const state = {
  overview: null,
  route: "dashboard",
  tradeFilters: { side: "", period: "", status: "", sort: "entry_time_desc" },
  decisionFilters: { status: "", side: "", period: "", rejection: "", score_min: "", score_max: "", sort: "newest", page: 1, page_size: 25 },
  trades: [],
  lastDecisionHash: null,
  chartPayload: null,
};

const PAGE_META = {
  dashboard: ["Dashboard", "Strategy status, historical results, and execution safety."],
  research: ["Research & Backtest", "Development, validation, and holdout results from the authoritative run."],
  strategy: ["Strategy", "The complete intended strategy specification and the research variant actually run."],
  chart: ["Market Chart", "Historical D1, H4, and M15 research candles. Not live prices."],
  trades: ["Trade Explorer", "Simulated historical trades from the authoritative trade records."],
  decisions: ["Decision History", "Canonical StrategyDecision records, filtered and paginated server-side."],
  data: ["Data & Datasets", "Dataset identity, row counts, checksums, and timestamp limitations."],
  reports: ["Reports & Results", "Experiment summary, manifest, artifacts, and validation status."],
  system: ["System & Safety", "Read-only capabilities, execution status, and unresolved limitations."],
  config: ["Configuration", "Effective read-only settings with paths and sensitive values redacted."],
};

const esc = (value) => String(value ?? "—").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
const isNum = (value) => typeof value === "number" && Number.isFinite(value);
const fmt = (value, digits = 2) => (isNum(value) ? value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits }) : "—");
const fmtSigned = (value, digits = 2) => (isNum(value) ? (value >= 0 ? "+" : "") + fmt(value, digits) : "—");
const fmtPct = (value, digits = 2) => (isNum(value) ? `${(value * 100).toFixed(digits)}%` : "—");
const fmtR = (value) => `${fmtSigned(value, 4)}R`;
const tone = (value) => (!isNum(value) ? "neutral" : value > 0 ? "positive" : value < 0 ? "negative" : "neutral");
const shortHash = (value) => (value ? `${String(value).slice(0, 10)}…${String(value).slice(-6)}` : "—");
const humanStatus = (value) => String(value ?? "unknown").replaceAll("_", " ");
const badgeClass = (value) => {
  const raw = String(value ?? "").toLowerCase();
  if (["pass", "available", "accepted", "closed", "tp2_closed", "defined", "green", "ok", "completed"].some((x) => raw.includes(x))) return "green";
  if (["warning", "inconclusive", "unverified", "research", "medium", "amber", "breakeven"].some((x) => raw.includes(x))) return "amber";
  if (["fail", "blocked", "disabled", "rejected", "stopped", "error", "red", "loss"].some((x) => raw.includes(x))) return "red";
  return "blue";
};
const badge = (value, klass = null) => `<span class="badge ${klass || badgeClass(value)}">${esc(humanStatus(value))}</span>`;
const labelChip = (text, klass = "") => `<span class="label-chip ${klass}">${esc(text)}</span>`;
const warningBanner = (warnings, klass = "warning") => {
  if (!warnings || !warnings.length) return "";
  return `<div class="warning-banner ${klass === "error" ? "error" : ""}"><span class="warning-mark">!</span><div>${warnings.map(esc).join(" ")}</div></div>`;
};
const infoBanner = (message) => `<div class="info-banner"><span class="warning-mark">i</span><div>${esc(message)}</div></div>`;
const pageHeading = (title, subtitle, actions = "") => `<div class="page-heading"><div><h1>${esc(title)}</h1><p>${esc(subtitle)}</p></div><div class="heading-actions">${actions}</div></div>`;
const loading = () => `<div class="loading-state"><div class="spinner"></div><div>Reading validated artifacts…</div></div>`;
const emptyState = (message) => `<div class="empty-state">${esc(message)}</div>`;
const metricCard = (label, value, sub = "", klass = "") => `<div class="metric-card"><div class="metric-label">${esc(label)}</div><div class="metric-value ${klass}">${value}</div>${sub ? `<div class="metric-sub">${sub}</div>` : ""}</div>`;
const keyValue = (pairs) => `<dl class="key-value">${pairs.map(([key, value]) => `<dt>${esc(key)}</dt><dd>${value ?? "—"}</dd>`).join("")}</dl>`;

async function getJSON(path, params = {}) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value).length) query.set(key, value);
  });
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const response = await fetch(`${path}${suffix}`, { headers: { Accept: "application/json" } });
  let body;
  try { body = await response.json(); } catch { throw new Error(`Invalid response from ${path}`); }
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}

function setTopbar(overview) {
  const app = overview?.application || {};
  const execution = overview?.execution || {};
  document.getElementById("top-mode").textContent = app.mode ? "Research" : "Research";
  document.getElementById("top-execution").textContent = "Disabled";
  document.getElementById("top-execution").classList.add("disabled");
  document.getElementById("top-version").textContent = app.version || "0.1.0";
  const instrument = overview?.instrument?.symbol || "XAUUSD-P";
  document.getElementById("top-instrument").textContent = instrument;
}

function showToast(message, isError = false) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.className = `toast show${isError ? " error" : ""}`;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => { toast.className = "toast"; }, 4200);
}

function currentRoute() {
  const raw = window.location.hash.replace(/^#\/?/, "") || "dashboard";
  const [path, query = ""] = raw.split("?");
  return { route: path || "dashboard", params: new URLSearchParams(query) };
}

function setActiveNav(route) {
  document.querySelectorAll("[data-route]").forEach((node) => node.classList.toggle("active", node.dataset.route === route));
}

async function refreshOverview() {
  const overview = await getJSON("/api/overview");
  state.overview = overview;
  setTopbar(overview);
  return overview;
}

function metricGrid(items) {
  return `<div class="metrics-grid">${items.map((item) => metricCard(item[0], item[1], item[2], item[3] || "")).join("")}</div>`;
}

function performanceTable(splits, overall) {
  const ordered = ["development", "validation", "holdout"];
  const rows = ordered.map((name) => {
    const split = splits?.[name] || {};
    const cls = tone(split.gross_realized_r);
    return `<tr>
      <td><strong>${esc(split.label || name)}</strong><div class="muted small">${esc(split.period?.start?.slice(0, 10) || "")} - ${esc(split.period?.end?.slice(0, 10) || "")}</div></td>
      <td class="num">${esc(split.closed_trade_count ?? 0)}</td>
      <td class="num">${fmtPct(split.win_rate)}</td>
      <td class="num ${cls}">${fmtR(split.gross_realized_r)}</td>
      <td class="num ${tone(split.average_r)}">${fmtR(split.average_r)}</td>
      <td class="num">${fmt(split.profit_factor, 4)}</td>
      <td class="num negative">${fmt(split.max_drawdown_r, 2)}R</td>
      <td>${split.inconclusive_under_30_trades ? badge("inconclusive") : badge("sample adequate")}</td>
    </tr>`;
  }).join("");
  return `<div class="table-scroll"><table class="data-table">
    <thead><tr><th>Split</th><th class="num">Closed trades</th><th class="num">Win rate</th><th class="num">Realized R</th><th class="num">Expectancy</th><th class="num">Profit factor</th><th class="num">Max DD</th><th>Sample gate</th></tr></thead>
    <tbody>${rows}${overall ? `<tr>
      <td><strong>Overall</strong><div class="muted small">All splits</div></td>
      <td class="num">${esc(overall.closed_trade_count ?? 0)}</td>
      <td class="num">${fmtPct(overall.win_rate)}</td>
      <td class="num ${tone(overall.gross_realized_r)}">${fmtR(overall.gross_realized_r)}</td>
      <td class="num ${tone(overall.average_r)}">${fmtR(overall.average_r)}</td>
      <td class="num">${fmt(overall.profit_factor, 4)}</td>
      <td class="num negative">${fmt(overall.max_drawdown_r, 2)}R</td>
      <td>${badge("descriptive")}</td>
    </tr>` : ""}</tbody>
  </table></div>`;
}

function splitCards(splits) {
  const ordered = ["development", "validation", "holdout"];
  return `<div class="grid-3">${ordered.map((name) => {
    const split = splits?.[name] || {};
    const exits = split.exit_reason_counts || {};
    return `<section class="panel"><div class="panel-head"><div><h2 class="panel-title">${esc(split.label || name)}</h2><div class="panel-subtitle">${esc(split.period?.start?.slice(0, 10) || "")} → ${esc(split.period?.end?.slice(0, 10) || "")}</div></div>${badge(split.inconclusive_under_30_trades ? "inconclusive" : "sample adequate")}</div>
      <div class="panel-body">${metricGrid([
        ["Closed trades", esc(split.closed_trade_count ?? 0), `${esc(split.filled_trade_count ?? 0)} filled`],
        ["Win rate", fmtPct(split.win_rate), `${esc(split.win_count ?? 0)} wins / ${esc(split.loss_count ?? 0)} losses`],
        ["Realized R", fmtR(split.gross_realized_r), "", tone(split.gross_realized_r)],
        ["Expectancy", fmtR(split.average_r), "", tone(split.average_r)],
        ["Profit factor", fmt(split.profit_factor, 4), "Gross R only"],
        ["Max drawdown", `${fmt(split.max_drawdown_r, 2)}R`, "", "negative"],
      ])}
      <div class="caption small muted" style="margin-top:12px">TP1 partials: ${esc(exits.tp1 ?? 0)} · TP2 completions: ${esc(exits.tp2 ?? 0)} · Ambiguous bars: ${esc(split.ambiguous_bar_count ?? 0)} · Open/truncated: ${esc(split.open_or_truncated_trade_count ?? 0)}</div></div></section>`;
  }).join("")}</div>`;
}

function recentDecisionsTable(items) {
  if (!items?.length) return emptyState("No recent decisions available.");
  return `<div class="table-scroll"><table class="data-table"><thead><tr><th>Decision time</th><th>Status</th><th>Direction</th><th class="num">Score</th><th>Reason / context</th><th>Hash</th></tr></thead><tbody>${items.map((item) => `<tr>
    <td class="mono">${esc(item.decision_time)}</td><td>${badge(item.status)}</td><td>${esc(item.side || "—")}</td><td class="num">${esc(item.score ?? "—")}</td>
    <td>${esc((item.rejections || []).join(", ") || (item.has_candidate ? "Candidate" : "—"))}</td><td class="mono">${esc(shortHash(item.record_hash))}</td>
  </tr>`).join("")}</tbody></table></div>`;
}

function artifactsPanel(statuses) {
  if (!statuses?.length) return emptyState("Artifact status unavailable.");
  return `<div class="stack">${statuses.map((item) => `<div class="stack-row">
    <div><div class="stack-row-label mono">${esc(item.name)}</div><div class="muted small">${item.bytes ? `${(item.bytes / 1024 / 1024).toFixed(2)} MB` : "missing"}</div></div>
    <div>${item.error ? badge("invalid", "red") : item.parsed ? badge("available", "green") : item.name === "decisions.jsonl" ? badge("on demand", "blue") : badge("unreadable", "amber")}</div>
  </div>`).join("")}</div>`;
}

async function renderDashboard() {
  let data = state.overview;
  if (!data) data = await refreshOverview();
  const m = data.metrics || {};
  const splits = data.splits || {};
  const warnings = data.warnings || [];
  const runCompleted = Boolean(data.run?.config_fingerprint) && data.record_validation?.status === "PASS";
  const runLabel = runCompleted ? "Completed" : data.run?.config_fingerprint ? "Review" : "Unavailable";
  document.getElementById("page").innerHTML = `
    ${pageHeading("Dashboard", "Strategy status, historical research results, and read-only safety state.", `<a class="button" href="#/reports">View Full Report</a>`)}
    ${warningBanner(warnings, warnings.some((w) => /missing|invalid|failed|unavailable/i.test(w)) ? "error" : "warning")}
    <div class="status-grid">
      <div class="status-card"><div class="status-card-label">Instrument</div><div class="status-card-value">${esc(data.instrument?.symbol || "XAUUSD-P")}</div><div class="status-card-sub">${esc(data.instrument?.asset_class || "unverified gold")}</div></div>
      <div class="status-card"><div class="status-card-label">Strategy</div><div class="status-card-value">${esc(data.strategy?.variant_name || "Research variant")}</div><div class="status-card-sub">${esc(data.strategy?.variant_id || "—")} · v${esc(data.strategy?.version || "—")}</div></div>
      <div class="status-card red"><div class="status-card-label">Execution mode</div><div class="status-card-value">Research</div><div class="status-card-sub"><span class="negative">Orders disabled</span> · read-only</div></div>
      <div class="status-card ${data.data?.status === "PASS" ? "green" : "amber"}"><div class="status-card-label">Historical data</div><div class="status-card-value">${esc(data.data?.status || "WARNING")}</div><div class="status-card-sub">D1 / H4 / M15 · dataset-time</div></div>
      <div class="status-card ${data.record_validation?.status === "PASS" ? "green" : "amber"}"><div class="status-card-label">Authoritative records</div><div class="status-card-value">${esc(data.record_validation?.status || "WARNING")}</div><div class="status-card-sub">${esc(data.record_validation?.decision_count ?? "—")} decisions · canonical</div></div>
      <div class="status-card ${runCompleted ? "green" : "amber"}"><div class="status-card-label">Most recent run</div><div class="status-card-value">${runLabel}</div><div class="status-card-sub">2024–2026 · gross research</div></div>
    </div>
    <div class="panel" style="margin-bottom:16px"><div class="panel-head"><div><h2 class="panel-title">Historical Performance</h2><div class="panel-subtitle">${esc(data.run?.artifact_dir || "authoritative experiment")}</div></div>${labelChip("GROSS BASELINE - EXECUTION COSTS EXCLUDED")}</div>
      <div class="panel-body">
        ${metricGrid([
          ["Closed trades", esc(m.closed_trade_count ?? 0), `${esc(m.win_count ?? 0)} wins / ${esc(m.loss_count ?? 0)} losses`],
          ["Win rate", fmtPct(m.win_rate), "Gross R sample"],
          ["Realized R", fmtR(m.gross_realized_r), "Sum of closed trades", tone(m.gross_realized_r)],
          ["Expectancy", fmtR(m.average_r), "Gross R per trade", tone(m.average_r)],
          ["Profit factor", fmt(m.profit_factor, 4), "Gross R only"],
          ["Max drawdown", `${fmt(m.max_drawdown_r, 2)}R`, "Peak-to-trough in R", "negative"],
        ])}
      </div>
      <div class="panel-body flush">${performanceTable(splits, m)}</div>
    </div>
    <div class="section-grid grid-2" style="margin-bottom:16px">
      <section class="panel"><div class="panel-head"><div><h2 class="panel-title">XAUUSD-P Historical Chart</h2><div class="panel-subtitle">Last 90 D1 research candles · not live prices</div></div><a class="button small" href="#/chart">Open Chart</a></div><div class="panel-body"><div class="chart-shell"><canvas id="dashboard-chart"></canvas></div><div class="chart-caption" id="dashboard-chart-caption">Loading historical research data…</div></div></section>
      <section class="panel"><div class="panel-head"><div><h2 class="panel-title">System Status</h2><div class="panel-subtitle">Capability boundaries</div></div>${badge("read-only", "blue")}</div><div class="panel-body">
        <div class="stack">
          <div class="stack-row"><span class="stack-row-label">Strategy loaded</span><span class="stack-row-value positive">Research variant v${esc(data.strategy?.version || "—")}</span></div>
          <div class="stack-row"><span class="stack-row-label">Datasets validated</span><span class="stack-row-value">${badge(data.data?.status || "WARNING")}</span></div>
          <div class="stack-row"><span class="stack-row-label">Canonical records</span><span class="stack-row-value">${badge(data.record_validation?.status || "WARNING")}</span></div>
          <div class="stack-row"><span class="stack-row-label">Execution gate</span><span class="stack-row-value negative">DISABLED</span></div>
          <div class="stack-row"><span class="stack-row-label">Broker order path</span><span class="stack-row-value negative">NOT PRESENT</span></div>
        </div>
        <div class="mini-callout" style="margin-top:14px"><strong>No broker orders.</strong> This dashboard is a read-only research view. Demo and live execution remain blocked.</div>
      </div></section>
    </div>
    <div class="section-grid grid-equal">
      <section class="panel"><div class="panel-head"><div><h2 class="panel-title">Recent Decisions</h2><div class="panel-subtitle">Latest records from decisions.jsonl</div></div><a class="button small" href="#/decisions">View All</a></div><div class="panel-body flush">${recentDecisionsTable(data.recent_decisions)}</div></section>
      <section class="panel"><div class="panel-head"><div><h2 class="panel-title">Run Artifacts</h2><div class="panel-subtitle">Authoritative experiment outputs</div></div><a class="button small" href="#/reports">Reports</a></div><div class="panel-body">${artifactsPanel(data.artifact_status)}</div></section>
    </div>
    <div class="warning-banner" style="margin-top:16px;border-color:#2a5279;background:#0b2238;color:#b8d7f2"><span class="warning-mark">i</span><div><strong>Research limitations.</strong> Gross price/R only; execution costs are excluded. Absolute UTC provenance, session/news compliance, and live readiness are not claimed. Every split has fewer than 30 closed trades and is inconclusive.</div></div>`;
  loadChartInto("dashboard-chart", "dashboard-chart-caption", "d1", 90);
}

async function renderResearch() {
  const data = await getJSON("/api/research");
  document.getElementById("page").innerHTML = `${pageHeading("Research & Backtest", "Authoritative gross-baseline results across the three chronological splits.")}
    ${warningBanner(data.warnings)}
    <div class="panel" style="margin-bottom:16px"><div class="panel-head"><div><h2 class="panel-title">Performance Comparison</h2><div class="panel-subtitle">All metrics are research-only and gross of execution costs.</div></div>${labelChip("GROSS BASELINE - EXECUTION COSTS EXCLUDED")}</div><div class="panel-body flush">${performanceTable(data.splits, data.overall)}</div></div>
    ${splitCards(data.splits)}
    <div class="section-grid grid-equal" style="margin-top:16px">
      <section class="panel"><div class="panel-head"><h2 class="panel-title">Sample Size Gates</h2></div><div class="panel-body"><ul class="list-clean"><li>Each split is labelled <strong>inconclusive</strong> because it has fewer than 30 closed trades.</li><li>Overall trade count does not remove split-level uncertainty.</li><li>No statistical significance, optimization, or profitability claim is made.</li></ul></div></section>
      <section class="panel"><div class="panel-head"><h2 class="panel-title">Exit Composition</h2></div><div class="panel-body">${Object.entries(data.splits || {}).map(([name, split]) => `<div class="stack-row"><span class="stack-row-label">${esc(split.label || name)}</span><span class="stack-row-value">${esc(split.tp1_partial_count ?? 0)} TP1 / ${esc(split.tp2_count ?? 0)} TP2 · ${esc(split.ambiguous_bar_count ?? 0)} ambiguous</span></div>`).join("")}</div></section>
    </div>`;
}

async function renderStrategy() {
  const data = await getJSON("/api/strategy");
  const params = Object.entries(data.parameters || {}).sort(([a], [b]) => a.localeCompare(b));
  document.getElementById("page").innerHTML = `${pageHeading("Strategy", "The intended D1/H4/M15 specification and the isolated research variant used in the historical run.")}
    ${warningBanner([data.variant_warning])}
    <div class="section-grid grid-equal" style="margin-bottom:16px">
      <section class="panel"><div class="panel-head"><div><h2 class="panel-title">Complete Intended Strategy</h2><div class="panel-subtitle">Design specification · not the variant used in this experiment</div></div>${badge("specification", "blue")}</div><div class="panel-body">
        ${keyValue([["Name", esc(data.complete_strategy?.name)], ["Instrument", esc(data.complete_strategy?.instrument)], ["Timeframes", (data.complete_strategy?.timeframes || []).map((x) => badge(x, "blue")).join(" ")], ["Spec source", `<span class="mono">${esc(data.spec_document)}</span>`]])}
        <p class="muted small" style="margin:14px 0 0">${esc(data.complete_strategy?.description)}</p>
      </div></section>
      <section class="panel"><div class="panel-head"><div><h2 class="panel-title">Variant Used in Historical Run</h2><div class="panel-subtitle">The experiment is not the complete approved strategy</div></div>${badge("research-only", "amber")}</div><div class="panel-body">
        ${keyValue([["Variant name", esc(data.run_variant?.variant_name)], ["Variant ID", `<span class="mono">${esc(data.run_variant?.variant_id)}</span>`], ["Version", esc(data.run_variant?.version)], ["Research only", data.run_variant?.research_only ? badge("yes", "amber") : badge("no", "red")]])}
        <div class="mini-callout" style="margin-top:14px">Session isolation and news-unaware research behavior are explicit limitations of this run.</div>
      </div></section>
    </div>
    <section class="panel" style="margin-bottom:16px"><div class="panel-head"><h2 class="panel-title">Specification Summary</h2><div class="panel-subtitle">D1 bias · H4 structure / zones · M15 trigger</div></div><div class="panel-body"><div class="section-grid grid-3">${(data.sections || []).map((section) => `<div class="mini-callout"><div class="section-title">${esc(section.title)}</div><ul class="list-clean">${(section.items || []).map((item) => `<li>${esc(item)}</li>`).join("")}</ul></div>`).join("")}</div></div></section>
    <section class="panel"><div class="panel-head"><h2 class="panel-title">Actual Strategy Parameters</h2><div class="panel-subtitle">From the authoritative summary record</div></div><div class="panel-body table-scroll"><table class="data-table"><thead><tr><th>Parameter</th><th class="num">Value</th></tr></thead><tbody>${params.map(([key, value]) => `<tr><td class="mono">${esc(key)}</td><td class="num mono">${esc(JSON.stringify(value))}</td></tr>`).join("")}</tbody></table></div></section>`;
}

async function renderTrades() {
  const data = await getJSON("/api/trades", state.tradeFilters);
  state.trades = data.items || [];
  const options = (items, selected, blank = "All") => `<option value="">${blank}</option>${items.map((item) => `<option value="${esc(item.value)}" ${item.value === selected ? "selected" : ""}>${esc(item.label)}</option>`).join("")}`;
  const periods = ["development", "validation", "holdout"].map((value) => ({ value, label: value[0].toUpperCase() + value.slice(1) }));
  const statuses = [...new Set(state.trades.map((trade) => trade.status).filter(Boolean))].sort().map((value) => ({ value, label: value }));
  const rows = state.trades.map((trade) => `<tr data-hash="${esc(trade.decision_hash || "")}">
    <td class="mono">${esc(trade.trade_id)}</td><td class="mono">${esc(trade.entry_time)}</td><td>${badge(trade.side)}</td>
    <td class="num mono">${fmt(trade.entry_price)}</td><td class="num mono">${fmt(trade.stop)}</td><td class="num mono">${fmt(trade.tp1)}</td><td class="num mono">${fmt(trade.tp2)}</td>
    <td class="num mono">${esc(trade.exit_time || "—")}</td><td class="num ${tone(trade.realized_r)}">${fmtR(trade.realized_r)}</td><td class="num">${esc(trade.bars_held ?? "—")}</td><td>${badge(trade.status)}</td><td class="mono">${esc(shortHash(trade.decision_hash))}</td></tr>`).join("");
  document.getElementById("page").innerHTML = `${pageHeading("Trade Explorer", "Actual simulated trades from the authoritative Phase E experiment.")}
    <section class="panel"><div class="panel-head"><div><h2 class="panel-title">Simulated Trades</h2><div class="panel-subtitle">${data.total} matching trades · all records are closed historical simulations</div></div><div class="toolbar">
      <div class="field"><label for="trade-side">Direction</label><select id="trade-side">${options([{value:"buy",label:"Buy"},{value:"sell",label:"Sell"}], state.tradeFilters.side)}</select></div>
      <div class="field"><label for="trade-period">Entry split</label><select id="trade-period">${options(periods, state.tradeFilters.period)}</select></div>
      <div class="field"><label for="trade-status">Exit status</label><select id="trade-status">${options(statuses, state.tradeFilters.status)}</select></div>
      <div class="field"><label for="trade-sort">Sort</label><select id="trade-sort">${options([{value:"entry_time_desc",label:"Newest entry"},{value:"entry_time_asc",label:"Oldest entry"},{value:"realized_r_desc",label:"Highest R"},{value:"realized_r_asc",label:"Lowest R"},{value:"trade_id",label:"Trade ID"}], state.tradeFilters.sort, "Default")}</select></div>
    </div></div><div class="table-scroll"><table class="data-table"><thead><tr><th>ID</th><th>Entry</th><th>Side</th><th class="num">Entry</th><th class="num">Stop</th><th class="num">TP1</th><th class="num">TP2</th><th>Exit</th><th class="num">Realized R</th><th class="num">Bars</th><th>Status</th><th>Decision</th></tr></thead><tbody>${rows || `<tr><td colspan="12">${emptyState("No trades match the selected filters.")}</td></tr>`}</tbody></table></div></section>
    <div id="trade-detail"></div>`;
  ["trade-side", "trade-period", "trade-status", "trade-sort"].forEach((id) => {
    document.getElementById(id).addEventListener("change", (event) => {
      state.tradeFilters[id === "trade-side" ? "side" : id === "trade-period" ? "period" : id === "trade-status" ? "status" : "sort"] = event.target.value;
      renderTrades().catch((error) => showToast(error.message, true));
    });
  });
  document.querySelectorAll("#page tbody tr[data-hash]").forEach((row) => row.addEventListener("click", () => {
    const trade = state.trades.find((item) => item.decision_hash === row.dataset.hash);
    if (trade) renderTradeDetail(trade);
  }));
}

function renderTradeDetail(trade) {
  const exits = (trade.exits || []).map((exit) => `<tr><td>${esc(exit.exit_time)}</td><td>${esc(exit.reason)}</td><td class="num mono">${fmt(exit.price)}</td><td class="num">${fmt(exit.fraction, 2)}</td><td class="num ${tone(exit.contribution)}">${fmtR(exit.contribution)}</td><td>${exit.ambiguous ? badge("ambiguous", "amber") : "—"}</td></tr>`).join("");
  document.getElementById("trade-detail").innerHTML = `<section class="panel detail-drawer"><div class="panel-head"><div><div class="detail-title">Trade #${esc(trade.trade_id)}</div><div class="detail-sub">${esc(trade.side)} · ${esc(trade.entry_time)} → ${esc(trade.exit_time || "open")}</div></div>${badge(trade.status)}</div><div class="panel-body">
    ${keyValue([["Decision time", `<span class="mono">${esc(trade.decision_time)}</span>`], ["Decision hash", `<span class="mono">${esc(trade.decision_hash)}</span>`], ["Entry reference / price", fmt(trade.entry_price, 2)], ["Structural stop", fmt(trade.stop, 2)], ["Target 1", `${fmt(trade.tp1, 2)} · ${fmt(trade.tp1_fraction, 2)} fraction`], ["Target 2", `${fmt(trade.tp2, 2)} · ${fmt(trade.tp2_fraction, 2)} fraction`], ["Realized R", `<span class="${tone(trade.realized_r)}">${fmtR(trade.realized_r)}</span>`], ["Bars held", esc(trade.bars_held)], ["Ambiguous bars", esc(trade.ambiguous_bar_count)], ["Later period data", esc(trade.later_period_data_required)]])}
    <div class="section-title" style="margin-top:16px">Exit events</div><div class="table-scroll"><table class="data-table"><thead><tr><th>Time</th><th>Reason</th><th class="num">Price</th><th class="num">Fraction</th><th class="num">Contribution</th><th>Flag</th></tr></thead><tbody>${exits || `<tr><td colspan="6">${emptyState("No exit events recorded.")}</td></tr>`}</tbody></table></div>
  </div></section>`;
}

async function renderDecisions() {
  const data = await getJSON("/api/decisions", state.decisionFilters);
  const items = data.items || [];
  const row = (item) => `<tr data-hash="${esc(item.record_hash)}"><td class="mono">${esc(item.decision_time)}</td><td>${esc(item.period)}</td><td>${badge(item.status)}</td><td>${esc(item.side || "—")}</td><td class="num">${esc(item.score ?? "—")}</td><td>${esc(item.confidence || "—")}</td><td>${esc(item.has_candidate ? "Candidate" : "No candidate")}</td><td>${esc((item.rejections || []).join(", ") || "—")}</td><td class="mono">${esc(shortHash(item.record_hash))}</td></tr>`;
  const pageButtons = [];
  const start = Math.max(1, data.page - 2), end = Math.min(data.pages, data.page + 2);
  for (let i = start; i <= end; i += 1) pageButtons.push(`<button class="button small ${i === data.page ? "active" : ""}" data-page="${i}">${i}</button>`);
  document.getElementById("page").innerHTML = `${pageHeading("Decision History", "Every displayed record is an authoritative StrategyDecision. The browser receives one server-filtered page at a time.")}
    ${infoBanner("Canonical decision records are indexed and filtered server-side; only this page is returned to the browser.")}
    <section class="panel"><div class="panel-head"><div><h2 class="panel-title">Canonical Decisions</h2><div class="panel-subtitle">${data.total.toLocaleString()} matching records · page ${data.page} of ${data.pages}</div></div><div class="toolbar">
      <div class="field"><label for="d-status">Status</label><select id="d-status"><option value="">All</option>${["candidate","rejected"].map((v) => `<option value="${v}" ${state.decisionFilters.status === v ? "selected" : ""}>${v}</option>`).join("")}</select></div>
      <div class="field"><label for="d-side">Direction</label><select id="d-side"><option value="">All</option>${["buy","sell"].map((v) => `<option value="${v}" ${state.decisionFilters.side === v ? "selected" : ""}>${v}</option>`).join("")}</select></div>
      <div class="field"><label for="d-period">Split</label><select id="d-period"><option value="">All</option>${["development","validation","holdout"].map((v) => `<option value="${v}" ${state.decisionFilters.period === v ? "selected" : ""}>${v}</option>`).join("")}</select></div>
      <div class="field"><label for="d-rejection">Rejection code</label><input id="d-rejection" value="${esc(state.decisionFilters.rejection)}" placeholder="e.g. h4_neutral"></div>
      <div class="field"><label for="d-score-min">Min score</label><input id="d-score-min" type="number" value="${esc(state.decisionFilters.score_min)}" placeholder="0"></div>
      <div class="field"><label for="d-sort">Sort</label><select id="d-sort"><option value="newest" ${state.decisionFilters.sort === "newest" ? "selected" : ""}>Newest</option><option value="oldest" ${state.decisionFilters.sort === "oldest" ? "selected" : ""}>Oldest</option></select></div>
    </div></div><div class="table-scroll"><table class="data-table"><thead><tr><th>Decision time</th><th>Split</th><th>Status</th><th>Direction</th><th class="num">Score</th><th>Confidence</th><th>Candidate</th><th>Rejections</th><th>Hash</th></tr></thead><tbody>${items.map(row).join("") || `<tr><td colspan="9">${emptyState("No decisions match the selected filters.")}</td></tr>`}</tbody></table></div><div class="pagination"><span>Page ${data.page} of ${data.pages} · ${data.total.toLocaleString()} records</span><div class="page-controls"><button class="button small" data-page="${Math.max(1,data.page-1)}" ${data.page <= 1 ? "disabled" : ""}>Prev</button>${pageButtons.join("")}<button class="button small" data-page="${Math.min(data.pages,data.page+1)}" ${data.page >= data.pages ? "disabled" : ""}>Next</button></div></div></section><div id="decision-detail"></div>`;
  const filterIds = { "d-status": "status", "d-side": "side", "d-period": "period", "d-rejection": "rejection", "d-score-min": "score_min", "d-sort": "sort" };
  Object.entries(filterIds).forEach(([id, key]) => document.getElementById(id).addEventListener("change", (event) => { state.decisionFilters[key] = event.target.value; state.decisionFilters.page = 1; renderDecisions().catch((error) => showToast(error.message, true)); }));
  document.querySelectorAll("[data-page]").forEach((button) => button.addEventListener("click", () => { state.decisionFilters.page = Number(button.dataset.page); renderDecisions().catch((error) => showToast(error.message, true)); }));
  document.querySelectorAll("#page tbody tr[data-hash]").forEach((rowNode) => rowNode.addEventListener("click", () => loadDecisionDetail(rowNode.dataset.hash)));
  const requestedHash = currentRoute().params.get("hash");
  if (requestedHash) loadDecisionDetail(requestedHash);
}

async function loadDecisionDetail(hash) {
  try {
    const record = await getJSON(`/api/decisions/${encodeURIComponent(hash)}`);
    const canonical = record.canonical || {};
    const candidate = canonical.candidate || {};
    const risk = canonical.risk || {};
    const score = canonical.score || {};
    const rejections = (canonical.rejections || []).map((item) => `${item.code}: ${item.message}`).join("\n") || "None";
    document.getElementById("decision-detail").innerHTML = `<section class="panel detail-drawer"><div class="panel-head"><div><div class="detail-title">Decision at ${esc(record.decision_time)}</div><div class="detail-sub">${esc(record.period)} · ${esc(record.status)} · hash verified on this detail request</div></div>${badge(record.status)}</div><div class="panel-body">
      ${keyValue([["Decision time", `<span class="mono">${esc(record.decision_time)}</span>`], ["Record hash", `<span class="mono">${esc(record.record_hash)}</span>`], ["Instrument", esc(canonical.instrument?.symbol || canonical.symbol)], ["Status", badge(record.status)], ["Candidate direction", esc(candidate.side || "—")], ["Score", `${esc(score.total ?? "—")} / ${esc(score.maximum ?? "—")} · ${esc(score.confidence || "—")}`], ["Session", esc(canonical.session?.reason || "—")], ["News", esc(canonical.news?.reason || "—")], ["Risk", esc(risk.status || risk.reason || "—")], ["Execution ready", esc(canonical.execution_ready)]])}
      <div class="section-title" style="margin-top:16px">Rejection reasons</div><pre class="raw-json">${esc(rejections)}</pre>
      <details class="raw-details" style="margin-top:14px"><summary>Raw canonical StrategyDecision record</summary><pre class="raw-json">${esc(JSON.stringify(record, null, 2))}</pre></details>
    </div></section>`;
  } catch (error) {
    document.getElementById("decision-detail").innerHTML = warningBanner([error.message], "error");
  }
}

async function renderData() {
  const data = await getJSON("/api/data");
  const sources = data.data?.sources || [];
  document.getElementById("page").innerHTML = `${pageHeading("Data & Datasets", "Dataset identity, row counts, checksums, and verified versus unverified metadata.")}
    ${warningBanner(data.data?.warnings || [])}
    <div class="status-grid" style="grid-template-columns:repeat(3,minmax(0,1fr));margin-bottom:16px">
      <div class="status-card ${data.data?.status === "PASS" ? "green" : "amber"}"><div class="status-card-label">File checksum validation</div><div class="status-card-value">${esc(data.data?.status || "WARNING")}</div><div class="status-card-sub">${sources.filter((s) => s.verified).length} / ${sources.length} sources verified</div></div>
      <div class="status-card amber"><div class="status-card-label">Timestamp provenance</div><div class="status-card-value">Unverified UTC</div><div class="status-card-sub">Coordinate: dataset_time</div></div>
      <div class="status-card"><div class="status-card-label">Calendar compliance</div><div class="status-card-value">Not claimed</div><div class="status-card-sub">Session / news calendars unverified</div></div>
    </div>
    <section class="panel" style="margin-bottom:16px"><div class="panel-head"><div><h2 class="panel-title">Source Datasets</h2><div class="panel-subtitle">D1, H4, M15 and D1 warm-up identity</div></div>${badge(data.data?.status || "WARNING")}</div><div class="table-scroll"><table class="data-table"><thead><tr><th>Role</th><th>Timeframe</th><th class="num">Rows</th><th>First observation</th><th>Last observation</th><th>SHA-256</th><th>Checksum</th><th>Path</th></tr></thead><tbody>${sources.map((src) => `<tr><td><strong>${esc(src.label)}</strong></td><td>${esc(src.timeframe)}</td><td class="num">${esc(src.rows)}</td><td class="mono">${esc(src.first_observation || "—")}</td><td class="mono">${esc(src.last_observation || "—")}</td><td class="mono">${esc(src.actual_checksum || src.declared_checksum || "—")}</td><td>${src.verified ? badge("verified", "green") : badge("warning", "amber")}</td><td class="mono">${esc(src.path)}</td></tr>`).join("")}</tbody></table></div></section>
    <div class="section-grid grid-equal"><section class="panel"><div class="panel-head"><h2 class="panel-title">Verified Information</h2></div><div class="panel-body"><ul class="list-clean"><li>Source CSV rows and SHA-256 checksums are checked against the run manifest.</li><li>Manifest identities are compared with the approved default dataset checksums.</li><li>Required artifact files are read-only and parsed before display.</li></ul></div></section><section class="panel"><div class="panel-head"><h2 class="panel-title">Unverified Information</h2></div><div class="panel-body"><ul class="list-clean"><li>Absolute UTC provenance.</li><li>Holiday and session-calendar compliance.</li><li>Economic-news calendar compliance.</li><li>Spread, commission, financing, and slippage.</li><li>Instrument contract economics and monetary sizing.</li></ul></div></section></div>`;
}

async function renderReports() {
  const data = await getJSON("/api/reports");
  const artifacts = data.artifacts || [];
  document.getElementById("page").innerHTML = `${pageHeading("Reports & Results", "Authoritative run summary, manifest, artifacts, and validation status.")}
    ${warningBanner(data.warnings)}
    <div class="status-grid" style="grid-template-columns:repeat(3,minmax(0,1fr));margin-bottom:16px">
      <div class="status-card ${data.record_validation?.status === "PASS" ? "green" : "amber"}"><div class="status-card-label">Record validation</div><div class="status-card-value">${esc(data.record_validation?.status || "WARNING")}</div><div class="status-card-sub">${esc(data.record_validation?.scope || "")}</div></div>
      <div class="status-card"><div class="status-card-label">Run identity</div><div class="status-card-value" style="font-size:12px"><span class="mono">${esc(data.summary?.config_fingerprint || "—")}</span></div><div class="status-card-sub">Configuration fingerprint</div></div>
      <div class="status-card ${data.summary?.data?.status === "PASS" ? "green" : "amber"}"><div class="status-card-label">Dataset validation</div><div class="status-card-value">${esc(data.summary?.data?.status || "WARNING")}</div><div class="status-card-sub">SHA-256 source checksums</div></div>
    </div>
    <div class="panel" style="margin-bottom:16px"><div class="panel-head"><div><h2 class="panel-title">Run Artifacts</h2><div class="panel-subtitle">${esc(data.artifact_dir)}</div></div>${labelChip("GROSS BASELINE - EXECUTION COSTS EXCLUDED")}</div><div class="table-scroll"><table class="data-table"><thead><tr><th>Artifact</th><th>Relative path</th><th class="num">Size</th><th>Parse status</th></tr></thead><tbody>${artifacts.map((item) => `<tr><td class="mono">${esc(item.name)}</td><td class="mono">${esc(item.path)}</td><td class="num">${item.bytes ? `${(item.bytes / 1024 / 1024).toFixed(2)} MB` : "missing"}</td><td>${item.error ? badge("invalid", "red") : item.parsed ? badge("available", "green") : item.name === "decisions.jsonl" ? badge("on demand", "blue") : badge("warning", "amber")}</td></tr>`).join("")}</tbody></table></div></div>
    <div class="section-grid grid-equal"><section class="panel"><div class="panel-head"><h2 class="panel-title">Record Contract</h2></div><div class="panel-body">${keyValue([["Authoritative", esc(data.manifest?.record_contract?.authoritative)], ["Canonical records", esc(data.manifest?.record_contract?.canonical_records_enabled)], ["Schema", esc(data.manifest?.record_contract?.schema)], ["Version", esc(data.manifest?.record_contract?.schema_version)], ["Decision source", esc(data.manifest?.record_contract?.decision_source)], ["Trades with hash", esc(data.summary?.overall?.trades_with_decision_hash)]])}</div></section><section class="panel"><div class="panel-head"><h2 class="panel-title">Cost and Time Boundaries</h2></div><div class="panel-body">${keyValue([["Gross only", esc(data.manifest?.costs?.gross_only)], ["Costs applied", esc(data.manifest?.costs?.costs_applied)], ["Monetary metrics", esc(data.manifest?.costs?.monetary_metrics)], ["Net profitability", esc(data.manifest?.costs?.net_profitability)], ["Absolute UTC provenance", esc(data.manifest?.time_semantics?.absolute_utc_provenance)], ["Session compliance", esc(data.manifest?.time_semantics?.session_compliance)], ["News compliance", esc(data.manifest?.time_semantics?.news_compliance)]])}</div></section></div>`;
}

async function renderSystem() {
  const data = await getJSON("/api/system");
  const capabilities = (data.capabilities || []).map((cap) => `<div class="capability"><div><div class="capability-name">${esc(cap.name)}</div><div class="capability-detail">${esc(cap.detail)}</div></div>${badge(cap.status)}</div>`).join("");
  document.getElementById("page").innerHTML = `${pageHeading("System & Safety", "Read-only capability boundaries and explicit execution-disabled status.")}
    ${warningBanner(["This dashboard cannot submit broker orders, modify positions, enable demo/live execution, or change broker/account settings."])}
    <div class="status-grid" style="grid-template-columns:repeat(4,minmax(0,1fr));margin-bottom:16px">
      <div class="status-card red"><div class="status-card-label">Execution status</div><div class="status-card-value">DISABLED</div><div class="status-card-sub">${esc(data.execution?.reason)}</div></div>
      <div class="status-card"><div class="status-card-label">Application mode</div><div class="status-card-value">Research</div><div class="status-card-sub">Read-only browser surface</div></div>
      <div class="status-card"><div class="status-card-label">Order submission</div><div class="status-card-value negative">BLOCKED</div><div class="status-card-sub">No order route exists</div></div>
      <div class="status-card amber"><div class="status-card-label">Demo / live</div><div class="status-card-value">BLOCKED</div><div class="status-card-sub">No execution adapter</div></div>
    </div>
    <div class="section-grid grid-2" style="margin-bottom:16px"><section class="panel"><div class="panel-head"><div><h2 class="panel-title">Capabilities</h2><div class="panel-subtitle">Available, blocked, and unverified surfaces</div></div>${badge("read-only", "blue")}</div><div class="panel-body">${capabilities}</div></section><section class="panel"><div class="panel-head"><h2 class="panel-title">Execution Boundary</h2></div><div class="panel-body">${keyValue([["Dashboard execution", "DISABLED"], ["Order submission", "BLOCKED"], ["Broker adapter", "NOT CONNECTED"], ["Demo execution", "BLOCKED"], ["Live execution", "BLOCKED"], ["Settings execution mode", esc(data.execution?.settings_execution_mode || "—")], ["Settings trading mode", esc(data.execution?.settings_trading_mode || "—")]])}<div class="mini-callout" style="margin-top:14px">The settings snapshot may describe paper-mode configuration, but this application does not connect that configuration to an execution path.</div></div></section></div>
    <section class="panel"><div class="panel-head"><h2 class="panel-title">Unresolved Limitations</h2></div><div class="panel-body"><ul class="list-clean">${(data.limitations || []).map((item) => `<li>${esc(item)}</li>`).join("")}</ul></div></section>`;
}

async function renderConfig() {
  const data = await getJSON("/api/config");
  const snapshot = data.snapshot || {};
  const rows = Object.entries(snapshot).sort(([a], [b]) => a.localeCompare(b)).map(([key, value]) => `<tr><td class="mono">${esc(key)}</td><td class="mono">${esc(typeof value === "object" ? JSON.stringify(value) : value)}</td></tr>`).join("");
  document.getElementById("page").innerHTML = `${pageHeading("Configuration", "Read-only effective settings with sensitive values and terminal paths redacted.")}
    ${data.warning ? warningBanner([data.warning]) : ""}${data.error ? warningBanner([data.error], "error") : ""}
    <section class="panel"><div class="panel-head"><div><h2 class="panel-title">Effective Settings Snapshot</h2><div class="panel-subtitle">Source: ${esc(data.source || "unavailable")}</div></div>${data.available ? badge("read-only", "blue") : badge("unavailable", "red")}</div><div class="panel-body flush">${data.available ? `<div class="table-scroll"><table class="data-table"><thead><tr><th>Setting</th><th>Value</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState("No settings snapshot available.")}</div></section>
    <div class="info-banner" style="margin-top:16px"><span class="warning-mark">i</span><div>Secrets and sensitive filesystem paths are redacted. Settings are displayed for inspection only; this dashboard does not modify them.</div></div>`;
}

async function renderChartPage() {
  document.getElementById("page").innerHTML = `${pageHeading("Market Chart", "Historical D1, H4, and M15 research candles from the validated datasets.")}
    ${warningBanner(["HISTORICAL RESEARCH DATA - NOT LIVE PRICES. Trade markers are available only on the M15 view."])}
    <section class="panel"><div class="panel-head"><div><h2 class="panel-title">Historical Candles</h2><div class="panel-subtitle">Actual validated research data · dataset-time</div></div><div class="chart-toolbar"><div class="field"><label for="chart-timeframe">Timeframe</label><select id="chart-timeframe"><option value="d1">D1</option><option value="h4">H4</option><option value="m15">M15</option></select></div><div class="field"><label for="chart-limit">Candles</label><select id="chart-limit"><option value="90">90</option><option value="180">180</option><option value="300">300</option><option value="400">400</option></select></div></div></div><div class="panel-body"><div class="chart-shell"><canvas id="market-chart"></canvas></div><div class="chart-caption" id="market-chart-caption">Loading historical research data…</div></div></section>`;
  const timeframe = document.getElementById("chart-timeframe");
  const limit = document.getElementById("chart-limit");
  const load = () => loadChartInto("market-chart", "market-chart-caption", timeframe.value, Number(limit.value));
  timeframe.addEventListener("change", load); limit.addEventListener("change", load);
  load();
}

async function loadChartInto(canvasId, captionId, timeframe, limit) {
  const canvas = document.getElementById(canvasId);
  const caption = document.getElementById(captionId);
  if (!canvas) return;
  try {
    const payload = await getJSON("/api/candles", { timeframe, limit });
    state.chartPayload = payload;
    drawCandles(canvas, payload);
    caption.textContent = `${payload.timeframe} · ${payload.candles.length} candles · ${payload.start || "no data"} → ${payload.end || "no data"} · ${payload.historical_label || "historical research data"}${payload.marker_note ? ` · ${payload.marker_note}` : ""}`;
  } catch (error) {
    caption.textContent = `Chart unavailable: ${error.message}`;
    const ctx = canvas.getContext("2d");
    if (ctx) { const rect = canvas.getBoundingClientRect(); canvas.width = Math.max(300, rect.width); canvas.height = Math.max(200, rect.height); ctx.clearRect(0, 0, canvas.width, canvas.height); ctx.fillStyle = "#89a1ba"; ctx.font = "12px Segoe UI"; ctx.fillText("Historical data could not be validated or loaded.", 18, 30); }
  }
}

function drawCandles(canvas, payload) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(320, rect.width || 700);
  const height = Math.max(220, rect.height || 320);
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const candles = payload.candles || [];
  if (!candles.length) {
    ctx.fillStyle = "#89a1ba"; ctx.font = "12px Segoe UI"; ctx.fillText("No historical candles returned.", 18, 30); return;
  }
  const left = 12, right = 62, top = 22, bottom = 28;
  const plotW = width - left - right, plotH = height - top - bottom;
  let low = Math.min(...candles.map((c) => c.low)), high = Math.max(...candles.map((c) => c.high));
  const pad = Math.max((high - low) * 0.08, 0.01);
  low -= pad; high += pad;
  const y = (value) => top + (high - value) / (high - low) * plotH;
  const step = plotW / candles.length;
  ctx.strokeStyle = "#17324b"; ctx.lineWidth = 1;
  for (let i = 0; i <= 5; i += 1) {
    const yy = top + (plotH / 5) * i;
    ctx.beginPath(); ctx.moveTo(left, yy); ctx.lineTo(width - right, yy); ctx.stroke();
    ctx.fillStyle = "#718ba3"; ctx.font = "10px Segoe UI"; ctx.textAlign = "left"; ctx.fillText((high - (high - low) * i / 5).toFixed(2), width - right + 8, yy + 3);
  }
  candles.forEach((candle, index) => {
    const x = left + step * index + step / 2;
    const green = candle.close >= candle.open;
    const color = green ? "#35d07d" : "#ff6170";
    ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, y(candle.high)); ctx.lineTo(x, y(candle.low)); ctx.stroke();
    const bodyW = Math.max(2, Math.min(8, step * 0.62));
    const yOpen = y(candle.open), yClose = y(candle.close);
    ctx.fillRect(x - bodyW / 2, Math.min(yOpen, yClose), bodyW, Math.max(1, Math.abs(yClose - yOpen)));
  });
  const markers = payload.markers || [];
  if (markers.length) {
    const timeToIndex = new Map(candles.map((c, i) => [c.time, i]));
    markers.forEach((marker) => {
      const idx = timeToIndex.get(marker.entry_time);
      if (idx === undefined) return;
      const x = left + step * idx + step / 2;
      ctx.strokeStyle = marker.side === "sell" ? "#ff6170" : "#4389f7";
      ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(x, y(marker.entry_price), 3, 0, Math.PI * 2); ctx.stroke();
      [["stop", "#ff6170"], ["tp1", "#35d07d"], ["tp2", "#2dd4bf"]].forEach(([key, color]) => {
        if (!isNum(marker[key])) return;
        ctx.strokeStyle = color; ctx.globalAlpha = 0.35; ctx.setLineDash([3, 4]);
        ctx.beginPath(); ctx.moveTo(x + 4, y(marker[key])); ctx.lineTo(Math.min(width - right, x + 24), y(marker[key])); ctx.stroke();
        ctx.setLineDash([]); ctx.globalAlpha = 1;
      });
    });
  }
  ctx.fillStyle = "#6f8aa2"; ctx.font = "10px Segoe UI"; ctx.textAlign = "left";
  ctx.fillText(payload.historical_label || "HISTORICAL RESEARCH DATA", left, height - 8);
}

async function route() {
  const { route: routeName } = currentRoute();
  const pageRoute = PAGE_META[routeName] ? routeName : "dashboard";
  state.route = pageRoute;
  setActiveNav(pageRoute);
  const meta = PAGE_META[pageRoute];
  document.title = `${meta[0]} · Trantor`;
  document.getElementById("page").innerHTML = loading();
  try {
    if (!state.overview && pageRoute !== "dashboard") state.overview = await refreshOverview();
    if (pageRoute === "dashboard") await renderDashboard();
    else if (pageRoute === "research") await renderResearch();
    else if (pageRoute === "strategy") await renderStrategy();
    else if (pageRoute === "chart") await renderChartPage();
    else if (pageRoute === "trades") await renderTrades();
    else if (pageRoute === "decisions") await renderDecisions();
    else if (pageRoute === "data") await renderData();
    else if (pageRoute === "reports") await renderReports();
    else if (pageRoute === "system") await renderSystem();
    else if (pageRoute === "config") await renderConfig();
  } catch (error) {
    document.getElementById("page").innerHTML = `${pageHeading(meta[0], meta[1])}${warningBanner([error.message], "error")}<div class="panel"><div class="panel-body">${emptyState("This view could not be loaded from the validated artifacts.")}</div></div>`;
    showToast(error.message, true);
  }
}

function init() {
  document.getElementById("sidebar-toggle").addEventListener("click", () => {
    document.getElementById("sidebar").classList.toggle("collapsed");
  });
  document.querySelectorAll("[data-route]").forEach((item) => item.addEventListener("click", () => {
    if (window.innerWidth < 760) document.getElementById("sidebar").classList.remove("open");
  }));
  window.addEventListener("hashchange", () => route());
  refreshOverview().then(() => route()).catch((error) => {
    document.getElementById("page").innerHTML = `${pageHeading("Dashboard", "Read-only research surface")}${warningBanner([`Dashboard data could not be loaded: ${error.message}`], "error")}`;
    showToast(error.message, true);
  });
}

document.addEventListener("DOMContentLoaded", init);
