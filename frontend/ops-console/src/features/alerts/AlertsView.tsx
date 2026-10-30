"use client";

import { useEffect, useState } from "react";
import { Button } from "react-aria-components";

import { api, DeadlineAlert, unwrap } from "@/api/client";
import { DeadlineBadge } from "@/components/DeadlineBadge";
import { ErrorSummary } from "@/components/ErrorSummary";
import { ukDateTime } from "@/lib/format";

export function AlertsView() {
  const [alerts, setAlerts] = useState<DeadlineAlert[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    api.GET("/deadline-alerts", { params: { query: { state: "open", limit: 100 } } })
      .then((result) => setAlerts(unwrap(result).items))
      .catch(setError);
  }, []);

  async function acknowledge(alert: DeadlineAlert) {
    setError(null);
    try {
      unwrap(await api.POST("/deadline-alerts/{id}/acknowledge", { params: { path: { id: alert.id } } }));
      setAlerts((current) => current?.filter((a) => a.id !== alert.id) ?? null);
      setStatus(`Alert for ${alert.client_name ?? "the client"} acknowledged. The deadline itself is unchanged.`);
    } catch (e) {
      setError(e);
    }
  }

  return (
    <>
      <h1>Deadline alerts</h1>
      <ErrorSummary error={error} />
      <p role="status" className={status ? "notice" : undefined}>{status}</p>
      {alerts === null && !error && <p role="status">Loading alerts…</p>}
      {alerts && alerts.length === 0 && <p>No open alerts.</p>}
      {alerts && alerts.length > 0 && (
        <table>
          <caption className="hint" style={{ textAlign: "left", captionSide: "top" }}>
            {alerts.length} open alerts, newest first. Acknowledging an alert does not change or hide the deadline.
          </caption>
          <thead>
            <tr>
              <th scope="col">Deadline</th>
              <th scope="col">Rule</th>
              <th scope="col">Client</th>
              <th scope="col">Raised</th>
              <th scope="col"><span className="visually-hidden">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {alerts.map((alert) => (
              <tr key={alert.id}>
                <td><DeadlineBadge dueAt={alert.deadline.due_at} level={alert.level} /></td>
                <td>{alert.deadline.rule.title}</td>
                <td>{alert.client_name}</td>
                <td>{ukDateTime(alert.raised_at)}</td>
                <td>
                  <Button className="button" data-variant="secondary" onPress={() => acknowledge(alert)}
                          aria-label={`Acknowledge alert: ${alert.deadline.rule.title}, ${alert.client_name}`}>
                    Acknowledge
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
