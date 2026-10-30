/**
 * Development sign-in only. Production sign-in is single sign-on with the
 * identity provider (not chosen yet; see docs/decisions/0012-ops-console.md).
 * This route refuses to work unless CASEWARD_DEV_SIGN_IN=1.
 */
import { cookies } from "next/headers";

const COOKIE = "cw_session";

function devOnly() {
  return process.env.CASEWARD_DEV_SIGN_IN === "1"
    ? null
    : Response.json({ code: "disabled", title: "Development sign-in is turned off", status: 404 }, { status: 404 });
}

export async function POST(request: Request) {
  const refused = devOnly();
  if (refused) return refused;
  const { token } = (await request.json()) as { token?: string };
  if (!token || token.length < 20) {
    return Response.json({ code: "invalid_token", title: "Paste a full access token", status: 400 }, { status: 400 });
  }
  (await cookies()).set(COOKIE, token, { httpOnly: true, sameSite: "strict", secure: process.env.NODE_ENV === "production",
    path: "/", maxAge: 60 * 60 * 8 });
  return new Response(null, { status: 204 });
}

export async function DELETE() {
  (await cookies()).delete(COOKIE);
  return new Response(null, { status: 204 });
}
