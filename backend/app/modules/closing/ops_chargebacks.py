"""Ops-accountability chargebacks — detection + shared read/write helpers (migration 504).

OWNER DIRECTIVE 2026-07-22. Two domain events, both org-scoped, both degrade to a no-op if
migration 504 hasn't run yet (every table read/write here is try/except-guarded):

  • missed_closing     — a store had >=1 storeops.timelog punch on a day but no commcalc.daily_
                          closing row for that (store, day). Charged against the EFFECTIVE closer:
                          the static storeops.store_closer assignee IF they actually worked that
                          store that day, else whoever clocked out last (the "last to leave").
                          applied_to='payroll' — decided at payroll (mod-people's payroll UI).
  • missed_dm_verify   — a daily_closing exists for (store, day) but was never DM-verified
                          (commcalc.daily_closing_verification has no verified=true row). Charged
                          against the store's District Manager, resolved via storeops.router.
                          _dm_for_store (unresolvable DM -> skip, never guess).
                          applied_to='commission' — decided on the DM Verify page (this module).

Both reasons are gated by commcalc.ops_chargeback_policy (org_id, reason): no row, or a row with
enabled=false, means detection runs but inserts NOTHING new (existing pending/posted/waived rows
from before the policy was disabled are left alone and still returned).

Idempotent: before inserting, we check for an existing row on the (org_id, employee_id, store_code,
reason, incident_date) unique key and skip if found — we never blind-upsert, because that would
silently reset an already-decided (posted/waived) row back to 'pending'.

This module is imported by mod-people (punch notices, payroll decide UI, employee dashboard) via
try/except, per the cross-agent contract in docs/handoffs/retail-ops.md. Keep the public function
signatures stable.
"""
from datetime import datetime, timezone, timedelta, date
from app.core.database import get_supabase
from app.core.config import settings

REASONS = [
    {"key": "missed_closing", "label": "Missed closing", "applied_to": "payroll"},
    {"key": "missed_dm_verify", "label": "Missed DM verification", "applied_to": "commission"},
]


def sb():
    return get_supabase()


# ── small local helpers (kept self-contained — no top-level cross-module import, mirrors the
#    closing<->commcalc lazy-import boundary documented in closing/router.py) ─────────────────
def _norm_store(s) -> str:
    return (s or "").strip().upper()


def _name_match(a: str, b: str) -> bool:
    """Loose match between two person names (same rule as closing/router.py's _name_match, kept
    local so this module has zero import-time dependency on router.py — avoids a cycle since
    router.py imports FROM this module)."""
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return a.split()[0] == b.split()[0]


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _biz_today(org_id: str = None) -> date:
    """Business-local 'today' for this tenant. Prefers storeops' per-tenant _biz_tz_for (lazy
    import, matches the timeclock/closing-gate 'today'); falls back to the global BUSINESS_TZ
    setting (closing/router.py's own _biz_today_iso pattern), then bare UTC."""
    tz = None
    try:
        from app.modules.storeops.router import _biz_tz_for
        tz = _biz_tz_for(org_id)
    except Exception:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(settings.BUSINESS_TZ or "America/New_York")
        except Exception:
            tz = timezone.utc
    return datetime.now(timezone.utc).astimezone(tz).date()


# ── policy (ops_chargeback_policy) ──────────────────────────────────────────────────────────
def _known_reasons_in_the_wild(client, org_id: str) -> dict:
    """{reason: applied_to} for every DISTINCT reason that has ever appeared in ops_chargeback for
    this org (the "log of errors" — including a future reason code this module never hard-coded).
    applied_to is best-effort/informational here (the settlement always uses the ROW's own
    applied_to, never the policy's) — first one seen wins. [] / {} on a read failure or unmigrated
    table."""
    try:
        rows = (client.schema("commcalc").table("ops_chargeback").select("reason,applied_to")
                .eq("org_id", org_id).execute().data) or []
    except Exception:
        return {}
    out = {}
    for r in rows:
        rs = r.get("reason")
        if rs and rs not in out and r.get("applied_to"):
            out[rs] = r["applied_to"]
        elif rs:
            out.setdefault(rs, None)
    return out


