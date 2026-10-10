"""Payroll employee-identity reconciliation (mod-people, 2026-07-27 owner directive: "the names are
coming twice for the same sales rep in the report ... need one line with both scheduled and actual").

ROOT CAUSE (confirmed by reading, not guessed): the Schedule page stores a NEW shift's `employee_id`
as the employee's NUMERIC `employees.id` primary key —
    frontend/src/app/(platform)/storeops/schedule/page.tsx:294
        employee_id: emp?.id?.toString() || '',
— while every OTHER source of payroll hours (a kiosk clock punch via `storeops.timelog`, a manual
hours adjustment via `storeops.manual_hours`, and the `employees` roster's own `employee_id` column
used to build `emp_map`/RBAC) uses the BUSINESS `employees.employee_id` (e.g. "E45"). This is the
EXACT SAME mismatch `_emp_id_variants()` (storeops/router.py) already reconciles for the
shift-extension / force-clockout gate — this module is the payroll-AGGREGATION side of that identical
bug.

`GET /payroll`'s `summary` dict is keyed by whatever raw `employee_id` a source row carries, so a
person with BOTH a shift this period (numeric-id-keyed) AND a clock punch (business-id-keyed) lands
in TWO buckets — visually two rows, same name: one shows scheduled hours (pay_rate wrongly $0,
because `emp_map` is keyed by the business id and the shift-derived bucket's key never matches it),
the other shows the real clocked "actual" hours at the correct rate. Exactly the reported symptom.

FIX SCOPE — presentation only, no pay-math change (AGENT_CONTRACT: "a change to hours/rate math is
propose-first"): `reconcile_employee_identity()` relabels each row's `employee_id` to the CANONICAL
business id (when resolvable) and, for rows that resolve to the SAME canonical id, SUMS their
already-computed scheduled_hours / actual_hours / shifts / scheduled_pay / actual_pay. Every summed
number is a value the existing (unmodified) router aggregation already computed per source row before
this function ever runs — it regroups outputs, it never recomputes hours×rate. A row's grand total,
and the SUM across every row, is therefore byte-identical before/after regrouping (associativity of
addition) — proven by harness_payroll_row_merge.py, including a control fixture where NO row's raw
employee_id needs aliasing (the existing employee-consistent fixtures in
harness_payroll_rpc_equivalence.py / harness_payroll_data_flow.py all fall in this "no-op" bucket,
which is exactly why this bug was invisible to 34+80 pre-existing harness checks despite being
essentially universal — every one of those fixtures happens to use the SAME id string for a person's
shift and timelog rows, unlike real Schedule-page-created shifts).

The one NEW thing shown is `pay_rate`: corrected to the employee's real rate (an informational label
read straight from `emp_map`) even for a single, un-merged shift-only row that would otherwise always
display 0 due to the identical key bug — the row's own scheduled_pay/actual_pay DOLLAR FIGURE is never
recomputed from this corrected rate, it stays exactly what the router already computed.

KNOWN, DELIBERATELY NOT FIXED HERE (money-adjacent, propose-first, see docs/handoffs/people.md and the
Gate-1 report): because the pre-merge shift-derived row's own actual_hours already carries the
act==0->scheduled FALLBACK (nothing ever writes storeops.shifts.actual_hours from a real clock punch —
grepped, confirmed empty), summing it together with the SAME day's real timelog hours can still show a
merged row's "Actual Hrs" larger than what the person truly worked that day (the fallback and the real
punch both count). This function does not touch that — it is the artifact investigated in Deliverable 3
of the 2026-07-27 payroll package (VERDICT: real double-count, not real overwork; propose-first fix is
canonicalizing the aggregation KEY server-side inside the shift/timelog merge itself, which would
change /payroll-by-store's pushed dollar amount and is therefore out of scope for a presentation-only
change).
"""


