/**
 * Single sign-on with several identity providers: our staff directory and any
 * other organisation that signs in to the console. Configured as JSON in
 * CASEWARD_CONSOLE_OIDC_PROVIDERS:
 *
 *   [{ "id": "staff", "name": "Caseward staff", "issuer": "https://login.example/tenant/v2.0",
 *      "client_id": "...", "client_secret": "...", "scope": "openid email api://caseward-ops/.default",
 *      "authorization_params": { "audience": "caseward-ops" } }]
 *
 * The access token's audience must be the operations API, and the API itself
 * enforces multi-factor authentication for each provider.
 */
import * as client from "openid-client";

export type ProviderConfig = {
  id: string;
  name: string;
  issuer: string;
  client_id: string;
  client_secret?: string;
  scope?: string;
  authorization_params?: Record<string, string>;
};

export function providers(): ProviderConfig[] {
  try {
    const parsed = JSON.parse(process.env.CASEWARD_CONSOLE_OIDC_PROVIDERS ?? "[]") as ProviderConfig[];
    return parsed.filter((p) => p.id && p.issuer && p.client_id);
  } catch {
    return [];
  }
}

export function provider(id: string | null): ProviderConfig | undefined {
  return providers().find((p) => p.id === id);
}

const discovered = new Map<string, Promise<client.Configuration>>();

export function configuration(p: ProviderConfig): Promise<client.Configuration> {
  if (!discovered.has(p.id)) {
    const auth = p.client_secret ? client.ClientSecretPost(p.client_secret) : client.None();
    const pending = client.discovery(new URL(p.issuer), p.client_id, undefined, auth);
    pending.catch(() => discovered.delete(p.id)); // retry discovery next time if it failed
    discovered.set(p.id, pending);
  }
  return discovered.get(p.id)!;
}

export function callbackUrl(): string {
  return `${(process.env.CASEWARD_CONSOLE_URL ?? "http://localhost:3100").replace(/\/$/, "")}/api/auth/callback`;
}

/** Only same-site paths, so sign-in cannot be used to redirect somewhere else. */
export function safeReturnTo(value: string | null | undefined): string {
  return value && value.startsWith("/") && !value.startsWith("//") && !value.startsWith("/\\") ? value : "/queue";
}

export const SESSION_COOKIE = "cw_session";
export const FLOW_COOKIE = "cw_oidc_flow";
