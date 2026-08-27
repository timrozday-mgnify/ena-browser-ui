"""Shared fakes. No test in this suite is allowed to reach ENA."""

from __future__ import annotations

import contextlib
from typing import Any

import pytest
from ena_submission_toolkit import records

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


class FakeBrowser:
    """The Browser API fetch that modify_records reads before it patches."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {"xml": STUDY_XML, "error": None, "fetched": []}

    def xml(self, accession: str) -> bytes:
        self.state["fetched"].append(accession)
        if self.state["error"] is not None:
            raise self.state["error"]
        return self.state["xml"]


class FakeClient:
    def __init__(self) -> None:
        self.reports = FakeReports()
        self.submit = FakeSubmit()
        self.browser = FakeBrowser()
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
def record_xml(ena: FakeClient) -> dict[str, Any]:
    """The fake Browser API's state, so a test can swap the XML or fail it."""
    return ena.browser.state


HEADERS = {"HTTP_X_WEBIN_USERNAME": "Webin-1", "HTTP_X_WEBIN_PASSWORD": "secret"}
