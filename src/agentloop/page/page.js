// The page: this machine's loops, started, stopped and killed; their settings, the bundles' and
// the machine's; the workers; and each loop's output as it comes, in the script's colours.
//
// Plain JavaScript, no framework and no build step. Every text from the server goes in through
// textContent, never as markup. Every write sends the version of the file it read, and a write
// refused because the file changed since (409) reads it again.

"use strict";

// What each run setting does, in a line (docs/settings.md, "Run settings").
const SETTINGS = {
  routes: "The model and effort a session gets: a triage, and a build by its item's complexity.",
  escalation: "The route a build moves to once its route's model has had its go at the item.",
  buildAfterTriage: "The complexities a triage that leaves its item ready goes straight on to build, in the same session.",
  buildInTriageUpTo: "A triage goes on to build an item of this many points or fewer, whatever its complexity.",
  trial: "Until a date, builds of one complexity up to some points go on another route, to try it.",
  maxIterations: "How many items a run works, at most.",
  stopAtWeeklyUse: "Stop between items once this share of the week's allowance is used; 1 never stops early.",
  askEveryMinutes: "While a usage limit is waited out, how often to ask whether it has lifted sooner.",
  chainUpTo: "How many items one session may work, going on to the one it asks for.",
  repeatUpTo: "Stop once sessions on one model have left an item at the same stage this many times.",
  chainContextUpTo: "A session goes on to another item only while its context is under this many tokens.",
  model: "Every session on this model, at its default effort, whatever its route.",
  push: "Pull before each session and push its commit after it; a conflict is given to the session to resolve.",
  pushTries: "How many times a push refused because another loop pushed first is replayed and tried again.",
  autoStart: "When the queue is empty, wait for work rather than end; the Auto button in the loop's row sets this.",
  autoStartMinutes: "While it waits for work, how often to look for an item.",
  acceptDefinition: "When the project's definition changes: auto goes on with it at once, ask runs no more sessions until the change is accepted.",
};
const COMPLEXITIES = ["trivial", "low", "medium", "high", "unassigned"];
const ROUTE_KEYS = ["triage", ...COMPLEXITIES];
const INTEGERS = ["buildInTriageUpTo", "maxIterations", "chainUpTo", "repeatUpTo", "chainContextUpTo", "pushTries"];
// The SGR codes colours.py writes, by the class the page draws each in.
const SGR = {
  30: "black", 31: "darkred", 32: "darkgreen", 33: "darkyellow", 34: "darkblue", 35: "darkmagenta", 36: "darkcyan", 37: "gray",
  90: "darkgray", 91: "red", 92: "green", 93: "yellow", 94: "blue", 95: "magenta", 96: "cyan", 97: "white",
};
// A pane keeps this many lines: a long run does not grow the page without end.
const MAX_LINES = 4000;
const POLL_MS = 5000;
const OUTPUT_MS = 500;
const PANE_MIN_WIDTH = 420;

const state = {
  loops: [], loopsVersion: null,
  settings: null, settingsVersion: null, appDefaults: null,
  bundles: [], bundleRaw: {},
  accounts: null, accountsAsked: null,
  panes: new Map(), active: null,
};

// Small helpers

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false || child === "") continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const $ = (id) => document.getElementById(id);
const copy = (value) => JSON.parse(JSON.stringify(value ?? null));

class ApiError extends Error {
  constructor(status, data) {
    super(data && data.error ? data.error : `The server answered ${status}.`);
    this.status = status;
    this.data = data || {};
  }
}

async function api(method, path, body) {
  const options = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const answer = await fetch(path, options);
  let data = null;
  try { data = await answer.json(); } catch (_) { data = null; }
  if (!answer.ok) throw new ApiError(answer.status, data);
  return data;
}

let messageTimer = null;
function say(text, error) {
  const el = $("message");
  el.textContent = text;
  el.classList.toggle("error", !!error);
  el.hidden = !text;
  clearTimeout(messageTimer);
  if (text && !error) messageTimer = setTimeout(() => { el.hidden = true; }, 6000);
}

function failed(error) {
  if (error instanceof ApiError && error.status === 401) {
    // The token changed under the page (the server started again with another): to the form.
    location.assign("/signin.html");
  } else {
    say(error.message || String(error), true);
  }
}

function money(value) {
  return "$" + (Number(value) || 0).toFixed(2);
}

function trackerText(tracker) {
  return tracker ? tracker.server : "";
}

function routeText(route) {
  return route ? `${route.model}/${route.effort}` : "";
}

// The run settings' layers, merged as config.effective_settings merges them: routes per key,
// every other key replaced.
function merged(...layers) {
  const out = {};
  for (const layer of layers) {
    for (const [key, value] of Object.entries(layer || {})) {
      if (key === "routes") out.routes = { ...(out.routes || {}), ...copy(value) };
      else out[key] = copy(value);
    }
  }
  return out;
}

function valueText(key, value) {
  if (value === null || value === undefined) return key === "trial" ? "none" : key === "model" ? "each route's own" : "";
  if (key === "routes") return ROUTE_KEYS.map((k) => `${k} ${routeText(value[k])}`).join(", ");
  if (key === "escalation") return routeText(value);
  if (key === "buildAfterTriage") return value.length ? value.join(", ") : "none";
  if (key === "push" || key === "autoStart") return value ? "on" : "off";
  if (key === "trial") return `until ${value.until}: ${value.complexity} up to ${value.upTo} points on ${routeText(value.route)}`;
  return String(value);
}

// Views

function showView() {
  const view = (location.hash || "#loops").slice(1);
  for (const name of ["loops", "machine", "bundles"]) {
    $(`view-${name}`).hidden = name !== view;
  }
  for (const a of document.querySelectorAll(".top nav a")) a.classList.toggle("active", a.dataset.view === view);
  if (view === "machine") renderMachine();
  if (view === "bundles") renderBundles();
}

// Reading what the server holds

async function readLoops() {
  const data = await api("GET", "/api/loops");
  state.loops = data.loops;
  state.loopsVersion = data.version;
  renderLoops();
}

function openAlive() {
  for (const loop of state.loops) {
    if (loop.status.alive && !state.panes.has(loop.name)) openPane(loop.name, false);
  }
}

async function loadLoops() {
  await readLoops();
  openAlive();
}

async function loadSettings() {
  const data = await api("GET", "/api/settings");
  state.settings = data.settings;
  state.settingsVersion = data.version;
  state.appDefaults = data.appDefaults;
}

async function loadBundles() {
  const data = await api("GET", "/api/bundles");
  state.bundles = data.bundles;
  const raws = await Promise.all(state.bundles.map((b) => api("GET", `/api/bundles/${encodeURIComponent(b.name)}`).catch(() => null)));
  state.bundleRaw = {};
  state.bundles.forEach((b, i) => { if (raws[i]) state.bundleRaw[b.name] = raws[i]; });
}

// claude answers for each account in a second or two, so the page asks once and keeps the answer
// until it is asked again.
function loadAccounts(again) {
  if (!state.accountsAsked || again) {
    state.accountsAsked = api("GET", "/api/accounts").then((data) => {
      state.accounts = data;
      if (!$("view-machine").hidden) renderMachine();
      renderLoops();
      return data;
    });
    state.accountsAsked.catch(() => { state.accountsAsked = null; });
  }
  return state.accountsAsked;
}

function workerOf(name) {
  return state.accounts ? state.accounts.workers.find((w) => w.name === name) : null;
}

// The loops

