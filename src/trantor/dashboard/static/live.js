"use strict";

// Operations views. Everything here reads /api/live/* (the runtime database,
// opened read-only). Values are labelled by provenance; nothing is invented
// when the runtime is stopped or the feed is not live.

state.liveStatus = null;
let liveTimer = null;
const LIVE_REFRESH_MS = 3000;

function stopLiveRefresh() {
  if (liveTimer) { window.clearInterval(liveTimer); liveTimer = null; }
}

function startLiveRefresh(fn) {
  stopLiveRefresh();
  liveTimer = window.setInterval(() => { fn().catch(() => {}); }, LIVE_REFRESH_MS);
}

async function loadLiveStatus() {
  const status = await getJSON("/api/live/status");
  state.liveStatus = status;
  setTopbar(state.overview);
  return status;
}

const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { hour12: false }) : "—");
const fmtUtc = (iso) => (iso ? `${String(iso).slice(0, 16).replace("T", " ")} UTC` : "—");
const ageText = (seconds) => (isNum(seconds) ? (seconds < 120 ? `${seconds.toFixed(0)} s ago` : `${(seconds / 60).toFixed(1)} min ago`) : "—");

function provenanceChips(status) {
  const chips = [];
  if (!status?.available || !status.running) chips.push(labelChip("RUNTIME NOT RUNNING - NO LIVE DATA", "red"));
  else {
    const feed = status.market?.status;
    chips.push(feed === "live" ? labelChip("LIVE DATA", "green") : labelChip(`FEED ${humanStatus(feed).toUpperCase()}`, "red"));
    if (status.profile === "paper") chips.push(labelChip("PAPER SIMULATION", "amber"));
    if (status.profile === "shadow") chips.push(labelChip("SHADOW - HYPOTHETICAL ONLY", "amber"));
  }
  chips.push(labelChip("DEMO ACCOUNT FEED", "blue"));
  chips.push(labelChip("BROKER ORDERS: BLOCKED", "red"));
  chips.push(labelChip("LIVE ACCOUNT: NOT AUTHORIZED", "red"));
  return `<div class="live-strip">${chips.join("")}</div>`;
}

function runtimeBanner(status) {
  if (!status?.available) return warningBanner([status?.reason || "No runtime database found."], "error");
  if (!status.running) return warningBanner([`The runtime is not running (last status ${ageText(status.status_age_seconds)}${status.stopped_at ? `, stopped ${fmtTime(status.stopped_at)}` : ""}). Prices are not shown; values below are the last recorded state.`], "error");
  const warnings = [];
  if (status.kill_switch?.engaged) warnings.push(`EMERGENCY STOP ENGAGED: ${status.kill_switch.reason || "no reason recorded"}. New entries are blocked; positions are still monitored.`);
  if (status.entries_halted) warnings.push(`New entries halted: ${status.entries_halted}`);
  const blockers = status.market?.entry_blockers || [];
  if (blockers.length) warnings.push(`Entry blockers: ${blockers.join("; ")}`);
  return warnings.length ? warningBanner(warnings, status.kill_switch?.engaged || status.entries_halted ? "error" : "warning") : "";
}

function liveCard(label, value, sub, klass = "") {
  return `<div class="status-card ${klass}"><div class="status-card-label">${esc(label)}</div><div class="status-card-value">${value}</div><div class="status-card-sub">${sub || ""}</div></div>`;
}

function liveStatusCards(status) {
  const market = status.market || {};
  const quote = market.quote;
  const risk = status.risk || {};
  const last = status.last_decision || {};
  const feedClass = market.status === "live" ? "green" : "red";
  return `<div class="status-grid">
    ${liveCard("Runtime", status.running ? "RUNNING" : "STOPPED", status.running ? `${esc(status.profile)} · up ${ageText(status.uptime_seconds).replace(" ago", "")}` : esc(status.reason || "not running"), status.running ? "green" : "red")}
    ${liveCard("Market feed", esc(humanStatus(market.status || "unknown")).toUpperCase(), `MT5 ${market.connection?.connected ? "connected" : "disconnected"} · tick ${ageText(market.last_tick?.age_seconds)}`, feedClass)}
    ${liveCard("XAUUSD-P bid / ask", quote ? `${fmt(quote.bid)} / ${fmt(quote.ask)}` : "—", quote ? `spread ${fmt(quote.spread)} · ${esc(quote.label)}` : "no live quote", quote ? "" : "amber")}
    ${liveCard("Last decision", last.decision_time ? esc(humanStatus(last.status)) : "—", last.decision_time ? `${fmtUtc(last.decision_time)} · score ${esc(last.score)} · ${esc(humanStatus(last.outcome))}` : "none yet")}
    ${liveCard("Risk today", risk.daily_locked_out ? "LOCKED OUT" : `${esc(risk.entries_today ?? 0)}/${esc(risk.max_daily_entries ?? 3)} entries`, `daily loss ${fmtPct(risk.daily_loss_fraction)} of ${fmtPct(risk.daily_loss_limit, 0)} · open ${esc(risk.open_positions ?? 0)}/${esc(risk.max_concurrent_positions ?? 2)}`, risk.daily_locked_out ? "red" : "")}
    ${liveCard("Emergency stop", status.kill_switch?.engaged ? "ENGAGED" : "RELEASED", status.kill_switch?.engaged ? esc(status.kill_switch.reason) : "entries permitted by gate", status.kill_switch?.engaged ? "red" : "green")}
  </div>`;
}

