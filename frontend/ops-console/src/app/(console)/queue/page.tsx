import type { Metadata } from "next";

import { QueueView } from "@/features/queue/QueueView";

export const metadata: Metadata = { title: "Work queue" };

export default function QueuePage() {
  return <QueueView />;
}
