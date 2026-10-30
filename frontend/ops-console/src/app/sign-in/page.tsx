"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

/**
 * Production sign-in is single sign-on with multi-factor authentication. The
 * identity provider has not been chosen yet. Until then, developers can paste
 * an access token here when CASEWARD_DEV_SIGN_IN=1; the server refuses it otherwise.
 */
export default function SignIn() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const response = await fetch("/api/session", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }) });
    if (response.ok) {
      router.push("/queue");
    } else {
      setError(response.status === 404 ? "Development sign-in is turned off. Use single sign-on."
        : "That token was not accepted.");
    }
  }

  return (
    <main id="main" style={{ maxWidth: "36rem" }}>
      <h1>Sign in to Caseward operations</h1>
      <p className="notice">Single sign-on with multi-factor authentication will replace this page.</p>
      {error && <div className="error-summary" role="alert"><p>{error}</p></div>}
      <form onSubmit={submit}>
        <div className="field">
          <label htmlFor="token">Development access token</label>
          <span className="hint" id="token-hint">For development only. Never paste a token into a shared machine.</span>
          <textarea id="token" aria-describedby="token-hint" rows={4} value={token}
            onChange={(e) => setToken(e.target.value)} autoComplete="off" spellCheck={false} />
        </div>
        <p><button className="button" type="submit">Sign in</button></p>
      </form>
    </main>
  );
}
