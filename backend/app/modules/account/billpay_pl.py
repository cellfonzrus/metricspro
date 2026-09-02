"""Bill-pay pass-through P&L presentation — PURE logic (owner directive 2026-09-02, item 5;
mig 939).

The owner's words, verbatim: "a separate box should assigned to the p&l to account for the cash /
credit epay assignment. technically the total cash collected and the credit collected comprises of
the total revenue, then billpay is deducted from it as it is not income and is offset by either
the cash deposited in the bank to cover the payments or by the commission received, different
carriers do it in a different way, boost pays commission and deducts payments separately, total
deducts the payment for the phones and bill payments from the commission earned."

WHAT THIS BOOKS. Bill payments the store collects (the reps' declared ePay/VidaPay split on the
daily closing sheet — `daily_closing.epay_on_cash` + `epay_on_credit`, the DM's verified
correction winning at store-day grain) are PASS-THROUGH, not income. Under the org's
`pl_billpay_presentation = 'carveout'` the P&L shows them explicitly as a matched PAIR of
revenue-section lines that always nets to ZERO:

    billpay_collected   +X   "Bill payments collected (pass-through)"
    billpay_offset      −X   label per the org's settlement convention (below)

Gross revenue therefore shows the collected volume the owner asked to see, while gross profit and
net income are IDENTICAL to the 'off' default — bill-pay dollars never masquerade as income, and
the actual commission earned keeps booking on its own lines (`carrier_comm` etc.) exactly as the
carrier pays it.

SETTLEMENT CONVENTION — PER-ORG CONFIG, NEVER CARRIER NAMES IN CODE (RULE TWO). The offset line's
LABEL states how this org's processor settles the pass-through:
    'remit_separate'      — commission is paid separately and the collected payments are remitted
                            / auto-debited separately (the owner's "boost style").
    'net_from_commission' — the processor nets phone + bill-pay payments out of the commission it
                            owes (the owner's "total style").
Both conventions book the same ±X pair (the dollars the store collected ARE offset either way);
the convention is presentation vocabulary, stored per org in
`commission_org_config.pl_billpay_settlement`.

House default `pl_billpay_presentation='off'` books NOTHING — every org is byte-identical until
it opts in (mig 939 gated seed). Proof: backend/harness_billpay_pl.py.
"""
from app.modules.commcalc.calculator import safe_float

PRESENTATIONS = ("off", "carveout")
SETTLEMENTS = ("remit_separate", "net_from_commission")

COLLECTED_KEY = "billpay_collected"
OFFSET_KEY = "billpay_offset"

OFFSET_LABELS = {
    "remit_separate": "Bill payments remitted to processor (pass-through)",
    "net_from_commission": "Bill payments netted from carrier commission (pass-through)",
}

# The six DM-corrected fields' bill-pay pair (store-day grain, verified_overlay vocabulary).
_DM_CASH, _DM_CC = "dm_epay_cash", "dm_epay_cc"


def default_config():
    return {"presentation": "off", "settlement": "remit_separate"}


def load_config(client, org_id):
    """Per-org bill-pay presentation config, ADAPTIVE (pre-mig-939 schema or no row ⇒ defaults).
    NEVER raises — each column its own defensive read (coa._account_config posture)."""
    cfg = default_config()
    try:
        rows = (client.schema("commcalc").table("commission_org_config")
                .select("pl_billpay_presentation,pl_billpay_settlement")
                .eq("org_id", org_id).limit(1).execute().data) or []
        if rows:
            p = str(rows[0].get("pl_billpay_presentation") or "").strip().lower()
            if p in PRESENTATIONS:
                cfg["presentation"] = p
            s = str(rows[0].get("pl_billpay_settlement") or "").strip().lower()
            if s in SETTLEMENTS:
                cfg["settlement"] = s
    except Exception:
        pass
    return cfg


def billpay_cells(closing_rows, ver_by_store_day=None):
    """PURE: per-store bill-pay dollars collected, from the reps' declared ePay split on the
    daily closing rows (`epay_on_cash` + `epay_on_credit`), with the DM's VERIFIED correction
    winning at store-day grain (dm_epay_cash / dm_epay_cc replace that store-day's rep-summed
    split — the TKT-1030 rule: a verified DM figure is authoritative). Returns
    (cells {store_code: {'cash': x, 'credit': y}}, meta)."""
    by_sd = {}
    for r in closing_rows or []:
        r = r or {}
        st = (str(r.get("store_code") or "").strip()) or None
        d = str(r.get("close_date") or "")[:10]
        if not st or not d:
            continue
        slot = by_sd.setdefault((st, d), {"cash": 0.0, "credit": 0.0})
        slot["cash"] = round(slot["cash"] + safe_float(r.get("epay_on_cash")), 2)
        slot["credit"] = round(slot["credit"] + safe_float(r.get("epay_on_credit")), 2)
    corrected_days = 0
    for (st, d), slot in by_sd.items():
        v = (ver_by_store_day or {}).get((st, d))
        if not v or not v.get("verified"):
            continue
        touched = False
        if v.get(_DM_CASH) is not None:
            slot["cash"] = round(safe_float(v.get(_DM_CASH)), 2)
            touched = True
        if v.get(_DM_CC) is not None:
            slot["credit"] = round(safe_float(v.get(_DM_CC)), 2)
            touched = True
        if touched:
            corrected_days += 1
    cells = {}
    for (st, _d), slot in by_sd.items():
        c = cells.setdefault(st, {"cash": 0.0, "credit": 0.0})
        c["cash"] = round(c["cash"] + slot["cash"], 2)
        c["credit"] = round(c["credit"] + slot["credit"], 2)
    total = round(sum(c["cash"] + c["credit"] for c in cells.values()), 2)
    meta = {"stores": len(cells), "store_days": len(by_sd),
            "dm_corrected_days": corrected_days, "total": total}
    return cells, meta


def billpay_bookings(cells, cfg):
    """PURE: the matched ± pair per store. Returns (bookings, offset_label) where bookings =
    [(line_key, store, amount, detail_label)] — collected positive, offset negative, EQUAL by
    construction (net income can never move; harness-proven). presentation 'off' ⇒ ([], None)."""
    if (cfg or {}).get("presentation") != "carveout":
        return [], None
    label = OFFSET_LABELS.get((cfg or {}).get("settlement") or "", OFFSET_LABELS["remit_separate"])
    out = []
    for st in sorted(cells or {}):
        c = cells[st]
        cash, credit = safe_float(c.get("cash")), safe_float(c.get("credit"))
        if cash:
            out.append((COLLECTED_KEY, st, round(cash, 2), "Bill payments on cash"))
        if credit:
            out.append((COLLECTED_KEY, st, round(credit, 2), "Bill payments on credit/card"))
        tot = round(cash + credit, 2)
        if tot:
            out.append((OFFSET_KEY, st, round(-tot, 2), label))
    return out, label
