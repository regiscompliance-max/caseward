"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, Dialog, Heading, Modal, ModalOverlay } from "react-aria-components";

import { api, Extraction, idempotencyKey, unwrap } from "@/api/client";
import { ErrorSummary } from "@/components/ErrorSummary";
import { FLAG_REASONS } from "@/lib/labels";
import { percent } from "@/lib/format";
import { useShortcut } from "@/lib/shortcuts";

import { DocumentViewer } from "./DocumentViewer";
import { CERTIFICATES, Defect, FieldDef, FormState, invalidFields, sameResult, toForm, toResult } from "./fields";

const LOW_CONFIDENCE = 0.9;

type Doc = { id: string; certificate_type?: string | null; page_count: number | null; property_id?: string | null };

export function ExtractionReview({ extractionId }: { extractionId: string }) {
  const [extraction, setExtraction] = useState<Extraction | null>(null);
  const [doc, setDoc] = useState<Doc | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    (async () => {
      try {
        const ex = unwrap(await api.GET("/extractions/{id}", { params: { path: { id: extractionId } } }));
        const d = unwrap(await api.GET("/documents/{id}", { params: { path: { id: ex.document_id } } }));
        setExtraction(ex);
        setDoc(d as Doc);
      } catch (e) {
        setError(e);
      }
    })();
  }, [extractionId]);

  if (error) return <ErrorSummary error={error} />;
  if (!extraction || !doc) return <p role="status">Loading the certificate…</p>;
  if (extraction.review) {
    return <p className="notice">This certificate has already been reviewed ({extraction.review.decision}).</p>;
  }
  const certificate = doc.certificate_type ? CERTIFICATES[doc.certificate_type] : undefined;
  if (!certificate) {
    return <p className="notice">There is no review form for this certificate type yet. Handle it manually.</p>;
  }
  return <ReviewForm extraction={extraction} doc={doc} fields={certificate.fields} codes={certificate.codes}
                     title={certificate.name} />;
}

