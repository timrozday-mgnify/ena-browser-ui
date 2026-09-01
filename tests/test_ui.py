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
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "server" / "static" / "vendor" / "ena-browser" / "ena-browser.iife.js"

pytestmark = pytest.mark.skipif(
    not BUNDLE.is_file(), reason="ena-browser bundle not vendored — run `task vendor` (or `task vendor:local`)"
)

#: The fetch has landed and the grid holds it.
#:
#: Deliberately not a predicate on `#rowCount`'s text. The page's initial
#: markup says "no records loaded", and the filter-change handler writes
#: "0 of 0 records from TEST" the moment the grid mounts empty — so every
#: substring of a loaded message is already on screen before anything is
#: fetched, and a test racing it pins nothing and asserts on an empty grid.
#: The rows are the thing being waited for, so wait for the rows. Every
#: caller loads an entity the stub answers with rows for.
LOADED = """() => {
    const grid = document.getElementById('grid');
    return !!grid && typeof grid.getRows === 'function' && grid.getRows().length > 0;
}"""

MANIFEST_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<WEBIN><SUBMISSION_SET><SUBMISSION alias="ena-browser-ui-modify">'
    "<ACTIONS><ACTION><MODIFY/></ACTION></ACTIONS></SUBMISSION></SUBMISSION_SET>"
    '<PROJECT_SET><PROJECT accession="PRJEB1" alias="study-one">'
    "<TITLE>Renamed study</TITLE></PROJECT></PROJECT_SET></WEBIN>"
)

UNDO_XML = MANIFEST_XML.replace("Renamed study", "First study")

ROWS = {
    "studies": [
        {"accession": "PRJEB1", "alias": "study-one", "title": "First study", "status": "PRIVATE"},
        {"accession": "PRJEB2", "alias": "study-two", "title": "Second study", "status": "PUBLIC"},
    ],
    "samples": [{"accession": "ERS1", "alias": "sample-one", "title": "A sample", "status": "PRIVATE"}],
}


#: What the record XML carries but the Reports API does not — what
#: /api/records/<entity>/fields answers with.
FIELDS = {
    "PRJEB1": {
        "alias": "study-one",
        "title": "First study",
        # Only the record XML has these; the listing APIs have neither.
        "attr:collection date": "2021-03-01",
        "attr:depth": "10 m",
    }
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
        # The interpreter running the tests, not a hard-coded `.venv`: CI
        # installs into the job's own environment and has no `.venv` to find.
        [sys.executable, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
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
    urls: list[str] = []

    def records(route, request):
        # The fetch criteria ride along as a query string.
        entity = request.url.rsplit("/", 1)[-1].split("?")[0]
        calls.append(entity)
        urls.append(request.url)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"rows": ROWS.get(entity, []), "editable_columns": ["alias", "title"]}),
        )

    def fields(route, request):
        calls.append("fields")
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"fields": FIELDS}),
        )

    page.route("**/api/records/*", records)
    page.route("**/api/records/*/fields", fields)
    page.route(
        "**/api/records/modify/preview",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "success": True,
                    "results": [
                        {
                            "accession": "PRJEB1",
                            "changes": {"title": "Renamed study"},
                            "success": True,
                            "messages": [],
                            "xml": MANIFEST_XML,
                        }
                    ],
                }
            ),
        ),
    )
    page.route(
        "**/api/records/modify",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "success": True,
                    "results": [
                        {
                            "accession": "PRJEB1",
                            "changes": {"title": "Renamed study"},
                            "success": True,
                            "messages": ["INFO: study updated"],
                            "info": ["INFO: study updated"],
                            "warnings": [],
                            "errors": [],
                            "xml": MANIFEST_XML,
                            "previous": {"title": "First study"},
                            "undo_xml": UNDO_XML,
                        }
                    ],
                }
            ),
        ),
    )
    page.goto(app_url)
    page.evaluate(
        "() => sessionStorage.setItem('ena-browser-ui.creds', JSON.stringify({username:'Webin-1', password:'x'}))"
    )
    page.goto(app_url)
    page.wait_for_function(LOADED)
    page.calls = calls  # type: ignore[attr-defined]
    page.urls = urls  # type: ignore[attr-defined]
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


def test_browsing_all_of_ena_switches_source_and_drops_the_other_criteria(app):
    """The Portal API resolves the relationship itself and knows only public
    data, so the criteria it cannot answer are disabled rather than sent."""
    app.fill("#qSearch", "gut metagenome")
    app.select_option("#qSource", "ena")
    app.fill("#qLinked", "PRJEB1787")
    app.press("#qLinked", "Enter")
    app.wait_for_function("() => document.getElementById('rowCount').textContent.includes('from ENA')")

    assert "source=ena" in app.urls[-1]
    assert "linked_to=PRJEB1787" in app.urls[-1]
    assert "search=" not in app.urls[-1]
    for selector in ("#qSearch", "#qUnlinked", "#qStatus", "#qFullFields"):
        assert app.locator(selector).is_disabled(), selector


