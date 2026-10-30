/**
 * Review form fields per certificate type. These mirror the backend output
 * schemas (backend/src/caseward/ai/schemas/*.v0.json), which are UNVERIFIED
 * drafts until the compliance expert confirms the field lists.
 */

export type FieldKind = "text" | "date" | "outcome";
export type FieldDef = { name: string; label: string; kind: FieldKind; hint?: string };
export type Defect = { code: string; description: string; location: string };
export type FormState = { values: Record<string, string>; defects: Defect[] };

const COMMON: FieldDef[] = [
  { name: "address_line_1", label: "First line of address", kind: "text" },
  { name: "postcode", label: "Postcode", kind: "text" },
  { name: "uprn", label: "UPRN", kind: "text", hint: "Up to 12 digits, if shown" },
  { name: "inspection_date", label: "Inspection date", kind: "date", hint: "Day/month/year, for example 14/09/2026" },
  { name: "next_inspection_due", label: "Next inspection due", kind: "date",
    hint: "Day/month/year, for example 14/09/2031" },
  { name: "outcome", label: "Overall outcome", kind: "outcome" },
  { name: "engineer_name", label: "Engineer's name", kind: "text" },
  { name: "engineer_registration", label: "Engineer's registration number", kind: "text" },
];

export const CERTIFICATES: Record<string, { name: string; fields: FieldDef[]; codes: [string, string][] }> = {
  eicr: {
    name: "Electrical installation condition report",
    fields: COMMON,
    codes: [["C1", "C1 Danger present"], ["C2", "C2 Potentially dangerous"], ["C3", "C3 Improvement recommended"],
      ["FI", "FI Further investigation"]],
  },
  gas_safety: {
    name: "Gas safety record",
    fields: COMMON.map((f) => f.name === "engineer_registration" ? { ...f, label: "Gas Safe registration number" } : f),
    codes: [["ID", "ID Immediately dangerous"], ["AR", "AR At risk"], ["NCS", "NCS Not to current standards"]],
  },
};

/**
 * Dates are typed as DD/MM/YYYY. Browser date pickers follow the machine's
 * locale (often US month-first), and a swapped day and month on a UK
 * certificate is exactly the error a reviewer must not make.
 */
export function isoToUk(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  return match ? `${match[3]}/${match[2]}/${match[1]}` : iso;
}

/** "14/09/2026" or "14/9/2026" to "2026-09-14"; null if it is not a real date. */
export function ukToIso(text: string): string | null {
  const match = /^\s*(\d{1,2})\/(\d{1,2})\/(\d{4})\s*$/.exec(text);
  if (!match) return null;
  const [day, month, year] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return null;
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

/** Fields whose text cannot be turned into what the backend expects. */
export function invalidFields(form: FormState, fields: FieldDef[]): string[] {
  return fields.filter((f) => f.kind === "date" && form.values[f.name].trim() !== ""
    && ukToIso(form.values[f.name]) === null).map((f) => f.name);
}

type Result = Record<string, unknown> & { defects?: { code: string; description: string; location: string | null }[] };

export function toForm(result: Result | null | undefined, fields: FieldDef[]): FormState {
  const values: Record<string, string> = {};
  for (const f of fields) {
    const v = result?.[f.name];
    const text = v === null || v === undefined ? "" : String(v);
    values[f.name] = f.kind === "date" ? isoToUk(text) : text;
  }
  const defects = (result?.defects ?? []).map((d) => ({ code: d.code, description: d.description,
    location: d.location ?? "" }));
  return { values, defects };
}

/** The approved result, in the backend schema's shape: blanks become null. */
export function toResult(form: FormState, fields: FieldDef[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    const v = form.values[f.name]?.trim() ?? "";
    out[f.name] = v === "" ? null : f.kind === "date" ? ukToIso(v) ?? v : v;
  }
  out.defects = form.defects.map((d) => ({ code: d.code, description: d.description.trim(),
    location: d.location.trim() === "" ? null : d.location.trim() }));
  return out;
}

export function sameResult(a: Record<string, unknown>, b: Record<string, unknown>): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}
