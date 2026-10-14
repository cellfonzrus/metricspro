"""WHICH PERSON IS THIS LOGIN — one home for the `app_users.employee_id` link (index §CORE-07).

OWNER 2026-10-14: *"all stores should be able to see their dashboard with commissions earned and
their schedules and submit their closing at night with their app, then send me the process to add app
access to all of them"*. Answering the last clause meant reading the three paths that create a login,
and they did not agree.

THE CLASS, not the instance. A login is tied to its person by ONE column, `app_users.employee_id`,
and with that column NULL the login is **nobody**:

  · `core/scope.py::visible_people_keyset` — a people_visibility-'self' role resolves to
    `set(self_employee_ids(app_user))`, which is EMPTY, so the rep opens Schedule and sees a blank
    week. Its own docstring says it: *"a login with no employee_id on it is nobody and is shown
    nothing."*
  · `closing/router.py::_closer_gate` — the submit is refused `closer_no_name`, because the caller's
    own names are read from the employee record this column points at.

Three paths write an `app_users` row for a person, and each decided the link for itself:

  POST /users/assign      from the Roles GRID — sends `employee_id`            → linked
  POST /users/assign      from the add-a-person FORM — sends no employee_id    → **NULL**
  POST /users/bulk-assign the spreadsheet upload — sends no employee_id        → **NULL**

Measured live 2026-10-14: the one Vzone sales_rep login carries no `employee_id` and is exactly this
instance. The two tenants whose reps were provisioned from the grid (Cellfonz 34, Luxelink 39) are
all linked — an accident of which button was pressed, not a rule.

THE TWO WRITE PATHS THAT ARE NOT PEOPLE are excused on purpose and the lock pins the excuse: tenant
creation and super-admin elevation both mint an 'all'-scope administrator, who is not a person on any
store's roster and whose people_visibility short-circuits before the column is read. (The owner's own
super-admin login carries no employee_id for that reason, harmlessly.)

PURE: no I/O, no imports. Resolving the roster's answer is the caller's job — `_ensure_employee` does
it, because it is already the one helper both write paths call to put the person on the roster.
"""

# Where a candidate id came from, in the order it wins. Declared so the lock can assert that no
# caller spells the order itself.
LINK_SOURCES = ("request", "stored", "roster")


def link_employee_id(*, request_id=None, stored_id=None, roster_id=None):
    """The `employee_id` to write on an `app_users` row, or None when nobody can be named.

    Precedence, and each step is deliberate:

      1. `request_id`  — what the caller explicitly asked for (the Roles grid, or a spreadsheet
         column). An administrator naming the person outranks anything inferred.
      2. `stored_id`   — the link the row already carries. An upsert must never DROP a link it had:
         a re-assign that only changes a role would otherwise blank the person's schedule.
      3. `roster_id`   — the employee this person's email resolves to on this org's roster.

    None (never "") when all three are empty, so the column stays NULL rather than being written with
    a falsy string that `self_employee_ids` would read as a real id. Pure; never raises.
    """
    for value in (request_id, stored_id, roster_id):
        try:
            text = str(value or "").strip()
        except Exception:                       # pragma: no cover - a non-stringable input
            continue
        if text:
            return text
    return None
