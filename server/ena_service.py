"""Service layer over ena-api-client. The only module that talks to ENA.

Three things happen here:

  * **listing**   -> ``WebinClient.reports.list_*``
  * **modifying** -> fetch the record's current XML, patch the edited fields,
    submit it back as a MODIFY (see :func:`modify_records` for why it is done
    that way and not by rebuilding the XML from the report row)
  * **lifecycle** -> ``WebinClient.submit.{release,hold,suppress,cancel}``

Credentials are passed in explicitly (they arrive as request headers, see
``webin_creds``) and turned into a per-call ``WebinClient``. Nothing is read
from or written to the environment or disk.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lxml import etree

if TYPE_CHECKING:  # pragma: no cover
    from ena_api import WebinClient

PROD_HOST = "www.ebi.ac.uk"
TEST_HOST = "wwwdev.ebi.ac.uk"

# Reports API entity -> ReportsProxy method. "studies" is "projects" at ENA.
_REPORT_METHODS = {
    "studies": "list_projects",
    "samples": "list_samples",
    "runs": "list_runs",
    "experiments": "list_experiments",
    "analyses": "list_analyses",
    "files": "list_files",
}

# Entity -> (XML set element, XML record element). Files have neither: they are
# not submittable objects in their own right.
_XML_TAGS = {
    "studies": ("PROJECT_SET", "PROJECT"),
    "samples": ("SAMPLE_SET", "SAMPLE"),
    "runs": ("RUN_SET", "RUN"),
    "experiments": ("EXPERIMENT_SET", "EXPERIMENT"),
    "analyses": ("ANALYSIS_SET", "ANALYSIS"),
}

# The only fields a MODIFY may change, per entity, and how each maps onto the
# record's XML. Deliberately small: every field here is one this module knows
# how to patch without touching anything else in the document.
#
#   ("attr", name)  -> an attribute of the record element
#   ("child", name) -> the text of a direct child element, created if absent
_EDITABLE: dict[str, dict[str, tuple[str, str]]] = {
    "studies": {"alias": ("attr", "alias"), "title": ("child", "TITLE")},
    "samples": {"alias": ("attr", "alias"), "title": ("child", "TITLE")},
    "experiments": {"alias": ("attr", "alias"), "title": ("child", "TITLE")},
    "analyses": {"alias": ("attr", "alias"), "title": ("child", "TITLE")},
    "runs": {"alias": ("attr", "alias")},
    "files": {},
}

#: Every entity the app knows, in the order the UI shows them.
ENTITIES = ("studies", "samples", "runs", "experiments", "analyses", "files")

_ACTIONS = ("release", "hold", "suppress", "cancel")
_ACCESSION_RE = re.compile(r"^[A-Za-z0-9._-]{3,64}$")


def editable_columns(entity: str) -> list[str]:
    """Fields the UI may put into edit mode for this entity."""
    return sorted(_EDITABLE.get(entity, {}))


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str


def _host(test: bool) -> str:
    return TEST_HOST if test else PROD_HOST


@contextmanager
def webin_client(creds: Credentials, test: bool) -> Iterator[WebinClient]:
    """Build an authenticated WebinClient for the duration of the block."""
    from ena_api import WebinClient, WebinConfig  # type: ignore

    client = WebinClient(config=WebinConfig(webin_id=creds.username, password=creds.password, test=test))
    try:
        yield client
    finally:
        client.close()


def validate_credentials(creds: Credentials, *, test: bool) -> None:
    """Validate Webin credentials with a lightweight authenticated call."""
    with webin_client(creds, test) as client:
        client.reports.list_projects(max_results=1)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def list_records(creds: Credentials, entity: str, *, test: bool, max_results: int = 5000) -> list[dict[str, Any]]:
    """List the account's records for one entity via the Webin Reports API."""
    method = _REPORT_METHODS.get(entity)
    if method is None:
        raise ValueError(f"Unknown entity {entity!r}; expected one of {', '.join(_REPORT_METHODS)}")
    with webin_client(creds, test) as client:
        return [r.model_dump() for r in getattr(client.reports, method)(max_results=max_results)]


# ---------------------------------------------------------------------------
# Modification
# ---------------------------------------------------------------------------


def _record_xml(creds: Credentials, accession: str, *, test: bool) -> etree._Element:
    """Fetch a record's current XML from the ENA Browser API.

    Webin Basic auth is what makes a *private* record readable here; without
    it the API only serves released data.
    """
    import httpx

    if not _ACCESSION_RE.match(accession):
        raise ValueError(f"Not a plausible accession: {accession!r}")

    url = f"https://{_host(test)}/ena/browser/api/xml/{accession}"
    with httpx.Client(auth=(creds.username, creds.password), timeout=60.0) as http:
        response = http.get(url, headers={"Accept": "application/xml"})
    if response.status_code in (401, 403):
        raise PermissionError(f"Not authorised to read {accession} — check the Webin credentials")
    if response.status_code == 404 or not response.content.strip():
        raise LookupError(f"ENA holds no XML for {accession}")
    response.raise_for_status()
    return etree.fromstring(response.content)


