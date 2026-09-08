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