// -- Live market -----------------------------------------------------------------

async function renderLive() {
  const status = await loadLiveStatus();
  const market = status.market || {};
  const session = status.session || {};
  const tb = market.time_basis || {};
  const frames = market.frames || {};
  const frameRow = (tf) => {
    const f = frames[tf];
    if (!f) return `<tr><td>${tf.toUpperCase()}</td><td colspan="6" class="muted">unavailable</td></tr>`;
    const last = f.last_completed || {};
    const q = f.quality || {};
    return `<tr><td><strong>${tf.toUpperCase()}</strong></td><td class="mono">${fmtUtc(last.close_time)}</td><td class="num">${fmt(last.open)} / ${fmt(last.high)} / ${fmt(last.low)} / ${fmt(last.close)}</td><td class="num">${esc(last.tick_volume ?? "—")}</td><td>${f.forming ? badge("forming", "amber") : badge("none", "blue")}</td><td class="num">${esc(q.completed)} · gaps ${esc(q.non_weekend_gap_count ?? 0)}</td><td>${q.clean ? badge("clean", "green") : badge("issues", "red")}</td></tr>`;
  };
  document.getElementById("page").innerHTML = `${pageHeading("Live Market", "XAUUSD-P from the connected MT5 demo terminal. Completed candles only feed the strategy.")}
    ${provenanceChips(status)}${runtimeBanner(status)}
    ${liveStatusCards(status)}
    <div class="section-grid grid-2" style="margin:16px 0">
      <section class="panel"><div class="panel-head"><div><h2 class="panel-title">Quote &amp; freshness</h2><div class="panel-subtitle">Bid/ask are executable sides; bars are bid-based (observed)</div></div>${market.status === "live" ? labelChip("LIVE DATA", "green") : labelChip("NOT LIVE", "red")}</div><div class="panel-body">
        <div class="quote-row"><div><div class="metric-label">Bid</div><div class="quote-value">${market.quote ? fmt(market.quote.bid) : "—"}</div></div><div><div class="metric-label">Ask</div><div class="quote-value">${market.quote ? fmt(market.quote.ask) : "—"}</div></div><div><div class="metric-label">Spread</div><div class="quote-value">${market.quote ? fmt(market.quote.spread) : "—"}</div></div></div>
        ${keyValue([
          ["Last tick (broker raw)", esc(market.last_tick?.raw_broker_time || "—")],
          ["Last tick (normalized UTC)", esc(market.last_tick?.normalized_utc || "—")],
          ["Tick age", ageText(market.last_tick?.age_seconds)],
          ["Spread samples (min / median / max)", market.spread_stats ? `${fmt(market.spread_stats.min)} / ${fmt(market.spread_stats.median)} / ${fmt(market.spread_stats.max)} (${market.spread_stats.samples})` : "—"],
          ["Latest M15 close lag", isNum(market.m15_lag_seconds) ? `${fmt(market.m15_lag_seconds / 60, 1)} min` : "—"],
          ["Broker clock offset", tb.offset_hours === null || tb.offset_hours === undefined ? "unknown" : `UTC${tb.offset_hours >= 0 ? "+" : ""}${tb.offset_hours} h`],
          ["Clock provenance", `${badge(tb.verified ? "verified" : "unverified")} ${esc(tb.provenance || "")}`],
          ["MT5 ping", isNum(market.connection?.ping_ms) ? `${fmt(market.connection.ping_ms, 1)} ms` : "—"],
          ["Reconnects", esc(market.connection?.reconnect_count ?? "—")],
        ])}
      </div></section>
      <section class="panel"><div class="panel-head"><div><h2 class="panel-title">Sessions</h2><div class="panel-subtitle">${esc(session.note || "IANA windows")}</div></div>${labelChip("BROKER CALENDAR UNVERIFIED", "amber")}</div><div class="panel-body">
        ${keyValue([
          ["London (08:00-16:30 Europe/London)", session.london ? `${fmtUtc(session.london.open_utc)} → ${fmtUtc(session.london.close_utc)} ${session.london.active ? badge("open", "green") : badge("closed", "blue")}` : "—"],
          ["New York (08:00-17:00 America/New_York)", session.new_york ? `${fmtUtc(session.new_york.open_utc)} → ${fmtUtc(session.new_york.close_utc)} ${session.new_york.active ? badge("open", "green") : badge("closed", "blue")}` : "—"],
          ["Strategy entry window (overlap)", session.overlap?.open_utc ? `${fmtUtc(session.overlap.open_utc)} → ${fmtUtc(session.overlap.close_utc)} ${session.overlap.active ? badge("active", "green") : badge("inactive", "amber")}` : "no overlap today"],
          ["Broker ticks observed now", session.broker_ticks_observed ? badge("yes", "green") : badge("no", "red")],
          ["Broker session calendar", badge("unverified", "amber")],
        ])}
      </div></section>
    </div>
    <section class="panel" style="margin-bottom:16px"><div class="panel-head"><div><h2 class="panel-title">Completed candles</h2><div class="panel-subtitle">Normalized UTC (broker raw - measured offset). A forming bar is never passed to the strategy.</div></div></div><div class="panel-body flush"><div class="table-scroll"><table class="data-table"><thead><tr><th>TF</th><th>Last completed close</th><th class="num">O / H / L / C</th><th class="num">Tick vol</th><th>Forming</th><th class="num">Completed · gaps</th><th>Quality</th></tr></thead><tbody>${["1d", "4h", "15m"].map(frameRow).join("")}</tbody></table></div></div></section>
    <div class="section-grid grid-3">
      ${["1d", "4h", "15m"].map((tf) => `<section class="panel"><div class="panel-head"><div><h2 class="panel-title">${tf.toUpperCase()}</h2><div class="panel-subtitle" id="live-caption-${tf}">loading…</div></div></div><div class="panel-body"><div class="chart-shell small"><canvas id="live-chart-${tf}"></canvas></div></div></section>`).join("")}
    </div>`;
  await Promise.all(["1d", "4h", "15m"].map(drawLiveChart));
  startLiveRefresh(async () => { if (state.route === "live") await renderLiveQuiet(); });
}

