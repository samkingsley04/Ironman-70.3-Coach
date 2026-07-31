/* Elite Triathlon Coach Dashboard — frontend logic. No build step, no dependencies. */

const SPORT_COLOR = {
  swim: "#3b82f6",
  bike: "#7c8798",
  run: "#f97316",
  strength: "#a78bfa",
};

const state = {
  dailyMetrics: [],
  activities: [],
  pmc: [],
  weeklySummary: [],
  blocks: [],
  chatHistory: [], // [{role, content}]
};

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} failed: ${res.status}`);
  return res.json();
}

async function loadAllData() {
  const [dailyMetrics, activities, pmc, weeklySummary, blocks] = await Promise.all([
    fetchJSON("/api/daily-metrics"),
    fetchJSON("/api/activities"),
    fetchJSON("/api/pmc"),
    fetchJSON("/api/weekly-summary"),
    fetchJSON("/api/blocks"),
  ]);
  state.dailyMetrics = dailyMetrics;
  state.activities = activities;
  state.pmc = pmc;
  state.weeklySummary = weeklySummary;
  state.blocks = blocks;
}

// ---------------------------------------------------------------------------
// Small formatting helpers
// ---------------------------------------------------------------------------

function fmtDate(iso) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function fmtDateFull(iso) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
}

function round1(n) {
  return Math.round(n * 10) / 10;
}

function statusClass(status) {
  if (["Productive", "Peaking", "Maintaining"].includes(status)) return "stat-good";
  if (["Recovery"].includes(status)) return "stat-good";
  if (["Overreaching"].includes(status)) return "stat-warn";
  if (["Detraining", "Unproductive"].includes(status)) return "stat-bad";
  return "";
}

// ---------------------------------------------------------------------------
// SVG chart primitives (dependency-free)
// ---------------------------------------------------------------------------

const CHART_W = 600;

function svgEl(tag, attrs) {
  let s = `<${tag} `;
  for (const k in attrs) s += `${k}="${attrs[k]}" `;
  return s + "/>";
}

/**
 * Renders a multi-series line chart into an SVG string.
 * series: [{name, color, values: number[]}]  (values may contain null gaps)
 * labels: array of x labels (same length as values), only a few get rendered
 */
function lineChartSVG(series, labels, opts = {}) {
  const width = CHART_W;
  const height = opts.height || 140;
  const padL = 34, padR = 8, padT = 10, padB = 18;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;

  const allVals = series.flatMap((s) => s.values.filter((v) => v !== null && v !== undefined));
  let min = Math.min(...allVals);
  let max = Math.max(...allVals);
  if (opts.zeroBaseline) min = Math.min(min, 0);
  if (min === max) { min -= 1; max += 1; }
  const pad = (max - min) * 0.08;
  min -= pad;
  max += pad;

  const n = labels.length;
  const xAt = (i) => padL + (n <= 1 ? 0 : (i / (n - 1)) * innerW);
  const yAt = (v) => padT + innerH - ((v - min) / (max - min)) * innerH;

  let paths = "";
  let dots = "";
  series.forEach((s) => {
    let d = "";
    s.values.forEach((v, i) => {
      if (v === null || v === undefined) return;
      const cmd = d === "" ? "M" : "L";
      d += `${cmd}${xAt(i).toFixed(1)},${yAt(v).toFixed(1)} `;
    });
    paths += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="1.8" />`;
    const lastIdx = [...s.values].map((v, i) => (v !== null && v !== undefined ? i : -1)).filter((i) => i >= 0).pop();
    if (lastIdx !== undefined) {
      dots += `<circle cx="${xAt(lastIdx).toFixed(1)}" cy="${yAt(s.values[lastIdx]).toFixed(1)}" r="3" fill="${s.color}" />`;
    }
  });

  // gridlines + axis labels (min/mid/max)
  const gridVals = [min + pad, (min + max) / 2, max - pad];
  let grid = "";
  gridVals.forEach((v) => {
    const y = yAt(v);
    grid += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${width - padR}" y2="${y.toFixed(1)}" stroke="#262d3a" stroke-width="1" stroke-dasharray="2,3" />`;
    grid += `<text x="2" y="${(y + 3).toFixed(1)}" class="chart-axis-label">${round1(v)}</text>`;
  });

  // x labels: first, mid, last
  let xLabels = "";
  const idxs = n > 1 ? [0, Math.floor((n - 1) / 2), n - 1] : [0];
  idxs.forEach((i) => {
    xLabels += `<text x="${xAt(i).toFixed(1)}" y="${height - 3}" text-anchor="middle" class="chart-axis-label">${labels[i]}</text>`;
  });

  return `<svg class="chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">${grid}${paths}${dots}${xLabels}</svg>`;
}