function renderLoops() {
  const body = $("loops");
  body.replaceChildren();
  $("no-loops").hidden = state.loops.length > 0;
  for (const loop of state.loops) {
    const s = loop.status;
    const item = s.item ? h("span", { title: s.title || "" }, `${s.kind || ""} ${s.item}`.trim(), s.title ? h("span", { class: "sub", text: s.title }) : null) : "";
    const stateCell = [h("span", { class: "state" }, h("span", { class: `dot ${s.state}` }), s.state)];
    if (s.until) stateCell.push(h("span", { class: "sub", text: s.state === "idle" ? `${s.watching ? "on any change, else " : ""}next look ${s.until}` : `until ${s.until}` }));
    if (s.state === "failed" && s.error) stateCell.push(h("span", { class: "problem", text: s.error }));
    if (s.busy) stateCell.push(h("span", { class: "sub", text: s.busy }));
    if (!s.alive && loop.missing && loop.missing.length) {
      stateCell.push(h("span", { class: "problem", text: `${loop.missing.length === 1 ? "one thing" : loop.missing.length + " things"} to set before it starts` }));
    }
    const worker = workerOf(loop.worker);
    const login = worker && worker.login.state !== "in" ? h("span", { class: "problem", text: worker.login.line }) : null;
    const canStart = !s.alive && !s.busy;
    const row = h("tr", {},
      h("td", { "data-label": "loop" }, h("strong", { text: loop.name }), loop.like ? h("span", { class: "sub", text: `copy of ${loop.like}` }) : null),
      // A loop on a local bundle is marked: projects are read from their trackers now, and a
      // bundle is for trying a brief out (item doc-read).
      h("td", { "data-label": "project" }, loop.project,
        loop.source === "bundle" ? h("span", { class: "chip", title: "Runs on a bundle folder on this PC, not on the definition beside its tracker", text: "local bundle" }) : null,
        // A loop that reaches its tracker on a server of its own, not its worker's (item loop-from-url).
        loop.server ? h("span", { class: "chip", title: "Its tracker is on this server, reached with the loop's own key file, not its worker's", text: loop.server }) : null,
        // A changed definition: waiting to be accepted, or used at once and not yet dismissed
        // (item doc-change).
        loop.definitionChange ? h("button", {
          type: "button", class: `chip link${loop.definitionChange.pending ? " warn" : ""}`,
          title: loop.definitionChange.pending ? "The definition changed; no session runs until the change is accepted" : "The definition changed, and the loop goes on with it",
          onclick: () => showDefinitionChange(loop.name),
        }, loop.definitionChange.pending ? "definition changed: accept?" : "definition changed") : null),
      h("td", { "data-label": "tracker", class: "mono", text: trackerText(loop.tracker) }),
      h("td", { "data-label": "state" }, stateCell),
      h("td", { "data-label": "item at work" }, item),
      h("td", { "data-label": "cost", text: s.alive || s.cost ? money(s.cost) : "" }),
      h("td", { "data-label": "worker" }, loop.worker, login),
      h("td", { class: "actions" },
        h("button", { type: "button", class: "small primary", "aria-label": `Start ${loop.name}`, disabled: !canStart, onclick: () => startLoop(loop.name) }, "Start"),
        h("button", { type: "button", class: "small", "aria-label": `Stop ${loop.name}`, disabled: !s.alive || s.state === "stopping", title: "Stop once the item at work is done", onclick: () => stopLoop(loop.name) }, "Stop"),
        h("button", { type: "button", class: "small danger", "aria-label": `Kill ${loop.name}`, disabled: !s.alive, title: "Stop at once, leaving the session's changes uncommitted", onclick: () => killLoop(loop.name) }, "Kill"),
        h("button", { type: "button", class: "small toggle", "aria-pressed": String(!!loop.effective.autoStart), "aria-label": `Auto-start ${loop.name}`, title: "On: wait for work when the queue is empty, looking every so often, rather than end", onclick: () => toggleAuto(loop.name, canStart) }, "Auto"),
        h("button", { type: "button", class: "small", "aria-label": `Output of ${loop.name}`, onclick: () => openPane(loop.name, true) }, "Output"),
        h("button", { type: "button", class: "small", "aria-label": `Edit ${loop.name}`, onclick: () => editLoop(loop.name) }, "Edit"),
        // A copy of a copy is a copy of its original: one level only.
        h("button", { type: "button", class: "small", "aria-label": `Copy ${loop.name}`, title: "A second loop on this project, in clones of its own", onclick: () => editLoop(null, null, loop.like || loop.name) }, "Copy"),
        h("button", { type: "button", class: "small danger", "aria-label": `Delete ${loop.name}`, disabled: s.alive, title: s.alive ? "Stop it first" : "", onclick: () => deleteLoop(loop.name) }, "Delete"),
      ),
    );
    body.append(row);
  }
}

// What changed in a loop's definition, with Accept for a change that waits, or Dismiss for one
// already in use.
function showDefinitionChange(name) {
  const loop = loopOf(name);
  if (!loop || !loop.definitionChange) return;
  const change = loop.definitionChange;
  const d = change.diff;
  const dialog = $("definition");
  $("definition-title").textContent = `${loop.project}'s definition changed`;
  const body = $("definition-body");
  body.replaceChildren(h("p", { text: change.pending
    ? `Read ${change.read_at}. ${loop.name} runs no more sessions until the change is accepted.`
    : `Read ${change.read_at}, and ${loop.name} went on with it (acceptDefinition is auto).` }));
  const list = [];
  for (const [part, what] of [["repos", "repository"], ["places", "place"]]) {
    for (const e of d[part].added) list.push(h("li", { class: "added" }, `added ${what} `, h("code", { text: e.name }), e.about ? ` - ${e.about}` : ""));
    for (const e of d[part].removed) list.push(h("li", { class: "removed" }, `removed ${what} `, h("code", { text: e.name })));
    for (const e of d[part].changed) list.push(h("li", {}, `${what} `, h("code", { text: e.name }), `: ${e.old || "(nothing said)"} → ${e.new || "(nothing said)"}`));
  }
  if (d.systems) {
    const said = (systems) => (systems ? systems.join(", ") : "every system");
    list.push(h("li", {}, `systems: ${said(d.systems.old)} → ${said(d.systems.new)}`));
  }
  for (const [k, v] of Object.entries(d.settings.added)) list.push(h("li", { class: "added" }, "added setting ", h("code", { text: k }), ` = ${JSON.stringify(v)}`));
  for (const [k, v] of Object.entries(d.settings.removed)) list.push(h("li", { class: "removed" }, "removed setting ", h("code", { text: k }), ` (was ${JSON.stringify(v)})`));
  for (const [k, v] of Object.entries(d.settings.changed)) list.push(h("li", {}, "setting ", h("code", { text: k }), `: ${JSON.stringify(v.old)} → ${JSON.stringify(v.new)}`));
  // The project's tools (item tools-fetch): a changed one's text diff comes after the list.
  const tools = d.tools || { added: [], removed: [], changed: [] };
  const systemsSaid = (systems) => (systems.length ? systems.join(", ") : "every system");
  for (const t of tools.added) list.push(h("li", { class: "added" }, "added tool ", h("code", { text: t.file }), t.systems.length ? ` (${systemsSaid(t.systems)})` : ""));
  for (const t of tools.removed) list.push(h("li", { class: "removed" }, "removed tool ", h("code", { text: t.file })));
  for (const t of tools.changed) {
    if (t.old_systems.join() !== t.new_systems.join()) list.push(h("li", {}, "tool ", h("code", { text: t.file }), ` systems: ${systemsSaid(t.old_systems)} → ${systemsSaid(t.new_systems)}`));
    if (t.contents) list.push(h("li", {}, "changed tool ", h("code", { text: t.file }), t.lines ? "" : " (not text, or not both fetched here)"));
  }
  if (list.length) body.append(h("ul", { class: "changes" }, list));
  for (const t of tools.changed.filter((t) => t.lines && t.lines.length)) {
    body.append(h("p", {}, "The tool ", h("code", { text: t.file }), ":"),
      h("pre", { class: "mono diff" }, t.lines.map((line) => h("span", {
        class: line.startsWith("+") ? "added" : line.startsWith("-") ? "removed" : line.startsWith("@@") ? "hunk" : "",
        text: line + "\n",
      }))));
  }
  if (d.description.length) {
    body.append(h("p", { text: "The description:" }),
      h("pre", { class: "mono diff" }, d.description.map((line) => h("span", {
        class: line.startsWith("+") ? "added" : line.startsWith("-") ? "removed" : line.startsWith("@@") ? "hunk" : "",
        text: line + "\n",
      }))));
  }
  if (loop.missing && loop.missing.length) {
    body.append(h("p", { class: "problem", text: "It names folders this loop has no path for: give them with Edit, or no session runs after it is accepted." }));
  }
  $("definition-buttons").replaceChildren(h("span", {},
    h("button", { type: "button", onclick: () => dialog.close() }, change.pending ? "Not now" : "Close"),
    loop.missing && loop.missing.length ? h("button", { type: "button", onclick: () => { dialog.close(); editLoop(loop.name, loop.missing); } }, "Set them") : null,
    h("button", { type: "button", class: "primary", onclick: async () => {
      try {
        const fresh = await api("POST", `/api/loops/${encodeURIComponent(name)}/accept-definition`, {});
        Object.assign(loop, fresh);
        renderLoops();
        dialog.close();
        say(change.pending ? `${name} goes on with the changed definition.` : "Dismissed.");
      } catch (error) {
        failed(error);
      }
    } }, change.pending ? "Accept" : "Dismiss"),
  ));
  dialog.showModal();
}

function loopOf(name) {
  return state.loops.find((l) => l.name === name);
}

function setStatus(name, status) {
  const loop = loopOf(name);
  if (loop) {
    loop.status = status;
    renderLoops();
  }
  const pane = state.panes.get(name);
  if (pane) pane.setStatus(status);
}

async function startLoop(name) {
  try {
    const [fresh] = await Promise.all([api("GET", `/api/loops/${encodeURIComponent(name)}`), loadAccounts(false).catch(() => null)]);
    const worker = workerOf(fresh.worker);
    const out = worker && (worker.login.state === "out" || worker.login.state === "no-folder");
    const unknown = worker && worker.login.state === "unknown";
    if (fresh.missing.length || out || unknown) {
      beforeStart(fresh, worker, { blocked: fresh.missing.length > 0 || out });
      return;
    }
    await reallyStart(name);
  } catch (error) {
    failed(error);
  }
}

async function reallyStart(name) {
  try {
    const status = await api("POST", `/api/loops/${encodeURIComponent(name)}/start`, {});
    setStatus(name, status);
    openPane(name, true);
    say(`${name} started.`);
  } catch (error) {
    failed(error);
  }
}

