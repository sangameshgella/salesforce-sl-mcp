# OAuth Setup for Railway MCP (Salesforce IdP)

This guide configures OAuth for inbound ChatGPT requests to the Railway-hosted MCP server using Salesforce as the identity provider and an OAuth gateway/proxy in front of `/mcp`.

## 1) Salesforce App Configuration

Create an External Client App / Connected App in Salesforce:

- Enable OAuth.
- Enable flow: `Authorization Code and Credentials Flow`.
- Keep `Require user credentials in the POST body...` unchecked.
- Callback URL: use your gateway callback URL (example: `https://<gateway-domain>/oauth2/callback`).
- Scopes (recommended):
  - `openid`
  - `profile`
  - `email`
  - `refresh_token` (or `offline_access` label depending on UI)

Use auth/token endpoints based on org type:

- Production/Developer:
  - `https://login.salesforce.com/services/oauth2/authorize`
  - `https://login.salesforce.com/services/oauth2/token`
- Sandbox:
  - `https://test.salesforce.com/services/oauth2/authorize`
  - `https://test.salesforce.com/services/oauth2/token`

Save:

- Consumer Key (client id)
- Consumer Secret (if required by gateway mode)

## 2) Gateway in Front of Railway

Run an OAuth-capable reverse proxy in front of your Railway MCP endpoint.

Gateway requirements:

- Handles OAuth redirect/login with Salesforce.
- Validates access token/session.
- Proxies authenticated requests to `https://<railway-app>/mcp`.
- Forwards identity headers to upstream:
  - `x-forwarded-user`
  - `x-forwarded-email`
  - `x-forwarded-sub` (optional)

## 3) MCP Server Trust Settings

`server.py` supports optional allowlisting and identity enforcement via env vars.

Set in Railway:

- `MCP_ENFORCE_IDENTITY=true` to require forwarded identity headers.
- `MCP_ALLOWED_EMAILS` as comma-separated exact principals.
  - Example: `user1@company.com,user2@company.com`
- `MCP_ALLOWED_EMAIL_DOMAINS` as comma-separated domains.
  - Example: `company.com,partner.org`

If enforcement is enabled and identity is missing or not allowlisted, MCP returns `403`.

## 4) ChatGPT App Connector Setup

In ChatGPT New App:

- MCP Server URL: gateway URL ending in `/mcp`
- Authentication: OAuth
- OAuth Client ID: Salesforce consumer key
- OAuth Client Secret: Salesforce consumer secret (if required)

Complete the OAuth redirect and consent flow.

## 5) Validation Checklist

1. Unauthenticated request to gateway `/mcp` is denied.
2. Authenticated ChatGPT app can connect and list tools.
3. `case_flow_summary` call succeeds through gateway.
4. MCP logs include:
   - `MCP IDENTITY: user=... email=... sub=...`
5. Disallowed identity receives 403 when allowlist is configured.