function stackedBarSVG(weeks, seriesKeys, colors, opts = {}) {
  const width = CHART_W;
  const height = opts.height || 180;
  const padL = 30, padR = 8, padT = 10, padB = 20;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;

  const totals = weeks.map((w) => seriesKeys.reduce((sum, k) => sum + (w[k] || 0), 0));
  const max = Math.max(...totals, 1) * 1.1;

  const n = weeks.length;
  const gap = 10;
  const barW = (innerW - gap * (n - 1)) / n;

  let bars = "";
  let xLabels = "";
  weeks.forEach((w, i) => {
    const x = padL + i * (barW + gap);
    let yCursor = padT + innerH;
    seriesKeys.forEach((k) => {
      const val = w[k] || 0;
      const h = (val / max) * innerH;
      yCursor -= h;
      if (h > 0.3) {
        bars += `<rect x="${x.toFixed(1)}" y="${yCursor.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" fill="${colors[k]}" rx="1.5" />`;
      }
    });
    xLabels += `<text x="${(x + barW / 2).toFixed(1)}" y="${height - 4}" text-anchor="middle" class="chart-axis-label">${w.label}</text>`;
    bars += `<text x="${(x + barW / 2).toFixed(1)}" y="${(padT + innerH - (totals[i] / max) * innerH - 4).toFixed(1)}" text-anchor="middle" class="chart-value-label">${round1(totals[i])}h</text>`;
  });

  const gridVal = max;
  let grid = `<line x1="${padL}" y1="${padT}" x2="${width - padR}" y2="${padT}" stroke="#262d3a" stroke-width="1" stroke-dasharray="2,3" />`;

  return `<svg class="chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">${grid}${bars}${xLabels}</svg>`;
}

// ---------------------------------------------------------------------------
// Overview page
// ---------------------------------------------------------------------------

function renderOverviewStats() {
  const latest = state.dailyMetrics[state.dailyMetrics.length - 1];
  const prev = state.dailyMetrics[state.dailyMetrics.length - 2] || latest;

  const thisWeekActs = weeksAgoActivities(0);
  const weekTss = round1(thisWeekActs.reduce((s, a) => s + a.tss, 0));
  const weekHours = round1(thisWeekActs.reduce((s, a) => s + a.duration_min, 0) / 60);

  const cards = [
    { label: "Training Readiness", value: latest.training_readiness, unit: "/100", cls: latest.training_readiness >= 60 ? "stat-good" : latest.training_readiness >= 35 ? "stat-warn" : "stat-bad" },
    { label: "HRV", value: latest.hrv_ms, unit: "ms", cls: latest.hrv_ms >= prev.hrv_ms ? "stat-good" : "" },
    { label: "Resting HR", value: latest.resting_hr, unit: "bpm", cls: latest.resting_hr <= prev.resting_hr ? "stat-good" : "" },
    { label: "Sleep", value: latest.sleep_hours, unit: "hrs", sub: `score ${latest.sleep_score}` },
    { label: "Training Status", value: latest.training_status, cls: statusClass(latest.training_status), isText: true },
    { label: "VO2max Running", value: latest.vo2max_running, unit: "" },
    { label: "VO2max Cycling", value: latest.vo2max_cycling, unit: "" },
    { label: "This Week TSS", value: weekTss, unit: "" },
    { label: "This Week Hours", value: weekHours, unit: "h" },
  ];

  const el = document.getElementById("overview-stats");
  el.innerHTML = cards.map((c) => `
    <div class="stat-card">
      <div class="stat-label">${c.label}</div>
      <div class="stat-value ${c.cls || ""}" style="${c.isText ? "font-size:17px" : ""}">${c.value}<span class="stat-unit">${c.unit || ""}</span></div>
      ${c.sub ? `<div class="stat-sub">${c.sub}</div>` : ""}
    </div>
  `).join("");
}

