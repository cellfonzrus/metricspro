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

    rows = []
    for (code, dday), rs in sorted(by_sd.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        agg = {"deposited": 0.0, "missing_slip": 0.0, "handed_confirmed": 0.0,
               "handed_unconfirmed": 0.0, "undisposed": 0.0}
        counts = {k: 0 for k in agg}
        picked_total, picked_n, flagged, envs = 0.0, 0, 0, []
        confirmed_by, confirmed_at = None, None
        for r in rs:
            st = envelope_state(r)
            amt = _f(r.get("amount"))
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
