"""Django settings for the single-user, local-only HTTP layer.

No database, no auth, no sessions, no CSRF: the app is one stateless process
that serves the page and proxies ENA. Webin credentials arrive per-request as
headers (see ``webin_creds``) and are never stored.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _flag(name: str, default: str) -> bool:
    return (os.environ.get(name, default) or "").strip().lower() in ("1", "true", "yes")


# Required by Django for request signing; no secrets persist anywhere.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "ena-browser-ui-insecure-local-key")

DEBUG = _flag("DJANGO_DEBUG", "")

ALLOWED_HOSTS = ["*"]  # local single-user; only ever bound to loopback.

INSTALLED_APPS: list[str] = []

MIDDLEWARE = ["django.middleware.common.CommonMiddleware"]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

USE_TZ = True

# --- App flags --------------------------------------------------------------
# The write lock. Default true: every write endpoint refuses while it is set,
# whatever the UI's own read/write toggle says. The UI toggle is a
# convenience on top of this, not the control.
READONLY = _flag("ENA_BROWSER_READONLY", "true")

# Initial value of the UI's test/production switch.
TEST_DEFAULT = _flag("ENA_BROWSER_TEST", "true")
