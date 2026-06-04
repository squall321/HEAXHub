"""HEAXHub Flask demo package."""

from .main import app  # noqa: F401  — re-export so "gunicorn app:app" works
