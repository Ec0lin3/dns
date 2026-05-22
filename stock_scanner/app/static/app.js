"use strict";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let CONFIG = null;
let POLL_TIMER = null;

// ---------------------------------------------------------------------------
// Option lists
// ---------------------------------------------------------------------------
const TF = [["15m", "15 דק'"], ["30m", "30 דק'"], ["60m", "שעה"],
            ["1d", "יומי"], ["1wk", "שבועי"], ["1mo", "חודשי"]];
const MODES = [["off", "כבוי"], ["mandatory", "חובה"], ["bonus", "בונוס"]];
const MATCH = [["all", "כל הבדיקות (AND)"], ["any", "לפחות אחת (OR)"]];
const MA_TYPE = [["EMA", "EMA"], ["SMA", "SMA"]];
const MA_CHECKS = [["price_vs_ma", "מחיר מול ממוצע"],
                   ["ma_cross", "חציית ממוצעים"],
                   ["price_near_ma", "מחיר קרוב לממוצע"]];
const ABOVE_BELOW = [["above", "מעל"], ["below", "מתחת"]];
const CROSS_DIR = [["golden", "Golden Cross"], ["death", "Death Cross"]];
const FVG_DIR = [["bullish", "עולה (Bullish)"], ["bearish", "יורד (Bearish)"]];
const FVG_COND = [["exists", "FVG פתוח קיים"], ["price_inside", "מחיר בתוך ה-FVG"]];
const LIQ_COND = [["untapped_above", "נזילות לא-נגועה מעל"],
                  ["untapped_below", "נזילות לא-נגועה מתחת"],
                  ["swept_above", "גריפת נזילות מעל"],
                  ["swept_below", "גריפת נזילות מתחת"]];
const ZONE = [["discount", "Discount (חצי תחתון)"],
              ["premium", "Premium (חצי עליון)"],
              ["equilibrium", "Equilibrium (סביב 50%)"]];
const GAP_DIR = [["up", "פער כלפי מעלה"], ["down", "פער כלפי מטה"]];
const GAP_COND = [["unfilled", "לא מולא"], ["filled", "מולא"],
                  ["price_inside", "מחיר בתוך הגאפ (כניסה)"], ["any", "כל פער"]];
const CRIT_ORDER = ["moving_average", "fvg", "liquidity", "range_equilibrium",
                    "gaps", "support_resistance"];
const CRIT_LABELS = {
  moving_average: "📈 ממוצעים נעים",
  fvg: "🟦 FVG — Fair Value Gap",
  liquidity: "💧 נזילות (Liquidity)",
  range_equilibrium: "📊 Range / Equilibrium",
  gaps: "↕ פערים (Gaps)",
  support_resistance: "📏 תמיכה / התנגדות",
};
const SR_ANCHOR = [["lows", "2 נמוכים (תמיכה עולה)"],
                   ["highs", "2 גבוהים (התנגדות יורדת)"]];
const CHART_TF = [["1d", "יומי"], ["1wk", "שבועי"], ["1mo", "חודשי"], ["60m", "שעה"]];

// ---------------------------------------------------------------------------
// Tiny DOM helper
// ---------------------------------------------------------------------------
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "checked" || k === "selected") { if (v) el.setAttribute(k, k); }
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.appendChild(typeof kid === "object" ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

function sel(value, options, onChange) {
  const s = h("select");
  for (const [v, label] of options) {
    const o = h("option", { value: v }, label);
    if (String(v) === String(value)) o.selected = true;
    s.appendChild(o);
  }
  s.addEventListener("change", () => onChange(s.value));
  return s;
}

function numInput(value, onChange, step) {
  const i = h("input", { type: "number", value: value, step: step || "1" });
  i.addEventListener("change", () => onChange(parseFloat(i.value)));
  return i;
}

function textInput(value, onChange, placeholder) {
  const i = h("input", { type: "text", value: value || "", placeholder: placeholder || "" });
  i.addEventListener("change", () => onChange(i.value));
  return i;
}

function checkbox(checked, onChange) {
  const i = h("input", { type: "checkbox" });
  i.checked = !!checked;
  i.addEventListener("change", () => onChange(i.checked));
  return i;
}

function field(labelText, ...controls) {
  return h("div", { class: "field" }, h("label", {}, labelText), ...controls);
}

// ---------------------------------------------------------------------------
// Load / save
// ---------------------------------------------------------------------------
async function loadConfig() {
  CONFIG = await fetch("/api/config").then((r) => r.json());
  renderAll();
}

async function saveConfig() {
  setStatus("שומר הגדרות…", "busy");
  try {
    const r = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(CONFIG),
    });
    if (!r.ok) throw new Error("save failed");
    setStatus("✓ ההגדרות נשמרו", "ok");
  } catch (e) {
    setStatus("✗ שמירת ההגדרות נכשלה", "err");
  }
}

