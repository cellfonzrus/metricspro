"""ATU (autopay) opportunity — how many card-paying customers are NOT enrolled, and what that costs.

Owner directive 2026-08-12: "a report of the number of customers paying on a credit card vs those doing
ATU, so ATU / no of customers paying by credit, to have there a percent of revenue loss for not doing
the ATU ... note the saving numbers and the income numbers will be entered by the user as they will
change."

PURE — no DB, no I/O. Rows come in from the router's reader; every number here is derived arithmetic, so
it is unit-testable against fixtures and against production rows without a live client.

THREE THINGS THIS MODULE EXISTS TO GET RIGHT
① THE AUTOPAY MARKER HAS A BLANK DEPARTMENT. Enrolment is a $0 `product_desc = 'Autopay'` LINE, and it
  carries no department and no category. So the flag MUST be reduced per transaction across ALL of that
  transaction's rows. Filtering rows by department before grouping deletes the marker and returns a
  confident 0% attach for every store — a wrong answer that looks like a finding. (Cost one wrong answer
  during the 2026-08-12 analysis; see [[autopay-marker-blank-department-trap]].)

② CUSTOMERS ARE NOT TRANSACTIONS, AND THE TWO DENOMINATORS DIFFER ON PURPOSE. `customer_no` is empty in
  this feed (6 non-null rows in a full month), so the customer key is `mdn` — which is also the correct
  unit, because autopay attaches per LINE. But only ~38% of rows carry an MDN, so a customer COUNT and a
  recharge TOTAL cannot come from the same population without understating one of them. Counts are
  quoted per identified line; the recharge base is quoted per transaction. Both are returned, labelled,
  and never silently mixed.

③ CARD MEANS "AN INSTRUMENT IS ON FILE", INCLUDING SPLIT TENDERS. `Cash; Credit Card` is a card
  customer — they have a card. Also note the feed spells it `Externel Credit Card`; matching is on the
  substring, not an enumerated list, so the typo cannot drop a whole tender class.

WHAT THIS DELIBERATELY DOES NOT CLAIM
Enrolment is observed at ACTIVATION only. A customer who activates without autopay and enrols later
through the carrier app never re-enters this feed. So this is the COUNTER conversion rate and the open
position is an UPPER BOUND on the opportunity, not a headcount of people to call. The report says so.
"""

CARD_TOKENS = ("credit card", "debit card")
AUTOPAY_MARKER = "autopay"
RTR_PREFIX = "boost rtr"


def _s(v):
    return (str(v).strip() if v is not None else "")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def is_card(tender):
    """True when the tender string names a card at all — including split tenders like 'Cash; Credit
    Card'. Substring match, not an enumeration: the feed spells one value 'Externel Credit Card', and an
    allow-list would silently drop ~12k transactions a month over a typo."""
    t = _s(tender).lower()
    return any(tok in t for tok in CARD_TOKENS)


def fold_transactions(rows):
    """Reduce raw line-item rows to ONE record per trans_id.

    This is the step the whole report depends on: the Autopay marker is a department-less $0 line, so
    enrolment can only be seen by OR-ing across every line of the transaction. Returns a list of
    {trans_id, store, trans_date, tender, card, atu, mdn, rtr, activation}.
    """
    tx = {}
    for r in rows:
        tid = _s(r.get("trans_id"))
        if not tid:
            continue
        t = tx.get(tid)
        if t is None:
            t = tx[tid] = {"trans_id": tid, "store": _s(r.get("store")), "trans_date": _s(r.get("trans_date")),
                           "tender": "", "atu": False, "mdn": "", "rtr": 0.0, "activation": False}
        # tender/store/date repeat on every line; keep the first non-empty
        if not t["tender"] and _s(r.get("tender_type")):
            t["tender"] = _s(r.get("tender_type"))
        if not t["store"] and _s(r.get("store")):
            t["store"] = _s(r.get("store"))
        if not t["trans_date"] and _s(r.get("trans_date")):
            t["trans_date"] = _s(r.get("trans_date"))
        if not t["mdn"] and _s(r.get("mdn")):
            t["mdn"] = _s(r.get("mdn"))
        desc = _s(r.get("product_desc"))
        if desc.strip().lower() == AUTOPAY_MARKER:
            t["atu"] = True
        if desc.lower().startswith(RTR_PREFIX):
            t["rtr"] += _f(r.get("ext_price"))
        if _s(r.get("contract_type")):
            t["activation"] = True
    for t in tx.values():
        t["card"] = is_card(t["tender"])
    return list(tx.values())


