from __future__ import annotations

import views_core
import views_records
from django.urls import path, re_path

urlpatterns = [
    path("", views_core.index),
    path("api/health", views_core.health),
    path("api/credentials/validate", views_records.credentials_validate),
    path("api/records/modify/preview", views_records.records_modify_preview),
    path("api/records/modify", views_records.records_modify),
    path("api/records/action", views_records.records_action),
    path("api/records/<str:entity>/fields", views_records.records_fields),
    path("api/records/<str:entity>", views_records.records_list),
    re_path(
        r"^static/(?P<path>.*)$",
        views_core.static_serve_view,
        {"document_root": str(views_core.STATIC_DIR)},
    ),
]