// ---------------------------------------------------------------------------
// Render: universe
// ---------------------------------------------------------------------------
function renderUniverse() {
  const box = document.getElementById("universe-section");
  box.innerHTML = "";
  const u = CONFIG.universe;
  box.appendChild(field("רשימת מניות",
    sel(u.type, [["sp500", "S&P 500"], ["nasdaq100", "Nasdaq 100"],
                 ["both", "S&P 500 + Nasdaq 100"], ["custom", "רשימה מותאמת אישית"]],
        (v) => { u.type = v; renderUniverse(); })));
  if (u.type === "custom") {
    const ta = h("textarea", { placeholder: "AAPL, MSFT, NVDA …" },
      (u.custom_tickers || []).join(", "));
    ta.addEventListener("change", () => {
      u.custom_tickers = ta.value.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean);
    });
    box.appendChild(h("div", { class: "field" },
      h("label", {}, "סמלים"), ta));
  } else {
    box.appendChild(h("p", { class: "muted" },
      "הרשימה נמשכת אוטומטית מויקיפדיה ומתעדכנת אחת לשבוע."));
  }
}

// ---------------------------------------------------------------------------
// Render: scoring
// ---------------------------------------------------------------------------
function renderScoring() {
  const box = document.getElementById("scoring-section");
  box.innerHTML = "";
  box.appendChild(field("ניקוד בונוס מינימלי",
    numInput(CONFIG.min_score, (v) => { CONFIG.min_score = v; })));
  box.appendChild(h("p", { class: "muted" },
    "מניה עוברת כאשר כל קריטריוני ה'חובה' מתקיימים וגם סכום נקודות הבונוס ≥ הערך הזה."));
}

// ---------------------------------------------------------------------------
// Render: criteria
// ---------------------------------------------------------------------------
function critCard(name) {
  const cfg = CONFIG.criteria[name];
  const head = h("div", { class: "crit-head" },
    h("span", { class: "title" }, CRIT_LABELS[name] || name),
    h("label", {}, "מצב"),
    sel(cfg.mode, MODES, (v) => { cfg.mode = v; renderCriteria(); }),
    h("label", {}, "משקל"),
    numInput(cfg.weight, (v) => { cfg.weight = v; }));
  const body = h("div", { class: "crit-body" + (cfg.mode === "off" ? " off" : "") });
  if (name === "moving_average") buildMA(cfg, body);
  else if (name === "fvg") buildFVG(cfg, body);
  else if (name === "liquidity") buildLiquidity(cfg, body);
  else if (name === "range_equilibrium") buildRange(cfg, body);
  else if (name === "gaps") buildGaps(cfg, body);
  else if (name === "support_resistance") buildSupportResistance(cfg, body);
  return h("div", { class: "crit" }, head, body);
}

function renderCriteria() {
  const box = document.getElementById("criteria-section");
  box.innerHTML = "";
  for (const name of CRIT_ORDER) box.appendChild(critCard(name));
}

function matchField(cfg) {
  return field("שילוב בדיקות",
    sel(cfg.match || "all", MATCH, (v) => { cfg.match = v; }));
}

function removeBtn(list, idx) {
  return h("button", { class: "small danger",
    onclick: () => { list.splice(idx, 1); renderCriteria(); } }, "✕ הסר");
}

// --- moving average ---
function maDefaults(type) {
  if (type === "ma_cross")
    return { check: "ma_cross", ma_type: "EMA", fast_period: 20, slow_period: 50,
             timeframe: "1d", direction: "golden", within_bars: 10 };
  if (type === "price_near_ma")
    return { check: "price_near_ma", ma_type: "EMA", period: 50,
             timeframe: "1d", tolerance_pct: 1.5 };
  return { check: "price_vs_ma", ma_type: "EMA", period: 200,
           timeframe: "1d", condition: "above" };
}

