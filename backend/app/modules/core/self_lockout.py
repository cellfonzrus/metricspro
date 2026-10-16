"""SELF-LOCKOUT — THE one answer to "would this write take away the authority that just made it?"
(index §CORE-12). PURE: stdlib only, no I/O, no fastapi, so the harness runs it DB-free.

OWNER 2026-10-16: *"i made my self market manager under vzone and cannot get admin privilidges
anymore under ss, fix urgently"*.

THE DEFECT, AS A CLASS. An administrative write may name the CALLER'S OWN membership. Every
role/permission write in `core/router.py` gates on `_can_edit_setting(caller, 'security')` and then
writes whatever it was asked to write — including a row or a role that is the very thing that answer
was computed from. One such write is a one-way door: the instance was the `ss@1313global.us` login's
Vzone membership moved admin -> market_manager, and Market Manager carries no `admin` module, so from
inside Vzone no screen was left that could move it back, and that login holds no `super_admin` flag
to reach in from another tenant.

The house already refused three INSTANCES of this class, each inline and each unaware of the others:
turning login enforcement on with no active admin (`PUT /core/auth-config`), removing the last
platform super-admin (`POST /core/revoke-super-admin`), and deleting the `admin` role
(`DELETE /core/roles/{id}`). All three answer "would this leave somebody with no way back in?" —
about the PLATFORM or the TENANT. None of them asked it about the CALLER, which is the instance that
actually happened. This module is that question, asked once, for every writer.

THE RULE. A write is refused when, for the tenant it affects, the caller can edit the `security`
area BEFORE it and cannot AFTER it. Nothing else is refused:
  - a caller who could not do it anyway is already refused by the gate, not here;
  - a caller who keeps the authority (a super-admin, or an administrator editing a tenant whose
    authority the write does not touch) is unaffected — before == after == True;
  - demoting SOMEBODY ELSE is untouched, whatever it does to them;
  - a caller may still demote themselves in a tenant where they would KEEP the authority some other
    way (the `super_admin` flag, an explicit `settings.security` grant on the new role).
So this can only ever refuse a write whose own author would be unable to undo it.

THE AUTHORITY QUESTION ITSELF LIVES HERE TOO. `can_edit_setting` was `core/router.py`'s private
`_can_edit_setting`; it moved here unchanged and the router dereferences it. A copy of it in this
module would be exactly the divergence the owner's design-fix directive forbids: the gate and the
lockout check MUST be the same answer or the refusal protects something other than the door.
"""
from __future__ import annotations

# The settings area every role/user/permission write in core/router.py gates on. One name, so a
# caller and this check cannot be asking about different doors.
WRITE_AREA = "security"


# ── THE AUTHORITY ANSWER (moved verbatim from core/router.py::_can_edit_setting) ────────────────
def can_edit_setting(caller, area):
    """True if this caller may EDIT the given settings area. Precedence:
      1. super_admin              -> always yes.
      2. explicit role grant/deny -> permissions.settings[area] (true/false) wins, even for an admin
         (so an owner can DISABLE a specific setting for a manager, or GRANT one setting to a manager).
      3. default                  -> a full-scope admin (scope='all' or the 'admin' role) edits every
         setting; anyone else cannot."""
    if not caller:
        return False
    if caller.get("super_admin"):
        return True
    perms = caller.get("perms") or {}
    s = perms.get("settings") or {}
    if area in s:
        return bool(s[area])
    return (perms.get("scope") == "all") or ((caller.get("role") or "").lower() == "admin")


# ── THE CALLER, REBUILT FROM ROWS (mirrors core/router.py::_resolve_caller) ─────────────────────
def acting_caller(rows, org_id, role_perms):
    """{org_id, role, super_admin, perms} for this login ACTING AS `org_id`, from its membership
    rows (`storeops.app_users` rows sharing one `auth_id`) and `role_perms` — {role name ->
    permissions dict} for that tenant.

    Deliberately the same shape and the same two rules `_resolve_caller` uses: the role and its
    permissions come from the row of the ACTING tenant, while `super_admin` is a LOGIN-level key,
    true when the flag is set on ANY membership. A row flagged `is_active = False` supplies no role
    (that login cannot sign in to that tenant), which is why deactivating your own account is the
    same lockout as demoting it and is answered by the same function."""
    row = None
    for r in (rows or []):
        if r.get("org_id") == org_id:
            row = r
            break
    if row is not None and row.get("is_active", True) is False:
        row = None
    role = (row or {}).get("role")
    return {"org_id": org_id, "role": role,
            "super_admin": any(bool(r.get("super_admin")) for r in (rows or [])),
            "perms": (role_perms or {}).get(role or "") or {}}


# ── THE REFUSAL ────────────────────────────────────────────────────────────────────────────────
REFUSAL = ("That change would remove your own administrator access to this company, and no screen "
           "would be left that could put it back — somebody else would have to undo it in the "
           "database. Ask another administrator to make the change for you, or give yourself the "
           "access somewhere else first.")


def lockout_reason(*, rows_before, rows_after, org_id, perms_before, perms_after,
                   area=WRITE_AREA, detail=""):
    """None when the write is fine; the refusal message when it would lock the caller out.

    `rows_before` / `rows_after` are the caller's own membership rows as they are and as the write
    would leave them; `perms_before` / `perms_after` are {role name -> permissions} for `org_id`
    likewise (a role-permissions edit moves the second pair, a role assignment the first). Passing
    the same object twice is correct for a write that does not touch that half."""
    before = can_edit_setting(acting_caller(rows_before, org_id, perms_before), area)
    after = can_edit_setting(acting_caller(rows_after, org_id, perms_after), area)
    if before and not after:
        return (REFUSAL + (" (%s)" % detail if detail else ""))
    return None


def rows_without(rows, *, row_id):
    """`rows` with the membership `row_id` REMOVED — the "after" half of a login delete. A deleted
    membership supplies no role and no `super_admin` flag, so a login that deleted its own last
    administrator row would resolve to nothing at all."""
    return [dict(r) for r in (rows or []) if r.get("id") != row_id]


def rows_with_change(rows, *, row_id, changes):
    """`rows` with the membership `row_id` carrying `changes` — the "after" half of a row write.
    Pure; the input rows are never mutated. A `row_id` naming none of them returns `rows` as-is,
    which is the right answer: the write is not about this login."""
    out = []
    for r in (rows or []):
        out.append({**r, **changes} if r.get("id") == row_id else dict(r))
    return out