function weeksAgoActivities(weeksAgo) {
  const today = new Date(state.dailyMetrics[state.dailyMetrics.length - 1].date + "T00:00:00");
  const dow = (today.getDay() + 6) % 7; // Monday=0
  const monday = new Date(today);
  monday.setDate(today.getDate() - dow - weeksAgo * 7);
  const sunday = new Date(monday);
  sunday.setDate(monday.getDate() + 6);
  return state.activities.filter((a) => {
    const d = new Date(a.date + "T00:00:00");
    return d >= monday && d <= sunday;
  });
}

function renderOverviewTrends() {
  const last30 = state.dailyMetrics.slice(-30);
  const labels = last30.map((d) => fmtDate(d.date));

  const metrics = [
    { key: "resting_hr", name: "Resting HR", color: "#f97316" },
    { key: "hrv_ms", name: "HRV (ms)", color: "#22d3ee" },
    { key: "sleep_hours", name: "Sleep (hrs)", color: "#a78bfa" },
    { key: "body_battery", name: "Body Battery", color: "#22c55e" },
    { key: "stress_avg", name: "Stress", color: "#ef4444" },
    { key: "steps", name: "Steps", color: "#3b82f6" },
  ];

  const el = document.getElementById("overview-trends");
  el.innerHTML = metrics.map((m) => {
    const values = last30.map((d) => d[m.key]);
    const chart = lineChartSVG([{ name: m.name, color: m.color, values }], labels, { height: 110 });
    return `<div><div class="panel-title" style="font-size:12px;margin-bottom:4px">${m.name}</div>${chart}</div>`;
  }).join("");
}

function renderOverviewWeeklyVolume() {
  const weeks = buildWeeklyVolumeSeries(8);
  const el = document.getElementById("overview-weekly-volume");
  el.innerHTML = stackedBarSVG(weeks, ["swim", "bike", "run", "strength"], SPORT_COLOR, { height: 170 });
}

function buildWeeklyVolumeSeries(numWeeks) {
  const byWeek = {};
  state.weeklySummary.forEach((row) => {
    if (!byWeek[row.week]) byWeek[row.week] = { week: row.week };
    byWeek[row.week][row.sport] = row.hours;
  });
  const weekKeys = Object.keys(byWeek).sort();
  const recent = weekKeys.slice(-numWeeks);
  return recent.map((wk) => ({ ...byWeek[wk], label: fmtDate(wk) }));
}

function renderOverviewPMC() {
  const last90 = state.pmc.slice(-90);
  const labels = last90.map((d) => fmtDate(d.date));
  const series = [
    { name: "CTL", color: "#22d3ee", values: last90.map((d) => d.ctl) },
    { name: "ATL", color: "#f97316", values: last90.map((d) => d.atl) },
    { name: "TSB", color: "#a78bfa", values: last90.map((d) => d.tsb) },
  ];
  document.getElementById("overview-pmc").innerHTML = lineChartSVG(series, labels, { height: 220 });
}

function renderOverview() {
  renderOverviewStats();
  renderOverviewTrends();
  renderOverviewWeeklyVolume();
  renderOverviewPMC();
}

// ---------------------------------------------------------------------------
// Training Load page
// ---------------------------------------------------------------------------

function renderTrainingLoadPMC(days) {
  const slice = state.pmc.slice(-days);
  const labels = slice.map((d) => fmtDate(d.date));
  const series = [
    { name: "CTL", color: "#22d3ee", values: slice.map((d) => d.ctl) },
    { name: "ATL", color: "#f97316", values: slice.map((d) => d.atl) },
    { name: "TSB", color: "#a78bfa", values: slice.map((d) => d.tsb) },
  ];
  document.getElementById("training-load-pmc").innerHTML = lineChartSVG(series, labels, { height: 280 });
}

