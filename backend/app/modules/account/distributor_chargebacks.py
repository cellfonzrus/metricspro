"""DISTRIBUTOR CHARGEBACKS — two separate chargebacks billed to the master/dealer account, derived
from the invoice feed.

OWNER, verbatim (2026-09-11): *"9663.75 is actually being mortised towards the chargeback - ttoal
chargeback is 159106.76 +9663.75 x6 should be in the balance sheet as chargeback"*, then, on being
shown what that implies over time: *"159106.76 is a separate cjhargeback which it seems occured iun
4 instalments as per teh report but 9663.75 is a seaparate chargeback which started in 2025 and
going to 2026"*.

═══════════════ THEY ARE TWO SEPARATE CHARGEBACKS. NOTHING AMORTISES ANYTHING ═════════════════════
That second message settles it, and it changes the vocabulary, not just the arithmetic:

    A — a ONE-OFF chargeback              $159,106.76   a single standing charge
    B — a RECURRING monthly chargeback    $  9,663.75 each, still running
    ────────────────────────────────────────────────────────────────────────
    combined, to date                     $217,089.26

B is not amortisation of A. It never reduces A, and A's balance is therefore *supposed* to stay
where it is — which is why an earlier draft's "carrying value never declines" was a correct
observation about a wrong model rather than a defect. **There is deliberately no `amortisation`,
`accumulated_amortisation` or `carrying_value` anywhere in this module.** A field named for
amortisation that never amortises anything misleads the next reader, and the audience here is the
owner's accountant.

═══════════════ TWO THINGS THE OWNER SAID THAT THE DATA DOES NOT SUPPORT ══════════════════════════
Both were measured across the entire feed. The report follows the DATA and shows the discrepancy
rather than arguing with it or quietly reproducing the description.

1. **A did NOT arrive in four instalments.** The "Return Item Chargeback ×4, $159,146.76" that the
   description came from is one charge of **$159,056.76** at the account (invoice 1604147) plus
   **three unrelated $30.00 store-level chargebacks** at three different retail stores, all billed
   the same day. Scoping on the account location AND the name vocabulary removes them; see the trap
   below. A is ONE charge, on ONE invoice, plus the $50.00 NSF fee on that same invoice.

2. **B did NOT start in 2025.** Searching the whole feed for any line valued 9,663.75 under ANY name
   in ANY year returns six hits, every one of them in **2026** (Feb 23, Apr 3, May 2, Jun 2, Jul 2,
   Aug 2), all at the account. There is no 2025 occurrence. Either B genuinely began in February
   2026, or 2025 instalments exist at the distributor and never reached our feed — and this report
   must not invent them either way. `recurring.first_seen` publishes the first date the DATA has, so
   the discrepancy is visible on the page instead of being settled by whoever remembers hardest.

═══════════════ THE TRAP A NAIVE NAME MATCH FALLS INTO ════════════════════════════════════════════
Summing every chargeback-NAMED line on this feed gives **$217,660.31**, not $217,089.26. The extra
$571.05 is other people's money: the three $30.00 store chargebacks above (plus their $50 NSF fees),
19 "Early Life Churn Chargeback" lines ($306.05, 2023) and one "Handset Returns Commission
Chargeback" ($25.00). Same word, same day, unrelated. So the scope is BOTH the account LOCATION and
the line-name vocabulary, and both are per-org config.

═══════════════ RULE TWO ══════════════════════════════════════════════════════════════════════════
No location, vendor, product or line name appears in this file. Every one is a per-org config value
and **the house default is EMPTY** — this module derives NOTHING and books NOTHING for any org, the
house org included, until an owner configures it. Same posture as `service_fee_products`,
`payroll_expense_names` and `overhead_config` in `coa._account_config`.

═══════════════ BOTH LEGS ARE P&L EXPENSE. THERE IS NO BALANCE-SHEET ASSET ════════════════════════
Owner, asked directly whether A belongs on the balance sheet: **"159106.76 is an expense"**. So both
legs book to the EXISTING `chargebacks` opex line and nothing reaches the Balance Sheet:

    A — the one-off      → expense in the month it was billed (December 2025)
    B — each recurrence  → expense in the month each was billed

An asset was the wrong shape for exactly the reason the earlier draft exposed: an asset implies
future recovery, and nothing ever reduced A. No new COA line and no new BS key is created;
`balance_sheet.py` and `statement_engine.py` are untouched, and there is no cumulative as-of leg.

THE FINDING THAT MAKES THIS MATERIAL: **this money is on no statement today.** `coa.build_inputs`
reads invoice HEADERS only (`shipping + other_cost` → `vip_fees`; unpaid `grand_total` → `vip_ap`),
and all seven invoices carry shipping $0.00, other_cost $0.00, status 'Paid In Full'. They contribute
$0.00 to the P&L and $0.00 to the Balance Sheet as things stand. So booking it RAISES December 2025 opex by $159,106.76
and 2026 opex by $9,663.75 in each month billed. This is recognition of money that was never on the
books — not a reclassification — and it is the one change in this package that moves an existing
figure rather than adding an inert one.

═══════════════ DUPLICATE CHECK (CLAUDE.md build gate) ════════════════════════════════════════════
  · `chargebacks` ("Chargebacks / clawbacks", opex, store) — the intended expense line for B (and
    for A under `booking='expense'`). Reused; no new expense line is created.
  · `chargeback_res` ("Chargeback reserve", liability) — DELIBERATELY NOT REUSED. It means EXPECTED,
    UNDEDUCTED chargebacks (`coa.build_inputs` books it from `chargeback_items` rows with no
    `decided_at`). These are decided, deducted AND paid in full; a reserve would state the opposite
    and would corrupt a line the P&L already uses.
  · `commcalc.chargeback_items` / `ops_chargeback` (migs 036/037/504) — INTERNAL store/employee
    chargebacks with their own decide/settle lifecycle. A DISTRIBUTOR chargeback is not one of those
    and is not written into that lifecycle.
  · `vip_fees` / `vip_ap` — read the invoice HEADERS, which carry nothing here (see above).

PURE. stdlib-only math over rows handed to it: no client, no I/O, no writes.
Proof: backend/harness_distributor_chargebacks.py.
"""
from app.modules.commcalc.calculator import safe_float

