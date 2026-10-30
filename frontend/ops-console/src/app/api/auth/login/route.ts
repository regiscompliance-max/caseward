import * as client from "openid-client";
import { NextRequest, NextResponse } from "next/server";

import { callbackUrl, configuration, FLOW_COOKIE, provider, safeReturnTo } from "@/lib/server/oidc";

export async function GET(request: NextRequest) {
  const p = provider(request.nextUrl.searchParams.get("provider"));
  if (!p) {
    return NextResponse.json({ code: "unknown_provider", title: "Unknown sign-in provider", status: 404 }, { status: 404 });
  }
  const config = await configuration(p);
  const verifier = client.randomPKCECodeVerifier();
  const state = client.randomState();
  const nonce = client.randomNonce();
  const url = client.buildAuthorizationUrl(config, {
    redirect_uri: callbackUrl(),
    scope: p.scope ?? "openid email profile",
    code_challenge: await client.calculatePKCECodeChallenge(verifier),
    code_challenge_method: "S256",
    state,
    nonce,
    ...(p.authorization_params ?? {}),
  });
  const response = NextResponse.redirect(url);
  // Lax, because the identity provider redirects back to us as a top-level navigation.
  response.cookies.set(FLOW_COOKIE, JSON.stringify({
    provider: p.id, verifier, state, nonce, returnTo: safeReturnTo(request.nextUrl.searchParams.get("returnTo")),
  }), { httpOnly: true, sameSite: "lax", secure: process.env.NODE_ENV === "production", path: "/api/auth", maxAge: 600 });
  return response;
}