function buildMA(cfg, body) {
  body.appendChild(matchField(cfg));
  (cfg.checks || []).forEach((chk, idx) => {
    const row = h("div", { class: "check-row" });
    row.appendChild(sel(chk.check, MA_CHECKS, (v) => {
      cfg.checks[idx] = maDefaults(v); renderCriteria();
    }));
    row.appendChild(sel(chk.timeframe, TF, (v) => { chk.timeframe = v; }));
    row.appendChild(sel(chk.ma_type, MA_TYPE, (v) => { chk.ma_type = v; }));
    if (chk.check === "ma_cross") {
      row.appendChild(h("label", {}, "מהיר"));
      row.appendChild(numInput(chk.fast_period, (v) => { chk.fast_period = v; }));
      row.appendChild(h("label", {}, "איטי"));
      row.appendChild(numInput(chk.slow_period, (v) => { chk.slow_period = v; }));
      row.appendChild(sel(chk.direction, CROSS_DIR, (v) => { chk.direction = v; }));
      row.appendChild(h("label", {}, "תוך X נרות"));
      row.appendChild(numInput(chk.within_bars, (v) => { chk.within_bars = v; }));
    } else if (chk.check === "price_near_ma") {
      row.appendChild(h("label", {}, "תקופה"));
      row.appendChild(numInput(chk.period, (v) => { chk.period = v; }));
      row.appendChild(h("label", {}, "סבולת %"));
      row.appendChild(numInput(chk.tolerance_pct, (v) => { chk.tolerance_pct = v; }, "0.1"));
    } else {
      row.appendChild(h("label", {}, "תקופה"));
      row.appendChild(numInput(chk.period, (v) => { chk.period = v; }));
      row.appendChild(sel(chk.condition, ABOVE_BELOW, (v) => { chk.condition = v; }));
    }
    row.appendChild(removeBtn(cfg.checks, idx));
    body.appendChild(row);
  });
  body.appendChild(h("button", { class: "small",
    onclick: () => { cfg.checks.push(maDefaults("price_vs_ma")); renderCriteria(); } },
    "+ הוסף בדיקת ממוצע"));
}

// --- fvg ---
function buildFVG(cfg, body) {
  body.appendChild(matchField(cfg));
  (cfg.checks || []).forEach((chk, idx) => {
    const row = h("div", { class: "check-row" },
      h("label", {}, "טיימפריים"),
      sel(chk.timeframe, TF, (v) => { chk.timeframe = v; }),
      sel(chk.direction, FVG_DIR, (v) => { chk.direction = v; }),
      sel(chk.condition, FVG_COND, (v) => { chk.condition = v; }),
      h("label", {}, "נרות אחורה"),
      numInput(chk.lookback, (v) => { chk.lookback = v; }),
      removeBtn(cfg.checks, idx));
    body.appendChild(row);
  });
  body.appendChild(h("button", { class: "small",
    onclick: () => {
      cfg.checks.push({ timeframe: "1d", direction: "bullish",
        condition: "exists", lookback: 60 });
      renderCriteria();
    } }, "+ הוסף בדיקת FVG"));
}

// --- liquidity ---
function buildLiquidity(cfg, body) {
  body.appendChild(matchField(cfg));
  (cfg.checks || []).forEach((chk, idx) => {
    const row = h("div", { class: "check-row" },
      h("label", {}, "טיימפריים"),
      sel(chk.timeframe, TF, (v) => { chk.timeframe = v; }),
      sel(chk.condition, LIQ_COND, (v) => { chk.condition = v; }),
      h("label", {}, "עוצמת סווינג"),
      numInput(chk.strength, (v) => { chk.strength = v; }),
      h("label", {}, "נרות אחורה"),
      numInput(chk.lookback, (v) => { chk.lookback = v; }),
      h("label", {}, "טריות גריפה"),
      numInput(chk.recency, (v) => { chk.recency = v; }),
      removeBtn(cfg.checks, idx));
    body.appendChild(row);
  });
  body.appendChild(h("p", { class: "muted" },
    "עוצמת סווינג גבוהה ⇐ נזילות חיצונית · עוצמה נמוכה ⇐ נזילות פנימית."));
  body.appendChild(h("button", { class: "small",
    onclick: () => {
      cfg.checks.push({ timeframe: "1d", strength: 5, lookback: 120,
        condition: "untapped_above", recency: 5 });
      renderCriteria();
    } }, "+ הוסף בדיקת נזילות"));
}

