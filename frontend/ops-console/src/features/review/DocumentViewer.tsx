"use client";

import { useCallback, useState } from "react";
import { Button } from "react-aria-components";

import { useShortcut } from "@/lib/shortcuts";

/**
 * Page images only, watermarked by the API with the viewer's name. Originals
 * are never downloaded to the remote desktop.
 */
export function DocumentViewer({ documentId, pageCount }: { documentId: string; pageCount: number }) {
  const [page, setPage] = useState(1);
  const [failed, setFailed] = useState(false);
  const go = useCallback((delta: number) => {
    setFailed(false);
    setPage((p) => Math.min(Math.max(p + delta, 1), pageCount));
  }, [pageCount]);
  useShortcut("alt+ArrowRight", useCallback(() => go(1), [go]));
  useShortcut("alt+ArrowLeft", useCallback(() => go(-1), [go]));

  return (
    <section className="viewer" aria-label="Certificate pages">
      <div className="viewer-controls">
        <Button className="button" data-variant="secondary" onPress={() => go(-1)} isDisabled={page <= 1}>
          Previous page
        </Button>
        <span aria-live="polite">Page {page} of {pageCount}</span>
        <Button className="button" data-variant="secondary" onPress={() => go(1)} isDisabled={page >= pageCount}>
          Next page
        </Button>
      </div>
      {pageCount === 0 ? (
        <p>This document has no pages to show yet.</p>
      ) : failed ? (
        <p role="alert">Page {page} could not be loaded. Try again, or move to another page.</p>
      ) : (
        // A plain img: watermarked, uncached page images from our own API, not optimised assets.
        <img src={`/api/ops/documents/${documentId}/pages/${page}`} alt={`Certificate page ${page} of ${pageCount}`}
             onError={() => setFailed(true)} />
      )}
    </section>
  );
}
