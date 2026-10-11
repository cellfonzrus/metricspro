"""THE ONE HOME for "does the carrier's clock report agree with ours, and who must fix it by when?"

OWNER ASK 2026-10-10 (verbatim): *"also need to import the clock in clock out report from b2b and
match with what we have in metrics pro and asses the difference and ask the DM on every monday and
require the correction to be done before the payroll is run, the dm should be notified as an alert
as soon as they log in to fix it and block them from logging in unless they fix it by tuesday so the
payroll can be run on Wednesday. this will be picked from the emails for each store like other
reports."*

THE FEED IS A CLAIM, NOT A CORRECTION
-------------------------------------
This module never writes `storeops.shifts.actual_hours` and never writes `storeops.timelog`. The
B2B clock report lands in `storeops.raw_clock_punch` (mig 1073) as what the CARRIER'S clock says —
the same posture as the distributor figure in the carrier work (owner ruling: *"the distributor
figure is a CLAIM about the same payment"*). The VARIANCE between the claim and what MetricsPro
holds is the thing a human corrects, and the correction is made through the mechanisms that already
own those numbers (`POST /storeops/manual-hours`, `PATCH /storeops/shifts`), never by this module
overwriting our own measurement with somebody else's.

Why that matters here specifically: our own hours come from `payroll_hours_basis` (§14y), which
already distinguishes a measurement from a schedule. If the feed were allowed to write
`actual_hours`, every variance would disappear by being asserted away and the basis would go back to
being a number nobody could question. One fact, one home — and the claim is a different fact from
the measurement.

WHAT A VARIANCE CAN BE (the vocabulary; an unruled kind is NOT auto-cleared — see `KINDS`)
  `missing_punch`      the feed has hours for this (store, employee, day) and MetricsPro has no
                       usable measurement. The common case, and the one that unpays a day under a
                       clocked-only basis.
  `extra_punch`        MetricsPro has a measurement and the feed has nothing for that day.
  `hours_differ`       both measured the day and the gap exceeds the org's declared tolerance.
  `employee_unmatched` the feed names somebody `payroll_identity` cannot bind to the roster.
  `store_unmatched`    the feed names a store string that binds to no store.

Only `hours_differ` has a tolerance; the others are present/absent facts. Tolerance is CONFIG
(`variance_tolerance_hours`), never a literal.

WHO IS ASKED
------------
The DM for the store, resolved through `storeops.org_chain.dm_by_store` over
`storeops.router.org_chain_inputs` — the ONE org walk (§48.7), not a second one. **Measured
2026-10-10, that walk resolves a DM for 6 of 29 Cellfonz stores and 0 of 20 Luxelink stores**
(Cellfonz's LI District carries all three DMs on the same six stores; NJ/NYC districts have no
managers; ten PA stores carry no `org_unit_id`; Luxelink's tree has only Company and Market levels
and no managers at all). A DM-keyed ask would therefore miss 43 of 51 stores, so `recipient_ladder`
falls back to the nearest manager ABOVE the district (market manager, regional, director) and, when
even that is empty, reports the store as UNOWNED rather than dropping it. `unowned_stores` is
rendered on the board and counted in the PR: an unasked store is a reported gap, not silence.

THE WEEK IS CONFIG
------------------
The owner said Monday / Tuesday / Wednesday, so Monday / Tuesday / Wednesday are the HOUSE DEFAULT
ROWS — not three weekday literals in code. `ask_weekday`, `gate_weekday` and `run_weekday` are
`storeops.payroll_hours_basis_config` columns (0 = Monday, ISO-style, matching
`shift_templates.weekday` which this codebase already keys that way). A tenant that runs payroll on
Thursday changes a row.

THE GATE IS A GATE, NOT A LOCKOUT
---------------------------------
`gate_decision` NEVER refuses a sign-in. From the gate weekday until the variances are corrected,
the manager signs in, lands on the correction board and reaches nothing else (`allowed_paths`). That
is the house's ★ NO LOCKOUT ★ property (index §22): a person who cannot sign in cannot fix the thing
that is blocking them. Every escape hatch is a config row, never a name in code.

Everything here is PURE: no DB, no network, no clock (the caller passes the business-local date it
resolved through `storeops.router._biz_tz_for`, never a zone spelled here). Proof + lock:
`backend/harness_clock_variance.py`, `backend/harness_payroll_hours_basis_lock.py`.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §14z.
"""
from __future__ import annotations

from app.modules.storeops import payroll_hours_basis as _basis
from app.modules.account.store_identity import fold_accents as _sid_fold  # noqa: E402

# ── THE VARIANCE VOCABULARY ──────────────────────────────────────────────────────────────────────
MISSING_PUNCH = "missing_punch"
EXTRA_PUNCH = "extra_punch"
HOURS_DIFFER = "hours_differ"
EMPLOYEE_UNMATCHED = "employee_unmatched"
STORE_UNMATCHED = "store_unmatched"
#: The clock report names a login the roster does not bind to anybody. MEASURED on the real export
#: 2026-10-09: only 14 of 57 house employees carry `epay_login` and the feed's own `User Login` is
#: not stored on the employee record at all, so 17 of 30 logins can only be PROPOSED by name and 2
#: bind to nothing. A name is never an attribution (`payroll_identity.match_is_binding`), so "who is
#: this login" is a variance a human resolves — not a silent guess and not a dropped row.
LOGIN_UNLINKED = "login_unlinked"
#: NO FILE ARRIVED for this store-day. The export is a DAILY 08:00 email for the previous day, so a
#: day with no file is a real event and must be visible: an absent feed is not an implicit zero, and
#: it is not "everything agreed" either.
NO_CLOCK_FEED = "no_clock_feed"
#: BOTH records have the person working that day, at DIFFERENT stores. Its own class because the
#: hours may be right while the attribution is wrong, and a store's labour cost is wrong either way.
#: MEASURED 2026-10-09: one employee is held at B-1750 by the kiosk (12.13h, auto-closed at exactly
#: 02:00:00Z) and at B-2509 by the report (9.17h) — wrong store AND 2.96h over, from one failure.
STORE_MISMATCH = "store_mismatch"
#: OUR OWN PUNCH WAS CLOSED BY THE KIOSK SWEEP, not by the person. Under the declared rule its
#: clock-out loses automatically — but trimming it quietly would hide a time-clock failure, so it is
#: its own flagged row for the DM. `clockin_evidence.py` documents the same failure at 122.19h.
AUTO_CLOCKOUT = "auto_clockout"

