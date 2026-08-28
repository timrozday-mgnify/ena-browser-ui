"use strict";

// ---------------------------------------------------------------------------
// The records tab: load an entity, mirror the read/write toggle into the grid,
// stage edits, submit them as MODIFY, and run lifecycle actions.
// ---------------------------------------------------------------------------
const grid = $("grid");

const ROW_ACTIONS = [
  { action: "release", label: "Release", title: "Make this record public now" },
  { action: "hold", label: "Hold", title: "Set the release date" },
  { action: "suppress", label: "Suppress", title: "Hide a public record" },
  { action: "cancel", label: "Cancel", title: "Cancel a private record" },
];

// Filled from /api/health: which fields this app knows how to MODIFY, per
// entity. The server is the source of truth — it is what builds the XML.
let EDITABLE = {};

function editableFor(entity) { return EDITABLE[entity] || []; }

/** Records this account did not submit. ENA would refuse a MODIFY of one, so
 *  the page must not offer the edit — see `applyMode`. */
function browsingEna() { return $("qSource").value === "ena"; }

function canEdit() { return WRITE && !browsingEna() && editableFor(ENTITY).length > 0; }

function applyMode() {
  if (!canEdit()) clearManifests();
  grid.applyConfig({
    mode: canEdit() ? "edit" : "read",
    editableColumns: editableFor(ENTITY),
    rowActions: WRITE && !browsingEna() ? ROW_ACTIONS : [],
  });
  refreshSubmitButton();
  if (WRITE && browsingEna()) {
    banner("warn", "Write mode, but these are ENA's public records, not yours — nothing here is editable.");
  } else if (WRITE && !canEdit()) {
    banner("warn", `Write mode — ${envLabel()}. ${ENTITY} cannot be edited here; row actions still apply.`);
  }
}

// --- The manifest gate ------------------------------------------------------
// Submitting a MODIFY replaces the whole record in ENA, so it is not a button
// press: the exact documents have to be built and shown first. MANIFESTS holds
// the last preview, MANIFEST_KEY the change set it was built from — edit
// anything and the key no longer matches, which re-locks the submit button.
let MANIFESTS = null;
let MANIFEST_KEY = "";

const changeKey = (entries) => JSON.stringify(entries);

function manifestsReady(entries) {
  return (
    entries.length > 0 &&
    MANIFESTS !== null &&
    MANIFEST_KEY === changeKey(entries) &&
    MANIFESTS.length === entries.length &&
    MANIFESTS.every((manifest) => manifest.success)
  );
}

function refreshSubmitButton() {
  const entries = canEdit() ? pendingChanges() : [];
  $("generate").disabled = entries.length === 0;
  $("submit").disabled = !manifestsReady(entries);
  renderManifestState(entries);
}

function renderManifestState(entries) {
  const state = $("manifestState");
  const stale = MANIFESTS !== null && MANIFEST_KEY !== changeKey(entries);
  const failed = (MANIFESTS || []).filter((manifest) => !manifest.success).length;
  let className = "muted";
  let text;
  if (!entries.length) {
    text = MANIFESTS ? "no staged changes left" : "no staged changes";
  } else if (MANIFESTS === null || stale) {
    className = "warn";
    text = `${entries.length} record(s) staged — no manifests for these edits yet`;
  } else if (failed) {
    className = "bad";
    text = `${failed} of ${MANIFESTS.length} manifest(s) could not be built — nothing will be submitted`;
  } else {
    className = "ok";
    text = `${MANIFESTS.length} manifest(s) built and ready to review`;
  }
  state.className = "state " + className;
  state.textContent = text;
}

function clearManifests() {
  MANIFESTS = null;
  MANIFEST_KEY = "";
  $("manifests").innerHTML = "";
  $("manifestEmpty").hidden = false;
}

function fieldList(entry) {
  return Object.entries(entry.changes || {})
    .map(([field, value]) => `<li><code>${esc(field)}</code>: ` +
      `<span class="muted">${esc(entry.before?.[field] ?? "")}</span> → ${esc(value)}</li>`)
    .join("");
}

function renderManifests(entries) {
  const byAccession = new Map(entries.map((entry) => [entry.accession, entry]));
  $("manifestEmpty").hidden = MANIFESTS.length > 0;
  $("manifests").innerHTML = MANIFESTS.map((manifest) => {
    const entry = byAccession.get(manifest.accession) || {};
    const changes = { ...entry, changes: manifest.changes || entry.changes };
    const body = manifest.success
      ? `<pre>${esc(manifest.xml)}</pre>`
      : `<p class="msg bad">${esc((manifest.messages || []).join("; ") || "could not be built")}</p>` +
        `<p class="muted">This record will not be submitted, and neither will any other ` +
        `until every manifest builds.</p>`;
    return `<details class="entry ${manifest.success ? "ok" : "bad"}"${MANIFESTS.length === 1 ? " open" : ""}>` +
      `<summary>${esc(manifest.accession)} — ${manifest.success ? "manifest built" : "could not be built"}</summary>` +
      `<div class="body"><ul class="fields">${fieldList(changes)}</ul>${body}</div></details>`;
  }).join("");
}

