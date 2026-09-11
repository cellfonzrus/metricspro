"""DEVICE PURCHASES FROM THE DISTRIBUTOR — what we were BILLED in a period, by company and by store.

Owner directive 2026-09-11: *"a permanent report in the finance menu giving the cost of all phones/
devices purchased from [the distributor], segregated by company and by store"* — asked for 2025
first, built as a period-ranged report (the feed carries 2024, 2025 and 2026).

═══════════════ THIS IS PURCHASES. IT IS NOT COGS. THEY WILL NOT TIE, AND THAT IS CORRECT ═════════
Owner, explicitly: *"build it as purchases, keep it separate from cogs."* Two different questions,
two different answers, both right:

  • THIS REPORT  — "what did the distributor BILL US in this window?"  Source: the invoice LINES
    (`commcalc.vip_invoice_lines`). Recognised on the INVOICE date. Every billed unit counts,
    whether it has since sold, is still on the shelf, or was never activated.
  • `account/device_cogs.py` — "what did the units we SOLD cost us?"  IMEI-deduped, invoice-first
    with a sale-time fallback, consignment/`asset_ledger`-aware, recognised at SALE. Unsold stock is
    a balance-sheet asset there, not an expense.

A device bought in December and sold in February is in THIS report's December and in device_cogs'
February. Reconciling the two and concluding one is broken is the mistake this paragraph exists to
prevent; the page states it too, in one line, naming the other report. **Nothing here books to the
P&L or the Balance Sheet, and nothing here writes anything.** `device_cogs` is not touched.

═══════════════ WHAT COUNTS AS A DEVICE IS ANSWERED BY DATA, NEVER BY A PRODUCT NAME ══════════════
A line is a DEVICE when its trimmed `name` also appears as a trimmed `product_name` in
`commcalc.vip_invoice_devices` — i.e. that product actually arrived as a SERIALISED unit with a
serial/IMEI. Measured on the house org's 2025 feed: 71 of 89 distinct line names qualify.

That is RULE TWO, not cleverness. The obvious alternative — `name LIKE '<carrier>%'` — puts one
carrier's branding in a report definition, and the product names in this feed are full of exactly
that branding. The data already knows which products are devices; asking it means the definition
keeps working for the next distributor, the next carrier and next year's SKUs with no edit.

  · TABLETS ARE IN. A tablet is a serialised device and is billed as one (house 2025: the
    "…Celero 5G TAB" family, ~$94.8k). The report does not silently exclude them — the product-grain
    table names every device product with its own dollars, so a reader can see and separate them,
    and the page says so out loud.
  · Non-device lines (SIM packs, Managed Services, Return Item Chargeback, Airtime ACH Return,
    Xfinity activations — house 2025: $296,508.59) are EXCLUDED from the headline and REPORTED
    beside it with their money, so "all lines" always reconciles to "devices + non-devices".
  · Line money is the LINE total. Invoice-level shipping / other cost / tax are not device purchase
    price and are not here — the P&L already books them (`coa.build_inputs` → `vip_fees`).

═══════════════ A VOIDED INVOICE IS NOT A PURCHASE ════════════════════════════════════════════════
An invoice whose header `status` says VOIDED was cancelled. Its lines and its serialised units stay
in the feed, and until 2026-09-11 this report counted them as purchases. It no longer does.

Measured house org (six voided invoices in the feed): the 2025 device figure falls from
$7,099,841.56 to **$7,090,741.82** ($9,099.74 over 5 lines, invoice 1595336 of 2025-11-20) and the
non-device figure from $296,508.59 to **$296,418.59** ($90.00). **2024 moves too, and by more**:
device $4,917,452.96 → $4,896,463.66 ($20,989.30 over 22 lines on four invoices all dated
2024-11-27). 2026 is unaffected.

NOTHING IS SILENTLY DROPPED — the excluded money is REPORTED, per invoice, in `excluded_voided`,
so "what we were billed" still reconciles to "what we count plus what we voided".

THE SAME RULE BINDS THE DEVICE PAYABLE REPORT (§23z), which imports `VOID_STATUSES` and
`voided_invoice_set` FROM HERE rather than keeping its own. Two finance reports disagreeing about
voided invoices is the defect this shares a definition to prevent: someone reconciles them one day
and both lose credibility. There is exactly one voided rule in the platform and this is it.

The status is read from `commcalc.vip_invoices` — the HEADER is what a status means.
`vip_invoice_lines.status` was checked against it and agrees on 12,536 of 12,536 rows, but this
feed's column names have lied before, so the authoritative table is the one that is read.

═══════════════ STORE AND COMPANY COME FROM THE PLATFORM'S ONE RESOLVER ═══════════════════════════
DUPLICATE CHECK (build gate): this module owns NO store→store or store→company derivation, and it
CHANGES NEITHER. It calls `coa.store_resolver` (§13/§13a) and `coa.build_company_matcher` (§13b)
EXACTLY as they are, so this report and the P&L can never disagree about which store or which company
a location belongs to, and no existing finance figure can move because of this report's existence.

THE RESOLVERS WERE NOT EXTENDED, AND THAT WAS THE RIGHT CALL. The feed drifts against our own
spellings — it writes "1 S 60th St" where store_mapping holds "1 S 60th street", "1598 Mt Ephraim Ave"
for "1598 Mount Ephraim Ave", "5135 Bergenline Ave" for "5135 Bergenline". A first draft of this
module added a spelling-normalisation step to `coa.store_resolver`. That was withdrawn: those two
functions are shared by the P&L and the Balance Sheet, a new matching step changes WHICH STORE money
books to, and no report is worth silently re-attributing the books. `store_resolver`'s existing chain
(exact → alias → code → unambiguous leading street number) already reaches most of this drift, and
where it does not, the honest answer is the house mechanism: a `commcalc.store_aliases` row, which
makes the match DELIBERATE and per-store instead of changing a rule for every tenant at once. This
report's job is to make those locations VISIBLE (see the resolution ledger below) so the owner can
decide; it is not to move the rule under the books.

THE THREE-STATE ANSWER IS TAKEN FROM THE RESOLVERS' OUTPUT, not re-derived from their inputs:
  · a store resolved when `coa.store_resolver`'s answer IS one of the org's `store_mapping` addresses
    (that set is a read of an existing table, not a second chain) — whatever step got it there;
  · a company is ASSIGNED when the pure `coa.build_company_matcher(rows, default_id=None)` — the very
    same function, called with no fallback — returns an id. `None` then means "no assignment row
    matched", which the booking call (`default_id` supplied) cannot tell you because it answers with
    the default. Both matchers are built from ONE read of `store_companies`, so they cannot disagree.

═══════════════ NOTHING IS EVER SILENTLY DROPPED ══════════════════════════════════════════════════
Measured / genuinely zero / not measured — a missing figure is never rendered $0.00:
  · a `location` no store vocabulary matches — including a distributor MASTER/DEALER ACCOUNT
    address, which is a legal entity and not a retail location → the "(store not mapped)" bucket,
    which LISTS each raw location with its own dollars, so it is READ, not lost, and never counted
    as some store's device spend;
  · a store with no `store_companies` assignment row → the "(company not mapped)" bucket. It is NOT
    printed as the org's Default Company: the default is a booking fallback, and a report that
    prints it is stating a fact it does not have;
  · a row whose month cannot be read → counted into its year and declared in
    `meta.month_unknown`, never quietly included in a partial-year window nor quietly dropped;
  · `meta.resolution` counts every row by how its store was placed (exact / resolver / unmapped), so
    the day store setup or the shared resolver changes, a bucket moves and someone can see it.

    THAT LEDGER IS ALSO A SETUP REPORT, and it should be read as one. Money in the `resolver` bucket
    reached its store by a route other than the feed spelling our address our way — in this feed,
    usually the resolver's unambiguous LEADING STREET NUMBER step, which compares the street NUMBER
    and never the street NAME. That is a right answer by a coincidence-prone route. The house fix is
    a `commcalc.store_aliases` row, which makes the match deliberate, per store, and moves nothing
    for any other tenant. Live example, house org 2026-09-11: the feed writes `5135 Bergenline Ave`
    where `store_mapping` holds `5135 Bergenline` (store B-5135, market NJ, company assigned) — the
    suffix is ABSENT from our spelling, not merely abbreviated. The report's job is to surface that
    row with its dollars so the owner can add the alias; it is NOT to loosen the shared matching rule
    under the P&L to make one feed tidier.

PURE except `assemble()`: every function below is stdlib-only math over rows handed to it.
Proof: backend/harness_device_purchases.py.
"""
from app.modules.commcalc.calculator import safe_float

