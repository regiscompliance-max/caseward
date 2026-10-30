# 0010: Non-superuser database owner, sign-in lookups and invitations

Date: 30 Oct 2026. Status: proposed; **security lead to review**.

## What

- **The schema is owned by an ordinary role**, not a superuser, and the
  owner does not have BYPASSRLS. The tests now run every migration as such a
  role (`cw_test_owner`), exactly as production should.
- **Sign-in lookups run as `caseward_auth`.** This role cannot log in, no
  login role is a member of it, and it has narrow policies of its own. It
  owns the three functions that run before any client context exists:
  - `resolve_user`: find the user from their token;
  - `user_memberships`: which clients they belong to;
  - `claim_invitation`: bind an invited user to their identity provider
    account on first sign-in.
- **Invitations.** Admins invite by email, and the user is stored with a
  placeholder subject (`invited:…`). On first sign-in, if the identity
  provider asserts `email_verified: true` for that email, the invitation is
  bound to the token's subject. This happens once only. After that, sign-in
  matches on the subject alone.

## Why

Until this change the tests ran migrations as a superuser. Superusers
bypass row-level security everywhere, including inside security definer
functions. So the sign-in lookups appeared to work, but in production, with
a normal owner and forced row-level security, **no one could have signed
in**. Running the tests as a realistic owner found this immediately. Every
other security definer function was checked the same way and works:
- audit and its hash chain;
- case status history;
- special-category reads and ending factors;
- deadline completion.

## Alternatives considered

- **Give the owner BYPASSRLS.** One line, but any bug in any owner-owned
  function would then see every client's data.
- **Turn off forced RLS on `app_user` and `membership`.** That weakens the
  tables most worth protecting.
- **Link users by email on every sign-in.** Email addresses get reused, and
  some identity providers let users change them. Binding once, to the
  provider's stable subject, avoids both.

## Consequences

- Production migrations must run as a role with CREATEROLE and CREATE on the
  database, but **not** superuser or BYPASSRLS.
- Client identity providers must send `email` and `email_verified` claims
  for invitations to work.

## Amendment (30 Oct 2026): several identity providers

The API now trusts a list of identity providers. Users are stored as
`issuer|subject` (migration 0013), since a subject is unique only within one
provider: two organisations' directories could otherwise sign in as the
same Caseward user. Invitations are still claimed by verified email, now
bound to the issuer-qualified subject. Configure the providers per API in
`CASEWARD_CLIENT_OIDC_PROVIDERS` and `CASEWARD_OPS_OIDC_PROVIDERS` (JSON),
including each provider's multi-factor rule.