async function renderLiveQuiet() {
  const scroll = window.scrollY;
  await renderLive();
  window.scrollTo(0, scroll);
}

async function drawLiveChart(tf) {
  const canvas = document.getElementById(`live-chart-${tf}`);
  const caption = document.getElementById(`live-caption-${tf}`);
  if (!canvas) return;
  const limits = { "1d": 90, "4h": 90, "15m": 96 };
  const payload = await getJSON("/api/live/candles", { tf });
  const bars = (payload.bars || []).slice(-limits[tf]);
  drawCandles(canvas, {
    candles: bars.map((b) => ({ time: b.t, open: b.o, high: b.h, low: b.l, close: b.c, complete: b.complete })),
    historical_label: payload.available ? `${payload.label} · last candle ${bars.length && bars[bars.length - 1].complete === false ? "FORMING (outlined)" : "complete"}` : "NO LIVE DATA",
  });
  caption.textContent = payload.available ? `${bars.length} bars · ${payload.label} · ${payload.completion_basis}` : payload.reason || "unavailable";
}

// -- Signals & decisions -------------------------------------------------------------

state.signalFilter = "";

async function renderSignals() {
  const status = await loadLiveStatus();
  const data = await getJSON("/api/live/decisions", { status: state.signalFilter, limit: 200 });
  const counts = status.decision_counts || {};
  const rows = (data.items || []).map((d) => `<tr class="clickable" data-decision="${esc(d.decision_id)}">
    <td class="mono nowrap">${fmtUtc(d.decision_time)}</td><td>${badge(d.status)}</td><td>${esc(d.side ? d.side.toUpperCase() : "—")}</td>
    <td class="num">${esc(d.score)}/8</td><td>${esc(d.confidence)}</td><td>${badge(d.outcome)}</td>
    <td class="small">${esc((d.rejection_codes || []).join(", ") || "—")}</td><td>${badge(d.news_status || "unknown")}</td><td class="mono small">${shortHash(d.record_hash)}</td></tr>`).join("");
  document.getElementById("page").innerHTML = `${pageHeading("Signals & Decisions", "Approved strategy decisions recorded by the runtime. Rejected candidates are never discarded.")}
    ${provenanceChips(status)}${runtimeBanner(status)}
    ${metricGrid([
      ["Decisions recorded", esc((counts.candidate || 0) + (counts.rejected || 0)), "canonical records"],
      ["Candidates", esc(counts.candidate || 0), "passed every strategy rule"],
      ["Rejected", esc(counts.rejected || 0), "with explicit reasons"],
      ["Strategy", esc(status.strategy?.variant_id || "—"), `v${esc(status.strategy?.version || "—")}`],
    ])}
    <section class="panel" style="margin-top:16px"><div class="panel-head"><div><h2 class="panel-title">Decision history</h2><div class="panel-subtitle">Click a row for the confluence breakdown, context and canonical record</div></div><div class="field"><label for="signal-filter">Status</label><select id="signal-filter"><option value="">All</option><option value="candidate">Candidates</option><option value="rejected">Rejected</option></select></div></div>
      <div class="panel-body flush"><div class="table-scroll"><table class="data-table"><thead><tr><th>Decision (UTC)</th><th>Status</th><th>Side</th><th class="num">Score</th><th>Confidence</th><th>Outcome</th><th>Rejections</th><th>News</th><th>Record hash</th></tr></thead><tbody>${rows || `<tr><td colspan="9">${emptyState("No runtime decisions recorded yet.")}</td></tr>`}</tbody></table></div></div></section>
    <section class="panel" style="margin-top:16px" id="signal-detail"></section>`;
  const filter = document.getElementById("signal-filter");
  filter.value = state.signalFilter;
  filter.addEventListener("change", () => { state.signalFilter = filter.value; renderSignals().catch((e) => showToast(e.message, true)); });
  document.querySelectorAll("tr[data-decision]").forEach((row) => row.addEventListener("click", () => loadSignalDetail(row.dataset.decision)));
}