# Labelled buckets — an unresolved row is a ROW WITH A LABEL, never a row that vanished.
STORE_NOT_MAPPED = "(store not mapped)"
COMPANY_NOT_MAPPED = "(company not mapped)"

# How a location reached a store, judged from `coa.store_resolver`'s ANSWER (we never re-walk its
# chain — that would be a second resolver):
#   exact    the feed already writes the store the way we hold it;
#   resolver the resolver got there another way (alias / store code / unambiguous leading street
#            number). The money is placed and the route is FLAGGED — a leading-number match compares
#            the street NUMBER and never the street NAME, so it is a right answer by a
#            coincidence-prone route. The house fix is a `commcalc.store_aliases` row, which makes
#            that match deliberate; this bucket is how the owner finds the rows worth an alias;
#   unmapped nothing in the store vocabulary matched at all.
RESOLUTION_KINDS = ("exact", "resolver", "unmapped")


# WHICH INVOICE-HEADER STATUSES MEAN "this invoice was cancelled". House default; a tenant whose
# distributor spells it differently overrides it in config rather than in code (RULE TWO). Matching
# is case-folded, so spelling drift in the feed cannot silently start counting voided money again.
#
# THIS IS THE PLATFORM'S ONE VOIDED RULE. The Device Payable report (§23z) imports this name and
# `voided_invoice_set` below instead of defining its own, so the two reports cannot drift apart on
# what "voided" means — which is the whole reason the rule lives in one module.
VOID_STATUSES = ("voided",)


