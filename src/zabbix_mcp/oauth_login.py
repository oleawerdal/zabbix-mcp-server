#
# Zabbix MCP Server
# Copyright (C) 2026 initMAX s.r.o.
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License as published by the Free
# Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
# details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#

"""Login + consent UI for the embedded OAuth 2.1 authorization flow.

Lives on the MCP server's HTTP port (not the admin portal) so the
authorize redirect chain stays on a single origin. The view validates
operator credentials against the existing admin-portal user table
(``[admin.users.*]`` in config.toml, scrypt-hashed) so OAuth does not
introduce a second identity store.

The page reuses the admin portal's Jinja2 templates and ``style.css``
so the login surface looks identical to the portal's own login screen
(theme switcher, logo, light/dark variables).  The MCP server mounts
the same ``admin/static`` directory at ``/static/`` for that purpose.

Flow:

1. The framework's authorize handler calls
   ``ZmcpOAuthProvider.authorize`` which stashes the pending request
   and returns ``<public_url>/oauth/login?request_id=<opaque>``.
   FastMCP redirects the user-agent there.
2. GET renders a login form + a consent block listing the scopes the
   client asked for plus the scopes the server is willing to grant.
3. POST verifies username + password against ``[admin.users.*]``,
   calls ``provider.complete_pending(request_id, granted_scopes,
   subject)`` to mint the authorization code, then 302's to the
   client's redirect_uri (carrying ``code`` and ``state``).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import jinja2
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from zabbix_mcp import __version__ as _zmcp_version
from zabbix_mcp.admin.auth import LoginRateLimiter

logger = logging.getLogger("zabbix_mcp.oauth_login")

# Per-IP brute-force throttle for /oauth/login.  Same parameters as the
# admin portal's /login (5 attempts per 5 minutes per IP) so an attacker
# does not get a softer surface here just because the OAuth flow is on
# a different port.  Single instance shared across requests via module
# scope - this module is imported once per server process.
_oauth_login_limiter = LoginRateLimiter()

# Reuse the admin portal's Jinja env so the login + error pages look
# identical to the portal's own login surface (logo, palette, theme
# switcher, ``style.css``).  Templates referenced below MUST live in
# ``src/zabbix_mcp/admin/templates/``.
_TEMPLATE_DIR = Path(__file__).parent / "admin" / "templates"
_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=jinja2.select_autoescape(["html"]),
)


def _render_template(name: str, **ctx: Any) -> str:
    tmpl = _jinja_env.get_template(name)
    return tmpl.render(
        version=_zmcp_version,
        year=datetime.now().year,
        **ctx,
    )


def _render_error_page(message: str, *, status_code: int = 400) -> HTMLResponse:
    body = _render_template("oauth_error.html", message=message)
    return HTMLResponse(body, status_code=status_code)


# ---------------------------------------------------------------------------
# Admin user authentication
# ---------------------------------------------------------------------------


def _verify_admin_user(config: Any, username: str, password: str) -> bool:
    """Validate (username, password) against [admin.users.*] in config.

    Returns True when the user exists, is not disabled, and the
    password matches.  Wrong username and wrong password both return
    False with the same code path so the response time does not leak
    which side mismatched.
    """
    if not username or not password:
        return False
    cfg_path = getattr(config, "_config_path", None)
    if not cfg_path:
        return False
    try:
        from zabbix_mcp.admin.config_writer import load_config_document
        from zabbix_mcp.admin.auth import verify_password
        doc = load_config_document(cfg_path)
        users = (doc.get("admin", {}) or {}).get("users", {}) or {}
        user = users.get(username)
        if user is None:
            return False
        if user.get("disabled"):
            return False
        return bool(verify_password(password, str(user.get("password_hash", ""))))
    except Exception as exc:  # pragma: no cover
        logger.warning("Admin user verification failed for %s: %s", username, exc)
        return False


# ---------------------------------------------------------------------------
# Login endpoint
# ---------------------------------------------------------------------------


async def handle_oauth_login(
    request: Request,
    provider: Any,
    config: Any,
) -> Response:
    """Route handler for ``GET /oauth/login`` and ``POST /oauth/login``.

    GET: render the login form, populated from the pending authorize
    request identified by ``?request_id=...``.

    POST: verify credentials, complete the pending authorize request,
    redirect the browser to the OAuth client's redirect_uri carrying
    ``code`` and ``state``.  On bad credentials, re-render the form
    with an error.
    """
    if request.method == "GET":
        request_id = request.query_params.get("request_id", "")
        pending = provider._pending.get(request_id)
        if pending is None:
            return _render_error_page(
                "This authorization request has expired or was never started. "
                "Reconnect from your MCP client to begin a new login.",
            )
        client_name = (pending.client.client_name or "").strip() or str(pending.client.client_id or "")
        return HTMLResponse(_render_template(
            "oauth_login.html",
            request_id=request_id,
            client_name=client_name,
            scopes=list(pending.params.scopes or []),
            error=None,
            username="",
        ))

    # POST
    form = await request.form()
    request_id = str(form.get("request_id", "") or "")
    username = str(form.get("username", "") or "").strip()
    password = str(form.get("password", "") or "")

    # Brute-force throttle by client IP (parity with admin /login).
    # Read the IP from the contextvar set by _client_ip_middleware so
    # an X-Forwarded-For from a trusted proxy is honoured upstream.
    from zabbix_mcp.token_store import current_client_ip
    client_ip = current_client_ip.get() or (
        request.client.host if request.client else "unknown"
    )
    if not _oauth_login_limiter.check(client_ip):
        return _render_error_page(
            "Too many failed login attempts. Wait 5 minutes before trying again.",
            status_code=429,
        )

    import time as _time
    pending = provider._pending.get(request_id)
    if pending is None or pending.expires_at < _time.time():
        # Drop expired pending entries so brute-force attempts on stale
        # request_ids do not pin them in memory.
        if pending is not None:
            provider._pending.pop(request_id, None)
        return _render_error_page(
            "This authorization request has expired. Reconnect from your "
            "MCP client to begin a new login.",
        )

    from zabbix_mcp.admin.audit_writer import write_audit

    if not _verify_admin_user(config, username, password):
        _oauth_login_limiter.record_attempt(client_ip)
        write_audit(
            action="oauth.login_failed",
            user=username or "(empty)",
            target_type="oauth_client",
            target_id=str(pending.client.client_id or ""),
            details={"client_name": pending.client.client_name or "", "reason": "invalid_credentials"},
            ip=client_ip or "",
        )
        client_name = (pending.client.client_name or "").strip() or str(pending.client.client_id or "")
        return HTMLResponse(_render_template(
            "oauth_login.html",
            request_id=request_id,
            client_name=client_name,
            scopes=list(pending.params.scopes or []),
            error="Invalid username or password.",
            username=username,
        ), status_code=401)
    _oauth_login_limiter.reset(client_ip)

    granted_scopes = list(pending.params.scopes or [])
    redirect_url = provider.complete_pending(
        request_id, granted_scopes, subject=username,
    )
    if redirect_url is None:
        return _render_error_page(
            "This authorization request has expired between submission and "
            "completion. Reconnect from your MCP client to begin a new login.",
        )
    write_audit(
        action="oauth.login_success",
        user=username,
        target_type="oauth_client",
        target_id=str(pending.client.client_id or ""),
        details={"client_name": pending.client.client_name or "", "scopes": granted_scopes},
        ip=client_ip or "",
    )
    logger.info(
        "OAuth login granted: user=%s client=%s scopes=%s",
        username,
        pending.client.client_id,
        granted_scopes,
    )
    return RedirectResponse(redirect_url, status_code=302)