def get_policy(client, org_id: str) -> list[dict]:
    """The org's chargeback amounts, one row per reason the admin editor should show: the union of
    (a) the REASONS registry (this module's two built-in reasons), (b) any saved policy row, and
    (c) any reason that has ever actually occurred in ops_chargeback ("the log of errors" — so
    management can label/enable/amount ANY reason that's ever fired, including one this module
    never hard-coded). Degrades to just the registry defaults if migration 504 hasn't run."""
    saved = {}
    try:
        rows = (client.schema("commcalc").table("ops_chargeback_policy").select("*")
                .eq("org_id", org_id).execute().data) or []
        saved = {r.get("reason"): r for r in rows if r.get("reason")}
    except Exception:
        pass
    registry = {r["key"]: r for r in REASONS}
    in_the_wild = _known_reasons_in_the_wild(client, org_id)
    all_reasons = sorted(set(registry) | set(saved) | set(in_the_wild))
    out = []
    for reason in all_reasons:
        row = saved.get(reason) or {}
        reg = registry.get(reason)
        out.append({
            "reason": reason,
            "label": row.get("label") or (reg["label"] if reg else reason),
            "applied_to": in_the_wild.get(reason) or (reg["applied_to"] if reg else None),
            "amount": _num(row.get("amount", 0)), "enabled": bool(row.get("enabled", False)),
            "overflow": row.get("overflow") or "payroll",
            "known": reg is not None,   # one of this module's built-in reasons vs. discovered
            "updated_at": row.get("updated_at"),
        })
    return out


