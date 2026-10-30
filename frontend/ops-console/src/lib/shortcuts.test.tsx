import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import { ShortcutProvider, useShortcut, useShortcutSettings } from "./shortcuts";

function Harness() {
  const [count, setCount] = useState(0);
  const [submits, setSubmits] = useState(0);
  const { setSingleKey } = useShortcutSettings();
  useShortcut("n", () => setCount((c) => c + 1));
  useShortcut("mod+Enter", () => setSubmits((c) => c + 1));
  return (
    <>
      <input aria-label="notes" />
      <output aria-label="count">{count}</output>
      <output aria-label="submits">{submits}</output>
      <button onClick={() => setSingleKey(false)}>off</button>
    </>
  );
}

function setup() {
  localStorage.clear();
  render(<ShortcutProvider><Harness /></ShortcutProvider>);
  return userEvent.setup();
}

it("fires single-key shortcuts outside fields only", async () => {
  const user = setup();
  await user.keyboard("n");
  expect(screen.getByLabelText("count")).toHaveTextContent("1");
  await user.click(screen.getByLabelText("notes"));
  await user.keyboard("n");
  expect(screen.getByLabelText("count")).toHaveTextContent("1");
  expect(screen.getByLabelText("notes")).toHaveValue("n");
});

it("lets people turn single-key shortcuts off (WCAG 2.1.4)", async () => {
  const user = setup();
  await user.click(screen.getByText("off"));
  await act(async () => { await user.keyboard("n"); });
  expect(screen.getByLabelText("count")).toHaveTextContent("0");
  expect(localStorage.getItem("caseward.singleKeyShortcuts")).toBe("off");
});

it("fires modifier shortcuts even while typing", async () => {
  const user = setup();
  await user.click(screen.getByLabelText("notes"));
  await user.keyboard("{Control>}{Enter}{/Control}");
  expect(screen.getByLabelText("submits")).toHaveTextContent("1");
});