// --- range ---
function buildRange(cfg, body) {
  body.appendChild(field("טיימפריים", sel(cfg.timeframe, TF, (v) => { cfg.timeframe = v; })));
  body.appendChild(field("עוצמת סווינג (משמעותיות)",
    numInput(cfg.swing_strength, (v) => { cfg.swing_strength = v; })));
  body.appendChild(field("מקסימום נרות אחורה לחיפוש",
    numInput(cfg.max_lookback, (v) => { cfg.max_lookback = v; })));
  body.appendChild(field("אזור מבוקש", sel(cfg.zone, ZONE, (v) => { cfg.zone = v; })));
  body.appendChild(field("רוחב אזור Equilibrium (%)",
    numInput(cfg.eq_band_pct, (v) => { cfg.eq_band_pct = v; })));
  body.appendChild(h("p", { class: "muted" },
    "ה-Range ננעל אוטומטית על סווינג-נמוך וסווינג-גבוה משמעותיים אחרונים — " +
    "לא חלון קבוע. עוצמת סווינג גבוהה = רק קיצונים גדולים, כמו סימון ידני."));
}

// --- gaps ---
function buildGaps(cfg, body) {
  body.appendChild(field("טיימפריים", sel(cfg.timeframe, TF, (v) => { cfg.timeframe = v; })));
  body.appendChild(field("כיוון הפער", sel(cfg.direction, GAP_DIR, (v) => { cfg.direction = v; })));
  body.appendChild(field("גודל פער מינימלי (%)",
    numInput(cfg.min_gap_pct, (v) => { cfg.min_gap_pct = v; }, "0.1")));
  body.appendChild(field("מצב הפער", sel(cfg.condition, GAP_COND, (v) => { cfg.condition = v; })));
  body.appendChild(field("נרות אחורה", numInput(cfg.lookback, (v) => { cfg.lookback = v; })));
}

// --- support / resistance ---
function buildSupportResistance(cfg, body) {
  if (!Array.isArray(cfg.line_types)) cfg.line_types = ["horizontal"];
  body.appendChild(field("טיימפריים", sel(cfg.timeframe, TF, (v) => { cfg.timeframe = v; })));
  body.appendChild(field("נרות אחורה", numInput(cfg.lookback, (v) => { cfg.lookback = v; })));
  const toggle = (type, on) => {
    const set = new Set(cfg.line_types);
    if (on) set.add(type); else set.delete(type);
    cfg.line_types = Array.from(set);
  };
  body.appendChild(h("div", { class: "field" },
    h("label", {}, "סוגי קווים"),
    checkbox(cfg.line_types.includes("horizontal"), (v) => toggle("horizontal", v)),
    h("span", { class: "muted" }, "אופקי"),
    checkbox(cfg.line_types.includes("trendline"), (v) => toggle("trendline", v)),
    h("span", { class: "muted" }, "קו מגמה אלכסוני")));
  body.appendChild(field("עוצמת סווינג",
    numInput(cfg.swing_strength, (v) => { cfg.swing_strength = v; })));
  body.appendChild(field("סבולת נגיעה (%)",
    numInput(cfg.tolerance_pct, (v) => { cfg.tolerance_pct = v; }, "0.1")));
  body.appendChild(field("עוגן קו המגמה",
    sel(cfg.trendline_anchor, SR_ANCHOR, (v) => { cfg.trendline_anchor = v; })));
  body.appendChild(h("p", { class: "muted" },
    "קו אופקי במחיר נקודת קיצון, וקו מגמה אלכסוני מהשפל/שיא המשמעותי שנמתח קדימה. " +
    "אם הנר הנוכחי נוגע בקו (בטווח הסבולת) — הקריטריון עובר."));
}

