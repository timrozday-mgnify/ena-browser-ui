# Implementation plan

**Status: all nine phases built and tested.** This document is kept as the
record of what was intended and where the build diverged; the two divergences
are marked *Built differently* in place. The important one is Phase 6: MODIFY
payloads are made by fetching and patching the record's current XML, not by
rebuilding it with `ena-submission-toolkit`'s builders — see the README's
[How a change reaches ENA](README.md#how-a-change-reaches-ena).

## Progress

| Phase | Status | Where it lives |
|---|---|---|
| 1 — Skeleton that serves a page | done | `manage.py`, `server/config/`, `server/views_core.py`, `server/static/index.html`, `theme.js` |
| 2 — Credentials | done | `server/webin_creds.py`, `static/creds.js`, `static/app.js` (`api()`) |
| 3 — Read-only browsing | done | `records.list_records`, `views_records.records_list`, `static/records.js` |
| 4 — Persisted layout | done | `static/state.js` |
| 5 — Read/write toggle | done | `config/settings.py` (`READONLY`), `views_records._guard`, `static/app.js` |
| 6 — Editing and MODIFY | done, **differently** | `records.modify_records` + `_EDITABLE`, the diff dialog in `static/records.js` |
| 7 — Lifecycle actions | done | `records.record_action`, the `row-action` handler in `static/records.js` |
| 8 — Undo/redo | done | `static/undo.js` |
| 9 — Tests and packaging | done, **differently** | `tests/` (38 tests), `Taskfile.yml`, `.github/workflows/ci.yml` |

Not built, and deliberately: no `Dockerfile`, no `docker-compose.yml` (Phase 9).

Phased build of `ena-browser-ui`. Each phase ends in a working app and is
independently revertable; each has a **Check** that is the definition of done.
Read [README.md](README.md) (the stack decisions) and
[`ena-browser`'s README](https://github.com/timrozday-mgnify/ena-browser)
(the element's API contract) first.

**Prerequisite:** `ena-browser` v0.1.0 tagged with `dist/ena-browser.iife.js`
and `dist/ena-browser.css` published as release assets. Without them `task
vendor` has nothing to fetch and nothing below can be verified. If the tag
does not exist yet, Phase 1 may temporarily point `ENA_BROWSER_REF` at a local
`../ena-browser/dist` copy — but do not merge that state.

---

## Target layout

```
manage.py                  Django entrypoint (PYTHONPATH=server)
server/
  config/{settings,urls,wsgi}.py
  webin_creds.py           request headers -> records.Credentials, or 401
  views_core.py            index + health + static serving
  views_records.py         /api/records/*
  static/
    index.html  app.js  records.js  creds.js  undo.js  state.js  theme.js
    vendor/ena-browser/    (gitignored; `task vendor`)
tests/
  test_api.py  test_ui.py  conftest.py
```

Eight Python files and six JS files is the whole app (ENA itself is
`ena_submission_toolkit.records`, not a module here). If a phase wants a tenth,
say why in the PR.

---

## Phase 1 — Skeleton that serves a page

1. `manage.py`, `server/config/{settings,urls,wsgi}.py` — copy the assistant's
   shape verbatim: `INSTALLED_APPS = []`, no DB, no CSRF, `ALLOWED_HOSTS=["*"]`
   with a loopback bind. This is a stateless proxy, not a Django project.
2. `views_core.py`: `index` (serves `static/index.html`), `health`
   (`{readonly, test_default, ena_browser_ref}`), and recursive static serving
   of `server/static/` — which must cover `vendor/`.
3. `static/index.html`: the EBI header bar, a test/production pill, a
   credentials panel, entity tabs, and one `<ena-browser id="grid">`. Reuse the
   assistant's CSS custom-property theme block (`:root[data-theme=…]`) so the
   two apps look like siblings, and `theme.js` with it.
4. `task vendor`, then the two `<link>`/`<script>` tags before the app scripts.

**Check:** `task dev` serves the page; `customElements.get("ena-browser")` is
defined; the grid renders empty; `/api/health` returns the flags.

## Phase 2 — Credentials

1. `creds.js`: username/password inputs → `sessionStorage` for this tab only,
   a "credentials: set/not set" indicator, and a clear button. Copy the
   assistant's `credentials.js` behaviour; do not invent a new one.
2. Every `fetch` goes through one `api()` helper that attaches
   `X-Webin-Username` / `X-Webin-Password` and the `test` flag, and surfaces a
   401 as "enter your Webin credentials" rather than a console error.
3. `server/webin_creds.py`: headers → `records.Credentials`, or a 401
   `JsonResponse`. Lifted from the assistant unchanged.
4. `POST /api/credentials/validate` → `records.validate_credentials()`
   (a `list_projects(max_results=1)` call), so a typo is caught at entry rather
   than on first fetch.

**Check:** wrong credentials produce a clear message; right ones validate
against test *and* production; nothing is written to disk; the password field
is cleared after saving; a reload in the same tab keeps them, a new tab does not.

## Phase 3 — Read-only browsing

1. `records.list_records(creds, entity, *, test, max_results)` wrapping
   `WebinClient.reports.list_*`. Entity → method map, `studies → list_projects`.
   Return plain dicts (`model_dump()`), not models — the grid wants JSON.
2. `GET /api/records/<entity>` → `{rows: [...]}`, with a per-request client
   that is always closed.
3. `records.js`: on tab click, fetch and `grid.applyConfig({entity, mode:"read"})`
   + `grid.setRows(rows)`. Do **not** define a column list — the element's
   `entities.ts` owns the defaults and appends unknown Reports fields
   automatically; a fixed list here would silently drop them.
4. Tabs: studies, samples, reads (runs), experiments; plus analyses and files
   behind a "more" affordance — they cost nothing, the element already knows them.
5. A row count and an error banner fed from `ena-browser:error`.

**Check:** each of the six entities loads against a real test account; an
account with no runs shows "no records" rather than an error (Reports returns
404 for an empty entity); the extra Reports fields appear as columns.

## Phase 4 — Persisted layout

1. `state.js`: subscribe to `ena-browser:layout-change` and
   `ena-browser:filter-change`, debounce, and write
   `{entity, layout, filters, sort}` per entity to `localStorage`.
2. On entity load, apply `setLayout()` / `setFilters()` / `setSort()` **before**
   `setRows()`.
3. Never persist rows. They are re-fetched; a persisted row would show a status
   ENA no longer holds.

**Check:** pin, reorder and filter; reload; the layout is back and the rows are
freshly fetched. Layouts are per entity, not global.

## Phase 5 — Read/write toggle

The toggle is a *safety* feature, so build it before anything that writes.

1. Server: `ENA_BROWSER_READONLY` (default `true`) read in `settings.py`. Every
   write view returns 403 `{detail: "Server is in read-only mode"}` when set.
   This is the actual control — a client-side toggle is not one.
2. `/api/health` reports it; the UI disables its own toggle and says why when
   the server is locked.
3. UI: a header switch, read-only by default on every load (never restored from
   storage — unlocking is a decision, not a preference). Flipping to read/write
   confirms, and shows a persistent banner naming the environment:
   "Write mode — PRODUCTION".
4. Write mode calls `grid.setMode("edit")` with an explicit `editableColumns`
   allow-list per entity (Phase 6); read mode calls `setMode("read")`.

**Check:** with the server locked, no UI path can produce a write request, and
a hand-crafted `curl` write is refused 403. Unlocked, flipping the toggle
changes the grid's editability and nothing else.

## Phase 6 — Editing and MODIFY

1. `editableColumns` per entity, starting deliberately small — what ENA
   actually accepts on a MODIFY:
   - studies: `alias`, `title`, plus study description fields
   - samples: `alias`, `title`, and the sample attribute columns
   - runs, experiments: **none in this phase** — see the gap below
   Accessions and `status` are never editable anywhere.
2. A "Submit changes" button enabled only when
   `getChangeSet().rows.length > 0` (listen to `ena-browser:change`); it shows
   a diff preview — accession, field, before → after — before submitting.
3. `POST /api/records/modify` with `{entity, records: [...], test}`. Delegate
   to `records.modify_records()`, which calls
   `submit_study.submit_batch(..., resubmit_with_modify=True)` /
   `submit_sample.submit_batch(...)`. Do not build XML here.
4. On success: `clearChanges()`, then re-fetch so the grid shows ENA's state
   rather than the optimistic local one. On failure: surface the receipt
   messages and **leave the change set intact** so the user can fix and retry.

**Built differently — and this replaces steps 3 and 4 above.** The gap this
phase opened with ("the toolkit has no MODIFY builder for runs or experiments")
turned out to be the smaller half of the problem. The larger half: a MODIFY
replaces the whole object, and the Reports API returns only alias, accession,
title and status — so *any* submission built from a report row deletes a
study's description and a sample's attributes, studies and samples included.

`records.modify_records()` therefore fetches each record's current XML from
the ENA Browser API with the user's Webin credentials, patches the edited
fields into it, and submits that. A record whose XML cannot be fetched is
reported as failed and never submitted. One generic path covers every entity,
so the toolkit's builders are not used here at all — they build records from
scratch, which is right for a new submission and wrong for editing one field of
an existing one.

What that costs: the editable set is per-entity and small (`_EDITABLE` in
`ena-submission-toolkit`'s `records.py`) — alias and title for studies, samples, experiments and
analyses; alias only for runs; nothing for files. Widening it means adding a
field-to-XML entry and a test, not new machinery.

**Check:** edit an alias against ENA test, submit, confirm the receipt, confirm
the re-fetch shows the new value; edit-then-revert-to-original submits nothing;
a rejected submission keeps the edits; runs and experiments cannot be edited.

## Phase 7 — Lifecycle actions

1. Configure `rowActions` — release, hold, suppress, cancel (`variant: "danger"`
   for cancel) — only in write mode.
2. `ena-browser:row-action` → `POST /api/records/action`
   `{entity, accession, action, hold_until_date?, test}` →
   `WebinClient.submit.{release,hold,suppress,cancel}`.
3. Hold prompts for a date and validates it with the toolkit's
   `common` hold-until-date validation before submitting. Cancel and suppress
   confirm, naming the accession and the environment.
4. Re-fetch after every action; the status column is how the user sees it worked.

**Check:** each action against ENA test, including a rejected one (e.g.
cancelling an already-public record) surfacing ENA's own message.

## Phase 8 — Undo/redo

The element is explicitly built for this: `getState()` / `setState()` is one
JSON-safe snapshot of everything the user can change, and `setState()` stamps
the events it causes with `source: "api"`.

1. `undo.js`: an array of states plus an index. Push a snapshot on every
   `change` / `filter-change` / `layout-change` / `selection-change` event whose
   `detail.source` is **not** `"api"` — that check is what stops a restore from
   pushing itself back onto the stack.
2. Coalesce bursts (a debounce, ~300 ms) so a drag-resize is one undo step, not
   forty.
3. `⌘Z` / `⇧⌘Z` (and `Ctrl`), plus toolbar buttons that disable at the ends of
   the stack. Ignore the shortcut while a grid cell editor is open — Handsontable
   owns undo inside a cell.
4. The stack is per entity and cleared on a successful submission: undoing
   *staged* edits is the point; undoing an ENA submission is not a thing this
   app can do, and the UI must not imply it can.

**Check:** ten edits, ten undos, ten redos, all reproducing exactly; an undo
past a filter change restores the filter; a submission empties the stack and
disables both buttons.

## Phase 9 — Tests and packaging

1. `tests/test_api.py`: every view with `ena_api` patched (mirror the
   assistant's `conftest.py` patching) — credentials required, read-only lock,
   entity mapping, modify delegation with the right flags, action dispatch.
2. `tests/test_ui.py`: Playwright against the running app with the API stubbed
   — credentials panel, entity switching, layout persistence across reload,
   the read/write toggle gating editability, an undo/redo round trip.
   Do **not** re-test the grid itself; filtering, sorting, pinning and selection
   are `ena-browser`'s suite.
3. `task serve` hardening: gunicorn worker count and timeout. **Built
   differently:** `task dev` and `task serve` both bind `127.0.0.1` (override
   with `ENA_BROWSER_HOST`) — the app has no authentication of its own and
   Webin credentials pass through it, so a default of `0.0.0.0` would be wrong.
   No `Dockerfile` or `docker-compose.yml`: neither is needed to run this on a
   laptop, which is the only place it runs today.

**Check:** `task lint` and `task test` are clean; `pre-commit run --all-files`
passes; the README's getting-started sequence works on a clean checkout.

---

## Explicitly not in scope

- Submitting **new** records. This app modifies what exists; creating studies
  and samples is `ena-submission-toolkit` and its callers.
- Read file upload, read↔sample pairing, DataHarmonizer, schema libraries —
  all `mimicc-ena-submission-assistant`.
- Multi-user anything: no accounts, no server-side sessions, no database.
- Re-implementing grid behaviour. Anything the grid should do differently is a
  PR against `ena-browser`, and lands here as a tag bump in `Taskfile.yml`.