def business_id_alias_map(employees):
    """{str(numeric employees.id): business employees.employee_id} for every employee whose two id
    forms actually differ (the common case, since the auto-generated business id is "E<numeric id>",
    e.g. numeric id 45 -> business id "E45" -- never equal as strings). An employee with no business
    employee_id, or whose business id happens to equal their numeric id (imported data), contributes
    no alias entry, which is exactly correct: nothing to reconcile.

    Gate-1 N1 fix (2026-07-27, MEDIUM, reviewer-proven executable): a NUMERIC id can collide with a
    DIFFERENT employee's own all-digit business employee_id — e.g. employee A (numeric id 42,
    business "E42") and employee B (business employee_id literally "42", imported/typed data, no
    relation to A). Before this fix, alias["42"] = "E42" unconditionally, so a shift stored under the
    numeric id "42" (A's Schedule-created shift) would be silently relabeled and MERGED into A's row
    even when "42" is actually B's own canonical identity — B's hours vanish from payroll, absorbed
    under A's name/rate. Guarded: an alias entry is only created when its numeric_s does NOT appear
    anywhere in the org's set of real business employee_ids — an ambiguous numeric_s is left
    UNALIASED (two rows, the safe fallback) rather than guessed at."""
    employees = list(employees or ())
    all_business_ids = {str(e.get("employee_id") or "").strip() for e in employees if e.get("employee_id")}
    alias = {}
    for e in employees:
        numeric = e.get("id")
        biz = str(e.get("employee_id") or "").strip()
        if numeric is None or not biz:
            continue
        numeric_s = str(numeric).strip()
        if not numeric_s or numeric_s == biz:
            continue
        if numeric_s in all_business_ids:
            continue   # AMBIGUOUS — numeric_s is itself someone's real business id; don't guess
        alias[numeric_s] = biz
    return alias


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def reconcile_employee_identity(rows, employees):
    """Collapse duplicate /payroll rows that represent the SAME physical employee (numeric-id-keyed
    shift bucket + business-id-keyed timelog bucket) into ONE row. See module docstring for the full
    root-cause + fix-scope writeup. `rows` = the router's already-built, already-$-computed summary
    rows (list of dicts with employee_id/name/store/pay_rate/scheduled_hours/actual_hours/shifts/
    scheduled_pay/actual_pay); `employees` = the same roster rows the caller already fetched (needs
    `id`, `employee_id`, `pay_rate`). A `None`/blank employee_id row (e.g. a shift with no assigned
    employee) is NEVER merged with anything — kept exactly as-is, in place."""
    if not rows:
        return rows
    alias = business_id_alias_map(employees)
    emp_map = {e.get("employee_id"): e for e in (employees or ()) if e.get("employee_id")}

    order = []
    groups = {}   # canonical key -> [(canon_id, row), ...], first-seen order preserved
    for r in rows:
        raw = r.get("employee_id")
        if raw is None or str(raw).strip() == "":
            canon = None
        else:
            canon = alias.get(str(raw), raw)
        group_key = canon if canon is not None else (id(r), "unmergeable")
        if group_key not in groups:
            groups[group_key] = []
            order.append(group_key)
        groups[group_key].append((canon, r))

    out = []
    for gk in order:
        members = groups[gk]
        canon, first = members[0]
        if len(members) == 1:
            merged = dict(first)
            if canon is not None:
                merged["employee_id"] = canon
                real = emp_map.get(canon)
                if real is not None:
                    merged["pay_rate"] = _num(real.get("pay_rate"))
            out.append(merged)
            continue
        # 2+ rows resolve to the same canonical employee -> ONE merged row, every numeric field the
        # exact SUM of the members' already-computed values (never recomputed from merged hours).
        real = emp_map.get(canon)
        merged = {
            "employee_id": canon,
            "name": next((r.get("name") for _, r in members if r.get("name")), first.get("name")),
            "store": first.get("store"),
            "pay_rate": _num(real.get("pay_rate")) if real is not None
                        else max(_num(r.get("pay_rate")) for _, r in members),
            "scheduled_hours": round(sum(_num(r.get("scheduled_hours")) for _, r in members), 2),
            "actual_hours": round(sum(_num(r.get("actual_hours")) for _, r in members), 2),
            "shifts": sum(int(r.get("shifts") or 0) for _, r in members),
            "scheduled_pay": round(sum(_num(r.get("scheduled_pay")) for _, r in members), 2),
            "actual_pay": round(sum(_num(r.get("actual_pay")) for _, r in members), 2),
        }
        # Lunch-break auto-deduction (2026-07-27, Deliverable 3): the deducted hours already subtracted
        # out of the SOURCE rows' own actual_hours above (see router.py get_payroll) — summed here too
        # so the informational total survives the merge instead of silently vanishing from whichever
        # bucket carried it (always the timelog/business-id bucket, since storeops.timelog.employee_id
        # is always the business id). EQUIVALENCE: the key is only added to the merged row when at
        # least one member actually HAS it (the feature is available) — never fabricated as a bare 0.0
        # on a caller/fixture that predates this feature, so an exact-JSON-shape assertion elsewhere
        # stays byte-identical pre-migration-418.
        if any("lunch_deduction_hours" in r for _, r in members):
            merged["lunch_deduction_hours"] = round(sum(_num(r.get("lunch_deduction_hours")) for _, r in members), 2)
        # Dominant-store LABEL only (non-monetary) across the merged members — the member with the
        # larger hours weight wins, first-seen tie-break (mirrors the router's own dominant-store
        # convention for a single employee's multi-store hours).
        best = max(members, key=lambda cr: (_num(cr[1].get("scheduled_hours")) + _num(cr[1].get("actual_hours"))))
        merged["store"] = best[1].get("store") or merged["store"]
        out.append(merged)
    return out


