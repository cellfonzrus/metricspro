"""THE ENDPOINTS for the clock-report variance board, its corrections, and the three gates.

Owner 2026-10-10: *"match with what we have in metrics pro and asses the difference and ask the DM on
every monday and require the correction to be done before the payroll is run, the dm should be
notified as an alert as soon as they log in to fix it and block them from logging in unless they fix
it by tuesday so the payroll can be run on Wednesday."*

WHAT IS HERE AND WHAT IS DELIBERATELY NOT. Every DECISION this surface makes already lives in a pure
module and is dereferenced here, never re-derived:

    which hours do we hold, and on what basis   `storeops/payroll_hours_basis`   (index §14y)
    what did the clock report say               `storeops/clock_feed`            (index §14z)
    what is the difference, and does it block   `storeops/clock_variance`        (index §14z)
    which employee is this login                `storeops/payroll_identity`      (one matcher)
    which store is this spelling                `account/store_identity`         (§13d/§64)
    who owns this store                         `storeops/org_chain`             (§48.7)
    which period is this                        `storeops/router._resolve_range` (one resolver)
    may this caller see pay money               `storeops/pay_visibility`        (mig 434)
    who changed a pay figure                    `storeops/router._log_payroll_change` (one writer)

So this file is I/O and HTTP shape. If a number here disagrees with the payroll report, that is a bug
in ONE place, not two.

THE PERIOD IS THE PAYROLL PERIOD, NOT A SECOND CALENDAR. `_resolve_range` is the same resolver
`GET /storeops/payroll`, `/payroll-by-store`, the payroll tax and the payroll expenses all use, so
"the hours I am being asked to correct" and "the hours payroll will pay" can never be different
windows. That was an explicit requirement and it is honoured by having no period code of its own.

EVERY READ IS ORG-SCOPED AND SPAN-GATED, and every money key is stripped server-side for a caller
below the configured level — the export cannot carry what the screen hides (RULE FOUR). Hours always
render, because a gated caller is exactly the person who must still fix them.

OFF BY DEFAULT. Every endpoint here answers honestly while the feature is off: the board returns its
"not switched on" state rather than an empty list that reads like "nothing wrong", and both gates
return `gated: false`. Nothing fires until the org's own config row says otherwise.

Proof: `backend/harness_clock_variance.py` (the pure decisions) and
`backend/harness_clock_variance_api.py` (this surface). Registered in docs/SYSTEM_DATA_FLOW_INDEX.md
§14z and §17.
"""
from fastapi import APIRouter, Body, Header, HTTPException, Response

from app.core.database import get_supabase
from app.modules.storeops import clock_variance as _cv
from app.modules.storeops import payroll_hours_basis as _hb
# THE ONE punch/correction read on the pay path. This module used to carry it; it now dereferences
# it, so the other four pay paths can read the same rows instead of each inventing a query (§14y).
from app.modules.storeops import payroll_evidence as _payevid
from app.modules.storeops import payroll_identity as _pid
from app.modules.storeops import pay_visibility as _payvis

router = APIRouter()

ORG_ID = "00000000-0000-0000-0000-000000000001"


def sb():
    """SCHEMA-SCOPED to `storeops`, exactly like `storeops/router.sb()`.

    Every table this surface reads or writes is a storeops table, and both config readers
    (`payroll_hours_basis.load_config` / `clock_variance.load_config`) document that they take a
    client the CALLER has already scoped. An unscoped client here reads the `public` schema, finds
    nothing, and the board renders an empty list — which is the absence-is-never-zero defect
    appearing as a silently clean screen. Caught by `harness_clock_variance_api.py` §B.
    """
    return get_supabase().schema("storeops")


def sb_public():
    """The UNSCOPED client, for the few helpers that scope themselves (`pay_visibility`,
    `core.scope`). Named so the difference is deliberate rather than an oversight."""
    return get_supabase()


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# LOADERS. Each read degrades on its own: a missing optional table NARROWS the answer and says so,
# and never 500s a board a DM is being gated into. A read that fails is REPORTED in `blockers`,
# because "we could not look" and "there is nothing wrong" are different answers and the whole point
# of this surface is that they never print the same.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def _rows(table, select, org_id, **eqs):
    """A failed read is REPORTED as {table, reason}, never as one formatted string.

    `reason` is a boundary MESSAGE key (`core.setup_notice.MESSAGE_KEYS`), so the raw database error
    is neutralized for a tenant and kept whole for a platform admin by the one boundary — and `table`
    stays DATA under a non-message key, which the page renders through `sourceLabel()` rather than
    printing the identifier at a district manager who is being gated into this screen."""
    try:
        q = sb().table(table).select(select).eq("org_id", org_id)
        for k, v in eqs.items():
            q = q.eq(k, v)
        return (q.execute().data or []), None
    except Exception as e:
        return [], {"table": table, "reason": str(e)}