# Where a configured chargeback books. Config, never a branch on a tenant. There is deliberately NO
# 'asset' value: the owner ruled both legs are expense, and an option nobody chose is an invitation
# to book money somewhere nobody decided on.
BOOKING_OFF = "off"
BOOKING_EXPENSE = "expense"
BOOKINGS = (BOOKING_OFF, BOOKING_EXPENSE)

# HOUSE DEFAULTS — deliberately EMPTY, and booking OFF. No org derives or books anything until an
# owner configures it, so every tenant is byte-identical the day this ships (RULE TWO).
DEFAULT_CONFIG = {
    "locations": [],          # invoice `location` values whose chargebacks are in scope
    "one_off_names": [],      # line names forming the ONE-OFF chargeback (A)
    "recurring_names": [],    # line names forming the RECURRING chargeback (B)
    # Names to REPORT but NEVER book — the chargeback-shaped money that is deliberately out of
    # scope, so the owner can see it and decide rather than discovering it later. Unscoped by
    # location on purpose: its whole point is to show what the location scope excluded.
    "watch_names": [],
    "booking": BOOKING_OFF,
}


def _t(v):
    return str(v or "").strip()


def _fold(v):
    return _t(v).casefold()


def as_date(v):
    """PURE: the ISO date prefix of a date/timestamp, '' when unreadable. ISO dates compare
    correctly as strings, so every comparison below is a string comparison."""
    s = _t(v)
    if len(s) < 10 or s[4] != "-" or s[7] != "-":
        return ""
    d = s[:10]
    return d if d[:4].isdigit() and d[5:7].isdigit() and d[8:10].isdigit() else ""


def normalise_config(cfg):
    """PURE: a raw config dict (or None) → the resolved vocabulary, case-folded for matching.

    A missing key, a null, a string where a list belongs, or a booking nobody recognises all degrade
    to the house default rather than raising — the never-raises posture `coa._account_config` takes,
    because a config typo must not take a statement down."""
    c = cfg if isinstance(cfg, dict) else {}

    def _list(key):
        v = c.get(key)
        if isinstance(v, str):
            v = [v]
        return [_t(x) for x in v if _t(x)] if isinstance(v, list) else []

    booking = _t(c.get("booking")) or BOOKING_OFF
    if booking not in BOOKINGS:
        booking = BOOKING_OFF
    locations, one_off, recurring = _list("locations"), _list("one_off_names"), _list("recurring_names")
    watch = _list("watch_names")
    return {
        "locations": locations, "one_off_names": one_off, "recurring_names": recurring,
        "watch_names": watch, "booking": booking,
        "_loc": {_fold(x) for x in locations},
        "_one": {_fold(x) for x in one_off},
        "_rec": {_fold(x) for x in recurring},
        "_watch": {_fold(x) for x in watch},
        # CONFIGURED means an account scope AND something to find in it. Without both, the
        # derivation either matches nothing or matches every store's chargebacks — and matching
        # every store's is exactly the $571.05 trap this module exists to avoid.
        "configured": bool(locations) and bool(one_off or recurring),
    }


