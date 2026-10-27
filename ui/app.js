/* Helm dashboard.
   No framework, no build step — the whole UI is this file plus one stylesheet, served by
   the same process that trades. One less thing that can be broken or stale at 3am.

   State arrives two ways: a WebSocket pushes `state` snapshots and `narrate` lines as they
   happen, and a slow poll backfills the tabs that don't need to be realtime. */

const $ = (id) => document.getElementById(id);
const money = (v, d = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—"
    : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const signed = (v, d = 2) => (v > 0 ? "+" : "") + money(v, d);
const sign = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function ago(ts) {
  const s = Math.max(0, (Date.now() - ts) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function clock(ts) {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/* ---------- auth ---------- */
let token = localStorage.getItem("helm_token") || "";
const headers = () => (token ? { "X-Helm-Token": token } : {});

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...headers(), ...(options.headers || {}) } });
  if (response.status === 401) { askToken(); throw new Error("unauthorised"); }
  if (!response.ok) throw new Error(`${path} ${response.status}`);
  return response.json();
}

function askToken() {
  if (document.querySelector(".modal-bg")) return;
  const wrap = document.createElement("div");
  wrap.className = "modal-bg";
  wrap.innerHTML = `<div class="modal">
    <h3>Access token</h3>
    <p>This dashboard is protected. Paste the value of <code>HELM_UI_TOKEN</code>.</p>
    <input id="tokenInput" type="password" placeholder="token" autocomplete="off">
    <div class="acts"><button class="ctl" id="tokenSave">Unlock</button></div>
  </div>`;
  document.body.appendChild(wrap);
  const input = wrap.querySelector("#tokenInput");
  input.focus();
  const save = () => {
    token = input.value.trim();
    localStorage.setItem("helm_token", token);
    wrap.remove();
    boot();
  };
  wrap.querySelector("#tokenSave").onclick = save;
  input.onkeydown = (e) => { if (e.key === "Enter") save(); };
}

/* ---------- number tweening ----------
   Equity is the number people stare at. Jumping it between polls reads as a glitch;
   easing it reads as a live instrument. */