def _t(v):
    """Trimmed text, or ''. The feed's own `btrim` — the join key on both sides."""
    return str(v or "").strip()


def voided_invoice_set(invoice_rows, void_statuses=VOID_STATUSES):
    """PURE: the invoice numbers whose HEADER says the invoice was cancelled.

    Header, not line: a status is a property of the invoice. (`vip_invoice_lines.status` was
    measured against the header and agrees on 12,536 of 12,536 rows, but agreeing today is not the
    same as being the authority, and this feed's column names have lied before.)"""
    want = {str(v).strip().lower() for v in (void_statuses or ()) if str(v).strip()}
    return {_t(r.get("invoice_number")) for r in (invoice_rows or [])
            if _t(r.get("invoice_number")) and _t(r.get("status")).lower() in want}


# ── WHAT IS A DEVICE (pure) ───────────────────────────────────────────────────────────────────────
def device_product_names(device_rows):
    """PURE: the serialised-device product vocabulary — every trimmed `product_name` that actually
    arrived on a serial/IMEI. Returns (exact, casefolded) so a line can be classified on the exact
    spelling (the measured definition) while a CASE-ONLY drift is still detectable rather than
    silently excluded."""
    exact = {_t(r.get("product_name")) for r in (device_rows or []) if _t(r.get("product_name"))}
    return exact, {n.casefold() for n in exact}


def classify_line(name, exact_names, ci_names):
    """PURE: 'device' | 'device_case_drift' | 'non_device' for one line name.

    'device_case_drift' means the name matches a real serialised product except for CASE. It is
    counted AS A DEVICE (it is one) and reported separately in `meta`, because a feed that starts
    case-drifting is a thing to notice, not a thing to absorb."""
    n = _t(name)
    if not n:
        return "non_device"
    if n in exact_names:
        return "device"
    if n.casefold() in ci_names:
        return "device_case_drift"
    return "non_device"


# ── WHERE A LOCATION LANDED (pure, over `coa.store_resolver`'s OUTPUT) ───────────────────────────
def store_addresses(mapping_rows):
    """PURE: the org's REAL retail-store addresses out of `commcalc.store_mapping` rows.

    A row with NO `store_code` is dropped. Those rows name a legal or dealer ACCOUNT rather than a
    location — live house org: the distributor's master dealer ("Cellular Services Dot net LLC",
    228 N Wood Ave), whose invoices are chargebacks, NSF fees and loans, not a store's device
    purchases. The platform already treats a codeless row as "contributes no store key"
    (`flag_store_resolver`, pinned in harness_flag_store_resolver §A10); this report holds the same
    line, so a master-account invoice can never be counted as some retail store's device spend.
    The signal is DATA (a missing code), never an account name written in code."""
    return [str(r.get("store_address") or "").strip() for r in (mapping_rows or [])
            if str(r.get("store_address") or "").strip()
            and str(r.get("store_code") or "").strip()]



