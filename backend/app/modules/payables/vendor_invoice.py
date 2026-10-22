"""ONE home for "what does the DISTRIBUTOR's own feed say is due, at invoice grain".

OWNER REPORT 2026-10-22: *"simlarly Upcoming Invoice Payment Due does not sow any data - some thing
is not working ??"*

WHAT WAS ACTUALLY WRONG. `GET /asset/invoice-due` is built on `commcalc.vip_invoices` — the VIP
Wireless Workbook, a Boost/VIP artefact — and Luxelink has **0** rows in it (measured 2026-10-22).
That module's own docstring already recorded the audit that got it there: the handset ordering feed
(`raw_ma_fulfillment`) carries no invoice number, no due date, no invoice total and no IMEI, so it
cannot source this report, and the audit correctly said to flag that back rather than guess.

THE FEED THAT AUDIT DID NOT REACH is `commcalc.raw_ma_daily_tx` — the processor's own daily
transaction record — and it carries exactly the four fields this report needs: `merchant_invoice`,
`due_date`, `retail_cost` and `order_type`. Measured on Luxelink the same day: **2,115 invoices, all
of them dated, $2,049,592.50**, of which 331 fall due on or after 2026-10-01. The report was not
short of data. It was asking the wrong table.

WHY THIS IS A SHARED HOME AND NOT A SECOND READER. `payables/router._owed_from_vendor_feed` already
reads this table, grouped BY DATE, for the Daily Owed tab. This report needs the same rows grouped BY
INVOICE. Two grains over one source is legitimate; two separately-written notions of which rows count
as a debt is not — a sign convention or an order-type exclusion written into one of them would make
the two disagree about the same money. So the row test and the grouping live here and both read them.

THE INVOICE NUMBER IS NOT ALWAYS THERE, AND THAT IS REPORTED. `merchant_invoice` is `0` on 2,536 of
Luxelink's lines ($606,525.41) spread across 44 different due dates. Grouping those by invoice number
alone would render ONE invoice for $606k due on 44 days at once — a number the feed never stated. So
the key is (invoice number, due date) and a numberless group is labelled as such, grouped by the day
it falls due, which is the only thing the feed actually says about it.

PURE grouping, separate I/O — provable DB-free.
"""
from app.modules.commcalc import date_window as _dw

#: The table the processor's own dated amounts live in. Named once.
VENDOR_FEED_TABLE = "raw_ma_daily_tx"

#: What a numberless invoice is called on screen. Never blank, never "0".
NO_INVOICE_LABEL = "(no invoice number)"


def _money(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _invoice_no(v):
    """The feed's `merchant_invoice` as a label. It arrives as a float (`365966773.0`), and `0` means
    the processor stated no number at all — which is a fact about the row, not an invoice called 0."""
    n = _money(v)
    if n is None or n == 0:
        return ""
    return str(int(n)) if float(n).is_integer() else str(n)


def is_debt_line(row) -> bool:
    """Does this feed row represent money the dealer OWES?

    A positive `retail_cost` is a charge; zero or negative is a credit or a payment TO the dealer and
    is not a payable. This is the same test `_owed_from_vendor_feed` applies for the Daily Owed tab,
    which is why it lives here — a change to it must reach both grains or they stop reconciling.
    """
    amt = _money((row or {}).get("retail_cost"))
    return amt is not None and amt > 0


def group_invoices(rows, date_from="", date_to="") -> list:
    """PURE: feed rows -> one entry per (invoice number, due date), newest due date first.

    Each entry: invoice_number · invoice_label · due_date · lines · amount_due · order_types ·
    product_sample · numbered. Rows outside the window are dropped through the ONE window home, so
    "both ends inclusive" means here what it means everywhere else, and an undated row is fail-closed
    out of a window exactly as it is on every other surface.
    """
    win = _dw.resolve_window(date_from, date_to, 0) if (date_from or date_to) else None
    by = {}
    for r in rows or []:
        if not is_debt_line(r):
            continue
        due = _dw.day(r.get("due_date"))
        if not due:
            continue          # an UNDATED charge cannot be "due" on a date — fail-closed, as everywhere
        if win and not _dw.in_window(due, win["start"], win["end"]):
            continue
        num = _invoice_no(r.get("merchant_invoice"))
        key = (num, due)
        e = by.setdefault(key, {"invoice_number": num or None,
                                "invoice_label": num or NO_INVOICE_LABEL,
                                "numbered": bool(num), "due_date": due or None,
                                "lines": 0, "amount_due": 0.0,
                                "order_types": set(), "product_sample": None})
        e["lines"] += 1
        e["amount_due"] += _money(r.get("retail_cost")) or 0.0
        ot = str(r.get("order_type") or "").strip()
        if ot:
            e["order_types"].add(ot)
        if not e["product_sample"]:
            e["product_sample"] = str(r.get("product_name") or "").strip() or None
    out = []
    for e in by.values():
        e["amount_due"] = round(e["amount_due"], 2)
        e["order_types"] = sorted(e["order_types"])
        out.append(e)
    out.sort(key=lambda x: (x["due_date"] or "", x["invoice_label"]), reverse=True)
    return out


def summarize(entries) -> dict:
    """Totals over `group_invoices` output, including how much of it the feed left unnumbered — a
    reader comparing this to a distributor statement needs that figure named, not buried."""
    amt = round(sum(e["amount_due"] for e in entries or []), 2)
    unnumbered = [e for e in entries or [] if not e["numbered"]]
    return {"invoice_count": len(entries or []), "amount_due": amt,
            "line_count": sum(e["lines"] for e in entries or []),
            "unnumbered_invoices": len(unnumbered),
            "unnumbered_amount": round(sum(e["amount_due"] for e in unnumbered), 2)}


def fetch_feed_rows(client, org_id, date_from="", date_to=""):
    """The feed's dated charge rows. I/O ONLY — every rule above is applied by the pure functions.
    Never raises: a tenant without the table gets an empty list and the caller says so."""
    from app.modules.core import feed_read as _feed_read
    win = _dw.resolve_window(date_from, date_to, 0) if (date_from or date_to) else None

    def _make():
        # A FRESH builder per page; the stable paging order is the one home's, because a paged read
        # with no total order serves some rows twice and others never (§19.48 / core/feed_read.py).
        q = (client.schema("commcalc").table(VENDOR_FEED_TABLE)
             .select("merchant_invoice,due_date,retail_cost,order_type,product_name,"
                     "account_name,tx_date,order_number")
             .eq("org_id", org_id).not_.is_("due_date", "null"))
        if win:
            q = _dw.apply_window(q, "due_date", win)
        return q

    try:
        return _feed_read.read_all(_make)
    except Exception as e:
        print(f"WARN vendor_invoice fetch_feed_rows failed: {e}")
        return []
