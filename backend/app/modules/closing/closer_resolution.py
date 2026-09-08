"""WHO IS THE CLOSER for a store-day, and is a partial closing something the DM must look at.

PURE (no DB, no network, stdlib only) so it can be proven offline and shared by every caller —
`backend/harness_closer_resolution.py`.

OWNER DIRECTIVE 2026-09-07
--------------------------
    "the rep asad amar has been deleted from the system but it shows that he is still the closer.
     by default the closer will be the person who worked in the store for that day, not a predefined
     person, unless mandated by the tenant … also there could be 2 or more people working and they
     are expected to close their own registers when they leave unless specified by the tenant. if
     there are 2 people working in the store and the closing is done by one, and the cash tallies up
     with the register x-report and pos report then it is green — but if the total is off and the
     second person who worked has not done their closing then it should be flagged in the DM verify
     that 2 people worked but only one submitted the closing, and the DM should verify."

WHAT WAS ACTUALLY WRONG. `storeops.store_closer` is a STATIC per-store assignment. The DM-Verify
summary printed it verbatim on every store card — in EVERY closing_mode, and without ever asking
whether that person worked that day or still exists. Live on 2026-09-07 the house org had
`B-1115 -> E008 "Asad Umar"` while E008 was not among the 45 employees on the roster: `delete_employee`
cascades to `app_users` but never to `store_closer`, so the assignment outlived the person. The card
therefore named a deleted employee as the closer of a store, on a tenant whose `closing_mode` is
`per_rep` — where a predefined closer has no operational meaning in the first place.

THE RULE, in one place (RULE TWO — the tenant's `closing_mode` decides; nothing here is per-carrier):

  closing_mode = 'one_closing'  (the tenant MANDATES a single closer per store)
      · the assignee is on the roster AND worked  -> they are the closer            source='assigned'
      · otherwise                                 -> fall back to reality below, and say WHY the
                                                     assignment did not apply (off-roster / absent)

  closing_mode = 'per_rep'      (the DEFAULT — everyone closes their own register)
      · there is no predefined closer at all. The closer is whoever actually did it: the sole
        submitter, else the sole worker, else nobody — a store where two people worked has no single
        "closer" and must not be labelled with one.

The assignment is never silently discarded: `assigned_name`, `assigned_off_roster` and
`assigned_did_not_work` always come back, so a stale row is visible and fixable instead of merely
ignored. This module decides DISPLAY and DM-attention only — it does not change who is charged for a
missed closing (`ops_chargebacks._effective_closer`, which already falls back when the assignee has
no punch, so a deleted employee was never charged).
"""


def name_match(a, b) -> bool:
    """Loose match between two spellings of a person (first-token / contains) — the SAME rule
    `closing/router._name_match` uses, kept here so this module stays import-free and provable."""
    a, b = str(a or "").strip().lower(), str(b or "").strip().lower()
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return a.split()[0] == b.split()[0]


def _any_match(name, names) -> bool:
    return any(name_match(name, n) for n in (names or ()))