def _range_rows(table, select, org_id, col, lo, hi):
    """Half-open [lo, hi) on `col` — the SAME convention `_resolve_range` returns, so a period
    boundary cannot be read one day differently here than by payroll."""
    try:
        q = sb().table(table).select(select).eq("org_id", org_id)
        if lo:
            q = q.gte(col, lo)
        if hi:
            q = q.lt(col, hi)
        return (q.execute().data or []), None
    except Exception as e:
        return [], {"table": table, "reason": str(e)}


def _load(org_id, lo, hi):
    """Everything the comparison reads, for ONE period. Returns (bundle, blockers)."""
    blockers = []

    def take(pair):
        rows, err = pair
        if err:
            blockers.append(err)
        return rows

    emps = take(_rows("employees", "employee_id,name,email,epay_login,epay_salesperson,home_store,"
                      "is_active,pay_basis,pay_rate,pay_from_schedule", org_id))
    shifts = take(_range_rows("shifts", "employee_id,shift_date,scheduled_hours,actual_hours,"
                              "store_code,status", org_id, "shift_date", lo, hi))
    # ★ THE PUNCH READ IS NOT HERE ANY MORE ★ (owner directive 2026-10-12, index §14y). It is
    # `storeops/payroll_evidence.read_pay_evidence` — the ONE punch/correction read on the pay path.
    # It used to live here, and the four other pay paths (the P&L wage line, labour coverage, PTO
    # accrual, HR total comp) therefore had NO way to get the same rows and reached the one home
    # with no evidence at all, which on a clocked-only org resolves every day to ZERO. Factoring it
    # out is what let them dereference it instead of growing a fifth query each. The column lists,
    # the half-open [lo, hi) range and the per-table degradation are the ones this function used, so
    # the board reads byte-identically.
    _pay_rows, _pay_blockers = _payevid.read_pay_evidence(sb(), org_id, lo, hi)
    blockers.extend(_pay_blockers)
    punches, manual = _pay_rows["punches"], _pay_rows["manual"]
    feed = take(_range_rows("raw_clock_punch", "employee_raw,feed_login,store_code,store_raw,"
                            "business_date,hours,clock_in,clock_out,excluded",
                            org_id, "business_date", lo, hi))
    links = take(_rows("clock_feed_login_link", "feed_login,employee_id,status", org_id))
    settled = take(_range_rows("clock_variance_correction",
                               "store_code,employee_id,business_date,resolution,override_hours,"
                               "override_reason,override_by,note",
                               org_id, "business_date", lo, hi))
    return {"employees": emps, "shifts": shifts, "punches": punches, "manual": manual,
            "feed": feed, "links": links, "settled": settled}, blockers


def _store_of(shifts, punches):
    """`{(employee_id, date): store_code}` for OUR rows — a punch's own store first, else the shift's.

    The punch wins because the punch is where the person actually was; the shift is where they were
    expected. On a borrowed day those differ, and the labour cost belongs where the work happened."""
    out = {}
    for s in shifts or ():
        k = ((s or {}).get("employee_id"), str((s or {}).get("shift_date") or "")[:10])
        if k[1] and (s or {}).get("store_code"):
            out.setdefault(k, str(s["store_code"]).strip())
    for t in punches or ():
        k = ((t or {}).get("employee_id"), str((t or {}).get("work_date") or "")[:10])
        if k[1] and (t or {}).get("store_code"):
            out[k] = str(t["store_code"]).strip()
    return out


def _expected_store_days(shifts, punches):
    """`{store_code: {dates}}` we EXPECTED a clock file for — the store-days we ourselves have a
    schedule or a punch for. INJECTED into `feed_day_coverage` rather than decided inside it: which
    days a store should have sent a file is a scheduling question, and scheduling has its own home."""
    out = {}
    for r in list(shifts or ()) + list(punches or ()):
        r = r or {}
        code = str(r.get("store_code") or "").strip()
        day = str(r.get("shift_date") or r.get("work_date") or "")[:10]
        if code and day:
            out.setdefault(code, set()).add(day)
    return out


def _overrides(settled):
    """The DM's per-row overrides, in the shape `build_evidence` takes. Only a row that carries an
    override hour is one; a plain `accepted_ours` decision is not an hours edit and must not read as
    one."""
    out = []
    for r in settled or ():
        r = r or {}
        if r.get("override_hours") is None:
            continue
        out.append({"employee_id": r.get("employee_id"),
                    "work_date": str(r.get("business_date") or "")[:10],
                    "hours": r.get("override_hours"), "reason": r.get("override_reason"),
                    "by": r.get("override_by")})
    return out


