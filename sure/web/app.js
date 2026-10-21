"use strict";
// Sure? board page. Every piece of server data goes through textContent / createElement only.
// The flow map is inline SVG built with createElementNS; class names come only from fixed lists.

const COLS = ["incoming", "needs_me", "delegated", "done", "declined"];
const PENDING = new Set(["incoming", "needs_me"]);
const TIERS = {
  easy: { label: "Easy", note: "Easy to undo" },
  hard: { label: "Hard", note: "Hard to undo" },
  impossible: { label: "Impossible", note: "Cannot be undone" },
};
const ACTIONS = { block: "Block", ask: "Ask Claude", allow_once: "Allow once", always: "Always allow" };
const DONE_TEXT = { allow_once: "Allowed once", block: "Blocked", ask: "Sent to Claude's prompt" };
const OUTCOMES = {
  decided: "Decided here",
  timeout: "No answer in time: Claude asked instead",
  replaced: "Replaced by a newer request",
  "answered elsewhere": "Answered in Claude",
  "board stopped": "Board stopped: Claude asked instead",
};
const TOKEN_KEY = "sure-board-token";
const CUT_MARK = /…\(\+\d+ chars\)$/; // the hook's marker on a command longer than 2000 characters
const DECIDED_HOLD_MS = 10000; // hide a locally decided card from stale polls for at most this long
const CONFIRM_MS = 5000; // impossible-tier Allow once needs a second click within this window
const CONFIRM_DELAY_MS = 600; // ...but not sooner: a double-click must not arm and confirm in one gesture
const FETCH_TIMEOUT_MS = 8000; // a hung server fails the call, so polls go stale instead of hanging
const REPLAY_ROWS = 500;
const SVG_NS = "http://www.w3.org/2000/svg";
const HANDLER_CLASS = { you: "h-you", rule: "h-rule", classifier: "h-classifier" };
const VERBS = { asked: "Asked you", ran: "Ran", auto: "Allowed by a rule", quiet: "Allowed quietly", failed: "Failed",
  decided: "Decided on the board" };

let token = null;
let lastState = null;
let seen = null; // pending request ids already announced; null until the first poll
let polling = false;
let stale = false;
let gen = 0; // bumped on every local decision; polls that started earlier are dropped
let drag = null;
let pend = null;
let timers = [];
let detailSeq = 0;
let toastTimer = 0;
const cards = new Map(); // key -> { key, c, col, el, sig, deadline }
const expanded = new Set(); // request ids whose details are open
const decided = new Map(); // request id -> time decided here (ms)
const okToasts = new Map(); // request id -> time of its success toast (ms)
const flowMemo = new Map(); // flow container id -> { sig, counts } so a 1 s poll rebuilds only on change
let trustedSig = null;
let cwdSig = null;
let replaySeq = 0;

const $ = (sel, root) => (root || document).querySelector(sel);

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function str(v) {
  // replay events come from a file anyone can edit: any JSON value may show up
  if (typeof v === "string") return v;
  return v === null || v === undefined ? "" : JSON.stringify(v);
}

function short(s, n) {
  s = String(s);
  return s.length > (n || 120) ? s.slice(0, (n || 120) - 1) + "…" : s;
}

// token

function readToken() {
  const h = location.hash.slice(1);
  if (h) {
    try { sessionStorage.setItem(TOKEN_KEY, h); } catch (e) { /* private mode: keep it in memory */ }
    history.replaceState(null, "", location.pathname + location.search);
    return h;
  }
  try { return sessionStorage.getItem(TOKEN_KEY); } catch (e) { return null; }
}

function forgetToken() {
  token = null;
  try { sessionStorage.removeItem(TOKEN_KEY); } catch (e) { /* nothing stored */ }
}

function showNotice(rejected) {
  timers.forEach(clearInterval);
  timers = [];
  $("#board").hidden = true;
  $("#tabs").hidden = true;
  $("#panel-live").hidden = true;
  $("#panel-replay").hidden = true;
  $("#stale").hidden = true;
  const n = $("#notice");
  $("h2", n).textContent = rejected ? "This board key no longer works" : "No board key";
  n.hidden = false;
  setStatus("Not connected", "bad");
}

// network

async function api(method, path, body) {
  const opts = { method, headers: { "X-Sure-Token": token }, cache: "no-store" };
  if (method !== "GET") {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body || {});
  }
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), FETCH_TIMEOUT_MS);
  opts.signal = ctl.signal;
  let r;
  let data = {};
  try {
    r = await fetch(path, opts);
    try { data = await r.json(); } catch (e) {
      if (e.name === "AbortError") throw e; // body never finished: a timeout, not an empty body
    }
  } catch (e) {
    throw e.name === "AbortError" ? new Error("no answer in " + FETCH_TIMEOUT_MS / 1000 + " s") : e;
  } finally {
    clearTimeout(timer);
  }
  if (r.status === 403) {
    forgetToken();
    showNotice(true);
  }
  if (!r.ok) {
    const err = new Error(data.error || "HTTP " + r.status);
    err.status = r.status;
    throw err;
  }
  return data;
}

