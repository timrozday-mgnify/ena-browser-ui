"""The HTTP layer and the ENA service, with ENA itself faked out."""

from __future__ import annotations

import json

import pytest
from conftest import HEADERS
from lxml import etree


def post(client, path, payload=None, **extra):
    return client.post(path, data=json.dumps(payload or {}), content_type="application/json", **extra)


# --- health -----------------------------------------------------------------


def test_health_reports_the_write_lock(client, settings):
    settings.READONLY = True
    body = client.get("/api/health").json()
    assert body["readonly"] is True
    assert body["editable_columns"]["studies"] == ["alias", "title"]
    assert body["editable_columns"]["files"] == []


# --- credentials ------------------------------------------------------------


def test_every_ena_endpoint_needs_credentials(client):
    assert client.get("/api/records/studies").status_code == 401
    assert post(client, "/api/credentials/validate").status_code == 401


def test_validate_uses_a_cheap_authenticated_call(client, ena):
    body = post(client, "/api/credentials/validate", **HEADERS).json()
    assert body == {"valid": True, "test": True}
    assert ena.reports.calls == [("projects", 1)]


# --- listing ----------------------------------------------------------------


def test_studies_are_listed_from_the_projects_report(client, ena):
    body = client.get("/api/records/studies", **HEADERS).json()
    assert body["rows"][0]["accession"] == "PRJEB1"
    assert body["editable_columns"] == ["alias", "title"]
    assert ena.reports.calls[0][0] == "projects"


def test_search_filters_the_rows_without_asking_ena_to(client, ena):
    """ENA has no search parameter — the criteria are applied over the rows."""
    assert client.get("/api/records/studies?search=old+title", **HEADERS).json()["rows"]
    assert client.get("/api/records/studies?search=nothing+like+it", **HEADERS).json()["rows"] == []


def test_the_account_listing_is_not_private_only(client, ena):
    """The Reports API is scoped by ownership, not by release status."""
    statuses = {row["status"] for row in client.get("/api/records/studies", **HEADERS).json()["rows"]}
    assert {"PRIVATE", "PUBLIC"} & statuses


# --- listing: all of ENA ----------------------------------------------------


def test_source_ena_searches_the_portal_not_the_reports_api(client, ena, ena_portal):
    ena_portal["rows"] = [{"run_accession": "ERR1", "instrument_model": "NovaSeq"}]
    body = client.get("/api/records/runs?source=ena&linked_to=PRJEB1787", **HEADERS).json()
    assert body["rows"] == ena_portal["rows"]
    assert ena_portal["calls"] == [("runs", "PRJEB1787", "Webin-1")]
    # The account's own reports were never asked.
    assert ena.reports.calls == []


def test_public_records_are_never_editable(client, ena, ena_portal):
    """A MODIFY of a record this account does not own would be refused by ENA."""
    ena_portal["rows"] = [{"run_accession": "ERR1"}]
    body = client.get("/api/records/runs?source=ena&linked_to=PRJEB1787", **HEADERS).json()
    assert body["editable_columns"] == []


def test_searching_ena_needs_an_accession(client, ena, ena_portal):
    response = client.get("/api/records/runs?source=ena", **HEADERS)
    assert response.status_code == 400
    assert "accession" in response.json()["detail"]
    assert ena_portal["calls"] == []


def test_a_portal_failure_is_reported_verbatim(client, ena, ena_portal):
    ena_portal["error"] = ValueError("ENA cannot list studies by 'ERR1'")
    response = client.get("/api/records/studies?source=ena&linked_to=ERR1", **HEADERS)
    assert response.status_code == 400
    assert "ENA cannot list studies" in response.json()["detail"]


def test_lineage_criteria_reach_the_library(client, ena):
    # The fake account holds one record that nothing else points at.
    assert client.get("/api/records/studies?unlinked=true", **HEADERS).json()["rows"]
    assert client.get("/api/records/studies?linked_to=PRJEB999", **HEADERS).json()["rows"] == []
    assert ("experiments", 5000) in ena.reports.calls


def test_no_criteria_costs_no_lineage_lookup(client, ena):
    client.get("/api/records/studies", **HEADERS)
    assert [name for name, _ in ena.reports.calls] == ["projects"]