// ---------------------------------------------------------------------------
// Render: telegram
// ---------------------------------------------------------------------------
function renderTelegram() {
  const box = document.getElementById("telegram-section");
  box.innerHTML = "";
  const tg = CONFIG.telegram;
  const cb = h("input", { type: "checkbox" });
  cb.checked = !!tg.enabled;
  cb.addEventListener("change", () => { tg.enabled = cb.checked; });
  box.appendChild(h("div", { class: "field" }, h("label", {}, "שלח התראות"), cb));
  box.appendChild(field("Bot Token",
    textInput(tg.bot_token, (v) => { tg.bot_token = v; }, "123456:ABC…")));
  box.appendChild(field("Chat ID",
    textInput(tg.chat_id, (v) => { tg.chat_id = v; }, "123456789")));
  const result = h("div", { class: "tg-result muted" });
  const testBtn = h("button", { class: "small", onclick: async () => {
    result.textContent = "שולח…";
    await saveConfig();
    const r = await fetch("/api/telegram/test", { method: "POST" }).then((x) => x.json());
    result.textContent = r.ok ? "✓ נשלחה הודעת בדיקה" : "✗ " + r.message;
    result.className = "tg-result " + (r.ok ? "" : "");
  } }, "שלח הודעת בדיקה");
  box.appendChild(testBtn);
  box.appendChild(result);
}

// ---------------------------------------------------------------------------
// Render: schedule
// ---------------------------------------------------------------------------
function renderSchedule() {
  const box = document.getElementById("schedule-section");
  box.innerHTML = "";
  const s = CONFIG.schedule;
  const cb = h("input", { type: "checkbox" });
  cb.checked = !!s.enabled;
  cb.addEventListener("change", () => { s.enabled = cb.checked; });
  box.appendChild(h("div", { class: "field" }, h("label", {}, "סריקה אוטומטית"), cb));
  box.appendChild(field("שעה (HH:MM)", textInput(s.time, (v) => { s.time = v; }, "16:30")));
  box.appendChild(field("אזור זמן", textInput(s.timezone, (v) => { s.timezone = v; },
    "America/New_York")));
  box.appendChild(h("p", { class: "muted" },
    "הסריקה תרוץ בימי ב׳–ו׳ בשעה שנבחרה (לפי אזור הזמן)."));
}

// ---------------------------------------------------------------------------
// Render everything
// ---------------------------------------------------------------------------
function renderAll() {
  renderUniverse();
  renderScoring();
  renderCriteria();
  renderTelegram();
  renderSchedule();
}

// ---------------------------------------------------------------------------
// Status bar
// ---------------------------------------------------------------------------
function setStatus(text, kind) {
  const bar = document.getElementById("status-bar");
  bar.textContent = text;
  bar.className = "status-bar" + (kind ? " " + kind : "");
}

// ---------------------------------------------------------------------------
// Scan + polling
// ---------------------------------------------------------------------------
async function startScan() {
  await saveConfig();
  await fetch("/api/scan", { method: "POST" });
  pollStatus();
}

function phaseText(s) {
  if (s.phase === "downloading")
    return `מוריד נתונים… (${s.progress}/${s.total} טיימפריימים)`;
  if (s.phase === "evaluating")
    return `מנתח מניות… (${s.progress}/${s.total})`;
  if (s.phase === "starting") return "מתחיל…";
  return s.phase;
}

async function pollStatus() {
  const s = await fetch("/api/scan/status").then((r) => r.json());
  if (s.running) {
    setStatus("⏳ " + phaseText(s), "busy");
    document.getElementById("btn-scan").disabled = true;
    clearTimeout(POLL_TIMER);
    POLL_TIMER = setTimeout(pollStatus, 1500);
    return;
  }
  document.getElementById("btn-scan").disabled = false;
  if (s.error) {
    setStatus("✗ שגיאת סריקה: " + s.error, "err");
  } else if (s.last_run) {
    setStatus(`✓ סריקה אחרונה: ${s.last_run} · ${s.results.length} תוצאות · ` +
      `${s.universe_size} מניות נסרקו · ${s.errors_count} שגיאות`, "ok");
  } else {
    setStatus("מוכן. לחץ 'סרוק עכשיו' כדי להתחיל.", "");
  }
  renderResults(s);
}

