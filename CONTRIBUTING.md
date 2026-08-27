# Contributing

## Setup

```bash
task venv
```

That creates `.venv`, installs the project with its dev extras, and installs
both pre-commit hooks. The Playwright tests additionally need a browser and the
vendored element:

```bash
.venv/bin/playwright install chromium
```

```bash
task vendor
```

Until `ena-browser` cuts its tag, `task vendor:local` copies the bundle from a
sibling `../ena-browser` checkout instead. Local only — never commit or ship
that state. Without either, the Playwright tests skip themselves.

## Checks

| Command | What it does | Runs on |
|---|---|---|
| `task lint` | every pre-commit hook: whitespace/EOF/YAML/TOML hygiene, secret detection, `ruff` lint + format, shellcheck | commit + CI |
| `task test` | the whole pytest suite — API tests with ENA faked out, Playwright tests with the API stubbed | push + CI |
| `task test:ui` | the Playwright tests alone | — |

CI runs the same two things: a `pre-commit` job and a `test` job on Python 3.11
and 3.12.

## House rules

These are the ones worth stating, because getting them wrong is how this app
would do damage rather than merely break:

- **Nothing in the test suite may reach ENA.** `tests/conftest.py` replaces
  `ena_service.webin_client`; a test that needs the ENA Browser API stubs
  `_record_xml` too. If a change makes ENA reachable from a test, the change is
  wrong, not the fixture.
- **Credentials stay in the request.** They arrive as headers, become a
  `Credentials`, and die with the request. Never log them, never persist them,
  never put them in a URL or a query string.
- **Writes stay behind the server lock.** Every write endpoint checks
  `settings.READONLY` before anything else. A new one needs that check and the
  test that proves nothing was submitted while locked.
- **A MODIFY must never lose data.** Edits are applied by patching the
  record's fetched XML — see the README's
  [How a change reaches ENA](README.md#how-a-change-reaches-ena). Adding a
  writable field means an entry in `_EDITABLE` and a test asserting the fields
  it does *not* touch survive the round trip. Building a submission from a
  report row is the bug this design exists to prevent.
- **Grid behaviour belongs to `ena-browser`.** Filtering, sorting, pinning,
  selection and cell editing are its tests, not ours; ours cover the wiring.
  Something the grid should do differently is a PR against that repo, landing
  here as a tag bump in `Taskfile.yml`.
- **Keep the docs true.** `IMPLEMENTATION_PLAN.md` records what was intended
  and where the build diverged. If you change something it describes, change
  it too.

## Pull requests

- Branch off `main`; no direct pushes to `main`.
- Both CI jobs (`pre-commit`, `test`) green before merge.
- Squash merge.

### Branch protection

Set once, by a repo admin:

```bash
gh api -X PUT repos/timrozday-mgnify/ena-browser-ui/branches/main/protection \
  -H "Accept: application/vnd.github+json" \
  -f 'required_status_checks[strict]=true' \
  -f 'required_status_checks[contexts][]=pre-commit' \
  -f 'required_status_checks[contexts][]=test (3.11)' \
  -f 'required_status_checks[contexts][]=test (3.12)' \
  -f 'required_pull_request_reviews[required_approving_review_count]=1' \
  -f 'enforce_admins=false' -f 'restrictions=null'
```
