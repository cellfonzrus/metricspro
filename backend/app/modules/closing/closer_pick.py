"""WHO a daily closing may be submitted under (owner directive 2026-09-26, index §29.7).

Owner, verbatim: *"while submitting the daily closing the employee name should default to the person who
is signed in, only the DM and above should get the option to select any body other than themselves"*.

ONE rule, read by the server (`closing/router.create_row` — the authority) and mirrored for the screen by
`frontend/src/lib/rbac.ts::canPickAnyCloser` (presentation only; `harness_closing_closer_pick.py` locks the
two together):

  may_pick_any(perms)
    · platform super admin                       → yes
    · an explicit per-role grant `data[GRANT_KEY]` → that answer, either way (the Roles page — config)
    · otherwise the role's REPORTING scope tier: market / region and company-wide ('all') → yes
      ("DM and above"); store / self → no, the closing is submitted under the signed-in person's own name.

PURE: no I/O. The router resolves the caller's permissions and own names, then asks `verdict`.
"""

GRANT_KEY = "closing_pick_any_employee"

# "DM and above" as scope tiers. DEREFERENCED, not copied: the tuple moved to `app.core.scope` on
# 2026-10-09 when a fourth copy was about to be written for the month-focus declaration (index §62),
# and this name stays as the re-export so every existing reader of it is byte-identical.
from app.core.scope import MARKET_OR_WIDER_SCOPES as PICK_ANY_SCOPES, is_market_or_wider


def _fold(v) -> str:
    return " ".join(str(v or "").split()).casefold()


def may_pick_any(perms) -> bool:
    """May this caller submit a closing under someone else's name? `perms` is the role permissions dict
    as `_caller_perms` returns it (`__super_admin` marks a platform super admin)."""
    p = perms or {}
    if p.get("__super_admin"):
        return True
    explicit = (p.get("data") or {}).get(GRANT_KEY)
    if isinstance(explicit, bool):
        return explicit
    # `default="all"` keeps this byte-identical: a role with no scope recorded has always been read
    # here as company-wide.
    return is_market_or_wider(p, default="all")


def own_names(*names) -> set:
    """The caller's own names (login full name, employee-record name), folded for comparison."""
    return {n for n in (_fold(x) for x in names) if n}


def verdict(perms, submitted_name, names) -> tuple:
    """(ok, message). A caller who may pick anyone always passes; everyone else must submit under one of
    their own names. A caller with no name on file is told so — never silently let through."""
    if may_pick_any(perms):
        return True, ""
    if not names:
        return False, ("Your login has no name on file, so this closing cannot be submitted under your name. "
                       "Ask an admin to set your name (Admin → Roles & Access).")
    if _fold(submitted_name) in names:
        return True, ""
    return False, ("You can only submit a closing under your own name. A district manager or above can "
                   "submit one for somebody else.")