# ════════════════════════════════════════════════════════════════════════════════════════════════
# WHICH EMPLOYEE IS THIS CLOCK-FEED ROW ABOUT? (owner ask 2026-10-10, index §14z)
# ════════════════════════════════════════════════════════════════════════════════════════════════
# THE CLASS. The B2B clock report names a person two ways — `Salesperson` as `"Last, First"` (and
# sometimes `"Last, First, M"`) and `User Login` (`gsantos`, `M.saul`, `Abdullah.M`, `Debo`) — and
# MEASURED on the live house roster 2026-10-10, **neither is reliably stored on the employee record**:
# only 14 of 57 employees have `epay_salesperson` and only 14 have `epay_login`, and the feed's own
# login column is not stored anywhere at all. 29 of 45 rows match a roster employee by NAME TOKEN;
# `Martínez, LESLIEE` and `Purkayastha, Deborshee` match nothing.
#
# SO A NAME IS A PROPOSAL, NEVER A BINDING. The house rule is that an identity is never guessed at
# runtime (`core.scope._squash`'s own docstring says so, and migration 740's human-reviewed cleanup
# exists because those decisions are not safe as code). A name-token hit therefore produces a
# PROPOSAL for a human to approve, persisted once approved in `storeops.clock_feed_login_link`
# (mig 1070); only an approved link, or one of the two stored id columns, BINDS. "This login is not
# linked to anybody" is a first-class variance the DM resolves, exactly like an hours difference.
#
# THIS IS AN EXTENSION OF THIS MODULE, NOT A SECOND MATCHER. `business_id_alias_map` above already
# owns "which employee id is which"; these functions add "which SPELLING is which employee" beside
# it, in the same home, so nothing else in the platform has to decide.

#: How a feed row was bound to an employee. Part of every answer; never a default.
MATCH_APPROVED_LINK = "approved_login_link"
MATCH_EPAY_LOGIN = "epay_login"
MATCH_EPAY_SALESPERSON = "epay_salesperson"
MATCH_EMPLOYEE_ID = "employee_id"
MATCH_NAME_PROPOSED = "name_proposed"
MATCH_NAME_AMBIGUOUS = "name_ambiguous"
MATCH_UNMATCHED = "unmatched"