async function loadSignalDetail(id) {
  const data = await getJSON(`/api/live/decisions/${encodeURIComponent(id)}`);
  const env = data.envelope || {};
  const rec = data.record || {};
  const factors = (env.score?.factors || []).map((f) => `<div class="factor-row"><span>${esc(humanStatus(f.name))}<div class="muted small">${esc(f.detail)}</div></span><span>${f.available ? badge(f.awarded ? "awarded" : "not awarded", f.awarded ? "green" : "blue") : badge("unavailable", "amber")}</span><span class="num"><strong>${f.awarded ? `+${esc(f.points)}` : "0"}</strong></span></div>`).join("");
  const p = env.proposal;
  document.getElementById("signal-detail").innerHTML = `<div class="panel-head"><div><h2 class="panel-title">Decision ${esc(data.decision_id)}</h2><div class="panel-subtitle">${esc(env.time?.decision_time_normalized_utc)} · ${esc(env.profile)} · ${(env.labels || []).map((l) => labelChip(l, "amber")).join(" ")}</div></div>${badge(data.outcome)}</div>
    <div class="panel-body"><div class="section-grid grid-3">
      <div><h3 class="section-title">Confluence ${esc(env.score?.total)}/8 · ${esc(env.score?.confidence)}</h3>${factors || emptyState("Rejected before scoring.")}</div>
      <div><h3 class="section-title">Context</h3>${keyValue([
        ["D1 bias", esc(rec.daily_bias?.state || "—")], ["D1 EMA50 / EMA200", rec.daily_bias ? `${fmt(rec.daily_bias.ema_fast)} / ${fmt(rec.daily_bias.ema_slow)}` : "—"],
        ["Prev. day high / low", rec.daily_bias ? `${fmt(rec.daily_bias.previous_high)} / ${fmt(rec.daily_bias.previous_low)}` : "—"],
        ["H4 trend", esc(rec.h4_trend?.state || "—")], ["H4 ATR14", fmt(rec.h4_trend?.atr)],
        ["H4 zone", rec.zone ? `${fmt(rec.zone.lower)} – ${fmt(rec.zone.upper)} (${esc((rec.zone.evidence || []).join(", "))})` : "—"],
        ["Session (IANA overlap)", !env.session ? "—" : (env.session.evaluated === false || env.session.reason === "not evaluated") ? badge("not evaluated (rejected earlier)", "blue") : badge(env.session.iana_overlap ? "inside" : "outside")],
        ["News", `${badge(env.news?.status || "unknown")} ${esc(env.news?.reason || "")}`],
        ["Rejections", esc((env.rejections || []).map((r) => `${r.code}: ${r.message}`).join(" | ") || "none")],
      ])}</div>
      <div><h3 class="section-title">Plan &amp; risk</h3>${keyValue([
        ["Side", p ? esc(p.side.toUpperCase()) : "—"], ["Entry reference", p ? fmt(p.entry_reference) : "—"], ["Stop", p ? fmt(p.stop) : "—"],
        ["TP1 (2R) / TP2", p ? `${fmt(p.tp1)} / ${fmt(p.tp2)}` : "—"], ["Risk distance", p ? fmt(p.risk_distance) : "—"],
        ["Risk ceiling", p ? fmtPct(p.risk_fraction_ceiling) : "—"], ["Monetary sizing", p ? badge(p.monetary_sizing) : "—"],
        ["Risk eligible", env.risk ? badge(env.risk.eligible ? "eligible" : "rejected") : "—"],
        ["Blocked reasons", esc((env.blocked_reasons || []).join("; ") || "—")],
        ["Entry lateness", isNum(env.time?.entry_lateness_seconds) ? `${fmt(env.time.entry_lateness_seconds, 0)} s` : "—"],
        ["Record hash", `<span class="mono small">${esc(data.record_hash)}</span>`],
        ["Config fingerprint", `<span class="mono small">${shortHash(env.config_fingerprint)}</span>`],
        ["Data identity", `<span class="mono small">${esc(env.data_identity)}</span>`],
      ])}</div>
    </div></div>`;
  document.getElementById("signal-detail").scrollIntoView({ behavior: "smooth", block: "start" });
}

// -- Positions & trades -----------------------------------------------------------------

