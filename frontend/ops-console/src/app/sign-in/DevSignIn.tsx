"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

/** Development only: the server refuses this unless CASEWARD_DEV_SIGN_IN=1. */
export function DevSignIn({ returnTo }: { returnTo: string }) {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const response = await fetch("/api/session", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }) });
    if (response.ok) router.push(returnTo);
    else setError("That token was not accepted.");
  }

  return (
    <form onSubmit={submit}>
      <h2>Development sign-in</h2>
      {error && <div className="error-summary" role="alert"><p>{error}</p></div>}
      <div className="field">
        <label htmlFor="token">Development access token</label>
        <span className="hint" id="token-hint">For development only. Never paste a token into a shared machine.</span>
        <textarea id="token" aria-describedby="token-hint" rows={4} value={token}
          onChange={(e) => setToken(e.target.value)} autoComplete="off" spellCheck={false} />
      </div>
      <p><button className="button" data-variant="secondary" type="submit">Sign in with token</button></p>
    </form>
  );
}