def store_placer(resolve, known_addresses):
    """PURE: wrap the org's REAL resolver into `place(raw) -> (store_label, how)`.

    `resolve` is `coa.store_resolver(client, org_id)` verbatim — called, never re-implemented.
    `known_addresses` is the org's `store_mapping` address set. The resolver's contract (its own
    docstring) is that every matching step lands on an address ALREADY in store_mapping and an
    unmappable string comes back cleaned-but-unchanged; so membership in that set IS the answer to
    "did this resolve?", with no knowledge of which step ran. That is the whole point: the step order
    can change under us and this classifier stays correct."""
    known = {str(a or "").strip().lower(): str(a or "").strip() for a in (known_addresses or [])}

    def place(raw):
        r = _t(raw)
        if not r:
            return (None, "unmapped")
        addr = _t(resolve(r))
        canon = known.get(addr.lower())
        if canon is None:
            return (None, "unmapped")
        return (canon, "exact" if r.lower() == canon.lower() else "resolver")

    return place


# ── THE PERIOD WINDOW (pure) ──────────────────────────────────────────────────────────────────────
def parse_month(v):
    """PURE: 'YYYY-MM' (or 'YYYY-MM-DD') → (year, month); None when unreadable."""
    s = _t(v)
    if len(s) < 7 or s[4] not in "-/":
        return None
    try:
        y, m = int(s[:4]), int(s[5:7])
    except ValueError:
        return None
    return (y, m) if 1 <= m <= 12 and 1900 <= y <= 2999 else None


def window_years(win):
    """PURE: the inclusive list of period_year values a window touches — the server's read filter."""
    (y0, _m0), (y1, _m1) = win
    return list(range(min(y0, y1), max(y0, y1) + 1))


def in_window(year, month, win):
    """PURE: is (year, month) inside the inclusive window ((y0,m0),(y1,m1))?

    `month` None/0 (the feed did not carry one) is IN when the row's YEAR is in the window's year
    span — the row is real money and excluding it would be a silent drop. On a partial-year window
    that is a declared over-inclusion, which is why every such row is also counted in
    `meta.month_unknown` with its dollars."""
    (y0, m0), (y1, m1) = win
    try:
        y = int(year)
    except (TypeError, ValueError):
        return False
    if not month:
        return min(y0, y1) <= y <= max(y0, y1)
    try:
        m = int(month)
    except (TypeError, ValueError):
        return min(y0, y1) <= y <= max(y0, y1)
    return (y0 * 12 + m0) <= (y * 12 + m) <= (y1 * 12 + m1)


