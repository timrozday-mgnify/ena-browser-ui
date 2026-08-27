"""Index, health and static serving."""

from __future__ import annotations

import pathlib

from django.conf import settings
from django.http import FileResponse, HttpRequest, JsonResponse
from django.views.static import serve as static_serve
from ena_submission_toolkit import records

STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"
VENDOR_DIR = STATIC_DIR / "vendor" / "ena-browser"


def index(request: HttpRequest) -> FileResponse:
    return FileResponse((STATIC_DIR / "index.html").open("rb"))


def static_serve_view(request: HttpRequest, path: str, document_root: str) -> FileResponse:
    return static_serve(request, path, document_root=document_root)


def health(request: HttpRequest) -> JsonResponse:
    return JsonResponse(
        {
            "status": "ok",
            "readonly": settings.READONLY,
            "test_default": settings.TEST_DEFAULT,
            # False means `task vendor` has not been run — the page says so
            # rather than rendering a blank area where the grid should be.
            "element_available": (VENDOR_DIR / "ena-browser.iife.js").is_file(),
            "editable_columns": {entity: records.editable_columns(entity) for entity in records.ENTITIES},
        }
    )
