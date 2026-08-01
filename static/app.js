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
  today: null,
  progression: null,
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
  const [dailyMetrics, activities, pmc, weeklySummary, blocks, today, progression] = await Promise.all([
    fetchJSON("/api/daily-metrics"),
    fetchJSON("/api/activities"),
    fetchJSON("/api/pmc"),
    fetchJSON("/api/weekly-summary"),
    fetchJSON("/api/blocks"),
    fetchJSON("/api/today"),
    fetchJSON("/api/progression"),
  ]);
  state.dailyMetrics = dailyMetrics;
  state.activities = activities;
  state.pmc = pmc;
  state.weeklySummary = weeklySummary;
  state.blocks = blocks;
  state.today = today;
  state.progression = progression;
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

/**
 * Mean-maximal power/pace curve with a LOG x-axis (duration), since 5s and
 * 90min points need to share one chart. curveData: {durations_s, all_time_best,
 * last_42_days, six_months_ago} — any of the three curve objects may be null.
 */
function powerCurveChartSVG(curveData) {
  const width = CHART_W;
  const height = 220;
  const padL = 40, padR = 8, padT = 10, padB = 22;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;

  const durations = curveData.durations_s;
  const seriesDefs = [
    { key: "all_time_best", color: "#5b6472", dash: "" },
    { key: "six_months_ago", color: "#f97316", dash: "4,3" },
    { key: "last_42_days", color: "#22d3ee", dash: "" },
  ];

  const allVals = [];
  seriesDefs.forEach((sd) => {
    const curve = curveData[sd.key];
    if (!curve) return;
    durations.forEach((d) => { const v = curve[d]; if (v !== null && v !== undefined) allVals.push(v); });
  });
  if (!allVals.length) {
    return `<div class="chat-empty" style="margin:0">Not enough long-enough sessions with a power stream yet to plot a curve.</div>`;
  }

  const logMin = Math.log10(durations[0]);
  const logMax = Math.log10(durations[durations.length - 1]);
  const xAt = (d) => padL + ((Math.log10(d) - logMin) / (logMax - logMin)) * innerW;

  let min = Math.min(...allVals), max = Math.max(...allVals);
  const pad = (max - min) * 0.1 || 5;
  min -= pad;
  max += pad;
  const yAt = (v) => padT + innerH - ((v - min) / (max - min)) * innerH;

  let paths = "";
  seriesDefs.forEach((sd) => {
    const curve = curveData[sd.key];
    if (!curve) return;
    let d = "";
    durations.forEach((dur) => {
      const v = curve[dur];
      if (v === null || v === undefined) return;
      const cmd = d === "" ? "M" : "L";
      d += `${cmd}${xAt(dur).toFixed(1)},${yAt(v).toFixed(1)} `;
    });
    if (d) paths += `<path d="${d}" fill="none" stroke="${sd.color}" stroke-width="2" ${sd.dash ? `stroke-dasharray="${sd.dash}"` : ""} />`;
  });

  const gridVals = [min + pad, (min + max) / 2, max - pad];
  let grid = "";
  gridVals.forEach((v) => {
    const y = yAt(v);
    grid += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${width - padR}" y2="${y.toFixed(1)}" stroke="#262d3a" stroke-width="1" stroke-dasharray="2,3" />`;
    grid += `<text x="2" y="${(y + 3).toFixed(1)}" class="chart-axis-label">${Math.round(v)}</text>`;
  });

  const labelFor = (d) => (d < 60 ? `${d}s` : d < 3600 ? `${Math.round(d / 60)}m` : `${round1(d / 3600)}h`);
  let xLabels = "";
  durations.forEach((d) => {
    xLabels += `<text x="${xAt(d).toFixed(1)}" y="${height - 4}" text-anchor="middle" class="chart-axis-label">${labelFor(d)}</text>`;
  });

  return `<svg class="chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">${grid}${paths}${xLabels}</svg>`;
}

// ---------------------------------------------------------------------------
// Today page
// ---------------------------------------------------------------------------

function renderHealthStrip() {
  const latest = state.dailyMetrics[state.dailyMetrics.length - 1];
  if (!latest) return;
  // Deliberately plain: no color-coding, no thresholds, no recommendations.
  // The athlete reads their own body; this is just a reference line.
  document.getElementById("health-strip").innerHTML = `
    <span>Resting HR <b>${latest.resting_hr ?? "&mdash;"}</b> bpm</span>
    <span>HRV <b>${latest.hrv_ms ?? "&mdash;"}</b> ms</span>
    <span>Sleep <b>${latest.sleep_hours ?? "&mdash;"}</b> hrs</span>
    <span>Body Battery <b>${latest.body_battery ?? "&mdash;"}</b></span>
  `;
}