def test_read_rows_carry_their_processing_status(client, ena):
    body = client.get("/api/records/runs", **HEADERS).json()
    assert ("run-process", 5000) in ena.reports.calls
    # the fake report row is PRJEB1, not ERR1 — a run the report says nothing
    # about must not acquire someone else's status
    assert "process_status" not in body["rows"][0]


def test_editable_fields_are_read_from_the_record_xml(client, ena, record_xml):
    record_xml["xml"] = b'<RUN_SET><RUN alias="run-a" accession="ERR1"><TITLE>A title</TITLE></RUN></RUN_SET>'
    body = post(client, "/api/records/runs/fields", {"accessions": ["ERR1"]}, **HEADERS).json()
    assert body["fields"]["ERR1"] == {"alias": "run-a", "title": "A title"}
    assert record_xml["fetched"] == ["ERR1"]
    # From the account's own copy: the Browser API has no private record to
    # give, and a submitter's records are private until released.
    assert record_xml["entities"] == ["runs"]


def test_editable_fields_are_a_read_not_a_write(client, ena, settings, record_xml):
    settings.READONLY = True
    record_xml["xml"] = b'<RUN_SET><RUN alias="run-a" accession="ERR1"/></RUN_SET>'
    assert post(client, "/api/records/runs/fields", {"accessions": ["ERR1"]}, **HEADERS).status_code == 200


def test_editable_fields_needs_an_accession_list(client, ena):
    response = post(client, "/api/records/runs/fields", {}, **HEADERS)
    assert response.status_code == 400
    assert "accessions" in response.json()["detail"]


def test_unknown_entity_is_a_400_not_a_crash(client, ena):
    response = client.get("/api/records/plasmids", **HEADERS)
    assert response.status_code == 400
    assert "plasmids" in response.json()["detail"]


def test_the_test_flag_chooses_the_ena_environment(client, ena):
    client.get("/api/records/studies", HTTP_X_ENA_TEST="false", **HEADERS)
    assert ena.test is False


# --- the write lock ---------------------------------------------------------


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/api/records/modify", {"entity": "studies", "records": [{"accession": "PRJEB1", "changes": {"title": "x"}}]}),
        (
            "/api/records/modify/preview",
            {"entity": "studies", "records": [{"accession": "PRJEB1", "changes": {"title": "x"}}]},
        ),
        ("/api/records/action", {"accession": "PRJEB1", "action": "cancel"}),
    ],
)
def test_read_only_server_refuses_writes_and_sends_nothing(client, ena, settings, path, payload):
    settings.READONLY = True
    response = post(client, path, payload, **HEADERS)
    assert response.status_code == 403
    assert ena.submit.documents == [] and ena.submit.calls == []


# --- modify -----------------------------------------------------------------


@pytest.fixture
def writable(settings):
    settings.READONLY = False


def modify(client, changes, entity="studies", accession="PRJEB1"):
    return post(
        client,
        "/api/records/modify",
        {"entity": entity, "records": [{"accession": accession, "changes": changes}]},
        **HEADERS,
    )


def test_modify_patches_the_fetched_xml_and_keeps_everything_else(client, ena, record_xml, writable):
    body = modify(client, {"title": "new title", "alias": "new-alias"}).json()
    assert body["success"] is True

    document = etree.fromstring(ena.submit.documents[0])
    assert document.find(".//ACTIONS/ACTION/MODIFY") is not None
    project = document.find(".//PROJECT")
    assert project.get("alias") == "new-alias"
    assert project.findtext("TITLE") == "new title"
    # The whole point of fetching first: fields the Reports API never returns
    # must survive the round trip instead of being dropped.
    assert project.findtext("DESCRIPTION") == "a description the Reports API never returns"
    assert project.findtext("NAME") == "a name"
    # and the caller is told exactly what went out, not just that it worked
    assert body["results"][0]["xml"].encode() == ena.submit.documents[0]


def test_a_non_editable_field_is_refused_without_submitting(client, ena, record_xml, writable):
    body = modify(client, {"status": "PUBLIC"}).json()
    assert body["success"] is False
    assert "not editable" in body["results"][0]["messages"][0]
    assert ena.submit.documents == []


def test_a_record_whose_xml_cannot_be_read_is_never_submitted(client, ena, record_xml, writable):
    record_xml["error"] = LookupError("ENA holds no XML for PRJEB1")
    body = modify(client, {"title": "new"}).json()
    assert body["success"] is False
    assert "no XML" in body["results"][0]["messages"][0]
    assert ena.submit.documents == []


