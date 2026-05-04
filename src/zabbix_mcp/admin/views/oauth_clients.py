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

"""Admin portal views for OAuth registered clients.

The embedded OAuth 2.1 authorization server keeps a record of every
client that ever called ``POST /register`` (RFC 7591 dynamic client
registration) in ``[oauth_clients.<id>]`` config sections so the
operator has a clear, auditable view of "who can ask my operators
to log in".  The pages here let the operator:

* see the registered clients,
* drill into one to inspect its redirect URIs / scopes / registration
  time / live token count,
* revoke a client (deletes the config entry and revokes any
  outstanding access / refresh tokens it holds).

When ``[oauth].enabled = false`` the page still loads but renders
an empty-state explainer that points at ``docs/OAUTH.md`` -- there is
no error, just no data.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from zabbix_mcp.admin.audit_writer import write_audit
from zabbix_mcp.admin.config_writer import (
    load_config_document,
    remove_config_table,
    TOMLKIT_AVAILABLE,
)

logger = logging.getLogger("zabbix_mcp.admin")


def _ts_human(ts: int | None) -> str:
    """Render a Unix epoch as ``YYYY-MM-DD HH:MM UTC`` for the table."""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError, OSError):
        return ""


def _list_registered_clients(admin_app) -> list[dict]:
    """Read [oauth_clients.*] from config.toml and join with live token counts."""
    if not TOMLKIT_AVAILABLE:
        return []
    try:
        doc = load_config_document(admin_app.config_path)
    except Exception as exc:
        logger.warning("oauth_clients: cannot load config: %s", exc)
        return []

    raw = doc.get("oauth_clients", {}) or {}
    provider = admin_app.oauth_provider

    rows: list[dict] = []
    for cid, body in raw.items():
        body = dict(body)
        ru = body.get("redirect_uris") or []
        if isinstance(ru, str):
            ru = [ru]

        access_count = 0
        refresh_count = 0
        if provider is not None:
            try:
                access_count = sum(
                    1 for at in provider._access_tokens.values()
                    if str(getattr(at, "client_id", "")) == cid
                )
                refresh_count = sum(
                    1 for rt in provider._refresh_tokens.values()
                    if str(getattr(rt, "client_id", "")) == cid
                )
            except Exception:
                pass

        rows.append({
            "id": cid,
            "name": body.get("client_name") or cid,
            "redirect_uris": [str(u) for u in ru],
            "scope": body.get("scope") or "",
            "grant_types": body.get("grant_types") or [],
            "token_endpoint_auth_method": body.get("token_endpoint_auth_method") or "none",
            "client_id_issued_at": body.get("client_id_issued_at"),
            "issued_human": _ts_human(body.get("client_id_issued_at")),
            "active_access_tokens": access_count,
            "active_refresh_tokens": refresh_count,
        })
    rows.sort(key=lambda r: r["client_id_issued_at"] or 0, reverse=True)
    return rows


async def oauth_clients_list(request: Request) -> Response:
    """GET /oauth-clients -- list all registered OAuth clients."""
    admin_app = request.app.state.admin_app
    session = admin_app.require_auth(request)
    if not session:
        return RedirectResponse("/login", status_code=303)

    oauth_enabled = bool(getattr(admin_app.config.oauth, "enabled", False))
    clients = _list_registered_clients(admin_app)

    return admin_app.render("oauth_clients/list.html", request, {
        "active": "oauth-clients",
        "page_title": "OAuth Clients",
        "oauth_enabled": oauth_enabled,
        "clients": clients,
        "public_url": (getattr(admin_app.config.server, "public_url", "") or "").rstrip("/"),
        "can_revoke": session.role in ("admin", "operator"),
    })


_SCOPE_DESCRIPTIONS: dict[str, str] = {
    "*":               "Full access to every tool (read + write).",
    "monitoring":      "Read live state: hosts, problems, items, triggers, history, graphs.",
    "data_collection": "Read templates, value maps, dashboards.",
    "alerts":          "Read action definitions, alerts, media types, scripts.",
    "users":           "Read or modify Zabbix users, groups, roles, MFA. High privilege.",
    "administration":  "Read or modify housekeeping, proxies, audit log, server settings. High privilege.",
    "extensions":      "Server-side analytics: graph_render, anomaly_detect, capacity_forecast, problem_active_get, report_generate, health_check.",
}


def _scope_catalog(active_scopes: list[str]) -> list[dict]:
    """Build the per-scope checkbox catalog for the detail page."""
    from zabbix_mcp.config import TOOL_GROUPS
    rows: list[dict] = []
    active_set = set(active_scopes or [])
    for sid in ["*"] + list(TOOL_GROUPS.keys()):
        rows.append({
            "id": sid,
            "label": "Full access (all tools)" if sid == "*" else sid.replace("_", " ").title(),
            "description": _SCOPE_DESCRIPTIONS.get(sid, "Tool group."),
            "checked": sid in active_set,
            "is_wildcard": sid == "*",
        })
    return rows


async def oauth_client_detail(request: Request) -> Response:
    """GET /oauth-clients/<id> -- single registered client."""
    admin_app = request.app.state.admin_app
    session = admin_app.require_auth(request)
    if not session:
        return RedirectResponse("/login", status_code=303)

    client_id = request.path_params.get("client_id", "")
    rows = _list_registered_clients(admin_app)
    client = next((r for r in rows if r["id"] == client_id), None)
    if client is None:
        return admin_app.flash_redirect(
            "/oauth-clients", "OAuth client not found.", flash_type="warning",
        )

    active_scopes = (client["scope"] or "").split() or ["*"]
    return admin_app.render("oauth_clients/detail.html", request, {
        "active": "oauth-clients",
        "page_title": f"OAuth Client: {client['name']}",
        "client": client,
        "scope_catalog": _scope_catalog(active_scopes),
        "can_edit": session.role in ("admin", "operator"),
        "can_revoke": session.role in ("admin", "operator"),
    })


async def oauth_client_scope_update(request: Request) -> Response:
    """POST /oauth-clients/<id>/scope -- replace the client's scope grant."""
    admin_app = request.app.state.admin_app
    session = admin_app.require_auth(request)
    if not session:
        return RedirectResponse("/login", status_code=303)
    if session.role not in ("admin", "operator"):
        return admin_app.flash_redirect(
            f"/oauth-clients/{request.path_params.get('client_id', '')}",
            "You do not have permission to edit OAuth client scopes.",
            flash_type="danger",
        )

    client_id = request.path_params.get("client_id", "")
    if not TOMLKIT_AVAILABLE:
        return admin_app.flash_redirect(
            "/oauth-clients",
            "Config writer unavailable; cannot edit OAuth client scopes.",
            flash_type="danger",
        )

    form = await request.form()
    scopes = [s for s in form.getlist("scope") if s]
    if not scopes:
        return admin_app.flash_redirect(
            f"/oauth-clients/{client_id}",
            "Pick at least one scope (or revoke the client to remove it entirely).",
            flash_type="warning",
        )

    # Mutate the in-memory provider client so the next /authorize from
    # this client uses the new scope. Updating just config.toml without
    # this would require a full server restart.
    provider = admin_app.oauth_provider
    if provider is not None:
        ci = provider._clients.get(client_id)
        if ci is not None:
            ci.scope = " ".join(scopes)

    # Persist to config so the change survives restart.
    try:
        from zabbix_mcp.admin.config_writer import (
            load_config_document, save_config_document,
        )
        doc = load_config_document(admin_app.config_path)
        section = doc.get("oauth_clients", {})
        if client_id in section:
            section[client_id]["scope"] = " ".join(scopes)
            save_config_document(admin_app.config_path, doc)
    except Exception as exc:
        logger.warning("Could not persist scope change for %s: %s", client_id, exc)
        return admin_app.flash_redirect(
            f"/oauth-clients/{client_id}",
            f"Failed to write scope change to config: {exc}",
            flash_type="danger",
        )

    write_audit(
        action="oauth_client.scope_update",
        user=session.username,
        target_type="oauth_client",
        target_id=client_id,
        details={"new_scope": " ".join(scopes)},
        ip=request.client.host if request.client else "",
    )
    return admin_app.flash_redirect(
        f"/oauth-clients/{client_id}",
        f"Scope updated to: {' '.join(scopes)}.",
        flash_type="success",
    )