$("generate").onclick = async () => {
  const entries = pendingChanges();
  if (!entries.length) { refreshSubmitButton(); return; }
  $("generate").disabled = true;
  try {
    const body = await api("/api/records/modify/preview", {
      method: "POST",
      body: JSON.stringify({ entity: ENTITY, records: entries.map(({ accession, changes }) => ({ accession, changes })) }),
    });
    MANIFESTS = body.results || [];
    MANIFEST_KEY = changeKey(entries);
    renderManifests(entries);
    reflectWriteMode();
  } catch (error) {
    clearManifests();
    banner("bad", `Could not build the manifests; nothing was submitted: ${error.message}`);
  }
  refreshSubmitButton();
};

// --- The submission log -----------------------------------------------------
// Verbose on purpose: what was sent, what ENA said, and whether the document
// that went out is the one that was reviewed.
function logEntry({ title, ok, lines = [], html = "" }) {
  $("logEmpty").hidden = true;
  const stamp = new Date().toLocaleTimeString();
  const element = document.createElement("details");
  element.className = "entry " + (ok ? "ok" : "bad");
  element.open = true;
  element.innerHTML =
    `<summary>${stamp} · ${esc(title)}</summary><div class="body">` +
    lines.map(([kind, text]) => `<p class="msg ${kind}">${esc(text)}</p>`).join("") +
    html + "</div>";
  $("log").prepend(element);
  while ($("log").children.length > 20) $("log").lastElementChild.remove();
}

function logSubmission(entries, results) {
  const reviewed = new Map((MANIFESTS || []).map((manifest) => [manifest.accession, manifest.xml]));
  for (const result of results) {
    const entry = entries.find((candidate) => candidate.accession === result.accession) || {};
    const lines = [];
    for (const message of result.info || []) lines.push(["", message]);
    for (const message of result.warnings || []) lines.push(["warn", message]);
    for (const message of result.errors || []) lines.push(["bad", message]);
    if (!lines.length) for (const message of result.messages || []) lines.push([result.success ? "" : "bad", message]);
    if (!lines.length) lines.push(["", result.success ? "ENA returned no messages." : "ENA rejected it without saying why."]);

    const sent = result.xml || "";
    const same = sent && reviewed.get(result.accession) === sent;
    lines.unshift([same ? "" : "warn",
      same ? "Document sent is byte-for-byte the manifest reviewed above."
           : "The document sent differs from the manifest that was reviewed."]);
    logEntry({
      title: `${result.accession} · MODIFY ${envLabel()} · ${result.success ? "accepted" : "REJECTED"}`,
      ok: result.success,
      lines,
      html: `<ul class="fields">${fieldList({ ...entry, changes: result.changes || entry.changes })}</ul>` +
        (sent ? `<pre>${esc(sent)}</pre>` : ""),
    });
  }
}

$("logClear").onclick = () => {
  $("log").innerHTML = "";
  $("logEmpty").hidden = false;
};

// --- Loading ----------------------------------------------------------------
/** Report rows + the editable fields only the record XML carries.
 *
 *  A run's title and an experiment's library/instrument are not in the Reports
 *  API's answer, so without this there would be no cell to edit them in. Only
 *  worth the requests in write mode; a failure here degrades to the report's
 *  own columns rather than losing the grid. */
async function withEditableFields(rows) {
  const accessions = rows.map((row) => row.accession).filter(Boolean);
  if (!accessions.length) return rows;
  try {
    const body = await api(`/api/records/${ENTITY}/fields`, {
      method: "POST",
      body: JSON.stringify({ accessions }),
    });
    const fields = body.fields || {};
    return rows.map((row) => ({ ...row, ...(fields[row.accession] || {}) }));
  } catch (error) {
    banner("warn", `Loaded ${ENTITY}, but not the fields held only in the record XML — ` +
      `those columns will be missing: ${error.message}`);
    return rows;
  }
}