function renderBlocks() {
  const panel = document.getElementById("blocks-panel");
  if (!state.blocks || state.blocks.length === 0) {
    panel.style.display = "none";
    return;
  }
  const today = state.dailyMetrics[state.dailyMetrics.length - 1].date;
  const current = state.blocks.find((b) => b.start_date <= today && today <= b.end_date);
  if (!current) {
    panel.style.display = "none";
    return;
  }
  panel.style.display = "block";
  document.getElementById("blocks-content").innerHTML = `
    <div class="stat-value" style="font-size:18px">${current.name}</div>
    <div class="stat-sub">${current.phase} &middot; ${fmtDate(current.start_date)} – ${fmtDate(current.end_date)}</div>
  `;
}

function renderWeeklyTssTable() {
  const byWeek = {};
  state.weeklySummary.forEach((row) => {
    if (!byWeek[row.week]) byWeek[row.week] = { week: row.week, tss: 0, hours: {}, distance: {} };
    byWeek[row.week].tss += row.tss;
    byWeek[row.week].hours[row.sport] = row.hours;
    byWeek[row.week].distance[row.sport] = row.distance_km;
  });
  const weeks = Object.keys(byWeek).sort().reverse();

  const tbody = document.querySelector("#weekly-tss-table tbody");
  tbody.innerHTML = weeks.map((wk) => {
    const w = byWeek[wk];
    const h = w.hours;
    const d = w.distance;
    return `
      <tr>
        <td>${fmtDate(wk)}</td>
        <td>${round1(w.tss)}</td>
        <td>${round1(h.swim || 0)} / ${round1(h.bike || 0)} / ${round1(h.run || 0)}</td>
        <td>${round1(d.swim || 0)} / ${round1(d.bike || 0)} / ${round1(d.run || 0)}</td>
      </tr>
    `;
  }).join("");
}

function renderTrainingLoad() {
  renderTrainingLoadPMC(180);
  renderBlocks();
  renderWeeklyTssTable();
}

// ---------------------------------------------------------------------------
// Activities page
// ---------------------------------------------------------------------------

function renderActivities() {
  const acts = [...state.activities].reverse();
  document.getElementById("activities-count").textContent = `${acts.length} sessions`;

  const tbody = document.querySelector("#activities-table tbody");
  tbody.innerHTML = "";

  acts.forEach((a, i) => {
    const row = document.createElement("tr");
    row.className = "activity-row";
    row.innerHTML = `
      <td>${fmtDate(a.date)}</td>
      <td><span class="sport-tag ${a.sport}"><span class="sport-dot ${a.sport}"></span>${a.sport}</span></td>
      <td>${a.name}</td>
      <td>${a.duration_min} min</td>
      <td>${a.distance_km ? round1(a.distance_km) + " km" : "&mdash;"}</td>
      <td>${round1(a.tss)} / ${a.if}</td>
      <td>${a.avg_hr} bpm</td>
    `;
    const detail = document.createElement("tr");
    detail.className = "activity-detail";
    detail.style.display = "none";
    detail.innerHTML = `
      <td colspan="7">
        <div class="activity-detail-grid">
          <div class="activity-detail-field"><div class="label">Date</div><div class="value">${fmtDateFull(a.date)}</div></div>
          <div class="activity-detail-field"><div class="label">TSS</div><div class="value">${round1(a.tss)}</div></div>
          <div class="activity-detail-field"><div class="label">Intensity Factor</div><div class="value">${a.if}</div></div>
          <div class="activity-detail-field"><div class="label">Training Load</div><div class="value">${a.training_load}</div></div>
          ${a.avg_power ? `<div class="activity-detail-field"><div class="label">Avg Power</div><div class="value">${a.avg_power} W</div></div>` : ""}
          ${a.normalized_power ? `<div class="activity-detail-field"><div class="label">Normalized Power</div><div class="value">${a.normalized_power} W</div></div>` : ""}
          ${a.avg_pace ? `<div class="activity-detail-field"><div class="label">Avg Pace</div><div class="value">${a.avg_pace}</div></div>` : ""}
          <div class="activity-detail-field"><div class="label">Avg HR</div><div class="value">${a.avg_hr} bpm</div></div>
          <div class="activity-detail-field"><div class="label">Kudos</div><div class="value">&#128077; ${a.strava_kudos}</div></div>
          <div class="activity-desc">&ldquo;${a.description}&rdquo;</div>
        </div>
      </td>
    `;
    row.addEventListener("click", () => {
      detail.style.display = detail.style.display === "none" ? "table-row" : "none";
    });
    tbody.appendChild(row);
    tbody.appendChild(detail);
  });
}

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------

