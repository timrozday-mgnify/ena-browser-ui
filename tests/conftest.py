"""Shared fakes. No test in this suite is allowed to reach ENA."""

from __future__ import annotations

import contextlib
from typing import Any

import pytest
from ena_submission_toolkit import portal, records

STUDY_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<PROJECT_SET>
  <PROJECT accession="PRJEB1" alias="old-alias" center_name="EBI">
    <NAME>a name</NAME>
    <TITLE>old title</TITLE>
    <DESCRIPTION>a description the Reports API never returns</DESCRIPTION>
  </PROJECT>
</PROJECT_SET>
"""


class FakeReceipt:
    def __init__(self, success: bool = True, messages: list[str] | None = None) -> None:
        self.success = success
        self.messages = messages or []
        self.warnings: list[str] = []
        self.errors: list[str] = []


class FakeReport:
    def __init__(self, **fields: Any) -> None:
        self._fields = fields

    def model_dump(self) -> dict[str, Any]:
        return dict(self._fields)

    def __getattr__(self, name: str) -> Any:
        # The lineage filters read report fields as attributes, as the real
        # pydantic models expose them; anything absent is empty, not an error.
        return self._fields.get(name, "")


class FakeSubmit:
    def __init__(self) -> None:
        self.documents: list[bytes] = []
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.receipt = FakeReceipt()

    def xml(self, xml_bytes: bytes) -> FakeReceipt:
        self.documents.append(xml_bytes)
        return self.receipt

    def _record(self, name: str, *args: Any) -> FakeReceipt:
        self.calls.append((name, args))
        return self.receipt

    def release(self, *a: Any) -> FakeReceipt:
        return self._record("release", *a)

    def suppress(self, *a: Any) -> FakeReceipt:
        return self._record("suppress", *a)

    def cancel(self, *a: Any) -> FakeReceipt:
        return self._record("cancel", *a)

    def hold(self, *a: Any) -> FakeReceipt:
        return self._record("hold", *a)


class FakeReports:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def _list(self, name: str, max_results: int) -> list[FakeReport]:
        self.calls.append((name, max_results))
        return [FakeReport(accession="PRJEB1", alias="old-alias", title="old title", status="PRIVATE")]

    def list_projects(self, max_results: int = 5000) -> list[FakeReport]:
        return self._list("projects", max_results)

    def list_samples(self, max_results: int = 5000) -> list[FakeReport]:
        return self._list("samples", max_results)

    def list_runs(self, max_results: int = 5000) -> list[FakeReport]:
        return self._list("runs", max_results)

    def list_experiments(self, max_results: int = 5000) -> list[FakeReport]:
        return self._list("experiments", max_results)

    def list_analyses(self, max_results: int = 5000) -> list[FakeReport]:
        return self._list("analyses", max_results)

    def list_files(self, max_results: int = 5000) -> list[FakeReport]:
        return self._list("files", max_results)

    def list_run_processes(self, max_results: int = 5000, **_kwargs: Any) -> list[FakeRunProcess]:
        self.calls.append(("run-process", max_results))
        return [FakeRunProcess("ERR1", "COMPLETED", "2026-01-02")]


class FakeRunProcess:
    """One row of the run-processing report."""

    def __init__(self, run_accession: str, status: str, date: str = "", error: str = "") -> None:
        self.run_accession = run_accession
        self.process_status = status
        self.process_date = date
        self.error_message = error


class FakeRecordXml:
    """The record XML every read starts from, and who serves it.

    The Reports API is the source now: the Browser API answers 404 for a
    private record, which is most of a submitter's account. ``owned=False``
    makes this account own none of them, which is the one case that still
    falls through to the Browser API — records the Portal listed and this
    account did not submit.
    """

    def __init__(self) -> None:
        self.state: dict[str, Any] = {
            "xml": STUDY_XML,
            "error": None,
            "fetched": [],
            "entities": [],
            "owned": True,
        }

    def reports_xml(self, entity: str, accessions: Any) -> bytes:
        if not self.state["owned"]:
            raise LookupError("the Webin account holds none of these")
        self.state["entities"].append(entity)
        return self._serve(list(accessions))

    def _serve(self, accessions: list[str]) -> bytes:
        self.state["fetched"].extend(accessions)
        if self.state["error"] is not None:
            raise self.state["error"]
        return self.state["xml"]


class FakeBrowser:
    """The public fallback: released records this account did not submit."""

    def __init__(self, records_xml: FakeRecordXml) -> None:
        self._records = records_xml
        self.state = records_xml.state

    def xml(self, accession: str) -> bytes:
        return self._records._serve([accession])

    def xml_many(self, accessions: list[str]) -> bytes:
        return self._records._serve(list(accessions))


class FakeClient:
    def __init__(self) -> None:
        record_xml = FakeRecordXml()
        self.reports = FakeReports()
        self.reports.xml = record_xml.reports_xml  # type: ignore[method-assign]
        self.submit = FakeSubmit()
        self.browser = FakeBrowser(record_xml)
        self.test: bool | None = None


@pytest.fixture
def ena(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    """Replace the WebinClient with a fake, so nothing leaves the process."""
    client = FakeClient()

    @contextlib.contextmanager
    def fake_webin_client(creds: records.Credentials, test: bool):
        client.test = test
        client.creds = creds
        yield client

    monkeypatch.setattr(records, "webin_client", fake_webin_client)
    return client


@pytest.fixture
def ena_portal(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the Portal API with a fake, so nothing leaves the process.

    ``state["rows"]`` is what a public search answers with; ``state["calls"]``
    records what was asked. Set ``state["error"]`` to make it fail.
    """
    state: dict[str, Any] = {"rows": [], "calls": [], "error": None}

    def fake_search_public(entity, linked_to, *, username="", password=""):
        state["calls"].append((entity, linked_to, username))
        if state["error"] is not None:
            raise state["error"]
        return list(state["rows"])

    monkeypatch.setattr(portal, "search_public", fake_search_public)
    return state


@pytest.fixture
def record_xml(ena: FakeClient) -> dict[str, Any]:
    """What ENA holds for a record, so a test can swap the XML or fail it."""
    return ena.browser.state


HEADERS = {"HTTP_X_WEBIN_USERNAME": "Webin-1", "HTTP_X_WEBIN_PASSWORD": "secret"}
