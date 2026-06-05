"""Caddy reverse-proxy manager.

Idempotent route management against the Caddy Admin API. Each app route uses
`@id = app-{app_id}` so PUT replaces the prior definition atomically.

In dev environments Caddy may not be running; in that case operations return a
sentinel `ProxyResult(ok=False, reason="caddy unreachable")` and the caller can
decide whether to surface or ignore it. We never raise on unreachable Caddy.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# Timeout is intentionally short — Caddy admin is local and should respond fast.
_HTTP_TIMEOUT_SECONDS = 5.0


@dataclass(slots=True)
class ProxyResult:
    """Outcome of a proxy mutation. `ok=False` with reason set on transport failures."""

    ok: bool
    reason: str | None = None
    payload: Any = None


def _route_id(app_id: str) -> str:
    return f"app-{app_id}"


def _admin_url(path: str) -> str:
    base = get_settings().caddy_admin_url.rstrip("/")
    return f"{base}{path}"


def _build_route(
    app_id: str,
    port: int,
    base_path: str | None,
    *,
    strip_prefix: bool = True,
) -> dict[str, Any]:
    """Build a Caddy route object for path-based reverse proxy.

    Default match: ``/apps/{app_id}`` and ``/apps/{app_id}/*``.

    ``strip_prefix=True`` (default) strips the matched prefix before forwarding
    so the upstream receives root paths — appropriate for apps that don't know
    about their base path (e.g. uvicorn with ``--root-path``).

    ``strip_prefix=False`` forwards the path unchanged — required for apps that
    bake the prefix into their own routing (e.g. Streamlit ``baseUrlPath``,
    Next.js ``basePath``).
    """
    path = base_path or f"/apps/{app_id}"
    if not path.startswith("/"):
        path = "/" + path
    path = path.rstrip("/")
    # Match both the bare prefix (no trailing slash) and any subpath.
    match_paths = [path, f"{path}/*"]

    handle_chain: list[dict[str, Any]] = []
    if strip_prefix:
        handle_chain.append({"handler": "rewrite", "strip_path_prefix": path})
    handle_chain.append({
        "handler": "reverse_proxy",
        "upstreams": [{"dial": f"127.0.0.1:{port}"}],
    })

    return {
        "@id": _route_id(app_id),
        "match": [{"path": match_paths}],
        "handle": [{"handler": "subroute", "routes": [{"handle": handle_chain}]}],
    }


def _build_static_route(
    app_id: str,
    root_path: str,
    base_path: str | None,
    *,
    index_file: str = "index.html",
) -> dict[str, Any]:
    """Build a Caddy route serving ``root_path`` from disk via ``file_server``.

    Used by static-runtime stacks (static_html, mkdocs_static) — there is no
    upstream process, so we attach Caddy's built-in file_server handler
    directly to the same ``/apps/{app_id}`` prefix and strip that prefix so
    the filesystem layout doesn't need to mirror the URL structure.
    """
    path = base_path or f"/apps/{app_id}"
    if not path.startswith("/"):
        path = "/" + path
    path = path.rstrip("/")
    match_paths = [path, f"{path}/*"]

    handle_chain: list[dict[str, Any]] = [
        {"handler": "rewrite", "strip_path_prefix": path},
        {
            "handler": "file_server",
            "root": root_path,
            "index_names": [index_file],
        },
    ]
    return {
        "@id": _route_id(app_id),
        "match": [{"path": match_paths}],
        "handle": [{"handler": "subroute", "routes": [{"handle": handle_chain}]}],
    }


def _ensure_spa_last(client: httpx.Client) -> None:
    """Guarantee ``spa-static`` is the LAST route so it never shadows an
    ``app-*`` route. Idempotent: if spa-static is already last (or absent),
    this is a no-op.

    Root-cause for the demo-shadowing bug: PUT /id/<route_id> succeeds when an
    entry with that @id already exists, but it preserves the entry's current
    position. If a new ``app-*`` was inserted BEHIND ``spa-static`` (e.g.
    because some previous step injected spa-static at the head), Caddy's
    first-match wins and spa-static swallows everything under /apps/. Fixing
    insertion order at every register call closes the race for good.
    """
    try:
        resp = client.get(_admin_url("/config/apps/http/servers/srv0/routes"))
        if resp.status_code != 200:
            return
        rs = resp.json() or []
    except Exception:
        return
    rs = [r for r in rs if r is not None]
    spa = [r for r in rs if r.get("@id") == "spa-static"]
    if not spa or rs[-1].get("@id") == "spa-static":
        return
    others = [r for r in rs if r.get("@id") != "spa-static"]
    try:
        client.patch(
            _admin_url("/config/apps/http/servers/srv0/routes"),
            json=others + spa,
        )
    except Exception:
        pass


def _idempotent_put_or_insert(
    client: httpx.Client, route: dict[str, Any], route_id: str
) -> None:
    """PUT-replace the route by @id; on any non-2xx (404 absent, 400 conflict,
    409, etc.) fall back to DELETE-by-id + POST-at-head so a stale entry from a
    previous run can never block re-registration. Raises on the final POST
    failure so the caller can surface it.

    After the write, always call :func:`_ensure_spa_last` so the SPA catch-all
    never shadows an ``app-*`` route — adding one demo must not break another.
    """
    resp = client.put(_admin_url(f"/id/{route_id}"), json=route)
    if 200 <= resp.status_code < 300:
        _ensure_spa_last(client)
        return
    # Anything other than 2xx — Caddy may already have a route with this @id
    # whose match/handle shape isn't PUT-replaceable, OR there's no route at
    # all yet. DELETE-then-POST is safe either way: DELETE on a missing @id
    # returns 404 (treated as success) and POST inserts at the head.
    client.delete(_admin_url(f"/id/{route_id}"))  # noqa: S113 — best effort
    resp = client.post(
        _admin_url("/config/apps/http/servers/srv0/routes/0"),
        json=route,
    )
    resp.raise_for_status()
    _ensure_spa_last(client)


def register_app_route(
    app_id: str,
    port: int,
    base_path: str | None = None,
    *,
    strip_prefix: bool = True,
) -> ProxyResult:
    """Idempotently register/replace a route for `app_id` -> `127.0.0.1:port`.

    ``strip_prefix=False`` for apps that handle the base path themselves
    (Streamlit baseUrlPath, Next.js basePath).
    """
    route = _build_route(app_id, port, base_path, strip_prefix=strip_prefix)
    route_id = _route_id(app_id)
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            _idempotent_put_or_insert(client, route, route_id)
    except httpx.HTTPError as exc:
        logger.warning("Caddy register failed for app=%s: %s", app_id, exc)
        return ProxyResult(ok=False, reason=f"caddy unreachable: {exc}")
    logger.info("Caddy route registered app=%s -> 127.0.0.1:%d", app_id, port)
    return ProxyResult(ok=True, payload=route)


def register_static_route(
    app_id: str,
    root_path: str,
    base_path: str | None = None,
    *,
    index_file: str = "index.html",
) -> ProxyResult:
    """Idempotently register a Caddy file_server route for static-only apps.

    ``root_path`` MUST be an absolute filesystem path readable by Caddy.
    Uses the same ``@id = app-{app_id}`` slot as :func:`register_app_route`,
    so transitioning a service-mode app to a static one (or vice versa) is
    a single PUT.
    """
    route = _build_static_route(app_id, root_path, base_path, index_file=index_file)
    route_id = _route_id(app_id)
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            _idempotent_put_or_insert(client, route, route_id)
    except httpx.HTTPError as exc:
        logger.warning("Caddy static register failed for app=%s: %s", app_id, exc)
        return ProxyResult(ok=False, reason=f"caddy unreachable: {exc}")
    logger.info("Caddy static route registered app=%s -> %s", app_id, root_path)
    return ProxyResult(ok=True, payload=route)


def _build_external_proxy_route(
    app_id: str,
    upstream_url: str,
    base_path: str | None,
    *,
    strip_prefix: bool = True,
) -> dict[str, Any]:
    """Build a Caddy route reverse-proxying to an external ``upstream_url``.

    Unlike :func:`_build_route`, the upstream is not ``127.0.0.1:<port>`` but
    an arbitrary ``host[:port]`` parsed out of ``upstream_url``. When the
    upstream is https we also attach an http+tls transport so Caddy talks
    SNI/TLS to the origin instead of plain TCP.
    """
    from urllib.parse import urlparse

    parsed = urlparse(upstream_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(
            f"upstream_url must be http(s)://host[:port][/path], got {upstream_url!r}"
        )
    is_tls = parsed.scheme == "https"
    port = parsed.port or (443 if is_tls else 80)
    dial = f"{parsed.hostname}:{port}"

    path = base_path or f"/apps/{app_id}"
    if not path.startswith("/"):
        path = "/" + path
    path = path.rstrip("/")
    match_paths = [path, f"{path}/*"]

    handle_chain: list[dict[str, Any]] = []
    if strip_prefix:
        handle_chain.append({"handler": "rewrite", "strip_path_prefix": path})

    reverse_proxy: dict[str, Any] = {
        "handler": "reverse_proxy",
        "upstreams": [{"dial": dial}],
        # Many SaaS upstreams virtual-host route on the Host header — without
        # this they 404 because they see HEAXHub's hostname instead of their own.
        "headers": {
            "request": {"set": {"Host": [parsed.hostname]}},
        },
    }
    if is_tls:
        reverse_proxy["transport"] = {
            "protocol": "http",
            "tls": {"server_name": parsed.hostname},
        }
    handle_chain.append(reverse_proxy)

    return {
        "@id": _route_id(app_id),
        "match": [{"path": match_paths}],
        "handle": [{"handler": "subroute", "routes": [{"handle": handle_chain}]}],
    }


def register_external_proxy_route(
    app_id: str,
    upstream_url: str,
    base_path: str | None = None,
    *,
    strip_prefix: bool = True,
) -> ProxyResult:
    """Register a Caddy route ``/apps/{app_id}/*`` -> external ``upstream_url``.

    Same idempotent PUT-then-POST pattern as :func:`register_app_route` but
    the upstream lives off-host. Used by the ``external_proxy`` launch mode.
    """
    try:
        route = _build_external_proxy_route(
            app_id, upstream_url, base_path, strip_prefix=strip_prefix,
        )
    except ValueError as exc:
        logger.warning("external proxy route invalid for app=%s: %s", app_id, exc)
        return ProxyResult(ok=False, reason=str(exc))

    route_id = _route_id(app_id)
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            _idempotent_put_or_insert(client, route, route_id)
    except httpx.HTTPError as exc:
        logger.warning("Caddy external register failed for app=%s: %s", app_id, exc)
        return ProxyResult(ok=False, reason=f"caddy unreachable: {exc}")
    logger.info(
        "Caddy external route registered app=%s -> %s", app_id, upstream_url,
    )
    return ProxyResult(ok=True, payload=route)


def unregister_app_route(app_id: str) -> ProxyResult:
    """Delete the route for `app_id`. Idempotent (404 treated as success)."""
    route_id = _route_id(app_id)
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            resp = client.delete(_admin_url(f"/id/{route_id}"))
            if resp.status_code == 404:
                logger.info("Caddy route already absent app=%s", app_id)
                return ProxyResult(ok=True, reason="already absent")
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Caddy unregister failed for app=%s: %s", app_id, exc)
        return ProxyResult(ok=False, reason=f"caddy unreachable: {exc}")
    logger.info("Caddy route unregistered app=%s", app_id)
    return ProxyResult(ok=True)


def list_routes() -> list[dict]:
    """Return the routes currently configured in Caddy (best-effort)."""
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            resp = client.get(_admin_url("/config/apps/http/servers/srv0/routes"))
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            data = resp.json()
            return list(data) if isinstance(data, list) else []
    except httpx.HTTPError as exc:
        logger.warning("Caddy list_routes failed: %s", exc)
        return []