// Presence: poll and heartbeat only while this tab is visible. A hidden board that kept claiming
// presence would make the hook wait for nobody; while hidden, the hook skips the board and
// Claude's own prompt handles the request.
async function poll() {
  if (polling || !token || document.hidden) return;
  polling = true;
  const started = gen;
  try {
    const s = await api("GET", "/api/state");
    setStale(false);
    setStatus("Live", "ok");
    if (!drag && started === gen) applyState(s, true); // a decision landed mid-flight: this answer is stale
  } catch (e) {
    if (token) setStale(true);
  } finally {
    polling = false;
  }
}

function beat() {
  if (token && !document.hidden) api("POST", "/api/heartbeat", {}).catch(() => {});
}

function setStatus(text, kind) {
  const s = $("#status");
  s.textContent = text;
  s.className = "status " + kind;
}

function setStale(on) {
  if (on === stale) return;
  stale = on;
  $("#stale").hidden = !on;
  $("#board").classList.toggle("stale", on);
  if (on) {
    setStatus("Board unreachable", "bad");
    cancelDrag();
  }
  document.querySelectorAll(".actions").forEach((row) => {
    if (!row.classList.contains("busy")) setRowDisabled(row, on);
  });
  tick();
}

// state -> DOM, keyed so expanded cards, focus and drags survive the 1 s poll

function keysFor(s) {
  const out = [];
  const now = Date.now();
  const stillPending = new Set();
  for (const col of COLS) {
    const list = Array.isArray(s[col]) ? s[col].slice() : [];
    if (!PENDING.has(col)) list.reverse(); // newest finished first
    const n = {};
    for (const c of list) {
      if (!c || typeof c.request_id !== "string") continue;
      let key = "p:" + c.request_id;
      if (PENDING.has(col)) {
        const at = decided.get(c.request_id);
        if (at !== undefined && now - at < DECIDED_HOLD_MS) {
          stillPending.add(c.request_id);
          continue; // decided here; this poll has not caught up yet
        }
      } else {
        n[c.request_id] = (n[c.request_id] || 0) + 1;
        key = "f:" + col + ":" + c.request_id + ":" + n[c.request_id];
      }
      out.push({ key, c, col });
    }
  }
  for (const rid of decided.keys()) if (!stillPending.has(rid)) decided.delete(rid);
  return out;
}

function sigOf(c) {
  return JSON.stringify([c.tier, c.kind, c.what, c.command, c.cwd, c.ts, c.session, c.tool, c.why, c.cost, c.undo]);
}

// fromServer: deadlines are recomputed from deadline_in only on a real poll; a local re-apply
// (moveLocally) keeps the running countdowns instead of resetting them to the last poll's numbers.
function applyState(s, fromServer) {
  lastState = s;
  const want = keysFor(s);
  const fresh = [];
  const keep = new Set();
  const perCol = {};
  const now = Date.now();
  COLS.forEach((col) => { perCol[col] = []; });
  for (const w of want) {
    keep.add(w.key);
    const sig = sigOf(w.c);
    let entry = cards.get(w.key);
    const moved = entry && entry.col !== w.col && !(PENDING.has(entry.col) && PENDING.has(w.col));
    if (!entry || moved || entry.sig !== sig) {
      const old = entry;
      entry = { key: w.key, c: w.c, col: w.col, sig, deadline: old ? old.deadline : undefined };
      entry.el = buildCard(entry);
      if (old) old.el.replaceWith(entry.el);
      cards.set(w.key, entry);
    }
    entry.c = w.c;
    entry.col = w.col;
    if (fromServer || entry.deadline === undefined) {
      entry.deadline = typeof w.c.deadline_in === "number" ? now + w.c.deadline_in * 1000 : null;
    }
    perCol[w.col].push(entry.el);
    if (PENDING.has(w.col) && seen && !seen.has(w.c.request_id)) fresh.push(w.c);
  }
  for (const [key, entry] of cards) {
    if (!keep.has(key)) {
      entry.el.remove();
      cards.delete(key);
    }
  }
  for (const col of COLS) placeCards($('[data-list="' + col + '"]'), perCol[col]);
  for (const col of COLS) $('[data-count="' + col + '"]').textContent = String(perCol[col].length);
  const waiting = perCol.incoming.length + perCol.needs_me.length;
  $("#live-count").textContent = waiting ? "(" + waiting + " waiting)" : "";
  announceNew(fresh, want);
  tick();
  renderFlowMap(s);
  renderTrusted(s);
  fillCwds(s);
}