def classify_line(row, cfg):
    """PURE: 'one_off' | 'recurring' | None for one invoice line, under a normalised config.

    Scoped on BOTH the account LOCATION and the name vocabulary. Location alone sweeps in every fee
    the account is ever billed; name alone sweeps in three retail stores' $30 chargebacks that share
    a date and a word with this charge."""
    if not cfg.get("configured"):
        return None
    if _fold(row.get("location")) not in cfg["_loc"]:
        return None
    name = _fold(row.get("name"))
    if name in cfg["_one"]:
        return "one_off"
    if name in cfg["_rec"]:
        return "recurring"
    return None


def _leg(line_rows, cfg, kind, as_of):
    """PURE: one chargeback's lines and its shape — total, count, and the first and last dates the
    DATA carries (never a date anybody remembers)."""
    rows = []
    for r in (line_rows or []):
        if classify_line(r, cfg) != kind:
            continue
        d = as_date(r.get("created_on"))
        if as_of and d and d > as_of:
            continue
        rows.append({"date": d, "invoice_number": _t(r.get("invoice_number")),
                     "name": _t(r.get("name")), "location": _t(r.get("location")),
                     "amount": safe_float(r.get("total")), "status": _t(r.get("status"))})
    rows.sort(key=lambda x: (x["date"], x["invoice_number"], x["name"]))
    dates = [x["date"] for x in rows if x["date"]]
    return {"total": round(sum(x["amount"] for x in rows), 2), "count": len(rows),
            "first_seen": (dates[0] if dates else None),
            "last_seen": (dates[-1] if dates else None),
            "invoices": sorted({x["invoice_number"] for x in rows if x["invoice_number"]}),
            "lines": rows}


def _missing_months(rows):
    """PURE: calendar months between the first and last charge that carry NO charge at all.

    A recurring charge with a hole in it is a fact worth seeing — it may be a genuine skipped month
    or a feed that missed an invoice, and the report must not smooth it into a tidy cadence. Live
    house org: 2026-03 is absent (Feb 23 → Apr 3)."""
    months = sorted({x["date"][:7] for x in rows if x["date"]})
    if len(months) < 2:
        return []
    y0, m0 = int(months[0][:4]), int(months[0][5:7])
    y1, m1 = int(months[-1][:4]), int(months[-1][5:7])
    have, out = set(months), []
    while (y0, m0) < (y1, m1):
        m0 += 1
        if m0 > 12:
            y0, m0 = y0 + 1, 1
        key = "%04d-%02d" % (y0, m0)
        if key < months[-1] and key not in have:
            out.append(key)
    return out