async def oauth_client_revoke(request: Request) -> Response:
    """POST /oauth-clients/<id>/revoke -- delete the registration and
    revoke every access / refresh token the client currently holds."""
    admin_app = request.app.state.admin_app
    session = admin_app.require_auth(request)
    if not session:
        return RedirectResponse("/login", status_code=303)
    if session.role not in ("admin", "operator"):
        return admin_app.flash_redirect(
            "/oauth-clients",
            "You do not have permission to revoke OAuth clients.",
            flash_type="danger",
        )

    client_id = request.path_params.get("client_id", "")
    if not TOMLKIT_AVAILABLE:
        return admin_app.flash_redirect(
            "/oauth-clients",
            "Config writer unavailable; cannot revoke OAuth clients.",
            flash_type="danger",
        )

    # 1. Drop the config entry so re-registration cannot resurrect it
    #    silently.  remove_config_table is idempotent on missing keys.
    try:
        remove_config_table(admin_app.config_path, "oauth_clients", client_id)
    except Exception as exc:
        logger.warning("oauth_clients: could not remove [oauth_clients.%s]: %s", client_id, exc)
        return admin_app.flash_redirect(
            "/oauth-clients",
            f"Failed to remove OAuth client config: {exc}",
            flash_type="danger",
        )

    # 2. Forget the client and every token it holds in the live
    #    provider so the next call from that client is rejected.
    provider = admin_app.oauth_provider
    revoked_at = 0
    revoked_rt = 0
    if provider is not None:
        try:
            provider._clients.pop(client_id, None)
            for tok, at in list(provider._access_tokens.items()):
                if str(getattr(at, "client_id", "")) == client_id:
                    provider._access_tokens.pop(tok, None)
                    provider._access_to_refresh.pop(tok, None)
                    revoked_at += 1
            for tok, rt in list(provider._refresh_tokens.items()):
                if str(getattr(rt, "client_id", "")) == client_id:
                    provider._refresh_tokens.pop(tok, None)
                    revoked_rt += 1
        except Exception as exc:
            logger.warning("oauth_clients: in-memory revoke partial for %s: %s", client_id, exc)

    write_audit(
        action="oauth_client.revoke",
        user=session.username,
        target_type="oauth_client",
        target_id=client_id,
        details={"access_tokens": revoked_at, "refresh_tokens": revoked_rt},
        ip=request.client.host if request.client else "",
    )
    return admin_app.flash_redirect(
        "/oauth-clients",
        f"OAuth client '{client_id}' revoked. {revoked_at} access token(s) and {revoked_rt} refresh token(s) invalidated.",
        flash_type="success",
    )