function placeCards(list, els) {
  els.forEach((node, i) => {
    if (list.children[i] !== node) list.insertBefore(node, list.children[i] || null);
  });
  while (list.children.length > els.length) list.lastChild.remove();
}

function announceNew(fresh, want) {
  const pendingIds = want.filter((w) => PENDING.has(w.col)).map((w) => w.c.request_id);
  if (seen === null) {
    if (pendingIds.length) say(pendingIds.length + " request(s) waiting for you.");
  } else if (fresh.length) {
    say(fresh.map((c) => "New " + tierOf(c) + " request: " + short(titleOf(c))).join(". "));
  }
  seen = new Set(pendingIds);
}

function say(text) {
  $("#live").textContent = text;
}

// flow map: sessions -> kinds -> handlers, as ribbons whose widths are the counts

function renderFlowMap(s) {
  $("#flow-map").hidden = false;
  renderFlowInto($("#live-flow"), s.flow, "No decisions yet. Each one you or a rule makes shows up here.", true);
}

function sessionName(v) {
  const s = str(v);
  return s ? "Session " + s.slice(0, 8) : "Unknown session";
}

function svgEl(tag, attrs, cls) {
  const n = document.createElementNS(SVG_NS, tag);
  for (const k of Object.keys(attrs || {})) n.setAttribute(k, String(attrs[k]));
  if (cls) n.setAttribute("class", cls);
  return n;
}

function flowModel(flow) {
  // cols[i]: Map name -> { name, full, total }; links: Map key -> { a, b, n, col, handler }
  const cols = [new Map(), new Map(), new Map()];
  const links = new Map();
  const add = (i, name, full, n) => {
    const node = cols[i].get(name) || { name, full, total: 0 };
    node.total += n;
    cols[i].set(name, node);
  };
  const link = (col, a, b, n, handler) => {
    const key = col + "\u0000" + a + "\u0000" + b;
    const l = links.get(key) || { key, col, a, b, n: 0, handler };
    l.n += n;
    links.set(key, l);
  };
  for (const r of Array.isArray(flow) ? flow : []) {
    const n = r && Number.isFinite(r.count) && r.count > 0 ? r.count : 0;
    if (!n) continue;
    const sid = "s:" + str(r.session);
    const kind = str(r.kind) || "unknown kind";
    const handler = str(r.handler) || "unknown";
    add(0, sid, str(r.session) ? "Session " + str(r.session) : "Unknown session", n);
    add(1, kind, "Kind " + kind, n);
    add(2, handler, "Decided by " + handler, n);
    link(0, sid, kind, n, null);
    link(1, kind, handler, n, handler);
  }
  return { cols, links };
}

function layoutFlow(model) {
  const W = 760, PAD = 14, GAP = 10, MIN_H = 22, NODE_W = 12;
  const X = [178, 392, 606];
  const total = [...model.cols[0].values()].reduce((a, n) => a + n.total, 0);
  const unit = Math.min(14, 240 / total);
  let H = 0;
  const pos = [new Map(), new Map(), new Map()];
  model.cols.forEach((col, i) => {
    let y = PAD;
    const nodes = [...col.values()].sort((p, q) => q.total - p.total || (p.name < q.name ? -1 : 1));
    nodes.forEach((node, idx) => {
      const h = Math.max(MIN_H, node.total * unit);
      pos[i].set(node.name, Object.assign({ x: X[i], y, h, idx, outY: y, inY: y }, node));
      y += h + GAP;
    });
    H = Math.max(H, y - GAP + PAD);
  });
  return { W, H, NODE_W, pos };
}

function ribbon(src, dst, n, nodeW) {
  const t1 = src.h * n / src.total;
  const t2 = dst.h * n / dst.total;
  const x1 = src.x + nodeW, x2 = dst.x, mx = (x1 + x2) / 2;
  const a1 = src.outY, a2 = dst.inY;
  src.outY += t1;
  dst.inY += t2;
  return "M" + x1 + " " + a1 + "C" + mx + " " + a1 + " " + mx + " " + a2 + " " + x2 + " " + a2 +
    "L" + x2 + " " + (a2 + t2) + "C" + mx + " " + (a2 + t2) + " " + mx + " " + (a1 + t1) + " " + x1 + " " + (a1 + t1) + "Z";
}