# ── THE AGGREGATION (pure) ────────────────────────────────────────────────────────────────────────
def aggregate(line_rows, device_rows, win, place_store, company_if_assigned,
              company_names=None, market_of=None, voided_invoices=None):
    """PURE: the whole report payload from rows + the two RESOLVERS (passed in as functions, so this
    math is provable with no database and the resolvers stay the platform's shared ones).

      line_rows     — commcalc.vip_invoice_lines rows (name, total, quantity, location,
                      period_year, period_month, invoice_number, status)
      device_rows   — commcalc.vip_invoice_devices rows (product_name, period_year, period_month)
      win           — ((year, month), (year, month)) inclusive
      place_store   — `store_placer(coa.store_resolver(...), known)` : location -> (store, how)
      company_if_assigned — `coa.build_company_matcher(rows, None)`  : store -> company_id or None
                      (the SAME pure matcher the books use, with the default fallback withheld so
                      "assigned" and "fell back to the default" stay distinguishable)
      company_names — {company_id: name}
      market_of     — optional store -> market (core.scope.store_market_resolver, §13a)
      voided_invoices — `voided_invoice_set(...)`: invoice numbers whose header says VOIDED. Their
                      lines are EXCLUDED from every figure and REPORTED in `excluded_voided`, so the
                      money is visible rather than merely absent. None ⇒ exclude nothing.
    """
    exact_names, ci_names = device_product_names(device_rows)
    names = company_names or {}
    mk = market_of or (lambda _s: "")

    cells = {}                      # (company_label, store_label) -> bucket
    products = {}                   # device product name -> bucket
    non_device = {}                 # non-device line name -> bucket
    unmapped_locations = {}         # raw location -> bucket
    resolution = {k: {"lines": 0, "amount": 0.0} for k in RESOLUTION_KINDS}
    invoices = set()
    tot = {"device_amount": 0.0, "device_units": 0.0, "device_lines": 0,
           "non_device_amount": 0.0, "non_device_lines": 0}
    month_unknown = {"lines": 0, "amount": 0.0}
    case_drift = {"lines": 0, "amount": 0.0, "names": set()}

    voided = set(voided_invoices or ())
    excluded = {}                   # voided invoice number -> what it would have contributed
    for r in (line_rows or []):
        if not in_window(r.get("period_year"), r.get("period_month"), win):
            continue
        amt = safe_float(r.get("total"))
        qty = safe_float(r.get("quantity"))
        name = _t(r.get("name"))
        kind = classify_line(name, exact_names, ci_names)
        inv = _t(r.get("invoice_number"))
        # ── A VOIDED INVOICE IS NOT A PURCHASE. Excluded from every figure — and kept, with its
        # money, so the exclusion is auditable instead of being a number that quietly went missing.
        if inv and inv in voided:
            e = excluded.setdefault(inv, {"invoice_number": inv,
                                          "location": _t(r.get("location")),
                                          "device_amount": 0.0, "non_device_amount": 0.0,
                                          "lines": 0, "units": 0.0})
            if kind == "non_device":
                e["non_device_amount"] += amt
            else:
                e["device_amount"] += amt
                e["units"] += qty
            e["lines"] += 1
            continue
        if inv:
            invoices.add(inv)
        if not r.get("period_month"):
            month_unknown["lines"] += 1
            month_unknown["amount"] += amt

        if kind == "non_device":
            tot["non_device_amount"] += amt
            tot["non_device_lines"] += 1
            b = non_device.setdefault(name or "(unnamed line)",
                                      {"name": name or "(unnamed line)", "amount": 0.0, "lines": 0})
            b["amount"] += amt
            b["lines"] += 1
            continue

        # ── a DEVICE line ────────────────────────────────────────────────────────────────────────
        if kind == "device_case_drift":
            case_drift["lines"] += 1
            case_drift["amount"] += amt
            case_drift["names"].add(name)
        tot["device_amount"] += amt
        tot["device_units"] += qty
        tot["device_lines"] += 1

        raw_loc = _t(r.get("location"))
        addr, how = place_store(raw_loc)
        resolution[how]["lines"] += 1
        resolution[how]["amount"] += amt
        if addr is None:
            store_label = STORE_NOT_MAPPED
            u = unmapped_locations.setdefault(
                raw_loc or "(no location on the invoice line)",
                {"location": raw_loc or "(no location on the invoice line)",
                 "amount": 0.0, "units": 0.0, "lines": 0})
            u["amount"] += amt
            u["units"] += qty
            u["lines"] += 1
            company_label, company_id = COMPANY_NOT_MAPPED, None
        else:
            store_label = addr
            cid = company_if_assigned(store_label)
            if cid is None:                    # no assignment row matched — say so, do not print
                company_label, company_id = COMPANY_NOT_MAPPED, None   # the default company
            else:
                company_id = cid
                company_label = names.get(cid) or names.get(str(cid)) or str(cid)

        key = (company_label, store_label)
        c = cells.setdefault(key, {
            "company": company_label, "company_id": company_id, "store": store_label,
            "market": (mk(store_label) or "") if store_label != STORE_NOT_MAPPED else "",
            "resolved_by": how,
            "amount": 0.0, "units": 0.0, "lines": 0})
        c["amount"] += amt
        c["units"] += qty
        c["lines"] += 1

        p = products.setdefault(name, {"name": name, "amount": 0.0, "units": 0.0, "lines": 0})
        p["amount"] += amt
        p["units"] += qty
        p["lines"] += 1

    rows = sorted(cells.values(), key=lambda x: (-x["amount"], x["company"], x["store"]))
    by_company = {}
    for c in rows:
        b = by_company.setdefault(c["company"], {
            "company": c["company"], "company_id": c["company_id"],
            "amount": 0.0, "units": 0.0, "lines": 0, "stores": 0})
        b["amount"] += c["amount"]
        b["units"] += c["units"]
        b["lines"] += c["lines"]
        b["stores"] += 1

    r2 = lambda v: round(v + 0.0, 2)                                            # noqa: E731
    for d in list(excluded.values()):
        d["device_amount"] = r2(d["device_amount"])
        d["non_device_amount"] = r2(d["non_device_amount"])
        d["units"] = r2(d["units"])
    for d in (list(rows) + list(by_company.values()) + list(products.values())
              + list(non_device.values()) + list(unmapped_locations.values())):
        d["amount"] = r2(d["amount"])
        if "units" in d:
            d["units"] = r2(d["units"])

    # the serialised-unit count honours the SAME rule — a voided invoice's units are not purchases
    # either, and a count that disagreed with the money would be its own defect
    serialised_units = sum(
        1 for d in (device_rows or [])
        if in_window(d.get("period_year"), d.get("period_month"), win)
        and _t(d.get("invoice_number")) not in voided)

    return {
        "window": {"from": "%04d-%02d" % win[0], "to": "%04d-%02d" % win[1]},
        "basis": "purchases",
        "totals": {
            "device_amount": r2(tot["device_amount"]),
            "device_units": r2(tot["device_units"]),
            "device_lines": tot["device_lines"],
            "non_device_amount": r2(tot["non_device_amount"]),
            "non_device_lines": tot["non_device_lines"],
            "all_lines_amount": r2(tot["device_amount"] + tot["non_device_amount"]),
            "invoices": len(invoices),
        },
        # EXCLUDED, NOT MISSING. What the voided invoices in this window would have contributed had
        # they been counted — so "billed" still reconciles to "counted + voided", per invoice.
        "excluded_voided": {
            "invoices": len(excluded),
            "device_amount": r2(sum(e["device_amount"] for e in excluded.values())),
            "non_device_amount": r2(sum(e["non_device_amount"] for e in excluded.values())),
            "lines": sum(e["lines"] for e in excluded.values()),
            "units": r2(sum(e["units"] for e in excluded.values())),
            "detail": sorted(excluded.values(),
                             key=lambda x: (-x["device_amount"], x["invoice_number"])),
        },
        "by_company": sorted(by_company.values(), key=lambda x: (-x["amount"], x["company"])),
        "by_store": rows,
        "by_product": sorted(products.values(), key=lambda x: (-x["amount"], x["name"])),
        "non_device_lines": sorted(non_device.values(), key=lambda x: (-x["amount"], x["name"])),
        "unresolved": {
            "store_not_mapped": sorted(unmapped_locations.values(),
                                       key=lambda x: (-x["amount"], x["location"])),
            "company_not_mapped": [c for c in rows if c["company"] == COMPANY_NOT_MAPPED],
        },
        "meta": {
            "device_products_known": len(exact_names),
            "serialised_units_in_window": serialised_units,
            "resolution": {k: {"lines": v["lines"], "amount": r2(v["amount"])}
                           for k, v in resolution.items()},
            "month_unknown": {"lines": month_unknown["lines"],
                              "amount": r2(month_unknown["amount"])},
            "case_drift": {"lines": case_drift["lines"], "amount": r2(case_drift["amount"]),
                           "names": sorted(case_drift["names"])},
        },
    }