def test_one_bad_record_does_not_sink_the_batch(client, ena, record_xml, writable):
    body = post(
        client,
        "/api/records/modify",
        {
            "entity": "studies",
            "records": [
                {"accession": "PRJEB1", "changes": {"title": "new"}},
                {"accession": "PRJEB2", "changes": {}},
            ],
        },
        **HEADERS,
    ).json()
    assert [r["success"] for r in body["results"]] == [True, False]
    assert len(ena.submit.documents) == 1


def test_files_cannot_be_modified(client, ena, record_xml, writable):
    response = modify(client, {"title": "new"}, entity="files")
    assert response.status_code == 400
    assert ena.submit.documents == []


def test_an_empty_change_set_is_rejected(client, ena, writable):
    assert post(client, "/api/records/modify", {"entity": "studies", "records": []}, **HEADERS).status_code == 400


# --- the manifest preview ---------------------------------------------------


def preview(client, changes, entity="studies", accession="PRJEB1"):
    return post(
        client,
        "/api/records/modify/preview",
        {"entity": entity, "records": [{"accession": accession, "changes": changes}]},
        **HEADERS,
    )


def test_preview_returns_the_document_it_would_submit_and_sends_nothing(client, ena, record_xml, writable):
    body = preview(client, {"title": "new title"}).json()
    assert body["success"] is True
    assert ena.submit.documents == []  # inspected, not submitted

    manifest = body["results"][0]["xml"]
    assert etree.fromstring(manifest.encode()).find(".//ACTIONS/ACTION/MODIFY") is not None
    modify(client, {"title": "new title"})
    assert ena.submit.documents[0] == manifest.encode()


def test_preview_reports_a_manifest_it_cannot_build(client, ena, record_xml, writable):
    body = preview(client, {"status": "PUBLIC"}).json()
    assert body["success"] is False
    assert "not editable" in body["results"][0]["messages"][0]
    assert body["results"][0]["xml"] == ""
    assert ena.submit.documents == []


def test_an_empty_preview_is_rejected(client, ena, writable):
    response = post(client, "/api/records/modify/preview", {"entity": "studies", "records": []}, **HEADERS)
    assert response.status_code == 400


# --- lifecycle actions ------------------------------------------------------


@pytest.mark.parametrize("action", ["release", "suppress", "cancel"])
def test_actions_dispatch_to_the_matching_submit_call(client, ena, writable, action):
    body = post(client, "/api/records/action", {"accession": "PRJEB1", "action": action}, **HEADERS).json()
    assert body["success"] is True
    assert ena.submit.calls == [(action, ("PRJEB1",))]


def test_hold_needs_a_date(client, ena, writable):
    response = post(client, "/api/records/action", {"accession": "PRJEB1", "action": "hold"}, **HEADERS)
    assert response.status_code == 400
    assert ena.submit.calls == []


def test_hold_passes_a_validated_date_through(client, ena, writable):
    import pendulum

    date = pendulum.today().add(months=1).format("YYYY-MM-DD")
    post(client, "/api/records/action", {"accession": "PRJEB1", "action": "hold", "hold_until_date": date}, **HEADERS)
    assert ena.submit.calls == [("hold", ("PRJEB1", date))]


def test_a_hold_date_in_the_past_is_refused(client, ena, writable):
    response = post(
        client,
        "/api/records/action",
        {"accession": "PRJEB1", "action": "hold", "hold_until_date": "2001-01-01"},
        **HEADERS,
    )
    assert response.status_code == 400
    assert ena.submit.calls == []


@pytest.mark.parametrize("payload", [{"accession": "PRJEB1", "action": "kill"}, {"accession": "", "action": "cancel"}])
def test_unknown_actions_and_junk_accessions_are_refused(client, ena, writable, payload):
    assert post(client, "/api/records/action", payload, **HEADERS).status_code == 400
    assert ena.submit.calls == []


# NOTE: the ENA service itself (listing, MODIFY-by-XML-patch, lifecycle
# actions, accession sanity checks) is tested in ena-submission-toolkit and
# ena-api-client. What is left to test here is this app's HTTP layer: the
# write lock, the action allow-list, and the error-to-status-code mapping.
