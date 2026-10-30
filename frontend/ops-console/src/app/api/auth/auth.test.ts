import { NextRequest } from "next/server";

const grant = vi.fn();
vi.mock("openid-client", () => ({
  None: () => "none",
  ClientSecretPost: (secret: string) => `post:${secret}`,
  discovery: vi.fn(async () => ({ issuer: "https://login.example/" })),
  randomPKCECodeVerifier: () => "verifier-123",
  calculatePKCECodeChallenge: async () => "challenge-abc",
  randomState: () => "state-xyz",
  randomNonce: () => "nonce-789",
  buildAuthorizationUrl: (_config: unknown, params: Record<string, string>) =>
    new URL(`https://login.example/authorize?${new URLSearchParams(params)}`),
  authorizationCodeGrant: (...args: unknown[]) => grant(...args),
}));

import { safeReturnTo } from "@/lib/server/oidc";

import { GET as callback } from "./callback/route";
import { GET as login } from "./login/route";

beforeEach(() => {
  grant.mockReset();
  process.env.CASEWARD_CONSOLE_URL = "https://ops.caseward.example";
  process.env.CASEWARD_CONSOLE_OIDC_PROVIDERS = JSON.stringify([
    { id: "staff", name: "Caseward staff", issuer: "https://login.example/", client_id: "console",
      scope: "openid email caseward-ops", authorization_params: { audience: "caseward-ops" } },
  ]);
});

const flowCookie = (flow: object) => `cw_oidc_flow=${encodeURIComponent(JSON.stringify(flow))}`;
const flow = { provider: "staff", verifier: "verifier-123", state: "state-xyz", nonce: "nonce-789", returnTo: "/alerts" };

describe("login", () => {
  it("refuses an unknown provider", async () => {
    const response = await login(new NextRequest("https://ops.caseward.example/api/auth/login?provider=nope"));
    expect(response.status).toBe(404);
  });

  it("redirects with PKCE, state and nonce, and remembers them in a short-lived cookie", async () => {
    const response = await login(new NextRequest(
      "https://ops.caseward.example/api/auth/login?provider=staff&returnTo=/tasks/1"));
    const location = new URL(response.headers.get("location")!);
    expect(location.origin).toBe("https://login.example");
    expect(location.searchParams.get("code_challenge")).toBe("challenge-abc");
    expect(location.searchParams.get("code_challenge_method")).toBe("S256");
    expect(location.searchParams.get("redirect_uri")).toBe("https://ops.caseward.example/api/auth/callback");
    expect(location.searchParams.get("audience")).toBe("caseward-ops");
    const cookie = response.headers.get("set-cookie")!;
    expect(cookie).toMatch(/HttpOnly/i);
    expect(cookie).toMatch(/SameSite=lax/i);
    expect(cookie).toMatch(/Max-Age=600/);
    const stored = JSON.parse(decodeURIComponent(cookie.split(";")[0].split("=").slice(1).join("=")));
    expect(stored).toEqual({ provider: "staff", verifier: "verifier-123", state: "state-xyz", nonce: "nonce-789",
      returnTo: "/tasks/1" });
  });
});

describe("callback", () => {
  it("sends people back to sign in if the flow cookie is missing", async () => {
    const response = await callback(new NextRequest("https://ops.caseward.example/api/auth/callback?code=c&state=s"));
    expect(response.headers.get("location")).toBe("https://ops.caseward.example/sign-in?error=expired");
  });

  it("fails cleanly when the provider's answer does not check out", async () => {
    grant.mockRejectedValue(new Error("state mismatch"));
    const response = await callback(new NextRequest("https://ops.caseward.example/api/auth/callback?code=c&state=bad",
      { headers: { cookie: flowCookie(flow) } }));
    expect(response.headers.get("location")).toBe("https://ops.caseward.example/sign-in?error=sign_in_failed");
    expect(response.headers.get("set-cookie")).not.toMatch(/cw_session=[^;]/);
  });

  it("checks the answer against our registered callback, with the stored verifier, state and nonce", async () => {
    grant.mockResolvedValue({ access_token: "access-1", expires_in: 3600 });
    const response = await callback(new NextRequest("http://internal-host:3100/api/auth/callback?code=c&state=state-xyz",
      { headers: { cookie: flowCookie(flow) } }));
    const [, url, checks] = grant.mock.calls[0];
    expect(String(url)).toBe("https://ops.caseward.example/api/auth/callback?code=c&state=state-xyz");
    expect(checks).toEqual({ pkceCodeVerifier: "verifier-123", expectedState: "state-xyz", expectedNonce: "nonce-789",
      idTokenExpected: true });
    expect(new URL(response.headers.get("location")!).pathname).toBe("/alerts");
    const session = response.headers.getSetCookie().find((c) => c.startsWith("cw_session="))!;
    expect(session).toMatch(/^cw_session=access-1;/);
    expect(session).toMatch(/HttpOnly/i);
    expect(session).toMatch(/SameSite=strict/i);
    expect(session).toMatch(/Max-Age=3600/);
  });

  it("never redirects off-site after sign-in", async () => {
    grant.mockResolvedValue({ access_token: "access-1", expires_in: 999_999 });
    const response = await callback(new NextRequest("https://ops.caseward.example/api/auth/callback?code=c&state=state-xyz",
      { headers: { cookie: flowCookie({ ...flow, returnTo: "//evil.example/steal" }) } }));
    expect(response.headers.get("location")).toBe("https://ops.caseward.example/queue");
    const session = response.headers.getSetCookie().find((c) => c.startsWith("cw_session="))!;
    expect(session).toMatch(/Max-Age=28800/); // capped at a working day
  });
});

it.each([["/tasks/1", "/tasks/1"], ["//evil.example", "/queue"], ["/\\evil.example", "/queue"],
  ["https://evil.example", "/queue"], [null, "/queue"]])("safeReturnTo(%s) is %s", (input, expected) => {
  expect(safeReturnTo(input)).toBe(expected);
});