// Asks for what a start needs first: the repositories the loop has not placed, a worker that is not
// logged in.
function beforeStart(loop, worker, { blocked }) {
  const dialog = $("before-start");
  $("before-title").textContent = `Before ${loop.name} can start`;
  const body = $("before-body");
  body.replaceChildren();
  if (loop.missing.length) {
    body.append(h("p", { text: "The loop is missing:" }),
      h("ul", {}, loop.missing.map((m) => h("li", {}, h("code", { text: m.field }), " ", m.message))));
  }
  if (worker && worker.login.state !== "in") {
    body.append(h("p", {}, `Its worker ${worker.name} is `, h("strong", { text: worker.login.line }),
      worker.login.state === "unknown" ? "; claude could not say. The first session will say if it cannot start." : ". Log in to the account once, in a terminal:"));
    if (worker.login.state !== "unknown") body.append(h("pre", { class: "mono", text: worker.loginCommand }));
  }
  const buttons = $("before-buttons");
  buttons.replaceChildren(h("span", {},
    h("button", { type: "button", onclick: () => dialog.close() }, "Cancel"),
    loop.missing.length ? h("button", { type: "button", onclick: () => { dialog.close(); editLoop(loop.name, loop.missing); } }, "Set them") : null,
    worker && worker.login.state !== "in" ? h("button", { type: "button", onclick: async () => {
      dialog.close();
      await loadAccounts(true).catch(failed);
      startLoop(loop.name);
    } }, "Ask again") : null,
    blocked ? null : h("button", { type: "button", class: "primary", onclick: () => { dialog.close(); reallyStart(loop.name); } }, "Start anyway"),
  ));
  dialog.showModal();
}

// Turned on for a loop that is not running, it starts it too: nothing else would ever look for work.
async function toggleAuto(name, canStart) {
  const on = !loopOf(name).effective.autoStart;
  try {
    await api("POST", `/api/loops/${encodeURIComponent(name)}/auto-start`, { on });
    await readLoops();
    say(`Auto-start is ${on ? "on" : "off"} for ${name}.`);
    if (on && canStart) await startLoop(name);
  } catch (error) {
    failed(error);
  }
}

async function stopLoop(name) {
  try {
    setStatus(name, await api("POST", `/api/loops/${encodeURIComponent(name)}/stop`, {}));
    say(`${name} stops once the item at work is done.`);
  } catch (error) {
    failed(error);
  }
}

async function killLoop(name) {
  if (!confirm(`Kill ${name} now? The session at work is ended, and its changes are left uncommitted.`)) return;
  try {
    setStatus(name, await api("POST", `/api/loops/${encodeURIComponent(name)}/kill`, {}));
  } catch (error) {
    failed(error);
  }
}

async function deleteLoop(name) {
  if (!confirm(`Delete the loop ${name} from this machine's settings? Its project's bundle and repositories are left as they are.`)) return;
  try {
    await api("DELETE", `/api/loops/${encodeURIComponent(name)}`, { version: state.loopsVersion });
    closePane(name);
    await loadLoops();
  } catch (error) {
    if (error.status === 409) await loadLoops().catch(() => {});
    failed(error);
  }
}

// The output panes

class Pane {
  constructor(name) {
    this.name = name;
    this.follow = true;
    this.count = 0;
    this.dot = h("span", { class: "dot" });
    this.tab = h("button", { type: "button", class: "tab", role: "tab", onclick: () => activate(name) }, this.dot, name);
    this.usage = h("span", { class: "usage" });
    this.lines = h("pre", { class: "lines", tabindex: "0" });
    this.jump = h("button", { type: "button", class: "small jump", hidden: true, onclick: () => this.toEnd() }, "↓ to the end");
    this.el = h("section", { class: "pane", "aria-label": `${name}'s output` },
      h("header", {}, h("span", { class: "name" }, this.dot.cloneNode(), name), this.usage,
        h("button", { type: "button", class: "small", title: "Close the pane; the loop goes on", onclick: () => closePane(name) }, "Close")),
      h("div", { class: "body" }, this.lines, this.jump));
    this.headDot = this.el.querySelector("header .dot");
    // Followed until the reader scrolls up, and again once they are back at the end.
    this.lines.addEventListener("scroll", () => {
      const atEnd = this.lines.scrollTop + this.lines.clientHeight >= this.lines.scrollHeight - 4;
      this.follow = atEnd;
      this.jump.hidden = atEnd;
    });
    // Where the output has been read to, which the server goes on from; none, after the page is
    // loaded again, is given the run's output from the top.
    this.lastId = null;
    this.lastStatus = null;
  }

  // What the server answered for this pane (`followOutput`).
  take(answer) {
    if (answer.reset) this.clear();
    // The server's text ends in a newline, which is no line of its own.
    if (answer.text) this.add(answer.text.slice(0, -1));
    this.lastId = answer.id;
    const status = JSON.stringify(answer.status);
    if (status !== this.lastStatus) {
      this.lastStatus = status;
      setStatus(this.name, answer.status);
    }
  }

  close() {
    this.tab.remove();
    this.el.remove();
  }

  clear() {
    this.lines.replaceChildren();
    this.count = 0;
    this.usage.textContent = "";
  }

  setStatus(status) {
    for (const dot of [this.dot, this.headDot]) dot.className = `dot ${status.state}`;
    if (status.usage) this.usage.textContent = status.usage;
  }

  add(text) {
    const fragment = document.createDocumentFragment();
    for (const line of text.split("\n")) {
      const row = drawLine(line);
      if (row.usage) this.usage.textContent = row.usage;
      fragment.append(row.el);
      this.count++;
    }
    this.lines.append(fragment);
    while (this.count > MAX_LINES) {
      this.lines.firstChild.remove();
      this.count--;
    }
    if (this.follow) this.toEnd();
  }

  toEnd() {
    this.lines.scrollTop = this.lines.scrollHeight;
    this.follow = true;
    this.jump.hidden = true;
  }
}

// One line of the output log: its text, in the colours its SGR codes give, and any other escape
// left out. The usage line, dark cyan, is also shown above the pane.
function drawLine(line) {
  const el = h("div");
  let colour = null;
  let usage = null;
  const parts = line.split(/\x1b\[([0-9;]*)m/);
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 1) {
      for (const code of parts[i].split(";")) {
        const n = Number(code || 0);
        if (n === 0 || n === 39) colour = null;
        else if (SGR[n]) colour = SGR[n];
      }
      continue;
    }
    // Any escape that is not a colour, which the loop never writes, is not drawn either.
    const text = parts[i].replace(/\x1b\[[0-9;?]*[A-Za-z]/g, "").replace(/[\x00-\x08\x0b-\x1f\x7f]/g, "");
    if (!text) continue;
    if (colour === "darkcyan" && /^\s*Usage on /.test(text)) usage = text.trim();
    el.append(colour ? h("span", { class: `c-${colour}`, text }) : document.createTextNode(text));
  }
  return { el, usage };
}

function openPane(name, focus) {
  let pane = state.panes.get(name);
  if (!pane) {
    pane = new Pane(name);
    state.panes.set(name, pane);
    $("tabs").append(pane.tab);
    $("panes").append(pane.el);
    const loop = loopOf(name);
    if (loop) pane.setStatus(loop.status);
    arrange();
    savePanes();
  }
  if (focus || !state.active) activate(name);
  $("no-panes").hidden = true;
}

function closePane(name) {
  const pane = state.panes.get(name);
  if (!pane) return;
  pane.close();
  state.panes.delete(name);
  if (state.active === name) {
    state.active = null;
    const next = state.panes.keys().next();
    if (!next.done) activate(next.value);
  }
  $("no-panes").hidden = state.panes.size > 0;
  arrange();
  savePanes();
}

function activate(name) {
  state.active = name;
  for (const [key, pane] of state.panes) {
    pane.tab.classList.toggle("active", key === name);
    pane.tab.setAttribute("aria-selected", key === name ? "true" : "false");
    pane.el.classList.toggle("active", key === name);
    if (key === name && pane.follow) requestAnimationFrame(() => pane.toEnd());
  }
  savePanes();
}

// Which panes were open, which in front, and how they were laid out, kept for the next load.
function savePanes() {
  try {
    localStorage.setItem("agentloop.panes", JSON.stringify({
      open: [...state.panes.keys()], active: state.active, side: $("side-by-side").checked,
    }));
  } catch (_) { /* A browser that keeps nothing keeps nothing. */ }
}

function restorePanes() {
  let kept = null;
  try { kept = JSON.parse(localStorage.getItem("agentloop.panes") || "null"); } catch (_) { kept = null; }
  if (!kept) return;
  $("side-by-side").checked = !!kept.side;
  layout();
  for (const name of kept.open || []) if (loopOf(name)) openPane(name, false);
  if (kept.active && state.panes.has(kept.active)) activate(kept.active);
}

function layout() {
  const side = $("side-by-side").checked;
  $("panes").classList.toggle("side", side);
  $("tabs").classList.toggle("side", side);
  arrange();
  // A pane that was hidden behind its tab could not be scrolled; one that follows is taken to its
  // end now that it shows.
  followToEnd();
}