def test_public_records_are_never_editable(app):
    """ENA would refuse a MODIFY of a record this account does not own, so
    write mode must not offer the edit while browsing them."""
    enable_write_mode(app)
    app.select_option("#qSource", "ena")
    app.wait_for_function("() => document.getElementById('grid').config.mode === 'read'")
    assert app.evaluate("() => document.getElementById('grid').config.rowActions.length") == 0


def test_switching_tab_fetches_the_other_entity(app):
    app.click("#tabs button[data-entity='samples']")
    app.wait_for_function("() => document.getElementById('grid').getRows().length === 1")
    # The rows, then the fields only their XML has.
    assert app.calls[-2:] == ["samples", "fields"]


def test_criteria_ride_along_with_the_fetch(app):
    """The search bars are criteria on the request, not the grid's own filters."""
    app.fill("#qSearch", "gut metagenome")
    app.fill("#qLinked", "PRJEB1234")
    app.press("#qSearch", "Enter")
    app.wait_for_function("() => document.getElementById('rowCount').textContent.includes('matching')")
    assert "search=gut+metagenome" in app.urls[-1]
    assert "linked_to=PRJEB1234" in app.urls[-1]

    app.click("#qClear")
    app.wait_for_function("() => !document.getElementById('rowCount').textContent.includes('matching')")
    assert "?" not in app.urls[-1]


def test_criteria_survive_a_tab_change(app):
    app.check("#qUnlinked")
    app.wait_for_function("() => document.getElementById('rowCount').textContent.includes('matching')")
    app.click("#tabs button[data-entity='samples']")
    app.wait_for_function("() => document.getElementById('grid').getRows().length === 1")
    assert app.urls[-1].endswith("/api/records/samples?unlinked=true")


# --- the read/write gate ----------------------------------------------------


def test_read_only_is_the_default_and_submit_is_disabled(app):
    assert app.evaluate("() => document.getElementById('grid').config.mode") == "read"
    assert app.locator("#submit").is_disabled()
    assert app.locator("#writeToggle").is_checked() is False


def test_write_mode_unlocks_editing_and_row_actions(app):
    enable_write_mode(app)
    assert app.evaluate("() => document.getElementById('grid').config.rowActions.length") == 4
    assert "Write mode — TEST" in app.locator("#banner").text_content()


def test_the_fields_only_the_record_xml_has_are_fetched_in_read_mode_too(app):
    # Not gated on write mode: a sample's checklist attributes are most of what
    # it says, and they are in the XML whether or not anyone is editing.
    assert "fields" in app.calls
    row = app.evaluate("() => document.getElementById('grid').getRows()[0]")
    assert row["attr:collection date"] == "2021-03-01"
    assert row["attr:depth"] == "10 m"


def test_the_attribute_columns_are_not_editable(app):
    enable_write_mode(app)
    assert app.calls[-1] == "fields"
    editable = app.evaluate("() => document.getElementById('grid').config.editableColumns")
    assert editable == ["alias", "title"]
    assert not any(column.startswith("attr:") for column in editable)


def test_write_mode_is_not_remembered_across_a_reload(app):
    enable_write_mode(app)
    app.reload()
    app.wait_for_function(LOADED)
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
    app.wait_for_function(LOADED)
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
    # Entering write mode re-reads the rows, this time with the fields that
    # only exist in the record XML; the grid flips to "edit" when that lands.
    page.wait_for_function("() => document.getElementById('grid').config.mode === 'edit'")


def edit_cell(page, current, new):
    cell = page.locator("ena-browser td", has_text=current).first
    cell.dblclick()
    page.keyboard.press("Meta+A")
    page.keyboard.type(new)
    page.keyboard.press("Enter")


def generate_manifests(page):
    page.wait_for_function("() => !document.getElementById('generate').disabled")
    page.click("#generate")
    page.wait_for_function("() => !document.getElementById('submit').disabled")


def test_a_staged_edit_cannot_be_submitted_until_its_manifest_is_built(app):
    enable_write_mode(app)
    edit_cell(app, "First study", "Renamed study")
    app.wait_for_function("() => !document.getElementById('generate').disabled")
    # The whole point: an edit alone does not unlock submission.
    assert app.locator("#submit").is_disabled()
    assert "no manifests for these edits yet" in app.locator("#manifestState").text_content()

    app.click("#generate")
    app.wait_for_function("() => !document.getElementById('submit').disabled")
    assert "PRJEB1" in app.locator("#manifests").text_content()
    assert "MODIFY" in app.locator("#manifests pre").text_content()