KINDS = (MISSING_PUNCH, EXTRA_PUNCH, HOURS_DIFFER, EMPLOYEE_UNMATCHED, STORE_UNMATCHED,
         LOGIN_UNLINKED, NO_CLOCK_FEED, STORE_MISMATCH, AUTO_CLOCKOUT)

#: THE ALLOWLIST (the `payroll_hours_basis.PAYING_BASES` / `carrier_dollar_class` pattern, index
#: §66). A variance kind that BLOCKS the payroll run until it is resolved. An unruled kind is IN
#: this list on purpose — the safe default for a blocker is to block, because the alternative is a
#: new variance kind added later that silently stops gating and lets an uncorrected period pay.
BLOCKING_KINDS = KINDS
#: Resolutions a human can record. `corrected` = the underlying hours were fixed; `accepted_ours` =
#: the DM says our number is right and the feed is wrong; `accepted_feed` = the DM says the feed is
#: right and has corrected ours to match. All three CLEAR the row; none of them edits hours here.
RESOLUTIONS = ("corrected", "accepted_ours", "accepted_feed")

KIND_REASONS = {
    MISSING_PUNCH: "The clock report has hours for this day and MetricsPro has no usable "
                   "measurement for it. Under a clocked-only payroll basis this day pays nothing "
                   "until somebody records the hours.",
    EXTRA_PUNCH: "MetricsPro has a measurement for this day and the clock report has none. Either "
                 "our punch belongs to a different day or store, or the report is incomplete.",
    HOURS_DIFFER: "Both sources measured this day and they disagree by more than this org's "
                  "declared tolerance.",
    EMPLOYEE_UNMATCHED: "The clock report names a person who does not bind to this org's employee "
                        "roster, so their hours cannot be attributed to anybody.",
    STORE_UNMATCHED: "The clock report names a store string that binds to no store in this org, so "
                     "its rows cannot be attributed to a store.",
    LOGIN_UNLINKED: "The clock report's login is not linked to an employee on this org's roster. A "
                    "name match is a PROPOSAL for somebody to approve, never an attribution, so "
                    "these hours belong to nobody until a human says who this is.",
    NO_CLOCK_FEED: "No clock report arrived for this store-day. The export is emailed every "
                   "morning for the previous day, so a missing day is an event — not a day on "
                   "which nobody worked, and not a day on which everything agreed. Since the "
                   "report is a PAY SOURCE, this store-day cannot be priced until it arrives.",
    STORE_MISMATCH: "Both clock records have this person working this day, at DIFFERENT stores. The "
                    "hours may be right while the store is wrong, and either way one store's "
                    "labour cost is.",
    AUTO_CLOCKOUT: "This platform's own punch was closed automatically by the kiosk sweep, so its "
                   "clock-out records when the sweep ran and not when anybody left. Under this "
                   "org's rule it loses the comparison automatically — it is flagged here anyway, "
                   "because a time clock that had to be closed for somebody is a failure worth "
                   "seeing rather than a number worth quietly trimming.",
}

# ── CONFIG (RULE TWO — house defaults; the same per-org row payroll_hours_basis reads) ───────────
#: 0 = Monday. The same weekday convention `storeops.shift_templates.weekday` already uses.
MONDAY, TUESDAY, WEDNESDAY = 0, 1, 2

DEFAULT_CONFIG = {
    # The whole feature, off. Sanjot turns each half on when he is ready.
    "clock_feed_enabled": False,
    "variance_ask_enabled": False,
    "login_gate_enabled": False,
    "payroll_run_gate_enabled": False,
    # The owner's Mon / Tue / Wed, as ROWS.
    "ask_weekday": MONDAY,
    "gate_weekday": TUESDAY,
    "run_weekday": WEDNESDAY,
    # How far apart two measurements of one day may be before it is a variance. Config, not a literal.
    "variance_tolerance_hours": 0.25,
    # Roles that reach the correction board and are therefore gateable. Spellings are folded by the
    # caller's own role normaliser (`pay_visibility._norm_role`) — no role is spelled in code here
    # beyond these house DEFAULT ROWS, which a tenant replaces.
    "gated_roles": ("district_manager", "dm", "market_manager", "market"),
    # The escape hatch, as a config row rather than a name in code: logins exempted from the gate.
    "gate_exempt_logins": (),
}
_CONFIG_FIELDS = tuple(DEFAULT_CONFIG.keys())
_BOOL_FIELDS = ("clock_feed_enabled", "variance_ask_enabled", "login_gate_enabled",
                "payroll_run_gate_enabled")
_WEEKDAY_FIELDS = ("ask_weekday", "gate_weekday", "run_weekday")


def resolve_config(org_row=None):
    """Merge a (possibly partial / absent) per-org row over the house defaults. An org with no row —
    or a database where mig 1073 has not run — gets the whole feature OFF, which is today."""
    eff = dict(DEFAULT_CONFIG)
    for f in _CONFIG_FIELDS:
        v = (org_row or {}).get(f)
        if v is not None:
            eff[f] = v
    for f in _BOOL_FIELDS:
        eff[f] = bool(eff[f])
    for f in _WEEKDAY_FIELDS:
        try:
            eff[f] = int(eff[f]) % 7
        except (TypeError, ValueError):
            eff[f] = DEFAULT_CONFIG[f]
    try:
        eff["variance_tolerance_hours"] = max(0.0, float(eff["variance_tolerance_hours"]))
    except (TypeError, ValueError):
        eff["variance_tolerance_hours"] = DEFAULT_CONFIG["variance_tolerance_hours"]
    eff["gated_roles"] = tuple(str(r).strip().lower() for r in (eff["gated_roles"] or ()) if str(r).strip())
    eff["gate_exempt_logins"] = tuple(str(x).strip().lower() for x in (eff["gate_exempt_logins"] or ()) if str(x).strip())
    return eff


