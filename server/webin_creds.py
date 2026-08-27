"""Webin credentials, supplied per-request by the single-user client.

There is no account system and nothing is persisted server-side. The browser
holds the Webin username/password in ``sessionStorage`` for its tab and sends
them on every request as ``X-Webin-Username`` / ``X-Webin-Password``; this
turns them into the ``Credentials`` object ``ena_submission_toolkit.records``
expects.
"""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from ena_submission_toolkit import records


def from_request(request: HttpRequest) -> tuple[records.Credentials | None, JsonResponse | None]:
    username = (request.headers.get("X-Webin-Username") or "").strip()
    password = request.headers.get("X-Webin-Password") or ""
    if not username or not password:
        return None, JsonResponse(
            {"detail": "Credentials not set. Enter your Webin username and password."}, status=401
        )
    return records.Credentials(username=username, password=password), None


def wants_test(request: HttpRequest) -> bool:
    """The ENA environment this request targets. Test unless explicitly not."""
    raw = request.headers.get("X-Ena-Test")
    if raw is None:
        raw = request.GET.get("test", "true")
    return (raw or "").strip().lower() not in ("0", "false", "no")
