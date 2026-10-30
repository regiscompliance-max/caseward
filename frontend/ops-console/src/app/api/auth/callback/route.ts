import * as client from "openid-client";
import { NextRequest, NextResponse } from "next/server";

import { callbackUrl, configuration, FLOW_COOKIE, provider, safeReturnTo, SESSION_COOKIE } from "@/lib/server/oidc";

type Flow = { provider: string; verifier: string; state: string; nonce: string; returnTo: string };

function failed(request: NextRequest, reason: string) {
  const response = NextResponse.redirect(new URL(`/sign-in?error=${reason}`, request.nextUrl));
  response.cookies.delete({ name: FLOW_COOKIE, path: "/api/auth" });
  return response;
}

export async function GET(request: NextRequest) {
  let flow: Flow;
  try {
    flow = JSON.parse(request.cookies.get(FLOW_COOKIE)?.value ?? "") as Flow;
  } catch {
    return failed(request, "expired");
  }
  const p = provider(flow.provider);
  if (!p) return failed(request, "unknown_provider");

  // Check the provider's answer against the callback URL we registered, not
  // whatever host a proxy in front of us reports.
  const current = new URL(callbackUrl());
  current.search = request.nextUrl.search;
  let tokens: client.TokenEndpointResponse & client.TokenEndpointResponseHelpers;
  try {
    tokens = await client.authorizationCodeGrant(await configuration(p), current, {
      pkceCodeVerifier: flow.verifier, expectedState: flow.state, expectedNonce: flow.nonce, idTokenExpected: true,
    });
  } catch {
    return failed(request, "sign_in_failed");
  }

  const response = NextResponse.redirect(new URL(safeReturnTo(flow.returnTo), request.nextUrl));
  response.cookies.delete({ name: FLOW_COOKIE, path: "/api/auth" });
  response.cookies.set(SESSION_COOKIE, tokens.access_token, {
    httpOnly: true, sameSite: "strict", secure: process.env.NODE_ENV === "production", path: "/",
    maxAge: Math.max(60, Math.min(tokens.expires_in ?? 3600, 8 * 3600)),
  });
  return response;
}
