"""Bill Payment Pickup & Deposit — PURE logic (owner directive 2026-09-02; mig 942).

The owner's words, verbatim: "for the cash pick up we need one more pick up for the bill payment
pickup and deposit menu, just under the cash pick up module, the same process same wiring as the
cash pick up." Same-day follow-up (verbatim): "in daily closing menu where the employee submit the
cash - it should be recorded as Total cash in store including Bill Payments ... the system should
also check with the bill payment received as per the POS reports for the day if they are not
matching then it should show, for the management it should show what has been received as per the
system in both cash pick up, epay pick up and the cash declared and the epay declared fields and
the credit fields of what has been recorded by the POS reports ... the employee is gated out of
it, dm is gated out of it only market manager and above see it."

THE MONEY MODEL (evidence, not assumption — LuxeLink live 854f6d7b-…, 2026-09-02):
  • The declared cash total (daily_closing.t_cash) INCLUDES the ePay-on-cash dollars:
    epay_on_cash is a SUBSET breakdown of t_cash (231 live rows with epay_on_cash>0, 177 with
    epay_on_cash ≤ t_cash; the 54 exceptions are exactly the mig-939 coverage-recon defect
    class), deposit_recon.cash_for_basis defines store_cash = t_cash − epay_on_cash, and the
    owner confirmed it verbatim ("Total cash in store including Bill Payments").
  • The GENERAL cash-pickup envelope sweeps the FULL declared cash (mig 034: amount =
    store_cash + epay_cash snapshot). So by default a billpay pickup is the TRACKING record of
    the bill-pay side leaving the store (the physical counterpart of the mig-939 remittance /
    coverage recon) and must NOT also relieve the general cash-on-hand movement
    (closing.router._cash_position_core) — that would relieve the same physical dollars twice.
  • The per-org knob `cash_pickup_config.billpay_relieves_cash` (mig 942, default false) flips
    this for an org that operates SPLIT envelopes: then billpay pickups fold into the general
    outflows exactly ONCE (`fold_billpay_outflows`), riding the mig-938 verified-basis
    symmetric-outflow rule + zero floor unchanged (outflows key on their envelope's close_date).

Everything here is pure (rows/dicts in, cells/rows out) — proof: backend/harness_billpay_pickup.py
(stdlib only). DB wrappers at the bottom follow the pay_visibility lazy-import fail-closed posture.
"""

# ── shared small coercion (kept local so this module is stdlib-pure for the harness) ────────────
def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


# ── declared bill-pay cash movement ─────────────────────────────────────────────────────────────
def declared_billpay_by_store_day(closing_rows):
    """PURE: daily_closing rows ({store_code, close_date, epay_on_cash}) → the reps' declared
    ePay-on-cash per (store, day): {store_code: {day: amount}}. Blank store → '?' (excluded from
    codes downstream, same convention as _cash_position_core)."""
    out = {}
    for r in closing_rows or []:
        r = r or {}
        code = (str(r.get("store_code") or "").strip()) or "?"
        dday = str(r.get("close_date") or "")[:10]
        if not dday:
            continue
        out.setdefault(code, {}).setdefault(dday, 0.0)
        out[code][dday] = round(out[code][dday] + _f(r.get("epay_on_cash")), 2)
    return out


def apply_billpay_overlay(decl_by_store_day, dm_epay_cash_by_store_day):
    """PURE: the TKT-1030 rule for the bill-pay split — a VERIFIED store-day's dm_epay_cash
    REPLACES that day's rep-summed declared figure (same replace-not-add semantics as
    _cash_position_core's dm_store_cash overlay and billpay_pl.billpay_cells). The overlay map is
    {(store_code, day): dm_epay_cash} carrying only verified days with a non-None value. Mutates
    and returns decl_by_store_day."""
    for (code, dday), v in (dm_epay_cash_by_store_day or {}).items():
        if v is None:
            continue
        days = decl_by_store_day.get(code)
        if days is not None and dday in days:
            days[dday] = round(_f(v), 2)
    return decl_by_store_day


