"""Isolated temporary Vercel preview; does not use Render's database."""
import os
from pathlib import Path
from .settings import *

DEBUG = False
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
ALLOWED_HOSTS = [".vercel.app"]
CSRF_TRUSTED_ORIGINS = ["https://*.vercel.app"]
WSGI_APPLICATION = "invoicepro.wsgi.application"

# Vercel's filesystem is ephemeral. This preview uses demonstration data only.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": "/tmp/ledgerlite-friend-preview.sqlite3",
        "OPTIONS": {"timeout": 20},
    }
}
MEDIA_ROOT = Path("/tmp/ledgerlite-friend-media")
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