# ── I/O (the only impure function in the module) ──────────────────────────────────────────────────
def _page(client, table, select, org_id, years=None):
    """Org-scoped paged read; `years=None` reads every year. `.order('id')` makes the paging
    DETERMINISTIC — an unordered range() can re-read or skip rows between pages (the lesson
    `whatif._all()` already carries)."""
    out, start, page = [], 0, 1000
    while start < 400000:
        q = (client.schema("commcalc").table(table).select(select).eq("org_id", org_id))
        if years is not None:
            q = q.in_("period_year", years)
        q = q.order("id", desc=False).range(start, start + page - 1)
        chunk = (q.execute().data) or []
        out.extend(chunk)
        if len(chunk) < page:
            break
        start += page
    return out


def compute(client, org_id, win):
    """The report. Org-scoped on every read; resolvers are the platform's shared ones."""
    from app.modules.account import coa
    years = window_years(win)
    lines = _page(client, "vip_invoice_lines",
                  "id,invoice_number,location,status,name,quantity,total,period_year,period_month",
                  org_id, years)
    # THE DEVICE VOCABULARY IS NOT WINDOWED. Whether a product is a device is a property of the
    # PRODUCT, not of the window: a phone first serialised in 2024 is still a phone on a 2026
    # invoice. Reading only the window's device rows would make classification depend on how much
    # data the window happens to contain — a narrow window would quietly reclassify real handsets
    # as non-device and under-report the very number this report exists to give. So the vocabulary
    # is read across every year (org-scoped), and only the UNIT COUNT is windowed, inside aggregate.
    devices = _page(client, "vip_invoice_devices",
                    "id,invoice_number,product_name,period_year,period_month", org_id)
    # invoice HEADERS — the authoritative status. A voided invoice is not a purchase (see above).
    invoice_rows = _page(client, "vip_invoices", "id,invoice_number,status,period_year", org_id)
    voided = voided_invoice_set(invoice_rows)

    # ── the SHARED resolvers, called exactly as they are ────────────────────────────────────────
    # store: the org's real resolver + the address set its own contract says every match lands on.
    # A DISTRIBUTOR MASTER/DEALER ACCOUNT IS NOT A STORE. The store vocabulary carries rows that name
    # a legal/dealer account rather than a retail location — live house org: the VIP master dealer
    # ("Cellular Services Dot net LLC", 228 N Wood Ave, the dealer on 187 of 189 PayGo batches), whose
    # invoices are chargebacks, NSF fees and loans, not store device purchases. The platform ALREADY
    # models that: such a row carries NO `store_code`, and `flag_store_resolver` pins that a codeless
    # store_mapping row contributes no store key (harness_flag_store_resolver §A10). This report holds
    # the same line — codeless rows are not stores here either — so a master-account invoice can never
    # be counted as some retail store's device spend. It lands in "(store not mapped)", named, with
    # its money. No account string is written in code: the signal is the missing code, which is data.
    known = store_addresses(coa._fetch_all(client, "store_mapping", "store_address,store_code",
                                           {"org_id": org_id}))
    place_store = store_placer(coa.store_resolver(client, org_id), known)
    # company: ONE read of store_companies feeding the SAME pure matcher twice — with the default
    # (what the books do) and without it (so "unassigned" is distinguishable from "the default").
    # This is byte-identical to `coa.company_assignment`'s own composition, so the two cannot drift.
    _mp, _default_id, companies = coa.store_company_map(client, org_id)
    try:
        assign_rows = coa._fetch_all(client, "store_companies", "store_address,company_id",
                                     {"org_id": org_id})
    except Exception:
        assign_rows = []                          # same fail-closed posture as company_assignment
    company_if_assigned = coa.build_company_matcher(assign_rows, None)
    names = {}
    for c in (companies or []):                  # both the raw id and its str() form, so a UUID
        names[c.get("id")] = c.get("name") or ""  # object and a string id both find their label
        names[str(c.get("id"))] = c.get("name") or ""

    market_of = None
    try:                                     # §13a canonical store→market; never fatal to a report
        from app.core.scope import store_market_resolver
        resolve_market, _markets = store_market_resolver(client, org_id)
        market_of = resolve_market
    except Exception as e:                                       # pragma: no cover - I/O guard
        print(f"WARN device_purchases market resolution unavailable: {e}")

    out = aggregate(lines, devices, win, place_store, company_if_assigned, names, market_of,
                    voided_invoices=voided)
    out["org_id"] = org_id
    # The distributor's NAME is never written in code or page copy (RULE TWO). It resolves through
    # the mig-953 `report_term` vocabulary — tenant override > house carrier preset > the neutral
    # noun "distributor" — the SAME one the processor/financing/POS copy already uses. A tenant
    # whose carrier has no preset reads "distributor", which is correct rather than another
    # carrier's vendor name.
    try:
        from app.modules.commcalc.report_labels import carrier_term
        label, source = carrier_term(client, org_id, "distributor")
    except Exception as e:                                       # pragma: no cover - I/O guard
        print(f"WARN device_purchases distributor term unavailable: {e}")
        label, source = "distributor", "neutral_default"
    out["distributor_label"] = label
    out["distributor_label_source"] = source
    out["source"] = {"lines_table": "commcalc.vip_invoice_lines",
                     "devices_table": "commcalc.vip_invoice_devices",
                     "invoice_headers_table": "commcalc.vip_invoices",
                     "lines_read": len(lines), "device_rows_read": len(devices),
                     "invoice_headers_read": len(invoice_rows),
                     "voided_invoices_in_feed": len(voided)}
    return out
