/**
 * Server-side proxy to the operations API. The browser never holds the API
 * token: it lives in an httpOnly cookie and is added here. Only the headers
 * the API contract uses are passed through.
 */
import { cookies } from "next/headers";
import { NextRequest } from "next/server";

const SESSION_COOKIE = "cw_session";
const FORWARD_REQUEST = ["content-type", "if-match", "idempotency-key", "x-access-reason", "x-request-id"];
const FORWARD_RESPONSE = ["content-type", "etag", "location", "cache-control", "x-request-id", "idempotent-replayed",
  "retry-after"];

async function proxy(request: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const base = process.env.CASEWARD_OPS_API_URL;
  if (!base) {
    return Response.json({ code: "not_configured", title: "CASEWARD_OPS_API_URL is not set", status: 500 },
      { status: 500 });
  }
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) {
    return Response.json({ code: "missing_token", title: "Not signed in", status: 401 }, { status: 401 });
  }
  const { path } = await params;
  if (path.some((segment) => segment === "." || segment === ".." || segment === "")) {
    // Encoding leaves dot segments alone, and URL parsing would resolve them out of the API base path.
    return Response.json({ code: "invalid_path", title: "Invalid path", status: 400 }, { status: 400 });
  }
  const url = new URL(`${base.replace(/\/$/, "")}/${path.map(encodeURIComponent).join("/")}`);
  url.search = request.nextUrl.search;

  const headers = new Headers({ Authorization: `Bearer ${token}` });
  for (const name of FORWARD_REQUEST) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const hasBody = !["GET", "HEAD"].includes(request.method);
  const upstream = await fetch(url, {
    method: request.method,
    headers,
    body: hasBody ? await request.arrayBuffer() : undefined,
    redirect: "manual",
    cache: "no-store",
  });
  const out = new Headers();
  for (const name of FORWARD_RESPONSE) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  out.set("Cache-Control", "no-store");
  return new Response(upstream.status === 204 ? null : upstream.body, { status: upstream.status, headers: out });
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