// --- Fetch criteria ---------------------------------------------------------
// The Webin Reports API takes a release status and nothing else — no search,
// no "which samples are in this study". Everything here is applied server-side
// by ena-submission-toolkit over the rows it fetched, so it is a criterion on
// the *request*, not the column filters the grid already does client-side.
// Deliberately not per entity: "everything linked to PRJEB1234" is a question
// worth asking of the samples tab and then the reads tab without retyping.
function criteriaQuery() {
  const params = new URLSearchParams();
  const search = $("qSearch").value.trim();
  const linked = $("qLinked").value.trim();
  // The Portal API resolves the relationship itself and answers about public
  // data only, so the rest of these have nothing to act on.
  if (browsingEna()) {
    params.set("source", "ena");
    if (linked) params.set("linked_to", linked);
    return params.toString();
  }
  if (search) params.set("search", search);
  if (linked) params.set("linked_to", linked);
  if ($("qUnlinked").checked) params.set("unlinked", "true");
  // Default on: the extra columns arrive hidden, so they cost a slower fetch
  // and nothing else until someone ticks one on in the Columns menu.
  if (!$("qFullFields").checked) params.set("full_fields", "false");
  if ($("qStatus").value !== "all") params.set("status", $("qStatus").value);
  return params.toString();
}

for (const id of ["qSearch", "qLinked"]) {
  $(id).onkeydown = (event) => { if (event.key === "Enter") loadEntity(); };
}
$("qUnlinked").onchange = () => loadEntity();
$("qFullFields").onchange = () => loadEntity();
$("qStatus").onchange = () => loadEntity();
$("qSource").onchange = () => { applyCriteriaSource(); loadEntity(); };

/** Grey out the criteria the current source cannot answer, so "ignored" is
 *  visible rather than something to discover. */
function applyCriteriaSource() {
  const ena = browsingEna();
  for (const id of ["qSearch", "qUnlinked", "qStatus", "qFullFields"]) $(id).disabled = ena;
  $("qLinked").placeholder = ena
    ? "accession to search ENA from, e.g. PRJEB1787"
    : "linked to accession, e.g. PRJEB1234";
  applyMode();
}
$("qClear").onclick = () => {
  $("qSearch").value = "";
  $("qLinked").value = "";
  $("qUnlinked").checked = false;
  $("qFullFields").checked = true;
  $("qStatus").value = "all";
  $("qSource").value = "account";
  applyCriteriaSource();
  loadEntity();
};

async function loadEntity() {
  if (!credsConfigured()) {
    $("rowCount").textContent = "no credentials";
    $("credPanel").hidden = false;
    banner("bad", "Enter your Webin username and password to load records.");
    return;
  }
  $("rowCount").textContent = "loading…";
  grid.applyConfig({ entity: ENTITY, mode: "read", rowActions: WRITE ? ROW_ACTIONS : [] });
  try {
    const query = criteriaQuery();
    const body = await api(`/api/records/${ENTITY}${query ? `?${query}` : ""}`);
    let rows = body.rows || [];
    if (canEdit()) rows = await withEditableFields(rows);
    applySavedLayout(ENTITY);
    grid.setRows(rows);
    clearManifests();
    applyMode();
    resetUndo();
    $("rowCount").textContent =
      browsingEna()
        // The Portal API is the public production index; the TEST/PRODUCTION
        // switch is a Webin submission environment and does not apply to it.
        ? `${rows.length} public records from ENA under ${$("qLinked").value.trim()}`
        : `${rows.length} records from ${envLabel()}${query ? " matching the criteria" : ""}`;
    if (WRITE) reflectWriteMode(); else clearBanner();
  } catch (error) {
    $("rowCount").textContent = "load failed";
    grid.setRows([]);
    banner("bad", `Could not load ${ENTITY} from ${browsingEna() ? "ENA" : envLabel()}: ${error.message}`);
  }
}

// --- Submitting the change set ---------------------------------------------
/** Change set rows -> what /api/records/modify wants, narrowed to editable
 *  fields. The server refuses anything else, but sending it would be a bug. */
function pendingChanges() {
  const allowed = new Set(editableFor(ENTITY));
  return grid.getChangeSet().rows
    .map((row) => {
      const changes = {};
      for (const field of row.changed) {
        if (allowed.has(field)) changes[field] = row.after[field];
      }
      return { accession: row.accession || row.key, changes, before: row.before };
    })
    .filter((entry) => Object.keys(entry.changes).length > 0);
}

function showDiff(entries) {
  $("diffEnv").textContent =
    `${entries.length} record(s) will be modified in ${envLabel()}.` +
    (TEST ? "" : " This is the production service.");
  const rows = entries.flatMap((entry) =>
    Object.entries(entry.changes).map(
      ([field, value]) => `<tr><td>${esc(entry.accession)}</td><td>${esc(field)}</td>` +
        `<td class="muted">${esc(entry.before?.[field] ?? "")}</td><td>${esc(value)}</td></tr>`,
    ),
  );
  $("diffTable").innerHTML =
    "<thead><tr><th>Accession</th><th>Field</th><th>Before</th><th>After</th></tr></thead><tbody>" +
    rows.join("") + "</tbody>";

  const dialog = $("diffDialog");
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true });
    dialog.showModal();
  });
}

