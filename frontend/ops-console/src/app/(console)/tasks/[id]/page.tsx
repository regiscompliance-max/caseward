import { TaskView } from "@/features/tasks/TaskView";

export default async function TaskPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <TaskView taskId={id} />;
}
