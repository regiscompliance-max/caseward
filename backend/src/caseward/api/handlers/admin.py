"""Administration: clients, engagements, users and memberships."""

from datetime import datetime
from uuid import uuid4

from caseward.api.framework import Call, Result
from caseward.api.handlers.shared import decode_cursor, limit_plus_one, page
from caseward.api.problems import Problem, not_found
from caseward.deadlines import UK

USER_COLUMNS = "u.id, u.email, u.display_name, u.staff_role, u.is_active, u.xmin::text as row_version"
MEMBERSHIP_SELECT = """
  select m.user_id, m.client_id, c.name as client_name, c.kind as client_kind, m.role, m.special_category_access
  from caseward.membership m join caseward.client c on c.id = m.client_id
"""


def list_clients(call: Call) -> Result:
    where, params = ["true"], []
    if "kind" in call.query:
        where.append("kind = %s")
        params.append(call.query["kind"])
    cursor = decode_cursor(call)
    if cursor:
        where.append("(name, id) > (%s, %s::uuid)")
        params += cursor
    rows = call.rows(f"select id, name, kind, created_at, name as _name from caseward.client"
                     f" where {' and '.join(where)} order by name, id limit %s", params + [limit_plus_one(call)])
    return Result(200, page(call, rows, ["_name", "id"]))


def create_client(call: Call) -> Result:
    row = call.row("insert into caseward.client (name, kind) values (%s, %s) returning id, name, kind, created_at",
                   [call.body["name"].strip(), call.body["kind"]])
    return Result(201, row)


def _landlord(call: Call, client_id) -> dict:
    row = call.row("select id, kind from caseward.client where id = %s", [client_id])
    if row is None:
        raise not_found()
    return row


def list_engagements(call: Call) -> Result:
    _landlord(call, call.path["id"])
    return Result(200, {"items": call.rows(
        "select id, landlord_client_id, provider_client_id, starts_on, ends_on from caseward.engagement"
        " where landlord_client_id = %s order by starts_on, id", [call.path["id"]])})


def create_engagement(call: Call) -> Result:
    b = call.body
    if _landlord(call, call.path["id"])["kind"] != "landlord":
        raise Problem(422, "not_a_landlord", "Only landlords engage providers")
    provider = call.row("select kind from caseward.client where id = %s", [b["provider_client_id"]])
    if provider is None:
        raise Problem(422, "invalid_reference", "No such provider")
    if provider["kind"] == "landlord":
        raise Problem(422, "not_a_provider", "Providers are contractors, managing agents or legal teams")
    overlapping = call.row(
        "select 1 from caseward.engagement where landlord_client_id = %s and provider_client_id = %s"
        " and daterange(starts_on, ends_on, '[]') && daterange(%s::date, %s::date, '[]')",
        [call.path["id"], b["provider_client_id"], b["starts_on"], b.get("ends_on")])
    if overlapping:
        raise Problem(409, "conflict", "This provider is already engaged for part of that period")
    row = call.row("insert into caseward.engagement (landlord_client_id, provider_client_id, starts_on, ends_on)"
                   " values (%s, %s, %s, %s)"
                   " returning id, landlord_client_id, provider_client_id, starts_on, ends_on",
                   [call.path["id"], b["provider_client_id"], b["starts_on"], b.get("ends_on")])
    return Result(201, row)


def end_engagement(call: Call) -> Result:
    engagement = call.row("select id, starts_on, ends_on from caseward.engagement where id = %s for update",
                          [call.path["id"]])
    if engagement is None:
        raise not_found()
    ends_on = datetime.fromisoformat(call.body["ends_on"]).date()
    if ends_on < engagement["starts_on"] or ends_on < datetime.now(UK).date():
        raise Problem(422, "rule_violation", "An engagement ends today or later, and not before it started")
    row = call.row("update caseward.engagement set ends_on = %s where id = %s"
                   " returning id, landlord_client_id, provider_client_id, starts_on, ends_on",
                   [ends_on, engagement["id"]])
    return Result(200, row)


def list_users(call: Call) -> Result:
    where, params = ["true"], []
    if "client_id" in call.query:
        where.append("exists (select 1 from caseward.membership m where m.user_id = u.id and m.client_id = %s)")
        params.append(call.query["client_id"])
    cursor = decode_cursor(call)
    if cursor:
        where.append("(lower(u.email), u.id) > (%s, %s::uuid)")
        params += cursor
    rows = call.rows(f"select {USER_COLUMNS}, lower(u.email) as _email from caseward.app_user u"
                     f" where {' and '.join(where)} order by lower(u.email), u.id limit %s",
                     params + [limit_plus_one(call)])
    return Result(200, page(call, rows, ["_email", "id"]))


def invite_user(call: Call) -> Result:
    b = call.body
    if call.row("select 1 from caseward.app_user where lower(email) = lower(%s)", [b["email"]]):
        raise Problem(409, "conflict", "A user with this email already exists")
    # The identity provider subject is bound on first sign-in (claim_invitation).
    row = call.row(f"insert into caseward.app_user as u (idp_subject, email, display_name, staff_role)"
                   f" values (%s, %s, %s, %s) returning {USER_COLUMNS}",
                   [f"invited:{uuid4()}", b["email"], b["display_name"], b.get("staff_role")])
    return Result(201, row)


def update_user(call: Call) -> Result:
    user = call.row(f"select {USER_COLUMNS} from caseward.app_user u where u.id = %s for update", [call.path["id"]])
    if user is None:
        raise not_found()
    if call.headers["If-Match"].strip().strip('"') != user["row_version"]:
        raise Problem(412, "stale", "The user changed since you loaded them; reload and try again")
    b = call.body
    if user["id"] == call.principal.user_id and (b.get("is_active") is False or
                                                 ("staff_role" in b and b["staff_role"] != "admin")):
        raise Problem(422, "self_lockout", "You cannot remove your own admin access")
    if b.get("staff_role") and call.row("select 1 from caseward.membership where user_id = %s", [user["id"]]):
        raise Problem(422, "staff_not_members", "Remove this user's client memberships before making them staff")
    changes = {k: b[k] for k in ("display_name", "staff_role", "is_active") if k in b}
    row = call.row(f"update caseward.app_user as u set {', '.join(f'{k} = %s' for k in changes)} where u.id = %s"
                   f" returning {USER_COLUMNS}", [*changes.values(), user["id"]])
    return Result(200, row)


def put_membership(call: Call) -> Result:
    client_id, user_id = call.path["id"], call.path["user_id"]
    if call.row("select 1 from caseward.client where id = %s", [client_id]) is None:
        raise not_found()
    user = call.row("select staff_role from caseward.app_user where id = %s", [user_id])
    if user is None:
        raise not_found()
    if user["staff_role"]:
        raise Problem(422, "staff_not_members", "Staff use the operations API and are not client members")
    call.conn.execute(
        "insert into caseward.membership (user_id, client_id, role, special_category_access) values (%s, %s, %s, %s)"
        " on conflict (user_id, client_id) do update set role = excluded.role,"
        " special_category_access = excluded.special_category_access",
        [user_id, client_id, call.body["role"], call.body.get("special_category_access", False)])
    return Result(200, call.row(MEMBERSHIP_SELECT + " where m.user_id = %s and m.client_id = %s", [user_id, client_id]))


def delete_membership(call: Call) -> Result:
    deleted = call.row("delete from caseward.membership where client_id = %s and user_id = %s returning user_id",
                       [call.path["id"], call.path["user_id"]])
    if deleted is None:
        raise not_found()
    return Result(204)