// ---------------------------------------------------------------------------
// Render: results
// ---------------------------------------------------------------------------
function renderResults(s) {
  const box = document.getElementById("results-section");
  const countBadge = document.getElementById("results-count");
  const results = s.results || [];
  countBadge.textContent = results.length ? results.length + " מניות" : "";
  if (!results.length) {
    box.innerHTML = s.last_run
      ? "לא נמצאו מניות שעוברות את הסינון."
      : "עדיין לא בוצעה סריקה.";
    return;
  }
  const table = h("table");
  table.appendChild(h("tr", {},
    h("th", {}, "מניה"), h("th", {}, "מחיר"),
    h("th", {}, "ניקוד"), h("th", {}, "פירוט קריטריונים")));
  for (const r of results) {
    const chips = h("td");
    for (const b of r.breakdown) {
      const cls = "chip " + (b.passed ? "pass" : "fail") +
        (b.mode === "mandatory" ? " mand" : "");
      chips.appendChild(h("span", { class: cls, title: b.detail },
        `${b.label}: ${b.detail}`));
    }
    table.appendChild(h("tr", { class: "clickable", onclick: () => openChart(r.ticker) },
      h("td", {}, h("b", {}, r.ticker)),
      h("td", {}, r.price !== null ? "$" + r.price.toFixed(2) : "-"),
      h("td", {}, h("span", { class: "score-pill" }, `${r.score}/${r.max_bonus}`)),
      chips));
  }
  box.innerHTML = "";
  box.appendChild(table);
}

// ---------------------------------------------------------------------------
// Chart modal (TradingView Lightweight Charts)
// ---------------------------------------------------------------------------
let CURRENT_CHART = null;
let CURRENT_CHART_EL = null;

// Custom primitive that paints FVG zones as filled rectangles.
function boxPrimitive(boxes) {
  let chart = null, series = null;
  return {
    attached(p) { chart = p.chart; series = p.series; },
    detached() { chart = null; series = null; },
    updateAllViews() {},
    paneViews() {
      return [{
        renderer() {
          return {
            draw(target) {
              if (!chart || !series) return;
              target.useBitmapCoordinateSpace((scope) => {
                const ctx = scope.context;
                const ts = chart.timeScale();
                for (const b of boxes) {
                  const x1 = ts.timeToCoordinate(b.time1);
                  const x2 = ts.timeToCoordinate(b.time2);
                  const y1 = series.priceToCoordinate(b.price1);
                  const y2 = series.priceToCoordinate(b.price2);
                  if (x1 == null || x2 == null || y1 == null || y2 == null) continue;
                  const hr = scope.horizontalPixelRatio, vr = scope.verticalPixelRatio;
                  const left = Math.min(x1, x2) * hr, right = Math.max(x1, x2) * hr;
                  const top = Math.min(y1, y2) * vr, bot = Math.max(y1, y2) * vr;
                  ctx.fillStyle = b.color;
                  ctx.fillRect(left, top, right - left, bot - top);
                }
              });
            },
          };
        },
      }];
    },
  };
}

// Draws a trendline through two anchors and projects it to the right edge.
function trendlinePrimitive(lines) {
  let chart = null, series = null;
  return {
    attached(p) { chart = p.chart; series = p.series; },
    detached() { chart = null; series = null; },
    updateAllViews() {},
    paneViews() {
      return [{
        renderer() {
          return {
            draw(target) {
              if (!chart || !series) return;
              target.useBitmapCoordinateSpace((scope) => {
                const ctx = scope.context;
                const ts = chart.timeScale();
                const hr = scope.horizontalPixelRatio, vr = scope.verticalPixelRatio;
                const rightCss = scope.mediaSize.width;
                for (const ln of lines) {
                  const x1 = ts.timeToCoordinate(ln.p1.time);
                  const x2 = ts.timeToCoordinate(ln.p2.time);
                  const y1 = series.priceToCoordinate(ln.p1.price);
                  const y2 = series.priceToCoordinate(ln.p2.price);
                  if (x1 == null || x2 == null || y1 == null || y2 == null) continue;
                  if (x2 === x1) continue;
                  const slope = (y2 - y1) / (x2 - x1);
                  const yEnd = y1 + slope * (rightCss - x1);
                  ctx.beginPath();
                  ctx.strokeStyle = ln.color;
                  ctx.lineWidth = 2 * hr;
                  ctx.moveTo(x1 * hr, y1 * vr);
                  ctx.lineTo(rightCss * hr, yEnd * vr);
                  ctx.stroke();
                }
              });
            },
          };
        },
      }];
    },
  };
}