def kind_blocks(kind):
    """PURE: does this variance kind block the payroll run until it is resolved? Answered from the
    kind ALONE — the question, not the policy (the caller decides whether to refuse or warn).

    An unknown kind BLOCKS. See `BLOCKING_KINDS` for why that is the safe direction here while it is
    the unsafe direction for `payroll_hours_basis.basis_pays_hours`: there, defaulting to payable
    would pay an unruled basis; here, defaulting to non-blocking would pay an unchecked period.
    """
    return kind in BLOCKING_KINDS or kind not in KINDS


def kind_reason(kind):
    """PURE: the sentence shown on the correction row."""
    return KIND_REASONS.get(kind, f"This variance kind ({kind!r}) has no ruling in clock_variance; "
                                  f"it blocks the payroll run until a human looks at it.")


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _s(v):
    return str(v or "").strip()


def _d(v):
    return str(v or "")[:10]


# ── THE COMPARISON ───────────────────────────────────────────────────────────────────────────────
def feed_day_hours(feed_rows, matcher=None):
    """PURE: the CLAIM, folded to `{(employee_id, store_code, business_date): hours}` plus the rows
    that bind to nobody.

    `feed_rows` are `clock_feed.normalize`'s `punches` — already store-resolved through
    `account/store_identity.build_store_resolver` (the ONE home; 28/28 on the real export),
    already type-allowlisted, already stripped of back-office logins, already converted out of
    local wall clock. This function does not parse, resolve or convert anything: that all belongs
    to `clock_feed`, and doing any of it again here would be the duplicate defect.

    `matcher` is `payroll_identity.build_employee_matcher`'s closure — INJECTED, the ONE home for
    "which employee is this spelling". A row whose basis does not BIND
    (`payroll_identity.match_is_binding`) is reported as `login_unlinked`, with the proposal when
    there is one, and its hours are NOT attributed to the proposed person.

    THE KEY INCLUDES THE STORE. One person legitimately has two punches at two stores on the same
    day in the real export, so folding on (employee, date) would merge them and attribute both to
    one store. Hours belong to the store the punch happened at.
    """
    hours = {}
    unmatched = []
    for r in feed_rows or ():
        r = r or {}
        store = _s(r.get("store_code"))
        day = _d(r.get("business_date"))
        login = _s(r.get("feed_login"))
        name = _s(r.get("employee_raw"))
        h = _f(r.get("hours"))
        if not store or not day:
            unmatched.append({"kind": STORE_UNMATCHED, "store_raw": _s(r.get("store_raw")),
                              "store_code": store, "employee_raw": name, "feed_login": login,
                              "date": day, "feed_hours": round(h, 2),
                              "reason": kind_reason(STORE_UNMATCHED)})
            continue
        eid, basis, detail = (matcher(login, name) if matcher is not None
                              else (name, "employee_id", {"binds": True}))
        if not eid or not detail.get("binds"):
            unmatched.append({"kind": LOGIN_UNLINKED, "store_code": store, "date": day,
                              "employee_raw": name, "feed_login": login,
                              "feed_hours": round(h, 2), "match_basis": basis,
                              "proposed_employee_id": detail.get("proposed_employee_id"),
                              "proposed_name": detail.get("proposed_name"),
                              "candidates": detail.get("candidates") or [],
                              "reason": detail.get("reason") or kind_reason(LOGIN_UNLINKED)})
            continue
        key = (eid, store, day)
        hours[key] = round(hours.get(key, 0.0) + h, 2)
    return hours, unmatched


def feed_day_coverage(feed_dates_by_store, expected, cfg):
    """PURE: the store-days the clock report never arrived for (`NO_CLOCK_FEED`).

    `feed_dates_by_store` `{store_code: {business dates the feed carried}}`.
    `expected`            `{store_code: {business dates we EXPECTED a file for}}` — the caller's
                          own answer, built from the store-days it actually has a schedule or a
                          punch for. Injected, because "which days should this store have sent a
                          file" is a scheduling question and this module does not own it.

    An absent file is reported as its own variance kind precisely so it cannot read as agreement:
    with no rows from a store, every one of the other tests is trivially silent.
    """
    cfg = resolve_config(cfg if isinstance(cfg, dict) else None)
    rows = []
    for store in sorted(expected or {}):
        got = {_d(d) for d in (feed_dates_by_store or {}).get(store) or ()}
        for day in sorted({_d(d) for d in (expected or {}).get(store) or ()}):
            if day and day not in got:
                rows.append({"employee_id": None, "store_code": store, "date": day,
                             "kind": NO_CLOCK_FEED, "reason": kind_reason(NO_CLOCK_FEED),
                             "feed_hours": None, "our_hours": None, "our_basis": None,
                             "our_basis_reason": None, "delta_hours": None,
                             "tolerance_hours": float(cfg["variance_tolerance_hours"]),
                             "resolved": False, "blocks": bool(kind_blocks(NO_CLOCK_FEED))})
    return rows