def put_policy(client, org_id: str, rows: list[dict]) -> list[dict]:
    """Save {reason, label?, amount, enabled, overflow?} rows. `reason` must be one this org has
    actually seen (registry, an existing policy row, or a reason present in ops_chargeback) — pick-
    don't-type: the admin UI only ever echoes back a reason GET already surfaced, never lets an
    operator type an arbitrary new one."""
    allowed = {r["key"] for r in REASONS} | set(_known_reasons_in_the_wild(client, org_id))
    try:
        pol_rows = (client.schema("commcalc").table("ops_chargeback_policy").select("reason")
                    .eq("org_id", org_id).execute().data) or []
        allowed |= {r.get("reason") for r in pol_rows if r.get("reason")}
    except Exception:
        pass
    for row in (rows or []):
        reason = (row.get("reason") or "").strip()
        if not reason or reason not in allowed:
            continue
        overflow = (row.get("overflow") or "payroll").strip()
        if overflow not in ("payroll", "next_cycle"):
            overflow = "payroll"
        body = {
            "org_id": org_id, "reason": reason,
            "label": (row.get("label") or "").strip() or None,
            "amount": _num(row.get("amount", 0)), "enabled": bool(row.get("enabled", False)),
            "overflow": overflow,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        client.schema("commcalc").table("ops_chargeback_policy") \
            .upsert(body, on_conflict="org_id,reason").execute()
    return get_policy(client, org_id)


def _policy_for(client, org_id: str, reason: str):
    """Enabled policy row for `reason`, or None (no row / disabled / table not migrated —
    detection must insert nothing in all three cases)."""
    try:
        rows = (client.schema("commcalc").table("ops_chargeback_policy").select("*")
                .eq("org_id", org_id).eq("reason", reason).limit(1).execute().data) or []
    except Exception:
        return None
    if not rows or not rows[0].get("enabled"):
        return None
    return rows[0]


# ── ops_chargeback reads ────────────────────────────────────────────────────────────────────
def _cb_exists(client, org_id, employee_id, store_code, reason, incident_date) -> bool:
    """True if a PARENT chargeback already exists on the parent unique key (idempotency guard —
    matches ops_chargeback_parent_uq, WHERE parent_id IS NULL, so this never gets confused by a
    settlement-created overflow child that happens to share the same identifying tuple as its
    parent). On a read failure we conservatively say 'yes' (skip inserting) rather than risk a
    duplicate."""
    try:
        rows = (client.schema("commcalc").table("ops_chargeback").select("id")
                .eq("org_id", org_id).eq("employee_id", employee_id or "")
                .eq("store_code", store_code or "").eq("reason", reason)
                .eq("incident_date", incident_date).is_("parent_id", "null")
                .limit(1).execute().data) or []
        return bool(rows)
    except Exception:
        return True


def list_chargebacks(client, org_id: str, reason: str, employee_id: str = None,
                     statuses=None) -> list[dict]:
    """ops_chargeback rows for a reason, newest incident first. statuses=None -> all statuses;
    else an iterable like ('pending',). Degrades to [] if the table isn't migrated yet."""
    try:
        q = (client.schema("commcalc").table("ops_chargeback").select("*")
             .eq("org_id", org_id).eq("reason", reason))
        if employee_id:
            q = q.eq("employee_id", employee_id)
        if statuses:
            q = q.in_("status", list(statuses))
        return q.order("incident_date", desc=True).execute().data or []
    except Exception:
        return []


def totals(rows: list[dict]) -> dict:
    """{pending, posted, waived} $ sums off a list of chargeback rows — 'to be foregone' is
    pending+posted (posted already reduced the DM's pay; waived is forgiven, excluded)."""
    out = {"pending": 0.0, "posted": 0.0, "waived": 0.0}
    for r in rows:
        st = (r.get("status") or "pending")
        if st in out:
            out[st] += _num(r.get("amount"))
    out["to_be_foregone"] = round(out["pending"] + out["posted"], 2)
    for k in ("pending", "posted", "waived"):
        out[k] = round(out[k], 2)
    return out


def decide_chargeback(client, org_id: str, chargeback_id: str, decision: str, decided_by: str,
                      notes: str = None, reason_filter: str = None) -> dict:
    """Post (deduct) or waive (forgive) one pending chargeback. `decision` is 'posted' or 'waived'.
    posted_ref is derived server-side (never caller-supplied) as the incident's 'YYYY-MM' commission
    period. `reason_filter`, when given, refuses to decide a row of a different reason (keeps the
    DM-verify decide endpoint from also deciding a payroll-side missed_closing row it doesn't own
    the UI for)."""
    if decision not in ("posted", "waived"):
        raise ValueError("decision must be 'posted' or 'waived'")
    rows = (client.schema("commcalc").table("ops_chargeback").select("*")
            .eq("id", chargeback_id).eq("org_id", org_id).limit(1).execute().data) or []
    if not rows:
        raise LookupError("chargeback not found")
    row = rows[0]
    if reason_filter and row.get("reason") != reason_filter:
        raise ValueError(f"not a '{reason_filter}' chargeback")
    upd = {
        "status": decision, "decided_by": decided_by,
        "decided_at": datetime.now(timezone.utc).isoformat(), "notes": notes,
    }
    if decision == "posted":
        upd["posted_ref"] = str(row.get("incident_date") or "")[:7]
    (client.schema("commcalc").table("ops_chargeback").update(upd)
     .eq("id", chargeback_id).eq("org_id", org_id).execute())
    return {**row, **upd}


# ── detection: missed_closing ───────────────────────────────────────────────────────────────
def _store_labels(client, org_id: str) -> dict:
    out = {}
    try:
        for s in (client.schema("storeops").table("stores").select("store_code,address")
                  .eq("org_id", org_id).execute().data) or []:
            sc = _norm_store(s.get("store_code"))
            if sc:
                out[sc] = s.get("address") or s.get("store_code")
    except Exception:
        pass
    return out


def _employee_roster(client, org_id: str) -> list:
    """The org's storeops.employees rows (employee_id, id, name) — fetched ONCE per sweep and
    reused, so a per-store/day loop isn't N+1ing the roster table."""
    try:
        return (client.schema("storeops").table("employees").select("employee_id,id,name")
                .eq("org_id", org_id).execute().data) or []
    except Exception:
        return []


def _resolve_roster(emps: list, employee_id=None, employee_name=None):
    """Best-effort resolve (employee_id, employee_name) to the CANONICAL storeops.employees row —
    'First Last', the exact shape the commission module's rep-pay join needs: it matches
    UPPER(ops_chargeback.employee_name) against rep_commissions.storeops_name (falling back to
    UPPER(login/epay name)); employee_id alone does not drive that match. Tries the id first
    (covers both the business employee_id key and the numeric `.id` a caller might be holding),
    then a loose name match. Falls back to the given values unresolved — never blocks an insert on
    an unmapped employee, just leaves the chargeback less matchable downstream."""
    eid = str(employee_id or "").strip()
    if eid:
        for e in emps:
            if str(e.get("employee_id") or "") == eid or str(e.get("id") or "") == eid:
                if e.get("employee_id") and e.get("name"):
                    return (e["employee_id"], e["name"])
    nm = (employee_name or "").strip()
    if nm:
        for e in emps:
            if e.get("employee_id") and e.get("name") and _name_match(nm, e["name"]):
                return (e["employee_id"], e["name"])
    return (employee_id, employee_name)


def _effective_closer(org_id, closer_row, punches, emps):
    """The static store_closer IF they actually worked (are among the day's punches for this
    store), else the punch with the latest clock-out (last to leave; falls back to clock-in for a
    punch nobody clocked out of, so a forgotten punch never breaks the tie-break). Always returns
    the CANONICAL roster (employee_id, name) via _resolve_roster — the commission-pay join needs
    the roster's 'First Last' name, not a denormalized punch/closer-config snapshot."""
    if closer_row:
        cid = str(closer_row.get("employee_id") or "").strip()
        cname = (closer_row.get("employee_name") or "").strip()
        id_variants = {cid} if cid else set()
        if cid:
            try:
                from app.modules.storeops.router import _emp_id_variants
                variants, _nm = _emp_id_variants(org_id, cid)
                id_variants |= variants
            except Exception:
                pass
        for p in punches:
            pid = str(p.get("employee_id") or "").strip()
            pname = (p.get("employee_name") or "").strip()
            if (cid and pid and pid in id_variants) or (cname and pname and _name_match(cname, pname)):
                return _resolve_roster(emps, closer_row.get("employee_id"), closer_row.get("employee_name") or pname)
    last = max(punches, key=lambda p: p.get("clock_out") or p.get("clock_in") or "")
    return _resolve_roster(emps, last.get("employee_id"), last.get("employee_name"))


def _run_missed_closing_detection(client, org_id: str, lookback_days: int):
    policy = _policy_for(client, org_id, "missed_closing")
    if not policy:
        return
    amount = _num(policy.get("amount"))
    today = _biz_today(org_id)
    days = [(today - timedelta(days=i)).isoformat() for i in range(1, max(lookback_days, 0) + 1)]
    if not days:
        return

    try:
        tl = (client.schema("storeops").table("timelog")
              .select("employee_id,employee_name,store_code,work_date,clock_in,clock_out")
              .eq("org_id", org_id).in_("work_date", days).execute().data) or []
    except Exception as e:
        print(f"missed-closing detection: timelog read failed: {e}")
        return
    worked_by_day = {}
    for t in tl:
        d = str(t.get("work_date") or "")[:10]
        sc = _norm_store(t.get("store_code"))
        if d and sc:
            worked_by_day.setdefault(d, set()).add(sc)
    if not worked_by_day:
        return

    try:
        closings = (client.schema("commcalc").table("daily_closing").select("store_code,close_date")
                    .eq("org_id", org_id).in_("close_date", list(worked_by_day.keys()))
                    .execute().data) or []
    except Exception as e:
        print(f"missed-closing detection: daily_closing read failed: {e}")
        return
    closed = {(str(c.get("close_date") or "")[:10], _norm_store(c.get("store_code"))) for c in closings}

    try:
        closer_rows = (client.schema("storeops").table("store_closer")
                       .select("store_code,employee_id,employee_name")
                       .eq("org_id", org_id).execute().data) or []
    except Exception:
        closer_rows = []
    closer_by_store = {_norm_store(c.get("store_code")): c for c in closer_rows if c.get("store_code")}
    labels = _store_labels(client, org_id)
    emps = _employee_roster(client, org_id)

    for d, stores in worked_by_day.items():
        for sc_norm in stores:
            if (d, sc_norm) in closed:
                continue
            punches = [t for t in tl if str(t.get("work_date") or "")[:10] == d
                      and _norm_store(t.get("store_code")) == sc_norm]
            if not punches:
                continue
            eff_id, eff_name = _effective_closer(org_id, closer_by_store.get(sc_norm), punches, emps)
            store_code = punches[0].get("store_code") or sc_norm
            if _cb_exists(client, org_id, eff_id, store_code, "missed_closing", d):
                continue
            row = {
                "org_id": org_id, "employee_id": (eff_id or ""), "employee_name": eff_name,
                "store_code": store_code, "reason": "missed_closing", "incident_date": d,
                "amount": amount, "status": "pending", "applied_to": "payroll",
            }
            try:
                client.schema("commcalc").table("ops_chargeback").insert(row).execute()
            except Exception as e:
                print(f"missed-closing chargeback insert failed ({store_code}/{d}): {e}")
                continue
            try:
                _write_missed_closing_flag(client, org_id, store_code, d, eff_name, amount,
                                           labels.get(sc_norm))
            except Exception as e:
                print(f"missed-closing flag write failed ({store_code}/{d}): {e}")


def _write_missed_closing_flag(client, org_id, store_code, incident_date, employee_name, amount,
                               store_address=None):
    """One commcalc.flags row per new missed-closing chargeback (mirrors asset's _sync_appeal_flags
    column shape). Only ever called right after a NEW chargeback insert, so this naturally never
    duplicates across repeated sweeps."""
    try:
        y, m, _d = [int(x) for x in str(incident_date)[:10].split("-")]
        period = date(y, m, 1).strftime("%B %Y")
    except Exception:
        period, y, m = "Unknown", None, None
    label = store_address or store_code
    row = {
        "org_id": org_id, "period": period, "period_month": m, "period_year": y,
        "flag_type": "Missed Daily Closing", "source": "missed_closing", "severity": "warning",
        "store_address": label, "amount": amount,
        "description": (f"No daily closing submitted for {label} on {incident_date}"
                        + (f" — effective closer {employee_name}" if employee_name else "")),
    }
    client.schema("commcalc").table("flags").insert(row).execute()


def detect_missed_closings(org_id: str, employee_id: str = None, lookback_days: int = 7) -> list[dict]:
    """Sweep business-local days [today-lookback_days, today-1) for stores that worked (timelog)
    but never closed, charge the effective closer, then return the org's (or one employee's,
    when employee_id is given) still-PENDING missed_closing chargebacks."""
    client = sb()
    try:
        _run_missed_closing_detection(client, org_id, lookback_days)
    except Exception as e:
        print(f"detect_missed_closings sweep failed (non-fatal): {e}")
    return list_chargebacks(client, org_id, "missed_closing", employee_id=employee_id,
                            statuses=("pending",))


# ── detection: missed_dm_verify ─────────────────────────────────────────────────────────────
def _run_missed_dm_verify_detection(client, org_id: str, lookback_days: int):
    policy = _policy_for(client, org_id, "missed_dm_verify")
    if not policy:
        return
    amount = _num(policy.get("amount"))
    today = _biz_today(org_id)
    if lookback_days < 1:
        return
    start = (today - timedelta(days=lookback_days)).isoformat()
    end = (today - timedelta(days=1)).isoformat()

    try:
        closings = (client.schema("commcalc").table("daily_closing").select("store_code,close_date")
                    .eq("org_id", org_id).gte("close_date", start).lte("close_date", end)
                    .execute().data) or []
    except Exception as e:
        print(f"missed-dm-verify detection: daily_closing read failed: {e}")
        return
    pairs = {(str(c.get("close_date") or "")[:10], (c.get("store_code") or "").strip())
             for c in closings if c.get("store_code")}
    if not pairs:
        return
    dates = sorted({d for d, _sc in pairs})

    try:
        vers = (client.schema("commcalc").table("daily_closing_verification")
                .select("store_code,close_date,verified").eq("org_id", org_id)
                .in_("close_date", dates).execute().data) or []
    except Exception as e:
        print(f"missed-dm-verify detection: verification read failed: {e}")
        return
    verified = {(str(v.get("close_date") or "")[:10], (v.get("store_code") or "").strip())
                for v in vers if v.get("verified")}
    emps = _employee_roster(client, org_id)

    for d, sc in sorted(pairs):
        if (d, sc) in verified:
            continue
        try:
            from app.modules.storeops.router import _dm_for_store
            dm_id, _dm_email, dm_name = _dm_for_store(org_id, sc)
        except Exception:
            dm_id, dm_name = None, None
        if not dm_id:
            continue  # unresolvable DM -> skip, never guess
        # Stamp the CANONICAL roster (employee_id, name) — commission's rep-pay join matches
        # UPPER(employee_name), not employee_id alone. _dm_for_store already resolves through
        # storeops.employees itself, but re-resolving here is a cheap extra safety net (e.g. if
        # org_managers.employee_id ever holds the numeric `.id` variant instead of the business key).
        dm_id, dm_name = _resolve_roster(emps, dm_id, dm_name)
        dm_id = str(dm_id)
        if _cb_exists(client, org_id, dm_id, sc, "missed_dm_verify", d):
            continue
        row = {
            "org_id": org_id, "employee_id": dm_id, "employee_name": dm_name,
            "store_code": sc, "reason": "missed_dm_verify", "incident_date": d,
            "amount": amount, "status": "pending", "applied_to": "commission",
        }
        try:
            client.schema("commcalc").table("ops_chargeback").insert(row).execute()
        except Exception as e:
            print(f"missed-dm-verify chargeback insert failed ({sc}/{d}): {e}")


def detect_missed_dm_verifies(org_id: str, lookback_days: int = 14) -> list[dict]:
    """Sweep business-local days [today-lookback_days, today-1] for daily_closing rows that were
    never DM-verified, charge the store's DM's commission, then return EVERY missed_dm_verify
    chargeback for the org (all statuses — the DM Verify page needs pending/posted/waived to badge
    + total each row, not just the still-open ones)."""
    client = sb()
    try:
        _run_missed_dm_verify_detection(client, org_id, lookback_days)
    except Exception as e:
        print(f"detect_missed_dm_verifies sweep failed (non-fatal): {e}")
    return list_chargebacks(client, org_id, "missed_dm_verify", statuses=None)
