"""Playwright tests for the page's wiring — not for the grid itself.

Filtering, sorting, pinning and cell editing are `ena-browser`'s own test
suite. What is tested here is what this app adds: credentials, entity tabs,
the read/write gate, layout persistence and undo/redo.

The ENA-facing endpoints are stubbed in the browser, so nothing reaches ENA
and the rows are deterministic.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "server" / "static" / "vendor" / "ena-browser" / "ena-browser.iife.js"

pytestmark = pytest.mark.skipif(
    not BUNDLE.is_file(), reason="ena-browser bundle not vendored — run `task vendor` (or `task vendor:local`)"
)

ROWS = {
    "studies": [
        {"accession": "PRJEB1", "alias": "study-one", "title": "First study", "status": "PRIVATE"},
        {"accession": "PRJEB2", "alias": "study-two", "title": "Second study", "status": "PUBLIC"},
    ],
    "samples": [{"accession": "ERS1", "alias": "sample-one", "title": "A sample", "status": "PRIVATE"}],
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def app_url() -> str:
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": "server", "ENA_BROWSER_READONLY": "false"}
    process = subprocess.Popen(
        [str(ROOT / ".venv" / "bin" / "python"), "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)
    else:  # pragma: no cover
        process.terminate()
        pytest.fail("the app did not start")
    yield url
    process.terminate()
    process.wait(timeout=10)


@pytest.fixture
def app(page, app_url):
    """The page with the ENA-facing endpoints stubbed and credentials set."""
    calls: list[str] = []

    def records(route, request):
        entity = request.url.rsplit("/", 1)[-1]
        calls.append(entity)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"rows": ROWS.get(entity, []), "editable_columns": ["alias", "title"]}),
        )

    page.route("**/api/records/*", records)
    page.route(
        "**/api/records/modify",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"success": True, "results": [{"accession": "PRJEB1", "success": True, "messages": []}]}),
        ),
    )
    page.goto(app_url)
    page.evaluate(
        "() => sessionStorage.setItem('ena-browser-ui.creds', JSON.stringify({username:'Webin-1', password:'x'}))"
    )
    page.goto(app_url)
    page.wait_for_function("() => document.getElementById('rowCount').textContent.includes('records')")
    page.calls = calls  # type: ignore[attr-defined]
    return page


def state(page, expression="s => s"):
    return page.evaluate(f"() => ({expression})(document.getElementById('grid').getState())")


# --- the page itself --------------------------------------------------------


def test_the_element_is_registered_and_the_grid_mounts(page, app_url):
    page.goto(app_url)
    assert page.evaluate("() => !!customElements.get('ena-browser')")
    assert page.locator("ena-browser .handsontable").count() >= 1


def test_without_credentials_the_page_asks_for_them(page, app_url):
    page.goto(app_url)
    assert page.locator("#credPanel").is_visible()
    assert "credentials: not set" in page.locator("#credStatus").text_content()


# --- loading ----------------------------------------------------------------


def test_records_load_and_are_counted(app):
    assert app.evaluate("() => document.getElementById('grid').getRows().length") == 2
    assert "2 records from TEST" in app.locator("#rowCount").text_content()


def test_switching_tab_fetches_the_other_entity(app):
    app.click("#tabs button[data-entity='samples']")
    app.wait_for_function("() => document.getElementById('grid').getRows().length === 1")
    assert app.calls[-1] == "samples"


# --- the read/write gate ----------------------------------------------------


def test_read_only_is_the_default_and_submit_is_disabled(app):
    assert app.evaluate("() => document.getElementById('grid').config.mode") == "read"
    assert app.locator("#submit").is_disabled()
    assert app.locator("#writeToggle").is_checked() is False


def test_write_mode_unlocks_editing_and_row_actions(app):
    app.on("dialog", lambda dialog: dialog.accept())
    app.click("#writeToggle")
    assert app.evaluate("() => document.getElementById('grid').config.mode") == "edit"
    assert app.evaluate("() => document.getElementById('grid').config.rowActions.length") == 4
    assert "Write mode — TEST" in app.locator("#banner").text_content()


def test_write_mode_is_not_remembered_across_a_reload(app):
    app.on("dialog", lambda dialog: dialog.accept())
    app.click("#writeToggle")
    app.reload()
    app.wait_for_function("() => document.getElementById('rowCount').textContent.includes('records')")
    assert app.locator("#writeToggle").is_checked() is False
    assert app.evaluate("() => document.getElementById('grid').config.mode") == "read"


# --- layout persistence -----------------------------------------------------


def pin_accession(page):
    """Pin a column the way the grid would, then tell the app a user did it."""
    page.evaluate(
        "() => { const g = document.getElementById('grid');"
        " g.setLayout({ pinned: ['accession'] });"
        " g.dispatchEvent(new CustomEvent('ena-browser:layout-change',"
        "   { detail: { layout: g.getLayout(), source: 'user' } })); }"
    )


def test_layout_survives_a_reload_but_rows_are_refetched(app):
    pin_accession(app)
    app.wait_for_timeout(600)  # the debounced save
    before = len(app.calls)
    app.reload()
    app.wait_for_function("() => document.getElementById('rowCount').textContent.includes('records')")
    assert state(app, "s => s.layout.pinned") == ["accession"]
    assert len(app.calls) > before  # re-fetched, not restored from storage


def test_layouts_are_per_entity(app):
    pin_accession(app)
    app.wait_for_timeout(600)
    app.click("#tabs button[data-entity='samples']")
    app.wait_for_function("() => document.getElementById('grid').getRows().length === 1")
    assert state(app, "s => s.layout.pinned || []") == []


# --- undo/redo --------------------------------------------------------------


def test_undo_and_redo_round_trip_a_layout_change(app):
    assert app.locator("#undo").is_disabled()
    pin_accession(app)
    app.wait_for_function("() => !document.getElementById('undo').disabled")

    app.click("#undo")
    assert state(app, "s => s.layout.pinned || []") == []
    assert app.locator("#undo").is_disabled()

    app.click("#redo")
    assert state(app, "s => s.layout.pinned") == ["accession"]
    assert app.locator("#redo").is_disabled()


def test_a_state_restore_does_not_push_itself_onto_the_stack(app):
    pin_accession(app)
    app.wait_for_function("() => !document.getElementById('undo').disabled")
    app.click("#undo")
    app.wait_for_timeout(500)  # long enough for a stray push to land
    # Still exactly one step of history either side, i.e. the restore was
    # recognised as source:"api" and ignored.
    assert app.locator("#undo").is_disabled()
    assert app.locator("#redo").is_enabled()


def test_loading_an_entity_starts_a_fresh_history(app):
    pin_accession(app)
    app.wait_for_function("() => !document.getElementById('undo').disabled")
    app.click("#tabs button[data-entity='samples']")
    app.wait_for_function("() => document.getElementById('grid').getRows().length === 1")
    assert app.locator("#undo").is_disabled()
    assert app.locator("#redo").is_disabled()


# --- editing and submitting -------------------------------------------------


def enable_write_mode(page):
    page.on("dialog", lambda dialog: dialog.accept())
    page.click("#writeToggle")


def edit_cell(page, current, new):
    cell = page.locator("ena-browser td", has_text=current).first
    cell.dblclick()
    page.keyboard.press("Meta+A")
    page.keyboard.type(new)
    page.keyboard.press("Enter")


def test_an_edit_is_staged_shown_as_a_diff_and_submitted(app):
    enable_write_mode(app)
    edit_cell(app, "First study", "Renamed study")
    app.wait_for_function("() => !document.getElementById('submit').disabled")

    app.click("#submit")
    assert app.locator("#diffDialog").is_visible()
    assert "PRJEB1" in app.locator("#diffTable").text_content()
    assert "Renamed study" in app.locator("#diffTable").text_content()

    app.click("#diffDialog button[value='ok']")
    app.wait_for_function("() => document.getElementById('banner').classList.contains('ok')")
    assert "Submitted 1 change" in app.locator("#banner").text_content()
    # A successful submission re-fetches, so nothing is left staged.
    app.wait_for_function("() => document.getElementById('submit').disabled")


def test_cancelling_the_diff_dialog_submits_nothing(app):
    submitted = []
    app.route("**/api/records/modify", lambda route: submitted.append(route.request.url))
    enable_write_mode(app)
    edit_cell(app, "First study", "Renamed study")
    app.wait_for_function("() => !document.getElementById('submit').disabled")
    app.click("#submit")
    app.click("#diffDialog button[value='cancel']")
    app.wait_for_timeout(300)
    assert submitted == []
    assert app.locator("#submit").is_enabled()  # the edits are still staged


def test_undo_takes_back_a_staged_edit(app):
    enable_write_mode(app)
    edit_cell(app, "First study", "Renamed study")
    app.wait_for_function("() => !document.getElementById('submit').disabled")

    app.click("#undo")
    assert app.evaluate("() => document.getElementById('grid').getChangeSet().rows.length") == 0
    assert app.locator("#submit").is_disabled()

    app.click("#redo")
    assert app.evaluate("() => document.getElementById('grid').getChangeSet().rows.length") == 1
    assert app.locator("#submit").is_enabled()
