import type { Metadata } from "next";

import { AlertsView } from "@/features/alerts/AlertsView";

export const metadata: Metadata = { title: "Deadline alerts" };

export default function AlertsPage() {
  return <AlertsView />;
}
