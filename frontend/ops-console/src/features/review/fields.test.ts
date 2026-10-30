import { CERTIFICATES, invalidFields, sameResult, toForm, toResult, ukToIso } from "./fields";

const fields = CERTIFICATES.eicr.fields;
const draft = {
  address_line_1: "1 High St", postcode: "SW1A 1AA", uprn: null, inspection_date: "2026-09-14",
  next_inspection_due: "2031-09-14", outcome: "unsatisfactory", engineer_name: "Sam Spark",
  engineer_registration: "NIC 12345",
  defects: [{ code: "C1", description: "Exposed live conductors", location: null }],
};

describe("review form conversion", () => {
  it("round-trips the AI draft exactly, so an untouched form is an approval", () => {
    const form = toForm(draft, fields);
    expect(toResult(form, fields)).toEqual(draft);
    expect(sameResult(toResult(form, fields), draft)).toBe(true);
  });

  it("turns blanks back into null and trims text", () => {
    const form = toForm(draft, fields);
    form.values.engineer_name = "  ";
    form.values.postcode = " SW1A 1AA ";
    form.defects[0].location = "  ";
    const result = toResult(form, fields);
    expect(result.engineer_name).toBeNull();
    expect(result.postcode).toBe("SW1A 1AA");
    expect((result.defects as { location: unknown }[])[0].location).toBeNull();
  });

  it("starts empty when the AI output was discarded", () => {
    const form = toForm({}, fields);
    expect(Object.values(form.values).every((v) => v === "")).toBe(true);
    expect(form.defects).toEqual([]);
  });

  it("uses the gas safety codes for gas records", () => {
    expect(CERTIFICATES.gas_safety.codes.map(([code]) => code)).toEqual(["ID", "AR", "NCS"]);
  });
});

describe("UK dates", () => {
  it("shows ISO dates as day/month/year and converts them back", () => {
    const form = toForm(draft, fields);
    expect(form.values.inspection_date).toBe("14/09/2026");
    expect(toResult(form, fields).inspection_date).toBe("2026-09-14");
  });

  it.each([["4/9/2026", "2026-09-04"], [" 31/12/2030 ", "2030-12-31"]])("reads %s as %s", (text, iso) => {
    expect(ukToIso(text)).toBe(iso);
  });

  it.each(["09/31/2026", "31/02/2027", "2026-09-14", "14.09.2026", ""])("refuses %s", (text) => {
    expect(ukToIso(text)).toBeNull();
  });

  it("names fields that cannot be saved", () => {
    const form = toForm(draft, fields);
    form.values.next_inspection_due = "09/31/2031";
    expect(invalidFields(form, fields)).toEqual(["next_inspection_due"]);
  });
});
