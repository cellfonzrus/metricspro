"""WHO a payroll change-log row is about — ONE home, read from the STORED employee record (index §19.42).

THE DEFECT (owner report 2026-10-03, Vzone): two `storeops.payroll_change_log` rows written by one
save at 2026-10-02T21:23:19 (entry_point 'pay_basis_change', source_id '237', employee_name 'Shweta')
carry employee_id NULL, while storeops.employees id 237 holds 'E237'; the same save wrote 'E240'
correctly for id 240. `update_employee` logged `after.get("employee_id")` — the UPDATE's echo — and
only THEN ran `_ensure_employee_id`, which minted 'E237' for a person who had none yet (added through
Roles & Access, whose insert never minted one). The log row was built from whatever the caller held at
that instant, not from the record.

THE CLASS: a change-log row's identity came from whatever each of its ~20 callers happened to have —
an UPDATE echo, a shift's snapshot name, a punch's raw `employee_id` (the Schedule page stores the
NUMERIC pk there, payroll_identity.py), a payroll-approval hours row — never from the stored person.

THE DESIGN: `storeops/router.py::_log_payroll_change` is the only inserter into the table (locked), and
it builds the row's (employee_id, employee_name, store_code) through `resolve_log_identity` below,
which reads the stored `storeops.employees` row — handed over by the caller when it already holds it,
otherwise found org-scoped by business id, then numeric pk — and, when that person has no business id
yet, asks the ONE mint (`_ensure_employee_id`) for it before the row is built. Callers' values are
HINTS used only when no stored person can be found (a deleted employee, a tenant-level config row).

Why not `app.core.identity.resolve_employee` (the declared SSOT): it is Phase-1 dormant (wired into
nothing), keyed on `entity_id` (mig 917 — a row without one is skipped) and served from a 30-second
cache — that cache would still hold 237 WITHOUT the id minted a moment earlier, reproducing this exact
defect. A log row needs the record as it is NOW, so this reads it directly, by the same two keys
(business id first, then numeric id only when no one owns that string as a business id — the
`business_id_alias_map` collision rule).

LEAF MODULE: no fastapi, no DB import — the caller passes its storeops-schema client and its mint, so
`backend/harness_payroll_log_identity.py` proves it with the stdlib alone. Never raises: a log row is
best-effort and must never 500 the payroll write it records.
"""


def _s(v):
    """Trimmed string, or None when blank."""
    t = str(v).strip() if v is not None else ""
    return t or None


def identity_from_stored(stored, *, hint_employee_id=None, hint_name=None, hint_store=None):
    """PURE. The log row's identity: the STORED person's business id and name win; a caller's values
    are used only when there is no stored person. The store is the EVENT's store when the caller has
    one (a shift or punch happened at a store), else the stored person's home store."""
    if stored:
        return {
            "employee_id": _s(stored.get("employee_id")) or _s(hint_employee_id),
            "employee_name": _s(stored.get("name")) or _s(hint_name),
            "store_code": _s(hint_store) or _s(stored.get("home_store")),
        }
    return {"employee_id": _s(hint_employee_id), "employee_name": _s(hint_name),
            "store_code": _s(hint_store)}


_EMP_COLS = "id,org_id,employee_id,name,home_store"


def _one(client, org_id, col, val):
    try:
        rows = (client.table("employees").select(_EMP_COLS)
                .eq("org_id", org_id).eq(col, val).limit(1).execute().data) or []
        return rows[0] if rows else None
    except Exception:
        return None


def find_stored_employee(client, org_id, *, employee_id=None, source_table=None, source_id=None):
    """The stored storeops.employees row this log entry is about, or None. ORG-SCOPED. Ladder:
      1. `employee_id` as a BUSINESS id (what payroll keys on);
      2. the employees pk when the change IS an employees row (`source_table='employees'`);
      3. `employee_id` as a numeric pk — reached only when no one in the org owns that string as a
         business id (rung 1 answered first), the collision rule of payroll_identity."""
    if not org_id or client is None:
        return None
    key = _s(employee_id)
    if key:
        hit = _one(client, org_id, "employee_id", key)
        if hit:
            return hit
    if source_table == "employees" and _s(source_id):
        hit = _one(client, org_id, "id", _s(source_id))
        if hit:
            return hit
    if key and key.isdigit():
        return _one(client, org_id, "id", key)
    return None


def resolve_log_identity(client, org_id, *, employee_row=None, employee_id=None, employee_name=None,
                         store_code=None, source_table=None, source_id=None, mint=None):
    """THE helper `_log_payroll_change` builds every row's identity through. Returns
    {employee_id, employee_name, store_code}. `employee_row` = the stored employees row when the caller
    already holds it (an UPDATE echo, a select it just made); otherwise it is looked up. `mint` = the
    one business-id mint (`storeops.router._ensure_employee_id`): a stored person with no business id
    gets one BEFORE the log row is built, so the log can never be the first place they appear
    without it. Never raises."""
    try:
        stored = employee_row if isinstance(employee_row, dict) and employee_row else None
        if stored is None:
            stored = find_stored_employee(client, org_id, employee_id=employee_id,
                                          source_table=source_table, source_id=source_id)
        if stored is not None and not _s(stored.get("employee_id")) and mint is not None:
            try:
                mint(stored)
            except Exception:
                pass
        return identity_from_stored(stored, hint_employee_id=employee_id, hint_name=employee_name,
                                    hint_store=store_code)
    except Exception:
        return identity_from_stored(None, hint_employee_id=employee_id, hint_name=employee_name,
                                    hint_store=store_code)