const PAGE_TITLES = {
  overview: "Overview",
  "training-load": "Training Load",
  activities: "Activities",
  "coach-chat": "Coach Chat",
};

function setActivePage(page) {
  document.querySelectorAll(".nav-item").forEach((el) => el.classList.toggle("active", el.dataset.page === page));
  document.querySelectorAll(".page").forEach((el) => el.classList.toggle("active", el.id === `page-${page}`));
  document.getElementById("page-title").textContent = PAGE_TITLES[page];
  if (page === "coach-chat") openChat();
}

function initNav() {
  document.querySelectorAll(".nav-item").forEach((el) => {
    el.addEventListener("click", () => setActivePage(el.dataset.page));
  });
}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------

function openChat() {
  document.getElementById("chat-dock").classList.add("open");
}

function closeChat() {
  document.getElementById("chat-dock").classList.remove("open");
}

function appendChatMessage(role, text) {
  const container = document.getElementById("chat-messages");
  const empty = document.getElementById("chat-empty");
  if (empty) empty.remove();
  const bubble = document.createElement("div");
  bubble.className = `chat-msg ${role}`;
  bubble.textContent = text;
  container.appendChild(bubble);
  container.scrollTop = container.scrollHeight;
  return bubble;
}

async function sendChatMessage(text) {
  if (!text.trim()) return;
  appendChatMessage("user", text);
  state.chatHistory.push({ role: "user", content: text });

  const input = document.getElementById("chat-input");
  input.value = "";
  const sendBtn = document.getElementById("chat-send");
  sendBtn.disabled = true;

  const assistantBubble = appendChatMessage("assistant", "");
  const typing = document.createElement("span");
  typing.className = "chat-typing";
  typing.textContent = "coach is typing…";
  assistantBubble.appendChild(typing);

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: state.chatHistory }),
    });
    if (!res.ok || !res.body) throw new Error(`Chat request failed: ${res.status}`);

    assistantBubble.textContent = "";
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let full = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      full += decoder.decode(value, { stream: true });
      assistantBubble.textContent = full;
      assistantBubble.parentElement.scrollTop = assistantBubble.parentElement.scrollHeight;
    }
    state.chatHistory.push({ role: "assistant", content: full });
  } catch (err) {
    assistantBubble.textContent = `Error reaching the coach: ${err.message}`;
  } finally {
    sendBtn.disabled = false;
  }
}

function initChat() {
  document.getElementById("chat-toggle").addEventListener("click", () => {
    const dock = document.getElementById("chat-dock");
    dock.classList.contains("open") ? closeChat() : openChat();
  });
  document.getElementById("chat-close").addEventListener("click", closeChat);

  document.getElementById("chat-send").addEventListener("click", () => {
    sendChatMessage(document.getElementById("chat-input").value);
  });
  document.getElementById("chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChatMessage(document.getElementById("chat-input").value);
    }
  });
  document.getElementById("chat-messages").addEventListener("click", (e) => {
    if (e.target.matches(".chat-suggestion")) {
      sendChatMessage(e.target.dataset.q);
    }
  });
}

// ---------------------------------------------------------------------------
// Sync + init
// ---------------------------------------------------------------------------

async function syncAndRenderAll() {
  await loadAllData();
  renderOverview();
  renderTrainingLoad();
  renderActivities();
}

function initSync() {
  document.getElementById("sync-btn").addEventListener("click", async () => {
    const btn = document.getElementById("sync-btn");
    btn.disabled = true;
    btn.textContent = "Syncing…";
    try {
      await syncAndRenderAll();
    } finally {
      btn.disabled = false;
      btn.innerHTML = "&#8635; Sync";
    }
  });
}

function initPmcRangeControls() {
  document.querySelectorAll("#pmc-range-controls .range-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#pmc-range-controls .range-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      renderTrainingLoadPMC(parseInt(btn.dataset.days, 10));
    });
  });
}

async function init() {
  document.getElementById("page-date").textContent = new Date().toLocaleDateString(undefined, {
    weekday: "long", year: "numeric", month: "long", day: "numeric",
  });
  initNav();
  initChat();
  initSync();
  initPmcRangeControls();
  await syncAndRenderAll();
}

init();