function followToEnd() {
  requestAnimationFrame(() => {
    for (const pane of state.panes.values()) if (pane.follow) pane.toEnd();
  });
}

// How many panes go in each row, first row first: one row up to four, then two rows with the
// larger first, and never more across than `fit`, so a narrow window wraps instead of squashing.
// Rows are shared out evenly: rows of a window too narrow, or of more than eight panes, differ by
// one at most.
function paneRows(count, fit) {
  const across = Math.min(count <= 4 ? count : Math.ceil(count / 2), 4, fit);
  const rows = [];
  for (let left = count; left > 0; left -= rows[rows.length - 1]) {
    rows.push(Math.ceil(left / Math.ceil(left / across)));
  }
  return rows;
}

// Sets each pane's span of the twelve columns of `.panes.side`.
function arrange() {
  const panes = $("panes");
  const gap = parseFloat(getComputedStyle(panes).columnGap) || 0;
  const fit = Math.max(1, Math.floor((panes.clientWidth + gap) / (PANE_MIN_WIDTH + gap)));
  const rows = paneRows(state.panes.size, fit);
  const spans = rows.flatMap((across) => Array(across).fill(12 / across));
  [...state.panes.values()].forEach((pane, i) => pane.el.style.setProperty("--span", spans[i]));
  panes.classList.toggle("rows", rows.length > 1);
}

// The run settings, edited a layer at a time: each key either set in this layer or left to the
// layers beneath, whose value and source it shows.

function settingsEditor(own, beneath) {
  const rows = [];
  const container = h("div");
  for (const key of Object.keys(SETTINGS)) {
    const isSet = Object.prototype.hasOwnProperty.call(own || {}, key);
    const row = settingRow(key, isSet ? own[key] : undefined, beneath[key]);
    rows.push(row);
    container.append(row.el);
  }
  return {
    el: container,
    collect() {
      const out = {};
      for (const row of rows) {
        const value = row.collect();
        if (value !== undefined) out[row.key] = value;
      }
      return out;
    },
    mark(field) {
      for (const row of rows) row.mark(field);
    },
  };
}

function settingRow(key, value, beneath) {
  const here = h("input", { type: "checkbox" });
  const from = h("span", { class: "from" });
  const control = h("div", { class: "control" });
  const problem = h("span", { class: "problem", hidden: true });
  const el = h("div", { class: "field", "data-field": key },
    h("div", { class: "label" }, key),
    h("div", { class: "control" }, h("label", { class: "group" }, here, "set here"), from),
    control,
    h("div", { class: "what", text: SETTINGS[key] }),
    problem);
  const shown = value !== undefined ? value : beneath.value;
  let collect;
  let inputs = [];

  if (key === "routes") {
    const pairs = {};
    for (const name of ROUTE_KEYS) {
      const route = (value && value[name]) || (beneath.value && beneath.value[name]) || {};
      const model = h("input", { class: "short", value: route.model || "", "aria-label": `${name} model` });
      const effort = h("input", { class: "short", value: route.effort || "", "aria-label": `${name} effort` });
      pairs[name] = { model, effort, set: !!(value && value[name]) };
      inputs.push(model, effort);
      control.append(h("span", { class: "group" }, h("span", { class: "sub", text: name }), model, effort));
    }
    // A layer's routes replace only the routes it names, so only those that differ from beneath
    // are written.
    collect = () => {
      const out = {};
      for (const [name, pair] of Object.entries(pairs)) {
        const route = { model: pair.model.value.trim(), effort: pair.effort.value.trim() };
        const under = (beneath.value || {})[name] || {};
        if (pair.set || route.model !== under.model || route.effort !== under.effort) out[name] = route;
      }
      return out;
    };
  } else if (key === "escalation") {
    const model = h("input", { class: "short", value: (shown || {}).model || "", "aria-label": "model" });
    const effort = h("input", { class: "short", value: (shown || {}).effort || "", "aria-label": "effort" });
    inputs = [model, effort];
    control.append(h("span", { class: "group" }, "model", model, "effort", effort));
    collect = () => ({ model: model.value.trim(), effort: effort.value.trim() });
  } else if (key === "buildAfterTriage") {
    const boxes = COMPLEXITIES.map((c) => {
      const box = h("input", { type: "checkbox", value: c, checked: (shown || []).includes(c) });
      inputs.push(box);
      control.append(h("label", { class: "group" }, box, c));
      return box;
    });
    collect = () => boxes.filter((b) => b.checked).map((b) => b.value);
  } else if (key === "trial") {
    const t = shown || { until: "", complexity: "medium", upTo: 3, route: { model: "", effort: "" } };
    const none = h("input", { type: "checkbox", checked: shown === null });
    const until = h("input", { type: "date", value: t.until || "" });
    const complexity = h("select", {}, COMPLEXITIES.map((c) => h("option", { value: c, selected: c === t.complexity }, c)));
    const upTo = h("input", { type: "number", min: "0", step: "1", class: "short", value: t.upTo ?? "" });
    const model = h("input", { class: "short", value: (t.route || {}).model || "", "aria-label": "model" });
    const effort = h("input", { class: "short", value: (t.route || {}).effort || "", "aria-label": "effort" });
    inputs = [none, until, complexity, upTo, model, effort];
    control.append(h("label", { class: "group" }, none, "none"),
      h("span", { class: "group" }, "until", until), h("span", { class: "group" }, complexity, "up to", upTo, "points"),
      h("span", { class: "group" }, "on", model, effort));
    collect = () => none.checked ? null : {
      until: until.value, complexity: complexity.value, upTo: Number(upTo.value), route: { model: model.value.trim(), effort: effort.value.trim() },
    };
  } else if (key === "push" || key === "autoStart") {
    const box = h("input", { type: "checkbox", checked: !!shown });
    inputs = [box];
    control.append(h("label", { class: "group" }, box, key === "push" ? "push" : "on"));
    collect = () => box.checked;
  } else if (key === "acceptDefinition") {
    const select = h("select", {}, ["auto", "ask"].map((v) => h("option", { value: v, selected: v === (shown || "auto") }, v)));
    inputs = [select];
    control.append(select);
    collect = () => select.value;
  } else if (key === "model") {
    const input = h("input", { value: shown || "", placeholder: "none: each route's own" });
    inputs = [input];
    control.append(input);
    collect = () => input.value.trim() || null;
  } else {
    const integer = INTEGERS.includes(key);
    const input = h("input", { type: "number", class: "short", min: integer ? "0" : "0.01", step: integer ? "1" : "any", value: shown ?? "" });
    inputs = [input];
    control.append(input);
    // Left empty, the key is left to the layers beneath, as if it were not set here.
    collect = () => (input.value.trim() === "" ? undefined : Number(input.value));
  }

  const sync = () => {
    for (const input of inputs) input.disabled = !here.checked;
    from.textContent = here.checked ? "" : `from ${beneath.from}: ${valueText(key, beneath.value)}`;
  };
  here.checked = value !== undefined;
  here.addEventListener("change", sync);
  sync();
  return {
    key, el,
    collect: () => (here.checked ? collect() : undefined),
    mark(field) {
      const wrong = field === key || field.startsWith(key + ".") || field.endsWith("." + key) || field.includes("." + key + ".");
      el.classList.toggle("wrong", wrong);
      for (const input of inputs) input.classList.toggle("wrong", wrong);
    },
  };
}

// What lies beneath a layer: each key's value and the layer it comes from.
function beneathOf(layers) {
  const out = {};
  for (const key of Object.keys(SETTINGS)) {
    let value;
    let from = "the app";
    for (const [name, layer] of layers) {
      if (layer && Object.prototype.hasOwnProperty.call(layer, key)) {
        value = key === "routes" ? { ...(value || {}), ...copy(layer[key]) } : copy(layer[key]);
        from = name;
      }
    }
    out[key] = { value, from };
  }
  return out;
}

function field(label, what, control, name) {
  return h("div", { class: "field", "data-field": name || "" },
    h("div", { class: "label" }, label),
    h("div", { class: "control" }, control),
    what ? h("div", { class: "what", text: what }) : null);
}

// The editor dialog: one form at a time, saved by `save`, which may throw an ApiError whose field
// is marked.
function openEditor(title, body, save, problems) {
  const dialog = $("editor");
  $("editor-title").textContent = title;
  $("editor-body").replaceChildren(body.el);
  const shown = $("editor-problems");
  shown.textContent = problems && problems.length ? problems.map((p) => `${p.field}: ${p.message}`).join("\n") : "";
  shown.hidden = !shown.textContent;
  for (const p of problems || []) body.mark(p.field);
  const form = $("editor-form");
  form.onsubmit = async (event) => {
    event.preventDefault();
    try {
      await save();
      dialog.close();
    } catch (error) {
      shown.textContent = error.message;
      shown.hidden = false;
      // Every field a refusal names, when it names several (a loop added from a tracker).
      if (error instanceof ApiError && Array.isArray(error.data.problems)) for (const p of error.data.problems) body.mark(p.field);
      else if (error instanceof ApiError && error.data.field) body.mark(error.data.field);
    }
  };
  $("editor-cancel").onclick = () => dialog.close();
  dialog.showModal();
}