def resolve_closer(*, closing_mode, assigned_name="", assigned_id="",
                   worked=(), submitted=(), roster_names=(), roster_ids=()):
    """Who the closer IS for this store-day. Returns a dict — never a bare string, because "who" and
    "why" have to travel together for a stale assignment to be fixable:

        name                   the effective closer, or None when the store legitimately has none
        source                 'assigned' | 'submitted' | 'worked' | None
        assigned_name          the static store_closer assignee, verbatim (or '')
        assigned_off_roster    True when that assignee is not an employee any more
        assigned_did_not_work  True when they exist but did not work this store-day
        note                   one plain sentence, or None when nothing needs saying

    `roster_ids` is consulted first when the assignment carries an employee_id, because a rename is
    not a deletion — only an id that matches nothing (and a name that matches nothing) is off-roster.
    """
    a_name = str(assigned_name or "").strip()
    a_id = str(assigned_id or "").strip()
    worked = [w for w in (worked or ()) if str(w or "").strip()]
    submitted = [s for s in (submitted or ()) if str(s or "").strip()]

    off_roster = False
    did_not_work = False
    if a_name or a_id:
        ids = {str(i or "").strip() for i in (roster_ids or ()) if str(i or "").strip()}
        on_roster = (a_id in ids) if (a_id and ids) else False
        if not on_roster and a_name:
            on_roster = _any_match(a_name, roster_names)
        # With NO roster supplied at all we cannot claim anyone is deleted — say nothing rather than
        # accuse the config of being stale on a failed lookup.
        off_roster = bool((roster_names or roster_ids) and not on_roster)
        did_not_work = bool(a_name and not off_roster and not _any_match(a_name, worked))

    def _fallback():
        if len(submitted) == 1:
            return submitted[0], "submitted"
        if not submitted and len(worked) == 1:
            return worked[0], "worked"
        return None, None

    if str(closing_mode or "").strip() == "one_closing" and (a_name or a_id):
        if not off_roster and not did_not_work and a_name:
            return {"name": a_name, "source": "assigned", "assigned_name": a_name,
                    "assigned_off_roster": False, "assigned_did_not_work": False, "note": None}
        name, source = _fallback()
        why = ("is no longer an employee" if off_roster else "did not work this store on this day")
        note = (f"The assigned closer {a_name or a_id} {why}, so this store-day falls back to "
                + (f"{name}, who actually did the closing." if name
                   else "whoever worked — nobody here submitted one."))
        return {"name": name, "source": source, "assigned_name": a_name,
                "assigned_off_roster": off_roster, "assigned_did_not_work": did_not_work,
                "note": note}

    # per_rep (the default): no predefined closer. Reality decides, and two workers means no single
    # closer to name.
    name, source = _fallback()
    note = None
    if off_roster:
        note = (f"{a_name or a_id} is still assigned as this store's closer but is no longer an "
                "employee — clear the assignment under Cash Setup.")
    return {"name": name, "source": source, "assigned_name": a_name,
            "assigned_off_roster": off_roster, "assigned_did_not_work": did_not_work, "note": note}


# ── "two worked, one closed" ─────────────────────────────────────────────────────────────────────
# Money-OK is NOT the same as money-not-flagged. A store-day with no X-report has nothing to tie the
# declared cash to, so it is UNKNOWN — reporting that as green would be exactly the silent-zero class
# of defect this codebase keeps paying for.
def partial_closing(*, worked=(), submitted=(), closing_mode="per_rep",
                    money_ok=None, money_variances=None):
    """Did fewer people close than worked, and does the DM need to look?

        flag       True only when someone who worked did not close AND the money does not tie.
        money_ok   True (ties) / False (off) / None (nothing to tie it against)
        missing    the workers with no closing of their own

    Per the owner: two people working and one closing is FINE when the cash tallies with the
    X-report — it is only a problem when the total is off and the second person never closed, and
    then the DM must verify. In `one_closing` mode the tenant has said one person closes for the
    store, so a single closing is expected and never flagged on its own.
    """
    worked = [w for w in (worked or ()) if str(w or "").strip()]
    submitted = [s for s in (submitted or ()) if str(s or "").strip()]
    missing = sorted({w for w in worked if not _any_match(w, submitted)})
    one_closing = str(closing_mode or "").strip() == "one_closing"

    res = {"worked": sorted(worked), "worked_count": len(worked),
           "submitted": sorted(submitted), "submitted_count": len(submitted),
           "missing": missing, "money_ok": money_ok,
           "variances": list(money_variances or ()), "flag": False, "note": None}

    if one_closing or not missing or not submitted or len(worked) < 2:
        return res

    if money_ok is True:
        res["note"] = (f"{len(worked)} people worked and only {len(submitted)} closed, but the cash "
                       "ties to the POS X-report — nothing to chase.")
        return res

    who = ", ".join(missing)
    if money_ok is False:
        res["flag"] = True
        off = (" (" + "; ".join(res["variances"]) + ")") if res["variances"] else ""
        res["note"] = (f"{len(worked)} people worked but only {len(submitted)} submitted a closing, "
                       f"and the money does not tie{off}. {who} never closed — verify before signing off.")
        return res

    res["flag"] = True
    res["note"] = (f"{len(worked)} people worked but only {len(submitted)} submitted a closing, and "
                   f"there is no POS X-report for this day to tie the cash against. {who} never "
                   "closed — verify before signing off.")
    return res
