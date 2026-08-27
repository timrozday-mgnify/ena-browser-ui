"use strict";

// ---------------------------------------------------------------------------
// Undo/redo over the element's own state snapshots.
//
// The element is built for this: getState() is one JSON-safe blob of
// everything the user can change, and setState() stamps the events it causes
// with source:"api" — which is what stops a restore from pushing itself back
// onto the stack.
//
// It undoes *staged* work. A submission to ENA is not undoable, so a
// successful one clears the stack rather than pretending otherwise.
// ---------------------------------------------------------------------------
const UNDO_LIMIT = 100;

let stack = [];
let index = -1;        // position in `stack` of the state currently shown
let restoring = false; // suppresses pushes while setState() settles
let pushTimer = null;

function reflectUndo() {
  $("undo").disabled = index <= 0;
  $("redo").disabled = index < 0 || index >= stack.length - 1;
}

/** Start a fresh history at the element's current state (a new entity load). */
function resetUndo() {
  clearTimeout(pushTimer);
  stack = [snapshot()];
  index = 0;
  reflectUndo();
}

function snapshot() {
  return JSON.parse(JSON.stringify($("grid").getState()));
}

/** Debounced so a drag-resize is one undo step, not forty. */
function pushUndo() {
  if (restoring) return;
  clearTimeout(pushTimer);
  pushTimer = setTimeout(() => {
    const state = snapshot();
    if (index >= 0 && JSON.stringify(stack[index]) === JSON.stringify(state)) return;
    stack = stack.slice(0, index + 1);
    stack.push(state);
    if (stack.length > UNDO_LIMIT) stack.shift();
    index = stack.length - 1;
    reflectUndo();
  }, 300);
}

function restore(to) {
  if (to < 0 || to >= stack.length) return;
  clearTimeout(pushTimer);
  restoring = true;
  index = to;
  $("grid").setState(stack[index]);
  reflectUndo();
  refreshSubmitButton();
  // setState() dispatches its events synchronously, but leave the guard up
  // for one turn in case a grid render defers any of them.
  setTimeout(() => { restoring = false; }, 0);
}

function undo() { restore(index - 1); }
function redo() { restore(index + 1); }

$("undo").onclick = undo;
$("redo").onclick = redo;

document.addEventListener("keydown", (event) => {
  if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "z") return;
  // Handsontable owns undo while a cell editor is open.
  if (document.querySelector(".handsontableInput:not([style*='display: none'])")) return;
  event.preventDefault();
  if (event.shiftKey) redo(); else undo();
});