function markInputs(root, field) {
  for (const el of root.querySelectorAll("[data-field]")) {
    const name = el.dataset.field;
    const wrong = !!name && (field === name || field.endsWith("." + name) || field.includes(name + "."));
    for (const input of el.querySelectorAll("input, select")) input.classList.toggle("wrong", wrong);
  }
}

// A loop's settings: project, worker, where each repository and place is, and its run settings.
// A copy of a loop (`like`, made with `copyOf`) shows what it takes from that loop greyed, and
// keeps only what is set here.
async function editLoop(name, problems, copyOf) {
  try {
    await Promise.all([loadSettings(), loadBundles(), loadAccounts(false).catch(() => null)]);
  } catch (error) {
    failed(error);
    return;
  }
  const loops = state.settings.loops || [];
  const raw = name ? copy(loops.find((l) => l.name === name)) : copyOf ? { name: "", like: copyOf, repos: {} } : {
    name: "", project: state.bundles[0] ? state.bundles[0].name : "", worker: (state.settings.workers || [])[0]?.name || "",
    repos: {}, places: {}, settings: {},
  };
  if (!raw) {
    say(`No loop named ${name}.`, true);
    return;
  }
  if (raw.like) {
    editCopy(name, raw, loops.find((l) => l.name === raw.like), problems);
    return;
  }
  if (!name && state.accounts) {
    const fallback = state.accounts.workers.find((w) => w.default);
    if (fallback) raw.worker = fallback.name;
  }
  const root = h("div");
  let settingsPart = null;
  const nameInput = h("input", { value: raw.name, required: true, pattern: "[a-z0-9_-]+" });
  // A loop on a tracker keeps it: the form changes where its repositories are, not what it works.
  const shown = raw.tracker ? (state.loops || []).find((l) => l.name === name) : null;
  const project = raw.tracker
    ? h("select", { disabled: true }, h("option", { value: "" }, raw.tracker))
    : h("select", {}, state.bundles.map((b) => h("option", { value: b.name, selected: b.name === raw.project }, b.name)));
  const worker = h("select");
  const placesBox = h("div");
  const settingsBox = h("div");

  const draw = () => {
    const bundle = raw.tracker ? (shown && shown.definition) : state.bundles.find((b) => b.name === project.value);
    worker.replaceChildren(...(state.settings.workers || []).map((w) => h("option", {
      value: w.name, selected: w.name === raw.worker, disabled: !w.overseer,
    }, w.name + (!w.overseer ? " (no server)" : ""))));
    placesBox.replaceChildren();
    if (bundle) {
      placesBox.append(h("h3", { text: "Where each repository is on this machine" }));
      for (const repo of bundle.repos) {
        const input = h("input", { "data-repo": repo, value: raw.repos[repo] || "", placeholder: "a folder inside a git repository" });
        placesBox.append(field(repo, bundle.repos[0] === repo ? "Sessions run here." : "", input, `repos.${repo}`));
      }
      for (const place of bundle.places || []) {
        const input = h("input", { "data-place": place, value: (raw.places || {})[place] || "", placeholder: "a folder; never committed" });
        placesBox.append(field(place, "A folder the brief names that is not a repository.", input, `places.${place}`));
      }
    }
    const bundleSettings = raw.tracker ? null : ((state.bundleRaw[project.value] || {}).bundle || {}).settings;
    const beneath = beneathOf([["the app", state.appDefaults], ["this machine", state.settings.defaults], ["the bundle", bundleSettings]]);
    settingsPart = settingsEditor(raw.settings || {}, beneath);
    if (raw.tracker && !bundle) placesBox.append(h("p", { class: "sub", text: "Its repositories are named by the definition beside its tracker, read when the loop first starts." }));
    settingsBox.replaceChildren(h("h3", { text: "Run settings" }), settingsPart.el);
  };
  project.addEventListener("change", () => {
    raw.project = project.value;
    draw();
  });
  worker.addEventListener("change", () => { raw.worker = worker.value; });
  draw();
  root.append(
    field("name", "The page's name for the loop, unique on this machine: lower case, digits, - and _.", nameInput, "name"),
    field(raw.tracker ? "tracker" : "project", raw.tracker ? "The tracker it reads its project from." : "The bundle it works.", project, raw.tracker ? "tracker" : "project"),
    field("worker", "The Claude account it runs on, and the server and key it reaches the tracker with.", worker, "worker"),
    placesBox, settingsBox);

  const body = {
    el: root,
    mark(f) {
      markInputs(root, f);
      if (settingsPart) settingsPart.mark(f.replace(/^loops\[\d+\]\./, "").replace(/^settings\./, ""));
    },
  };
  const save = async () => {
    const loop = {
      ...raw, name: nameInput.value.trim(), worker: worker.value,
      repos: {}, places: {}, settings: settingsPart.collect(),
    };
    if (!raw.tracker) loop.project = project.value;
    for (const input of root.querySelectorAll("[data-repo]")) if (input.value.trim()) loop.repos[input.dataset.repo] = input.value.trim();
    for (const input of root.querySelectorAll("[data-place]")) if (input.value.trim()) loop.places[input.dataset.place] = input.value.trim();
    if (!Object.keys(loop.places).length) delete loop.places;
    await putLoop(name, loop);
  };
  openEditor(name ? `The loop ${name}` : "A new loop", body, save, problems);
}

async function putLoop(name, loop) {
  try {
    if (name) await api("PUT", `/api/loops/${encodeURIComponent(name)}`, { version: state.settingsVersion, loop });
    else await api("POST", "/api/loops", { version: state.settingsVersion, loop });
  } catch (error) {
    if (error.status === 409 && error.data.version) {
      await loadSettings();
      throw new ApiError(409, { error: "The settings were changed since they were read; they are read again now: look over the form and save again." });
    }
    throw error;
  }
  await loadLoops();
  say(`${loop.name} saved.`);
}