function buildFlowSvg(model, L, before) {
  const svg = svgEl("svg", { viewBox: "0 0 " + L.W + " " + L.H, width: L.W, height: L.H, role: "img" }, "flow-svg");
  const total = [...model.cols[0].values()].reduce((a, n) => a + n.total, 0);
  svg.setAttribute("aria-label", "Flow map: " + total + " decision(s) from " + model.cols[0].size + " session(s) across " +
    model.cols[1].size + " kind(s). The same numbers are in the table that follows.");
  const linkG = svgEl("g", {}, "links");
  const ordered = [...model.links.values()].sort((p, q) =>
    p.col - q.col || L.pos[p.col].get(p.a).idx - L.pos[p.col].get(q.a).idx ||
    L.pos[p.col + 1].get(p.b).idx - L.pos[p.col + 1].get(q.b).idx);
  for (const l of ordered) {
    const src = L.pos[l.col].get(l.a), dst = L.pos[l.col + 1].get(l.b);
    const grew = before && (before.get(l.key) || 0) < l.n;
    const cls = "link " + (l.handler === null ? "h-none" : HANDLER_CLASS[l.handler] || "h-other") + (grew ? " fresh" : "");
    const p = svgEl("path", { d: ribbon(src, dst, l.n, L.NODE_W) }, cls);
    const tip = svgEl("title");
    tip.textContent = src.full + " → " + dst.full + ": " + l.n;
    p.append(tip);
    linkG.append(p);
  }
  svg.append(linkG);
  L.pos.forEach((col, i) => {
    for (const node of col.values()) {
      const g = svgEl("g", {}, "node " + (i === 2 ? HANDLER_CLASS[node.name] || "h-other" : "h-none"));
      const tip = svgEl("title");
      tip.textContent = node.full + ": " + node.total;
      const label = i === 0 ? sessionName(node.name.slice(2)) : short(node.name, i === 1 ? 22 : 14);
      const left = i === 0;
      const t = svgEl("text", { x: left ? node.x - 8 : node.x + L.NODE_W + 8, y: node.y + node.h / 2,
        "text-anchor": left ? "end" : "start", "dominant-baseline": "middle" });
      t.textContent = label + " · " + node.total;
      g.append(tip, svgEl("rect", { x: node.x, y: node.y, width: L.NODE_W, height: node.h, rx: 3 }), t);
      svg.append(g);
    }
  });
  return svg;
}

function flowTable(flow) {
  const table = el("table", "sr-only");
  table.append(el("caption", "", "Decisions by session, kind and who decided"));
  const head = el("tr");
  for (const h of ["Session", "Kind", "Decided by", "Count"]) {
    const th = el("th", "", h);
    th.scope = "col";
    head.append(th);
  }
  const thead = el("thead");
  thead.append(head);
  const body = el("tbody");
  for (const r of flow) {
    if (!r) continue;
    const tr = el("tr");
    tr.append(el("td", "", sessionName(r.session)), el("td", "", str(r.kind) || "unknown kind"),
      el("td", "", str(r.handler) || "unknown"), el("td", "", str(r.count)));
    body.append(tr);
  }
  table.append(thead, body);
  return table;
}

function renderFlowInto(box, flow, emptyText, animate) {
  const rows = Array.isArray(flow) ? flow : [];
  const sig = JSON.stringify(rows);
  const memo = flowMemo.get(box.id);
  if (memo && memo.sig === sig) return;
  const model = flowModel(rows);
  const counts = new Map([...model.links.values()].map((l) => [l.key, l.n]));
  flowMemo.set(box.id, { sig, counts });
  if (!model.links.size) {
    box.replaceChildren(el("p", "hint", emptyText));
    return;
  }
  // links pulse only when a count grew since the last render, never on first paint
  box.replaceChildren(buildFlowSvg(model, layoutFlow(model), animate && memo ? memo.counts : null), flowTable(rows));
}

// trusted kinds: per project, revocable here

function renderTrusted(s) {
  const t = s.trusted && typeof s.trusted === "object" ? s.trusted : {};
  const cwds = Object.keys(t).filter((cwd) => Array.isArray(t[cwd]));
  const sig = JSON.stringify(cwds.map((cwd) => [cwd, t[cwd]]));
  if (sig === trustedSig) return;
  trustedSig = sig;
  const strip = $("#trusted-strip");
  const hadFocus = strip.contains(document.activeElement);
  strip.hidden = !cwds.length; // nothing to show until some project has asked
  const rows = [];
  for (const cwd of cwds) {
    const kinds = t[cwd].filter((k) => typeof k === "string");
    if (!kinds.length) continue;
    const row = el("div", "trust-row");
    const where = el("span", "trust-cwd", short(cwd, 60));
    where.title = cwd;
    const list = el("ul", "chips");
    list.setAttribute("aria-label", "Always allowed in " + cwd);
    for (const kind of kinds) {
      const chip = el("li", "chip");
      const b = el("button", "chip-x", "Remove");
      b.type = "button";
      b.setAttribute("aria-label", "Stop always allowing " + kind + " in " + cwd);
      b.addEventListener("click", () => removeTrust(cwd, kind, b));
      chip.append(el("span", "kind", kind), b);
      list.append(chip);
    }
    row.append(where, list);
    rows.push(row);
  }
  if (!rows.length) rows.push(el("p", "hint", "Nothing is always allowed. \"Always allow\" on an easy card adds its kind here."));
  $("#trusted-list").replaceChildren(...rows);
  if (hadFocus && !strip.contains(document.activeElement)) $("#h-trusted").focus();
}