async function renderPositions() {
  const status = await loadLiveStatus();
  const data = await getJSON("/api/live/positions");
  const s = data.summary || {};
  const liveOpen = new Map((status.open_positions || []).map((p) => [p.position_id, p]));
  const open = (data.positions || []).filter((p) => !p.closed);
  const closed = (data.positions || []).filter((p) => p.closed);
  const exitsText = (st) => (st.exits || []).map((e) => `${e.reason} ${fmt(e.price)} (${fmtSigned(e.r_multiple, 2)}R × ${fmt(e.fraction * 100, 0)}%)`).join("; ") || "—";
  const openRows = open.map((p) => {
    const st = p.state; const live = liveOpen.get(p.position_id) || {};
    return `<tr><td>${badge(p.mode === "paper" ? "paper" : "shadow", "amber")}</td><td>${esc(st.side.toUpperCase())}</td><td class="mono">${fmtUtc(st.entry_time)}</td><td class="num">${fmt(st.entry_price)}</td><td class="num">${fmt(st.stop_price)}${st.breakeven_active ? " (BE)" : ""}</td><td class="num">${fmt(st.tp1)} / ${fmt(st.tp2)}</td><td class="num">${fmt(st.remaining_fraction * 100, 0)}%</td><td class="num ${tone(live.unrealized_r)}">${status.running ? fmtSigned(live.unrealized_r, 2) + "R" : "—"}</td><td class="num ${tone(live.unrealized_pnl)}">${status.running ? fmtSigned(live.unrealized_pnl) : "—"}</td><td>${badge(st.status)}</td><td class="small">${esc(exitsText(st))}</td></tr>`;
  }).join("");
  const closedRows = closed.map((p) => {
    const st = p.state;
    return `<tr><td>${badge(p.mode === "paper" ? "paper" : "shadow", "amber")}</td><td>${esc(st.side.toUpperCase())}</td><td class="mono">${fmtUtc(st.entry_time)}</td><td class="mono">${fmtUtc(p.closed_at)}</td><td class="num">${fmt(st.entry_price)}</td><td class="num">${fmt(st.initial_stop)}</td><td class="num">${fmt(st.tp1)} / ${fmt(st.tp2)}</td><td class="num ${tone(st.realized_r)}">${fmtSigned(st.realized_r, 2)}R</td><td class="num ${tone(st.realized_pnl)}">${fmtSigned(st.realized_pnl)}</td><td>${badge(st.status)}</td><td class="small">${esc(exitsText(st))}</td></tr>`;
  }).join("");
  const orderRows = (data.orders || []).map((o) => `<tr><td class="mono small">${esc(o.client_order_id)}</td><td>${esc(o.side.toUpperCase())}</td><td>${badge(o.status)}</td><td class="num">${fmt(o.average_fill_price)}</td><td class="small">${esc((o.events || []).map((e) => `${e.event}→${e.status}`).join(" · "))}</td><td class="mono small">${fmtTime(o.created_at)}</td></tr>`).join("");
  document.getElementById("page").innerHTML = `${pageHeading("Positions & Trades", "Shadow (hypothetical) and paper positions managed with the conservative research fill model.")}
    ${provenanceChips(status)}${runtimeBanner(status)}
    ${warningBanner([s.label || "SIMULATION - NOT LIVE PERFORMANCE", s.inconclusive ? `Only ${s.closed || 0} closed trades: far too few for any performance conclusion.` : ""].filter(Boolean))}
    ${metricGrid([
      ["Open", esc(s.open ?? 0), "managed on completed M15 bars"],
      ["Closed", esc(s.closed ?? 0), `${esc(s.wins ?? 0)} wins / ${esc(s.losses ?? 0)} losses / ${esc(s.breakeven ?? 0)} BE`],
      ["Realized", `${fmtSigned(s.realized_r, 2)}R`, "gross; costs excluded", tone(s.realized_r)],
      ["Broker positions", "NONE", "no broker order path exists", "negative"],
    ])}
    <section class="panel" style="margin-top:16px"><div class="panel-head"><h2 class="panel-title">Open positions</h2>${status.running ? labelChip("MARKED TO LIVE BID/ASK", "green") : labelChip("RUNTIME STOPPED - NO MARK", "red")}</div><div class="panel-body flush"><div class="table-scroll"><table class="data-table"><thead><tr><th>Mode</th><th>Side</th><th>Entry (UTC)</th><th class="num">Entry</th><th class="num">Stop</th><th class="num">TP1 / TP2</th><th class="num">Remaining</th><th class="num">Unreal. R</th><th class="num">Unreal. P/L</th><th>Status</th><th>Partial exits</th></tr></thead><tbody>${openRows || `<tr><td colspan="11">${emptyState("No open shadow or paper positions.")}</td></tr>`}</tbody></table></div></div></section>
    <section class="panel" style="margin-top:16px"><div class="panel-head"><h2 class="panel-title">Closed trades</h2>${labelChip("GROSS - PAPER UNITS", "amber")}</div><div class="panel-body flush"><div class="table-scroll"><table class="data-table"><thead><tr><th>Mode</th><th>Side</th><th>Entry</th><th>Closed</th><th class="num">Entry</th><th class="num">Stop</th><th class="num">TP1 / TP2</th><th class="num">R</th><th class="num">P/L</th><th>Status</th><th>Exits</th></tr></thead><tbody>${closedRows || `<tr><td colspan="11">${emptyState("No closed shadow or paper trades yet.")}</td></tr>`}</tbody></table></div></div></section>
    <section class="panel" style="margin-top:16px"><div class="panel-head"><h2 class="panel-title">Order lifecycle (paper)</h2>${labelChip("PAPER BROKER - IN PROCESS", "amber")}</div><div class="panel-body flush"><div class="table-scroll"><table class="data-table"><thead><tr><th>Client order id</th><th>Side</th><th>Status</th><th class="num">Fill</th><th>Events</th><th>Created</th></tr></thead><tbody>${orderRows || `<tr><td colspan="6">${emptyState("No paper orders. Shadow mode creates no orders.")}</td></tr>`}</tbody></table></div></div></section>`;
  startLiveRefresh(async () => { if (state.route === "positions") { const y = window.scrollY; await renderPositions(); window.scrollTo(0, y); } });
}