// A loop added from a tracker's address (item loop-from-url), in two steps on one form: the address,
// the key file for its server and the worker, read; then what the project's definition says it is,
// a path for each repository and place it names, the loop's name and its run settings. The server
// checks it whole before it is saved, and every field it refuses is marked.
async function addFromTracker() {
  try {
    await Promise.all([loadSettings(), loadAccounts(false).catch(() => null)]);
  } catch (error) {
    failed(error);
    return;
  }
  const workers = state.settings.workers || [];
  const fallback = state.accounts && state.accounts.workers.find((w) => w.default);
  let chosen = fallback ? fallback.name : (workers[0] || {}).name || "";
  const theirs = () => (workers.find((w) => w.name === chosen) || {}).overseer || null;
  const root = h("div");
  const addressInput = h("input", { placeholder: "https://overseer.example.com/doc/alice/projects/game.os", required: true });
  const keyInput = h("input", { placeholder: "the worker's, when left empty" });
  const worker = h("select", {}, workers.map((w) => h("option", { value: w.name, selected: w.name === chosen }, w.name)));
  const readButton = h("button", { type: "button", text: "Read" });
  const shownBox = h("div");
  let shown = null;
  let names = null;
  let settingsPart = null;
  const hintKey = () => {
    const o = theirs();
    keyInput.placeholder = o ? `the worker's: ${o.keyFile}` : "the file holding the agents' key for that server";
  };
  hintKey();

  const clearMarks = () => { for (const el of root.querySelectorAll(".wrong")) el.classList.remove("wrong"); };
  // Marks add up: a refusal names several fields at once.
  const mark = (f) => {
    for (const el of root.querySelectorAll("[data-field]")) {
      const name = el.dataset.field;
      if (name && (f === name || f.endsWith("." + name))) for (const input of el.querySelectorAll("input, select")) input.classList.add("wrong");
    }
  };
  const forget = () => {
    shown = null;
    shownBox.replaceChildren(h("p", { class: "sub", text: "Read the tracker to see what the project names." }));
  };

  const draw = () => {
    const s = shown.summary;
    const systems = shown.systems ? shown.systems.join(", ") : "every system";
    const parts = [
      h("h3", { text: `${shown.project}, on ${shown.server}` }),
      s.lead ? h("p", { class: "lead", text: s.lead }) : null,
      s.headings.length ? h("p", { class: "sub", text: `Its description: ${s.headings.join(" · ")}` }) : null,
      field("systems", shown.systemProblem ? `Not this one: ${shown.systemProblem}.` : `This machine runs ${shown.system}.`,
        h("span", { class: shown.systemProblem ? "problem" : "", text: systems }), "systems"),
    ];
    // The project's tools, those for this system fetched when the loop is added (item tools-fetch).
    const tools = shown.tools || [];
    if (tools.length) {
      const here = tools.filter((t) => t.here);
      parts.push(field("tools", here.length ? `Fetched to this machine, checked by hash: ${here.length} of ${tools.length}.` : "None is for this system.",
        h("span", {}, tools.map((t, i) => [i ? ", " : "", h("code", { class: t.here ? "" : "sub", text: t.file })]).flat()), "tools"));
    }
    parts.push(h("h3", { text: "Where each is on this machine" }));
    shown.repos.forEach((repo, index) => {
      const input = h("input", { "data-repo": repo.name, placeholder: "a folder inside a git repository" });
      const about = [index === 0 ? "Sessions run here." : "", repo.about || ""].filter(Boolean).join(" ");
      // Any repository's folder given moves the folders offered for those with an address.
      input.addEventListener("input", () => { delete input.dataset.offered; offerFolders(); });
      if (!repo.url) {
        parts.push(field(repo.name, about || "A repository the project names.", input, `repos.${repo.name}`));
        return;
      }
      // A repository the definition gives an address: its folder offered, and cloned into on
      // request (item clone). The folder offered follows the ones the person gives.
      input.dataset.offered = "1";
      const status = h("span", { class: "sub clone-status" });
      const button = h("button", { type: "button", text: "Clone here" });
      button.addEventListener("click", () => cloneInto(repo, input, button, status));
      const from = `Cloned from ${repo.url}${repo.branch ? `, branch ${repo.branch}` : ""}: Clone here clones it into an empty or new folder; a folder holding a clone of it is used as it is.`;
      parts.push(field(repo.name, [about && !/[.!?]$/.test(about) ? `${about}.` : about, from].filter(Boolean).join(" "),
        h("span", { class: "group clone" }, input, button, status), `repos.${repo.name}`));
    });
    for (const place of shown.places) {
      const input = h("input", { "data-place": place.name, placeholder: "a folder; never committed" });
      parts.push(field(place.name, place.about || "A folder the project names that is not a repository.", input, `places.${place.name}`));
    }
    names = h("input", { value: shown.project, required: true, pattern: "[a-z0-9_-]+" });
    parts.push(h("h3", { text: "The loop" }),
      field("name", "The page's name for the loop, unique on this machine: lower case, digits, - and _.", names, "name"));
    const beneath = beneathOf([["the app", state.appDefaults], ["this machine", state.settings.defaults], ["the project", shown.settings]]);
    settingsPart = settingsEditor({}, beneath);
    parts.push(h("h3", { text: "Run settings" }), settingsPart.el);
    // A part left out is null, which replaceChildren would write as text.
    shownBox.replaceChildren(...parts.filter(Boolean));
    offerFolders();
  };

  // The folder offered for a repository with an address: beside the first folder the person gave
  // another repository, else `<home>/<project>/<repo>` on this machine (item clone).
  const parentOf = (path) => {
    const trimmed = path.replace(/[\\/]+$/, "");
    const cut = Math.max(trimmed.lastIndexOf("/"), trimmed.lastIndexOf("\\"));
    return cut > 0 ? trimmed.slice(0, cut) : trimmed;
  };
  const offerFolders = () => {
    const inputs = [...root.querySelectorAll("[data-repo]")];
    const picked = inputs.find((i) => !i.dataset.offered && i.value.trim());
    const base = picked ? parentOf(picked.value.trim()) : `${shown.home}/${shown.project}`;
    for (const input of inputs) if (input.dataset.offered) input.value = `${base}/${input.dataset.repo}`;
  };
  const cloneInto = async (repo, input, button, status) => {
    clearMarks();
    const problems = $("editor-problems");
    button.disabled = true;
    status.className = "sub clone-status";
    status.textContent = "cloning…";
    try {
      const done = await api("POST", "/api/clone", { ...form(), repo: repo.name, folder: input.value.trim() });
      delete input.dataset.offered;
      status.textContent = done.cloned ? "cloned" : "a clone of it already, used as it is";
      problems.hidden = true;
    } catch (error) {
      status.className = "problem clone-status";
      status.textContent = "not cloned";
      problems.textContent = error.message;
      problems.hidden = false;
      if (error instanceof ApiError && error.data.field) mark(error.data.field);
    } finally {
      button.disabled = false;
    }
  };

  const form = () => ({ address: addressInput.value.trim(), keyFile: keyInput.value.trim() || null, worker: worker.value });
  const read = async () => {
    clearMarks();
    try {
      shown = await api("POST", "/api/definition", form());
    } catch (error) {
      forget();
      throw error;
    }
    draw();
  };
  readButton.addEventListener("click", async () => {
    const problems = $("editor-problems");
    readButton.disabled = true;
    try {
      await read();
      problems.hidden = true;
    } catch (error) {
      problems.textContent = error.message;
      problems.hidden = false;
      if (error instanceof ApiError && Array.isArray(error.data.problems)) for (const p of error.data.problems) mark(p.field);
      else if (error instanceof ApiError && error.data.field) mark(error.data.field);
    } finally {
      readButton.disabled = false;
    }
  });
  for (const input of [addressInput, keyInput]) input.addEventListener("input", () => { if (shown) forget(); });
  worker.addEventListener("change", () => {
    chosen = worker.value;
    hintKey();
    if (shown) forget();
  });
  forget();

  root.append(
    field("address", "Overseer's address of the tracker, as its page shows it - or its name on the worker's server.",
      h("span", { class: "group address" }, addressInput, readButton), "address"),
    field("key file", "The file holding the agents' key for that server. Its path is kept; the key is never read here.", keyInput, "keyFile"),
    field("worker", "The Claude account it runs on.", worker, "worker"),
    field("paste a key", "Or paste the key itself, for the address's server or else the worker's: it is checked with the server, kept in a file only you can read, and its path goes in key file.",
      pasteKey(keyInput, () => ({ address: addressInput.value.trim(), worker: worker.value })), "key"),
    shownBox);

  const body = { el: root, mark };
  const save = async () => {
    clearMarks();
    if (!shown) {
      await read();
      throw new Error("Read: now say where each repository and place is on this machine, and save again.");
    }
    const loop = { ...form(), name: names.value.trim(), repos: {}, places: {}, settings: settingsPart.collect(), version: state.settingsVersion };
    for (const input of root.querySelectorAll("[data-repo]")) loop.repos[input.dataset.repo] = input.value.trim();
    for (const input of root.querySelectorAll("[data-place]")) loop.places[input.dataset.place] = input.value.trim();
    try {
      await api("POST", "/api/loops/from-tracker", loop);
    } catch (error) {
      if (error.status === 409 && error.data.version) {
        await loadSettings();
        throw new ApiError(409, { error: "The settings were changed since they were read; they are read again now: save again." });
      }
      throw error;
    }
    await loadLoops();
    say(`${loop.name} added, on ${shown.tracker}.`);
  };
  openEditor("A loop from a tracker", body, save, []);
}

// A copy of a loop: its name and its repositories' folders asked for, everything else the
// original's unless set here.
function editCopy(name, raw, base, problems) {
  if (!base) {
    say(`No loop named ${raw.like}, which ${name || "the copy"} is a copy of.`, true);
    return;
  }
  const bundle = state.bundles.find((b) => b.name === base.project);
  const root = h("div");
  const nameInput = h("input", { value: raw.name, required: true, pattern: "[a-z0-9_-]+" });
  const worker = h("select", {},
    h("option", { value: "", selected: raw.worker === undefined }, `as ${base.name}: ${base.worker}`),
    (state.settings.workers || []).map((w) => h("option", {
      value: w.name, selected: w.name === raw.worker, disabled: !w.overseer,
    }, w.name + (!w.overseer ? " (no server)" : ""))));
  const greyed = () => worker.classList.toggle("copied", worker.value === "");
  worker.addEventListener("change", greyed);
  greyed();
  // A folder left empty is the original's, shown greyed in its place.
  const folder = (kind, key, own, theirs, what) =>
    h("input", { [`data-${kind}`]: key, value: own || "", placeholder: theirs ? `as ${base.name}: ${theirs}` : what });
  const placesBox = h("div");
  if (bundle) {
    placesBox.append(h("h3", { text: "Where each repository is on this machine" }),
      h("p", { class: "sub", text: `Each its own clone: two loops of ${base.project} may not commit in one repository.` }));
    for (const repo of bundle.repos) {
      const input = folder("repo", repo, (raw.repos || {})[repo], (base.repos || {})[repo], "a folder inside a git repository");
      placesBox.append(field(repo, bundle.repos[0] === repo ? "Sessions run here." : "", input, `repos.${repo}`));
    }
    for (const place of bundle.places || []) {
      const input = folder("place", place, (raw.places || {})[place], (base.places || {})[place], "a folder; never committed");
      placesBox.append(field(place, "A folder the brief names that is not a repository; left empty, the original's.", input, `places.${place}`));
    }
  }
  const bundleSettings = ((state.bundleRaw[base.project] || {}).bundle || {}).settings;
  const beneath = beneathOf([["the app", state.appDefaults], ["this machine", state.settings.defaults], ["the bundle", bundleSettings],
    [`the loop ${base.name}`, base.settings]]);
  const settingsPart = settingsEditor(raw.settings || {}, beneath);
  root.append(
    field("name", "The page's name for the loop, unique on this machine: lower case, digits, - and _.", nameInput, "name"),
    field("copy of", `What is not set here is ${base.name}'s, and follows it when it changes.`, h("strong", { text: base.name }), "like"),
    field("project", "The original's.", h("select", { disabled: true }, h("option", {}, base.project)), "project"),
    field("worker", "The Claude account it runs on: another one than the original's, to run on its usage.", worker, "worker"),
    placesBox, h("h3", { text: "Run settings" }), settingsPart.el);
  const body = {
    el: root,
    mark(f) {
      markInputs(root, f);
      settingsPart.mark(f.replace(/^loops\[\d+\]\./, "").replace(/^settings\./, ""));
    },
  };
  const save = async () => {
    // Only what the copy gives itself: the rest stays the original's.
    const loop = { ...raw, name: nameInput.value.trim(), like: base.name };
    delete loop.project;
    if (worker.value) loop.worker = worker.value;
    else delete loop.worker;
    const own = (kind) => {
      const out = {};
      for (const input of root.querySelectorAll(`[data-${kind}]`)) if (input.value.trim()) out[input.dataset[kind]] = input.value.trim();
      return out;
    };
    for (const [key, value] of [["repos", own("repo")], ["places", own("place")], ["settings", settingsPart.collect()]]) {
      if (Object.keys(value).length) loop[key] = value;
      else delete loop[key];
    }
    await putLoop(name, loop);
  };
  openEditor(name ? `The loop ${name}` : `A copy of ${base.name}`, body, save, problems);
}