def _settled_keys(settled):
    """The (employee, store, day) keys a human has already settled WITHOUT an override — those rows
    are reported as resolved and stop blocking. An overridden row is not in this set because its
    override already changed the hours, so there is nothing left to disagree about."""
    return {((r or {}).get("employee_id"), str((r or {}).get("store_code") or "").strip(),
             str((r or {}).get("business_date") or "")[:10])
            for r in (settled or ()) if (r or {}).get("resolution")}


def _assemble(org_id, lo, hi):
    """The whole answer for one period, from the pure modules. No decision is made here."""
    bundle, blockers = _load(org_id, lo, hi)
    basis_cfg = _hb.load_config(sb(), org_id)
    var_cfg = _cv.load_config(sb(), org_id)

    matcher = _pid.build_employee_matcher(bundle["employees"], link_rows=bundle["links"])
    # The feed's PAYABLE rows only: an excluded row (a back-office login, an unpayable type, a store
    # we could not place) is already reported by the feed itself and must not also read as a punch.
    payable = [r for r in bundle["feed"] if not (r or {}).get("excluded")]
    feed_hours, unmatched = _cv.feed_day_hours(payable, matcher)

    evidence = _hb.build_evidence(shifts=bundle["shifts"], punches=bundle["punches"],
                                  manual_rows=bundle["manual"],
                                  feed_punches=payable if basis_cfg.get("clock_feed_is_pay_source") else None,
                                  overrides=_overrides(bundle["settled"]))
    arrived = {}
    for r in payable:
        code = str((r or {}).get("store_code") or "").strip()
        day = str((r or {}).get("business_date") or "")[:10]
        if code and day:
            arrived.setdefault(code, set()).add(day)
    gaps = _cv.feed_day_coverage(arrived, _expected_store_days(bundle["shifts"], bundle["punches"]),
                                 var_cfg)

    out = _cv.compare(feed_hours, evidence, basis_cfg, var_cfg,
                      employee_rows=bundle["employees"],
                      store_of=_store_of(bundle["shifts"], bundle["punches"]),
                      resolved=_settled_keys(bundle["settled"]),
                      extra_rows=list(unmatched) + list(gaps))
    out["blockers"] = blockers
    out["bundle"] = bundle
    out["basis_config"] = basis_cfg
    out["variance_config"] = var_cfg
    return out


def _name_map(emps):
    return {str((e or {}).get("employee_id") or "").strip(): (e or {}).get("name")
            for e in (emps or ())}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE BOARD
# ══════════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/payroll/clock-variances")
def clock_variances(month: str = None, start: str = None, end: str = None,
                    markets: str = None, stores: str = None, reps: str = None,
                    authorization: str = Header(default=""), org_id: str = ORG_ID,
                    response: Response = None):
    """The variance board the DM works, for the SAME period payroll pays.

    Standard filters (`markets` / `stores` / `reps`) and the standard export options ride the
    platform's existing filter + notify/report registry — this endpoint carries no exporter of its
    own (RULE FIVE §3d).

    EVERY ROW CARRIES BOTH NUMBERS AND OUR BASIS, with the sentence explaining it. A board that shows
    a difference without saying where our side came from asks the DM to arbitrate between two
    unexplained figures.

    WHEN THE FEED IS OFF this returns `enabled: false` and an empty row list — stated, not implied.
    An empty board that cannot say whether it looked is the absence-is-never-zero defect.
    """
    from app.modules.storeops.router import _resolve_range, gate_person_rows
    lo, hi = _resolve_range(month, start, end)
    res = _assemble(org_id, lo, hi)
    cfg = res["variance_config"]
    names = _name_map(res["bundle"]["employees"])

    rows = res["rows"]
    for r in rows:
        r["employee_name"] = names.get(str(r.get("employee_id") or "").strip()) or r.get("employee_raw")
    rows = gate_person_rows(rows, authorization, org_id, people_fields=("employee_id",),
                            store_fields=("store_code",), since=lo, until=hi)
    rows = _apply_filters(rows, markets=markets, stores=stores, reps=reps, org_id=org_id)

    allowed = _payvis.can_see_pay(authorization or "", org_id, client=sb_public())
    if not allowed:
        # Hours are NOT money and always render — a gated DM is exactly who must fix them. What is
        # stripped is any priced figure a row carries.
        _payvis.strip_pay(rows)
    if response is not None:
        try:
            response.headers["X-Can-See-Pay-Rates"] = "true" if allowed else "false"
        except Exception:
            pass

    stores_seen = sorted({str(r.get("store_code") or "") for r in rows if r.get("store_code")})
    return {
        "enabled": bool(cfg["clock_feed_enabled"]),
        "is_pay_source": bool(res["basis_config"].get("clock_feed_is_pay_source")),
        "clock_source_rule": res["basis_config"].get("clock_source_rule"),
        "clock_source_rule_reason": _hb.CLOCK_RULE_REASONS.get(
            res["basis_config"].get("clock_source_rule")),
        "agreement_tolerance_hours": res["basis_config"].get("clock_agreement_tolerance_hours"),
        "tolerance_hours": cfg["variance_tolerance_hours"],
        "period": {"start": lo, "end": hi},
        "weekday_plan": _cv.weekday_plan(cfg),
        "rows": rows,
        "by_store": res["by_store"],
        "all_clear": _cv.store_all_clear(res["by_store"], stores_seen),
        "totals": res["totals"],
        "blocking_count": len([r for r in rows if r.get("blocks")]),
        "kinds": {k: _cv.kind_reason(k) for k in _cv.KINDS},
        "correction_path": _cv.CORRECTION_PATH,
        "blockers": res["blockers"],
        "not_enabled_reason": (
            None if cfg["clock_feed_enabled"] else
            "This organisation has not switched the clock-report import on, so there is nothing to "
            "compare against. No file is being read and no gate can fire."),
    }