ALL_MATCH_BASES = (MATCH_APPROVED_LINK, MATCH_EPAY_LOGIN, MATCH_EPAY_SALESPERSON,
                   MATCH_EMPLOYEE_ID, MATCH_NAME_PROPOSED, MATCH_NAME_AMBIGUOUS, MATCH_UNMATCHED)

#: THE ALLOWLIST (the `carrier_dollar_class.component_is_evidence` pattern, index §66). A basis on
#: which a feed row may actually be ATTRIBUTED to a person. A name proposal is deliberately absent,
#: and so is anything nobody has ruled on: an unknown basis does NOT bind, because the direction the
#: next mistake should fall in is "ask a human", never "assume it is them".
BINDING_MATCH_BASES = (MATCH_APPROVED_LINK, MATCH_EPAY_LOGIN, MATCH_EPAY_SALESPERSON,
                       MATCH_EMPLOYEE_ID)
NON_BINDING_MATCH_BASES = tuple(b for b in ALL_MATCH_BASES if b not in BINDING_MATCH_BASES)

MATCH_REASONS = {
    MATCH_APPROVED_LINK: "A human approved the link between this clock-report login and this "
                         "employee, so the row is attributed to them.",
    MATCH_EPAY_LOGIN: "The employee record stores this login (employees.epay_login).",
    MATCH_EPAY_SALESPERSON: "The employee record stores this spelling of the name "
                            "(employees.epay_salesperson).",
    MATCH_EMPLOYEE_ID: "The feed carried this org's own employee id.",
    MATCH_NAME_PROPOSED: "The name on this row matches exactly one roster employee by name token. "
                         "That is a PROPOSAL for someone to approve, not an attribution — a person "
                         "is never inferred from a name at runtime.",
    MATCH_NAME_AMBIGUOUS: "The name on this row matches more than one roster employee, so it names "
                          "nobody. Reported rather than resolved to the first hit.",
    MATCH_UNMATCHED: "Nothing on this row binds to this org's employee roster — not an approved "
                     "link, not a stored login, not a name. The hours cannot be attributed to "
                     "anybody until a human says who this is.",
}


def match_is_binding(basis):
    """PURE: may a clock-feed row be ATTRIBUTED to an employee on this basis? Allowlist; unknown
    bases do not bind. THE QUESTION, NOT THE POLICY — the caller decides what a False means."""
    return basis in BINDING_MATCH_BASES


def match_reason(basis):
    """PURE: the sentence shown beside an unmatched or proposed row."""
    return MATCH_REASONS.get(basis, f"This match basis ({basis!r}) has no ruling in "
                                    f"payroll_identity, so it does not bind.")


def _fold_person(v):
    """The comparison form for a person spelling: accent-folded through THE one accent fold
    (`account/store_identity.fold_accents`), lower-cased, non-alphanumerics dropped.

    THE ACCENT FOLD IS WHY THIS EXISTS. `str.isalnum()` is True for `'í'`, so every plain fold in
    this codebase kept it and `Martínez` compared unequal to `Martinez` — a live employee whose
    clock rows matched nothing at all. The fold itself was fixed in its one home, for every caller;
    this function simply uses it."""
    from app.modules.account.store_identity import fold_accents
    return "".join(ch for ch in fold_accents(v).lower() if ch.isalnum())


def name_tokens(v):
    """PURE: the set of folded name TOKENS in a person spelling, in any of its shapes.

    `"Martínez, LESLIEE"` → `{'martinez', 'lesliee'}`; `"Caba, David, A"` → `{'caba','david','a'}`
    (a middle initial is a THIRD comma segment, so nothing here splits on the comma and assumes two
    parts); `"Leslie Martinez"` → `{'leslie','martinez'}`. Order-free on purpose: the feed writes
    `Last, First` and the roster writes `First Last`, and which way round a name is written is not a
    fact about who the person is.
    """
    import re as _re
    return {t for t in (_fold_person(x) for x in _re.split(r"[,\s]+", str(v or ""))) if t}


