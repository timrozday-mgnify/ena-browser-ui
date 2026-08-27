"use strict";

// ---------------------------------------------------------------------------
// Globals, the fetch helper, tabs and the two header toggles.
// ---------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);

const ENTITIES = [
  { id: "studies", label: "Studies" },
  { id: "samples", label: "Samples" },
  { id: "runs", label: "Reads" },
  { id: "experiments", label: "Experiments" },
  { id: "analyses", label: "Analyses" },
  { id: "files", label: "Files" },
];

let TEST = true;      // ENA test vs production
let WRITE = false;    // read-only vs read/write. Never restored from storage.
let ENTITY = "studies";
let HEALTH = {};

// Credentials live in the browser for this tab only (see creds.js) and ride
// along on every call as headers; the server holds no state.
let CREDS = { username: "", password: "" };

function webinHeaders() {
  const headers = { "Content-Type": "application/json", "X-Ena-Test": TEST ? "true" : "false" };
  if (CREDS.username && CREDS.password) {
    headers["X-Webin-Username"] = CREDS.username;
    headers["X-Webin-Password"] = CREDS.password;
  }
  return headers;
}

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: webinHeaders(), ...opts });
  const text = await res.text();
  let body;
  try { body = text ? JSON.parse(text) : {}; } catch { body = { detail: text }; }
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

function banner(kind, msg) {
  const el = $("banner");
  el.className = "banner show " + kind;
  el.textContent = msg;
}
function clearBanner() { $("banner").className = "banner"; }

function envLabel() { return TEST ? "TEST" : "PRODUCTION"; }

function reflectEnv() {
  const pill = $("envPill");
  pill.textContent = envLabel();
  pill.className = "pill " + (TEST ? "test" : "prod");
}

function reflectWriteMode() {
  $("writeToggle").checked = WRITE;
  if (WRITE) banner("warn", `Write mode — ${envLabel()}. Edits and row actions are sent to ENA.`);
  else clearBanner();
}

// --- Tabs -------------------------------------------------------------------
function buildTabs() {
  const nav = $("tabs");
  nav.innerHTML = "";
  for (const entity of ENTITIES) {
    const button = document.createElement("button");
    button.textContent = entity.label;
    button.dataset.entity = entity.id;
    button.className = entity.id === ENTITY ? "active" : "";
    button.onclick = () => selectEntity(entity.id);
    nav.appendChild(button);
  }
}

function selectEntity(entity) {
  ENTITY = entity;
  for (const button of $("tabs").children) {
    button.classList.toggle("active", button.dataset.entity === entity);
  }
  saveUi();
  loadEntity();
}

// --- Header toggles ---------------------------------------------------------
$("prodToggle").onchange = (event) => {
  if (event.target.checked && !confirm("Switch to the PRODUCTION ENA service? Changes there are permanent.")) {
    event.target.checked = false;
    return;
  }
  TEST = !event.target.checked;
  reflectEnv();
  reflectWriteMode();
  saveUi();
  loadEntity();
};

$("writeToggle").onchange = (event) => {
  if (event.target.checked) {
    if (HEALTH.readonly) {
      event.target.checked = false;
      banner("bad", "The server is running read-only (ENA_BROWSER_READONLY). Restart it with that unset to make changes.");
      return;
    }
    if (!confirm(`Enable write mode against ${envLabel()}? Edits you submit change records in ENA.`)) {
      event.target.checked = false;
      return;
    }
  }
  WRITE = event.target.checked;
  reflectWriteMode();
  applyMode();
};

$("credToggle").onclick = () => { $("credPanel").hidden = !$("credPanel").hidden; };
$("reload").onclick = () => loadEntity();