def summarize(txs, saving_per_month, boost_rate_pct, total_rate_pct, total_recharge_base):
    """The report. `txs` are folded transactions; the four assumptions are the tenant's own config.

    Rates arrive as PERCENTS (5 = 5%). Returns counts on both the activation and the customer (MDN)
    basis, the recharge base, and the money — every figure derived, none hard-coded.
    """
    br = _f(boost_rate_pct) / 100.0
    tr = _f(total_rate_pct) / 100.0
    save = _f(saving_per_month)
    tbase = _f(total_recharge_base)

    acts = [t for t in txs if t["activation"]]
    card_acts = [t for t in acts if t["card"]]
    noncard_acts = [t for t in acts if not t["card"]]

    # Customer basis: distinct MDN. A line is "enrolled" if ANY of its transactions carried the marker —
    # one enrolment is enough, and counting it once per transaction would inflate the attach rate.
    card_lines, card_lines_atu = set(), set()
    noncard_lines, noncard_lines_atu = set(), set()
    for t in txs:
        m = t["mdn"]
        if not m:
            continue
        if t["card"]:
            card_lines.add(m)
            if t["atu"]:
                card_lines_atu.add(m)
        else:
            noncard_lines.add(m)
            if t["atu"]:
                noncard_lines_atu.add(m)
    # A line seen on both a card and a cash visit counts as a card customer — the instrument exists.
    noncard_lines -= card_lines
    noncard_lines_atu -= card_lines_atu

    card_rtr = sum(t["rtr"] for t in txs if t["card"])
    card_rtr_open = sum(t["rtr"] for t in txs if t["card"] and not t["atu"])

    boost_carry = card_rtr_open * br
    total_carry = tbase * tr
    open_customers = len(card_lines) - len(card_lines_atu)

    def pct(n, d):
        return round(100.0 * n / d, 1) if d else 0.0

    return {
        "assumptions": {"saving_per_month": save, "boost_rate_pct": _f(boost_rate_pct),
                        "total_rate_pct": _f(total_rate_pct), "total_recharge_base": tbase},
        # Customer (MDN) basis — the owner's "number of customers".
        "customers": {
            "card": len(card_lines), "card_on_atu": len(card_lines_atu),
            "card_open": open_customers, "card_attach_pct": pct(len(card_lines_atu), len(card_lines)),
            "noncard": len(noncard_lines), "noncard_on_atu": len(noncard_lines_atu),
            "noncard_attach_pct": pct(len(noncard_lines_atu), len(noncard_lines)),
        },
        # Activation basis — complete, since it does not need an MDN.
        "activations": {
            "card": len(card_acts), "card_on_atu": sum(1 for t in card_acts if t["atu"]),
            "card_attach_pct": pct(sum(1 for t in card_acts if t["atu"]), len(card_acts)),
            "noncard": len(noncard_acts), "noncard_on_atu": sum(1 for t in noncard_acts if t["atu"]),
            "noncard_attach_pct": pct(sum(1 for t in noncard_acts if t["atu"]), len(noncard_acts)),
        },
        "recharge": {"card_total": round(card_rtr, 2), "card_open": round(card_rtr_open, 2)},
        "money": {
            "boost_carry_monthly": round(boost_carry, 2),
            "total_carry_monthly": round(total_carry, 2),
            "carry_monthly": round(boost_carry + total_carry, 2),
            "carry_annual": round((boost_carry + total_carry) * 12, 2),
            "customer_savings_monthly": round(open_customers * save, 2),
            # The owner's headline: what share of the card recharge base we forgo by not converting.
            "pct_of_card_recharge_forgone": pct(card_rtr_open, card_rtr),
        },
        "totals_measurable": {
            "boost": True,
            # Stated, not implied: the Total figure is only as good as the number a human typed.
            "total": tbase > 0,
            "total_note": ("Total recharges settle through VidaPay and do not reach the POS export, so the "
                           "Total side uses the recharge base entered in Settings. It reads $0 until one "
                           "is supplied."),
        },
    }


def by_store(txs, boost_rate_pct):
    """Per-store card/ATU/open + the recharge left on the table, ordered by the biggest open position."""
    br = _f(boost_rate_pct) / 100.0
    acc = {}
    for t in txs:
        if not t["card"]:
            continue
        s = t["store"] or "(unknown)"
        a = acc.get(s)
        if a is None:
            a = acc[s] = {"store": s, "card_acts": 0, "card_atu": 0, "rtr_open": 0.0}
        if t["activation"]:
            a["card_acts"] += 1
            if t["atu"]:
                a["card_atu"] += 1
        if not t["atu"]:
            a["rtr_open"] += t["rtr"]
    out = []
    for a in acc.values():
        a["card_open"] = a["card_acts"] - a["card_atu"]
        a["attach_pct"] = round(100.0 * a["card_atu"] / a["card_acts"], 1) if a["card_acts"] else 0.0
        a["rtr_open"] = round(a["rtr_open"], 2)
        a["carry_forgone"] = round(a["rtr_open"] * br, 2)
        out.append(a)
    out.sort(key=lambda r: (-r["carry_forgone"], -r["card_open"], r["store"]))
    return out
