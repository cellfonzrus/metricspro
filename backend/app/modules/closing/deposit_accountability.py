"""Deposit accountability + POS-beside-declared — PURE logic (owner directive 2026-09-02;
mig 943).

Two owner asks, verbatim:

  1. "the cash pick up and bill pick up only show what the stores have entered but not what is
     in the system, from the pos report, those numbers should be right next to these numbers
     also to have one quick look" → `pos_next_to`: attach the POS-side figure (X-report cash /
     processor bill payments — the SAME resolutions cash-recon-management and the mig-939
     coverage recon ride, resolved by the router and passed in here) to each store-day, with an
     honest `no_pos_data` when the feed has nothing for it (never a fake zero, never a fake
     mismatch).

  2. "cash deposit capture should be shown as a separate line item under cash deposit recon,
     every cash deposit should be accompanied by the bank deposit slip, if the cash has been
     handed over to the management then a check box should be there ... then the management
     should be able to confirm that the cash has been received by them in the system as a check
     box and making the color green for the days the cash has been accounted for whether deposit
     or handed over, it should be a similar workflow as did for the approval" →
     `day_accountability`: the per-(store, day) state machine. The GREEN rule, exactly:

       a store-day is GREEN ⇔ it has at least one picked-up envelope AND every picked-up
       envelope is accounted for, where accounted means
         • disposition 'deposited'      AND the bank deposit slip is on file, or
         • disposition 'handed_to_mgmt' AND management confirmed receipt (mig 943
           mgmt_confirmed — the approval-style actor+timestamp handshake, payroll-approvals
           precedent dm_status/dm_by/dm_at).

     SLIP POSTURE — flag, never hard-block: the existing deposit flow (mig 089) never blocks a
     save (OCR optional, amount mismatch FLAGS deposit_flagged for review), and live evidence
     2026-09-02 shows all 9 recorded 'deposited' dispositions predate slip discipline (zero
     slips on file) — a hard block would strand every one of them. "Must be accompanied" is
     enforced the way the flow already enforces correctness: a slip-less deposit day is loudly
     `missing_slip` and can NEVER turn green until the slip is uploaded.

     An amount-mismatch flag (deposit_flagged, mig 089) does NOT block green — the disposition
     is complete and slip-backed; the mismatch has its own review flow — but it is surfaced
     (`flagged_rows`) so the board shows the warning on the day.

Everything here is pure (rows/dicts in, rows out) — proof: backend/harness_deposit_accountability.py
(stdlib only). The management-confirmation GATE reuses billpay_pickup.can_see_cash_recon /
resolve_recon_access unchanged (market manager and above, mig-434 posture, fail-closed) — no
second gate implementation.
"""


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


# ── ASK 1: the POS figure right next to the store-entered figure ───────────────────────────────
def pos_next_to(declared_by_sd, pos_by_sd, feed_present, tolerance=1.0, zero_missing=False):
    """PURE: {(store, day): declared} + {(store, day): pos} → {(store, day): {declared, pos,
    delta, status}} for every declared key (the page's own rows).

    `feed_present` False (the whole feed absent for the range) → every key is `no_pos_data`
    (pos None, delta None) — an honest gap, never a fake zero or a fake mismatch.
    `feed_present` True:
      • key in pos → compare (|declared − pos| ≤ tolerance ⇒ 'ok', else 'mismatch');
      • key missing, zero_missing=True → an HONEST ZERO: the feed reported for the range but has
        nothing for this store-day (the cash-recon-management / mig-939 processor-feed
        precedent) — compares against 0.0;
      • key missing, zero_missing=False → `no_pos_data` (the X-report precedent: tenders import
        per store-day; a missing store-day is a gap, not a zero — deposit-recon shows 'pending'
        for exactly this case).
    """
    out = {}
    tol = abs(_f(tolerance))
    for k, d in (declared_by_sd or {}).items():
        d = round(_f(d), 2)
        cell = {"declared": d, "pos": None, "delta": None, "status": "no_pos_data"}
        if feed_present:
            if k in (pos_by_sd or {}):
                cell["pos"] = round(_f(pos_by_sd[k]), 2)
            elif zero_missing:
                cell["pos"] = 0.0
            if cell["pos"] is not None:
                cell["delta"] = round(d - cell["pos"], 2)
                cell["status"] = "ok" if abs(cell["delta"]) <= tol else "mismatch"
        out[k] = cell
    return out


