"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Button } from "react-aria-components";

import { api, Task, unwrap } from "@/api/client";
import { DeadlineBadge } from "@/components/DeadlineBadge";
import { ErrorSummary } from "@/components/ErrorSummary";
import { ExtractionReview } from "@/features/review/ExtractionReview";
import { PRIORITIES, TASK_KINDS, TASK_STATUSES } from "@/lib/labels";

export function TaskView({ taskId }: { taskId: string }) {
  const router = useRouter();
  const [task, setTask] = useState<Task | null>(null);
  const [etag, setEtag] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api.GET("/tasks/{id}", { params: { path: { id: taskId } } })
      .then((result) => { setTask(unwrap(result)); setEtag(result.response.headers.get("ETag")); })
      .catch(setError);
  }, [taskId]);

  if (error) return <ErrorSummary error={error} />;
  if (!task) return <p role="status">Loading the task…</p>;
  if (task.kind === "review_extraction" && task.subject?.kind === "extraction" && task.subject.id
      && task.status !== "done") {
    return <ExtractionReview extractionId={task.subject.id} />;
  }

  async function complete() {
    try {
      unwrap(await api.POST("/tasks/{id}/complete", { params: { path: { id: taskId }, header: { "If-Match": etag ?? "" } } }));
      router.push("/queue");
    } catch (e) {
      setError(e);
    }
  }

  return (
    <>
      <h1>{TASK_KINDS[task.kind]}</h1>
      <dl>
        <dt>Client</dt><dd>{task.client_name}</dd>
        <dt>Priority</dt><dd>{PRIORITIES[task.priority]}</dd>
        <dt>Status</dt><dd>{TASK_STATUSES[task.status]}</dd>
        <dt>Legal deadline</dt><dd><DeadlineBadge dueAt={task.earliest_deadline_due_at} /></dd>
        {task.note && (<><dt>Note</dt><dd>{task.note}</dd></>)}
      </dl>
      <p className="notice">The screen for this kind of task is not built yet. Do the work, then mark it done.</p>
      {task.status !== "done" && <Button className="button" onPress={complete}>Mark as done</Button>}
    </>
  );
}