// -- Risk -----------------------------------------------------------------------

function meter(value, limit) {
  const ratio = isNum(value) && isNum(limit) && limit > 0 ? Math.min(1, value / limit) : 0;
  const klass = ratio >= 1 ? "red" : ratio >= 0.6 ? "amber" : "";
  return `<div class="meter ${klass}"><span style="width:${(ratio * 100).toFixed(1)}%"></span></div>`;
}

async function renderRisk() {
  const data = await getJSON("/api/live/risk");
  const status = await loadLiveStatus();
  const r = data.risk || {};
  const rows = (data.daily || []).map((d) => `<tr><td class="mono">${esc(d.day)}</td><td class="num">${fmt(d.start_equity)}</td><td class="num">${esc(d.entries)}</td><td class="num">${esc(d.losses)}</td><td class="num ${tone(d.realized_r)}">${fmtSigned(d.realized_r, 2)}R</td><td class="num ${tone(d.realized_pnl)}">${fmtSigned(d.realized_pnl)}</td><td>${d.locked_out ? badge("locked out", "red") : badge("ok", "green")}</td></tr>`).join("");
  document.getElementById("page").innerHTML = `${pageHeading("Risk", "Capital protection first. These limits are ceilings enforced by the strategy and re-checked by the execution gate.")}
    ${provenanceChips(status)}${runtimeBanner(status)}
    ${!r.mode ? warningBanner(["No risk state recorded yet."], "error") : ""}
    <div class="status-grid">
      ${liveCard("Daily loss", fmtPct(r.daily_loss_fraction), `limit ${fmtPct(r.daily_loss_limit, 0)} ${meter(r.daily_loss_fraction, r.daily_loss_limit)}`, r.daily_locked_out ? "red" : "")}
      ${liveCard("Entries today", `${esc(r.entries_today ?? 0)} / ${esc(r.max_daily_entries ?? 3)}`, meter(r.entries_today, r.max_daily_entries))}
      ${liveCard("Open positions", `${esc(r.open_positions ?? 0)} / ${esc(r.max_concurrent_positions ?? 2)}`, `open risk ${fmt(r.open_risk_amount)} paper units ${meter(r.open_positions, r.max_concurrent_positions)}`)}
      ${liveCard("Cooldown", r.cooldown_active ? "ACTIVE" : "none", r.cooldown_until ? `until ${fmtUtc(r.cooldown_until)} · ${esc(r.consecutive_losses)} consecutive loss(es)` : `${esc(r.consecutive_losses ?? 0)} consecutive losses`, r.cooldown_active ? "amber" : "green")}
      ${liveCard("Daily lockout", r.daily_locked_out ? "LOCKED" : "no", esc(r.lockout_reason || "resets at the next UTC day"), r.daily_locked_out ? "red" : "green")}
      ${liveCard("Emergency stop", data.kill_switch?.engaged ? "ENGAGED" : "released", data.kill_switch?.engaged ? esc(data.kill_switch.reason) : "use the CLI to engage", data.kill_switch?.engaged ? "red" : "green")}
    </div>
    <div class="section-grid grid-2" style="margin:16px 0">
      <section class="panel"><div class="panel-head"><h2 class="panel-title">Risk model</h2>${labelChip("PAPER EQUITY MODEL", "amber")}</div><div class="panel-body">${keyValue([
        ["Profile", esc(data.profile || "—")], ["Paper equity", fmt(r.equity)], ["Start-of-day equity", fmt(r.start_of_day_equity)],
        ["Realized today", `${fmtSigned(r.realized_r_today, 2)}R · ${fmtSigned(r.realized_pnl_today)}`],
        ["Equity model", esc(r.equity_model || "—")], ["Trading-day boundary", esc(r.day_boundary || "—")],
        ["Risk ceilings", "High confidence 1.0% · Medium 0.5% (ceilings, not targets)"],
        ["Monetary sizing", `${badge("blocked")} contract economics not owner-confirmed`],
        ["Account balance / equity", `${badge("not shown")} live account values are not used for any decision`],
      ])}</div></section>
      <section class="panel"><div class="panel-head"><h2 class="panel-title">Emergency stop</h2>${data.kill_switch?.engaged ? badge("engaged", "red") : badge("released", "green")}</div><div class="panel-body">
        <p class="muted small">Engaging the stop blocks new entries and strategy-triggered orders. It does not close positions and does not stop position monitoring or risk enforcement. The dashboard is read-only; use the command line:</p>
        <pre class="raw-json">python -m trantor.runtime stop-entries --reason "..."\npython -m trantor.runtime resume-entries --confirm</pre>
        ${data.entries_halted ? warningBanner([`Entries halted by the runtime: ${data.entries_halted}`], "error") : ""}
      </div></section>
    </div>
    <section class="panel"><div class="panel-head"><h2 class="panel-title">Daily history</h2>${labelChip("SIMULATION", "amber")}</div><div class="panel-body flush"><div class="table-scroll"><table class="data-table"><thead><tr><th>Day (UTC)</th><th class="num">Start equity</th><th class="num">Entries</th><th class="num">Losses</th><th class="num">Realized R</th><th class="num">Realized P/L</th><th>Lockout</th></tr></thead><tbody>${rows || `<tr><td colspan="7">${emptyState("No daily risk rows yet.")}</td></tr>`}</tbody></table></div></div></section>`;
  startLiveRefresh(async () => { if (state.route === "risk") { const y = window.scrollY; await renderRisk(); window.scrollTo(0, y); } });
}