def _apply_filters(rows, *, markets=None, stores=None, reps=None, org_id=ORG_ID):
    """The platform's STANDARD filters, applied through the platform's own market resolver — there is
    no market vocabulary read here (§13a)."""
    def _set(v):
        return {x.strip() for x in str(v or "").split(",") if x.strip()}

    want_stores, want_reps, want_markets = _set(stores), _set(reps), _set(markets)
    if want_stores:
        rows = [r for r in rows if str(r.get("store_code") or "") in want_stores]
    if want_reps:
        rows = [r for r in rows if str(r.get("employee_id") or "") in want_reps]
    if want_markets:
        try:
            from app.core import scope as _cscope
            mkt = _cscope.market_by_code(sb_public(), org_id) or {}
        except Exception:
            mkt = {}
        rows = [r for r in rows
                if (mkt.get(str(r.get("store_code") or "").upper()) or "") in want_markets]
    return rows


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# ONE HUMAN DECISION ABOUT ONE ROW — including the DM's override
# ══════════════════════════════════════════════════════════════════════════════════════════════════
@router.post("/payroll/clock-variances/resolve")
def resolve_variance(body: dict = Body(...), authorization: str = Header(default=""),
                     org_id: str = ORG_ID):
    """Record what a human decided about ONE (store, employee, day).

    `resolution`: `corrected` (the hours were fixed through the mechanism that owns them) /
    `accepted_ours` / `accepted_feed`. Optional `override_hours` + `override_reason` is the DM's own
    figure for the day, which OUT-RANKS the org's declared clock-source rule (owner ruling
    2026-10-10) and is audited.

    THE REASON IS NOT OPTIONAL ON AN OVERRIDE, and it is refused in two places on purpose: here, so
    the caller gets a sentence they can act on, and by a CHECK constraint in the schema, so no other
    writer — a script, a hand-written UPDATE, a future endpoint — can land a bare number on a pay
    row. A number on somebody's pay with no explanation is the thing this whole package exists to
    stop.
    """
    from app.modules.storeops.router import _require_manager, _log_payroll_change, _who_for_log
    mgr = _require_manager(authorization, org_id)
    org_id = (mgr.get("org_id") or "").strip() or org_id

    store_code = str(body.get("store_code") or "").strip()
    employee_id = str(body.get("employee_id") or "").strip()
    business_date = str(body.get("business_date") or "")[:10]
    resolution = str(body.get("resolution") or "").strip()
    kind = str(body.get("variance_kind") or "").strip()
    if not (store_code and employee_id and business_date):
        raise HTTPException(400, "store_code, employee_id and business_date are required")
    if resolution not in ("corrected", "accepted_ours", "accepted_feed"):
        raise HTTPException(400, "resolution must be corrected, accepted_ours or accepted_feed")

    ov_hours = body.get("override_hours")
    ov_reason = str(body.get("override_reason") or "").strip()
    if ov_hours not in (None, ""):
        try:
            ov_hours = float(ov_hours)
        except (TypeError, ValueError):
            raise HTTPException(400, "override_hours must be a number of hours")
        if ov_hours < 0 or ov_hours > 24:
            raise HTTPException(400, "override_hours must be between 0 and 24")
        if not ov_reason:
            raise HTTPException(
                400, "An override needs a reason. This figure becomes somebody's pay and the reason "
                     "travels with it onto the report and into the payroll change log.")
    else:
        ov_hours = None

    # The PERIOD the correction belongs to, from the one resolver — not a window of its own.
    from app.modules.storeops.router import _resolve_range
    p_lo, p_hi = _resolve_range(body.get("month"), body.get("start"), body.get("end"))

    row = {"org_id": org_id, "store_code": store_code, "employee_id": employee_id,
           "business_date": business_date, "variance_kind": kind or "hours_differ",
           "resolution": resolution,
           "period_start": p_lo or business_date, "period_end": p_hi or business_date,
           "feed_hours": body.get("feed_hours"), "our_hours": body.get("our_hours"),
           "note": str(body.get("note") or "").strip() or None,
           "corrected_by": mgr.get("email")}
    if ov_hours is not None:
        row.update({"override_hours": ov_hours, "override_reason": ov_reason,
                    "override_by": mgr.get("email")})
        from datetime import datetime, timezone
        row["override_at"] = datetime.now(timezone.utc).isoformat()

    try:
        saved = (sb().table("clock_variance_correction")
                 .upsert(row, on_conflict="org_id,period_start,period_end,store_code,"
                                          "employee_id,business_date")
                 .execute().data or [row])[0]
    except Exception as e:
        raise HTTPException(400, f"The decision could not be saved: {e}")

    # AUDIT THROUGH THE ONE WRITER. `_log_payroll_change` resolves who the row is about from the
    # STORED employee and mints a missing business id (§19.45) — a second writer cannot do that,
    # which is why there is no database trigger here.
    if ov_hours is not None:
        try:
            emp = (sb().table("employees").select("*").eq("org_id", org_id)
                   .eq("employee_id", employee_id).limit(1).execute().data or [None])[0]
            _log_payroll_change(
                org_id, field=_cv.OVERRIDE_FIELD, entry_point=_cv.OVERRIDE_ENTRY_POINT,
                employee_id=employee_id, employee_row=emp, store_code=store_code,
                work_date=business_date, before=body.get("our_hours"), after=ov_hours,
                source_table="clock_variance_correction", source_id=saved.get("id"),
                who=_who_for_log(authorization, org_id), reason=ov_reason)
        except Exception:
            pass
    return saved


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# WHICH LOGIN IS WHICH EMPLOYEE — proposals a human approves
# ══════════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/payroll/clock-link-proposals")
def clock_link_proposals(month: str = None, start: str = None, end: str = None,
                         authorization: str = Header(default=""), org_id: str = ORG_ID):
    """Every B2B login the period carried, and who it binds to — or who it might be.

    A NAME IS NEVER AN ATTRIBUTION. A login the roster does not carry is reported with its hours and
    with a PROPOSAL when exactly one roster name matches, for a human to approve. Until they do, the
    hours are not attributed to anybody and the row blocks. That is the opposite of convenient and it
    is the only honest shape: the alternative is paying somebody else's hours to the person whose
    surname happened to match.
    """
    from app.modules.storeops.router import _resolve_range
    lo, hi = _resolve_range(month, start, end)
    bundle, blockers = _load(org_id, lo, hi)
    matcher = _pid.build_employee_matcher(bundle["employees"], link_rows=bundle["links"])
    payable = [r for r in bundle["feed"] if not (r or {}).get("excluded")]
    out = _pid.link_proposals(payable, matcher)
    out["blockers"] = blockers
    out["period"] = {"start": lo, "end": hi}
    out["reasons"] = {b: _pid.match_reason(b) for b in _pid.ALL_MATCH_BASES}
    return out


