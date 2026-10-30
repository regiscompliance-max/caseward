import { ApiError } from "@/api/client";

export function ErrorSummary({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof ApiError ? error.message : "Something went wrong. Try again.";
  const fields = error instanceof ApiError ? error.problem.errors ?? [] : [];
  return (
    <div className="error-summary" role="alert" tabIndex={-1}>
      <h2>There is a problem</h2>
      <p>{message}</p>
      {fields.length > 0 && (
        <ul>{fields.map((f) => <li key={f.field}>{f.field}: {f.message}</li>)}</ul>
      )}
    </div>
  );
}
