# 0012: Operations console

Date: 30 Oct 2026. Status: proposed.

## What

- **Next.js 15 + TypeScript** in `frontend/ops-console`. The API types are
  generated from `docs/api/operations.yaml` (`npm run gen:api`), and CI fails
  if they drift from the spec (`npm run check:api`).
- **Backend-for-frontend proxy.** The browser calls `/api/ops/*` on the
  console's own server, which adds the access token from an httpOnly,
  SameSite=Strict cookie and forwards only the headers the API contract uses.
  The token is never readable by page scripts. Path segments are encoded, and
  `.` and `..` are refused, so a request can't escape the API's base path.
- **React Aria Components** for interactive widgets: buttons, dialogs and the
  switch. They give correct keyboard and focus behaviour, while the styling
  stays ours.
- **Built for speed and accuracy:**
  - The queue is in the API's order (priority, legal deadline, SLA).
  - "Take next task" claims and opens the next task (N).
  - J/K and Enter move through the queue.
  - Review shows page images beside the fields. Alt+←/→ turns pages, and
    Ctrl+Enter submits.
  - After a review, the next task opens automatically.
- **Accessibility (WCAG 2.2 AA):**
  - a skip link, landmarks, labelled fields and an error summary;
  - status shown in words and shapes, not colour alone;
  - focus is always visible;
  - single-key shortcuts can be turned off and never fire while typing
    (criterion 2.1.4);
  - every page is scanned with axe in the browser tests.
- **Dates are typed as day/month/year, not with the browser's date
  picker.** The picker follows the machine's locale. The first screenshot
  showed `09/14/2026`, month first, which is exactly the error to avoid on UK
  certificates checked from Nairobi. Impossible dates are caught before
  submitting.
- **Times:** deadlines are shown in UK time, with the zone named, plus the
  time left.
- **The review decision follows the form.** An untouched draft is
  "approved", any edit makes it "corrected", and rejecting needs a reason.
- **Security headers:** `no-store` everywhere, no framing, nosniff, no
  referrer, and a Content Security Policy.

- **Single sign-on with several identity providers** (founder's decision,
  30 Oct 2026):
  - Our staff directory and other organisations' own providers are each
    configured in `CASEWARD_CONSOLE_OIDC_PROVIDERS`, and the sign-in page
    offers one button per provider.
  - The flow is OpenID Connect authorisation code with PKCE, state and nonce
    (`openid-client`).
  - The access token goes in an httpOnly, SameSite=Strict cookie that lasts
    as long as the token, capped at 8 hours.
  - After sign-in the console only redirects to its own pages.
  - An ended session goes back to sign-in and returns afterwards.
  - The development token form appears only when `CASEWARD_DEV_SIGN_IN=1`.
- **The API trusts several issuers** (decision 0010, amended):
  - Each provider has its own keys, audience and multi-factor rule (`amr`
    or `acr`, which providers report differently).
  - A token is checked only with its own issuer's keys, and unknown issuers
    are refused.
  - Users are stored as `issuer|subject`, because a subject is unique only
    within one provider (migration 0013).

## Not yet
- **Real sign-in tested end to end.** The flow is tested with the OIDC
  library mocked. Sign in once through each configured provider before go-live.
- **Token refresh.** Sessions end when the access token expires (at most 8
  hours). Add refresh tokens if staff find that disruptive.
- **The Content Security Policy still allows inline scripts**, which Next.js
  needs without per-request nonces. Move to nonces before production.
- **Screens still to build:** letters, case timeline, documents, quality
  sampling and administration. Other task kinds show a summary with "Mark as
  done".

## Alternatives considered

- **Calling the API straight from the browser with the token in memory.**
  That's simpler, but any script injection could read the token, and
  cross-origin calls need CORS.
- **The GOV.UK Frontend design system.** It's excellent for public services,
  but its components are built for one-question-per-page forms. A dense,
  keyboard-driven review screen fits React Aria better. We still follow its
  patterns for error summaries and dates.