$("add-loop").addEventListener("click", () => editLoop(null));
$("add-from-tracker").addEventListener("click", () => addFromTracker());

// This machine: its workers and its settings.

async function renderMachine() {
  try {
    await loadSettings();
  } catch (error) {
    failed(error);
    return;
  }
  drawWorkers();
  drawMachineSettings();
  loadAccounts(false).catch(failed);
}

// A worker's last usage, and when it was reported, so that an old line can be told from a new one.
function usageText(usage) {
  if (!usage || !usage.line) return "";
  // An API key's line is said without a report: it has no allowance to report.
  if (!usage.at) return usage.line;
  const at = new Date(usage.at * 1000);
  const today = at.toDateString() === new Date().toDateString();
  const when = at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return `${usage.line} (reported ${today ? when : `${at.toLocaleDateString([], { weekday: "short" })} ${when}`})`;
}

// The usage alone is read from the workers' files, not from claude, so it is asked for on each
// poll while this view is shown, and the lines are changed in place: redrawing the cards would
// lose a worker's edit form left open.
async function readUsage() {
  const data = await api("GET", "/api/usage");
  for (const [name, usage] of Object.entries(data.workers)) {
    const said = workerOf(name);
    if (said) said.usage = usage;
    for (const el of document.querySelectorAll("#workers .usage")) {
      if (el.dataset.worker === name) el.textContent = usageText(usage);
    }
  }
}

function drawWorkers() {
  const box = $("workers");
  box.replaceChildren();
  const found = $("found");
  found.replaceChildren();
  const accounts = state.accounts;
  for (const worker of state.settings.workers || []) {
    const said = accounts ? accounts.workers.find((w) => w.name === worker.name) : null;
    const overseer = worker.overseer || {};
    const login = said ? said.login.line : "asking claude...";
    box.append(h("div", { class: "card" },
      h("div", { class: "head" },
        h("strong", { text: worker.name }),
        h("span", {}, said && said.default ? h("span", { class: "sub", text: "the default for a new loop" }) :
          h("button", { type: "button", class: "small", onclick: () => setDefault(worker.name) }, "Make it the default"),
          " ", h("button", { type: "button", class: "small", onclick: () => editWorker(worker.name) }, "Edit"))),
      h("div", { class: "mono", text: worker.claude || "the default account (~/.claude)" }),
      said && said.signIn ? h("div", { class: "sub", text: said.signIn }) : null,
      h("div", {}, h("span", { class: said && said.login.state === "in" ? "" : "problem", text: login })),
      said && said.login.state !== "in" && said.loginCommand ? h("pre", { class: "mono", text: said.loginCommand }) : null,
      h("div", { class: "usage c-darkcyan mono", "data-worker": worker.name, text: usageText(said && said.usage) }),
      worker.manager ? h("div", { class: "sub" }, "manager ", h("span", { class: "mono", text: worker.manager }), ": its loops take their items first") : null,
      worker.overseer ? h("div", { class: "sub" }, "server ", h("span", { class: "mono", text: overseer.server || "" }),
        ", key file ", h("span", { class: "mono", text: overseer.keyFile || "" })) : h("div", { class: "problem", text: "no server: it cannot run a loop, as every tracker is on one" })));
  }
  if (accounts && accounts.found.length) {
    found.append(h("p", { class: "note", text: "Claude folders on this machine that no worker names yet:" }));
    for (const f of accounts.found) {
      found.append(h("div", { class: "card" }, h("div", { class: "head" },
        h("span", { class: "mono", text: f.claude || "the default account (~/.claude)" }),
        h("button", { type: "button", class: "small", onclick: () => addWorker(f.claude, f.name) }, `Add as ${f.name}`))));
    }
  }
}

async function addWorker(claude, name) {
  try {
    await api("POST", "/api/accounts", { claude: claude || null, name: name || undefined });
    await loadSettings();
    await loadAccounts(true);
    drawWorkers();
    say("Worker added.");
  } catch (error) {
    failed(error);
  }
}

$("add-worker").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  addWorker(form.claude.value.trim() || null, form.name.value.trim() || null).then(() => form.reset());
});

async function setDefault(name) {
  try {
    await api("POST", "/api/accounts/default", { worker: name });
    await loadSettings();
    await loadAccounts(true);
  } catch (error) {
    failed(error);
  }
}

// The settings file as a whole, changed in one place and written back from the version read.
async function saveSettings(change) {
  const raw = copy(state.settings);
  change(raw);
  try {
    const data = await api("PUT", "/api/settings", { version: state.settingsVersion, settings: raw });
    state.settings = data.settings;
    state.settingsVersion = data.version;
  } catch (error) {
    if (error.status === 409) {
      await loadSettings();
      throw new ApiError(409, { error: "The settings were changed since they were read; they are read again now: look it over and save again." });
    }
    throw error;
  }
}

// A key pasted in rather than a file made by hand (item paste-key): the server checks that it signs
// in, writes it to a file only this person can read, and answers the account and the path, which
// goes in the key-file field. The key is kept nowhere on the page: the field is emptied once saved.
// `target` gives what says which server it is for: {server}, or {address, worker}.
function pasteKey(keyFileInput, target) {
  const input = h("input", { type: "password", autocomplete: "off", spellcheck: "false", placeholder: "paste the key Overseer's page made" });
  const button = h("button", { type: "button", text: "Save key" });
  const said = h("span", { class: "sub" });
  button.addEventListener("click", async () => {
    button.disabled = true;
    input.classList.remove("wrong");
    try {
      const saved = await api("POST", "/api/keys", { key: input.value, ...target() });
      input.value = "";
      keyFileInput.value = saved.path;
      keyFileInput.dispatchEvent(new Event("input"));
      said.classList.remove("problem");
      said.textContent = `Signs in as ${saved.account}; kept in ${saved.path}, readable by you alone.`;
    } catch (error) {
      said.classList.add("problem");
      said.textContent = error.message;
      if (error instanceof ApiError && error.data.field === "key") input.classList.add("wrong");
    } finally {
      button.disabled = false;
    }
  });
  return h("span", { class: "group paste-key" }, input, button, said);
}

function editWorker(name) {
  const worker = (state.settings.workers || []).find((w) => w.name === name);
  if (!worker) return;
  const root = h("div");
  const claude = h("input", { value: worker.claude || "", placeholder: "empty for the default account" });
  const hasServer = h("input", { type: "checkbox", checked: !!worker.overseer });
  const server = h("input", { value: (worker.overseer || {}).server || "", placeholder: "https://..." });
  const keyFile = h("input", { value: (worker.overseer || {}).keyFile || "", placeholder: "~/.overseer/agent.key" });
  const manager = h("input", { value: worker.manager || "", placeholder: "empty for none" });
  // How it signs in to Claude: the folder's login, or a token or key from an environment variable
  // named here - the variable, never the secret.
  const signKind = worker.signIn ? Object.keys(worker.signIn)[0] : "";
  const signIn = h("select", {},
    h("option", { value: "", text: "the login in its Claude folder" }),
    h("option", { value: "token", text: "a token (claude setup-token)" }),
    h("option", { value: "apiKey", text: "an API key, billed per use" }));
  signIn.value = signKind;
  const signVariable = h("input", { class: "mono", value: signKind ? worker.signIn[signKind] : "",
    placeholder: "CLAUDE_CODE_OAUTH_TOKEN" });
  root.append(
    field("Claude folder", "The account's config folder, CLAUDE_CONFIG_DIR; empty for the default account.", claude, "claude"),
    field("a server", "The Overseer server the trackers are on, and the key its loops sign with.", h("label", { class: "group" }, hasServer, "this worker has one"), "overseer"),
    field("server", "The server's address.", server, "server"),
    field("key file", "Where the agents' key is kept: a path only, never the key.", keyFile, "keyFile"),
    field("paste a key", "Or paste the key itself: it is checked with the server, kept in a file only you can read, and its path goes above.",
      pasteKey(keyFile, () => ({ server: server.value.trim() })), "key"),
    field("manager", "The Overseer username whose items this worker's loops take first; empty for none.", manager, "manager"),
    field("signs in with", "How its sessions sign in to Claude. A token or key is read from an environment variable where agentloop runs, as in a container.", signIn, "signIn"),
    field("variable", "The name of the environment variable holding the token or key: never the secret itself.", signVariable, "signIn"));
  const body = { el: root, mark: (f) => markInputs(root, f) };
  openEditor(`The worker ${name}`, body, async () => {
    await saveSettings((raw) => {
      const w = raw.workers.find((x) => x.name === name);
      w.claude = claude.value.trim() || null;
      w.overseer = hasServer.checked ? { server: server.value.trim(), keyFile: keyFile.value.trim() } : null;
      w.manager = manager.value.trim() || null;
      if (signIn.value) w.signIn = { [signIn.value]: signVariable.value.trim() };
      else delete w.signIn;
    });
    await loadAccounts(true);
    drawWorkers();
    // A key file is never deleted with the setting that named it: say where it is left.
    const before = (worker.overseer || {}).keyFile;
    const left = before && (!hasServer.checked || keyFile.value.trim() !== before) ? ` The key file ${before} is left where it is.` : "";
    say(`${name} saved.${left}`);
  });
}

