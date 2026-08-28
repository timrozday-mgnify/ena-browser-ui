"use strict";

// ---------------------------------------------------------------------------
// Change history: a stack of the MODIFYs ENA has accepted from this browser,
// newest on top, each one clickable for its contents and revertible.
//
// Not the same thing as undo.js, which is a stack of *staged* grid state and
// dies with the page. This is the record of what actually reached ENA, so it
// outlives a reload (localStorage) — and reverting is not an undo but a fresh
// MODIFY built from `previous`, the values ENA held before the change, which
// `ena_submission_toolkit.records` returns with every result.
// ---------------------------------------------------------------------------
const HISTORY_KEY = "ena-browser-ui.history";
const HISTORY_MAX = 100;

let HISTORY = readJson(HISTORY_KEY, []);

function saveHistory() {
  HISTORY = HISTORY.slice(0, HISTORY_MAX);
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(HISTORY));
  } catch {
    // A manifest is a whole XML document; a long history can fill the quota.
    // Losing the oldest half is better than losing the panel.
    HISTORY = HISTORY.slice(0, Math.floor(HISTORY.length / 2));
    localStorage.setItem(HISTORY_KEY, JSON.stringify(HISTORY));
  }
}

/** Push the accepted results of one MODIFY submission onto the stack. */
function pushHistory(entity, results, { revertOf = "" } = {}) {
  for (const result of results) {
    if (!result.success) continue;   // nothing changed in ENA, nothing to revert
    HISTORY.unshift({
      id: `${Date.now()}-${result.accession}-${HISTORY.length}`,
      at: new Date().toISOString(),
      entity,
      test: TEST,
      accession: result.accession,
      changes: result.changes || {},
      previous: result.previous || {},
      xml: result.xml || "",
      undoXml: result.undo_xml || "",
      revertOf,
      revertedBy: "",
    });
  }
  if (revertOf) {
    const target = HISTORY.find((item) => item.id === revertOf);
    if (target) target.revertedBy = HISTORY[0]?.id || "done";
  }
  saveHistory();
  renderHistory();
}

/** The change set that puts this record back as it was before `item`. */
function revertChanges(item) {
  return Object.fromEntries(
    Object.entries(item.previous || {}).filter(([, value]) => value !== null && value !== undefined),
  );
}

function historyLine(item) {
  return Object.entries(item.changes)
    .map(([field, value]) =>
      `<li><code>${esc(field)}</code>: <span class="muted">${esc(item.previous?.[field] ?? "")}</span> → ${esc(value)}</li>`)
    .join("");
}

function renderHistory() {
  $("historyEmpty").hidden = HISTORY.length > 0;
  $("historyCount").textContent = HISTORY.length ? `${HISTORY.length} accepted change(s)` : "";
  $("history").innerHTML = HISTORY.map((item) => {
    const fields = Object.keys(item.changes).join(", ") || "no fields";
    const when = new Date(item.at).toLocaleString();
    const state = item.revertedBy ? " · reverted" : item.revertOf ? " · revert" : "";
    const canRevert = !item.revertedBy && Object.keys(revertChanges(item)).length > 0;
    return (
      `<details class="entry ${item.revertedBy ? "" : "ok"}" data-id="${esc(item.id)}">` +
      `<summary>${esc(when)} · ${esc(item.accession)} · ${esc(item.entity)} ${item.test ? "TEST" : "PRODUCTION"}` +
      ` · ${esc(fields)}${state}</summary>` +
      `<div class="body">` +
      `<ul class="fields">${historyLine(item)}</ul>` +
      `<div class="row" style="margin:8px 0 0">` +
      `<button class="secondary" data-revert="${esc(item.id)}"${canRevert ? "" : " disabled"}>` +
      `Revert this change</button>` +
      `<span class="muted">${item.revertedBy ? "already reverted" :
        canRevert ? "re-submits the values ENA held before this change" :
        "ENA held no previous value to put back"}</span></div>` +
      `<p class="muted" style="margin:8px 0 0">Submitted document</p><pre>${esc(item.xml)}</pre>` +
      (item.undoXml
        ? `<p class="muted" style="margin:8px 0 0">Manifest that would undo it</p><pre>${esc(item.undoXml)}</pre>`
        : "") +
      `</div></details>`
    );
  }).join("");
}

$("history").onclick = async (event) => {
  const id = event.target.dataset?.revert;
  if (!id) return;
  const item = HISTORY.find((candidate) => candidate.id === id);
  if (!item) return;
  const changes = revertChanges(item);

  if (!WRITE) { banner("bad", "Reverting sends a MODIFY to ENA — turn on write mode first."); return; }
  if (item.test !== TEST) {
    banner("bad", `That change was made against ${item.test ? "TEST" : "PRODUCTION"}; switch environment to revert it.`);
    return;
  }
  const summary = Object.entries(changes).map(([field, value]) => `${field} → ${value}`).join(", ");
  if (!confirm(`Revert ${item.accession} in ${envLabel()}?\n\n${summary}`)) return;

  event.target.disabled = true;
  try {
    const body = await api("/api/records/modify", {
      method: "POST",
      body: JSON.stringify({ entity: item.entity, records: [{ accession: item.accession, changes }] }),
    });
    const results = body.results || [];
    logSubmission([{ accession: item.accession, changes, before: item.changes }], results);
    pushHistory(item.entity, results, { revertOf: item.id });
    if (body.success) {
      banner("ok", `Reverted ${item.accession} in ${envLabel()}.`);
      if (item.entity === ENTITY) await loadEntity();
    } else {
      banner("bad", `ENA rejected the revert of ${item.accession}; the submission log below has what it said.`);
      renderHistory();
    }
  } catch (error) {
    banner("bad", `Revert failed: ${error.message}`);
    renderHistory();
  }
};

$("historyClear").onclick = () => {
  if (!confirm("Forget this history? The changes stay in ENA — only the record of them here is dropped.")) return;
  HISTORY = [];
  saveHistory();
  renderHistory();
};

renderHistory();
