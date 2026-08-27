"""The API the page talks to: list, modify, act, validate credentials.

Thin by design — parse, enforce the write lock, delegate to
``ena_submission_toolkit.records`` (the only thing in this stack that talks to
ENA), turn exceptions into a JSON ``detail`` the UI can show verbatim.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import webin_creds
from django.conf import settings
from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from ena_submission_toolkit import records

#: The lifecycle actions this app offers. ``records.ACTIONS`` also has ``kill``
#: — irreversible and admin-only, deliberately not reachable from here.
ALLOWED_ACTIONS = ("release", "hold", "suppress", "cancel")

#: Names this app's MODIFY submissions, so they are identifiable in ENA.
MODIFY_ALIAS = "ena-browser-ui-modify"


def _body(request: HttpRequest) -> dict[str, Any]:
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON body: {exc}") from None
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object")
    return payload


def _guard(request: HttpRequest, *, write: bool = False) -> tuple[Any, bool, JsonResponse | None]:
    """Credentials + environment, plus the server-side write lock."""
    if write and settings.READONLY:
        return (
            None,
            False,
            JsonResponse(
                {"detail": "Server is in read-only mode (ENA_BROWSER_READONLY). No changes were sent to ENA."},
                status=403,
            ),
        )
    creds, error = webin_creds.from_request(request)
    if error is not None:
        return None, False, error
    return creds, webin_creds.wants_test(request), None


def _run(fn: Callable[[], dict[str, Any] | list[Any]]) -> JsonResponse:
    """Call a records function, mapping its failures onto status codes."""
    try:
        result = fn()
    except PermissionError as exc:
        return JsonResponse({"detail": str(exc)}, status=401)
    except (ValueError, LookupError) as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001 - the UI shows this text; a 500 page would not
        return JsonResponse({"detail": f"{type(exc).__name__}: {exc}"}, status=502)
    return JsonResponse(result if isinstance(result, dict) else {"rows": result})


def credentials_validate(request: HttpRequest) -> JsonResponse | HttpResponseNotAllowed:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    creds, test, error = _guard(request)
    if error is not None:
        return error

    def call() -> dict[str, Any]:
        records.validate_credentials(creds, test=test)
        return {"valid": True, "test": test}

    return _run(call)


def _criteria(request: HttpRequest) -> dict[str, Any]:
    """The fetch criteria from the query string, as ``list_records`` kwargs.

    ENA answers none of these itself beyond the release status — the Reports
    API has no search and no relational query — so they are the library's
    filters, named the same way, passed straight through.
    """
    query = request.GET
    return {
        "status": query.get("status") or "all",
        "search": query.get("search", "").strip(),
        "linked_to": query.get("linked_to", "").strip(),
        "unlinked": query.get("unlinked") == "true",
    }


def records_list(request: HttpRequest, entity: str) -> JsonResponse | HttpResponseNotAllowed:
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    creds, test, error = _guard(request)
    if error is not None:
        return error

    def call() -> dict[str, Any]:
        rows = records.list_records(creds, entity, test=test, **_criteria(request))
        return {"rows": rows, "editable_columns": records.editable_columns(entity)}

    return _run(call)


def records_fields(request: HttpRequest, entity: str) -> JsonResponse | HttpResponseNotAllowed:
    """The current value of every editable field, for the given accessions.

    The Reports API does not return a run's title or an experiment's library
    and instrument — they live in the record's XML. The grid cannot let anyone
    edit a field it has never shown them, so the page asks for these once it is
    in write mode and merges them into the rows it already has. A read, not a
    write: no write lock, nothing is sent to ENA.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    creds, test, error = _guard(request)
    if error is not None:
        return error

    def call() -> dict[str, Any]:
        accessions = _body(request).get("accessions")
        if not isinstance(accessions, list):
            raise ValueError('Expected an "accessions" list')
        return {"fields": records.read_editable_fields(creds, entity, [str(a) for a in accessions], test=test)}

    return _run(call)


def _modify_batch(request: HttpRequest) -> tuple[str, list[Any]]:
    payload = _body(request)
    batch = payload.get("records")
    if not isinstance(batch, list) or not batch:
        raise ValueError("No records to modify")
    return str(payload.get("entity") or ""), batch


def records_modify_preview(request: HttpRequest) -> JsonResponse | HttpResponseNotAllowed:
    """Build the MODIFY manifests for a change set. Submits nothing.

    Write-gated like the submission itself: a read-only server has no editable
    grid to build a change set from, and gating both keeps "can this app write"
    a single answer. What it returns is the exact document ``records_modify``
    would send, so the page can show it before anything is committed.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    creds, test, error = _guard(request, write=True)
    if error is not None:
        return error

    def call() -> dict[str, Any]:
        entity, batch = _modify_batch(request)
        return records.preview_modify_records(creds, entity, batch, test=test, submission_alias=MODIFY_ALIAS)

    return _run(call)


def records_modify(request: HttpRequest) -> JsonResponse | HttpResponseNotAllowed:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    creds, test, error = _guard(request, write=True)
    if error is not None:
        return error

    def call() -> dict[str, Any]:
        entity, batch = _modify_batch(request)
        return records.modify_records(creds, entity, batch, test=test, submission_alias=MODIFY_ALIAS)

    return _run(call)


def records_action(request: HttpRequest) -> JsonResponse | HttpResponseNotAllowed:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    creds, test, error = _guard(request, write=True)
    if error is not None:
        return error

    def call() -> dict[str, Any]:
        payload = _body(request)
        action = str(payload.get("action") or "")
        if action not in ALLOWED_ACTIONS:
            raise ValueError(f"Unknown action {action!r}; expected one of {', '.join(ALLOWED_ACTIONS)}")
        return records.record_action(
            creds,
            str(payload.get("accession") or ""),
            action,
            test=test,
            hold_until=payload.get("hold_until_date"),
        )

    return _run(call)