def compare(feed_hours, evidence, basis_cfg, cfg, employee_rows=None, store_of=None,
            resolved=None, extra_rows=None):
    """PURE: the variance list for one payroll period.

    `feed_hours`   `feed_day_hours`'s first return.
    `evidence`     `payroll_hours_basis.build_evidence` over OUR OWN rows for the same period.
    `basis_cfg`    `payroll_hours_basis.resolve_config` — so "what MetricsPro holds" is read through
                   the ONE home and its basis travels on every row. This module does not decide
                   what our hours are; it asks.
    `store_of`     `{(employee_id, date): store_code}` for our own rows (a punch's store, else the
                   shift's). Injected; a day we cannot place keeps the feed's store.
    `resolved`     `{(employee_id, store_code, date)}` already cleared by a human — those rows are
                   reported as resolved and never block.
    `extra_rows`   variance rows this function cannot compute because they are not a comparison of
                   two numbers: `feed_day_hours`'s `login_unlinked` / `store_unmatched` rows and
                   `feed_day_coverage`'s `no_clock_feed` rows. They are folded in here so there is
                   ONE board and ONE blocking count — a second list of "the other problems" is
                   exactly how a gate gets passed with a store unaccounted for.

    Returns `{rows, by_store, blocking, totals}`. `rows` carries every variance with BOTH numbers,
    our basis, the kind, its reason and whether it blocks.
    """
    cfg = resolve_config(cfg if isinstance(cfg, dict) else None)
    basis_cfg = basis_cfg if isinstance(basis_cfg, dict) else _basis.resolve_config(None)
    tol = float(cfg["variance_tolerance_hours"])
    emp_by_id = {(e or {}).get("employee_id"): e for e in (employee_rows or ()) if (e or {}).get("employee_id")}
    store_of = store_of or {}
    done = set(resolved or ())

    ours = {}
    for (eid, day), cell in (evidence or {}).items():
        if not isinstance(cell, dict):
            continue
        h, basis, detail = _basis.resolve_day(cell, basis_cfg, emp_by_id.get(eid))
        measured = _basis.basis_is_measurement(basis)
        # THE FACT, read from the one home, NOT re-derived here: how many of this day's punches the
        # kiosk closed by itself. It is carried separately from `measured` because an org that pays
        # auto-closed punches (today's default) still has to see them.
        auto = int(detail.get("auto_closed_punches") or 0)
        ours[(eid, _s(store_of.get((eid, day))), day)] = (h, basis, measured, auto)

    # Fold our rows onto the feed's store when we could not place ours — the variance is about the
    # employee's DAY, and an unplaced store must not fork one day into two rows.
    feed_keys = set(feed_hours or {})
    our_keys = set(ours)
    by_emp_day = {}
    for (eid, store, day) in feed_keys | our_keys:
        by_emp_day.setdefault((eid, day), set()).add(store)

    rows = []
    for (eid, day), stores in sorted(by_emp_day.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        feed_total = 0.0
        feed_seen = False
        our_total = 0.0
        our_seen = False
        our_basis = None
        store_code = ""
        auto_closed = 0
        feed_stores = set()
        our_stores = set()
        for st in sorted(stores):
            if (eid, st, day) in feed_keys:
                feed_total += float(feed_hours[(eid, st, day)])
                feed_seen = True
                if st:
                    feed_stores.add(st)
                store_code = store_code or st
            if (eid, st, day) in our_keys:
                h, basis, measured, auto = ours[(eid, st, day)]
                our_total += float(h)
                our_basis = our_basis or basis
                our_seen = our_seen or measured
                auto_closed += auto
                if st:
                    our_stores.add(st)
                store_code = store_code or st
        kind = None
        if feed_seen and not our_seen:
            kind = MISSING_PUNCH
        elif our_seen and not feed_seen:
            kind = EXTRA_PUNCH
        elif feed_seen and our_seen and abs(feed_total - our_total) > tol:
            kind = HOURS_DIFFER
        # TWO KINDS THAT ARE NOT A DISAGREEMENT ABOUT A NUMBER, and so would never be reached by the
        # three tests above. Both were in the vocabulary and emitted by nobody until 2026-10-10 —
        # a declared kind no caller raises is the un-wiring failure this house has had three times.
        #
        # STORE MISMATCH. The two records place the same person on the same day at DIFFERENT stores.
        # The hours may tie exactly, and the labour cost still lands on the wrong store's P&L.
        elif feed_stores and our_stores and not (feed_stores & our_stores):
            kind = STORE_MISMATCH
        # AUTO-CLOCKOUT. The kiosk closed our punch by itself, so its clock-out time is a sweep's
        # timestamp and not a measurement of anything. It is raised EVEN WHEN THE NUMBERS AGREE,
        # because today's default PAYS these punches: an org where it matters most is precisely the
        # org where an unraised flag would be invisible.
        elif auto_closed > 0:
            kind = AUTO_CLOCKOUT
        if kind is None:
            continue
        key = (eid, store_code, day)
        is_resolved = key in done
        rows.append({
            "employee_id": eid, "store_code": store_code, "date": day,
            "kind": kind, "reason": kind_reason(kind),
            "feed_hours": round(feed_total, 2) if feed_seen else None,
            "our_hours": round(our_total, 2) if our_basis else None,
            "our_basis": our_basis,
            "our_basis_reason": _basis.basis_reason(our_basis) if our_basis else None,
            "delta_hours": round(feed_total - our_total, 2),
            "tolerance_hours": tol,
            "auto_closed_punches": auto_closed,
            "feed_stores": sorted(feed_stores),
            "our_stores": sorted(our_stores),
            "resolved": is_resolved,
            "blocks": bool(kind_blocks(kind)) and not is_resolved,
        })

    # The rows that are not a two-number comparison, normalised onto the same shape.
    for x in (extra_rows or ()):
        x = x if isinstance(x, dict) else {}
        kind = x.get("kind") or EMPLOYEE_UNMATCHED
        key = (x.get("employee_id"), _s(x.get("store_code")), _d(x.get("date")))
        is_resolved = key in done
        rows.append({
            "employee_id": x.get("employee_id"), "store_code": _s(x.get("store_code")),
            "date": _d(x.get("date")), "kind": kind,
            "reason": x.get("reason") or kind_reason(kind),
            "feed_hours": x.get("feed_hours"), "our_hours": x.get("our_hours"),
            "our_basis": x.get("our_basis"), "our_basis_reason": x.get("our_basis_reason"),
            "delta_hours": x.get("delta_hours"), "tolerance_hours": tol,
            "employee_raw": x.get("employee_raw"), "feed_login": x.get("feed_login"),
            "match_basis": x.get("match_basis"),
            "proposed_employee_id": x.get("proposed_employee_id"),
            "proposed_name": x.get("proposed_name"), "candidates": x.get("candidates") or [],
            "resolved": is_resolved,
            "blocks": bool(kind_blocks(kind)) and not is_resolved,
        })

    by_store = {}
    for r in rows:
        cell = by_store.setdefault(r["store_code"] or "", {
            "store_code": r["store_code"] or "", "variances": 0, "blocking": 0,
            "resolved": 0, "kinds": {}})
        cell["variances"] += 1
        cell["kinds"][r["kind"]] = cell["kinds"].get(r["kind"], 0) + 1
        if r["resolved"]:
            cell["resolved"] += 1
        if r["blocks"]:
            cell["blocking"] += 1
    for cell in by_store.values():
        cell["all_clear"] = cell["blocking"] == 0
    blocking = [r for r in rows if r["blocks"]]
    return {"rows": rows, "by_store": by_store, "blocking": blocking,
            "totals": {"variances": len(rows), "blocking": len(blocking),
                       "resolved": sum(1 for r in rows if r["resolved"]),
                       "stores_with_variances": len(by_store),
                       "stores_all_clear": sum(1 for c in by_store.values() if c["all_clear"]),
                       "by_kind": _tally_kinds(rows),
                       # The headline a reader needs before they start correcting: how many hours of
                       # reported work has no measurement here at all.
                       "feed_hours": round(sum(_f(r.get("feed_hours")) for r in rows), 2),
                       "our_hours": round(sum(_f(r.get("our_hours")) for r in rows), 2),
                       "unmeasured_feed_hours": round(sum(_f(r.get("feed_hours")) for r in rows
                                                          if r["kind"] in (MISSING_PUNCH, LOGIN_UNLINKED)), 2)}}


def _tally_kinds(rows):
    out = {}
    for r in rows or ():
        k = (r or {}).get("kind")
        cell = out.setdefault(k, {"rows": 0, "blocking": 0, "feed_hours": 0.0, "our_hours": 0.0})
        cell["rows"] += 1
        if r.get("blocks"):
            cell["blocking"] += 1
        cell["feed_hours"] = round(cell["feed_hours"] + _f(r.get("feed_hours")), 2)
        cell["our_hours"] = round(cell["our_hours"] + _f(r.get("our_hours")), 2)
    return out


def store_all_clear(by_store, store_codes):
    """PURE: `{store_code: bool}` over EVERY store handed in — a store with no variance row at all
    is all-clear, and must appear saying so. A board that only lists problems cannot tell "clean"
    from "not looked at", which is the absence-is-never-zero doctrine applied to a checklist."""
    out = {}
    for code in store_codes or ():
        c = _s(code)
        if not c:
            continue
        cell = (by_store or {}).get(c)
        out[c] = True if cell is None else bool(cell.get("all_clear"))
    return out


# ── WHO IS ASKED ─────────────────────────────────────────────────────────────────────────────────
def recipient_ladder(chain):
    """PURE: `(recipients, level)` for ONE store's `org_chain.chain_for` answer.

    DM first; when the tree resolves no DM, the nearest manager ABOVE the district (market manager,
    regional, director — whatever that tenant's levels are called; nothing is spelled here). When
    neither resolves, `([], "unowned")` — REPORTED, never dropped, never a guessed name.

    This ladder exists because the measured coverage demands it: 6 of 29 Cellfonz stores and 0 of 20
    Luxelink stores resolve a DM (2026-10-10). A DM-keyed ask alone would reach eight stores out of
    fifty-one and look like it had asked everybody.
    """
    chain = chain if isinstance(chain, dict) else {}
    dm = [m for m in (chain.get("dm") or ()) if (m or {}).get("employee_id")]
    if dm:
        return dm, "dm"
    above = [m for m in (chain.get("above") or ()) if (m or {}).get("employee_id")]
    if above:
        return [above[0]], "above_district"
    return [], "unowned"


def ask_plan(by_store, dm_chains, store_codes=None):
    """PURE: who is asked about which stores, plus the stores nobody owns.

    Returns `{recipients, unowned_stores, coverage}`. `recipients` is keyed by employee_id and
    carries `stores`, `variances`, `blocking` and the ladder `level` used, so the digest can say
    "you are getting this because your district has no manager and you are the market manager".
    """
    codes = [c for c in (store_codes if store_codes is not None else list((by_store or {}).keys())) if _s(c)]
    recipients = {}
    unowned = []
    levels = {}
    for code in sorted(set(_s(c) for c in codes)):
        cell = (by_store or {}).get(code) or {}
        if not cell.get("blocking"):
            continue
        people, level = recipient_ladder((dm_chains or {}).get(code))
        levels[code] = level
        if not people:
            unowned.append({"store_code": code, "variances": cell.get("variances", 0),
                            "blocking": cell.get("blocking", 0),
                            "reason": "no district manager and no manager above the district "
                                      "resolves for this store in the org tree"})
            continue
        for p in people:
            eid = _s(p.get("employee_id"))
            if not eid:
                continue
            r = recipients.setdefault(eid, {"employee_id": eid, "name": p.get("name"),
                                            "email": p.get("email"), "phone": p.get("phone"),
                                            "stores": [], "variances": 0, "blocking": 0,
                                            "levels": set()})
            r["stores"].append(code)
            r["variances"] += int(cell.get("variances") or 0)
            r["blocking"] += int(cell.get("blocking") or 0)
            r["levels"].add(level)
    for r in recipients.values():
        r["stores"] = sorted(set(r["stores"]))
        r["levels"] = sorted(r["levels"])
        r["level"] = r["levels"][0] if len(r["levels"]) == 1 else "mixed"
    asked = sum(len(r["stores"]) for r in recipients.values())
    return {"recipients": recipients, "unowned_stores": unowned,
            "coverage": {"stores_with_blocking": sum(1 for c in codes
                                                     if ((by_store or {}).get(_s(c)) or {}).get("blocking")),
                         "store_assignments": asked,
                         "stores_unowned": len(unowned),
                         "levels": levels}}


#: `storeops.alert_log.scope` for the weekly ask and for the login alert. One scope each, so the
#: existing dedup/record machinery is reused rather than a second log being invented.
ASK_SCOPE = "clock_variance_weekly_ask"
LOGIN_SCOPE = "clock_variance_login_alert"


def ask_ref_key(org_id, period_start, period_end, employee_id, store_code=None):
    """PURE: the `alert_log.ref_key` for one recipient's weekly ask. Keyed by PERIOD, so a second
    sweep in the same week is deduped and the next period asks again — the same shape every other
    weekly digest in this codebase uses.

    `store_code`, when given, puts the STORE in the key tail. That is deliberate and not cosmetic: a
    store whose file arrives late turns blocking halfway through the week, and a key that named only
    the recipient would count them as already asked and never mention the new store. One key per
    (recipient, store) asks again for the store that is new and stays silent about the ones already
    carried — which is what the per-channel dedup in `alert_log` is for.
    """
    tail = f":{store_code}" if _s(store_code) else ""
    return f"{org_id}:{period_start}:{period_end}:{employee_id}{tail}"


def ask_digests(plan, *, org_id, period_start, period_end):
    """PURE: `ask_plan` output rendered as the digest fan-out `digest_delivery.deliver_digests` eats.

    One digest per recipient, one ITEM per store, each item carrying its own `ref_key` — see
    `ask_ref_key`. The `addresses` dict is what the delivery ladder reads, so a recipient with only a
    phone is still reached on WhatsApp and a recipient with neither is returned with no addresses at
    all rather than silently dropped (`deliver_recipient` marks that `no_channel`, which retries).

    Nothing here decides whether to send. The weekday decision is `is_ask_day`, the switch is
    `variance_ask_enabled`, and both live with the rest of the config.
    """
    out = []
    for eid, r in sorted(((k, v) for k, v in ((plan or {}).get("recipients") or {}).items()),
                         key=lambda kv: _s(kv[0])):
        items = [{"ref_key": ask_ref_key(org_id, period_start, period_end, eid, code),
                  "employee_id": eid, "store_code": code, "level": r.get("level"),
                  "variances": int(r.get("variances") or 0),
                  "blocking": int(r.get("blocking") or 0)}
                 for code in (r.get("stores") or ())]
        if not items:
            continue
        addrs = {}
        if _s(r.get("email")):
            addrs["email"] = _s(r.get("email"))
        if _s(r.get("phone")):
            addrs["whatsapp"] = _s(r.get("phone"))
        out.append({"to": addrs.get("email") or addrs.get("whatsapp") or "",
                    "to_name": r.get("name"), "addresses": addrs,
                    "employee_id": eid, "items": items})
    return out


def _esc(v):
    """PURE: the five XML entities, so a store spelling with an `&` in it cannot break the email."""
    return (_s(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


#: Why the ask says what it says. The owner's own words, kept whole, because the reason the Elevate
#: clock-out is used as pay has to travel to the person being asked to correct it — the ruling says
#: *"mention on the report why you are doing so"*, and an email that asks for a correction without
#: saying why is the report the ruling was about.
ASK_WHY = ("Where the Elevate clock-out is earlier than ours, the earlier record is what payroll "
           "pays: a later clock-out with no work left to do is time in the store, not time worked. "
           "If that reading is wrong for a row, override it with a reason and the override wins.")


def build_ask_digest(name, items, *, period_start=None, period_end=None, run_weekday=None,
                     gate_weekday=None, link=None, why=None):
    """PURE: ONE recipient's weekly clock-variance ask — `{"subject", "html", "text"}`.

    The text body is the WhatsApp rendering: shorter, same facts in the same order, because two
    renderings of one ask that disagree is an ask nobody trusts (the `visit_alerts.build_digest`
    convention, followed rather than re-invented).

    It states WHAT blocks, WHICH stores, BY WHEN, and WHY — the four things a manager needs to act
    without replying to ask. It never carries a pay figure: the ask goes to district managers, and
    money is market-manager-and-above (`storeops/pay_visibility.py`).
    """
    items = [it for it in (items or []) if isinstance(it, dict)]
    stores = sorted({_s(it.get("store_code")) for it in items if _s(it.get("store_code"))})
    blocking = sum(int(it.get("blocking") or 0) for it in items)
    period = (f"{period_start} to {period_end}" if _s(period_start) and _s(period_end)
              else "the current pay period")
    subject = ("Clock-in corrections needed before payroll: {b} row{s} across {k} store{ks}"
               .format(b=blocking, s="" if blocking == 1 else "s", k=len(stores),
                       ks="" if len(stores) == 1 else "s"))
    by = ""
    if gate_weekday is not None and run_weekday is not None:
        by = (" Corrections are due before this org's gate day (weekday {g}); payroll runs on "
              "weekday {r}.".format(g=int(gate_weekday), r=int(run_weekday)))
    rows = "".join(
        "<tr><td>{st}</td><td>{b}</td><td>{v}</td></tr>".format(
            st=_esc(it.get("store_code")), b=int(it.get("blocking") or 0),
            v=int(it.get("variances") or 0)) for it in items)
    foot = ('<p><a href="{0}">Open the clock-variance board</a></p>'.format(_esc(link))
            if _s(link) else "")
    html = (
        "<p>Hello {name},</p>"
        "<p>The Elevate clock-in/clock-out report and our own time clock disagree on {b} row{s} "
        "for {p}, across {k} store{ks}. Payroll cannot run for those rows until each one is either "
        "corrected or accepted.{by}</p>"
        "<p><i>{why}</i></p>"
        "<table border=1 cellpadding=6 cellspacing=0>"
        "<tr><th>Store</th><th>Still blocking</th><th>Variances seen</th></tr>{rows}</table>{foot}"
    ).format(name=_esc(name) or "there", b=blocking, s="" if blocking == 1 else "s", p=_esc(period),
             k=len(stores), ks="" if len(stores) == 1 else "s", by=_esc(by).replace("&#39;", "'"),
             why=_esc(why or ASK_WHY), rows=rows, foot=foot)
    text = ("{b} clock-in row{s} need correcting for {p} ({k} store{ks}: {names}). "
            "Payroll cannot run for those rows until each is corrected or accepted.{by} {why}"
            ).format(b=blocking, s="" if blocking == 1 else "s", p=period, k=len(stores),
                     ks="" if len(stores) == 1 else "s", names=", ".join(stores) or "none named",
                     by=by, why=why or ASK_WHY)
    return {"subject": subject, "html": html, "text": text}


# ── THE WEEK ─────────────────────────────────────────────────────────────────────────────────────
def weekday_plan(cfg):
    """PURE: `{ask, gate, run}` weekday numbers (0 = Monday) for this org. One read, so no caller
    spells a weekday."""
    cfg = resolve_config(cfg if isinstance(cfg, dict) else None)
    return {"ask": cfg["ask_weekday"], "gate": cfg["gate_weekday"], "run": cfg["run_weekday"]}


def is_ask_day(weekday, cfg):
    """PURE: should the weekly ask go out on this business-local weekday?"""
    cfg = resolve_config(cfg if isinstance(cfg, dict) else None)
    return bool(cfg["variance_ask_enabled"]) and int(weekday) % 7 == cfg["ask_weekday"]


def _gate_window_open(weekday, cfg):
    """PURE: is the business-local weekday at or past the gate weekday, within the ask→run week?

    The window runs from the gate weekday up to AND INCLUDING the run weekday, computed as an offset
    from the ASK weekday so a tenant that asks on Thursday and runs on Saturday needs no new code.
    After the run weekday the week is over and the gate lifts on its own — a gate that outlives its
    payroll run is a lockout nobody can clear.
    """
    ask, gate, run = cfg["ask_weekday"], cfg["gate_weekday"], cfg["run_weekday"]
    off = lambda d: (int(d) - ask) % 7           # noqa: E731 — days since the ask, in week order
    return off(gate) <= off(weekday) <= off(run)


# ── THE DM's PER-ROW OVERRIDE (owner ruling 2026-10-10) ──────────────────────────────────────────
#: How the override appears in `storeops.payroll_change_log`. DECLARED HERE, once, because the
#: endpoint that writes the log and the harness that proves it was written must agree on the word —
#: two string literals in two files is how an audit trail quietly stops being searchable.
OVERRIDE_ENTRY_POINT = "clock_variance_override"
OVERRIDE_FIELD = "override_hours"
#: The resolutions a human may record. The SQL CHECK constraint carries the same three, so a value
#: this module does not know cannot reach the table either.
RESOLUTIONS = ("corrected", "accepted_ours", "accepted_feed")


# ── THE GATE ─────────────────────────────────────────────────────────────────────────────────────
#: The ONE path a gated manager may reach, plus the calls that page itself needs. A gate that does
#: not name its own escape is a lockout.
CORRECTION_PATH = "/storeops/payroll/clock-variances"
ALLOWED_PATH_PREFIXES = (CORRECTION_PATH, "/login", "/logout", "/auth", "/api/storeops/payroll/clock-variance")


def gate_decision(*, weekday, role, login, blocking_count, cfg, business_date=None):
    """PURE: what a signing-in manager is shown. NEVER a refused sign-in.

    Returns `{alert, gated, allowed_paths, reason, weekday_plan}`:

      `alert`   True as soon as there is anything uncorrected for their stores, on ANY weekday —
                the owner's *"notified as an alert as soon as they log in"*.
      `gated`   True only from the gate weekday, only while something is still blocking, only for a
                role this org gates, and only when the login gate is switched on. A gated session
                signs in normally and is confined to `allowed_paths`; it is not logged out and it is
                never refused, which is the house's ★ NO LOCKOUT ★ property (index §22).

    `role` is folded case/space-insensitively against the org's `gated_roles` rows. `login` is
    checked against `gate_exempt_logins` — the escape hatch as a config row, so no login is named in
    code and an exemption is visible to whoever reads the config.
    """
    cfg = resolve_config(cfg if isinstance(cfg, dict) else None)
    plan = {"ask": cfg["ask_weekday"], "gate": cfg["gate_weekday"], "run": cfg["run_weekday"]}
    n = int(blocking_count or 0)
    # THE ONE ACCENT FOLD (`account/store_identity.fold_accents`, 2026-10-10) — dereferenced,
    # never re-spelled: `str.isalnum()` is TRUE for an accented letter, so this fold kept the
    # accent and an accented spelling was a different key from its own plain twin. ASCII input
    # is byte-identical, so no existing match moves; the fold only gains the ones it should
    # always have had.
    role_key = "".join(ch for ch in _sid_fold(role).strip().lower().replace(" ", "_")
                       if ch.isalnum() or ch == "_")
    login_key = str(login or "").strip().lower()
    base = {"weekday_plan": plan, "blocking_count": n, "allowed_paths": list(ALLOWED_PATH_PREFIXES),
            "correction_path": CORRECTION_PATH, "business_date": business_date}
    if n <= 0:
        return {**base, "alert": False, "gated": False,
                "reason": "Nothing uncorrected for this manager's stores in the current pay period."}
    alert = True
    if not cfg["login_gate_enabled"]:
        return {**base, "alert": alert, "gated": False,
                "reason": f"{n} uncorrected clock variance(s) — this org has the sign-in gate "
                          f"switched off, so this is an alert only."}
    if login_key and login_key in cfg["gate_exempt_logins"]:
        return {**base, "alert": alert, "gated": False,
                "reason": f"{n} uncorrected clock variance(s) — this login is on this org's "
                          f"gate_exempt_logins list."}
    if role_key not in cfg["gated_roles"]:
        return {**base, "alert": alert, "gated": False,
                "reason": f"{n} uncorrected clock variance(s) — this role is not one this org "
                          f"gates, so it is an alert only."}
    if not _gate_window_open(int(weekday) % 7, cfg):
        return {**base, "alert": alert, "gated": False,
                "reason": f"{n} uncorrected clock variance(s) — the correction window does not "
                          f"close until this org's gate weekday ({cfg['gate_weekday']})."}
    return {**base, "alert": alert, "gated": True,
            "reason": f"{n} clock variance(s) are still uncorrected and payroll runs on this org's "
                      f"run weekday ({cfg['run_weekday']}). You can sign in and fix them; until "
                      f"they are cleared this session can only reach the correction board."}


def employees_without_rate(employee_rows, paid_employee_ids=None):
    """PURE: the ACTIVE HOURLY employees this period would price at nothing, because they have no
    `pay_rate` at all.

    MEASURED on production 2026-10-10: **13 of 46 active hourly house employees have no pay_rate**,
    and ten of them are exactly the reps a runbook written on 3 October was meant to set to $17.00/h
    — still NULL, so it was never applied. Those same people dominate the unpaid-hours list. Pricing
    them at zero would produce a payroll that looks complete and silently pays nobody; the house rule
    is that a defect found in live data is REPORTED. `paid_employee_ids`, when given, narrows the
    check to the people this period would actually pay, so a dormant record never blocks a run.

    A SALARIED employee legitimately has no hourly rate (their pay comes from `pay_amount` through
    `payroll_salary`), so only `pay_basis = 'hourly'` — or a blank basis, which this platform treats
    as hourly everywhere — is counted.
    """
    want = None if paid_employee_ids is None else {_s(x) for x in paid_employee_ids if _s(x)}
    out = []
    for e in (employee_rows or ()):
        e = e or {}
        eid = _s(e.get("employee_id"))
        if not eid or (want is not None and eid not in want):
            continue
        if e.get("is_active") is False:
            continue
        basis = _s(e.get("pay_basis")).lower()
        if basis and basis != "hourly":
            continue
        rate = e.get("pay_rate")
        if rate is None or _f(rate) <= 0:
            out.append({"employee_id": eid, "name": e.get("name"),
                        "store_code": e.get("home_store"), "pay_basis": basis or "hourly"})
    return sorted(out, key=lambda r: r["employee_id"])


def payroll_run_gate(*, blocking, cfg, employee_rows=None, paid_employee_ids=None):
    """PURE: may the payroll run compute for this period?

    TWO preflight refusals, both of them "report the defect, never hide it":
      1. uncorrected clock variances (the owner's Wednesday rule), and
      2. employees this run would price at NOTHING because they carry no `pay_rate`.

    Refuses by default and NAMES what blocks — a refusal that does not say which rows is a wall, not
    a gate. `payroll_run_gate_enabled` off = today's behaviour exactly for the variance half; the
    MISSING-RATE half is reported either way, because a payroll that pays somebody $0.00 because a
    column is NULL is wrong whether or not an org has opted into the clock feed.
    """
    cfg = resolve_config(cfg if isinstance(cfg, dict) else None)
    rows = list(blocking or ())
    no_rate = employees_without_rate(employee_rows, paid_employee_ids)
    reasons = []
    refused = False
    if no_rate:
        refused = True
        reasons.append(
            f"{len(no_rate)} employee(s) this run would pay have NO pay rate on file "
            f"({', '.join(r['employee_id'] for r in no_rate[:20])}"
            f"{', …' if len(no_rate) > 20 else ''}). They would be priced at $0.00, which looks "
            f"like a finished payroll and pays nobody. Set their rate — this is a missing-data "
            f"defect, not a figure.")
    if cfg["payroll_run_gate_enabled"] and rows:
        refused = True
        names = ", ".join(sorted({f"{r.get('store_code')}/{r.get('employee_id')} {r.get('date')}"
                                  for r in rows})[:20])
        reasons.append(f"{len(rows)} clock variance(s) are uncorrected for this period and this org "
                       f"requires them cleared before payroll runs: {names}")
    if not refused:
        base = ("Every clock variance in this period is corrected or accepted"
                if cfg["payroll_run_gate_enabled"]
                else "This org has the payroll-run clock-variance gate switched off")
        reasons.append(base + ", and every employee this run would pay has a rate on file.")
    return {"ok": not refused, "refused": refused, "blocking_count": len(rows), "blocking": rows,
            "employees_without_rate": no_rate, "reason": " ".join(reasons)}


# ── THE ONE IMPURE FUNCTION (the `carrier_dollar_class.load_config` convention) ──────────────────
#: The variance / ask / gate knobs live in the SAME per-org row the hours basis reads (mig 1073):
#: one row, one read, no second config table for one feature.
CONFIG_TABLE = _basis.CONFIG_TABLE
_CONFIG_CACHE = {}


def load_config(client, org_id, *, use_cache=True):
    """The per-org row merged over the house defaults. THE one reader of these columns.

    Every failure mode — no row, no table, an unreachable database — returns the house defaults,
    i.e. the whole feature OFF. A missing config must never switch a gate ON.
    """
    key = str(org_id)
    if use_cache and key in _CONFIG_CACHE:
        return _CONFIG_CACHE[key]
    row = None
    try:
        rows = (client.table(CONFIG_TABLE).select("*").eq("org_id", org_id).limit(1).execute().data) or []
        row = rows[0] if rows else None
    except Exception:
        row = None
    cfg = resolve_config(row)
    cfg["config_row_present"] = row is not None
    if use_cache:
        _CONFIG_CACHE[key] = cfg
    return cfg


def invalidate_config(org_id=None):
    if org_id is None:
        _CONFIG_CACHE.clear()
    else:
        _CONFIG_CACHE.pop(str(org_id), None)
