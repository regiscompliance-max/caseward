"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useState } from "react";
import { Button, Dialog, Heading, Modal, ModalOverlay, Switch } from "react-aria-components";

import { ShortcutProvider, useShortcut, useShortcutSettings } from "@/lib/shortcuts";

const NAV = [
  { href: "/queue", label: "Queue" },
  { href: "/alerts", label: "Deadline alerts" },
];

export const SHORTCUTS: [string, string][] = [
  ["N", "Take the next task (queue)"],
  ["J / K", "Move down or up the queue"],
  ["Enter", "Open the selected task"],
  ["Alt + → / Alt + ←", "Next or previous page (review)"],
  ["Ctrl + Enter", "Submit the review"],
  ["?", "Show this list"],
];

function ShortcutHelp({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { singleKey, setSingleKey } = useShortcutSettings();
  return (
    <ModalOverlay isOpen={open} onOpenChange={onOpenChange} isDismissable className="modal-overlay">
      <Modal className="modal">
        <Dialog>
          {({ close }) => (
            <>
              <Heading slot="title">Keyboard shortcuts</Heading>
              <table>
                <tbody>
                  {SHORTCUTS.map(([keys, action]) => (
                    <tr key={keys}><th scope="row"><kbd>{keys}</kbd></th><td>{action}</td></tr>
                  ))}
                </tbody>
              </table>
              <p>
                <Switch isSelected={singleKey} onChange={setSingleKey} className="switch">
                  <span className="switch-track" aria-hidden="true"><span className="switch-thumb" /></span>
                  Single-key shortcuts (N, J, K, ?) are {singleKey ? "on" : "off"}
                </Switch>
              </p>
              <Button className="button" onPress={close}>Close</Button>
            </>
          )}
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [help, setHelp] = useState(false);
  useShortcut("?", useCallback(() => setHelp(true), []));
  return (
    <>
      <a className="skip-link" href="#main">Skip to main content</a>
      <header className="header">
        <span className="brand">Caseward operations</span>
        <nav aria-label="Main">
          <ul>
            {NAV.map((item) => (
              <li key={item.href}>
                <Link href={item.href} aria-current={pathname?.startsWith(item.href) ? "page" : undefined}>
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <span className="spacer" />
        <Button className="button" data-variant="secondary" onPress={() => setHelp(true)}>
          Shortcuts <kbd aria-hidden="true">?</kbd>
        </Button>
      </header>
      <main id="main" tabIndex={-1}>{children}</main>
      <ShortcutHelp open={help} onOpenChange={setHelp} />
    </>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  return <ShortcutProvider><Shell>{children}</Shell></ShortcutProvider>;
}