def person_key(v):
    """PURE: the order-free identity key for a person spelling — the sorted folded tokens joined.

    `"Martínez, LESLIEE"` and `"LESLIEE Martinez"` share a key; `"Caba, David, A"` does NOT share a
    key with `"Caba, David"`, because dropping a middle initial is a judgement a human makes, not
    one a key may make silently. `match_tokens` is the looser, REPORTED comparison for that case.
    """
    return "|".join(sorted(name_tokens(v)))


def build_employee_matcher(employees, link_rows=None):
    """`match(feed_login, feed_name) -> (employee_id | None, basis, detail)` for one org.

    `employees`  the org's `storeops.employees` rows (`employee_id`, `name`, `epay_login`,
                 `epay_salesperson`).
    `link_rows`  `storeops.clock_feed_login_link` rows (mig 1070): `feed_login`, `employee_id`,
                 `status`. ONLY `status == 'approved'` binds; a `proposed` row is a pending
                 question and a `rejected` row is a human saying "not this person", which must
                 never be re-proposed as if nobody had looked.

    PRECEDENCE: approved link > stored login > stored salesperson spelling > our own employee id >
    a name PROPOSAL (not binding) > ambiguous > unmatched.
    """
    emps = [e for e in (employees or ()) if (e or {}).get("employee_id")]
    by_id = {str(e["employee_id"]).strip(): e for e in emps}
    by_login = {}
    by_sales = {}
    by_key = {}
    by_token = {}
    for e in emps:
        eid = str(e["employee_id"]).strip()
        for col, bucket in (("epay_login", by_login), ("epay_salesperson", by_sales)):
            k = _fold_person(e.get(col))
            if k:
                bucket.setdefault(k, set()).add(eid)
        k = person_key(e.get("name"))
        if k:
            by_key.setdefault(k, set()).add(eid)
        for t in name_tokens(e.get("name")):
            by_token.setdefault(t, set()).add(eid)

    approved, rejected = {}, {}
    for r in (link_rows or ()):
        login = _fold_person((r or {}).get("feed_login"))
        eid = str((r or {}).get("employee_id") or "").strip()
        status = str((r or {}).get("status") or "").strip().lower()
        if not login or not eid:
            continue
        if status == "approved":
            approved[login] = eid
        elif status == "rejected":
            rejected.setdefault(login, set()).add(eid)

    def match(feed_login, feed_name):
        login_k = _fold_person(feed_login)
        detail = {"feed_login": str(feed_login or "").strip(),
                  "feed_name": str(feed_name or "").strip(),
                  "candidates": []}

        def done(eid, basis):
            detail["reason"] = match_reason(basis)
            detail["binds"] = match_is_binding(basis)
            return eid, basis, detail

        if login_k and login_k in approved:
            return done(approved[login_k], MATCH_APPROVED_LINK)
        for bucket, basis in ((by_login, MATCH_EPAY_LOGIN), (by_sales, MATCH_EPAY_SALESPERSON)):
            hits = bucket.get(login_k) or set()
            if len(hits) == 1:
                return done(next(iter(hits)), basis)
            if len(hits) > 1:
                detail["candidates"] = sorted(hits)
                return done(None, MATCH_NAME_AMBIGUOUS)
        name_login_hits = by_sales.get(_fold_person(feed_name)) or set()
        if len(name_login_hits) == 1:
            return done(next(iter(name_login_hits)), MATCH_EPAY_SALESPERSON)
        raw_id = str(feed_login or "").strip()
        if raw_id in by_id:
            return done(raw_id, MATCH_EMPLOYEE_ID)

        # ── NAME: a PROPOSAL, never an attribution ──────────────────────────────────────────────
        key = person_key(feed_name)
        hits = set(by_key.get(key) or set())
        if not hits:
            toks = name_tokens(feed_name)
            if toks:
                # every roster employee sharing ALL of this row's tokens (so `Caba, David, A`
                # proposes `David Caba`, while `Caba, David` never proposes a different David).
                shared = None
                for t in toks:
                    s = by_token.get(t) or set()
                    shared = s if shared is None else (shared & s)
                hits = set(shared or ())
                if not hits:
                    # the other direction: the roster spelling is a SUBSET of the feed's tokens
                    for t in toks:
                        for eid in by_token.get(t) or ():
                            if name_tokens((by_id.get(eid) or {}).get("name")) <= toks:
                                hits.add(eid)
        hits = {h for h in hits if h not in (rejected.get(login_k) or set())}
        if len(hits) == 1:
            eid = next(iter(hits))
            detail["candidates"] = [eid]
            detail["proposed_employee_id"] = eid
            detail["proposed_name"] = (by_id.get(eid) or {}).get("name")
            return done(None, MATCH_NAME_PROPOSED)
        if len(hits) > 1:
            detail["candidates"] = sorted(hits)
            return done(None, MATCH_NAME_AMBIGUOUS)
        return done(None, MATCH_UNMATCHED)

    return match