# ── ASK 2: the per-(store, day) accountability state machine ───────────────────────────────────
def envelope_state(row):
    """PURE: one pickup row → its accountability state:
      'unpicked'          — not picked up yet (cash still in the store);
      'undisposed'        — picked up, no disposition recorded yet;
      'missing_slip'      — deposited but the bank deposit slip is NOT on file (owner: "every
                            cash deposit should be accompanied by the bank deposit slip");
      'deposited'         — deposited with the slip on file (accounted);
      'handed_unconfirmed'— handed to management, receipt not yet confirmed in the system;
      'handed_confirmed'  — handed to management AND management confirmed (accounted)."""
    r = row or {}
    if not r.get("picked_up"):
        return "unpicked"
    disp = (str(r.get("disposition") or "")).strip().lower()
    if disp == "deposited":
        return "deposited" if (r.get("deposit_slip_path") or "").strip() else "missing_slip"
    if disp == "handed_to_mgmt":
        return "handed_confirmed" if r.get("mgmt_confirmed") else "handed_unconfirmed"
    return "undisposed"


ACCOUNTED_STATES = ("deposited", "handed_confirmed")


def day_accountability(pickup_rows):
    """PURE: pickup rows (cash_pickup ∪ billpay_pickup, each optionally tagged kind) → one
    accountability row per (store_code, day), sorted by (day, store). THE GREEN RULE (owner
    2026-09-02): green ⇔ ≥1 picked-up envelope AND every picked-up envelope accounted
    (deposited-with-slip, or handed-and-mgmt-confirmed). Returns (rows, summary)."""
    by_sd = {}
    for r in pickup_rows or []:
        r = r or {}
        code = (str(r.get("store_code") or "").strip()) or "?"
        dday = str(r.get("close_date") or "")[:10]
        if not dday:
            continue
        by_sd.setdefault((code, dday), []).append(r)

    from .pickup_actual import row_variance as _row_variance
    from .pickup_actual import envelope_opened as _envelope_opened
    rows = []
    for (code, dday), rs in sorted(by_sd.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        agg = {"deposited": 0.0, "missing_slip": 0.0, "handed_confirmed": 0.0,
               "handed_unconfirmed": 0.0, "undisposed": 0.0}
        counts = {k: 0 for k in agg}
        picked_total, picked_n, flagged, envs = 0.0, 0, 0, []
        confirmed_by, confirmed_at = None, None
        # mig 949 (owner 2026-09-04): actual-vs-declared visibility on the day view — a SHORT
        # pickup must be visible on the accountability board. Display + flag only: variance never
        # affects the GREEN rule (green is about disposition/confirmation, not the count), and
        # `picked_total` stays the declared movement figure (the money posture lives solely in
        # _cash_position_core's knob — one gate, not two).
        pickup_short_rows, pickup_over_rows, pickup_variance_total = 0, 0, 0.0
        for r in rs:
            st = envelope_state(r)
            amt = _f(r.get("amount"))
            vf = _row_variance(r) if st != "unpicked" else None
            if vf:
                pickup_variance_total = round(pickup_variance_total + vf["variance"], 2)
                pickup_short_rows += 1 if vf["status"] == "short" else 0
                pickup_over_rows += 1 if vf["status"] == "over" else 0
            if st != "unpicked":
                picked_total += amt
                picked_n += 1
                agg[st] += amt
                counts[st] += 1
            if r.get("deposit_flagged"):
                flagged += 1
            if st == "handed_confirmed":
                # surface the LATEST confirmation actor/timestamp for the day chip
                at = str(r.get("mgmt_confirmed_at") or "")
                if at >= str(confirmed_at or ""):
                    confirmed_at = r.get("mgmt_confirmed_at")
                    confirmed_by = r.get("mgmt_confirmed_by")
            envs.append({
                "kind": r.get("kind") or "cash", "employee_name": r.get("employee_name"),
                "amount": round(amt, 2), "state": st,
                # WHO collected it (mig 034) — carried onto the envelope so the by-DM shortage
                # rollup below folds the rows that are ACTUALLY ON SCREEN (post-keyset), rather
                # than re-reading the pickup tables and risking a different population.
                "picked_up_by": r.get("picked_up_by"),
                # WHEN it was collected / banked (mig 034 / 089). Added 2026-10-02 for the
                # five-stage chain, which reads its pickup and handover timestamps off THESE
                # envelopes rather than re-walking the pickup tables — so the chain and this board
                # can never disagree about when an envelope moved. Additive: no existing consumer
                # reads these keys, and no value already here changed.
                "picked_up_at": r.get("picked_up_at"),
                "deposited_at": r.get("deposited_at"),
                # mig 990 — the DM's statement that they opened and counted this envelope
                "envelope_opened": _envelope_opened(r),
                # mig 949 — the DM's actual count at pickup (None = not recorded, never fake 0)
                "actual_picked_amount": vf["actual"] if vf else None,
                "pickup_variance": vf["variance"] if vf else None,
                "pickup_variance_status": vf["status"] if vf else None,
                "disposition": r.get("disposition"), "handed_to": r.get("handed_to"),
                "deposit_amount": r.get("deposit_amount"),
                "deposit_flagged": bool(r.get("deposit_flagged")),
                "deposit_slip_path": r.get("deposit_slip_path"),
                "mgmt_confirmed": bool(r.get("mgmt_confirmed")),
                "mgmt_confirmed_by": r.get("mgmt_confirmed_by"),
                "mgmt_confirmed_at": r.get("mgmt_confirmed_at"),
            })
        unaccounted = counts["missing_slip"] + counts["handed_unconfirmed"] + counts["undisposed"]
        green = picked_n > 0 and unaccounted == 0
        # handed checkbox state (owner: "a check box ... for all the dates of which the cash has
        # been handed over"): checked when the day has ≥1 handed envelope.
        handed_n = counts["handed_confirmed"] + counts["handed_unconfirmed"]
        rows.append({
            "store_code": code, "day": dday,
            "picked_total": round(picked_total, 2), "picked_envelopes": picked_n,
            "deposited_total": round(agg["deposited"] + agg["missing_slip"], 2),
            "deposited_rows": counts["deposited"] + counts["missing_slip"],
            "missing_slip_rows": counts["missing_slip"],
            "missing_slip_total": round(agg["missing_slip"], 2),
            "handed_total": round(agg["handed_confirmed"] + agg["handed_unconfirmed"], 2),
            "handed": handed_n > 0, "handed_rows": handed_n,
            "confirmed_rows": counts["handed_confirmed"],
            "unconfirmed_rows": counts["handed_unconfirmed"],
            "mgmt_confirmed": handed_n > 0 and counts["handed_unconfirmed"] == 0,
            "mgmt_confirmed_by": confirmed_by, "mgmt_confirmed_at": confirmed_at,
            "undisposed_total": round(agg["undisposed"], 2),
            "undisposed_rows": counts["undisposed"],
            "flagged_rows": flagged,
            # mig 949 — actual-vs-declared day chips (display/flag only; never touches `green`)
            "pickup_short_rows": pickup_short_rows,
            "pickup_over_rows": pickup_over_rows,
            "pickup_variance_total": round(pickup_variance_total, 2),
            "green": green,
            "envelopes": envs,
        })
    summary = {
        "store_days": len(rows),
        "green_days": sum(1 for r in rows if r["green"]),
        "missing_slip_days": sum(1 for r in rows if r["missing_slip_rows"]),
        "awaiting_confirm_days": sum(1 for r in rows if r["unconfirmed_rows"]),
        "undisposed_days": sum(1 for r in rows if r["undisposed_rows"]),
        "picked_total": round(sum(r["picked_total"] for r in rows), 2),
        "deposited_total": round(sum(r["deposited_total"] for r in rows), 2),
        "handed_total": round(sum(r["handed_total"] for r in rows), 2),
        # mig 949 — days with at least one short pickup (actual < declared at pickup time)
        "short_pickup_days": sum(1 for r in rows if r["pickup_short_rows"]),
    }
    return rows, summary


# ── CASH SHORT BY DM (owner directive 2026-09-08) ──────────────────────────────────────────────
# Owner, verbatim: "if the cash is short then it should generate a cash short report by DM".
#
# WHY THIS LIVES HERE AND IS NOT A FOURTH SURFACE (the duplicate-check verdict). Three surfaces
# already carry pickup short/over, and each answers a DIFFERENT question:
#   · GET /closing/pickups          — per ENVELOPE, on the DM's own working screen for a day/range.
#   · GET /closing/cash-recon-management — per STORE-DAY, management-gated, with the dm_short_days /
#     dm_short_amount totals. Its rows are store-days and carry no picked_up_by, so grouping by DM
#     there would mean a SECOND read of the pickup tables — a sibling derivation of exactly what
#     _accountability_pickup_rows already returns — and its market-manager gate would hide a DM's
#     own shortages from the DM.
#   · this board — already reads BOTH pickup tables over the range (cash ∪ billpay, org-scoped,
#     keyset-filtered), already computes pickup_short_rows / short_pickup_days from row_variance,
#     and is the surface where "was this cash accounted for" is answered.
# So the by-DM report is ONE MORE FOLD over rows already in hand: no new read, no new query, and
# short/over is not re-derived — it is pickup_actual.row_variance, the same envelope_report
# count_fields truth table used everywhere else, arriving pre-computed on the day rows' envelopes.
#
# IT FOLDS THE DAY ROWS, NOT THE RAW PICKUPS, and that is deliberate: the endpoint filters day rows
# by the caller's keyset, so folding those rows makes the DM totals agree with what is on screen by
# construction. Re-reading the raw rows could report a shortage for a store the viewer cannot see.


def dm_shortage_rows(day_rows, tolerance=0.0):
    """PURE: accountability day rows (already keyset-filtered) -> one row per DM who picked up,
    with their counted/uncounted envelopes and their short/over money. Sorted worst-short first.
    Returns (rows, summary).

    UNCOUNTED IS NOT SHORT. An envelope with no recorded count contributes to `uncounted_rows`
    and to NEITHER short nor over — the same rule the rest of this file follows (a store-day
    nobody counted is None, never 0.00). A DM whose envelopes were all collected sealed shows
    zero short and every envelope uncounted, which is an honest description of a sealed round,
    not a clean bill of health.

    `tolerance` is a dollar band, default 0 — the pickup variance ties out to the cent or it
    does not (count_fields' own rule). It is a parameter rather than a constant because
    cash-recon-management already exposes one on the same comparison; nothing here invents a
    second default.
    """
    tol = abs(_f(tolerance))
    by_dm = {}
    for r in day_rows or []:
        for env in (r or {}).get("envelopes") or []:
            if (env or {}).get("state") == "unpicked":
                continue          # still in the store — nobody has picked it up to be short of it
            dm = (str(env.get("picked_up_by") or "").strip()) or "(unattributed)"
            b = by_dm.setdefault(dm, {
                "dm": dm, "envelopes": 0, "counted_rows": 0, "uncounted_rows": 0,
                "opened_rows": 0, "short_rows": 0, "over_rows": 0, "match_rows": 0,
                "declared_total": 0.0, "actual_total": 0.0,
                "short_amount": 0.0, "over_amount": 0.0, "net_variance": 0.0,
                "stores": set(), "days": set(), "shorts": [],
            })
            b["envelopes"] += 1
            b["declared_total"] = round(b["declared_total"] + _f(env.get("amount")), 2)
            if env.get("envelope_opened"):
                b["opened_rows"] += 1
            b["stores"].add(r.get("store_code") or "?")
            b["days"].add(r.get("day") or "")
            var = env.get("pickup_variance")
            if var is None:
                b["uncounted_rows"] += 1
                continue
            b["counted_rows"] += 1
            b["actual_total"] = round(b["actual_total"] + _f(env.get("actual_picked_amount")), 2)
            b["net_variance"] = round(b["net_variance"] + _f(var), 2)
            if _f(var) < -tol:
                b["short_rows"] += 1
                b["short_amount"] = round(b["short_amount"] + _f(var), 2)   # negative = short
                b["shorts"].append({
                    "day": r.get("day"), "store_code": r.get("store_code"),
                    "store_name": r.get("store_name"), "market": r.get("market"),
                    "kind": env.get("kind") or "cash",
                    "employee_name": env.get("employee_name"),
                    "declared": round(_f(env.get("amount")), 2),
                    "actual": round(_f(env.get("actual_picked_amount")), 2),
                    "variance": round(_f(var), 2),
                    "envelope_opened": bool(env.get("envelope_opened")),
                })
            elif _f(var) > tol:
                b["over_rows"] += 1
                b["over_amount"] = round(b["over_amount"] + _f(var), 2)
            else:
                b["match_rows"] += 1

    rows = []
    for b in by_dm.values():
        b["stores_count"] = len(b["stores"])
        b["days_count"] = len([d for d in b["days"] if d])
        b.pop("stores", None)
        b.pop("days", None)
        b["shorts"].sort(key=lambda s: (_f(s.get("variance")), str(s.get("day") or "")))
        b["is_short"] = b["short_rows"] > 0
        rows.append(b)
    # worst shortage first (short_amount is negative), then most short envelopes, then name —
    # the DM the report exists to surface is the one at the top.
    rows.sort(key=lambda b: (b["short_amount"], -b["short_rows"], str(b["dm"])))

    summary = {
        "dms": len(rows),
        "dms_short": sum(1 for b in rows if b["is_short"]),
        "short_rows": sum(b["short_rows"] for b in rows),
        "over_rows": sum(b["over_rows"] for b in rows),
        "counted_rows": sum(b["counted_rows"] for b in rows),
        "uncounted_rows": sum(b["uncounted_rows"] for b in rows),
        "opened_rows": sum(b["opened_rows"] for b in rows),
        "short_amount": round(sum(b["short_amount"] for b in rows), 2),
        "over_amount": round(sum(b["over_amount"] for b in rows), 2),
        "net_variance": round(sum(b["net_variance"] for b in rows), 2),
        "declared_total": round(sum(b["declared_total"] for b in rows), 2),
        "actual_total": round(sum(b["actual_total"] for b in rows), 2),
        "tolerance": tol,
    }
    return rows, summary


def pickup_deposit_line(pickup_rows):
    """PURE: the 'deposit capture as its own separate line item under cash deposit recon' (owner
    2026-09-02) — per (store, day), the deposit-disposition captures recorded through the pickup
    flow (POST /pickup/deposit + the billpay sibling): {(store, day): {amount, rows, slips,
    missing_slip, flagged, deposits:[...]}}. Distinct from commcalc.bank_deposit rows — this is
    the CAPTURE side (the slip photographed at the pickup), never summed into the recon's
    expected/deposited math (that stays bank_deposit's; one number, one source)."""
    out = {}
    for r in pickup_rows or []:
        r = r or {}
        if (str(r.get("disposition") or "")).strip().lower() != "deposited":
            continue
        code = (str(r.get("store_code") or "").strip()) or "?"
        dday = str(r.get("close_date") or "")[:10]
        if not dday:
            continue
        slot = out.setdefault((code, dday), {"amount": 0.0, "rows": 0, "slips": 0,
                                             "missing_slip": 0, "flagged": 0, "deposits": []})
        amt = _f(r.get("deposit_amount") if r.get("deposit_amount") is not None
                 else r.get("amount"))
        has_slip = bool((r.get("deposit_slip_path") or "").strip())
        slot["amount"] = round(slot["amount"] + amt, 2)
        slot["rows"] += 1
        slot["slips"] += 1 if has_slip else 0
        slot["missing_slip"] += 0 if has_slip else 1
        slot["flagged"] += 1 if r.get("deposit_flagged") else 0
        slot["deposits"].append({
            "kind": r.get("kind") or "cash", "employee_name": r.get("employee_name"),
            "amount": round(amt, 2), "has_slip": has_slip,
            "deposit_slip_path": r.get("deposit_slip_path"),
            "flagged": bool(r.get("deposit_flagged")), "deposited_at": r.get("deposited_at"),
        })
    return out


# ── WHICH STORE-DAYS A CLOSING IS EXPECTED FROM — one home (owner 2026-10-02) ───────────────────
# "Daily Closing done or not" needs the DENOMINATOR: a store that filed nothing has no row to key
# on, so it can only be reported as missing against a list of stores that SHOULD have filed.
#
# That rule already existed, inline, in the cash-pickup screen's `not_closed` straggler list
# (router.py, owner bug reports 2026-08-06 / 2026-09-07): an ACTIVE store, passing the viewer's
# keyset and the screen's store/market filters, that has no `daily_closing` row for the date. Both
# that list and the accountability chain now DEREFERENCE this function, because "this store did not
# file" must not be able to mean two different things on two screens (the duplicate defect the index
# rules forbid). The filters arrive as already-resolved predicates/sets so this stays PURE — the
# caller owns the I/O, exactly as it did before.
def expected_store_codes(store_rows, keyset_ok=None, store_set=None, market_set=None,
                         market_of=None):
    """PURE: the store codes a closing is expected from, in `store_rows` order.

    · a blank `store_code`, or `is_active is False`, is never expected (the roster holds non-store
      entries and closed stores);
    · `keyset_ok(code, address)` — the viewer's span (None ⇒ no keyset restriction);
    · `store_set` — UPPERCASE codes the screen is filtered to (falsy ⇒ no filter);
    · `market_set` — casefolded market names the screen is filtered to (falsy ⇒ no filter);
      a store with a BLANK market is never excluded by a market filter (it has nothing to compare,
      and dropping it would hide a store rather than report it) — the pre-existing `and mk` posture.
    · `market_of(code)` — fallback market when the roster row's own is blank.
    """
    out = []
    for s in store_rows or []:
        s = s or {}
        code = (s.get("store_code") or "")
        if not code or s.get("is_active") is False:
            continue
        if keyset_ok is not None and not keyset_ok(code, s.get("address")):
            continue
        if store_set and code.upper() not in store_set:
            continue
        mk = (s.get("market") or "").strip() or ((market_of or (lambda _c: ""))(code) or "")
        if market_set and mk and mk.casefold() not in market_set:
            continue
        out.append(code)
    return out


def date_span(start, end):
    """PURE: every ISO date from start..end inclusive. The chain's spine is (expected store × day),
    so the days have to be enumerated rather than taken from whatever rows happen to exist."""
    from datetime import date as _date, timedelta as _td
    try:
        a, b = _date.fromisoformat(str(start)[:10]), _date.fromisoformat(str(end)[:10])
    except ValueError:
        return []
    if a > b:
        a, b = b, a
    return [(a + _td(days=i)).isoformat() for i in range((b - a).days + 1)]


# ── THE FIVE-STAGE ACCOUNTABILITY CHAIN (owner 2026-10-02) ──────────────────────────────────────
# Owner, verbatim: "we need to see in a daily report or date range report for the following /
#   Daily Closing done or not with dates and by who / DM verified or not with dates and by who /
#   CAsh pick with dates and by who / Cash Handover with dates and by who / managment review with
#   dates and by who"
#
# NOT ONE NEW FACT IS DERIVED HERE. Every stage reads the actor + timestamp its own home already
# records, and the two middle stages are read off `day_accountability`'s OWN day rows rather than
# re-walking the pickup tables — so the chain and the green-day board can never disagree about what
# happened to an envelope:
#
#   stage        done when                                   by / at come from
#   closing      ≥1 daily_closing row for the store-day      employee_name / submitted_at
#   dm_verify    a verification row with verified truthy      verified_by / verified_at
#   pickup       every envelope on the day is picked up       picked_up_by / picked_up_at   (day row)
#   handover     every picked envelope has a disposition      handed_to|'bank' / deposited_at,
#                (deposited, or handed to management)         plus mgmt_confirmed_by / _at
#   mgmt_review  ≥1 envelope_count row for the store-day      counted_by / counted_at
#
# WHERE THE OWNER'S FIVE NAMES LAND, stated so it can be corrected in one line rather than guessed
# at twice: "Cash Handover" is the DM's disposition — the cash leaving their hands, to the bank or to
# management — and management's RECEIPT handshake (mig-943 mgmt_confirmed, the thing the GREEN rule
# already waits for) is carried in that same cell as its second actor pair, because confirming
# receipt completes a handover rather than being a separate step. "Management review" is the
# Management Envelope Receipt (§47) — management counting the envelope and recording short/over.
# Both facts are on every row either way, so if the owner means mgmt_confirmed by "management
# review", it is a change of which cell shows it, never a change of what is read.
STAGE_KEYS = ("closing", "dm_verify", "pickup", "handover", "mgmt_review")
STAGE_LABELS = {
    "closing": "Daily Closing",
    "dm_verify": "DM verified",
    "pickup": "Cash pickup",
    "handover": "Cash handover",
    "mgmt_review": "Management review",
}
# The screen renders these; it spells no stage key and no stage label of its own (the §47 posture).
def stage_catalog():
    """PURE: [{key, label}] in chain order — the selector/column vocabulary, served by the API."""
    return [{"key": k, "label": STAGE_LABELS[k]} for k in STAGE_KEYS]


def _names(vals):
    """Distinct, blank-dropped, order-stable — several reps file one store-day, several DMs can
    collect it. Returns a list; the caller joins for display."""
    out = []
    for v in vals or []:
        s = str(v or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def _cell(done, at=None, by=None, detail="", **extra):
    c = {"done": bool(done), "at": at or None, "by": by or [], "detail": detail}
    c.update(extra)
    return c


def _max(a, b):
    """The later of two timestamps, treating blank as absent (string ISO compare — the same
    posture day_accountability already uses for the latest confirmation)."""
    a, b = str(a or ""), str(b or "")
    return (a if a >= b else b) or None


def stage_chain(expected_keys, closing_rows, verification_rows, day_rows, count_rows):
    """PURE: → (rows, summary). One row per (store_code, day) with the five stage cells.

    `expected_keys` is the SPINE — an iterable of (store_code, day) from `expected_store_codes` ×
    `date_span`. A store-day with no artefact at all still appears, reading "closing not done",
    which is the whole point of "done or not"; and any store-day carrying an artefact is added even
    when it is not in `expected_keys` (an inactive or out-of-roster store that filed anyway is a
    real thing to see, never silently dropped).

    `day_rows` are `day_accountability`'s own rows — pass them straight through.
    """
    chain = {}

    def slot(code, day):
        k = (str(code or "").strip() or "?", str(day or "")[:10])
        if not k[1]:
            return None
        return chain.setdefault(k, {"closing": [], "ver": None, "day": None, "counts": []})

    for code, day in expected_keys or []:
        slot(code, day)
    for r in closing_rows or []:
        s = slot((r or {}).get("store_code"), (r or {}).get("close_date"))
        if s is not None:
            s["closing"].append(r)
    for r in verification_rows or []:
        s = slot((r or {}).get("store_code"), (r or {}).get("close_date"))
        if s is not None:
            # one verification row per store-day by design; last write wins, as the upsert does
            s["ver"] = r
    for r in day_rows or []:
        s = slot((r or {}).get("store_code"), (r or {}).get("day"))
        if s is not None:
            s["day"] = r
    for r in count_rows or []:
        s = slot((r or {}).get("store_code"), (r or {}).get("close_date"))
        if s is not None:
            s["counts"].append(r)

    rows = []
    for (code, day), g in sorted(chain.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        rows.append(_chain_row(code, day, g))
    return rows, chain_summary(rows)


def _chain_row(code, day, g):
    crs, ver, dayr, cnts = g["closing"], g["ver"], g["day"], g["counts"]

    # 1. DAILY CLOSING — the rep's filing. `at` is the LATEST submission of the day: a store-day is
    #    not filed until its last rep has filed, and reporting the earliest would call a
    #    half-finished day done at its first row's timestamp.
    c_at = None
    for r in crs:
        c_at = _max(c_at, (r or {}).get("submitted_at") or (r or {}).get("created_at"))
    closing = _cell(bool(crs), c_at, _names((r or {}).get("employee_name") for r in crs),
                    ("%d row(s)" % len(crs)) if crs else "no closing filed", rows=len(crs))

    # 2. DM VERIFIED — a verification row whose `verified` is truthy. A row that exists but is not
    #    verified is NOT done, and says so, rather than being counted by its mere existence.
    v_ok = bool(ver and ver.get("verified"))
    dm_verify = _cell(v_ok, (ver or {}).get("verified_at"),
                      _names([(ver or {}).get("verified_by")]),
                      "verified" if v_ok else ("row present, not verified" if ver else "not verified"),
                      note=(ver or {}).get("note") or None)

    # 3/4. PICKUP + HANDOVER — read off day_accountability's envelopes, never re-derived.
    envs = (dayr or {}).get("envelopes") or []
    picked = [e for e in envs if e.get("state") != "unpicked"]
    unpicked = [e for e in envs if e.get("state") == "unpicked"]
    p_at, pickers = None, []
    for e in picked:
        p_at = _max(p_at, e.get("picked_up_at"))
        pickers.append(e.get("picked_up_by"))
    pickup = _cell(bool(picked) and not unpicked, p_at, _names(pickers),
                   ("%d of %d envelope(s) picked up" % (len(picked), len(envs))) if envs
                   else "no envelope recorded",
                   envelopes=len(envs), picked=len(picked), unpicked=len(unpicked))

    # A picked envelope is HANDED OVER once its disposition is recorded — deposited at the bank, or
    # handed to management. `undisposed` is precisely "the DM still holds it", which is the gap the
    # live data shows on most pickups.
    undisposed = [e for e in picked if e.get("state") == "undisposed"]
    handed = [e for e in picked if str(e.get("disposition") or "").strip().lower() == "handed_to_mgmt"]
    deposited = [e for e in picked if str(e.get("disposition") or "").strip().lower() == "deposited"]
    h_at, receivers = None, []
    for e in handed:
        h_at = _max(h_at, e.get("mgmt_confirmed_at"))
        receivers.append(e.get("handed_to"))
    for e in deposited:
        h_at = _max(h_at, e.get("deposited_at"))
    conf_at, confirmers = None, []
    for e in picked:
        if e.get("mgmt_confirmed"):
            conf_at = _max(conf_at, e.get("mgmt_confirmed_at"))
            confirmers.append(e.get("mgmt_confirmed_by"))
    handover = _cell(
        bool(picked) and not undisposed, h_at,
        _names(receivers + (["bank deposit"] if deposited else [])),
        ("%d handed, %d deposited, %d still with the DM" % (len(handed), len(deposited), len(undisposed)))
        if picked else "nothing picked up to hand over",
        handed=len(handed), deposited=len(deposited), undisposed=len(undisposed),
        # The RECEIPT handshake the GREEN rule waits for — the handover's second actor pair.
        confirmed_rows=len(confirmers), confirmed_by=_names(confirmers), confirmed_at=conf_at,
        missing_slip=(dayr or {}).get("missing_slip_rows") or 0,
        green=bool((dayr or {}).get("green")))

    # 5. MANAGEMENT REVIEW — the Management Envelope Receipt (§47): management counted it.
    m_at, counters, statuses = None, [], []
    for r in cnts:
        m_at = _max(m_at, (r or {}).get("counted_at"))
        counters.append((r or {}).get("counted_by"))
        st = str((r or {}).get("status") or "").strip().lower()
        if st:
            statuses.append(st)
    mgmt_review = _cell(bool(cnts), m_at, _names(counters),
                        (", ".join(sorted(set(statuses))) if statuses else "counted")
                        if cnts else "not reviewed",
                        counts=len(cnts), statuses=sorted(set(statuses)))

    stages = {"closing": closing, "dm_verify": dm_verify, "pickup": pickup,
              "handover": handover, "mgmt_review": mgmt_review}
    # WHERE IT IS STUCK — the first stage in chain order that is not done. One word per store-day,
    # so a 30-day board answers "what is holding this up" without reading five columns.
    stuck = next((k for k in STAGE_KEYS if not stages[k]["done"]), None)
    return {
        "store_code": code, "day": day,
        "stages": [dict(stages[k], key=k, label=STAGE_LABELS[k]) for k in STAGE_KEYS],
        "stuck_at": stuck, "stuck_label": STAGE_LABELS.get(stuck) if stuck else None,
        "stages_done": sum(1 for k in STAGE_KEYS if stages[k]["done"]),
        "complete": stuck is None,
    }


def chain_summary(rows):
    """PURE: per-stage done counts + the stuck histogram, over exactly the rows handed in (so a
    keyset-filtered board's summary can never count a store-day the viewer cannot see)."""
    rows = rows or []
    by_stage = {}
    for i, k in enumerate(STAGE_KEYS):
        done = sum(1 for r in rows if (r.get("stages") or [{}] * 5)[i].get("done"))
        by_stage[k] = {"key": k, "label": STAGE_LABELS[k], "done": done,
                       "missing": len(rows) - done}
    stuck = {}
    for r in rows:
        if r.get("stuck_at"):
            stuck[r["stuck_at"]] = stuck.get(r["stuck_at"], 0) + 1
    return {
        "store_days": len(rows),
        "complete_days": sum(1 for r in rows if r.get("complete")),
        "by_stage": [by_stage[k] for k in STAGE_KEYS],
        "stuck_at": [{"key": k, "label": STAGE_LABELS[k], "store_days": stuck.get(k, 0)}
                     for k in STAGE_KEYS if stuck.get(k)],
    }