// -- Operations --------------------------------------------------------------------

state.opsLevel = "";

async function renderOps() {
  const status = await loadLiveStatus();
  const health = await getJSON("/api/live/health", { level: state.opsLevel });
  const v = status.broker_verification?.report;
  const checks = (v?.checks || []).map((c) => `<tr><td class="mono small">${esc(c.name)}</td><td>${badge(c.status)}</td><td class="mono small">${esc(typeof c.observed === "object" ? JSON.stringify(c.observed) : c.observed)}</td><td class="small">${esc(c.detail || "")}</td></tr>`).join("");
  const events = (health.events || []).map((e) => `<tr><td class="mono small nowrap">${fmtTime(e.at)}</td><td><span class="event-level ${esc(e.level)}">${esc(e.level)}</span></td><td>${esc(e.event)}</td><td class="mono small">${esc(JSON.stringify(e.detail || {}).slice(0, 220))}</td></tr>`).join("");
  const notes = (health.notifications || []).map((n) => `<tr><td class="mono small">${fmtTime(n.at)}</td><td>${esc(n.kind)}</td><td>${badge(n.status)}</td><td class="small">${esc(n.last_error || "")}</td></tr>`).join("");
  const stats = status.stats || {};
  const market = status.market || {};
  const rec = status.reconciliation;
  document.getElementById("page").innerHTML = `${pageHeading("Operations", "Runtime process, MT5 connection, broker verification, calendar, reconciliation and the event log.")}
    ${provenanceChips(status)}${runtimeBanner(status)}
    <div class="status-grid">
      ${liveCard("Runtime process", status.running ? "RUNNING" : "STOPPED", `steps ${esc(stats.steps ?? "—")} · errors ${esc(stats.step_errors ?? "—")} · memory ${isNum(stats.memory_mb) ? fmt(stats.memory_mb, 1) + " MB" : "—"}`, status.running ? "green" : "red")}
      ${liveCard("MT5 connection", market.connection?.connected ? "CONNECTED" : "DOWN", `build ${esc(market.connection?.terminal_build ?? "—")} · reconnects ${esc(market.connection?.reconnect_count ?? 0)}`, market.connection?.connected ? "green" : "red")}
      ${liveCard("Broker verification", esc(humanStatus(v?.overall || "not run")).toUpperCase(), v ? `report ${esc(v.report_id)} · sizing ${esc(v.monetary_sizing)}` : "not run", v?.overall === "economics_verified" ? "green" : "amber")}
      ${liveCard("News calendar", status.calendar?.verified ? "VERIFIED" : "UNAVAILABLE", esc(status.calendar?.failure || status.calendar?.source || "no calendar configured"), status.calendar?.verified ? "green" : "amber")}
      ${liveCard("Reconciliation", rec ? (rec.consistent ? "CONSISTENT" : "INCONSISTENT") : "—", rec ? `${fmtTime(rec.at)} · ${esc((rec.detail?.issues || []).join("; ") || "no issues")}` : "not run", rec?.consistent ? "green" : "red")}
      ${liveCard("Notifications", status.notifications?.enabled ? "TELEGRAM ON" : "DISABLED", status.notifications ? `sent ${esc(status.notifications.sent)} · failed ${esc(status.notifications.failures)} · dropped ${esc(status.notifications.dropped)}` : "—", status.notifications?.enabled ? "green" : "amber")}
    </div>
    <div class="section-grid grid-2" style="margin:16px 0">
      <section class="panel"><div class="panel-head"><h2 class="panel-title">Process &amp; scheduling</h2></div><div class="panel-body">${keyValue([
        ["Execution mode / profile", `${esc(status.execution_mode)} / ${esc(status.profile)}`],
        ["Strategy", `${esc(status.strategy?.variant_id)} v${esc(status.strategy?.version)}`],
        ["Parameters fingerprint", `<span class="mono small">${shortHash(status.strategy?.parameters_fingerprint)}</span>`],
        ["Config fingerprint", `<span class="mono small">${shortHash(status.config_fingerprint)}</span>`],
        ["Started", fmtTime(status.started_at)], ["Evaluations", esc(stats.evaluations ?? "—")],
        ["Duplicate evaluations skipped", esc(stats.duplicates_skipped ?? "—")],
        ["Evaluations not run (closed market / downtime)", esc(stats.missed_evaluations ?? "—")],
        ["Slowest step", isNum(stats.max_step_seconds) ? `${fmt(stats.max_step_seconds, 3)} s` : "—"],
        ["Database integrity", badge(health.integrity === "ok" ? "ok" : health.integrity)],
      ])}</div></section>
      <section class="panel"><div class="panel-head"><h2 class="panel-title">Notifications</h2>${labelChip("ONE-WAY - DEDUPLICATED", "blue")}</div><div class="panel-body flush"><div class="table-scroll"><table class="data-table"><thead><tr><th>At</th><th>Kind</th><th>Status</th><th>Error</th></tr></thead><tbody>${notes || `<tr><td colspan="4">${emptyState("No notifications recorded.")}</td></tr>`}</tbody></table></div></div></section>
    </div>
    <section class="panel" style="margin-bottom:16px"><div class="panel-head"><div><h2 class="panel-title">Broker metadata verification</h2><div class="panel-subtitle">observed = read live from the broker; verified = also owner-confirmed against the contract specification</div></div>${badge(v?.overall || "not run")}</div><div class="panel-body flush"><div class="table-scroll"><table class="data-table"><thead><tr><th>Check</th><th>Status</th><th>Observed</th><th>Detail</th></tr></thead><tbody>${checks || `<tr><td colspan="4">${emptyState("No verification report yet.")}</td></tr>`}</tbody></table></div></div></section>
    <section class="panel"><div class="panel-head"><h2 class="panel-title">Event log</h2><div class="field"><label for="ops-level">Minimum level</label><select id="ops-level"><option value="">Info</option><option value="warning">Warning</option><option value="error">Error</option><option value="critical">Critical</option></select></div></div><div class="panel-body flush"><div class="table-scroll"><table class="data-table"><thead><tr><th>At (local)</th><th>Level</th><th>Event</th><th>Detail</th></tr></thead><tbody>${events || `<tr><td colspan="4">${emptyState("No events.")}</td></tr>`}</tbody></table></div></div></section>`;
  const level = document.getElementById("ops-level");
  level.value = state.opsLevel;
  level.addEventListener("change", () => { state.opsLevel = level.value; renderOps().catch((e) => showToast(e.message, true)); });
}

