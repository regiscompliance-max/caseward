"use client";

/**
 * Keyboard shortcuts. Single-key shortcuts can be turned off, and never fire
 * while typing in a field (WCAG 2.2 success criterion 2.1.4).
 */
import { createContext, useCallback, useContext, useEffect, useState } from "react";

const STORAGE_KEY = "caseward.singleKeyShortcuts";

type Settings = { singleKey: boolean; setSingleKey: (on: boolean) => void };
const ShortcutSettings = createContext<Settings>({ singleKey: true, setSingleKey: () => {} });

export function ShortcutProvider({ children }: { children: React.ReactNode }) {
  const [singleKey, setState] = useState(true);
  useEffect(() => {
    try {
      setState(localStorage.getItem(STORAGE_KEY) !== "off");
    } catch {
      /* storage unavailable: keep the default */
    }
  }, []);
  const setSingleKey = useCallback((on: boolean) => {
    setState(on);
    try {
      localStorage.setItem(STORAGE_KEY, on ? "on" : "off");
    } catch {
      /* not persisted */
    }
  }, []);
  return <ShortcutSettings.Provider value={{ singleKey, setSingleKey }}>{children}</ShortcutSettings.Provider>;
}

export function useShortcutSettings(): Settings {
  return useContext(ShortcutSettings);
}

function typing(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  return el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName);
}

/**
 * key: a single character (e.g. "n"), or a combination such as "mod+Enter"
 * (Ctrl or Cmd) or "alt+ArrowRight". Combinations work while typing.
 */
export function useShortcut(key: string, handler: () => void, enabled = true) {
  const { singleKey } = useShortcutSettings();
  useEffect(() => {
    if (!enabled) return;
    const parts = key.split("+");
    const main = parts[parts.length - 1];
    const needsMod = parts.includes("mod");
    const needsAlt = parts.includes("alt");
    const single = !needsMod && !needsAlt;
    function onKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented) return;
      if (single && (!singleKey || typing(event.target) || event.ctrlKey || event.metaKey || event.altKey)) return;
      if (needsMod !== (event.ctrlKey || event.metaKey)) return;
      if (needsAlt !== event.altKey) return;
      if (event.key.toLowerCase() !== main.toLowerCase() && event.key !== main) return;
      event.preventDefault();
      handler();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [key, handler, enabled, singleKey]);
}
