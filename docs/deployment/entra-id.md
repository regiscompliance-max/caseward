# Signing in with Microsoft 365 (Entra ID)

Staff sign in with the organisation's Microsoft 365 accounts. Landlords that
use Microsoft 365 can be added the same way, as further providers (decision
0010).

## In Entra ID (Azure portal → Microsoft Entra ID → App registrations)

1. **API registration for the operations API**, e.g. "Caseward operations API".
   - Expose an API with Application ID URI `api://caseward-ops` and a scope
     `access` (admin consent).
   - Manifest: `"requestedAccessTokenVersion": 1`, i.e. leave it null or set 1.
     **v1 access tokens carry the `amr` claim**, which the API checks for
     `mfa`. Microsoft's documentation and community answers disagree on
     whether v2 access tokens carry it. If they don't, the API refuses
     every sign-in, so it fails safe rather than open.
   - Tokens then come from issuer `https://sts.windows.net/<tenant-id>/` with
     audience `api://caseward-ops`.
2. **Web registration for the console**, e.g. "Caseward operations console".
   - Redirect URI: `https://<console host>/api/auth/callback`.
   - A client secret, kept in AWS Secrets Manager.
   - API permission: `api://caseward-ops/access`, with admin consent granted.
3. **Conditional Access policy:** require multi-factor authentication for
   both registrations, for all users. Without it, `amr` may not include
   `mfa` and sign-in is refused.
4. Optional claims on the console registration: `email`, so that invitations
   can be claimed.

## Caseward configuration

Operations API (`CASEWARD_OPS_OIDC_PROVIDERS`):

```json
[{"issuer": "https://sts.windows.net/<tenant-id>/",
  "jwks_url": "https://login.microsoftonline.com/<tenant-id>/discovery/keys",
  "audience": "api://caseward-ops",
  "mfa_amr": ["mfa"]}]
```

Console (`CASEWARD_CONSOLE_OIDC_PROVIDERS`):

```json
[{"id": "staff", "name": "Caseward staff (Microsoft 365)",
  "issuer": "https://login.microsoftonline.com/<tenant-id>/v2.0",
  "client_id": "<console application id>", "client_secret": "<from Secrets Manager>",
  "scope": "openid email profile api://caseward-ops/access"}]
```

## Before go-live

Sign in once with a test account. Decode its access token (the console's
`cw_session` cookie, in a test environment only) and confirm:
- `iss` is `https://sts.windows.net/<tenant-id>/`;
- `aud` is `api://caseward-ops`;
- `amr` contains `mfa`.