async function removeTrust(cwd, kind, b) {
  if (b.getAttribute("aria-disabled") === "true") return;
  b.setAttribute("aria-disabled", "true"); // not .disabled: that would drop keyboard focus
  try {
    await api("DELETE", "/api/trust", { cwd, kind });
    toast("No longer always allowing " + short(kind, 40) + " in " + short(cwd, 60), "ok");
    poll();
  } catch (e) {
    b.removeAttribute("aria-disabled");
    toast("Could not remove " + short(kind, 40) + ": " + short(e.message), "err");
  }
}

// replay

function fillCwds(s) {
  const set = new Set();
  if (s.trusted && typeof s.trusted === "object") Object.keys(s.trusted).forEach((k) => set.add(k));
  for (const col of COLS) {
    for (const c of Array.isArray(s[col]) ? s[col] : []) if (c && typeof c.cwd === "string" && c.cwd) set.add(c.cwd);
  }
  const cwds = [...set].sort();
  const sig = JSON.stringify(cwds);
  if (sig === cwdSig) return;
  cwdSig = sig;
  const sel = $("#replay-cwd");
  const keep = sel.value;
  sel.replaceChildren(...cwds.map((cwd) => {
    const o = el("option", "", cwd);
    o.value = cwd;
    return o;
  }));
  if (cwds.includes(keep)) sel.value = keep;
  $("#replay-empty").hidden = cwds.length > 0;
  sel.disabled = !cwds.length;
  $("#replay-load").disabled = !cwds.length;
}

function showReplayError(text) {
  const n = $("#replay-error");
  n.textContent = text;
  n.hidden = !text;
}

async function loadReplay() {
  const btn = $("#replay-load");
  const cwd = $("#replay-cwd").value;
  if (!cwd || btn.getAttribute("aria-disabled") === "true") return;
  const my = ++replaySeq;
  const days = $("#replay-days").value; // snapshot: the label names what was loaded, not what is selected now
  btn.setAttribute("aria-disabled", "true");
  btn.textContent = "Loading…";
  showReplayError("");
  $("#replay-out").hidden = true; // the previous result must not sit under a new load or its error
  $("#replay-showing").hidden = true;
  try {
    const r = await api("GET", "/api/replay?cwd=" + encodeURIComponent(cwd) + "&days=" + encodeURIComponent(days));
    if (my !== replaySeq) return;
    if (r.error) return showReplayError("Could not read this project's history: " + str(r.error));
    renderReplay(r);
    const showing = $("#replay-showing");
    showing.textContent = "Showing: " + cwd + ", last " + days + (days === "1" ? " day" : " days");
    showing.hidden = false;
  } catch (e) {
    if (my === replaySeq) showReplayError("Replay failed: " + short(e.message));
  } finally {
    if (my === replaySeq) {
      btn.removeAttribute("aria-disabled");
      btn.textContent = "Load";
    }
  }
}

function renderReplay(r) {
  $("#replay-out").hidden = false;
  $("#replay-report").textContent = str(r.report) || "No report for this range.";
  renderFlowInto($("#replay-flow"), r.flow, "Nothing was decided in this range.", false);
  const evs = Array.isArray(r.events) ? r.events : [];
  const rows = evs.slice(0, REPLAY_ROWS).map((ev) => {
    const e = ev && typeof ev === "object" ? ev : {};
    const tr = el("tr");
    const verb = str(e.verb);
    const kind = [str(e.kind), str(e.tier)].filter(Boolean).join(" / ");
    let said = Object.prototype.hasOwnProperty.call(VERBS, verb) ? VERBS[verb] : verb;
    const d = str(e.decision);
    if (verb === "decided" && Object.prototype.hasOwnProperty.call(DONE_TEXT, d)) said += ": " + DONE_TEXT[d];
    tr.append(el("td", "mono", str(e.ts).replace("T", " ")), el("td", "", said),
      el("td", "", str(e.what) || "-"), el("td", "mono", kind), el("td", "", str(e.via) || "-"));
    return tr;
  });
  $("tbody", $("#replay-table")).replaceChildren(...rows);
  $("#replay-note").textContent = evs.length > REPLAY_ROWS
    ? "Showing first " + REPLAY_ROWS + " of " + evs.length + " events."
    : evs.length + (evs.length === 1 ? " event." : " events.");
}