def pickup_totals_by_store_day(pickup_rows):
    """PURE: billpay_pickup rows → ({store: {day: picked amount}}, last_pickup_at {store: ts},
    last_deposited_at {store: ts}). Only picked_up=true rows count as movement (mirror of
    _cash_position_core's cash_pickup read); deposited_at is tracked on every row."""
    picked, last_pu, last_dep = {}, {}, {}
    for r in pickup_rows or []:
        r = r or {}
        code = (str(r.get("store_code") or "").strip()) or "?"
        dday = str(r.get("close_date") or "")[:10]
        if r.get("picked_up"):
            amt = _f(r.get("amount"))
            picked.setdefault(code, {}).setdefault(dday, 0.0)
            picked[code][dday] = round(picked[code][dday] + amt, 2)
            pu = r.get("picked_up_at")
            if pu and str(pu) > str(last_pu.get(code) or ""):
                last_pu[code] = pu
        dep = r.get("deposited_at")
        if dep and str(dep) > str(last_dep.get(code) or ""):
            last_dep[code] = dep
    return picked, last_pu, last_dep


def billpay_position(decl_by_store_day, picked_by_store_day, as_of):
    """PURE: per-store bill-pay-cash position as of `as_of` ('YYYY-MM-DD'):
    declared-to-date − picked-up-to-date = PENDING (bill-pay cash still in the store awaiting
    pickup/remittance). The operational number — like GET /cash-position it is NOT floored
    (a negative pending = more billpay picked up than was ever declared, a real signal the page
    must show honestly). Returns (cells {store: {declared, picked, pending}}, meta)."""
    cutoff = str(as_of or "")[:10]
    cells = {}
    if not cutoff:
        return cells, {"stores": 0, "declared": 0.0, "picked": 0.0, "pending": 0.0}
    for code, days in (decl_by_store_day or {}).items():
        if code == "?":
            continue
        tot = round(sum(a for d, a in (days or {}).items() if str(d)[:10] <= cutoff), 2)
        if tot:
            cells.setdefault(code, {"declared": 0.0, "picked": 0.0})["declared"] = tot
    for code, days in (picked_by_store_day or {}).items():
        if code == "?":
            continue
        tot = round(sum(a for d, a in (days or {}).items() if str(d)[:10] <= cutoff), 2)
        if tot:
            cells.setdefault(code, {"declared": 0.0, "picked": 0.0})["picked"] = tot
    for c in cells.values():
        c["pending"] = round(c["declared"] - c["picked"], 2)
    meta = {"stores": len(cells), "as_of": cutoff,
            "declared": round(sum(c["declared"] for c in cells.values()), 2),
            "picked": round(sum(c["picked"] for c in cells.values()), 2),
            "pending": round(sum(c["pending"] for c in cells.values()), 2)}
    return cells, meta


def fold_billpay_outflows(pick_by_store_day, pickup_by_store_day, billpay_picked_by_store_day,
                          relieves):
    """PURE — THE NO-DOUBLE-COUNT GATE. When `relieves` is False (the house default: the general
    envelope already sweeps the full declared cash, ePay included) this is the IDENTITY — the
    general movement dicts are returned untouched, byte-identical, and billpay pickups live only
    on the bill-pay side. When True (split-envelope org), each billpay pickup folds into BOTH
    `pick_by_store_day` (total taken) and `pickup_by_store_day` (the physical-pickup breakdown)
    exactly once, keyed on its envelope's close_date — preserving the _cash_position_core
    invariant pickup_by_store_day + eep_by_store_day == pick_by_store_day at every key, and
    riding the mig-938 verified-day symmetry + zero floor downstream unchanged.
    Mutates and returns (pick_by_store_day, pickup_by_store_day)."""
    if not relieves:
        return pick_by_store_day, pickup_by_store_day
    for code, days in (billpay_picked_by_store_day or {}).items():
        for dday, amt in (days or {}).items():
            a = _f(amt)
            if not a:
                continue
            pick_by_store_day.setdefault(code, {}).setdefault(dday, 0.0)
            pick_by_store_day[code][dday] = round(pick_by_store_day[code][dday] + a, 2)
            pickup_by_store_day.setdefault(code, {}).setdefault(dday, 0.0)
            pickup_by_store_day[code][dday] = round(pickup_by_store_day[code][dday] + a, 2)
    return pick_by_store_day, pickup_by_store_day


