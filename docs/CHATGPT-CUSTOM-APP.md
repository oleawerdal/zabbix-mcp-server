# Connecting ChatGPT to a Zabbix MCP Server

This guide walks an operator through plugging a Zabbix MCP server
running v1.28+ with `[oauth].enabled = true` into ChatGPT's "Custom
Apps" feature. ChatGPT auto-discovers everything from the
`/.well-known/...` endpoints; the operator only types the URL.

## Prerequisites

- ChatGPT plan that exposes Custom Apps: Plus / Pro / Business /
  Enterprise / Edu (Developer mode).
- Zabbix MCP server running v1.28 or newer with
  `[oauth].enabled = true`. Confirm with
  `https://<your-mcp-host>/.well-known/oauth-authorization-server` -
  it must return JSON over HTTPS, not 404.
- Server reachable from the OpenAI cloud over **standard port 443
  HTTPS**. Non-standard ports (`:8443`, `:9443`, ...) are rejected
  by ChatGPT's discovery probe even when curl from another host
  succeeds (see "Why standard port 443 only?" below).
- Publicly-trusted TLS certificate (Let's Encrypt / commercial CA).
  Self-signed certs are rejected. See [`docs/OAUTH.md`](OAUTH.md)
  for reverse-proxy patterns (Apache, Nginx, Caddy).
- A Zabbix MCP admin-portal user (`[admin.users.<name>]` in
  `config.toml`). The operator signs in to that account during the
  authorize step.

## Walkthrough

The flow below was driven end-to-end via Playwright on a live
deployment at `https://student-postgresql-01.initmax.cz/mcp` so
every screenshot is what you actually see.

### 1. Open the Custom Apps dialog

In ChatGPT: **Settings -> Apps & Connectors -> Apps**, click
**Create app**. The "New App (Beta)" dialog opens.

![New App dialog](screenshots-oauth/02-chatgpt-new-app.png)

### 2. Fill in the basics

| Field | Value |
|---|---|
| Name | Whatever you want; appears in the chat tool menu (e.g. "Zabbix" or "Wiki Zabbix MCP"). |
| Description (optional) | Free-form. |
| MCP Server URL | `https://<your-mcp-host>/mcp` - **standard 443, no port**. |
| Authentication | **OAuth**. |

![URL filled in](screenshots-oauth/03-chatgpt-url-filled.png)

Do NOT put the issuer URL (`/`) or the discovery URL
(`/.well-known/...`) in the MCP Server URL field - ChatGPT expects
the protocol endpoint (`/mcp`).

The "Advanced OAuth settings" row reads "Loading..." for a moment
while ChatGPT probes `/.well-known/oauth-authorization-server` from
the OpenAI cloud. When discovery succeeds it flips to "Review the
discovered OAuth settings...".

### 3. Advanced OAuth settings

Expand the **Advanced OAuth settings** card. This is where ChatGPT
shows what it discovered:

![DCR auto-discovered](screenshots-oauth/04-chatgpt-dcr-discovered.png)

- **Client registration -> Registration method**: ChatGPT picks
  **Dynamic Client Registration (DCR)** automatically, with the
  hint "ChatGPT will dynamically register an OAuth client using
  the Registration URL in the OAuth endpoints section". Leave it.
- Do NOT switch to "User-Defined OAuth Client" unless you have
  a pre-registered `client_id` for some reason - DCR avoids that
  step entirely.
- "CIMD is unavailable because the server did not advertise CIMD
  support" is **expected**. Client ID Metadata Documents are an
  optional add-on we do not implement; DCR (RFC 7591) covers the
  same ground.
- Default scopes: leave empty. The server's metadata advertises
  no `scopes_supported`, so ChatGPT requests the operator's full
  grant at login time.

If the panel says **"DCR is unavailable until a Registration URL
is present in the OAuth endpoints section below"** the discovery
probe failed. Common causes:

| Symptom | Cause | Fix |
|---|---|---|
| "DCR is unavailable" + "CIMD is unavailable" | ChatGPT cannot reach the server | Verify `https://<host>/.well-known/oauth-authorization-server` returns JSON in your browser. If it does, check the URL has no `:port`. |
| "Error fetching OAuth configuration: Cannot connect to host ... ssl:default [None]" | Non-standard port, self-signed cert, or hostname mismatch | Move OAuth + MCP behind standard port 443 with a publicly-trusted TLS cert. See `docs/OAUTH.md`. |
| "Loading..." spins forever | Discovery URL serves the wrong JSON or returns 404 | The server is not on v1.28, or `[oauth].enabled` is false. |

### 4. Accept the disclaimer

Tick **"I understand and want to continue"** under "Custom MCP
servers introduce risk" and click **Create**. The app lands in
**Drafts** with a `DEV` badge - it is private to your account
until OpenAI reviews it.

### 5. Connect

Click the draft to open its detail card and press **Connect**.
ChatGPT shows a permissions disclosure:

![Connect permissions](screenshots-oauth/05-chatgpt-permissions.png)

Press **Continue to <your app name>**. ChatGPT opens a new tab
on `<your-mcp-host>/oauth/login?request_id=...` - that is the
authorization step running on YOUR server, not on chatgpt.com.

### 6. Sign in to the MCP server

The login page renders in the same theme as the admin portal,
with "ChatGPT is asking to use your operator account":

![Login form](screenshots-oauth/01-login-form.png)

Type the **admin-portal username + password** (`[admin.users.X]`
in your `config.toml`).

### 6b. Consent screen - what you are granting

After credentials check, the server renders a **consent screen**
listing the scopes and the operator role that is signing in:

![Consent screen with wildcard ticked + concrete groups dimmed](screenshots-oauth/10-consent-wildcard-default.png)

Two important controls here:

- **Wildcard vs concrete groups are mutually exclusive.** The
  default has `Full access (all tools)` ticked because ChatGPT's
  registration request asks for `*`.  When you untick it, the six
  concrete groups (`monitoring`, `data_collection`, `alerts`,
  `users`, `administration`, `extensions`) re-enable so you can
  pick a narrower combination:

  ![Wildcard unticked, concrete groups enabled](screenshots-oauth/11-consent-wildcard-unticked.png)

  The audit log records the actual grant, not the requested set,
  so a downscoped grant shows up as `granted_scopes=["monitoring",
  "extensions"]` instead of the wider `["*"]`.

- **Role cap is enforced server-side.** Your admin-portal role
  (admin / operator / viewer) determines the maximum scope set
  you may grant.  An operator-role user cannot grant `users` or
  `administration` to a third-party client even if the client
  asked for `*`; those rows render disabled with a "not available
  to your role" hint.

After "Sign in & allow", the browser is 302-redirected to ChatGPT
carrying the authorization code, ChatGPT exchanges it for an
access token at `/token`, and the tab closes itself.

> Heads up: the OAuth request_id has a 10-minute TTL. If you walk
> away from the login form for too long, you get an "Authorization
> request invalid" page. Click Connect again from the draft card -
> ChatGPT issues a fresh authorize URL.

### 7. Connector status: Connected

Back in Settings -> Apps -> your draft, the status flips to
**Connected on YYYY-MM-DD** with **Authorization used: OAuth**:

![Connected](screenshots-oauth/06-chatgpt-connected.png)

### 8. Use it from a chat

Open a new chat and ask a Zabbix question that names the
connector. ChatGPT picks tools from the MCP catalog and renders
real data:

![Tool call result](screenshots-oauth/07-chatgpt-tool-call.png)

The "Called tool" pill confirms the call went through MCP.

### 6. Use the connector

Back in ChatGPT, the connector status flips to **Connected** and
the Zabbix MCP tools appear in the chat tool menu. Try:

> "What problems are currently active?"

ChatGPT picks `problem_active_get` from the catalog (the
description steers it there) and renders host names + severity
labels.

## Why standard port 443 only?

OpenAI's docs say the connector needs an "HTTPS endpoint" without
mentioning the port. In practice ChatGPT's discovery probe rejects
non-443 HTTPS even when the cert is valid Let's Encrypt and curl
from the operator's laptop works. The exact reason is not in the
public docs, but two observable signals point at the same root
cause:

1. **The error message format.** A blocked non-443 connection
   surfaces as "Error fetching OAuth configuration. Cannot connect
   to host `<host>:<port>` ssl:default [None]" - the `ssl:default`
   marker is what aiohttp produces when the client side TLS
   context has no SNI hint to use, which lines up with a probe
   that only configures SNI for `host` (port 443) and falls back
   to bare-host on anything else. Let's Encrypt requires SNI, so
   the bare-host probe gets a default vhost cert (or rejected),
   and the probe gives up.

2. **Empirical reachability.** `curl -v ... :8443` from a third-party
   host shows TLS 1.3 + valid cert + 200 OK. The same URL from
   ChatGPT's cloud worker shows the error above. Switching the
   server to share `:443` with the existing Apache vhost (path
   based proxying, see `docs/OAUTH.md`) flips ChatGPT to the happy
   path immediately - same TLS cert, same backend, only the port
   changes.

Industry-wise this is consistent with how cloud egress hardening
works: large SaaS vendors typically allow outbound 443 freely and
treat non-443 HTTPS as suspicious (potential C2 / proxy bypass).
ChatGPT's connector probe seems to follow the same pattern.

**Practical consequence**: when you put OAuth + MCP behind a
reverse proxy, route them on `:443`. If your `:443` already serves
something else (like the Zabbix UI), use path-based routing
(`/mcp`, `/authorize`, `/token`, `/.well-known/...`) - the
operator's existing setup at student-postgresql-01.initmax.cz
runs Zabbix on `/` and MCP on `/mcp` + OAuth paths, both via the
same Apache `<VirtualHost *:443>`.

## What is happening on the wire

For an operator who wants to understand or troubleshoot, here is
the actual sequence of HTTP calls. Every step is exercised by
`tests/integration/test_oauth_e2e.py` so a regression in any of
them fails CI before it reaches production.

```
1. ChatGPT  ->  GET /.well-known/oauth-authorization-server   (RFC 8414)
   ChatGPT  ->  GET /.well-known/oauth-protected-resource     (RFC 9728)

2. ChatGPT  ->  POST /register                                (RFC 7591)
                  { redirect_uris: [ chatgpt callback ],
                    client_name:   "<the name you typed>",
                    grant_types:   ["authorization_code","refresh_token"],
                    token_endpoint_auth_method: "none" }
   MCP      ->  201 { client_id: <fresh uuid> }   # public client, no secret

3. ChatGPT generates PKCE verifier + S256 challenge.

4. ChatGPT  ->  GET /authorize?response_type=code&client_id=...&redirect_uri=...
                          &code_challenge=...&code_challenge_method=S256&state=...
   MCP      ->  302 to /oauth/login?request_id=<opaque>

5. Browser  ->  GET /oauth/login?request_id=<opaque>
   MCP      ->  200 + login form

6. Browser  ->  POST /oauth/login (username, password, request_id)
   MCP      ->  302 to <chatgpt callback>?code=...&state=...

7. ChatGPT  ->  POST /token
                  grant_type=authorization_code&client_id=...&code=...
                  &redirect_uri=...&code_verifier=<PKCE verifier>
   MCP      ->  200 { access_token, token_type=Bearer, expires_in=3600,
                       refresh_token, scope }

8. ChatGPT  ->  POST /mcp  (Authorization: Bearer <access_token>)
                  initialize / tools/list / tools/call ...

9. (Hourly)
   ChatGPT  ->  POST /token
                  grant_type=refresh_token&client_id=...&refresh_token=...
   MCP      ->  200 { new access + new refresh; old refresh evicted,
                       old access cascade-invalidated }
```

Three security properties worth knowing:

- **PKCE S256 is mandatory.** A client that does not advertise
  `code_challenge_methods_supported` containing `S256` is refused
  by the framework before it can ever request a token.
- **Token audience binding.** Every issued access token is bound
  via the `aud` claim to `[server].public_url`. A token leaked from
  one MCP deployment cannot be replayed against another.
- **Refresh-token rotation.** Each refresh-token use rotates both
  tokens and evicts the old access token in the same step. A
  stolen-then-replayed refresh token at most pulls one fresh
  access token before the legitimate client's next refresh
  produces a different one (the AS will then mint two parallel
  chains - operator-side detection of refresh reuse is on the
  v1.29 backlog).

## Adding more OAuth-capable clients

The same flow works for:

- **Claude Desktop** -> Settings -> Customize -> Connectors ->
  + Add custom connector -> paste the same `https://<host>/mcp`
  URL, pick **OAuth**.
- **MCP Inspector** (`npx @modelcontextprotocol/inspector`) ->
  Connect to `https://<host>/mcp`, pick OAuth, the inspector runs
  the same authorize flow.
- **Custom CLIs / SDKs** that implement MCP 2025-11-25 -> point
  the SDK at the URL; the SDK's auth helper handles discovery on
  its own.

Bearer-token clients (`[tokens.X]` in `config.toml`) keep working
alongside OAuth - existing CLI scripts, n8n workflows, and any
legacy integration need no change.

## Operator hygiene

- Every dynamically-registered ChatGPT instance becomes an entry
  in `[oauth_clients.<id>]` config sections, visible in the admin
  portal under **OAuth Clients** (`/oauth-clients`). Review the
  list periodically; revoke clients you no longer recognise.
- Revoking a client wipes its config row AND every access /
  refresh token it holds in memory, so the next request from the
  ChatGPT side fails with 401 and the operator has to reconnect
  (which goes through a fresh login + consent).
- Operator login attempts on `/oauth/login` are rate-limited to
  5 failed attempts per IP per 5-minute rolling window (parity
  with the admin portal's own login).
- All login + revoke actions land in the audit log
  (`/var/log/zabbix-mcp/audit.log`).

## See also

- [`docs/OAUTH.md`](OAUTH.md) - server-side setup,
  reverse-proxy patterns, RFC reference, security checklist
- [`tests/integration/test_oauth_e2e.py`](../tests/integration/test_oauth_e2e.py) -
  executable specification of the steps above; what we test on
  every release