$("submit").onclick = async () => {
  const entries = pendingChanges();
  if (!manifestsReady(entries)) { refreshSubmitButton(); return; }
  if (!(await showDiff(entries))) return;

  $("submit").disabled = true;
  const payload = entries.map(({ accession, changes }) => ({ accession, changes }));
  try {
    const body = await api("/api/records/modify", {
      method: "POST",
      body: JSON.stringify({ entity: ENTITY, records: payload }),
    });
    const results = body.results || [];
    logSubmission(entries, results);
    const failed = results.filter((result) => !result.success);
    if (body.success) {
      // ENA now holds the new values; drop the local ones and re-fetch rather
      // than trusting the optimistic copy.
      grid.clearChanges();
      banner("ok", `Submitted ${results.length} change(s) to ${envLabel()}. See the submission log below.`);
      await loadEntity();
    } else {
      // Deliberately keep the change set so the user can fix and retry.
      banner("bad", `ENA rejected ${failed.length} of ${results.length} record(s); your edits are kept. ` +
        "The submission log below has what it said.");
    }
  } catch (error) {
    logEntry({
      title: `MODIFY ${envLabel()} · failed before ENA answered`,
      ok: false,
      lines: [["bad", error.message], ["", `${payload.length} record(s) were in the batch; your edits are kept.`]],
    });
    banner("bad", `Submission failed; your edits are kept: ${error.message}`);
  }
  refreshSubmitButton();
};

// --- Lifecycle actions ------------------------------------------------------
grid.addEventListener("ena-browser:row-action", async (event) => {
  const { action, key, row } = event.detail;
  const accession = row?.accession || key;
  const payload = { entity: ENTITY, accession, action };

  if (action === "hold") {
    const date = prompt(`Hold ${accession} until (YYYY-MM-DD):`);
    if (!date) return;
    payload.hold_until_date = date.trim();
  } else if (action !== "release") {
    if (!confirm(`${action.toUpperCase()} ${accession} in ${envLabel()}?`)) return;
  }

  try {
    const body = await api("/api/records/action", { method: "POST", body: JSON.stringify(payload) });
    const detail = (body.messages || []).join("; ");
    logEntry({
      title: `${accession} · ${action.toUpperCase()} ${envLabel()} · ${body.success ? "accepted" : "REJECTED"}`,
      ok: body.success,
      lines: (body.messages || []).map((message) => [body.success ? "" : "bad", message]),
    });
    if (body.success) {
      banner("ok", `${action} ${accession}: done. ${detail}`);
      await loadEntity();   // the status column is how the user sees it worked
    } else {
      banner("bad", `${action} ${accession} failed: ${detail || "ENA rejected it"}`);
    }
  } catch (error) {
    logEntry({ title: `${accession} · ${action.toUpperCase()} ${envLabel()} · failed`, ok: false,
               lines: [["bad", error.message]] });
    banner("bad", `${action} ${accession} failed: ${error.message}`);
  }
});

// --- Element events ---------------------------------------------------------
grid.addEventListener("ena-browser:change", (event) => {
  refreshSubmitButton();
  if (event.detail.source !== "api") pushUndo();
});

for (const name of ["filter-change", "layout-change", "selection-change"]) {
  grid.addEventListener(`ena-browser:${name}`, (event) => {
    if (event.detail.source === "api") return;
    pushUndo();
    scheduleLayoutSave();
  });
}

grid.addEventListener("ena-browser:filter-change", (event) => {
  if (typeof event.detail.visibleCount === "number") {
    $("rowCount").textContent = `${event.detail.visibleCount} of ${grid.getRows().length} records from ${envLabel()}`;
  }
});

grid.addEventListener("ena-browser:error", (event) => banner("bad", event.detail.message));

// --- Boot -------------------------------------------------------------------
(async function boot() {
  restoreUi();
  buildTabs();
  reflectEnv();
  restoreCreds();
  try {
    HEALTH = await api("/api/health");
    EDITABLE = HEALTH.editable_columns || {};
  } catch (error) {
    banner("bad", `Could not reach the server: ${error.message}`);
    return;
  }
  if (!HEALTH.element_available) {
    banner("bad", "The ena-browser bundle is missing from server/static/vendor/ — run `task vendor`.");
    return;
  }
  if (HEALTH.readonly) {
    $("writeToggle").disabled = true;
    $("writeToggle").title = "Server is read-only (ENA_BROWSER_READONLY)";
  }
  if (credsConfigured()) await loadEntity();
  else { $("credPanel").hidden = false; banner("ok", "Enter your Webin credentials to begin."); }
})();