async function openChart(ticker) {
  const modal = document.getElementById("chart-modal");
  modal.classList.remove("hidden");
  modal.dataset.ticker = ticker;
  document.getElementById("chart-title").textContent = "גרף — " + ticker;
  document.getElementById("chart-tf").value =
    (CONFIG && CONFIG.chart && CONFIG.chart.timeframe) || "1d";
  await loadChart();
}

function closeChart() {
  document.getElementById("chart-modal").classList.add("hidden");
  if (CURRENT_CHART) { CURRENT_CHART.remove(); CURRENT_CHART = null; }
}

async function loadChart() {
  const modal = document.getElementById("chart-modal");
  const ticker = modal.dataset.ticker;
  const tf = document.getElementById("chart-tf").value;
  const status = document.getElementById("chart-status");
  document.getElementById("chart-legend").innerHTML = "";
  status.textContent = "טוען נתונים…";
  try {
    const data = await fetch(`/api/chart/${ticker}?timeframe=${tf}`)
      .then((r) => r.json());
    if (data.error || !data.candles || !data.candles.length) {
      status.textContent = "✗ " + (data.error || "אין נתונים להצגה");
      if (CURRENT_CHART) { CURRENT_CHART.remove(); CURRENT_CHART = null; }
      return;
    }
    status.textContent = "";
    renderChart(data, "chart-container", "chart-legend");
  } catch (e) {
    status.textContent = "✗ שגיאה בטעינת הגרף";
  }
}

