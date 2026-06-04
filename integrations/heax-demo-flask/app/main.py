"""HEAXHub Flask demo app.

Caddy reverse-proxies ``/apps/{id}/`` to this app. The launcher exports
``HEAX_BASE_PATH`` (e.g. ``/apps/heax_demo_flask``) so that ``url_for``
and the WSGI ``SCRIPT_NAME`` agree on the public prefix.

This single module demonstrates Flask's main features:

* Jinja template rendering (``index.html`` extends ``base.html``)
* Form submission with ``flash()`` (POST ``/echo``)
* Static asset (``/static/heax.svg``)
* Session-backed visit counter
* JSON API (``/health``, ``/api/info``)
* Base-path awareness via ``SCRIPT_NAME``
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import flask
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.wrappers import Response

# ── App factory style, kept inline so gunicorn can import `app.main:app` ──
_BASE_DIR = Path(__file__).resolve().parent.parent

app = Flask(
    __name__,
    template_folder=str(_BASE_DIR / "templates"),
    static_folder=str(_BASE_DIR / "static"),
)

# Secret key: stable in dev, env-overridable in prod. Sessions and flash()
# both need this.
app.secret_key = os.environ.get("HEAX_SECRET_KEY", "heaxhub-demo-flask-dev-secret")

# Base path the launcher hands us. We expose it to templates so navigation
# survives the Caddy ``/apps/{id}/`` reverse proxy.
BASE_PATH = os.environ.get("HEAX_BASE_PATH", "").rstrip("/")


@app.context_processor
def _inject_base_path() -> dict:
    """Make ``base_path`` available to every template."""
    return {"base_path": BASE_PATH}


# ── Routes ────────────────────────────────────────────────────────────────


@app.route("/")
def index() -> str:
    # Session-backed visit counter.
    visits = session.get("visits", 0) + 1
    session["visits"] = visits

    return render_template(
        "index.html",
        greeting="Hello from HEAXHub Flask demo",
        python_version=sys.version.split()[0],
        flask_version=flask.__version__,
        request_path=request.path,
        visits=visits,
    )


@app.route("/echo", methods=["POST"])
def echo() -> Response | str:
    message = (request.form.get("message") or "").strip()
    if not message:
        flash("Please type something before submitting.", "warning")
        return redirect(url_for("index"))

    flash("Received your message.", "success")
    return render_template("echo.html", message=message)


@app.route("/health")
def health() -> dict:
    return {"status": "ok"}


@app.route("/api/info")
def api_info() -> dict:
    return {
        "python": sys.version.split()[0],
        "flask": flask.__version__,
        "base_path": BASE_PATH,
    }


# ── WSGI base-path mount ──────────────────────────────────────────────────
# Caddy strips the ``/apps/{id}`` prefix before forwarding, but other
# deployments (e.g. running gunicorn directly behind a path-preserving
# proxy) may not. If HEAX_BASE_PATH is set, mount the app under it so the
# same WSGI object works in both cases.
if BASE_PATH:
    def _not_found(environ, start_response):  # pragma: no cover - trivial
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"Not Found"]

    application = DispatcherMiddleware(_not_found, {BASE_PATH: app})
else:
    application = app


if __name__ == "__main__":  # pragma: no cover - dev convenience
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
