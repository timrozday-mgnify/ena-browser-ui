"use strict";

// ---------------------------------------------------------------------------
// Layout persistence. Column layout, filters and sort survive a reload, per
// entity. Rows never do — they are re-fetched, so a stale ENA status cannot be
// shown as if it were current.
// ---------------------------------------------------------------------------
const LAYOUT_KEY = "ena-browser-ui.layout";
const UI_KEY = "ena-browser-ui.ui";

function readJson(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}

function savedLayouts() { return readJson(LAYOUT_KEY, {}); }

function saveLayoutFor(entity, patch) {
  const all = savedLayouts();
  all[entity] = { ...(all[entity] || {}), ...patch };
  localStorage.setItem(LAYOUT_KEY, JSON.stringify(all));
}

/** Apply the saved layout/filters/sort for an entity. Call before setRows().
 *
 *  Always sets all three, even when nothing is saved: entities share one grid,
 *  so anything left unset would carry the previous entity's pins and filters
 *  over to the new one. The element reconciles an empty order against the
 *  actual columns on rebuild. */
function applySavedLayout(entity) {
  const saved = savedLayouts()[entity] || {};
  const grid = $("grid");
  grid.setLayout(saved.layout || { order: [], pinned: [], hidden: [], widths: {} });
  grid.setFilters(saved.filters || []);
  grid.setSort(saved.sort || []);
}

function saveUi() {
  localStorage.setItem(UI_KEY, JSON.stringify({ entity: ENTITY, test: TEST }));
}

/** Restore the last entity and environment. Write mode is deliberately not
 *  restored: unlocking writes is a decision, not a preference. */
function restoreUi() {
  const ui = readJson(UI_KEY, {});
  if (ENTITIES.some((e) => e.id === ui.entity)) ENTITY = ui.entity;
  if (typeof ui.test === "boolean") TEST = ui.test;
  $("prodToggle").checked = !TEST;
}

let saveTimer = null;
/** Debounced: a drag-resize fires layout-change per pixel. */
function scheduleLayoutSave() {
  const entity = ENTITY;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    const { layout, filters, sort } = $("grid").getState();
    saveLayoutFor(entity, { layout, filters, sort });
  }, 400);
}
