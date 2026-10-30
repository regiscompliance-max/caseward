import { render, screen } from "@testing-library/react";

import { DeadlineBadge } from "./DeadlineBadge";

const now = new Date("2026-10-30T12:00:00Z");

it("says the state in words, not just colour", () => {
  render(<DeadlineBadge dueAt="2026-10-30T10:00:00Z" level="overdue" now={now} />);
  expect(screen.getByText("Overdue: overdue by 2 hours")).toBeInTheDocument();
});

it("works out overdue when no level is given", () => {
  render(<DeadlineBadge dueAt="2026-10-30T10:00:00Z" now={now} />);
  expect(screen.getByText(/Overdue/)).toBeInTheDocument();
});

it("is explicit when there is no legal deadline", () => {
  render(<DeadlineBadge dueAt={null} />);
  expect(screen.getByText("No legal deadline")).toBeInTheDocument();
});