function renderChart(data, containerId, legendId) {
  const container = document.getElementById(containerId);
  if (CURRENT_CHART) { CURRENT_CHART.remove(); CURRENT_CHART = null; }
  container.innerHTML = "";
  const LWC = window.LightweightCharts;
  const chart = LWC.createChart(container, {
    width: container.clientWidth,
    height: 440,
    layout: { background: { color: "#0f1419" }, textColor: "#e6e9ee" },
    grid: { vertLines: { color: "#1e2730" }, horzLines: { color: "#1e2730" } },
    timeScale: { borderColor: "#2f3a48", timeVisible: true },
    rightPriceScale: { borderColor: "#2f3a48" },
  });
  CURRENT_CHART = chart;
  CURRENT_CHART_EL = container;

  const candles = chart.addCandlestickSeries({
    upColor: "#22c55e", downColor: "#ef4444", borderVisible: false,
    wickUpColor: "#22c55e", wickDownColor: "#ef4444",
  });
  candles.setData(data.candles);

  for (const ln of data.lines) {
    const s = chart.addLineSeries({
      color: ln.color, lineWidth: 2,
      lineStyle: ln.dashed ? LWC.LineStyle.Dashed : LWC.LineStyle.Solid,
      priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    s.setData(ln.points);
  }

  for (const pl of data.priceLines) {
    candles.createPriceLine({
      price: pl.price, color: pl.color, lineWidth: 1,
      lineStyle: LWC.LineStyle.Dashed, axisLabelVisible: true, title: pl.label,
    });
  }

  if (data.boxes && data.boxes.length) {
    candles.attachPrimitive(boxPrimitive(data.boxes));
  }

  if (data.trendlines && data.trendlines.length) {
    candles.attachPrimitive(trendlinePrimitive(data.trendlines));
  }

  if (data.markers && data.markers.length) {
    candles.setMarkers(data.markers);
  }

  chart.timeScale().fitContent();
  renderLegend(data.legend, legendId);
}

function renderLegend(legend, legendId) {
  const box = document.getElementById(legendId);
  box.innerHTML = "";
  if (!legend || !legend.length) {
    box.appendChild(h("span", { class: "muted" },
      "אין סימונים — אף קריטריון לא מופעל."));
    return;
  }
  for (const item of legend) {
    box.appendChild(h("div", { class: "legend-item" },
      h("span", { class: "legend-swatch", style: "background:" + item.color }),
      h("b", {}, item.label),
      h("span", { class: "legend-desc" }, "— " + item.desc)));
  }
}

// ---------------------------------------------------------------------------
// Test page (single-stock analysis)
// ---------------------------------------------------------------------------
function renderTestFunctions() {
  const box = document.getElementById("test-functions");
  box.innerHTML = "";
  for (const name of CRIT_ORDER) {
    const cb = h("input", { type: "checkbox" });
    cb.checked = true;
    cb.dataset.crit = name;
    box.appendChild(h("label", { class: "func-toggle" }, cb,
      h("span", {}, CRIT_LABELS[name])));
  }
}

async function runTest() {
  const ticker = document.getElementById("test-ticker").value.trim().toUpperCase();
  const status = document.getElementById("test-status");
  if (!ticker) { status.textContent = "✗ הזן סימול מניה"; return; }
  const timeframe = document.getElementById("test-tf").value;
  const criteria = [...document.querySelectorAll("#test-functions input:checked")]
    .map((c) => c.dataset.crit);
  status.textContent = "⏳ בודק את " + ticker + "…";
  document.getElementById("test-results").innerHTML = "";
  document.getElementById("test-chart-legend").innerHTML = "";
  try {
    const data = await fetch("/api/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker, timeframe, criteria }),
    }).then((r) => r.json());
    if (data.error) { status.textContent = "✗ " + data.error; return; }
    status.textContent = "";
    renderTestResults(data);
  } catch (e) {
    status.textContent = "✗ שגיאה בבדיקה";
  }
}

function renderTestResults(data) {
  const box = document.getElementById("test-results");
  box.innerHTML = "";
  const breakdown = (data.evaluation && data.evaluation.breakdown) || [];
  if (!breakdown.length) {
    box.appendChild(h("p", { class: "muted" }, "לא נבחרו פונקציות לבדיקה."));
  } else {
    const table = h("table");
    table.appendChild(h("tr", {}, h("th", {}, "פונקציה"),
      h("th", {}, "זוהה?"), h("th", {}, "פירוט")));
    for (const b of breakdown) {
      table.appendChild(h("tr", {},
        h("td", {}, b.label),
        h("td", {}, h("span", { class: "chip " + (b.passed ? "pass" : "fail") },
          b.passed ? "✓ כן" : "✗ לא")),
        h("td", {}, b.detail)));
    }
    box.appendChild(table);
  }
  const chart = data.chart;
  if (chart && !chart.error && chart.candles && chart.candles.length) {
    renderChart(chart, "test-chart-container", "test-chart-legend");
  } else {
    if (CURRENT_CHART) { CURRENT_CHART.remove(); CURRENT_CHART = null; }
    document.getElementById("test-chart-container").innerHTML = "";
    box.appendChild(h("p", { class: "muted" },
      "אין נתוני גרף: " + ((chart && chart.error) || "—")));
  }
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------
function switchView(view) {
  const isTest = view === "test";
  document.getElementById("view-dashboard").classList.toggle("hidden", isTest);
  document.getElementById("view-test").classList.toggle("hidden", !isTest);
  document.getElementById("dashboard-actions").classList.toggle("hidden", isTest);
  document.getElementById("tab-dashboard").classList.toggle("active", !isTest);
  document.getElementById("tab-test").classList.toggle("active", isTest);
  if (CURRENT_CHART) { CURRENT_CHART.remove(); CURRENT_CHART = null; }
}

// ---------------------------------------------------------------------------
// Wire up
// ---------------------------------------------------------------------------
document.getElementById("btn-scan").addEventListener("click", startScan);
document.getElementById("btn-save").addEventListener("click", saveConfig);
document.getElementById("btn-cache").addEventListener("click", async () => {
  await fetch("/api/cache/clear", { method: "POST" });
  setStatus("✓ המטמון נוקה — הסריקה הבאה תמשוך נתונים טריים", "ok");
});
document.getElementById("chart-close").addEventListener("click", closeChart);
document.getElementById("chart-tf").addEventListener("change", loadChart);
document.getElementById("chart-modal").addEventListener("click", (e) => {
  if (e.target.id === "chart-modal") closeChart();
});
document.getElementById("tab-dashboard").addEventListener("click",
  () => switchView("dashboard"));
document.getElementById("tab-test").addEventListener("click",
  () => switchView("test"));
document.getElementById("test-run").addEventListener("click", runTest);
window.addEventListener("resize", () => {
  if (CURRENT_CHART && CURRENT_CHART_EL) {
    CURRENT_CHART.applyOptions({ width: CURRENT_CHART_EL.clientWidth });
  }
});

renderTestFunctions();
loadConfig().then(pollStatus);