function drawMachineSettings() {
  const form = $("machine-settings");
  form.replaceChildren();
  const raw = state.settings;
  const port = h("input", { type: "number", class: "short", min: "0", max: "65535", value: (raw.server || {}).port ?? "", placeholder: "8765" });
  const install = h("input", { value: raw.install || "", placeholder: "not built yet" });
  const claude = h("input", { value: raw.claude || "", placeholder: "found on PATH" });
  const bundles = h("textarea", { rows: String(Math.max(2, (raw.bundles || []).length)), class: "mono", style: "flex: 1 1 30rem; max-width: 40rem" });
  bundles.value = (raw.bundles || []).join("\n");
  const defaults = settingsEditor(raw.defaults || {}, beneathOf([["the app", state.appDefaults]]));
  const problem = h("p", { class: "problems", hidden: true });
  form.append(
    field("page's port", "The port agentloop serve listens on, on 127.0.0.1; used from the next start of the server.", port, "server.port"),
    field("build installed at", "Where agentloop build puts the copy of the app that runs.", install, "install"),
    field("claude", "The claude to run, where it is not on PATH.", claude, "claude"),
    field("bundle folders", "The folders of the bundles this machine knows, one a line.", bundles, "bundles"),
    h("h3", { text: "Run settings for every loop here" }),
    h("p", { class: "note", text: "Beneath each bundle's and each loop's own." }),
    defaults.el, problem,
    h("div", { class: "buttons" }, h("button", { type: "submit", class: "primary" }, "Save")));
  form.onsubmit = async (event) => {
    event.preventDefault();
    problem.hidden = true;
    try {
      await saveSettings((r) => {
        const p = port.value.trim();
        if (p) r.server = { ...(r.server || {}), port: Number(p) };
        else if (r.server) delete r.server.port;
        r.install = install.value.trim() || null;
        r.claude = claude.value.trim() || null;
        // An Overseer library named here once means nothing now: the server hands out the template.
        delete r.overseer;
        r.bundles = bundles.value.split("\n").map((x) => x.trim()).filter(Boolean);
        r.defaults = defaults.collect();
      });
      say("This machine's settings saved.");
      drawMachineSettings();
    } catch (error) {
      problem.textContent = error.message;
      problem.hidden = false;
      if (error instanceof ApiError && error.data.field) {
        markInputs(form, error.data.field);
        defaults.mark(error.data.field.replace(/^defaults\./, ""));
      }
    }
  };
}

// The bundles

async function renderBundles() {
  try {
    await Promise.all([loadSettings(), loadBundles()]);
  } catch (error) {
    failed(error);
    return;
  }
  const box = $("bundles");
  box.replaceChildren();
  if (!state.bundles.length) box.append(h("p", { class: "note", text: "No bundles yet: add a bundle's folder in this machine's settings." }));
  for (const bundle of state.bundles) {
    box.append(h("div", { class: "card" },
      h("div", { class: "head" }, h("strong", { text: bundle.name }),
        h("button", { type: "button", class: "small", onclick: () => editBundle(bundle.name) }, "Edit")),
      h("div", { class: "mono sub", text: bundle.folder }),
      h("div", {}, "tracker ", h("span", { class: "mono", text: trackerText(bundle.tracker) })),
      h("div", { class: "sub", text: `repositories ${bundle.repos.join(", ")}${bundle.places.length ? "; places " + bundle.places.join(", ") : ""}; ${bundle.systems ? "runs on " + bundle.systems.join(", ") : "runs on every system"}` })));
  }
}

function editBundle(name) {
  const kept = state.bundleRaw[name];
  if (!kept) return;
  const raw = copy(kept.bundle);
  const root = h("div");
  const server = h("input", { value: (raw.tracker || {}).server || "", placeholder: "alice/projects/<name>.os" });
  const repos = h("input", { value: (raw.repos || []).join(", ") });
  const places = h("input", { value: (raw.places || []).join(", ") });
  const systems = ["windows", "macos", "linux"].map((s) => h("input", { type: "checkbox", value: s, checked: !raw.systems || raw.systems.includes(s) }));
  const beneath = beneathOf([["the app", state.appDefaults], ["this machine", state.settings.defaults]]);
  const settings = settingsEditor(raw.settings || {}, beneath);
  root.append(
    field("tracker", "The tracker's name on the Overseer server.", server, "tracker"),
    field("repositories", "The project's names for its repositories, comma separated; sessions run in the first.", repos, "repos"),
    field("places", "Folders the brief names that are not repositories, comma separated.", places, "places"),
    field("systems", "The systems the project runs on.", h("span", { class: "group" }, systems.map((box) => h("label", { class: "group" }, box, box.value))), "systems"),
    h("h3", { text: "Run settings for the project" }), settings.el);
  const list = (input) => input.value.split(",").map((x) => x.trim()).filter(Boolean);
  const body = {
    el: root,
    mark(f) {
      markInputs(root, f);
      settings.mark(f.replace(/^settings\./, ""));
    },
  };
  openEditor(`The bundle ${name}`, body, async () => {
    raw.tracker = { server: server.value.trim() };
    raw.repos = list(repos);
    if (list(places).length) raw.places = list(places);
    else delete raw.places;
    const chosen = systems.filter((b) => b.checked).map((b) => b.value);
    if (chosen.length === 3) delete raw.systems;
    else raw.systems = chosen;
    raw.settings = settings.collect();
    try {
      const data = await api("PUT", `/api/bundles/${encodeURIComponent(name)}`, { version: kept.version, bundle: raw });
      state.bundleRaw[name] = data;
    } catch (error) {
      if (error.status === 409) {
        await loadBundles();
        throw new ApiError(409, { error: "The bundle was changed since it was read; it is read again now: open it again." });
      }
      throw error;
    }
    say(`${name} saved.`);
    renderBundles();
    loadLoops().catch(failed);
  });
}

// Starting the page

$("side-by-side").addEventListener("change", () => {
  layout();
  savePanes();
});
window.addEventListener("hashchange", showView);

// The rows depend on the width, and the wrapped lines with it; a change of height alone, as the
// loop list grows, changes neither.
let panesWidth = 0;
new ResizeObserver(() => {
  const width = $("panes").clientWidth;
  if (width === panesWidth) return;
  panesWidth = width;
  arrange();
  followToEnd();
}).observe($("panes"));

async function poll() {
  if (!document.hidden) {
    try {
      await loadLoops();
      if (!$("view-machine").hidden) await readUsage();
    } catch (error) {
      failed(error);
    }
  }
  setTimeout(poll, POLL_MS);
}

// One request for all the panes, and the next once it is answered: a stream for each pane would
// take up the six connections a browser has for a host, and leave none for anything else. A request
// that failed is simply asked again.
async function followOutput() {
  const asked = [...state.panes.values()];
  if (asked.length && !document.hidden) {
    try {
      const data = await api("POST", "/api/output", { loops: Object.fromEntries(asked.map((p) => [p.name, p.lastId])) });
      for (const pane of asked) {
        // A pane closed, or closed and opened again, meanwhile begins from its own start.
        const answer = data.loops[pane.name];
        if (answer && state.panes.get(pane.name) === pane) pane.take(answer);
      }
    } catch (_) { /* The next round asks again. */ }
  }
  setTimeout(followOutput, OUTPUT_MS);
}

(async function start() {
  showView();
  try {
    // Opening a pane saves the panes, so what was kept is restored before any is opened.
    await readLoops();
    restorePanes();
    openAlive();
    loadSettings().catch(() => {});
    loadAccounts(false).catch(() => {});
  } catch (error) {
    failed(error);
  }
  setTimeout(poll, POLL_MS);
  followOutput();
})();