// -- Dashboard live strip -------------------------------------------------------------

const originalRenderDashboard = renderDashboard;
renderDashboard = async function renderDashboardWithLive() {
  await originalRenderDashboard();
  let status = null;
  try { status = await loadLiveStatus(); } catch { status = null; }
  const page = document.getElementById("page");
  const heading = page.querySelector(".page-heading");
  if (!heading) return;
  const panel = document.createElement("section");
  panel.className = "panel";
  panel.style.marginBottom = "16px";
  panel.innerHTML = `<div class="panel-head"><div><h2 class="panel-title">Live operations</h2><div class="panel-subtitle">From the runtime database · approved strategy ${esc(status?.strategy?.variant_id || "xauusd_p_approved_v1")}</div></div><a class="button small" href="#/live">Live Market</a></div><div class="panel-body">${status ? provenanceChips(status) + runtimeBanner(status) + liveStatusCards(status) : warningBanner(["Live status unavailable."], "error")}</div>`;
  heading.insertAdjacentElement("afterend", panel);
};

// -- Research: strategy version comparison --------------------------------------------

const originalRenderResearch = renderResearch;
renderResearch = async function renderResearchWithComparison() {
  await originalRenderResearch();
  let data;
  try { data = await getJSON("/api/experiments"); } catch { return; }
  const rows = (data.experiments || []).map((e) => {
    const sp = e.splits || {};
    const split = (name) => sp[name] ? `${esc(sp[name].closed)} · <span class="${tone(sp[name].realized_r)}">${fmtSigned(sp[name].realized_r, 2)}R</span>` : "—";
    return `<tr><td class="mono small">${esc(e.experiment)}</td><td class="mono small">${esc(e.variant_id)}</td><td>${badge(e.time_coordinate || "unknown", e.time_coordinate === "dataset_time" ? "amber" : "blue")}</td><td class="num">${esc(e.closed)}</td><td class="num">${fmtPct(e.win_rate)}</td><td class="num ${tone(e.realized_r)}">${fmtSigned(e.realized_r, 2)}R</td><td class="num">${fmt(e.profit_factor, 2)}</td><td class="num">${fmt(e.max_drawdown_r, 1)}R</td><td class="num">${split("development")}</td><td class="num">${split("validation")}</td><td class="num">${split("holdout")}</td></tr>`;
  }).join("");
  const panel = document.createElement("section");
  panel.className = "panel";
  panel.style.marginTop = "16px";
  panel.innerHTML = `<div class="panel-head"><div><h2 class="panel-title">Strategy version comparison</h2><div class="panel-subtitle">${esc(data.note || "")}</div></div>${labelChip("RESEARCH-ONLY RESULT", "amber")}</div><div class="panel-body flush"><div class="table-scroll"><table class="data-table"><thead><tr><th>Experiment</th><th>Strategy</th><th>Time</th><th class="num">Trades</th><th class="num">Win rate</th><th class="num">Gross R</th><th class="num">PF</th><th class="num">Max DD</th><th class="num">Dev 2024</th><th class="num">Val 2025</th><th class="num">Holdout 2026</th></tr></thead><tbody>${rows || `<tr><td colspan="11">${emptyState("No experiments found.")}</td></tr>`}</tbody></table></div></div>`;
  document.getElementById("page").appendChild(panel);
};
