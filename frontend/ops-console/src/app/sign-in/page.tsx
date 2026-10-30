import type { Metadata } from "next";

import { providers, safeReturnTo } from "@/lib/server/oidc";

import { DevSignIn } from "./DevSignIn";

export const metadata: Metadata = { title: "Sign in" };
export const dynamic = "force-dynamic";

const ERRORS: Record<string, string> = {
  expired: "Your sign-in took too long or was interrupted. Start again.",
  sign_in_failed: "Sign-in did not complete. Start again, and contact your administrator if it keeps happening.",
  unknown_provider: "That sign-in option is no longer available.",
  session_ended: "Your session has ended. Sign in again to carry on.",
};

export default async function SignIn({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const params = await searchParams;
  const returnTo = safeReturnTo(params.returnTo);
  const options = providers();
  const error = params.error ? ERRORS[params.error] ?? ERRORS.sign_in_failed : null;
  return (
    <main id="main" style={{ maxWidth: "36rem" }}>
      <h1>Sign in to Caseward operations</h1>
      {error && <div className="error-summary" role="alert"><p>{error}</p></div>}
      {params.signed_out && <p className="notice" role="status">You have signed out.</p>}
      {options.length > 0 ? (
        <>
          <p>Sign in with your organisation&apos;s account. You will be asked for a second factor.</p>
          <ul className="sign-in-options">
            {options.map((p) => (
              <li key={p.id}>
                <a className="button" href={`/api/auth/login?provider=${encodeURIComponent(p.id)}&returnTo=${encodeURIComponent(returnTo)}`}>
                  Sign in with {p.name}
                </a>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="notice">No sign-in providers are configured. Set CASEWARD_CONSOLE_OIDC_PROVIDERS.</p>
      )}
      {process.env.CASEWARD_DEV_SIGN_IN === "1" && <DevSignIn returnTo={returnTo} />}
    </main>
  );
}