def _find_record(document: etree._Element, record_tag: str) -> etree._Element:
    """The single record element in a Browser API response, set-wrapped or not."""
    if document.tag == record_tag:
        return document
    found = document.findall(f".//{record_tag}")
    if len(found) != 1:
        raise LookupError(f"Expected exactly one <{record_tag}> in the fetched XML, found {len(found)}")
    return found[0]


def _apply_change(record: etree._Element, entity: str, field: str, value: Any) -> None:
    mapping = _EDITABLE.get(entity, {})
    if field not in mapping:
        raise ValueError(f"{field!r} is not editable on {entity}")
    kind, name = mapping[field]
    text = "" if value is None else str(value)
    if kind == "attr":
        if not text:
            raise ValueError(f"{field!r} cannot be emptied")
        record.set(name, text)
        return
    child = record.find(name)
    if child is None:
        # Element order matters to ENA's XSDs; a missing optional element is
        # rare here (report rows carry these fields) and getting the position
        # right is not something this generic patcher can know.
        raise LookupError(f"The record's XML has no <{name}> element to change")
    child.text = text


def _modify_document(record: etree._Element, entity: str) -> bytes:
    """Wrap a patched record element in a WEBIN MODIFY submission."""
    set_tag, _ = _XML_TAGS[entity]
    webin = etree.Element("WEBIN")
    submission = etree.SubElement(etree.SubElement(webin, "SUBMISSION_SET"), "SUBMISSION")
    submission.set("alias", "ena-browser-ui-modify")
    etree.SubElement(etree.SubElement(etree.SubElement(submission, "ACTIONS"), "ACTION"), "MODIFY")
    etree.SubElement(webin, set_tag).append(record)
    return etree.tostring(webin, encoding="UTF-8", xml_declaration=True)


def modify_records(
    creds: Credentials,
    entity: str,
    records: list[dict[str, Any]],
    *,
    test: bool,
) -> dict[str, Any]:
    """Apply a change set to ENA, one MODIFY submission per record.

    ``records`` is ``[{"accession": ..., "changes": {field: value}}]`` — the
    element's change set, narrowed to the fields the host allowed.

    An ENA MODIFY **replaces** the whole object, and the Reports API returns
    only a handful of fields per record (alias, accession, title, status) — so
    building the submission from a report row would silently drop everything
    ENA holds but does not report, e.g. a study's description or a sample's
    attributes. Instead each record's current XML is fetched, the edited
    fields are patched into it, and that document goes back. A record whose
    XML cannot be fetched is reported as failed and **not** submitted: a
    partial document is worse than no submission.
    """
    if entity not in _XML_TAGS:
        raise ValueError(f"{entity} records cannot be modified")

    _, record_tag = _XML_TAGS[entity]
    results: list[dict[str, Any]] = []

    with webin_client(creds, test) as client:
        for entry in records:
            accession = str(entry.get("accession") or "")
            changes = entry.get("changes") or {}
            result: dict[str, Any] = {"accession": accession, "success": False, "messages": []}
            if not accession or not changes:
                result["messages"] = ["Nothing to change"]
                results.append(result)
                continue
            try:
                document = _record_xml(creds, accession, test=test)
                record = _find_record(document, record_tag)
                for field, value in changes.items():
                    _apply_change(record, entity, field, value)
                receipt = client.submit.xml(_modify_document(record, entity))
                result["success"] = receipt.success
                result["messages"] = receipt.messages + receipt.warnings + receipt.errors
            except Exception as exc:  # noqa: BLE001 - one bad record must not sink the batch
                result["messages"] = [str(exc)]
            results.append(result)

    return {"success": all(r["success"] for r in results), "results": results}


# ---------------------------------------------------------------------------
# Lifecycle actions
# ---------------------------------------------------------------------------


def record_action(
    creds: Credentials,
    accession: str,
    action: str,
    *,
    test: bool,
    hold_until_date: str | None = None,
) -> dict[str, Any]:
    """Release / hold / suppress / cancel one record."""
    if action not in _ACTIONS:
        raise ValueError(f"Unknown action {action!r}; expected one of {', '.join(_ACTIONS)}")
    if not _ACCESSION_RE.match(accession or ""):
        raise ValueError(f"Not a plausible accession: {accession!r}")
    if action == "hold":
        if not hold_until_date:
            raise ValueError("hold needs a hold_until_date (YYYY-MM-DD)")
        from ena_submission_toolkit import common  # type: ignore

        common.validate_hold_until(hold_until_date)

    with webin_client(creds, test) as client:
        if action == "hold":
            receipt = client.submit.hold(accession, hold_until_date)  # type: ignore[arg-type]
        else:
            receipt = getattr(client.submit, action)(accession)
    return {
        "success": receipt.success,
        "messages": receipt.messages + receipt.warnings + receipt.errors,
    }