// tabs (WAI-ARIA tabs pattern, automatic activation)

function selectTab(tab, focus) {
  for (const t of document.querySelectorAll('[role="tab"]')) {
    const on = t === tab;
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;
    document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
  }
  if (tab.id !== "tab-live") cancelDrag();
  if (focus) tab.focus();
}

function tabKeys(ev) {
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const i = tabs.indexOf(document.activeElement);
  if (i < 0) return;
  const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[ev.key];
  if (next === undefined) return;
  ev.preventDefault();
  selectTab(tabs[(next + tabs.length) % tabs.length], true);
}

// one card

function tierOf(c) {
  return Object.prototype.hasOwnProperty.call(TIERS, c.tier) ? c.tier : "hard";
}

function titleOf(c) {
  return c.what || (c.tool ? c.tool + " request" : "Request");
}

// "Description: target" -> the plain description; the exact target is shown in the command block
function descOf(c) {
  const w = titleOf(c);
  const i = w.indexOf(": ");
  return i > 0 ? w.slice(0, i) : w;
}

function buttonsFor(c) {
  const t = tierOf(c);
  if (t === "impossible") return ["block", "allow_once"]; // server refuses ask here too
  if (t === "easy") return ["block", "ask", "allow_once", "always"];
  return ["block", "ask", "allow_once"];
}

function zonesFor(c) {
  // impossible-tier Allow once is a confirmed button only, never a drop
  return tierOf(c) === "impossible" ? ["block"] : buttonsFor(c);
}

function buildCard(entry) {
  const c = entry.c;
  const t = tierOf(c);
  const live = PENDING.has(entry.col);
  const li = el("li", "card tier-" + t + (live ? " live" : ""));
  li.dataset.rid = c.request_id;

  const meta = el("p", "meta");
  const badge = el("span", "tier-badge");
  badge.append(el("span", "tier-icon"), document.createTextNode(TIERS[t].label));
  badge.title = TIERS[t].note;
  meta.append(badge, el("span", "kind", c.kind || "unknown kind"),
    el("span", "session", String(c.session || "").slice(0, 8) || "unknown session"));
  li.append(meta);
  li.append(el("h3", "card-title", descOf(c)));
  if (c.command) li.append(el("pre", "cmd", c.command)); // what will actually run, always visible
  if (live && c.undo) li.append(el("p", "undo-line", c.undo.replace(/^(?:Easy|Hard|Impossible): */, "")));

  const status = el("p", "wait");
  if (!live) status.textContent = (DONE_TEXT[c.decision] || "Released") + " · " + (OUTCOMES[c.outcome] || c.outcome || "");
  li.append(status);

  li.append(buildDetails(entry, li));
  if (live) {
    li.append(buildActions(entry));
    li.addEventListener("pointerdown", (ev) => pointerDown(ev, entry));
  }
  return li;
}

function buildDetails(entry, li) {
  const c = entry.c;
  const id = "detail-" + (++detailSeq);
  const btn = el("button", "expand", "Details");
  btn.type = "button";
  btn.setAttribute("aria-controls", id);
  const box = el("dl", "details");
  box.id = id;
  if (c.command) {
    // the exact text Claude will run (control and bidi characters already stripped by the hook and server)
    const dd = el("dd");
    dd.append(el("pre", "command", c.command));
    if (CUT_MARK.test(c.command)) dd.append(el("p", "hint", "Cut short: the full command is longer than the card shows."));
    box.append(el("dt", "", "Exact command"), dd);
  }
  const rows = [["Why it matters", c.why], ["What it could cost", c.cost], ["How to undo", c.undo],
    ["Tool", c.tool], ["Folder", c.cwd], ["Asked at", c.ts]];
  for (const [k, v] of rows) {
    if (!v) continue;
    box.append(el("dt", "", k), el("dd", "", v));
  }
  const open = expanded.has(c.request_id);
  box.hidden = !open;
  li.classList.toggle("open", open);
  btn.setAttribute("aria-expanded", String(open));
  btn.addEventListener("click", () => {
    const now = box.hidden;
    box.hidden = !now;
    li.classList.toggle("open", now);
    btn.setAttribute("aria-expanded", String(now));
    if (now) expanded.add(entry.c.request_id); else expanded.delete(entry.c.request_id);
  });
  const wrap = el("div", "detail-wrap");
  wrap.append(btn, box);
  return wrap;
}

function buildActions(entry) {
  const row = el("div", "actions");
  row.setAttribute("role", "group");
  row.setAttribute("aria-label", "Decide: " + short(titleOf(entry.c)));
  for (const d of buttonsFor(entry.c)) {
    const b = el("button", "btn btn-" + d, ACTIONS[d]);
    b.type = "button";
    b.disabled = stale;
    if (d === "allow_once" && tierOf(entry.c) === "impossible") {
      b.addEventListener("click", () => confirmThenDecide(entry, b));
    } else {
      b.addEventListener("click", () => decideCard(entry, d));
    }
    row.append(b);
  }
  return row;
}

