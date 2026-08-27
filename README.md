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

Status: **planned.** This repo currently holds the tooling and the docs; see
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the phased build.

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
| **Browsing** | Per-column filters, multi-column sort, pin/hide/reorder/resize columns, include or exclude cancelled and suppressed records. All of this is the element's, not ours. |
| **Read-only ⇄ read/write** | One explicit toggle. Read-only is the default and the server refuses writes outright unless `ENA_BROWSER_READONLY=false`, so a mis-click in the UI cannot reach ENA. |
| **Modifications** | In write mode, edits to an allow-list of ENA-modifiable fields accumulate into a change set, which is submitted as a MODIFY through `ena-submission-toolkit`. Accessions and status are never editable — status changes go through lifecycle actions. |
| **Lifecycle actions** | Release, hold, suppress, cancel per row, via `ena-api-client`'s submission endpoints. Write-gated like every other write. |
| **Undo/redo** | A host-side stack over the element's `getState()` / `setState()` — edits, filters, sort, layout and selection. `⌘Z` / `⇧⌘Z`. It undoes *staged* work; anything already submitted to ENA is outside the stack. |
| **Layout persistence** | Column layout, filters, sort and the active entity survive a reload (`localStorage`). Rows never persist — they are re-fetched, so a stale status can't be shown. |

## Stack

Deliberately the same shape as `mimicc-ena-submission-assistant`, so the two
stay swappable and the pinning story is identical.

| Concern | Choice | Why |
|---|---|---|
| Backend | **Django 5**, no DB, no auth, no sessions, no CSRF | A thin, stateless proxy. Same as the assistant's `server/`, minus everything it needs that this doesn't. |
| Why a backend at all | CORS + XML | The browser cannot call the Webin Reports API directly (its own adapter documents the CORS rejection), and MODIFY means building and validating XML — which is `ena-submission-toolkit`'s job, in Python. |
| ENA transport | **`ena-api-client`** (`WebinClient.reports.*`, `.submit.*`) | Already typed, already pinned elsewhere. |
| MODIFY payloads | **`ena-submission-toolkit`** (`submit_study` / `submit_sample`) | The MODIFY path exists there; do not rebuild it. |
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

Configuration is environment-only and optional — see [.env.example](.env.example).

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

`pre-commit` runs the lint hooks on commit and pytest on push; `task venv`
installs both hooks.