function renderTodayWhereIAm() {
  const t = state.today;
  if (!t) return;

  const block = t.current_block;
  document.getElementById("today-block-title").textContent = block
    ? block.name
    : "Where I Am (no active block configured yet)";

  const days = t.days_to_race;
  document.getElementById("today-race-countdown").textContent =
    `${days >= 0 ? days : Math.abs(days)} days ${days >= 0 ? "to" : "since"} ${t.goal_race.name}`;

  const pmc = t.pmc || {};
  const ramp = t.ramp_rate || {};

  document.getElementById("today-stats").innerHTML = [
    { label: "CTL (Fitness)", value: pmc.ctl ?? "&mdash;" },
    { label: "ATL (Fatigue)", value: pmc.atl ?? "&mdash;" },
    { label: "TSB (Form)", value: pmc.tsb ?? "&mdash;" },
    { label: "Ramp Rate", value: ramp.weekly_ctl_change != null ? `${ramp.weekly_ctl_change}/wk` : "&mdash;" },
  ].map((s) => `
    <div class="stat-card">
      <div class="stat-label">${s.label}</div>
      <div class="stat-value">${s.value}</div>
    </div>
  `).join("");

  document.getElementById("today-ramp-reading").textContent = ramp.reading
    ? `Ramp rate reading: ${ramp.reading}`
    : "";
}

function renderTodayDiscipline() {
  const db = state.today?.discipline_balance;
  const el = document.getElementById("today-discipline");
  if (!db) { el.innerHTML = ""; return; }

  const sports = Object.keys(db.target_split);
  document.getElementById("today-discipline-sub").textContent = `last ${db.last_n_weeks} weeks`;

  el.innerHTML = sports.map((sport) => {
    const actual = (db.actual_split[sport] || 0) * 100;
    const target = db.target_split[sport] * 100;
    const delta = (db.deltas[sport] || 0) * 100;
    const totals = db.totals[sport] || { hours: 0, tss: 0 };
    const isFurthestBelow = db.furthest_below_target === sport;
    const deltaClass = delta < -3 ? "stat-warn" : delta < 0 ? "stat-sub" : "stat-good";
    return `
      <div class="discipline-row">
        <div class="discipline-row-header">
          <span class="sport-tag ${sport}"><span class="sport-dot ${sport}"></span>${sport}${isFurthestBelow ? " &mdash; furthest below target" : ""}</span>
          <span>${round1(totals.hours)}h &middot; ${round1(totals.tss)} TSS</span>
        </div>
        <div class="discipline-bar-track">
          <div class="discipline-bar-fill" style="width:${Math.min(actual, 100)}%;background:${SPORT_COLOR[sport] || "#3b82f6"}"></div>
          <div class="discipline-bar-target" style="left:${Math.min(target, 100)}%"></div>
        </div>
        <div class="discipline-delta ${deltaClass}">${round1(actual)}% actual vs ${round1(target)}% target (${delta >= 0 ? "+" : ""}${round1(delta)}pp)</div>
      </div>
    `;
  }).join("");
}

function renderTodayIntensity() {
  const dist = state.today?.intensity_distribution;
  const el = document.getElementById("today-intensity");
  if (!dist) { el.innerHTML = ""; return; }

  const model = dist.model;
  document.getElementById("today-intensity-model-sub").textContent = `last 4 weeks vs ${model.name} model`;

  const sports = Object.keys(dist.by_sport);
  el.innerHTML = sports.map((sport) => {
    const d = dist.by_sport[sport];
    return `
      <div class="intensity-row">
        <div class="intensity-row-header">${sport}</div>
        <div class="intensity-bar">
          ${d.easy_pct > 0 ? `<div class="intensity-segment easy" style="width:${d.easy_pct}%">${d.easy_pct}%</div>` : ""}
          ${d.moderate_pct > 0 ? `<div class="intensity-segment moderate" style="width:${d.moderate_pct}%">${d.moderate_pct}%</div>` : ""}
          ${d.hard_pct > 0 ? `<div class="intensity-segment hard" style="width:${d.hard_pct}%">${d.hard_pct}%</div>` : ""}
        </div>
      </div>
    `;
  }).join("") + `
    <div class="intensity-model-note">
      Chosen model: ${model.name} (~${model.easy_pct}% easy / ${model.hard_pct}% hard). Bucketed per-session from
      overall IF (a proxy until real per-second zone time is wired in) &mdash; not asserted as the only valid model.
    </div>
  `;
}

