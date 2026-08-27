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

function canEdit() { return WRITE && editableFor(ENTITY).length > 0; }

function applyMode() {
  grid.applyConfig({
    mode: canEdit() ? "edit" : "read",
    editableColumns: editableFor(ENTITY),
    rowActions: WRITE ? ROW_ACTIONS : [],
  });
  refreshSubmitButton();
  if (WRITE && !canEdit()) {
    banner("warn", `Write mode — ${envLabel()}. ${ENTITY} cannot be edited here; row actions still apply.`);
  }
}

function refreshSubmitButton() {
  $("submit").disabled = !canEdit() || grid.getChangeSet().rows.length === 0;
}

// --- Loading ----------------------------------------------------------------
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
    const body = await api(`/api/records/${ENTITY}`);
    applySavedLayout(ENTITY);
    grid.setRows(body.rows || []);
    applyMode();
    resetUndo();
    $("rowCount").textContent = `${(body.rows || []).length} records from ${envLabel()}`;
    if (WRITE) reflectWriteMode(); else clearBanner();
  } catch (error) {
    $("rowCount").textContent = "load failed";
    grid.setRows([]);
    banner("bad", `Could not load ${ENTITY} from ${envLabel()}: ${error.message}`);
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
      ([field, value]) => `<tr><td>${entry.accession}</td><td>${field}</td>` +
        `<td class="muted">${entry.before?.[field] ?? ""}</td><td>${value ?? ""}</td></tr>`,
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
  if (!entries.length) { refreshSubmitButton(); return; }
  if (!(await showDiff(entries))) return;

  $("submit").disabled = true;
  try {
    const body = await api("/api/records/modify", {
      method: "POST",
      body: JSON.stringify({
        entity: ENTITY,
        records: entries.map(({ accession, changes }) => ({ accession, changes })),
      }),
    });
    const failed = (body.results || []).filter((r) => !r.success);
    if (body.success) {
      // ENA now holds the new values; drop the local ones and re-fetch rather
      // than trusting the optimistic copy.
      grid.clearChanges();
      banner("ok", `Submitted ${body.results.length} change(s) to ${envLabel()}.`);
      await loadEntity();
    } else {
      // Deliberately keep the change set so the user can fix and retry.
      banner("bad", "ENA rejected some changes; your edits are kept.\n" +
        failed.map((r) => `${r.accession}: ${r.messages.join("; ")}`).join("\n"));
    }
  } catch (error) {
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
    if (body.success) {
      banner("ok", `${action} ${accession}: done. ${detail}`);
      await loadEntity();   // the status column is how the user sees it worked
    } else {
      banner("bad", `${action} ${accession} failed: ${detail || "ENA rejected it"}`);
    }
  } catch (error) {
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
