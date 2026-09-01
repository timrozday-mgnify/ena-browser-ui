# ena-browser-ui

A standalone web app for **browsing and editing the records held under an ENA
Webin account** — studies, samples, reads (runs) and experiments.

It is the application that
[`ena-browser`](https://github.com/timrozday-mgnify/ena-browser)'s demo page
only hints at: enter your Webin credentials, pick an entity, get a filterable,
sortable grid of everything ENA holds for you, and — when you deliberately
unlock write mode — edit cells, stage the changes, undo/redo them, and submit
them to ENA as a MODIFY.

```
browser                                  this app (Django)              ENA
┌──────────────────────────┐             ┌────────────────┐     ┌────────────────┐
│ <ena-browser> grid       │  X-Webin-*  │ /api/records/* │ ──► │ Reports API    │
│ tabs, creds, undo/redo   │ ──────────► │ /api/records/  │     │ Submission API │
│ read-only ⇄ read/write   │             │   modify|action│ ──► │ (MODIFY, etc.) │
└──────────────────────────┘             └────────────────┘     └────────────────┘
```

Status: **implemented.** All nine phases of
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) are built and tested (38 tests:
pytest with ENA faked out, Playwright with the API stubbed); that file's
**Progress** table maps each phase to the code. The one deviation worth knowing
about — how a MODIFY is built — is
[How a change reaches ENA](#how-a-change-reaches-ena). Not built, deliberately:
no container image.

---

## Why it exists

`ena-browser` is a view element, not an application: it renders rows and hands
back a change set. `mimicc-ena-submission-assistant` is an application, but a
MIMICC-specific submission pipeline — DataHarmonizer templates, read↔sample
pairing, a schema library — where record browsing is one tab among many.

This repo is the middle: the generic "look at, and fix, what is in my Webin
account" tool, with none of the MIMICC submission machinery, reusing the same
libraries so anything learned here transfers back.

## What it does

| | |
|---|---|
| **Credentials** | Webin ID + password entered in the page, held in `sessionStorage` for that tab only, sent as `X-Webin-Username` / `X-Webin-Password` headers on each request. Never written to disk, never stored server-side. Test/production is a switch in the header. |
| **Entities** | Studies, samples, reads (runs) and experiments as first-class tabs; analyses and files come free from the element's entity defaults, read-only. |
| **Read processing status** | Read rows carry ENA's file-processing status (`process_status`, `process_date`, `process_error`) alongside the release status, so "submitted" and "archived" are visibly different things. |
| **Browsing** | Per-column filters, multi-column sort, pin/hide/reorder/resize columns, include or exclude cancelled and suppressed records. All of this is the element's, not ours. |
| **Fetch criteria** | A row of search bars above the grid: free text across every column, *linked to* an accession (the samples in a study, the reads for a sample — resolved through the experiments that join them), *unlinked only* (samples with no experiment or read against them), and a release status. These are criteria on the **request**, not the grid's client-side column filters, and they stay put when you change tab. ENA answers none of them itself — the Reports API has no search and no relational query — so `ena_submission_toolkit.records.list_records` applies them over the report rows. |
| **All fields** | *all fields* (on by default) also asks the ENA **Portal API** (via [`ena-api-handler`](https://github.com/EBI-Metagenomics/ena-api-handler)) for everything it indexes about the listed records — around 200 columns for a run (instrument, library strategy, collection date, the archived files' FTP paths) against the Reports API's five. They arrive **hidden**: the grid shows each entity's default columns and the rest wait in the *Columns* menu to be ticked on. Best-effort and production-only — the Portal has no test environment and does not know about a record it has not indexed, and anything it cannot answer for simply keeps its report columns. Costs one request per 50 records, so untick it on a large account. |
| **Checklist attributes** | With *all fields* on, each record's own submitted XML is read too, and its TAG/VALUE attribute list — a sample's collection date, host, isolation source, whatever its checklist defines — arrives as `attr:`-prefixed columns. Unlike the Portal's extras these are **shown**, not hidden behind the *Columns* menu, and titled by their tag alone: they are what the record actually says. Editable in write mode — a MODIFY addresses an attribute by its tag and keeps its unit in `<UNITS>` — except ENA's own `ENA-*` bookkeeping tags, and any tag the record does not already carry (adding one means knowing where it may legally sit and whether the record's checklist knows it, so the server refuses rather than guess). |
| **My account ⇄ all of ENA** | The Reports API is scoped by **ownership, not by release status**: it lists everything registered under these credentials, private and public alike, and nothing anybody else submitted. The *source* selector switches to the **Portal API**, which is keyed by accession instead, so it reaches any record in ENA. It has no listing of its own — there is no "everything" to ask for — so it needs an accession to search from: *all of ENA* + `PRJEB1787` gives that study's public runs, samples or experiments, whoever submitted them. Always read-only: ENA would refuse a MODIFY of a record this account does not own, so the page does not offer the edit. Production only, and the criteria the Portal cannot answer (free text, status, *unlinked only*) grey out. |
| **Read-only ⇄ read/write** | One explicit toggle. Read-only is the default and the server refuses writes outright unless `ENA_BROWSER_READONLY=false`, so a mis-click in the UI cannot reach ENA. |
| **Modifications** | In write mode, edits to an allow-list of fields accumulate into a change set. Submitting is deliberately a two-step act: **generate the MODIFY manifests**, read the XML that would go to ENA in the panel under the grid, then submit. Accessions and status are never editable — status changes go through lifecycle actions. |
| **Manifest inspection** | The *MODIFY manifests* panel holds one entry per record: the fields changed, and the full submission document built from the record as ENA currently holds it. Submit stays locked until manifests exist for the current edits, and any further edit re-locks it. |
| **Submission log** | The *Submission log* panel keeps every MODIFY and lifecycle action this tab has sent: what ENA said (info, warnings, errors, verbatim), the document that was actually sent, and whether it matches the manifest that was reviewed. |
| **Change history** | Every MODIFY ENA accepted from this browser is pushed onto a stack under the grid, newest first. Click an entry for its contents: the fields it changed (before → after), the document that was sent, and the manifest that would undo it. *Revert this change* re-submits the values ENA held immediately before it — `ena_submission_toolkit.records` returns them with every result — as a fresh MODIFY built from the record as ENA holds it *now*, so a revert puts back the fields it changed and leaves any later edit alone. The revert is itself an entry on the stack, so it can be reverted in turn. Survives a reload (`localStorage`); it holds accessions, field values and XML, never credentials. |
| **Lifecycle actions** | Release, hold, suppress, cancel per row, via `ena-api-client`'s submission endpoints. Write-gated like every other write. |
| **Undo/redo** | A host-side stack over the element's `getState()` / `setState()` — edits, filters, sort, layout and selection. `⌘Z` / `⇧⌘Z`. It undoes *staged* work; anything already submitted to ENA is outside it and belongs to **Change history** above. |
| **Layout persistence** | Column layout, filters, sort and the active entity survive a reload (`localStorage`). Rows never persist — they are re-fetched, so a stale status can't be shown. |

## Stack

Deliberately the same shape as `mimicc-ena-submission-assistant`, so the two
stay swappable and the pinning story is identical.

| Concern | Choice | Why |
|---|---|---|
| Backend | **Django 5**, no DB, no auth, no sessions, no CSRF | A thin, stateless proxy. Same as the assistant's `server/`, minus everything it needs that this doesn't. |
| Why a backend at all | CORS + XML | The browser cannot call the Webin Reports API directly (its own adapter documents the CORS rejection), and MODIFY means building and validating XML — which is `ena-submission-toolkit`'s job, in Python. |
| ENA transport | **`ena-api-client`** (`WebinClient.reports.*`, `.submit.*`) | Already typed, already pinned elsewhere. |
| MODIFY payloads | Fetch the record's XML, patch it, submit it back (see below) | `ena-submission-toolkit`'s builders construct a record from scratch, which is right for a new submission and wrong for editing one field of an existing one. |
| Frontend | **Vanilla JS + the vendored `ena-browser` IIFE bundle** | No npm build step in this repo, exactly like the assistant's `server/static/`. The element ships an IIFE with Handsontable bundled precisely for this. |
| Serving | `manage.py runserver` locally, **gunicorn** as a server | `task dev` / `task serve`. |
| Tests | **pytest** for the API layer, **Playwright** for the UI | Same as the assistant. The grid's own mechanics are `ena-browser`'s test suite, not ours. |
| Task runner | **Taskfile** | `task venv`, `vendor`, `dev`, `serve`, `test`, `lint`. |

### Pinned dependency versions

| Dependency | Pin | Where |
|---|---|---|
| `ena-api-client` | `v0.1.0` | `pyproject.toml` |
| `ena-submission-toolkit` | `v0.1.0` | `pyproject.toml` |
| `ena-browser` | `v0.1.0` | `Taskfile.yml` (`ENA_BROWSER_REF`) — `task vendor` downloads that tag's release assets |

The `ena-browser` bundle is a build artefact, not a source dependency: it is
downloaded into `server/static/vendor/ena-browser/` and gitignored. Re-run
`task vendor` after bumping the tag.

## Getting started

```bash
task venv
```

```bash
task vendor
```

```bash
task dev
```

Then open <http://127.0.0.1:9200>, enter a Webin ID and password, and load an
entity. Leave the **test** switch on until you have a reason not to.

To run it as a server instead:

```bash
task serve
```

Configuration is environment-only — see [.env.example](.env.example). Note
that **the server starts read-only**: to make changes, set
`ENA_BROWSER_READONLY=false` (in `.env` or the environment) *and* switch the UI
into write mode. The UI's toggle is disabled while the server is locked.

## How a change reaches ENA

An ENA MODIFY **replaces the whole object**, and the Reports API returns only
a handful of fields per record — alias, accession, title, status. Building the
submission from a report row would therefore silently delete everything ENA
holds but does not report: a study's description, a sample's attributes.

So an edit is applied like this, in `ena_submission_toolkit.records.modify_records()`
(this app has no ENA code of its own — see [Where the ENA code lives](#where-the-ena-code-lives)):

1. `GET /ena/browser/api/xml/<accession>` with the user's Webin credentials —
   which is what makes a *private* record readable — returning the record as
   ENA currently holds it, in full.
2. The edited fields are patched into that document. Which fields those are,
   and where each one lives in the XML, is the `_EDITABLE` table: an attribute
   or a direct child element, nothing more clever.
3. The patched record is wrapped in a `WEBIN`/`MODIFY` submission and posted.

Steps 1–3 are also reachable without step 3's post: `preview_modify_records()`
builds the same documents and returns them, which is what
`POST /api/records/modify/preview` and the manifests panel use. The submission
that follows runs the identical builder, and its results carry the document it
sent — so the log can say whether what went to ENA is byte-for-byte what was
reviewed, rather than asking anyone to take that on trust.

A MODIFY replaces a record. Making it a button next to an edited cell would
make it feel like saving a spreadsheet, which it is not: nothing is sent until
the manifests have been generated, and any edit after that invalidates them.

If step 1 fails, the record is reported as failed and **nothing is submitted**.
A partial document is worse than no submission.

`files` are not editable at all, and no entity is editable in every field: the
table only carries fields this patcher can change without guessing at XSD
element ordering. Widening it is a matter of adding entries to `_EDITABLE`
in `ena-submission-toolkit` (and a test), not new machinery.

### Reads and experiments

A run's title and an experiment's design, library and instrument are editable,
which raises a problem the other entities do not have: the Reports API never
returns those fields, so there is no cell in the grid to edit them in. Write
mode therefore re-reads the rows and asks
`POST /api/records/<entity>/fields` for them
(`records.read_editable_fields()`), which pulls them out of the same Browser
API documents a MODIFY patches — one request per 100 accessions — and merges
them into the rows. What you edit is what ENA currently holds.

Read-only browsing pays nothing for this: the fetch only happens in write mode.

| Entity | Editable |
|---|---|
| Studies, samples, analyses | `alias`, `title` |
| Reads (runs) | `alias`, `title`, `run_center`, `run_date` |
| Experiments | `alias`, `title`, `design_description`, `library_name`, `library_strategy`, `library_source`, `library_selection`, `instrument_model` |
| Files | — |

An experiment's `instrument_model` is patched inside whichever platform block
the experiment was registered with, so the model can be corrected but the
platform cannot be swapped. `LIBRARY_LAYOUT` (single vs paired) is structural
and deliberately absent.

## Has ENA processed my reads?

Registering a run and archiving its read files are two different events, and
`/report/runs` only answers the first. Read rows therefore also carry
`process_status`, `process_date` and `process_error`, merged in from the
Reports API's `/report/run-process` — ENA's own vocabulary
(`COMPLETED`, `IN_PROGRESS`, `ERROR`, ...), passed through verbatim rather
than mapped onto anything of ours. It is a column like any other: filter and
sort on it to find the reads still in the queue, or the ones that failed.

Nothing else pays for it — the extra report is fetched for reads only.

## Where the ENA code lives

Not here. Every ENA request this app makes is made by a shared library, so the
same behaviour is available to `mimicc-ena-submission-assistant` and to
anything else built on this stack:

| Layer | Repo | What it owns |
|-------|------|--------------|
| Transport (Webin) | [`ena-api-client`](https://github.com/timrozday-mgnify/ena-api-client) | `client.submit` (Submission API), `client.reports` (Reports API), `client.browser.xml()` / `.xml_many()` (a record's current XML), `reports.list_run_processes()` (read-file processing status) |
| Transport (Portal) | [`ena-api-handler`](https://github.com/EBI-Metagenomics/ena-api-handler) | `ENAClient.search()` over the ENA Portal API, with generated per-result models. Not re-implemented in `ena-api-client`: the Portal already has a maintained typed client, and `ena-api-client`'s remit is the Webin account APIs. |
| Behaviour | [`ena-submission-toolkit`](https://github.com/timrozday-mgnify/ena-submission-toolkit) | `records.list_records` / `read_editable_fields` / `modify_records` / `record_action` / `editable_columns` / `validate_credentials`, plus `Credentials` and the `webin_client` context manager; `portal.search_public` / `fields_for_accessions` (which Portal result answers for which entity, turning an accession into a query, merging Portal rows into report rows) |
| View | [`ena-browser`](https://github.com/timrozday-mgnify/ena-browser) | the `<ena-browser>` grid element — rows in, events out, never an ENA request |
| This app | — | HTTP endpoints, the server-side write lock, the action allow-list (no `kill`), the manifest gate in the page, and the page itself |

`server/` therefore holds no ENA logic at all: `views_records.py` parses a
request, checks the lock, calls `records.*` or `portal.*`, and maps the
exception types onto status codes.

## Safety

- Credentials never touch the server's disk, environment or logs; they arrive
  as request headers and are turned into a per-call `WebinClient`.
- Every write path is refused server-side unless `ENA_BROWSER_READONLY=false`.
  The UI toggle is a convenience on top of that, not the control.
- Destructive lifecycle actions (cancel, suppress) confirm in the UI and name
  the accession being acted on.
- Production vs test is shown as a coloured pill in the header at all times.

## Development

```bash
task lint
```

```bash
task test
```

The Playwright tests need browsers and the vendored element bundle:

```bash
.venv/bin/playwright install chromium
```

They skip themselves if the bundle is missing. Until `ena-browser` cuts its
tag, `task vendor:local` copies the build from a sibling `../ena-browser`
checkout — for local work only, never a state to ship.

`pre-commit` runs the lint hooks on commit and pytest on push; `task venv`
installs both hooks. CI runs the same two things on every pull request.

[CONTRIBUTING.md](CONTRIBUTING.md) has the house rules — the ones about not
reaching ENA from a test, not persisting credentials, and not letting a MODIFY
lose data are the ones that matter.
