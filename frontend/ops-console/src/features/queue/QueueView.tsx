"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "react-aria-components";

import { api, Task, unwrap } from "@/api/client";
import { DeadlineBadge } from "@/components/DeadlineBadge";
import { ErrorSummary } from "@/components/ErrorSummary";
import { PRIORITIES, TASK_KINDS, TASK_STATUSES } from "@/lib/labels";
import { timeLeft } from "@/lib/format";
import { useShortcut } from "@/lib/shortcuts";

type Assignee = "unassigned" | "me" | "all";

export function QueueView() {
  const router = useRouter();
  const [assignee, setAssignee] = useState<Assignee>("unassigned");
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [selected, setSelected] = useState(0);
  const [error, setError] = useState<unknown>(null);
  const [message, setMessage] = useState<string | null>(null);
  const rows = useRef<(HTMLTableRowElement | null)[]>([]);

  useEffect(() => {
    let cancelled = false;
    setTasks(null);
    const query = { limit: 100, ...(assignee === "all" ? {} : { assignee }) };
    api.GET("/tasks", { params: { query } })
      .then((result) => {
        if (!cancelled) {
          setTasks(unwrap(result).items.filter((t) => t.status !== "done" && t.status !== "cancelled"));
          setSelected(0);
        }
      })
      .catch((e) => !cancelled && setError(e));
    return () => { cancelled = true; };
  }, [assignee]);

  const takeNext = useCallback(async () => {
    setError(null);
    try {
      const result = await api.POST("/tasks/claim", { body: {} });
      if (result.response.status === 204) {
        setMessage("The queue is empty. Nothing to take.");
        return;
      }
      router.push(`/tasks/${unwrap(result).id}`);
    } catch (e) {
      setError(e);
    }
  }, [router]);

  const move = useCallback((delta: number) => {
    setSelected((current) => {
      const next = Math.min(Math.max(current + delta, 0), Math.max((tasks?.length ?? 1) - 1, 0));
      rows.current[next]?.scrollIntoView({ block: "nearest" });
      return next;
    });
  }, [tasks]);

  useShortcut("n", takeNext);
  useShortcut("j", useCallback(() => move(1), [move]));
  useShortcut("k", useCallback(() => move(-1), [move]));
  useShortcut("Enter", useCallback(() => {
    if (tasks?.[selected]) router.push(`/tasks/${tasks[selected].id}`);
  }, [router, tasks, selected]), Boolean(tasks?.length));

  return (
    <>
      <h1>Work queue</h1>
      <ErrorSummary error={error} />
      {message && <p className="notice" role="status">{message}</p>}
      <div className="toolbar">
        <Button className="button" onPress={takeNext}>Take next task <kbd aria-hidden="true">N</kbd></Button>
        <div className="field">
          <label htmlFor="assignee">Show</label>
          <select id="assignee" value={assignee} onChange={(e) => setAssignee(e.target.value as Assignee)}>
            <option value="unassigned">Unassigned tasks</option>
            <option value="me">My tasks</option>
            <option value="all">All open tasks</option>
          </select>
        </div>
      </div>
      {tasks === null && !error && <p role="status">Loading the queue…</p>}
      {tasks && tasks.length === 0 && <p role="status">No tasks here.</p>}
      {tasks && tasks.length > 0 && (
        <table aria-describedby="queue-order">
          <caption id="queue-order" className="hint" style={{ textAlign: "left", captionSide: "top" }}>
            {tasks.length} tasks, most urgent first: priority, then the nearest legal deadline, then the review SLA.
          </caption>
          <thead>
            <tr>
              <th scope="col">Priority</th>
              <th scope="col">Task</th>
              <th scope="col">Client</th>
              <th scope="col">Legal deadline</th>
              <th scope="col">Review SLA</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((task, i) => (
              <tr key={task.id} ref={(el) => { rows.current[i] = el; }} aria-selected={i === selected}
                  onClick={() => setSelected(i)}>
                <td>{PRIORITIES[task.priority] ?? task.priority}</td>
                <td><Link href={`/tasks/${task.id}`}>{TASK_KINDS[task.kind]}</Link></td>
                <td>{task.client_name}</td>
                <td><DeadlineBadge dueAt={task.earliest_deadline_due_at} /></td>
                <td>{task.sla_due_at ? timeLeft(task.sla_due_at) : "None"}</td>
                <td>{TASK_STATUSES[task.status]}{task.note ? ` (${task.note})` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