function ReviewForm({ extraction, doc, fields, codes, title }: {
  extraction: Extraction; doc: Doc; fields: FieldDef[]; codes: [string, string][]; title: string;
}) {
  const router = useRouter();
  const original = useMemo(() => toForm(extraction.result as Record<string, never>, fields), [extraction, fields]);
  const [form, setForm] = useState<FormState>(original);
  const [propertyId, setPropertyId] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const errorRef = useRef<HTMLDivElement>(null);

  const changed = !sameResult(toResult(form, fields), toResult(original, fields));
  const [showInvalid, setShowInvalid] = useState(false);
  const invalid = invalidFields(form, fields);
  const flagsFor = (name: string) => extraction.flags.filter((f) => f.field === `/${name}`);
  const generalFlags = extraction.flags.filter((f) => !fields.some((d) => f.field === `/${d.name}`)
    && !f.field.startsWith("/defects"));
  const confidence = (extraction.field_confidence ?? {}) as Record<string, number>;

  const finish = useCallback(async () => {
    const next = await api.POST("/tasks/claim", { body: {} });
    if (next.response.status === 200 && next.data) router.push(`/tasks/${next.data.id}`);
    else router.push("/queue");
  }, [router]);

  const submit = useCallback(async (decision: "approved" | "corrected" | "rejected") => {
    setSubmitting(true);
    setError(null);
    try {
      const body = decision === "rejected"
        ? { decision, notes }
        : { decision, approved_result: toResult(form, fields),
            ...(propertyId ? { property_id: propertyId } : {}) };
      unwrap(await api.POST("/extractions/{id}/review", {
        params: { path: { id: extraction.id }, header: { "Idempotency-Key": idempotencyKey() } },
        body: body as never,
      }));
      await finish();
    } catch (e) {
      setError(e);
      setSubmitting(false);
      requestAnimationFrame(() => errorRef.current?.querySelector<HTMLElement>("[role=alert]")?.focus());
    }
  }, [extraction.id, form, fields, notes, propertyId, finish]);

  const approve = useCallback(() => {
    if (invalid.length > 0) {
      setShowInvalid(true);
      document.getElementById(invalid[0])?.focus();
      return;
    }
    submit(changed ? "corrected" : "approved");
  }, [submit, changed, invalid]);
  useShortcut("mod+Enter", approve, !submitting && !rejecting);

  const setValue = (name: string, value: string) => setForm((f) => ({ ...f, values: { ...f.values, [name]: value } }));
  const setDefect = (i: number, patch: Partial<Defect>) =>
    setForm((f) => ({ ...f, defects: f.defects.map((d, j) => (j === i ? { ...d, ...patch } : d)) }));

  return (
    <>
      <h1>Review certificate: {title}</h1>
      <p className="hint">
        AI draft from {extraction.model_provider} {extraction.model_version}, prompt {extraction.prompt_id} v
        {extraction.prompt_version}. Check every field against the page. Nothing reaches the client until you approve.
      </p>
      <div ref={errorRef}><ErrorSummary error={error} /></div>
      {generalFlags.length > 0 && (
        <div className="error-summary" role="alert">
          <h2>Check this certificate carefully</h2>
          <ul>{generalFlags.map((f, i) => <li key={i}>{FLAG_REASONS[f.reason]}: {f.detail}</li>)}</ul>
        </div>
      )}
      <div className="review">
        <DocumentViewer documentId={doc.id} pageCount={doc.page_count ?? 0} />
        <form className="fields" aria-label="Extracted fields" onSubmit={(e) => { e.preventDefault(); approve(); }}>
          {!doc.property_id && (
            <div className="field notice">
              <label htmlFor="property_id">Property id</label>
              <span className="hint" id="property_id-hint">This document is not linked to a property yet.</span>
              <input id="property_id" aria-describedby="property_id-hint" value={propertyId}
                     onChange={(e) => setPropertyId(e.target.value)} />
            </div>
          )}
          <fieldset>
            <legend className="label">Certificate details</legend>
            {fields.map((f) => {
              const flags = flagsFor(f.name);
              const score = confidence[f.name];
              const badDate = showInvalid && invalid.includes(f.name);
              const describedBy = [badDate && `${f.name}-error`, f.hint && `${f.name}-hint`,
                flags.length && `${f.name}-flags`, score !== undefined && `${f.name}-confidence`]
                .filter(Boolean).join(" ") || undefined;
              return (
                <div className="field-row" key={f.name}>
                  <div className="field">
                    <label htmlFor={f.name}>{f.label}</label>
                    {f.hint && <span className="hint" id={`${f.name}-hint`}>{f.hint}</span>}
                    {badDate && (
                      <span className="field-error" id={`${f.name}-error`}>
                        Enter a real date as day/month/year, for example 14/09/2026
                      </span>
                    )}
                    {f.kind === "outcome" ? (
                      <select id={f.name} value={form.values[f.name]} aria-describedby={describedBy}
                              aria-invalid={flags.length > 0 || undefined}
                              onChange={(e) => setValue(f.name, e.target.value)}>
                        <option value="">Not stated</option>
                        <option value="satisfactory">Satisfactory</option>
                        <option value="unsatisfactory">Unsatisfactory</option>
                      </select>
                    ) : (
                      <input id={f.name} type="text" value={form.values[f.name]}
                             inputMode={f.kind === "date" ? "numeric" : undefined}
                             aria-describedby={describedBy} aria-invalid={flags.length > 0 || badDate || undefined}
                             onChange={(e) => setValue(f.name, e.target.value)} />
                    )}
                  </div>
                  {score !== undefined && (
                    <span className="confidence" id={`${f.name}-confidence`} data-low={score < LOW_CONFIDENCE}>
                      AI confidence {percent(score)}{score < LOW_CONFIDENCE ? " (low)" : ""}
                    </span>
                  )}
                  {flags.length > 0 && (
                    <ul className="flags" id={`${f.name}-flags`}>
                      {flags.map((flag, i) => <li key={i}>{FLAG_REASONS[flag.reason]}: {flag.detail}</li>)}
                    </ul>
                  )}
                </div>
              );
            })}
          </fieldset>

          <fieldset>
            <legend className="label">Defects and observations ({form.defects.length})</legend>
            {form.defects.length === 0 && <p className="hint">None recorded. Add any the certificate lists.</p>}
            {form.defects.map((d, i) => (
              <div className="defect" key={i} role="group" aria-label={`Defect ${i + 1}`}>
                <div className="field">
                  <label htmlFor={`defect-${i}-code`}>Code</label>
                  <select id={`defect-${i}-code`} value={d.code} onChange={(e) => setDefect(i, { code: e.target.value })}>
                    {codes.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
                  </select>
                </div>
                <div className="field">
                  <label htmlFor={`defect-${i}-description`}>Description</label>
                  <input id={`defect-${i}-description`} value={d.description}
                         onChange={(e) => setDefect(i, { description: e.target.value })} />
                </div>
                <div className="field">
                  <label htmlFor={`defect-${i}-location`}>Location</label>
                  <input id={`defect-${i}-location`} value={d.location}
                         onChange={(e) => setDefect(i, { location: e.target.value })} />
                </div>
                <Button className="button" data-variant="secondary" aria-label={`Remove defect ${i + 1}`}
                        onPress={() => setForm((f) => ({ ...f, defects: f.defects.filter((_, j) => j !== i) }))}>
                  Remove
                </Button>
              </div>
            ))}
            <Button className="button" data-variant="secondary"
                    onPress={() => setForm((f) => ({ ...f, defects: [...f.defects, { code: codes[0][0], description: "", location: "" }] }))}>
              Add a defect
            </Button>
          </fieldset>

          <div className="actions">
            <Button className="button" type="submit" isDisabled={submitting}>
              {changed ? "Approve with corrections" : "Approve"} <kbd aria-hidden="true">Ctrl+Enter</kbd>
            </Button>
            <Button className="button" data-variant="danger" onPress={() => setRejecting(true)} isDisabled={submitting}>
              Reject
            </Button>
          </div>
          {changed && <p className="hint" role="status">You have changed the AI draft. It will be saved as a correction.</p>}
        </form>
      </div>

      <ModalOverlay isOpen={rejecting} onOpenChange={setRejecting} isDismissable className="modal-overlay">
        <Modal className="modal">
          <Dialog>
            <Heading slot="title">Reject this extraction</Heading>
            <p>Nothing will be created from it. Say why, for the audit trail.</p>
            <div className="field">
              <label htmlFor="reject-notes">Reason</label>
              <textarea id="reject-notes" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />
            </div>
            <div className="actions">
              <Button className="button" data-variant="danger" isDisabled={notes.trim().length < 3 || submitting}
                      onPress={() => { setRejecting(false); submit("rejected"); }}>
                Reject extraction
              </Button>
              <Button className="button" data-variant="secondary" onPress={() => setRejecting(false)}>Cancel</Button>
            </div>
          </Dialog>
        </Modal>
      </ModalOverlay>
    </>
  );
}