@router.post("/payroll/clock-link-proposals/decide")
def decide_clock_link(body: dict = Body(...), authorization: str = Header(default=""),
                      org_id: str = ORG_ID):
    """Approve or reject ONE login→employee link. `status`: `approved` / `rejected`.

    ONLY `approved` BINDS. `rejected` is a human saying "not this person", and it is stored so the
    same proposal is never put in front of them again as though nobody had looked — a board that
    re-asks a settled question stops being read.
    """
    from app.modules.storeops.router import _require_manager
    mgr = _require_manager(authorization, org_id)
    org_id = (mgr.get("org_id") or "").strip() or org_id
    feed_login = str(body.get("feed_login") or "").strip()
    employee_id = str(body.get("employee_id") or "").strip()
    status = str(body.get("status") or "").strip().lower()
    if not feed_login or not employee_id:
        raise HTTPException(400, "feed_login and employee_id are required")
    if status not in ("approved", "rejected"):
        raise HTTPException(400, "status must be approved or rejected")
    from datetime import datetime, timezone
    row = {"org_id": org_id, "feed_login": feed_login, "employee_id": employee_id,
           "status": status,
           "proposed_name": str(body.get("proposed_name") or "").strip() or None,
           "note": str(body.get("note") or "").strip() or None}
    if status == "approved":
        row["approved_by"] = mgr.get("email")
        row["approved_at"] = datetime.now(timezone.utc).isoformat()
    else:
        # A REJECTION IS ALSO A DECISION BY A NAMED PERSON. It goes in `note` rather than inventing a
        # `rejected_by` column, because the question "who last decided about this login" is answered
        # by the row's own audit trail and a second pair of who/when columns would be a second
        # answer to it.
        row["note"] = ((row.get("note") + " | ") if row.get("note") else "") + \
            f"rejected by {mgr.get('email') or 'a manager'}"

    # READ-THEN-WRITE rather than upsert, ON PURPOSE. The unique index is FUNCTIONAL
    # (`lower(feed_login)`), because 'gsantos' and 'GSantos' are one login — and PostgREST's
    # `on_conflict` can only name plain columns, so an upsert would silently insert a duplicate that
    # the index then rejects with a 409 nobody can act on. Two rows claiming one login is also
    # exactly the ambiguity `payroll_identity` treats as binding nothing, so a duplicate here would
    # quietly un-approve an approved link.
    try:
        existing = (sb().table("clock_feed_login_link")
                    .select("id,status").eq("org_id", org_id).eq("employee_id", employee_id)
                    .ilike("feed_login", feed_login).limit(1).execute().data) or []
        if existing:
            return (sb().table("clock_feed_login_link")
                    .update(row).eq("id", existing[0]["id"]).execute().data or [row])[0]
        return (sb().table("clock_feed_login_link")
                .insert(row).execute().data or [row])[0]
    except Exception as e:
        raise HTTPException(400, f"The link decision could not be saved: {e}")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE SIGN-IN GATE — ★ NEVER A REFUSED SIGN-IN ★