def test_editing_again_re_locks_submission(app):
    enable_write_mode(app)
    edit_cell(app, "First study", "Renamed study")
    generate_manifests(app)
    edit_cell(app, "Second study", "Renamed again")
    app.wait_for_function("() => document.getElementById('submit').disabled")
    assert "no manifests for these edits yet" in app.locator("#manifestState").text_content()


def test_a_submission_is_logged_verbatim_with_what_ena_said(app):
    enable_write_mode(app)
    edit_cell(app, "First study", "Renamed study")
    generate_manifests(app)
    app.click("#submit")
    app.click("#diffDialog button[value='ok']")
    app.wait_for_function("() => document.getElementById('log').children.length === 1")
    log = app.locator("#log").text_content()
    assert "PRJEB1" in log and "accepted" in log
    assert "INFO: study updated" in log
    assert "byte-for-byte the manifest reviewed" in log


def test_an_edit_is_staged_shown_as_a_diff_and_submitted(app):
    enable_write_mode(app)
    edit_cell(app, "First study", "Renamed study")
    generate_manifests(app)

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
    generate_manifests(app)
    app.click("#submit")
    app.click("#diffDialog button[value='cancel']")
    app.wait_for_timeout(300)
    assert submitted == []
    assert app.locator("#submit").is_enabled()  # the edits are still staged


def test_undo_takes_back_a_staged_edit(app):
    enable_write_mode(app)
    edit_cell(app, "First study", "Renamed study")
    generate_manifests(app)

    app.click("#undo")
    assert app.evaluate("() => document.getElementById('grid').getChangeSet().rows.length") == 0
    assert app.locator("#submit").is_disabled()

    app.click("#redo")
    assert app.evaluate("() => document.getElementById('grid').getChangeSet().rows.length") == 1
    # Back to the edit the manifests were built from, so submission unlocks again.
    app.wait_for_function("() => !document.getElementById('submit').disabled")


# --- change history ---------------------------------------------------------


def submit_an_edit(app):
    enable_write_mode(app)
    edit_cell(app, "First study", "Renamed study")
    generate_manifests(app)
    app.click("#submit")
    app.click("#diffDialog button[value='ok']")
    app.wait_for_function("() => document.getElementById('history').children.length === 1")


def test_an_accepted_change_becomes_a_history_entry_holding_its_documents(app):
    submit_an_edit(app)
    entry = app.locator("#history details").first
    summary = entry.locator("summary").text_content()
    assert "PRJEB1" in summary and "studies" in summary and "title" in summary

    entry.locator("summary").click()  # the contents are behind the click
    body = entry.locator(".body").text_content()
    assert "First study" in body and "Renamed study" in body
    assert "Renamed study</TITLE>" in body  # the document that was sent
    assert "First study</TITLE>" in body  # the manifest that would undo it


def test_the_history_survives_a_reload(app, app_url):
    submit_an_edit(app)
    app.goto(app_url)
    app.wait_for_function(LOADED)
    assert app.locator("#history details").count() == 1


def test_reverting_submits_the_values_ena_held_before(app):
    submit_an_edit(app)
    sent: list[dict] = []
    app.route(
        "**/api/records/modify",
        lambda route: (
            sent.append(route.request.post_data_json),
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "success": True,
                        "results": [
                            {
                                "accession": "PRJEB1",
                                "changes": {"title": "First study"},
                                "success": True,
                                "messages": ["INFO: study updated"],
                                "info": ["INFO: study updated"],
                                "warnings": [],
                                "errors": [],
                                "xml": UNDO_XML,
                                "previous": {"title": "Renamed study"},
                                "undo_xml": MANIFEST_XML,
                            }
                        ],
                    }
                ),
            ),
        )[-1],
    )
    # enable_write_mode() left a dialog handler accepting the revert confirm.
    app.click("#history details:first-child summary")
    app.click("#history button[data-revert]")
    app.wait_for_function("() => document.getElementById('history').children.length === 2")

    assert sent == [{"entity": "studies", "records": [{"accession": "PRJEB1", "changes": {"title": "First study"}}]}]
    # The revert is itself a change on the stack, and the change it undid is
    # marked so it cannot be reverted twice.
    assert "revert" in app.locator("#history details").first.locator("summary").text_content()
    assert app.locator("#history button[data-revert]").last.is_disabled()


def test_reverting_needs_write_mode(app):
    submit_an_edit(app)
    app.uncheck("#writeToggle")
    app.click("#history details:first-child summary")
    app.click("#history button[data-revert]")
    app.wait_for_function("() => document.getElementById('banner').classList.contains('bad')")
    assert "write mode" in app.locator("#banner").text_content()
