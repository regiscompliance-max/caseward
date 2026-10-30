/**
 * A mocked operations API, intercepted in the browser before it reaches the
 * console's proxy. Shapes follow docs/api/operations.yaml.
 */
import type { Page, Request } from "@playwright/test";

const now = Date.now();
const iso = (hours: number) => new Date(now + hours * 3_600_000).toISOString();

export const EXTRACTION_ID = "e1e1e1e1-0000-4000-8000-000000000001";
export const DOCUMENT_ID = "d0d0d0d0-0000-4000-8000-000000000001";

export const tasks = [
  { id: "a0000000-0000-4000-8000-000000000001", client_id: "c1", client_name: "Riverside Homes",
    kind: "review_extraction", subject: { kind: "extraction", id: EXTRACTION_ID }, assignee_id: null,
    status: "queued", priority: 1, sla_due_at: iso(1), earliest_deadline_due_at: null, created_at: iso(-2),
    started_at: null, completed_at: null, note: null },
  { id: "a0000000-0000-4000-8000-000000000002", client_id: "c1", client_name: "Riverside Homes",
    kind: "approve_letter", subject: { kind: "letter", id: "l1" }, assignee_id: null, status: "queued",
    priority: 2, sla_due_at: iso(20), earliest_deadline_due_at: iso(-3), created_at: iso(-5), started_at: null,
    completed_at: null, note: null },
];

export const extraction = {
  id: EXTRACTION_ID, document_id: DOCUMENT_ID, status: "succeeded",
  result: { address_line_1: "1 High St", postcode: "SW1A 1AA", uprn: null, inspection_date: "2026-09-14",
    next_inspection_due: "2031-09-14", outcome: "satisfactory", engineer_name: "Sam Spark",
    engineer_registration: "NIC 12345",
    defects: [{ code: "C2", description: "No RCD on sockets", location: "Kitchen" }] },
  field_confidence: { address_line_1: 0.98, postcode: 0.62, uprn: 0.95, inspection_date: 0.97,
    next_inspection_due: 0.96, outcome: 0.93, engineer_name: 0.99, engineer_registration: 0.94, defects: 0.92 },
  flags: [
    { field: "/postcode", reason: "low_confidence", detail: "Model confidence 0.62 is below 0.9." },
    { field: "/outcome", reason: "contradiction", detail: "Outcome \"Satisfactory\" with C2 present." },
  ],
  model_provider: "xai", model_version: "grok-test-2026-10", prompt_id: "certificate_extraction",
  prompt_version: "1", output_schema_version: "0", error: null, created_at: iso(-1), completed_at: iso(-1),
  review: null,
};

export const alerts = [
  { id: "f0000000-0000-4000-8000-000000000001", client_id: "c1", client_name: "Riverside Homes", level: "alert_80",
    raised_at: iso(-1), acknowledged_by: null, acknowledged_at: null,
    deadline: { id: "dl1", subject: { kind: "case", id: "case1" },
      rule: { key: "awaab.investigate", version: 1, title: "Investigate significant hazard", rule_pack: "awaab_social" },
      clock_started_at: iso(-200), due_at: iso(40), due_local_date: "2026-11-01", status: "open", completed_at: null,
      alert_level: "alert_80", elapsed_fraction: 0.83 } },
];

// A 2x2 grey PNG stands in for a watermarked page image.
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGNkYPjPwMDAxMDAwMDAAAAOCQEBrDuP0QAAAABJRU5ErkJggg==",
  "base64");

export type Captured = { reviews: { body: unknown; idempotencyKey: string | null }[]; claims: number; acknowledged: string[] };

export async function mockApi(page: Page, options: { emptyQueueAfterReview?: boolean } = {}): Promise<Captured> {
  const captured: Captured = { reviews: [], claims: 0, acknowledged: [] };
  const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
    ({ status, contentType: "application/json", headers, body: JSON.stringify(body) });

  await page.route("**/api/ops/**", async (route) => {
    const request: Request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/ops", "");
    const method = request.method();

    if (method === "GET" && path === "/tasks") return route.fulfill(json({ items: tasks, next_cursor: null }));
    if (method === "POST" && path === "/tasks/claim") {
      captured.claims += 1;
      if (options.emptyQueueAfterReview && captured.reviews.length > 0) return route.fulfill({ status: 204 });
      return route.fulfill(json({ ...tasks[0], status: "in_progress", assignee_id: "me" }, 200, { ETag: '"1"' }));
    }
    const task = tasks.find((t) => path === `/tasks/${t.id}`);
    if (method === "GET" && task) return route.fulfill(json(task, 200, { ETag: '"1"' }));
    if (method === "GET" && path === `/extractions/${EXTRACTION_ID}`) return route.fulfill(json(extraction));
    if (method === "GET" && path === `/documents/${DOCUMENT_ID}`) {
      return route.fulfill(json({ id: DOCUMENT_ID, client_id: "c1", property_id: "p1", doc_type: "certificate",
        certificate_type: "eicr", source: "upload", received_at: iso(-3), original_filename: "EICR.pdf",
        sha256: "a".repeat(64), mime_type: "application/pdf", size_bytes: 1000, malware_scan: "clean",
        processing: "awaiting_review", created_at: iso(-3), page_count: 2, extraction_ids: [EXTRACTION_ID] }));
    }
    if (method === "GET" && path.startsWith(`/documents/${DOCUMENT_ID}/pages/`)) {
      return route.fulfill({ status: 200, contentType: "image/png", body: PNG });
    }
    if (method === "POST" && path === `/extractions/${EXTRACTION_ID}/review`) {
      captured.reviews.push({ body: request.postDataJSON(), idempotencyKey: request.headers()["idempotency-key"] ?? null });
      return route.fulfill(json({ id: "r1", extraction_id: EXTRACTION_ID, reviewer_id: "me",
        decision: request.postDataJSON().decision, approved_result: null, notes: null, reviewed_at: iso(0),
        created: { inspection_id: null, defect_ids: [] } }, 201));
    }
    if (method === "GET" && path === "/deadline-alerts") return route.fulfill(json({ items: alerts, next_cursor: null }));
    const ack = alerts.find((a) => path === `/deadline-alerts/${a.id}/acknowledge`);
    if (method === "POST" && ack) {
      captured.acknowledged.push(ack.id);
      return route.fulfill(json({ ...ack, acknowledged_by: "me", acknowledged_at: iso(0) }));
    }
    return route.fulfill(json({ code: "not_found", title: "Not mocked", status: 404 }, 404));
  });
  return captured;
}
