import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type Task = Schemas["Task"];
export type Extraction = Schemas["Extraction"];
export type ExtractionFlag = Schemas["ExtractionFlag"];
export type DeadlineAlert = Schemas["DeadlineAlert"];
export type Problem = Schemas["Problem"];

/** All calls go through the console's own server, which adds the token. */
export const api = createClient<paths>({ baseUrl: "/api/ops" });

// When the session has ended, go back to sign-in and return here afterwards.
api.use({
  onResponse({ response }) {
    if (response.status === 401 && typeof window !== "undefined") {
      const here = window.location.pathname + window.location.search;
      window.location.assign(`/sign-in?error=session_ended&returnTo=${encodeURIComponent(here)}`);
    }
    return response;
  },
});

export class ApiError extends Error {
  constructor(public status: number, public problem: Partial<Problem>) {
    super(problem.detail || problem.title || `Request failed (${status})`);
  }
}

/** Unwrap an openapi-fetch result, turning problem responses into ApiError. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || !result.response.ok) {
    throw new ApiError(result.response.status, (result.error ?? {}) as Partial<Problem>);
  }
  return result.data as T;
}

export function idempotencyKey(): string {
  return crypto.randomUUID();
}
