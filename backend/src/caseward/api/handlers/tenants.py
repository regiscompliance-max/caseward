"""Tenancies, tenant contacts and household vulnerability.

Landlords and staff only: providers never see tenant data. Vulnerability
factors are special-category data. Reads go through the database function
that checks access, requires a reason and audits the read (decision 0003).
"""

from caseward.api.framework import Call, Result
from caseward.api.problems import Problem, forbidden, not_found

CONTACT_COLUMNS = "id, tenancy_id, full_name, phone, email, preferred_channel, xmin::text as row_version"


def _contacts(call: Call, tenancy_ids) -> dict:
    out: dict = {}
    for c in call.rows(f"select {CONTACT_COLUMNS} from caseward.tenant_contact where tenancy_id = any(%s)"
                       " order by full_name", [list(tenancy_ids)]):
        out.setdefault(c["tenancy_id"], []).append(c)
    return out


def list_tenancies(call: Call) -> Result:
    prop = call.row("select id, client_id from caseward.property where id = %s", [call.path["id"]])
    if prop is None:
        raise not_found()
    tenancies = call.rows("select id, property_id, starts_on, ends_on from caseward.tenancy where property_id = %s"
                          " order by starts_on desc", [prop["id"]])
    contacts = _contacts(call, [t["id"] for t in tenancies])
    return Result(200, {"items": [{**t, "contacts": contacts.get(t["id"], [])} for t in tenancies]},
                  audit_client_id=prop["client_id"])


def create_tenancy(call: Call) -> Result:
    prop = call.row("select id, client_id from caseward.property where id = %s", [call.path["id"]])
    if prop is None:
        raise not_found()
    row = call.row("insert into caseward.tenancy (client_id, property_id, starts_on, ends_on) values (%s, %s, %s, %s)"
                   " returning id, property_id, starts_on, ends_on",
                   [prop["client_id"], prop["id"], call.body["starts_on"], call.body.get("ends_on")])
    return Result(201, {**row, "contacts": []})


def create_tenant_contact(call: Call) -> Result:
    tenancy = call.row("select id, client_id from caseward.tenancy where id = %s", [call.path["id"]])
    if tenancy is None:
        raise not_found()
    b = call.body
    row = call.row(f"insert into caseward.tenant_contact (client_id, tenancy_id, full_name, phone, email,"
                   f" preferred_channel) values (%s, %s, %s, %s, %s, %s) returning {CONTACT_COLUMNS}",
                   [tenancy["client_id"], tenancy["id"], b["full_name"], b.get("phone"), b.get("email"),
                    b.get("preferred_channel")])
    return Result(201, row)


def update_tenant_contact(call: Call) -> Result:
    contact = call.row(f"select {CONTACT_COLUMNS} from caseward.tenant_contact where id = %s for update",
                       [call.path["id"]])
    if contact is None:
        raise not_found()
    if call.headers["If-Match"].strip().strip('"') != contact["row_version"]:
        raise Problem(412, "stale", "The contact changed since you loaded it; reload and try again")
    changes = {k: call.body[k] for k in ("full_name", "phone", "email", "preferred_channel") if k in call.body}
    if changes.get("full_name", "x") is None:
        raise Problem(422, "rule_violation", "A contact needs a name")
    row = call.row(f"update caseward.tenant_contact set {', '.join(f'{k} = %s' for k in changes)} where id = %s"
                   f" returning {CONTACT_COLUMNS}", [*changes.values(), contact["id"]])
    return Result(200, row, headers={"ETag": f'"{row["row_version"]}"'})


def _special_category_client(call: Call, tenancy_id) -> object:
    tenancy = call.row("select client_id from caseward.tenancy where id = %s", [tenancy_id])
    if tenancy is None:
        raise not_found()
    if not call.row("select caseward.can_view_special_category(%s) as ok", [tenancy["client_id"]])["ok"]:
        raise forbidden("You do not have access to household vulnerability details.")
    return tenancy["client_id"]


def list_vulnerabilities(call: Call) -> Result:
    _special_category_client(call, call.path["id"])
    rows = call.rows("select * from caseward.read_household_vulnerabilities(%s, %s)",
                     [call.path["id"], call.headers["X-Access-Reason"]])
    # The database function wrote the audit event, with the reason.
    return Result(200, {"items": rows}, headers={"Cache-Control": "no-store"}, audited=True)


def create_vulnerability(call: Call) -> Result:
    client_id = _special_category_client(call, call.path["id"])
    b = call.body
    call.conn.execute(
        "insert into caseward.household_vulnerability (client_id, tenancy_id, factor, notes, source, recorded_by)"
        " values (%s, %s, %s, %s, %s, %s)",
        [client_id, call.path["id"], b["factor"], b.get("notes"), b["source"], call.principal.user_id])
    return Result(201)


def end_vulnerability(call: Call) -> Result:
    call.conn.execute("select caseward.end_household_vulnerability(%s)", [call.path["id"]])
    return Result(204)