// Two clicks, at least CONFIRM_DELAY_MS and at most CONFIRM_MS apart. The delay is enforced by the
// timestamp; "Wait…" with aria-disabled is the visible half (aria-disabled keeps keyboard focus).
async function confirmThenDecide(entry, b) {
  if (b.dataset.armed === "1") {
    if (Date.now() - Number(b.dataset.armedAt) < CONFIRM_DELAY_MS) return;
    clearTimeout(Number(b.dataset.timer));
    if (!(await decideCard(entry, "allow_once"))) disarm(b); // failed: the next click starts over
    return;
  }
  b.dataset.armed = "1";
  b.dataset.armedAt = String(Date.now());
  b.textContent = "Wait…";
  b.setAttribute("aria-disabled", "true");
  b.classList.add("armed", "waiting");
  say("This cannot be undone. Press Allow once again within 5 seconds to allow it once.");
  setTimeout(() => {
    if (b.dataset.armed !== "1") return;
    b.textContent = "Really allow once?";
    b.removeAttribute("aria-disabled");
    b.classList.remove("waiting");
  }, CONFIRM_DELAY_MS);
  b.dataset.timer = String(setTimeout(() => disarm(b), CONFIRM_MS));
}

function disarm(b) {
  clearTimeout(Number(b.dataset.timer));
  b.dataset.armed = "";
  b.textContent = ACTIONS.allow_once;
  b.removeAttribute("aria-disabled");
  b.classList.remove("armed", "waiting");
}

// countdown, once a second, from the server's deadline_in

function tick() {
  const now = Date.now();
  for (const entry of cards.values()) {
    if (!PENDING.has(entry.col)) continue;
    const w = $(".wait", entry.el);
    if (stale) { w.textContent = "Board unreachable: Claude's prompt will handle this"; continue; }
    if (entry.deadline === null || entry.deadline === undefined) { w.textContent = "Waiting for you"; continue; }
    const left = Math.max(0, Math.ceil((entry.deadline - now) / 1000));
    w.textContent = left > 0 ? "Falls back to Claude's prompt in " + left + "s" : "Falling back to Claude's prompt";
    entry.el.classList.toggle("urgent", left > 0 && left <= 20);
  }
}

// decisions

function setRowDisabled(row, on) {
  row.querySelectorAll("button").forEach((b) => { b.disabled = on; });
}

function focusTargetAfter(li) {
  // keyboard users keep their place: next live card, else previous, else the column heading.
  // (the caller checks focus was in the card before its buttons were disabled)
  // A card detached during the await has no siblings or column: use the first heading still in the page.
  if (!li.isConnected) return $(".col h2");
  for (const sib of [li.nextElementSibling, li.previousElementSibling]) {
    const b = sib && $(".actions button", sib);
    if (b) return b;
  }
  const col = li.closest(".col");
  return col ? $("h2", col) : null;
}

async function decideCard(entry, d) {
  const c = entry.c;
  const li = entry.el;
  const row = $(".actions", li);
  if (stale || !row || row.classList.contains("busy")) return false;
  const hadFocus = li.contains(document.activeElement) ? document.activeElement : null;
  row.classList.add("busy");
  setRowDisabled(row, true);
  const title = short(titleOf(c));
  let trusted = false;
  try {
    if (d === "always") {
      await api("POST", "/api/trust", { cwd: c.cwd, kind: c.kind });
      trusted = true;
    }
    const decision = d === "always" ? "allow_once" : d;
    await api("POST", "/api/decide", { request_id: c.request_id, decision });
    const target = hadFocus ? focusTargetAfter(li) : null;
    gen++;
    decided.set(c.request_id, Date.now());
    moveLocally(c, decision);
    if (target && target.isConnected) target.focus();
    okToasts.set(c.request_id, Date.now());
    toast((trusted ? "Always allowed " + short(c.kind, 40) + " here. " : "") + DONE_TEXT[decision] + ": " + title, "ok");
    return true;
  } catch (e) {
    row.classList.remove("busy");
    setRowDisabled(row, stale);
    if (hadFocus && !stale && hadFocus.isConnected) hadFocus.focus();
    const okAt = okToasts.get(c.request_id);
    if (okAt !== undefined && Date.now() - okAt < 5000) return false; // keep the success message visible
    const why = e.status === 404 ? "Already answered or timed out" : short(e.message);
    toast((trusted ? "Always allow saved, but this card could not be released: " : ACTIONS[d] + " failed: ") + why, "err");
    return false;
  }
}