def link_proposals(rows, matcher):
    """PURE: the login→employee links a human is asked to approve, one per distinct feed login.

    `rows` are clock-feed rows carrying `feed_login` and `employee_raw`. Returns
    `{proposals, unmatched, bound}`:
      `proposals` one entry per login whose name proposes exactly one employee — the review screen's
                  list, with the row count and hours behind it so the reviewer can see the weight of
                  the decision.
      `unmatched` logins that propose nobody (or more than one). These are REPORTED as a count on
                  the discrepancy board; they are never guessed.
      `bound`     logins that already bind, with the basis, so an approved link is visibly settled.
    """
    proposals, unmatched, bound = {}, {}, {}
    for r in rows or ():
        login = str((r or {}).get("feed_login") or "").strip()
        name = str((r or {}).get("employee_raw") or "").strip()
        eid, basis, detail = matcher(login, name)
        key = _fold_person(login) or person_key(name)
        if not key:
            continue
        hours = float((r or {}).get("hours") or 0)
        if match_is_binding(basis):
            cell = bound.setdefault(key, {"feed_login": login, "employee_id": eid, "basis": basis,
                                          "rows": 0, "hours": 0.0})
        elif basis == MATCH_NAME_PROPOSED:
            cell = proposals.setdefault(key, {
                "feed_login": login, "feed_name": name,
                "proposed_employee_id": detail.get("proposed_employee_id"),
                "proposed_name": detail.get("proposed_name"),
                "basis": basis, "reason": detail["reason"], "rows": 0, "hours": 0.0})
        else:
            cell = unmatched.setdefault(key, {"feed_login": login, "feed_name": name,
                                              "basis": basis, "reason": detail["reason"],
                                              "candidates": detail.get("candidates") or [],
                                              "rows": 0, "hours": 0.0})
        cell["rows"] += 1
        cell["hours"] = round(cell["hours"] + hours, 2)
    return {"proposals": sorted(proposals.values(), key=lambda c: -c["hours"]),
            "unmatched": sorted(unmatched.values(), key=lambda c: -c["hours"]),
            "bound": sorted(bound.values(), key=lambda c: -c["hours"]),
            "totals": {"logins": len(proposals) + len(unmatched) + len(bound),
                       "proposed": len(proposals), "unmatched": len(unmatched),
                       "bound": len(bound),
                       "unmatched_hours": round(sum(c["hours"] for c in unmatched.values()), 2),
                       "proposed_hours": round(sum(c["hours"] for c in proposals.values()), 2)}}