const tweens = new Map();
function tweenTo(el, target, format) {
  const from = tweens.get(el) ?? target;
  tweens.set(el, target);
  if (from === target) { el.textContent = format(target); return; }
  const started = performance.now();
  const duration = 600;
  const step = (now) => {
    const t = Math.min(1, (now - started) / duration);
    const eased = 1 - Math.pow(1 - t, 3);
    el.textContent = format(from + (target - from) * eased);
    if (t < 1 && tweens.get(el) === target) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/* ---------- state ---------- */
let snapshot = null;
let equityPoints = [];
const seenEvents = new Set();

const PHASES = {
  idle: "idle", syncing: "checking the account", deciding: "reading the daily close",
  executing: "sending an order", waiting: "watching",
};

const MARKS = {
  entry: "▲", win: "✓", loss: "✕", warn: "!", error: "!", info: "·",
};

const coinName = (symbol) => String(symbol || "").replace(/(EUR|USDT|USDC)$/, "");
const day = (ms) => (ms ? new Date(ms).toISOString().slice(0, 10) : "—");

function untilNext(state) {
  const due = state.decided_day == null ||
    state.decided_day < state.next_decision_at - 2 * 86400000;
  if (due) return { text: "due now", fraction: 1 };
  const left = Math.max(0, state.next_decision_at - Date.now());
  const h = Math.floor(left / 3600000), m = Math.floor((left % 3600000) / 60000);
  return { text: `in ${h}h ${String(m).padStart(2, "0")}m`, fraction: 1 - left / 86400000 };
}

function applyState(state) {
  snapshot = state;
  const account = state.account || {};
  const quote = account.quote || "";

  /* header */
  const modePill = $("modePill");
  const live = state.config?.mode === "live";
  modePill.className = "pill " + (state.halted ? "halt" : live ? "live" : "dry");
  $("modeText").textContent = state.halted ? "halted" : live ? "live · real money" : "dry run";

  const venuePill = $("venuePill");
  venuePill.hidden = false;
  venuePill.textContent = `${state.config?.venue ?? "?"} · ${quote}`;

  $("phase").dataset.phase = state.phase;
  $("phaseText").textContent = PHASES[state.phase] || state.phase;
  $("pauseBtn").textContent = state.paused ? "Resume" : "Pause";

  /* equity */
  $("quoteCur").textContent = quote;
  tweenTo($("equity"), account.equity || 0, (v) => money(v));
  tweenTo($("cash"), account.cash || 0, (v) => money(v));
  $("peak").textContent = money(account.peak_equity);
  const symbols = state.doctrine?.universe?.symbols || [];
  $("heldCount").textContent = `${(state.positions || []).length} of ${symbols.length}`;

  const ret = account.total_return_pct || 0;
  $("ringPct").textContent = signed(ret, 1) + "%";
  $("ringPct").className = "pct num " + sign(ret);
  // The ring fills over a -50%..+50% span: a trend rule moves in big, slow steps, and a
  // narrower span would sit pinned at full for most of a good year.
  const span = 50;
  const fraction = Math.min(1, Math.abs(ret) / span);
  const arc = $("ringArc");
  arc.style.strokeDashoffset = String(326.7 * (1 - fraction));
  // A green ring above a red number reads as a bug. The arc takes the sign too.
  arc.style.stroke = ret < 0 ? "var(--loss)" : "url(#ringGrad)";
  $("equitySub").textContent =
    `started at ${money(account.starting_equity)} ${quote} · ` +
    (state.cycle ? `cycle ${state.cycle}, last ${ago(state.last_cycle_at)}` : "starting");

  /* the rule */
  const equity = account.equity || 0;
  const invested = equity ? (account.invested || 0) / equity * 100 : 0;
  $("investedText").textContent = `${money(account.invested)} ${quote} · ${money(invested, 0)}%`;
  $("investedBar").style.width = Math.min(100, invested) + "%";

  const dd = (account.drawdown || 0) * 100;
  const ddLimit = (state.doctrine?.halts?.max_drawdown || 0.7) * 100;
  $("ddText").textContent = `${money(dd, 1)}% of ${money(ddLimit, 0)}%`;
  const ddBar = $("ddBar");
  ddBar.style.width = Math.min(100, (dd / ddLimit) * 100) + "%";
  ddBar.className = dd > ddLimit * 0.6 ? "hot" : "";

  const next = untilNext(state);
  $("nextText").textContent = `${next.text} · 00:00 UTC`;
  $("nextBar").style.width = Math.min(100, next.fraction * 100) + "%";

  renderBlocks(state);
  renderCoins(state.coins || [], state);
  renderPositions(state.positions || [], quote, state.coins || []);
  renderNotices(state);
}

function renderBlocks(state) {
  const el = $("blocks");
  const blocks = state.blocks || [];
  const rule = state.doctrine?.rule || {};
  const sizing = state.doctrine?.sizing || {};
  if (!blocks.length) {
    el.innerHTML = `<div style="display:flex;align-items:center;gap:9px;font-size:13px">
        <span class="dot" style="color:var(--win)"></span>
        <span>Following the rule.</span>
      </div>
      <p class="muted" style="margin:10px 0 0;font-size:11.5px;line-height:1.6">
        Hold a coin while its daily close is above its ${rule.sma_days ?? 200}-day average;
        sell it on a close below. Each purchase is
        ${money((sizing.entry_fraction || 0.25) * 100, 0)}% of the account. Checked once a day,
        at 00:00 UTC — nothing happens in between.
      </p>`;
    return;
  }
  el.innerHTML = blocks.map((reason) =>
    `<div style="display:flex;gap:9px;font-size:12.5px;margin-bottom:9px;align-items:flex-start">
       <span class="dot" style="color:var(--warn);margin-top:6px"></span>
       <span>Not buying: ${esc(reason)}</span>
     </div>`).join("") +
    `<p class="muted" style="margin:4px 0 0;font-size:11.5px">A coin whose trend breaks is still sold.</p>`;
}

function renderNotices(state) {
  const notices = [];
  if (state.halted) {
    notices.push(`<div class="notice danger"><b>Halted.</b>
      <span>${esc(state.halt_reason)} — no purchases until this is cleared by hand. Sales
      still happen.</span></div>`);
  }
  if (state.synthetic) {
    notices.push(`<div class="notice warn"><b>Generated prices.</b>
      <span>The venue was unreachable or synthetic mode is on, so these are simulated
      markets. Nothing shown here reflects a real price and no result means anything.</span></div>`);
  }
  if (state.last_error) {
    notices.push(`<div class="notice danger"><b>Last cycle failed.</b>
      <span>${esc(state.last_error)}</span></div>`);
  }
  $("notices").innerHTML = notices.join("");
}

/* ---------- narration ---------- */
function pushLine(event, animate = true) {
  const key = `${event.ts}-${event.data?.message}`;
  if (seenEvents.has(key)) return;
  seenEvents.add(key);

  const feed = $("feed");
  feed.querySelector(".empty")?.remove();

  const level = event.data?.level || "info";
  const line = document.createElement("div");
  line.className = "line";
  line.dataset.level = level;
  if (!animate) line.style.animation = "none";
  line.innerHTML = `<div class="mark">${MARKS[level] || "·"}</div>
    <div class="body">
      <div class="msg">${esc(event.data?.message)}</div>
      <div class="when">${clock(event.ts)}</div>
    </div>`;
  feed.prepend(line);

  while (feed.children.length > 90) feed.lastChild.remove();
  $("feedCount").textContent = String(feed.children.length);
}

/* ---------- the four coins ----------
   Each coin against its average. The gauge is centred on the average: right of centre is
   above it (held, or bought at the next close), left is below (cash). The verdict that
   counts is the one at the close; where the price is now is shown for interest only. */
function gauge(distance, span = 0.3) {
  if (distance === null || distance === undefined) return "";
  const x = Math.max(-1, Math.min(1, distance / span)) * 50;
  const left = x >= 0 ? 50 : 50 + x;
  return `<div class="gauge">
      <div class="fill ${x >= 0 ? "up" : "down"}" style="left:${left}%;width:${Math.abs(x)}%"></div>
      <div class="mid"></div>
    </div>`;
}

function renderCoins(rows, state) {
  const el = $("coins");
  const decided = rows.find((r) => r.day != null);
  $("coinDay").textContent = decided ? `close of ${day(decided.day)}` : "—";
  if (!rows.length) { el.innerHTML = `<div class="empty">waiting for the first daily close…</div>`; return; }

  el.innerHTML = rows.map((row) => {
    const known = row.on !== null && row.on !== undefined;
    const state = !known ? "no verdict yet"
      : row.on ? (row.held ? "above · held" : "above · buys at the close")
      : (row.held ? "below · sells at the close" : "below · cash");
    const digits = row.price > 100 ? 2 : 4;
    return `<div class="coin" data-on="${known ? row.on : ""}">
      <div class="top">
        <span class="sym">${esc(coinName(row.symbol))}</span>
        <span class="state">${esc(state)}</span>
      </div>
      <div class="price num">${money(row.price, digits)}</div>
      ${known ? gauge(row.distance) : ""}
      ${known ? `<div class="foot num">
          <span>close <b class="${sign(row.distance)}">${signed(row.distance * 100, 1)}%</b></span>
          <span>now <b class="${sign(row.now_distance)}">${row.now_distance == null ? "—" : signed(row.now_distance * 100, 1) + "%"}</b></span>
          <span>avg ${money(row.average, digits)}</span>
        </div>` : ""}
    </div>`;
  }).join("");
}

/* ---------- held coins ---------- */
function renderPositions(positions, quote, coins) {
  const el = $("positions");
  $("posCount").textContent = String(positions.length);
  if (!positions.length) {
    el.innerHTML = `<div class="empty"><div class="big">All cash</div>No coin is above its average and held.</div>`;
    return;
  }

  el.innerHTML = positions.map((p) => {
    const coin = coins.find((c) => c.symbol === p.symbol) || {};
    const average = coin.average;
    // Drawn to scale between the average (the line a close must stay above) and the highest
    // of entry and now, so the gap between the price and the exit is literal.
    const points = [p.entry_price, p.price, average].filter((v) => v);
    const low = Math.min(...points), high = Math.max(...points);
    const pad = (high - low) * 0.12 || high * 0.02;
    const at = (v) => ((v - (low - pad)) / ((high + pad) - (low - pad))) * 100;
    const held = Math.max(0, (Date.now() - p.opened_at) / 86400000);

    return `<div class="position">
      <div class="head">
        <span class="sym">${esc(coinName(p.symbol))}</span>
        <span class="setup">held ${held < 1 ? "today" : money(held, 0) + "d"}</span>
        <div class="pnl">
          <div class="big num ${sign(p.unrealized)}">${signed(p.unrealized)} ${esc(quote)}</div>
          <div class="r num">${signed(p.unrealized_pct, 1)}% on ${money(p.cost)}</div>
        </div>
      </div>

      ${average ? `<div class="track">
        <div class="rail">
          <div class="cushion" style="left:${at(average)}%;width:${Math.max(0, at(p.price) - at(average))}%"></div>
        </div>
        <div class="tick avg" style="left:${at(average)}%"></div>
        <div class="tick entry" style="left:${at(p.entry_price)}%"></div>
        <div class="now" style="left:${at(p.price)}%"></div>
        <div class="lbl" style="left:${at(average)}%">average</div>
        <div class="lbl" style="left:${at(p.entry_price)}%">bought</div>
      </div>` : ""}

      <div class="meta">
        <span>worth <b class="num">${money(p.value)} ${esc(quote)}</b></span>
        <span>bought <b class="num">${money(p.entry_price, p.entry_price > 100 ? 2 : 4)}</b></span>
        <span>now <b class="num">${money(p.price, p.price > 100 ? 2 : 4)}</b></span>
        ${average ? `<span>sold on a close below <b class="num">${money(average, average > 100 ? 2 : 4)}</b></span>` : ""}
      </div>
      ${p.last_note ? `<div class="note">${esc(p.last_note)}</div>` : ""}
      <div class="act"><button class="ctl danger" data-close="${esc(p.id)}">Sell now</button></div>
    </div>`;
  }).join("");

  el.querySelectorAll("[data-close]").forEach((button) => {
    button.onclick = async () => {
      if (!confirm("Sell this coin at market, right now? The rule will buy it back at the next " +
                   "daily close if it is still above its average — pause first to stop that.")) return;
      button.disabled = true;
      try { await api(`/api/control/close/${button.dataset.close}`, { method: "POST" }); }
      catch (e) { alert(e.message); button.disabled = false; }
    };
  });
}

/* ---------- equity chart ---------- */
function drawChart() {
  const canvas = $("equityChart");
  const empty = $("chartEmpty");
  if (equityPoints.length < 2) { empty.style.display = "grid"; return; }
  empty.style.display = "none";

  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const values = equityPoints.map((p) => p.equity);
  const start = snapshot?.account?.starting_equity ?? values[0];
  let low = Math.min(...values, start);
  let high = Math.max(...values, start);

  // Enforce a minimum visible range of +/-2% around the starting capital. Without it the
  // y-axis auto-scales to whatever tiny spread exists and a 30-cent move is drawn as a
  // crash — technically accurate, completely misleading.
  const floor = start * 0.02;
  if (high - low < floor * 2) {
    const middle = (high + low) / 2;
    low = Math.min(low, middle - floor);
    high = Math.max(high, middle + floor);
  }
  const pad = (high - low) * 0.12;
  low -= pad; high += pad;

  const x = (i) => (i / (equityPoints.length - 1)) * (width - 12) + 6;
  const y = (v) => height - 14 - ((v - low) / (high - low)) * (height - 28);

  // The starting-capital line: the only reference that matters for "am I ahead".
  ctx.strokeStyle = "rgba(120,130,154,0.35)";
  ctx.setLineDash([3, 4]);
  ctx.beginPath(); ctx.moveTo(0, y(start)); ctx.lineTo(width, y(start)); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = "rgba(120,130,154,0.8)";
  ctx.font = "10px ui-monospace, monospace";
  ctx.fillText(money(start), 6, y(start) - 5);

  const last = values[values.length - 1];
  const up = last >= start;
  const line = up ? "#25e0a0" : "#ff5b78";

  const fill = ctx.createLinearGradient(0, 0, 0, height);
  fill.addColorStop(0, up ? "rgba(37,224,160,0.22)" : "rgba(255,91,120,0.20)");
  fill.addColorStop(1, "rgba(0,0,0,0)");

  ctx.beginPath();
  equityPoints.forEach((p, i) => (i ? ctx.lineTo(x(i), y(p.equity)) : ctx.moveTo(x(i), y(p.equity))));
  ctx.lineTo(x(equityPoints.length - 1), height);
  ctx.lineTo(x(0), height);
  ctx.closePath();
  ctx.fillStyle = fill; ctx.fill();

  ctx.beginPath();
  equityPoints.forEach((p, i) => (i ? ctx.lineTo(x(i), y(p.equity)) : ctx.moveTo(x(i), y(p.equity))));
  ctx.strokeStyle = line; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.stroke();

  const lastX = x(equityPoints.length - 1);
  const lastY = y(last);
  ctx.beginPath(); ctx.arc(lastX, lastY, 3.5, 0, Math.PI * 2);
  ctx.fillStyle = line; ctx.fill();
  ctx.beginPath(); ctx.arc(lastX, lastY, 7, 0, Math.PI * 2);
  ctx.fillStyle = up ? "rgba(37,224,160,0.18)" : "rgba(255,91,120,0.18)"; ctx.fill();
}

/* ---------- trades ---------- */
function renderTrades(rows, stats) {
  const quote = snapshot?.account?.quote || "";
  $("tradeCount").textContent = String(rows.length);

  $("stats").innerHTML = [
    ["Trades", stats.trades],
    ["Won", stats.trades ? money(stats.win_rate, 0) + "%" : "—"],
    ["Average", stats.trades ? signed(stats.avg_return_pct, 1) + "%" : "—"],
    ["Total P&L", signed(stats.total_pnl) + " " + quote],
    ["Fees", money(stats.fees)],
    ["Days held", stats.trades ? money(stats.avg_days_held, 0) : "—"],
    ["Best", signed(stats.best)],
    ["Worst", signed(stats.worst)],
  ].map(([k, v]) => {
    const cls = typeof v === "string" && v.startsWith("+") ? "pos"
      : typeof v === "string" && v.startsWith("-") ? "neg" : "";
    return `<div class="stat"><div class="k">${k}</div><div class="v num ${cls}">${esc(v)}</div></div>`;
  }).join("");

  const el = $("trades");
  if (!rows.length) { el.innerHTML = `<div class="empty">No closed trades yet.</div>`; return; }

  el.innerHTML = `<table><thead><tr>
      <th>Sold</th><th>Coin</th><th>Why</th><th class="r">Days</th>
      <th class="r">Bought</th><th class="r">Sold</th><th class="r">Return</th><th class="r">P&L</th>
    </tr></thead><tbody>${rows.map((t) => `<tr>
      <td class="muted num">${day(t.closed_at)}</td>
      <td><b>${esc(coinName(t.symbol))}</b></td>
      <td class="muted">${esc({ TREND_OFF: "closed below average", MANUAL: "sold by hand",
        FLATTENED: "flattened", EXTERNALLY_CLOSED: "left the account" }[t.exit_reason]
        || (t.exit_reason || "").replace(/_/g, " ").toLowerCase())}</td>
      <td class="r num">${money(((t.closed_at || 0) - (t.opened_at || 0)) / 86400000, 0)}</td>
      <td class="r num">${money(t.entry_price, t.entry_price > 100 ? 2 : 4)}</td>
      <td class="r num">${money(t.exit_price, t.exit_price > 100 ? 2 : 4)}</td>
      <td class="r num ${sign(t.return_pct)}">${signed(t.return_pct, 1)}%</td>
      <td class="r num ${sign(t.realized_pnl)}">${signed(t.realized_pnl)}</td>
    </tr>`).join("")}</tbody></table>`;
}

/* ---------- decisions ---------- */
const ACTIONS = {
  BUY: ["bought", "go"], SELL: ["sold", "sell"], HOLD: ["held", "hold"], STAY_OUT: ["no trade", "no"],
  BUY_FAILED: ["buy failed", "fail"], SELL_FAILED: ["sale failed", "fail"],
};

function renderDecisions(rows) {
  const el = $("decisions");
  if (!rows.length) { el.innerHTML = `<div class="empty">No daily close has been read yet.</div>`; return; }

  el.innerHTML = `<table><thead><tr>
      <th>Close of</th><th>Coin</th><th>Verdict</th><th class="r">Close</th><th class="r">Average</th>
      <th class="r">Distance</th><th>Action</th><th>Note</th>
    </tr></thead><tbody>${rows.map((d) => {
      const [label, cls] = ACTIONS[d.action] || [d.action, "no"];
      const digits = d.close > 100 ? 2 : 4;
      return `<tr>
        <td class="muted num">${day(d.day)}</td>
        <td><b>${esc(coinName(d.symbol))}</b></td>
        <td class="${d.on ? "pos" : "neg"}">${d.on ? "above" : "below"}</td>
        <td class="r num">${money(d.close, digits)}</td>
        <td class="r num">${money(d.average, digits)}</td>
        <td class="r num ${sign(d.distance)}">${signed(d.distance * 100, 1)}%</td>
        <td><span class="verdict ${cls}">${esc(label)}</span></td>
        <td class="muted" style="font-size:11.5px">${esc(d.note)}</td>
      </tr>`;
    }).join("")}</tbody></table>`;
}

/* ---------- doctrine ----------
   A deliberately small markdown subset: enough for the document that actually exists,
   with everything escaped first so the renderer can never inject markup. */
function renderMarkdown(text) {
  const lines = esc(text).split("\n");
  const out = [];
  let inList = false, inCode = false, inTable = false;

  const closeList = () => { if (inList) { out.push("</ul>"); inList = false; } };
  const closeTable = () => { if (inTable) { out.push("</tbody></table>"); inTable = false; } };

  const inline = (s) => s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>");

  for (const raw of lines) {
    const line = raw.trimEnd();

    if (line.startsWith("```")) {
      closeList(); closeTable();
      out.push(inCode ? "</code></pre>" : "<pre><code>");
      inCode = !inCode;
      continue;
    }
    if (inCode) { out.push(raw + "\n"); continue; }

    if (/^\|/.test(line)) {
      const cells = line.split("|").slice(1, -1).map((c) => c.trim());
      if (/^[-:\s|]+$/.test(line)) continue;      // the |---| separator row
      if (!inTable) {
        closeList();
        out.push("<table><thead><tr>" + cells.map((c) => `<th>${inline(c)}</th>`).join("") +
                 "</tr></thead><tbody>");
        inTable = true;
      } else {
        out.push("<tr>" + cells.map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>");
      }
      continue;
    }
    closeTable();

    if (/^#{1,6}\s/.test(line)) {
      closeList();
      const level = line.match(/^#+/)[0].length;
      out.push(`<h${level}>${inline(line.replace(/^#+\s*/, ""))}</h${level}>`);
    } else if (/^[-*]\s+/.test(line)) {
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push(`<li>${inline(line.replace(/^[-*]\s+/, ""))}</li>`);
    } else if (/^\d+\.\s+/.test(line)) {
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push(`<li>${inline(line.replace(/^\d+\.\s+/, ""))}</li>`);
    } else if (/^---+$/.test(line)) {
      closeList(); out.push("<hr>");
    } else if (/^&gt;\s?/.test(line)) {
      closeList(); out.push(`<blockquote>${inline(line.replace(/^&gt;\s?/, ""))}</blockquote>`);
    } else if (!line.trim()) {
      closeList();
    } else {
      closeList(); out.push(`<p>${inline(line)}</p>`);
    }
  }
  closeList(); closeTable();
  return out.join("\n");
}

/* ---------- live connection ---------- */
let socket = null;
let retryDelay = 1000;

function connect() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  socket = new WebSocket(`${scheme}://${location.host}/ws${query}`);

  socket.onopen = () => { retryDelay = 1000; };

  socket.onmessage = (message) => {
    const event = JSON.parse(message.data);
    if (event.kind === "state") applyState(event.data);
    else if (event.kind === "narrate") pushLine(event);
    else if (["position_closed", "position_opened", "decision"].includes(event.kind)) refreshSlow();
  };

  socket.onclose = (event) => {
    if (event.code === 4401) { askToken(); return; }
    $("phaseText").textContent = "reconnecting…";
    // Back off, but never past 15s — a dashboard that gives up is worse than useless.
    setTimeout(connect, retryDelay);
    retryDelay = Math.min(15000, retryDelay * 1.7);
  };
}

async function refreshSlow() {
  try {
    const [trades, stats, decisions, equity] = await Promise.all([
      api("/api/trades"), api("/api/stats"), api("/api/decisions"), api("/api/equity"),
    ]);
    renderTrades(trades, stats);
    renderDecisions(decisions);
    equityPoints = equity;
    drawChart();
  } catch (e) { /* the socket carries the live half; a failed poll is not fatal */ }
}

/* ---------- controls ---------- */
$("pauseBtn").onclick = async () => {
  const path = snapshot?.paused ? "/api/control/resume" : "/api/control/pause";
  try { await api(path, { method: "POST" }); } catch (e) { alert(e.message); }
};

$("flattenBtn").onclick = async () => {
  const open = snapshot?.positions?.length || 0;
  if (!open) { alert("Nothing to flatten — nothing held."); return; }
  if (!confirm(`Sell all ${open} coin(s) at market immediately? Purchases are paused ` +
               `afterwards, so the rule does not buy straight back in.`)) return;
  try { await api("/api/control/flatten", { method: "POST" }); } catch (e) { alert(e.message); }
};

document.querySelectorAll("nav.tabs button").forEach((button) => {
  button.onclick = () => {
    document.querySelectorAll("nav.tabs button").forEach((b) => b.classList.toggle("on", b === button));
    document.querySelectorAll(".view").forEach((v) =>
      v.classList.toggle("on", v.id === `view-${button.dataset.view}`));
    if (button.dataset.view === "live") drawChart();
  };
});

window.addEventListener("resize", drawChart);

/* ---------- boot ---------- */
async function boot() {
  try {
    const state = await api("/api/state");
    applyState(state);
  } catch (e) { return; }

  try {
    const events = await api("/api/feed?limit=60");
    events.slice().reverse().forEach((row) =>
      pushLine({ ts: row.ts, data: { message: row.message, level: row.level } }, false));
  } catch (e) { /* the feed backfill is a nicety, not a requirement */ }

  try {
    const text = await (await fetch("/api/doctrine.md")).text();
    $("doctrine").innerHTML = renderMarkdown(text);
  } catch (e) { $("doctrine").textContent = "Could not load the doctrine."; }

  await refreshSlow();
  connect();
  setInterval(refreshSlow, 30000);
}

boot();