# ── POS cross-check (owner: "check with the bill payment received as per the POS reports") ──────
def billpay_pos_mismatch(declared_by_store_day, pos_by_store_day, tolerance=1.0):
    """PURE: reps' DECLARED bill-pay split vs the POS/processor-REPORTED bill payments, per
    (store, day). Both inputs are {(store, day): amount}. delta = declared − pos (positive =
    declared more than POS shows; negative = POS shows bill payments the closing sheet never
    declared). A (store, day) inside tolerance is 'ok'; a day present on only one side still
    compares against 0 (never silently dropped). Returns (rows, summary)."""
    keys = set(declared_by_store_day or {}) | set(pos_by_store_day or {})
    rows, mismatched = [], 0
    for k in sorted(keys):
        st, day = (k if isinstance(k, tuple) and len(k) == 2 else (str(k), ""))
        d = round(_f((declared_by_store_day or {}).get(k)), 2)
        p = round(_f((pos_by_store_day or {}).get(k)), 2)
        delta = round(d - p, 2)
        ok = abs(delta) <= abs(_f(tolerance))
        if not ok:
            mismatched += 1
        rows.append({"store": st, "day": day, "declared": d, "pos": p,
                     "delta": delta, "status": "ok" if ok else "mismatch"})
    summary = {"store_days": len(rows), "mismatched": mismatched,
               "declared": round(sum(r["declared"] for r in rows), 2),
               "pos": round(sum(r["pos"] for r in rows), 2)}
    return rows, summary


# ── management cash-recon gate: "market manager and above" (mig-434 posture, fail-closed) ───────
def resolve_recon_access(caller_role, caller_scope, visible_roles=None):
    """PURE: may (role, scope) open the management cash recon? EXACTLY the pay_visibility
    'manager_up' truth table (the mig-434 owner precedent: market manager and above), with no
    grant channel: scope 'all' passes; else the role must be in the allow-list (the tenant's
    cash_recon_visible_roles, or pay_visibility.DEFAULT_VISIBLE_ROLES when unset — admin /
    master_admin / market_manager / market). Employees, store managers and district managers are
    NOT in the default list ⇒ gated out. Unresolvable role with a narrow scope ⇒ False."""
    from app.modules.storeops.pay_visibility import resolve_pay_access
    return resolve_pay_access("manager_up", caller_role, caller_scope,
                              visible_roles=visible_roles, has_grant=False)


def tenant_recon_roles(org_id, client):
    """storeops.tenants.cash_recon_visible_roles for the org — ADAPTIVE: pre-mig-942 schema,
    missing row, or any read failure resolve to None (the built-in 'market manager and above'
    default). A config problem can only make the screen MORE hidden, never open it."""
    try:
        rows = (client.schema("storeops").table("tenants").select("cash_recon_visible_roles")
                .eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        return None
    raw = rows[0].get("cash_recon_visible_roles") if rows else None
    roles = [str(r) for r in raw if str(r or "").strip()] if isinstance(raw, (list, tuple)) else []
    return roles or None


def can_see_cash_recon(authorization, org_id, client):
    """May this caller open the management one-screen cash recon? Caller resolution is EXACTLY
    pay_visibility.can_see_pay's path (core _uid_from_token + _resolve_caller) and, like it,
    FAILS CLOSED: an unverifiable token, an unresolvable login, or a resolver fault hides the
    screen. Same single platform-parity carve-out: NO token at all while the login master switch
    is OFF (the open app's normal state) is allowed; a PRESENT token never opens on failure.
    Super-admin always passes; everyone else goes through resolve_recon_access."""
    try:
        from app.modules.storeops.pay_visibility import _login_enforced
        auth = authorization if isinstance(authorization, str) else ""
        uid, caller, resolver_broke = None, None, False
        if auth.strip():
            try:
                from app.modules.core.router import _uid_from_token, _resolve_caller
                uid = _uid_from_token(auth)
                if uid:
                    caller = _resolve_caller(client, uid, org_id or None)
            except Exception:
                resolver_broke = True
        if resolver_broke:
            return False
        if caller is None:
            return (uid is None) and (not _login_enforced(client))
        if caller.get("super_admin"):
            return True
        perms = caller.get("perms") or {}
        return resolve_recon_access(caller.get("role"), perms.get("scope"),
                                    tenant_recon_roles(org_id, client))
    except Exception:
        return False


def billpay_relieves_cash(client, org_id):
    """The mig-942 no-double-count knob for this org — ADAPTIVE: pre-942 schema, no config row,
    or any read failure resolve to False (today's behavior: billpay pickups never touch the
    general cash movement). NEVER raises."""
    try:
        rows = (client.schema("commcalc").table("cash_pickup_config")
                .select("billpay_relieves_cash").eq("org_id", org_id).limit(1).execute().data) or []
        return bool(rows and rows[0].get("billpay_relieves_cash"))
    except Exception:
        return False