def derive(line_rows, cfg, as_of=""):
    """PURE: both chargebacks as at `as_of` ('' ⇒ everything the feed has).

    THREE STATES, never a bare 0.00:
      · not_configured — no org vocabulary, so nothing is measured and every figure is None;
      · measured with real numbers;
      · measured and genuinely zero — the vocabulary is set but the feed carries no such line, a
        true $0.00 reported as one.
    """
    c = normalise_config(cfg)
    shown = {k: c[k] for k in ("locations", "one_off_names", "recurring_names", "watch_names",
                               "booking")}
    if not c["configured"]:
        return {"state": "not_configured",
                "reason": ("no distributor-chargeback vocabulary is configured for this "
                           "organisation, so nothing is derived and nothing is booked"),
                "as_of": as_of or None, "one_off": None, "recurring": None,
                "combined_total": None, "booking": c["booking"], "booking_wired": False,
                "unbooked_watch": None, "config": shown}

    one_off = _leg(line_rows, c, "one_off", as_of)
    recurring = _leg(line_rows, c, "recurring", as_of)
    recurring["missing_months"] = _missing_months(recurring["lines"])
    # The per-charge amount, only when the feed actually bills one constant amount. A recurring
    # charge that has CHANGED must not be described by a single number, so this is None then.
    amounts = {round(x["amount"], 2) for x in recurring["lines"]}
    recurring["amount_each"] = (amounts.pop() if len(amounts) == 1 else None)

    # CHARGEBACK-SHAPED MONEY THAT IS DELIBERATELY OUT OF SCOPE. Reported, never booked, never
    # absorbed — the owner asked to see what the location scope excludes so they can decide whether
    # any of it belongs. Live house org: $571.05 (three retail stores' $30.00 Return Item
    # Chargebacks billed the same day as A, 19 "early life churn" lines, one commission chargeback).
    watch = []
    for r in (line_rows or []):
        d = as_date(r.get("created_on"))
        if as_of and d and d > as_of:
            continue
        if classify_line(r, c) is not None:
            continue                       # already booked in a leg above — never counted twice
        if _fold(r.get("name")) not in c["_watch"]:
            continue
        watch.append({"date": d, "invoice_number": _t(r.get("invoice_number")),
                      "name": _t(r.get("name")), "location": _t(r.get("location")),
                      "amount": safe_float(r.get("total"))})
    by_loc = {}
    for w in watch:
        b = by_loc.setdefault(w["location"] or "(no location)",
                              {"location": w["location"] or "(no location)",
                               "amount": 0.0, "count": 0})
        b["amount"] = round(b["amount"] + w["amount"], 2)
        b["count"] += 1

    return {
        "state": "measured",
        "reason": None,
        "as_of": as_of or None,
        # A — ONE charge, not a schedule. Its total is what it is and nothing reduces it.
        "one_off": one_off,
        # B — a RECURRING charge that is still running. `first_seen` is the first date the DATA
        # carries, which is the number to check a remembered start date against.
        "recurring": recurring,
        "combined_total": round(one_off["total"] + recurring["total"], 2),
        # Where this books is still an open owner decision, and NOTHING is wired either way.
        "booking": c["booking"],
        "booking_wired": False,
        # DECLARED, NEVER ABSORBED: in no total above, in no P&L leg, and visible with its money.
        "unbooked_watch": {
            "total": round(sum(w["amount"] for w in watch), 2),
            "count": len(watch),
            "by_location": sorted(by_loc.values(), key=lambda x: (-x["amount"], x["location"])),
            "lines": sorted(watch, key=lambda x: (x["date"], x["invoice_number"], x["name"])),
        },
        "config": shown,
    }


def expense_in_period(line_rows, cfg, period_pred):
    """PURE: EVERY configured chargeback billed inside one P&L period, per invoice `location`.

    Both legs book to the same opex line in the month each was billed — A once, in December 2025;
    B in each month it recurs. This is the function the P&L calls; `recurring_in_period` and
    `one_off_in_period` below are the same sweep narrowed to one leg, for reporting.

    `period_pred(row) -> bool` is the caller's — the P&L already owns period matching and this
    module must not grow a second one. Returns {location: amount}; the caller maps location to its
    own store/company grain through the platform's shared resolvers, so no attribution rule is
    re-derived here either."""
    return _in_period(line_rows, cfg, period_pred, ("one_off", "recurring"))


def one_off_in_period(line_rows, cfg, period_pred):
    """PURE: the ONE-OFF charge billed inside one P&L period, per invoice `location`."""
    return _in_period(line_rows, cfg, period_pred, ("one_off",))


def recurring_in_period(line_rows, cfg, period_pred):
    """PURE: the RECURRING charge billed inside one P&L period, per invoice `location`.

    `period_pred(row) -> bool` is the caller's — the P&L already owns period matching and this
    module must not grow a second one. Returns {location: amount}; the caller maps location to its
    own store/company grain through the platform's shared resolvers, so no attribution rule is
    re-derived here either."""
    return _in_period(line_rows, cfg, period_pred, ("recurring",))


def _in_period(line_rows, cfg, period_pred, kinds):
    """PURE: the shared sweep behind the three period readers above — one definition, so the legs
    and their total can never disagree about which rows are in a period."""
    c = normalise_config(cfg)
    out = {}
    if not c["configured"]:
        return out
    for r in (line_rows or []):
        if classify_line(r, c) not in kinds or not period_pred(r):
            continue
        loc = _t(r.get("location"))
        out[loc] = round(out.get(loc, 0.0) + safe_float(r.get("total")), 2)
    return out
