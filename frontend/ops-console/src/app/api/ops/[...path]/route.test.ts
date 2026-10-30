import { NextRequest } from "next/server";

let cookieValue: string | undefined = "token-abc";
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: () => (cookieValue ? { value: cookieValue } : undefined) }),
}));

import { GET, POST } from "./route";

const params = (path: string[]) => ({ params: Promise.resolve({ path }) });

beforeEach(() => {
  cookieValue = "token-abc";
  process.env.CASEWARD_OPS_API_URL = "https://ops.example/ops/v1";
});

it("adds the token from the httpOnly cookie and forwards only contract headers", async () => {
  const upstream = vi.fn(async () => new Response("{}", { status: 200, headers: {
    "content-type": "application/json", etag: '"7"', "set-cookie": "evil=1", server: "x" } }));
  vi.stubGlobal("fetch", upstream);
  const request = new NextRequest("http://console.example/api/ops/tasks/claim?x=1", {
    method: "POST", body: "{}", headers: { "content-type": "application/json", "idempotency-key": "k-1",
      cookie: "cw_session=token-abc; other=1", "x-forwarded-for": "1.2.3.4" } });

  const response = await POST(request, params(["tasks", "claim"]));

  const [url, init] = upstream.mock.calls[0] as unknown as [URL, RequestInit];
  expect(String(url)).toBe("https://ops.example/ops/v1/tasks/claim?x=1");
  const sent = init.headers as Headers;
  expect(sent.get("authorization")).toBe("Bearer token-abc");
  expect(sent.get("idempotency-key")).toBe("k-1");
  expect(sent.get("cookie")).toBeNull();
  expect(sent.get("x-forwarded-for")).toBeNull();
  expect(response.headers.get("etag")).toBe('"7"');
  expect(response.headers.get("set-cookie")).toBeNull();
  expect(response.headers.get("cache-control")).toBe("no-store");
});

it("refuses without a session", async () => {
  cookieValue = undefined;
  const response = await GET(new NextRequest("http://console.example/api/ops/tasks"), params(["tasks"]));
  expect(response.status).toBe(401);
});

it("refuses dot segments, which would escape the API base path", async () => {
  const upstream = vi.fn(async () => new Response(null, { status: 404 }));
  vi.stubGlobal("fetch", upstream);
  for (const path of [["..", "admin"], ["tasks", ".", "x"], ["tasks", ""]]) {
    const response = await GET(new NextRequest("http://console.example/api/ops/x"), params(path));
    expect(response.status).toBe(400);
  }
  expect(upstream).not.toHaveBeenCalled();
});

it("encodes other characters so a segment stays one segment", async () => {
  const upstream = vi.fn(async () => new Response(null, { status: 404 }));
  vi.stubGlobal("fetch", upstream);
  await GET(new NextRequest("http://console.example/api/ops/x"), params(["tasks", "a/b?c"]));
  const [url] = upstream.mock.calls[0] as unknown as [URL];
  expect(String(url)).toBe("https://ops.example/ops/v1/tasks/a%2Fb%3Fc");
});