# ══════════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/payroll/clock-gate")
def clock_gate(authorization: str = Header(default=""), org_id: str = ORG_ID):
    """What this signing-in manager should be shown. Called by the app shell after sign-in.

    ★ NO LOCKOUT ★ (index §22). The owner asked to *"block them from logging in"*; this confines the
    SESSION to the correction board instead, and the reason is not squeamishness: a person who cannot
    sign in cannot fix what blocks them, so a refused sign-in would make the Wednesday payroll
    impossible rather than protected. The sign-in always succeeds; `allowed_paths` is what the shell
    permits until the blocking rows are gone.

    It returns `alert: true` on ANY weekday as soon as something is uncorrected — the owner's *"should
    be notified as an alert as soon as they log in"* — and `gated: true` only from the org's gate
    weekday, only for a role the org gates, and only while something still blocks.
    """
    from app.modules.storeops.router import _who_for_log, _resolve_range
    from datetime import date as _date
    who = _who_for_log(authorization, org_id) or {}
    org_id = (who.get("org_id") or "").strip() or org_id
    cfg = _cv.load_config(sb(), org_id)
    today = _date.today()

    if not cfg["login_gate_enabled"]:
        # ANSWERED HONESTLY WHILE OFF. Returning a bare `false` with no reason would make a
        # switched-off gate indistinguishable from a clean board.
        return {"alert": False, "gated": False, "blocking_count": 0,
                "weekday_plan": _cv.weekday_plan(cfg),
                "allowed_paths": list(_cv.ALLOWED_PATH_PREFIXES),
                "correction_path": _cv.CORRECTION_PATH,
                "reason": "This organisation has not switched the clock-variance login gate on."}

    # The CURRENT payroll period, from the one resolver, so the gate is about the hours payroll is
    # about to pay and not about some other window.
    lo, hi = _resolve_range(today.strftime("%Y-%m"), None, None)
    try:
        res = _assemble(org_id, lo, hi)
        blocking = [r for r in res["rows"] if r.get("blocks")]
        # Their OWN stores only: a DM is not gated by another district's unfixed rows.
        from app.modules.storeops.router import gate_person_rows
        blocking = gate_person_rows(blocking, authorization, org_id,
                                    people_fields=("employee_id",), store_fields=("store_code",),
                                    since=lo, until=hi)
        count = len(blocking)
    except Exception as e:
        # A GATE THAT CANNOT LOOK DOES NOT GATE. Failing closed here would lock a DM out of the app
        # over a database hiccup, which is the ★ NO LOCKOUT ★ property failing in the direction it
        # exists to prevent. The inability is REPORTED instead.
        return {"alert": False, "gated": False, "blocking_count": 0,
                "weekday_plan": _cv.weekday_plan(cfg),
                "allowed_paths": list(_cv.ALLOWED_PATH_PREFIXES),
                "correction_path": _cv.CORRECTION_PATH,
                "reason": f"The clock-variance board could not be read, so no gate is applied: {e}"}

    return _cv.gate_decision(weekday=today.weekday(), role=who.get("role"),
                             login=who.get("email"), blocking_count=count, cfg=cfg,
                             business_date=today.isoformat())


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE PAYROLL RUN PREFLIGHT
# ══════════════════════════════════════════════════════════════════════════════════════════════════
@router.get("/payroll/run-preflight")
def run_preflight(month: str = None, start: str = None, end: str = None,
                  authorization: str = Header(default=""), org_id: str = ORG_ID):
    """May payroll be run for this period? Refuses and NAMES what blocks it.

    TWO refusals, both of them "report the defect, never hide it":
      1. uncorrected clock variances — only when the org switched that gate on; and
      2. employees this run would price at NOTHING because they carry no `pay_rate`. That one is
         reported WHETHER OR NOT the clock feed is on, because a payroll that pays somebody $0.00
         because a column is NULL is wrong either way. Measured 2026-10-10: 13 of 46 active hourly
         house employees. It is reported, never repaired in code.

    A refusal that does not say which rows is a wall, not a gate — so `blocking` and
    `employees_without_rate` both travel with the verdict.
    """
    from app.modules.storeops.router import _resolve_range
    lo, hi = _resolve_range(month, start, end)
    res = _assemble(org_id, lo, hi)
    cfg = res["variance_config"]
    blocking = [r for r in res["rows"] if r.get("blocks")]
    paid = {str((s or {}).get("employee_id") or "").strip()
            for s in res["bundle"]["shifts"] + res["bundle"]["punches"]
            if (s or {}).get("employee_id")} or None
    verdict = _cv.payroll_run_gate(blocking=blocking, cfg=cfg,
                                   employee_rows=res["bundle"]["employees"],
                                   paid_employee_ids=paid)
    verdict["period"] = {"start": lo, "end": hi}
    verdict["weekday_plan"] = _cv.weekday_plan(cfg)
    verdict["blockers"] = res["blockers"]
    # THE COVERAGE INTERLOCK rides the same preflight, so "may we run" is ONE question with one
    # answer rather than two screens that can disagree.
    cov = _hb.coverage(_hb.build_evidence(shifts=res["bundle"]["shifts"],
                                          punches=res["bundle"]["punches"],
                                          manual_rows=res["bundle"]["manual"]),
                       res["basis_config"], employee_rows=res["bundle"]["employees"])
    verdict["coverage"] = cov
    verdict["interlock"] = _hb.interlock(cov, res["basis_config"])
    if not verdict["interlock"].get("ok"):
        verdict["refused"] = True
        verdict["reason"] = (verdict.get("reason", "") + "  "
                             + str(verdict["interlock"].get("reason") or "")).strip()
    return verdict


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE MONDAY ASK (owner: *"ask the DM on every monday and require the correction to be done before
# the payroll is run"*)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
@router.post("/payroll/clock-variance-ask")
async def clock_variance_ask(month: str = None, start: str = None, end: str = None,
                             dry_run: bool = True, respect_weekday: bool = True,
                             org_id: str = ORG_ID):
    """Carry the weekly ask to whoever owns each store with something still blocking.

    NOTHING ABOUT DELIVERY IS DECIDED HERE. The recipients come from `clock_variance.ask_plan` over
    `storeops/org_chain.dm_by_store` (THE org walk, §48.7), the bodies from
    `clock_variance.build_ask_digest`, and the sending, the per-channel dedup and the send record
    from `notify/digest_delivery.deliver_digests` + `storeops/alert_log.py` (§15.1/§15.2). There is
    no exporter, no mail call and no second alert table in this endpoint — that is the whole point of
    routing it through the existing ladder.

    `dry_run` DEFAULTS TO TRUE, and `variance_ask_enabled` defaults to off, so the first thing a new
    caller (or a cron line pasted early) does is describe what it would send and send nothing.

    `respect_weekday=False` is the manual "send it now" the owner will want on a week when Monday was
    a holiday. It overrides only the WEEKDAY, never the enable switch: a switched-off ask stays off.

    THE STORES NOBODY OWNS ARE RETURNED, NOT DROPPED. Measured 2026-10-10: 6 of 29 Cellfonz stores
    and 0 of 20 Luxelink stores resolve a district manager, so an ask keyed on DMs alone would reach
    eight stores out of fifty-one and look like it had asked everybody.
    """
    from app.modules.storeops.router import _resolve_range, org_chain_inputs
    from app.modules.storeops import org_chain as _org_chain
    from app.modules.notify import digest_delivery as _delivery
    from app.modules.notify.channels import email_resend

    lo, hi = _resolve_range(month, start, end)
    res = _assemble(org_id, lo, hi)
    cfg = res["variance_config"]
    plan_days = _cv.weekday_plan(cfg)

    # The ask weekday is read in the BUSINESS timezone, not the server's. A sweep that runs at
    # 00:30 UTC on Monday is still Sunday evening in the store, and an ask keyed on UTC would fire a
    # day early every week — the same trap the feed's own wall-clock conversion exists for.
    from app.modules.storeops.router import _biz_tz_for
    import datetime as _dt
    try:
        tz = _biz_tz_for(org_id)
        today = _dt.datetime.now(tz).date()
    except Exception:
        today = _dt.date.today()
    weekday = today.weekday()

    out = {"org_id": org_id, "period": {"start": lo, "end": hi}, "weekday_plan": plan_days,
           "business_date": str(today), "weekday": weekday, "dry_run": bool(dry_run),
           "enabled": bool(cfg["variance_ask_enabled"]),
           "feed_enabled": bool(cfg["clock_feed_enabled"]), "blockers": res["blockers"]}

    if not cfg["variance_ask_enabled"]:
        out["sent"] = out["skipped"] = 0
        out["reason"] = ("This organisation has the weekly clock-variance ask switched off, so "
                         "nobody was asked. Nothing was read and nothing was sent.")
        return out
    if respect_weekday and not _cv.is_ask_day(weekday, cfg):
        out["sent"] = out["skipped"] = 0
        out["reason"] = (f"Today is weekday {weekday} and this organisation asks on weekday "
                         f"{plan_days['ask']}. Nothing was sent; pass respect_weekday=false to ask "
                         f"anyway.")
        return out

    chains = _org_chain.dm_by_store(**org_chain_inputs(org_id))
    codes = sorted(set(list(res["by_store"].keys()) + list(chains.keys())))
    plan = _cv.ask_plan(res["by_store"], chains, store_codes=codes)
    digests = _cv.ask_digests(plan, org_id=org_id, period_start=lo, period_end=hi)

    def _build(name, items, _p=plan_days, _lo=lo, _hi=hi):
        return _cv.build_ask_digest(name, items, period_start=_lo, period_end=_hi,
                                    gate_weekday=_p["gate"], run_weekday=_p["run"],
                                    link=_cv.CORRECTION_PATH)

    sent, skipped, planned = await _delivery.deliver_digests(
        sb(), org_id, _cv.ASK_SCOPE, digests, build=_build,
        wa_filename="clock-variance-corrections.txt",
        channels_ok={"email": email_resend.is_configured(), "whatsapp": False},
        dry_run=bool(dry_run),
        preview_item=lambda i: {"store_code": i.get("store_code"),
                                "blocking": i.get("blocking"), "level": i.get("level")},
        detail={"period_start": lo, "period_end": hi, "why": _cv.ASK_WHY})

    out.update({"sent": sent, "skipped": skipped, "recipients": planned,
                "coverage": plan["coverage"], "unowned_stores": plan["unowned_stores"],
                "email_configured": email_resend.is_configured(),
                "why": _cv.ASK_WHY,
                "reason": (f"{len(digests)} recipient(s) own stores with something still blocking; "
                           f"{len(plan['unowned_stores'])} store(s) resolve nobody at all and are "
                           f"listed rather than dropped.")})
    return out


@router.post("/payroll/clock-variance-ask/run-due")
async def clock_variance_ask_run_due(x_notify_secret: str = Header(default=""),
                                     dry_run: bool = False):
    """pg_cron entrypoint for the weekly ask — the SAME `NOTIFY_RUN_SECRET` guard every other
    run-due sweep in this estate uses (`notify/router.run_due`, `hr/letters.late_checkin_run_due`).

    ONE JOB SERVES EVERY TENANT IN EVERY TIMEZONE. Schedule it hourly; the endpoint asks each org
    whether TODAY, in THAT org's own business timezone, is its ask weekday, and does nothing for the
    rest of the week. A daily job pinned to a server weekday would fire on Sunday evening for a
    store in a behind-UTC timezone — the trap `_biz_tz_for` exists for.

    IT IS IDEMPOTENT. A second poke in the same hour re-asks nobody: the per-(recipient, store,
    period) dedup lives in `storeops.alert_log` through the one ladder, not in a cursor here.

    A tenant that has not switched the ask on is skipped and SAID so, because "no tenant is
    configured" and "every tenant is square" are different facts and a sweep that conflates them is
    the absence-is-never-zero defect wearing a cron hat.
    """
    from app.core.run_secret import verify_notify_secret
    if not verify_notify_secret(x_notify_secret):
        raise HTTPException(403, "forbidden")
    try:
        tenants = sb().table("tenants").select("org_id").execute().data or []
    except Exception as e:
        # pg_cron can fire before migration 1073 is applied. Degrade honestly — nothing is
        # configured yet, so there is nothing to ask — rather than a bare 500 in a cron log.
        return {"tenants_checked": 0, "results": [],
                "note": f"could not read tenants — is migration 1073 applied? {str(e)[:200]}"}
    results = []
    for t in tenants:
        oid = (t or {}).get("org_id")
        if not oid:
            continue
        try:
            results.append(await clock_variance_ask(dry_run=bool(dry_run), org_id=oid))
        except Exception as e:
            results.append({"org_id": oid, "error": str(e)[:300]})
    return {"tenants_checked": len(results),
            "asked": sum(int((r or {}).get("sent") or 0) for r in results),
            "results": results}