function renderOverview() {
  renderHealthStrip();
  renderTodayWhereIAm();
  renderTodayDiscipline();
  renderTodayIntensity();
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
// Progression page
// ---------------------------------------------------------------------------

function renderProgressionPowerCurve() {
  const curve = state.progression?.power_curve;
  document.getElementById("progression-power-curve").innerHTML = curve ? powerCurveChartSVG(curve) : "";
}

function durabilityTrendChart(trend, color) {
  if (!trend || trend.length < 2) {
    return `<div class="chat-empty" style="margin:0">Not enough long sessions yet to show a trend.</div>`;
  }
  const labels = trend.map((t) => fmtDate(t.date));
  const series = [{ name: "Fade %", color, values: trend.map((t) => t.fade_pct) }];
  return lineChartSVG(series, labels, { height: 140, zeroBaseline: true });
}

function renderProgressionDurability() {
  const dt = state.progression?.durability_trend || {};
  document.getElementById("progression-durability-bike").innerHTML = durabilityTrendChart(dt.bike, SPORT_COLOR.bike);
  document.getElementById("progression-durability-run").innerHTML = durabilityTrendChart(dt.run, SPORT_COLOR.run);
}

function efTrendChart(trend, color) {
  if (!trend || trend.length < 2) {
    return `<div class="chat-empty" style="margin:0">Not enough aerobic sessions logged yet to show a trend.</div>`;
  }
  const labels = trend.map((t) => fmtDate(t.date));
  const series = [{ name: "EF", color, values: trend.map((t) => t.ef) }];
  return lineChartSVG(series, labels, { height: 140 });
}

function renderProgressionEF() {
  const ef = state.progression?.ef_trend || {};
  document.getElementById("progression-ef-bike").innerHTML = efTrendChart(ef.bike, SPORT_COLOR.bike);
  document.getElementById("progression-ef-run").innerHTML = efTrendChart(ef.run, SPORT_COLOR.run);
  document.getElementById("progression-ef-swim").innerHTML = efTrendChart(ef.swim, SPORT_COLOR.swim);

  const swimCheck = state.progression?.swim_volume_check;
  document.getElementById("progression-swim-volume-note").textContent = swimCheck ? swimCheck.note : "";
}

function renderProgressionThresholds() {
  const tt = state.progression?.threshold_trend;
  const el = document.getElementById("progression-thresholds");
  const latest = tt?.latest;
  if (!latest) {
    el.innerHTML = `<div class="chat-empty" style="margin:0">No threshold entries synced yet.</div>`;
    return;
  }
  const fields = [
    { key: "ftp", label: "FTP", unit: "W" },
    { key: "threshold_hr", label: "Threshold HR", unit: "bpm" },
    { key: "max_hr", label: "Max HR", unit: "bpm" },
    { key: "resting_hr", label: "Resting HR", unit: "bpm" },
    { key: "threshold_run_pace_per_km", label: "Threshold Run Pace", unit: "/km" },
    { key: "css_per_100m", label: "CSS", unit: "/100m" },
    { key: "weight_kg", label: "Weight", unit: "kg" },
  ].filter((f) => latest[f.key] !== null && latest[f.key] !== undefined);

  el.innerHTML = fields.map((f) => `
    <div class="stat-card">
      <div class="stat-label">${f.label}</div>
      <div class="stat-value">${latest[f.key]}<span class="stat-unit">${f.unit}</span></div>
    </div>
  `).join("") + `
    <div class="stat-sub" style="grid-column:1/-1">
      As of ${fmtDate(latest.date)}${tt.has_trend ? "" : " — only one synced entry so far, not enough to trend yet."}
    </div>
  `;
}

function renderProgressionRacePace() {
  const rp = state.progression?.race_pace_benchmarks;
  document.getElementById("progression-race-pace").textContent = rp
    ? rp.note || "No sessions tagged as race-pace efforts yet."
    : "";
}

function renderProgression() {
  renderProgressionPowerCurve();
  renderProgressionDurability();
  renderProgressionEF();
  renderProgressionThresholds();
  renderProgressionRacePace();
  renderTrainingLoad(); // PMC + blocks + weekly TSS, folded in from the old Training Load page
}

// ---------------------------------------------------------------------------
// Sessions page — list
// ---------------------------------------------------------------------------

function renderActivities() {
  const acts = [...state.activities].reverse();
  document.getElementById("activities-count").textContent = `${acts.length} sessions`;

  const tbody = document.querySelector("#activities-table tbody");
  tbody.innerHTML = "";

  acts.forEach((a) => {
    const row = document.createElement("tr");
    row.className = "activity-row";
    const npOrPace = a.sport === "bike"
      ? (a.normalized_power ? `${a.normalized_power} W` : "&mdash;")
      : (a.avg_pace || "&mdash;");
    row.innerHTML = `
      <td>${fmtDate(a.date)}</td>
      <td><span class="sport-tag ${a.sport}"><span class="sport-dot ${a.sport}"></span>${a.sport}</span></td>
      <td>${a.name}</td>
      <td>${a.duration_min} min</td>
      <td>${a.distance_km ? round1(a.distance_km) + " km" : "&mdash;"}</td>
      <td>${round1(a.tss)} / ${a.if}</td>
      <td>${npOrPace}</td>
      <td>${a.avg_hr ? a.avg_hr + " bpm" : "&mdash;"}</td>
    `;
    row.addEventListener("click", () => openSessionDetail(a.id));
    tbody.appendChild(row);
  });
}

// ---------------------------------------------------------------------------
// Sessions page — detail (drill-down)
// ---------------------------------------------------------------------------

function fmtSpeedAsPace(mps, sport) {
  if (!mps) return "&mdash;";
  if (sport === "swim") {
    const secPer100 = 100 / mps;
    return `${Math.floor(secPer100 / 60)}:${String(Math.round(secPer100 % 60)).padStart(2, "0")}/100m`;
  }
  const secPerKm = 1000 / mps;
  return `${Math.floor(secPerKm / 60)}:${String(Math.round(secPerKm % 60)).padStart(2, "0")}/km`;
}

function verdictLabel(v) {
  return { progress: "Ready to Progress", hold: "Hold", stale: "Stale — Change Stimulus", insufficient_data: "Not Enough Data" }[v] || v;
}

/** Two stacked mini line charts (output, HR) sharing an x-axis of elapsed minutes,
 * with vertical lap-boundary markers. */
function sessionChartSVG(records, laps, sport) {
  const isBike = sport === "bike";
  const outputKey = isBike ? "power" : "enhanced_speed";
  const times = records.map((r) => (r.timestamp || 0) / 60);
  const outputs = records.map((r) => r[outputKey]);
  const hrs = records.map((r) => r.heart_rate);

  const labels = times.map((t) => `${round1(t)}m`);
  const outputChart = lineChartSVG(
    [{ name: isBike ? "Power (W)" : "Speed", color: SPORT_COLOR[sport] || "#3b82f6", values: outputs }],
    labels,
    { height: 100 }
  );
  const hrChart = lineChartSVG(
    [{ name: "HR", color: "#ef4444", values: hrs }],
    labels,
    { height: 90 }
  );

  return `
    <div style="margin-bottom:6px;font-size:11px;color:var(--text-faint)">${isBike ? "Power (W)" : "Speed"}</div>
    ${outputChart}
    <div style="margin:10px 0 6px;font-size:11px;color:var(--text-faint)">Heart Rate (bpm)</div>
    ${hrChart}
  `;
}

async function openSessionDetail(id) {
  document.getElementById("sessions-list-view").style.display = "none";
  document.getElementById("session-detail-view").style.display = "block";

  const [detail, comparable] = await Promise.all([
    fetchJSON(`/api/activities/${id}`),
    fetchJSON(`/api/activities/${id}/comparable`),
  ]);
  const a = detail.activity;
  const computed = detail.computed;

  document.getElementById("session-detail-title").textContent = a.name;
  document.getElementById("session-detail-date").textContent = `${fmtDateFull(a.date)} · ${a.sport}`;

  const efDisplay = computed.ef ? round1(computed.ef * 100) / 100 : "&mdash;";
  const decouplingDisplay = computed.decoupling
    ? `${computed.decoupling.decoupling_pct}% ${computed.decoupling.aerobically_sound ? "(sound)" : "(not settled)"}`
    : "n/a (session too short/steady-only metric)";
  const durabilityDisplay = computed.durability ? `${computed.durability.fade_pct}%` : "&mdash;";

  document.getElementById("session-detail-stats").innerHTML = [
    { label: "TSS", value: round1(a.tss) },
    { label: "IF", value: a.if },
    { label: a.sport === "bike" ? "Normalized Power" : "Avg Pace", value: a.sport === "bike" ? `${a.normalized_power || "&mdash;"} W` : (a.avg_pace || "&mdash;") },
    { label: "Avg HR", value: a.avg_hr ? `${a.avg_hr} bpm` : "&mdash;" },
    { label: "Efficiency Factor", value: efDisplay },
    { label: "Decoupling", value: decouplingDisplay, small: true },
    { label: "Durability (final vs first 1/3)", value: durabilityDisplay },
  ].map((s) => `
    <div class="stat-card">
      <div class="stat-label">${s.label}</div>
      <div class="stat-value" style="${s.small ? "font-size:15px" : ""}">${s.value}</div>
    </div>
  `).join("");

  document.getElementById("session-chart").innerHTML = detail.records.length
    ? sessionChartSVG(detail.records, detail.laps, a.sport)
    : `<div class="chat-empty" style="margin:0">No time-series stream for this session (likely a manual entry with no device file).</div>`;

  const intervalsPanel = document.getElementById("session-intervals-panel");
  const repFadeEl = document.getElementById("session-rep-fade");
  if (detail.laps.length > 1) {
    intervalsPanel.style.display = "block";
    const tbody = document.querySelector("#session-intervals-table tbody");
    tbody.innerHTML = detail.laps.map((lap, i) => {
      const outputVal = a.sport === "bike"
        ? (lap.avg_power ? `${lap.avg_power} W (NP ${lap.normalized_power || "&mdash;"})` : "&mdash;")
        : fmtSpeedAsPace(lap.enhanced_avg_speed, a.sport);
      const durationMin = round1((lap.total_elapsed_time || 0) / 60);
      return `
        <tr>
          <td>${i + 1}</td>
          <td>${durationMin} min</td>
          <td>${outputVal}</td>
          <td>${lap.avg_heart_rate ? lap.avg_heart_rate + " bpm" : "&mdash;"}</td>
        </tr>
      `;
    }).join("");

    if (computed.rep_fade) {
      const rf = computed.rep_fade;
      repFadeEl.innerHTML = rf.faded
        ? `<span class="stat-bad">Faded ${Math.abs(rf.fade_pct)}% from rep 1 to the last rep.</span>`
        : `<span class="stat-good">Held steady across reps (${rf.fade_pct}% change).</span>`;
    } else {
      repFadeEl.innerHTML = "";
    }
  } else {
    intervalsPanel.style.display = "none";
  }

  const verdict = comparable.verdict;
  document.getElementById("session-verdict").innerHTML = `
    <div class="verdict-box verdict-${verdict.verdict}">
      <div class="verdict-label">${verdictLabel(verdict.verdict)}</div>
      <div class="verdict-reason">${verdict.reason}</div>
      <div class="verdict-rule">rule: ${verdict.rule_applied}</div>
    </div>
  `;

  const comparableEl = document.getElementById("session-comparable");
  comparableEl.innerHTML = comparable.comparable_sessions.length
    ? comparable.comparable_sessions.map((c) => `
        <div class="comparable-item" data-id="${c.id}">
          <span>${c.name}</span>
          <span class="date">${fmtDate(c.date)}</span>
        </div>
      `).join("")
    : `<div class="stat-sub">No prior comparable sessions found yet.</div>`;
  comparableEl.querySelectorAll(".comparable-item").forEach((el) => {
    el.addEventListener("click", () => openSessionDetail(parseInt(el.dataset.id, 10)));
  });
}

function backToSessionsList() {
  document.getElementById("session-detail-view").style.display = "none";
  document.getElementById("sessions-list-view").style.display = "block";
}

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------

const PAGE_TITLES = {
  overview: "Today",
  progression: "Progression",
  activities: "Sessions",
  "coach-chat": "Coach Chat",
};

function setActivePage(page) {
  document.querySelectorAll(".nav-item").forEach((el) => el.classList.toggle("active", el.dataset.page === page));
  document.querySelectorAll(".page").forEach((el) => el.classList.toggle("active", el.id === `page-${page}`));
  document.getElementById("page-title").textContent = PAGE_TITLES[page];
  if (page === "coach-chat") openChat();
  if (page === "activities") backToSessionsList();
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
  renderProgression();
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
  document.getElementById("session-back-btn").addEventListener("click", backToSessionsList);
  await syncAndRenderAll();
}

init();