function moveLocally(c, decision) {
  if (!lastState) return;
  const s = Object.assign({}, lastState);
  const done = Object.assign({}, c, { decision, outcome: "decided", handler: "you" });
  delete done.deadline_in;
  for (const col of PENDING) s[col] = (s[col] || []).filter((x) => x.request_id !== c.request_id);
  const target = decision === "block" ? "declined" : "done";
  const already = (s[target] || []).some((x) => x && x.request_id === c.request_id && x.outcome === "decided");
  if (!already) s[target] = (s[target] || []).concat([done]); // a mid-flight poll may have placed it
  applyState(s, false); // the next poll re-syncs from the server
}

function toast(text, kind) {
  const t = $("#toast");
  t.textContent = text;
  t.className = "toast " + kind;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 5000);
}

// drag: pointer events (mouse and pen); touch users have the buttons

function pointerDown(ev, entry) {
  const li = entry.el;
  const row = $(".actions", li);
  if (ev.button !== 0 || ev.pointerType === "touch" || ev.target.closest("button, dd")) return;
  if (stale || !row || row.classList.contains("busy")) return;
  pend = { li, entry, x: ev.clientX, y: ev.clientY, id: ev.pointerId };
  li.setPointerCapture(ev.pointerId);
  li.addEventListener("pointermove", pointerMove);
  li.addEventListener("pointerup", pointerUp);
  li.addEventListener("pointercancel", cancelDrag);
}

function pointerMove(ev) {
  if (!pend) return;
  const dx = ev.clientX - pend.x;
  const dy = ev.clientY - pend.y;
  if (!drag && Math.hypot(dx, dy) > 5) beginDrag();
  if (!drag) return;
  drag.ghost.style.transform = "translate(" + dx + "px," + dy + "px) rotate(1.5deg)";
  const z = zoneAt(ev.clientX, ev.clientY);
  document.querySelectorAll(".zone").forEach((n) => n.classList.toggle("hot", n === z));
}

function beginDrag() {
  const entry = pend.entry;
  const r = pend.li.getBoundingClientRect();
  const ghost = pend.li.cloneNode(true);
  ghost.querySelectorAll("[id]").forEach((n) => n.removeAttribute("id"));
  ghost.classList.add("ghost");
  ghost.setAttribute("aria-hidden", "true");
  ghost.style.left = r.left + "px";
  ghost.style.top = r.top + "px";
  ghost.style.width = r.width + "px";
  document.body.append(ghost);
  pend.li.classList.add("placeholder");
  const ok = zonesFor(entry.c);
  document.querySelectorAll(".zone").forEach((n) => { n.hidden = !ok.includes(n.dataset.decision); });
  $("#zones").classList.add("show");
  document.body.classList.add("dragging");
  drag = { entry, ghost, li: pend.li };
}

function zoneAt(x, y) {
  for (const n of document.elementsFromPoint(x, y)) {
    const z = n.closest(".zone");
    if (z && !z.hidden) return z;
  }
  return null;
}

function pointerUp(ev) {
  const d = drag;
  const z = d ? zoneAt(ev.clientX, ev.clientY) : null;
  cancelDrag();
  if (d && z) decideCard(d.entry, z.dataset.decision);
}

function cancelDrag() {
  if (pend) {
    pend.li.removeEventListener("pointermove", pointerMove);
    pend.li.removeEventListener("pointerup", pointerUp);
    pend.li.removeEventListener("pointercancel", cancelDrag);
    try { pend.li.releasePointerCapture(pend.id); } catch (e) { /* already released */ }
  }
  if (drag) {
    drag.ghost.remove();
    drag.li.classList.remove("placeholder");
    $("#zones").classList.remove("show");
    document.querySelectorAll(".zone").forEach((n) => n.classList.remove("hot"));
    document.body.classList.remove("dragging");
  }
  drag = null;
  pend = null;
}

// start

function init() {
  // a fresh board URL pasted into this tab only changes the fragment: start over with the new key
  window.addEventListener("hashchange", () => { if (location.hash.length > 1) location.reload(); });
  token = readToken();
  if (!token) return showNotice(false);
  $("#board").hidden = false;
  $("#tabs").hidden = false;
  selectTab($("#tab-live"), false);
  document.querySelectorAll('[role="tab"]').forEach((t) => t.addEventListener("click", () => selectTab(t, false)));
  $("#tabs").addEventListener("keydown", tabKeys);
  $("#replay-load").addEventListener("click", loadReplay);
  fillCwds({}); // until the first poll: no folders, so Replay explains why
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && drag) cancelDrag(); });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) { beat(); poll(); }
  });
  poll();
  beat();
  timers = [setInterval(poll, 1000), setInterval(beat, 2000), setInterval(tick, 1000)];
}

init();
