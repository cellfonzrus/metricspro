"""FRANCHISE ROYALTY REPORT — parse, validate, book, reconcile (owner 2026-09-25, mig 1022, index §37). PURE at the top.

OWNER, verbatim (abridged): *"create and manage the finance data like p&l and create cost centers and capture royalty
report … all sales will be captured via the royalty report and reconciled against the daily report uploaded by the
tenant … Royalty report is uploaded for your reference to create the same in the finance module and only show up if
a ups store is selected while onboarding."*

WHAT THE REPORT IS. One per center per month, five sections: product / service sales → Total Gross Sales; exclusions
(amount / adjustment / adjusted amount / reason) → Total Exclusions; commissions (same columns) → Total Commissions;
Subject to Royalty (STR = gross sales − exclusions + commissions, and the adjusted STR); royalty fees (one line per fee,
each a rate of the STR) → Total Due.

ONE FACT, ONE HOME (CLAUDE.md "A fix is a DESIGN fix"):
  · THE LINE VOCABULARY — which labels the report prints, in which section, what each line books to on the P&L
    (`pl_line_key`, the mig-1009 commission_bucket shape), why a line deliberately books nothing (`pl_note`), each fee's
    rate and which fee absorbs the rounding remainder, and which daily-report categories each sales line sums — is
    per-org CONFIG: `commcalc.royalty_line_def` (mig 1022). House rows are the default a tenant reads (scoped to a
    vertical as DATA, `applies_to_vertical`); a tenant row overrides per key. `HOUSE_ROYALTY_LINES` below is the
    byte-equal mirror of the seed (parsed back by backend/harness_royalty.py §A). No vertical, franchisor or tenant is
    spelled in code (RULE TWO).
  · THE ROUNDING RULE — `fee_schedule()`: the TOTAL fee is round(basis × Σ rates); every fee but one is round(basis ×
    its rate); the fee flagged `absorbs_remainder` is total − the others. Measured on the owner's sample (a Σ-rate of
    8.5% where a straight round of the 1% fee lands one cent short of the printed figure) and reproduced with synthetic
    figures in the harness. The report's OWN numbers are what is stored; a cent the rule disagrees with is FLAGGED,
    never hidden and never "corrected".
  · THE P&L BOOKING — `pl_bookings()`, called by coa.build_inputs (the one place a P&L line is booked). A line with no
    `pl_line_key` books nothing and is REPORTED — as `excluded` (with its configured reason) or `unmapped` (no reason:
    a line nobody decided about). Silence is the class this closes.
  · THE RECONCILIATION — `reconcile()`: each sales line against the tenant's daily report(s) summed over the month
    through the line's `daily_categories`, per line with the days and categories behind the figure; daily categories no
    line claims are reported, never dropped.

Stdlib only above the I/O fold, so the harness runs on bare Python in the carrier-vocab-guard CI job.
"""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from html.parser import HTMLParser

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"
MIGRATION = "1022_franchise_royalty_cost_profit_centers.sql"
SECTIONS = ("sales", "exclusion", "commission", "str", "fee")
ROLES = ("header", "line", "total", "echo", "str", "str_adjusted")
BOOKABLE_SECTIONS = ("sales", "exclusion", "commission", "fee")
RECON_SECTIONS = ("sales",)
REPORT_KIND = "royalty_report"          # the report-kind registry key (report_kinds.HOUSE_KINDS, mig 1022)
CENT = Decimal("0.01")

# ── THE HOUSE CONFIG DEFAULT (an org row in commcalc.royalty_config overrides any key) ─────────────────
CONFIG_DEFAULT = {
    "fee_basis": "adjusted_str",        # 'adjusted_str' | 'str' — which STR the fees are charged on
    "tolerance": 0.004,                 # a difference larger than this is a flag (i.e. any cent)
    "daily_source": "raw_sales_product",  # 'raw_sales_product' | 'raw_sales' — the POS table the recon sums
    "daily_match_field": "category",    # 'category' | 'department' | 'product_desc'
    "book_pl": True,                    # the royalty report books the P&L revenue / fee lines
    "center_pattern": r"\bCenter\s+([A-Za-z]*\d[A-Za-z0-9-]*)",   # the center's code carries a digit
    "period_pattern": r"Period:\s*([A-Za-z]+\.?\s+\d{4}|\d{4}-\d{1,2})",
}
DAILY_SOURCES = ("raw_sales_product", "raw_sales")
MATCH_FIELDS = ("category", "department", "product_desc")
FEE_BASES = ("adjusted_str", "str")


def _L(line_key, label, section, role="line", aliases=(), pl_line_key=None, pl_note=None, rate=None,
       absorbs_remainder=False, daily_categories=(), sort_order=100):
    return {"line_key": line_key, "label": label, "section": section, "role": role, "aliases": list(aliases),
            "pl_line_key": pl_line_key, "pl_note": pl_note, "rate": rate, "absorbs_remainder": absorbs_remainder,
            "daily_categories": list(daily_categories), "sort_order": sort_order}


_PASS = "pass-through: the face value is collected for a third party, not earned; the commission on it books under Commissions"
_LIAB_TAX = "sales tax collected is owed to the taxing authority (a liability), not revenue"
_LIAB_DEP = "a customer deposit is a liability until it is earned, not revenue"
_EXCL = "an exclusion reduces the royalty base only; the underlying cost books through expenses, not from this report"

# ── MIRROR of mig 1022's royalty_line_def house seed (byte-equal; parsed back by harness_royalty.py §A) ──
HOUSE_ROYALTY_LINES = [
    # section headers — the phrase that starts each section of the printed report
    _L("hdr_sales", "Products / Services", "sales", "header", sort_order=1),
    _L("hdr_exclusion", "Exclusions", "exclusion", "header", sort_order=2),
    _L("hdr_commission", "Commissions", "commission", "header", sort_order=3),
    _L("hdr_str", "Subject to Royalty", "str", "header", sort_order=4),
    _L("hdr_fee", "Royalty Fees", "fee", "header", sort_order=5),
    # products / services
    _L("mailbox_service", "Mailbox Service", "sales", pl_line_key="service_sales", sort_order=10),
    _L("copies", "Copies", "sales", pl_line_key="service_sales", sort_order=11),
    _L("color_copies", "Color Copies", "sales", aliases=["Colour Copies"], pl_line_key="service_sales", sort_order=12),
    _L("laminating_binding", "Laminating/Binding", "sales", pl_line_key="service_sales", sort_order=13),
    _L("facsimile", "Facsimile", "sales", aliases=["Fax"], pl_line_key="service_sales", sort_order=14),
    _L("stamp_sales", "Stamp Sales", "sales", pl_line_key="merchandise_sales", sort_order=15),
    _L("metered_mail", "Metered Mail", "sales", pl_line_key="shipping_sales", sort_order=16),
    _L("shipping_charge", "Shipping Charge (UPS)", "sales", aliases=["Shipping Charge"], pl_line_key="shipping_sales", sort_order=17),
    _L("no_limit_shipping", "No Limit Shipping", "sales", pl_line_key="shipping_sales", sort_order=18),
    _L("retail_shipping_supply", "Retail Shipping Supply", "sales", pl_line_key="merchandise_sales", sort_order=19),
    _L("packaging_materials", "Packaging Materials", "sales", pl_line_key="merchandise_sales", sort_order=20),
    _L("packaging_service_fee", "Packaging Service Fee", "sales", pl_line_key="service_sales", sort_order=21),
    _L("office_supplies", "Office Supplies", "sales", pl_line_key="merchandise_sales", sort_order=22),
    _L("rubber_stamps", "Rubber Stamps", "sales", pl_line_key="merchandise_sales", sort_order=23),
    _L("greeting_cards", "Greeting Cards", "sales", pl_line_key="merchandise_sales", sort_order=24),
    _L("printing", "Printing", "sales", pl_line_key="service_sales", sort_order=25),
    _L("desktop_word_processing", "Desktop/Word Processing", "sales", pl_line_key="service_sales", sort_order=26),
    _L("computer_timeshare", "Computer Timeshare", "sales", pl_line_key="service_sales", sort_order=27),
    _L("message_services", "Message Services", "sales", pl_line_key="service_sales", sort_order=28),
    _L("pagers", "Pagers", "sales", pl_line_key="merchandise_sales", sort_order=29),
    _L("money_transfer", "Money Transfer", "sales", pl_note=_PASS, sort_order=30),
    _L("money_orders", "Money Orders", "sales", pl_note=_PASS, sort_order=31),
    _L("notary", "Notary", "sales", pl_line_key="service_sales", sort_order=32),
    _L("passport_photos", "Passport Photos", "sales", pl_line_key="service_sales", sort_order=33),
    _L("public_service_payments", "Public Service Payments", "sales", pl_note=_PASS, sort_order=34),
    _L("rapid_air", "Rapid Air", "sales", pl_line_key="shipping_sales", sort_order=35),
    _L("misc_taxable", "Miscellaneous Taxable", "sales", pl_line_key="merchandise_sales", sort_order=36),
    _L("misc_non_taxable", "Miscellaneous Non Taxable", "sales", aliases=["Miscellaneous Non-Taxable"],
       pl_line_key="merchandise_sales", sort_order=37),
    _L("deposits", "Deposits", "sales", pl_note=_LIAB_DEP, sort_order=38),
    _L("sales_tax", "Sales Tax", "sales", pl_note=_LIAB_TAX, sort_order=39),
    _L("gross_sales_total", "Total Gross Sales", "sales", "total", sort_order=49),
    # exclusions
    _L("excl_stamp_cost", "Stamp Cost", "exclusion", pl_note=_EXCL, sort_order=50),
    _L("excl_metered_mail_cost", "Metered Mail Cost", "exclusion", pl_note=_EXCL, sort_order=51),
    _L("excl_money_transfer", "Money Transfer", "exclusion", pl_note=_EXCL, sort_order=52),
    _L("excl_money_order_cost", "Money Order Cost", "exclusion", pl_note=_EXCL, sort_order=53),
    _L("excl_public_service_payment_cost", "Public Service Payment Cost", "exclusion", pl_note=_EXCL, sort_order=54),
    _L("excl_sales_tax", "Sales Tax", "exclusion", pl_note=_EXCL, sort_order=55),
    _L("excl_deposits", "Deposits", "exclusion", pl_note=_EXCL, sort_order=56),
    _L("excl_other_1", "Other 1", "exclusion", pl_note=_EXCL, sort_order=57),
    _L("excl_iship_proc_fee", "iShip Proc Fee", "exclusion", aliases=["iShip Processing Fee"], pl_note=_EXCL, sort_order=58),
    _L("exclusions_total", "Total Exclusions", "exclusion", "total", sort_order=69),
    # commissions
    _L("comm_money_transfer", "Money Transfer", "commission", pl_line_key="commission_income", sort_order=70),
    _L("comm_other_1", "Other 1", "commission", pl_line_key="commission_income", sort_order=71),
    _L("comm_other_2", "Other 2", "commission", pl_line_key="commission_income", sort_order=72),
    _L("commissions_total", "Total Commissions", "commission", "total", sort_order=79),
    # subject to royalty — the section echoes the three totals, then the STR and the adjusted STR
    _L("str_gross_sales", "Total Gross Sales", "str", "echo", sort_order=80),
    _L("str_exclusions", "Total Exclusions", "str", "echo", sort_order=81),
    _L("str_commissions", "Total Commissions", "str", "echo", sort_order=82),
    _L("str_total", "Total STR", "str", "str", sort_order=83),
    _L("str_adjusted", "Total Adjusted STR", "str", "str_adjusted", sort_order=84),
    # royalty fees — rate per fee; ONE fee absorbs the rounding remainder
    _L("fee_royalty", "Royalty Due", "fee", pl_line_key="royalty_fee", rate=0.05, sort_order=90),
    _L("fee_marketing", "Marketing Due", "fee", pl_line_key="marketing_fee", rate=0.01, absorbs_remainder=True, sort_order=91),
    _L("fee_naf", "NAF Due", "fee", aliases=["National Advertising Fund Due"], pl_line_key="ad_fund_fee", rate=0.025, sort_order=92),
    _L("fee_total", "Total Due", "fee", "total", sort_order=99),
]
# the echo lines of the STR section → the section whose total they repeat
ECHO_OF = {"str_gross_sales": "sales", "str_exclusions": "exclusion", "str_commissions": "commission"}


# ══ helpers ═════════════════════════════════════════════════════════════════════════════════════════
def _s(v):
    return "" if v is None else str(v).strip()


def D(v):
    """Money as Decimal, 2dp, half-up. None/blank → Decimal(0)."""
    if v is None or v == "":
        return Decimal("0.00")
    if isinstance(v, Decimal):
        return v.quantize(CENT, rounding=ROUND_HALF_UP)
    return Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP)


def f2(v):
    return float(D(v))


def norm_label(label):
    """'Shipping Charge (UPS)' → 'shipping charge ups'; '&' reads as 'and'; an ellipsis is dropped."""
    s = _s(label).lower().replace("&", " and ").replace("…", " ").replace("...", " ")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _list(v):
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("{") and s.endswith("}"):
            s = s[1:-1]
            return [x.strip().strip('"') for x in s.split(",") if x.strip()]
        return [s] if s else []
    return [x for x in v if _s(x)]


def normalise_line(row):
    r = _L(_s(row.get("line_key")), _s(row.get("label")) or _s(row.get("line_key")), _s(row.get("section")))
    r["role"] = _s(row.get("role")) or "line"
    r["aliases"] = [_s(a) for a in _list(row.get("aliases")) if _s(a)]
    r["pl_line_key"] = _s(row.get("pl_line_key")) or None
    r["pl_note"] = _s(row.get("pl_note")) or None
    r["rate"] = float(row["rate"]) if row.get("rate") not in (None, "") else None
    r["absorbs_remainder"] = bool(row.get("absorbs_remainder"))
    r["daily_categories"] = [_s(c) for c in _list(row.get("daily_categories")) if _s(c)]
    r["sort_order"] = int(row.get("sort_order") or 100)
    r["is_active"] = row.get("is_active") is not False
    r["applies_to_vertical"] = [_s(v) for v in _list(row.get("applies_to_vertical")) if _s(v)]
    r["org_id"] = _s(row.get("org_id")) or HOUSE_ORG
    return r


def merge_vocab(rows, org_id, vertical_key=None):
    """House rows (those whose applies_to_vertical admits the tenant's vertical) + this org's rows, merged PER
    line_key — the tenant row wins (the mig-207 / report_kind shape). A third org's row is never read. Inactive
    rows drop out after the merge (a tenant deactivates a house line by overriding it inactive)."""
    house, own = {}, {}
    for raw in rows or []:
        r = normalise_line(raw)
        if not r["line_key"] or r["section"] not in SECTIONS or r["role"] not in ROLES:
            continue
        if r["org_id"] == HOUSE_ORG and _s(org_id) != HOUSE_ORG:
            scope = r["applies_to_vertical"]
            if scope and vertical_key not in scope:
                continue
            house[r["line_key"]] = r
        elif r["org_id"] == _s(org_id):
            own[r["line_key"]] = r
    out = []
    for k in set(house) | set(own):
        r = dict(own.get(k) or house[k])
        r["_source"] = "override" if (k in own and k in house) else ("tenant" if k in own else "house")
        if r["is_active"]:
            out.append(r)
    return sorted(out, key=lambda r: (SECTIONS.index(r["section"]), r["sort_order"], r["line_key"]))


def house_vocab():
    return [dict(normalise_line({**r, "org_id": HOUSE_ORG}), _source="house") for r in HOUSE_ROYALTY_LINES]


def resolve_config(row=None):
    cfg = dict(CONFIG_DEFAULT)
    for k, v in (row or {}).items():
        if k in cfg and v not in (None, ""):
            cfg[k] = v
    cfg["tolerance"] = float(cfg["tolerance"])
    cfg["book_pl"] = cfg["book_pl"] is not False and str(cfg["book_pl"]).lower() not in ("false", "0", "no")
    if cfg["fee_basis"] not in FEE_BASES:
        cfg["fee_basis"] = CONFIG_DEFAULT["fee_basis"]
    if cfg["daily_source"] not in DAILY_SOURCES:
        cfg["daily_source"] = CONFIG_DEFAULT["daily_source"]
    if cfg["daily_match_field"] not in MATCH_FIELDS:
        cfg["daily_match_field"] = CONFIG_DEFAULT["daily_match_field"]
    return cfg


def vocab_problems(vocab):
    """Config errors a person must fix, in words: no absorbing fee / two absorbing fees, a fee with no rate, two
    lines of one section answering the same label."""
    out = []
    fees = [r for r in vocab if r["section"] == "fee" and r["role"] == "line"]
    absorb = [r["line_key"] for r in fees if r["absorbs_remainder"]]
    if fees and len(absorb) != 1:
        out.append(f"exactly one fee must absorb the rounding remainder (found {len(absorb)}: {', '.join(absorb) or 'none'})")
    for r in fees:
        if r["rate"] is None:
            out.append(f"fee '{r['label']}' has no rate")
    seen = {}
    for r in vocab:
        for name in [r["label"]] + r["aliases"]:
            k = (r["section"], norm_label(name))
            if k in seen and seen[k] != r["line_key"]:
                out.append(f"'{name}' names two {r['section']} lines ({seen[k]}, {r['line_key']})")
            seen.setdefault(k, r["line_key"])
    return out


# ══ THE ROUNDING RULE ═══════════════════════════════════════════════════════════════════════════════
def fee_schedule(basis, fee_lines):
    """{line_key: Decimal} + '__total__'. Total = round(basis × Σ rates); each non-absorbing fee = round(basis × rate);
    the absorbing fee = total − the others. `fee_lines` = vocab fee rows (role 'line')."""
    b = D(basis)
    fees = [r for r in fee_lines if r["section"] == "fee" and r["role"] == "line" and r["rate"] is not None]
    total_rate = sum((Decimal(str(r["rate"])) for r in fees), Decimal("0"))
    total = (b * total_rate).quantize(CENT, rounding=ROUND_HALF_UP)
    out, absorber = {}, None
    for r in fees:
        if r["absorbs_remainder"] and absorber is None:
            absorber = r["line_key"]
            continue
        out[r["line_key"]] = (b * Decimal(str(r["rate"]))).quantize(CENT, rounding=ROUND_HALF_UP)
    if absorber:
        out[absorber] = total - sum(out.values(), Decimal("0"))
    out["__total__"] = total
    return out


# ══ THE PARSER — text (PDF-extracted / pasted) or saved HTML → sections of lines ═══════════════════
_MONEY = re.compile(r"\(\s*-?\$?\s?[\d,]*\d(?:\.\d{1,2})?\s*\)(?!\s*%)|-?\$\s?[\d,]*\d(?:\.\d{1,2})?(?![\d%])"
                    r"|(?<![\w.$])-?[\d,]*\d\.\d{2}(?![\d%])")
_RATE = re.compile(r"\(\s*(\d+(?:\.\d+)?)\s*%\s*\)")
_NA = re.compile(r"\bN/?A\b", re.I)


def money_value(tok):
    t = tok.strip()
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").replace("$", "").replace(",", "").strip()
    if t.startswith("-"):
        neg, t = True, t[1:]
    v = D(t)
    return -v if neg else v


class _HTMLText(HTMLParser):
    """Saved HTML of the report page → text lines: a row / block ends a line, a cell becomes a tab, an <input>'s
    value (an editable amount on the submit page) is read as text. Script / style bodies are dropped."""
    _BLOCK = {"tr", "p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "thead", "tbody"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("td", "th"):
            self.parts.append("\t")
        elif tag == "input":
            a = dict(attrs)
            if (a.get("type") or "text").lower() in ("text", "number", "") and _s(a.get("value")):
                v = _s(a.get("value"))
                self.parts.append(" " + (("$" + v) if re.fullmatch(r"-?[\d,]*\d(?:\.\d+)?", v) else v) + " ")
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html):
    p = _HTMLText()
    p.feed(html or "")
    return "".join(p.parts)


def looks_like_html(text):
    return bool(re.search(r"<\s*(html|table|tr|td|div|body)\b", text or "", re.I))


def _lines(text):
    out = []
    for raw in (text or "").replace("\r", "\n").split("\n"):
        s = re.sub(r"[ \t ]+", " ", raw).strip()
        if s:
            out.append(s)
    return out


def _split_line(line):
    """(label, [Decimal …], trailing text, printed rate or None)."""
    rate = None
    m = _RATE.search(line)
    if m:
        rate = float(m.group(1)) / 100.0
        line = line[:m.start()] + line[m.end():]
    toks = list(_MONEY.finditer(line))
    if not toks:
        return line.strip(), [], "", rate
    label = line[:toks[0].start()].strip()
    vals = [money_value(t.group(0)) for t in toks]
    tail = _NA.sub("", line[toks[-1].end():]).strip()
    return label, vals, tail, rate


def _index(vocab):
    """{section: [(normalised name, row)]} for lines/totals/echo/str rows; {section: [names]} for headers."""
    by_sec, headers = {s: [] for s in SECTIONS}, []
    for r in vocab:
        names = [r["label"]] + r["aliases"]
        if r["role"] == "header":
            for n in names:
                headers.append((norm_label(n), r["section"]))
            continue
        for n in names:
            by_sec[r["section"]].append((norm_label(n), r))
    headers.sort(key=lambda x: -len(x[0]))
    return by_sec, headers


def _match(section, label, by_sec, truncated):
    n = norm_label(label)
    if not n:
        return None
    for name, r in by_sec.get(section, []):
        if name == n:
            return r
    if truncated and len(n) >= 6:                      # 'Public Service Payment C…' → the one line it can be
        hits = {r["line_key"]: r for name, r in by_sec.get(section, []) if name.startswith(n)}
        if len(hits) == 1:
            return next(iter(hits.values()))
    return None


def slug(label):
    return re.sub(r"[^a-z0-9]+", "_", norm_label(label)).strip("_")[:60] or "unlabelled"


def parse(text, vocab, config=None):
    """The report's text (or saved HTML) → {center, period_label, lines[], totals{}, unknown[], sections_seen[]}.

    Robust to the layout: a line is a LABEL followed by one to three money figures (amount, adjustment, adjusted
    amount) and an optional reason; a label on its own line followed by a figures-only line joins; the section is set
    by the configured header phrases; a label nobody configured is KEPT as an `unknown` line of its section (never
    dropped). The report's own figures are returned verbatim — nothing is recomputed here."""
    cfg = resolve_config(config)
    if looks_like_html(text):
        text = html_to_text(text)
    by_sec, headers = _index(vocab)
    center = period = None
    mc = re.search(cfg["center_pattern"], text or "", re.I)
    if mc:
        center = mc.group(1).strip()
    mp = re.search(cfg["period_pattern"], text or "", re.I)
    if mp:
        period = mp.group(1).strip()
    section, pending = None, None
    lines, totals, unknown, seen = [], {}, [], []
    order = 0
    for raw in _lines(text):
        label, vals, tail, rate = _split_line(raw)
        nl = norm_label(label)
        hdr = next((sec for name, sec in headers if name and (nl == name or nl.startswith(name + " "))), None)
        if hdr and not vals and not _match(section, label, by_sec, False):
            section, pending = hdr, None
            if hdr not in seen:
                seen.append(hdr)
            continue
        if not vals:
            pending = label if (label and section) else None
            continue
        if not label and pending:
            label, pending = pending, None
        else:
            pending = None
        if not label or section is None:
            continue
        truncated = raw.rstrip().find("…") >= 0 or "..." in raw
        row = _match(section, label, by_sec, truncated)
        amount = vals[0]
        adjustment = vals[1] if len(vals) >= 2 else None
        adjusted = vals[2] if len(vals) >= 3 else (amount + adjustment if adjustment is not None else amount)
        rec = {"section": section, "label": label, "amount": amount, "adjustment": adjustment,
               "adjusted_amount": adjusted, "reason": tail or None, "printed_rate": rate, "order": order}
        order += 1
        if row is None:
            rec.update(line_key=f"unknown_{section}_{slug(label)}", role="line", known=False)
            unknown.append(label)
            lines.append(rec)
            continue
        rec.update(line_key=row["line_key"], role=row["role"], known=True)
        if row["role"] == "line":
            lines.append(rec)
        else:
            totals[row["line_key"]] = rec
    return {"center": center, "period_label": period, "lines": lines, "totals": totals,
            "unknown": unknown, "sections_seen": seen}


# ══ VALIDATION — the report's figures against themselves and the configured rule ══════════════════
def _flag(flags, code, message, expected=None, reported=None, line_key=None):
    diff = None if expected is None or reported is None else f2(D(reported) - D(expected))
    flags.append({"code": code, "message": message, "line_key": line_key,
                  "expected": None if expected is None else f2(expected),
                  "reported": None if reported is None else f2(reported), "diff": diff})


def section_sums(lines):
    out = {}
    for s in BOOKABLE_SECTIONS:
        ls = [l for l in lines if l["section"] == s]
        out[s] = {"amount": sum((D(l["amount"]) for l in ls), Decimal("0.00")),
                  "adjustment": sum((D(l.get("adjustment") or 0) for l in ls), Decimal("0.00")),
                  "adjusted": sum((D(l.get("adjusted_amount") if l.get("adjusted_amount") is not None else l["amount"])
                                   for l in ls), Decimal("0.00"))}
    return out


def validate(parsed, vocab, config=None):
    """Every check the report's own numbers must pass, each difference FLAGGED with expected vs reported and the
    cents between them. Returns {status, flags[], computed{}, reported{}}. The report's figures are never changed."""
    cfg = resolve_config(config)
    tol = Decimal(str(cfg["tolerance"]))
    lines, totals = parsed.get("lines") or [], parsed.get("totals") or {}
    flags = []
    sums = section_sums(lines)

    def off(a, b):
        return abs(D(a) - D(b)) > tol

    def tot(key, col="amount"):
        t = totals.get(key)
        if not t:
            return None
        if col == "adjusted":
            return t.get("adjusted_amount") if t.get("adjusted_amount") is not None else t["amount"]
        return t.get(col) if t.get(col) is not None else (Decimal("0.00") if col == "adjustment" else None)

    total_keys = {r["section"]: r["line_key"] for r in vocab if r["role"] == "total"}
    rep = {}
    for sec in ("sales", "exclusion", "commission"):
        k = total_keys.get(sec)
        t = tot(k) if k else None
        if t is None:
            _flag(flags, "missing_total", f"the report carries no {sec} total", line_key=k)
            continue
        rep[sec] = {"amount": D(t), "adjustment": D(tot(k, "adjustment") or 0), "adjusted": D(tot(k, "adjusted"))}
        if off(sums[sec]["amount"], t):
            _flag(flags, "section_sum", f"the {sec} lines add to {f2(sums[sec]['amount']):,.2f}, the report's total says "
                  f"{f2(t):,.2f}", sums[sec]["amount"], t, k)
        if sec != "sales" and off(sums[sec]["adjusted"], rep[sec]["adjusted"]):
            _flag(flags, "section_sum_adjusted", f"the {sec} lines' adjusted amounts add to {f2(sums[sec]['adjusted']):,.2f}, "
                  f"the report's adjusted total says {f2(rep[sec]['adjusted']):,.2f}", sums[sec]["adjusted"], rep[sec]["adjusted"], k)
    # the STR section's echoes repeat the section totals (exclusions print NEGATIVE there)
    for ek, sec in ECHO_OF.items():
        e = totals.get(ek)
        if e and sec in rep:
            want = rep[sec]["amount"]
            got = abs(D(e["amount"])) if sec == "exclusion" else D(e["amount"])
            if off(want, got):
                _flag(flags, "str_echo", f"the Subject-to-Royalty section repeats the {sec} total as {f2(got):,.2f}, the "
                      f"section itself says {f2(want):,.2f}", want, got, ek)
    gs = rep.get("sales", {}).get("amount", sums["sales"]["amount"])
    ex = rep.get("exclusion", {}).get("adjusted", sums["exclusion"]["adjusted"])
    co = rep.get("commission", {}).get("adjusted", sums["commission"]["adjusted"])
    str_calc = D(gs) - D(ex) + D(co)
    str_key = next((r["line_key"] for r in vocab if r["role"] == "str"), None)
    adj_key = next((r["line_key"] for r in vocab if r["role"] == "str_adjusted"), None)
    str_rep = totals.get(str_key) if str_key else None
    adj_rep = totals.get(adj_key) if adj_key else None
    str_rep_amt = D(str_rep["amount"]) if str_rep else None
    str_rep_adj = (D(str_rep["adjusted_amount"]) if str_rep and str_rep.get("adjusted_amount") is not None else str_rep_amt)
    adj_amt = D(adj_rep["amount"]) if adj_rep else str_rep_adj
    if str_rep is None:
        _flag(flags, "missing_str", "the report carries no Total STR line", line_key=str_key)
    elif off(str_calc, str_rep_amt):
        _flag(flags, "str", f"gross sales − exclusions + commissions = {f2(str_calc):,.2f}; the report's Total STR says "
              f"{f2(str_rep_amt):,.2f}", str_calc, str_rep_amt, str_key)
    if adj_rep is not None and str_rep_adj is not None and off(adj_amt, str_rep_adj):
        _flag(flags, "str_adjusted", f"the Total Adjusted STR ({f2(adj_amt):,.2f}) differs from the STR line's adjusted "
              f"amount ({f2(str_rep_adj):,.2f})", str_rep_adj, adj_amt, adj_key)
    basis = adj_amt if cfg["fee_basis"] == "adjusted_str" else str_rep_amt
    if basis is None:
        basis = str_calc
    fees_vocab = [r for r in vocab if r["section"] == "fee" and r["role"] == "line"]
    sched = fee_schedule(basis, fees_vocab)
    fee_rep = {l["line_key"]: l for l in lines if l["section"] == "fee"}
    for r in fees_vocab:
        got = fee_rep.get(r["line_key"])
        if got is None:
            _flag(flags, "missing_fee", f"the report carries no '{r['label']}' line", sched.get(r["line_key"]), None, r["line_key"])
            continue
        if got.get("printed_rate") is not None and r["rate"] is not None and abs(got["printed_rate"] - r["rate"]) > 1e-9:
            _flag(flags, "fee_rate", f"'{r['label']}' prints a rate of {got['printed_rate']*100:g}%, configured "
                  f"{r['rate']*100:g}%", None, None, r["line_key"])
        if off(sched[r["line_key"]], got["amount"]):
            _flag(flags, "fee", f"'{r['label']}' by the rule = {f2(sched[r['line_key']]):,.2f}; the report says "
                  f"{f2(got['amount']):,.2f}", sched[r["line_key"]], got["amount"], r["line_key"])
    fee_total_key = total_keys.get("fee")
    ft = totals.get(fee_total_key) if fee_total_key else None
    if ft is None:
        _flag(flags, "missing_total", "the report carries no fee total (Total Due)", line_key=fee_total_key)
    else:
        if off(sched["__total__"], ft["amount"]):
            _flag(flags, "fee_total", f"total due by the rule = {f2(sched['__total__']):,.2f}; the report says "
                  f"{f2(ft['amount']):,.2f}", sched["__total__"], ft["amount"], fee_total_key)
        fsum = sum((D(l["amount"]) for l in fee_rep.values()), Decimal("0.00"))
        if off(fsum, ft["amount"]):
            _flag(flags, "fee_sum", f"the fee lines add to {f2(fsum):,.2f}; the report's Total Due says {f2(ft['amount']):,.2f}",
                  fsum, ft["amount"], fee_total_key)
    for u in parsed.get("unknown") or []:
        flags.append({"code": "unknown_label", "message": f"'{u}' is not in the line vocabulary — kept as its own line, "
                      "books nothing until it is mapped", "line_key": None, "expected": None, "reported": None, "diff": None})
    computed = {"gross_sales": f2(sums["sales"]["amount"]), "exclusions": f2(sums["exclusion"]["adjusted"]),
                "commissions": f2(sums["commission"]["adjusted"]), "str": f2(str_calc), "fee_basis": f2(basis),
                "fees": {k: f2(v) for k, v in sched.items() if k != "__total__"}, "total_due": f2(sched["__total__"])}
    reported = {"gross_sales": f2(gs), "exclusions": f2(ex), "commissions": f2(co),
                "str": None if str_rep_amt is None else f2(str_rep_amt),
                "adjusted_str": None if adj_amt is None else f2(adj_amt),
                "total_due": None if ft is None else f2(ft["amount"])}
    return {"status": "flagged" if flags else "ok", "flags": flags, "computed": computed, "reported": reported}


# ══ HEADER + LINE ROWS — what the writer stores (the report's own numbers) ═════════════════════════
def header_fields(parsed, validation):
    rep = validation["reported"]
    t = parsed.get("totals") or {}

    def g(role_key, col="amount"):
        r = t.get(role_key)
        if not r:
            return None
        v = r.get(col)
        return None if v is None else f2(v)
    return {"total_gross_sales": rep.get("gross_sales"), "total_exclusions": g("exclusions_total"),
            "total_exclusions_adjusted": rep.get("exclusions"), "total_commissions": g("commissions_total"),
            "total_commissions_adjusted": rep.get("commissions"), "total_str": rep.get("str"),
            "total_adjusted_str": rep.get("adjusted_str"), "total_due": rep.get("total_due"),
            "status": validation["status"], "validation": {"flags": validation["flags"], "computed": validation["computed"]}}


def line_rows(parsed):
    """Every line AND every total/echo row, as stored (one row per section × line_key; a repeated key is summed and
    flagged by the caller's validation through the section sum)."""
    out, seen = [], {}
    for rec in list(parsed.get("lines") or []) + list((parsed.get("totals") or {}).values()):
        k = (rec["section"], rec["line_key"])
        row = {"section": rec["section"], "line_key": rec["line_key"], "label": rec["label"], "role": rec.get("role", "line"),
               "amount": f2(rec["amount"]), "adjustment": None if rec.get("adjustment") is None else f2(rec["adjustment"]),
               "adjusted_amount": None if rec.get("adjusted_amount") is None else f2(rec["adjusted_amount"]),
               "reason": rec.get("reason"), "printed_rate": rec.get("printed_rate"), "sort_order": rec.get("order", 0)}
        if k in seen:
            prev = seen[k]
            prev["amount"] = f2(D(prev["amount"]) + D(row["amount"]))
            prev["adjusted_amount"] = f2(D(prev["adjusted_amount"] or 0) + D(row["adjusted_amount"] or row["amount"]))
            continue
        seen[k] = row
        out.append(row)
    return out


def from_manual(entries, vocab):
    """A manual-entry form's rows → the same `parsed` shape the parser returns (so ONE validation and ONE writer serve
    both). `entries` = [{line_key, amount, adjustment?, adjusted_amount?, reason?}]; unknown keys are kept as unknown."""
    by_key = {r["line_key"]: r for r in vocab}
    lines, totals, unknown = [], {}, []
    for i, e in enumerate(entries or []):
        k = _s(e.get("line_key"))
        r = by_key.get(k)
        if e.get("amount") in (None, "") and e.get("adjusted_amount") in (None, ""):
            continue
        amt = D(e.get("amount") or 0)
        adj = None if e.get("adjustment") in (None, "") else D(e.get("adjustment"))
        adjd = D(e["adjusted_amount"]) if e.get("adjusted_amount") not in (None, "") else (amt + adj if adj is not None else amt)
        rec = {"section": r["section"] if r else _s(e.get("section")) or "sales", "label": r["label"] if r else _s(e.get("label")) or k,
               "amount": amt, "adjustment": adj, "adjusted_amount": adjd, "reason": _s(e.get("reason")) or None,
               "printed_rate": None, "order": i, "line_key": k, "role": r["role"] if r else "line", "known": bool(r)}
        if not r:
            unknown.append(rec["label"])
            lines.append(rec)
        elif r["role"] == "line":
            lines.append(rec)
        elif r["role"] != "header":
            totals[k] = rec
    return {"center": None, "period_label": None, "lines": lines, "totals": totals, "unknown": unknown,
            "sections_seen": sorted({l["section"] for l in lines})}


def fill_totals(parsed, vocab):
    """A manual entry may leave the totals blank: fill each missing total from the lines (marked `derived`) so the
    header carries a figure — the validation then shows nothing to flag for a derived total, by construction, and
    the header records that the total was derived, not reported."""
    t = parsed.setdefault("totals", {})
    sums = section_sums(parsed.get("lines") or [])
    derived = []
    for r in vocab:
        if r["role"] == "total" and r["line_key"] not in t and r["section"] in sums:
            s = sums[r["section"]]
            amt = s["adjusted"] if r["section"] == "fee" else s["amount"]
            t[r["line_key"]] = {"section": r["section"], "label": r["label"], "amount": amt, "adjustment": s["adjustment"] if r["section"] in ("exclusion", "commission") else None,
                                "adjusted_amount": s["adjusted"], "reason": None, "printed_rate": None, "order": 900,
                                "line_key": r["line_key"], "role": "total", "known": True}
            derived.append(r["line_key"])
    str_key = next((r["line_key"] for r in vocab if r["role"] == "str"), None)
    if str_key and str_key not in t:
        tk = {r["section"]: r["line_key"] for r in vocab if r["role"] == "total"}
        gs = D(t.get(tk.get("sales"), {}).get("amount", 0))
        ex = D(t.get(tk.get("exclusion"), {}).get("adjusted_amount", 0))
        co = D(t.get(tk.get("commission"), {}).get("adjusted_amount", 0))
        t[str_key] = {"section": "str", "label": "Total STR", "amount": gs - ex + co, "adjustment": None,
                      "adjusted_amount": gs - ex + co, "reason": None, "printed_rate": None, "order": 950,
                      "line_key": str_key, "role": "str", "known": True}
        derived.append(str_key)
    parsed["derived_totals"] = derived
    return parsed


# ══ THE P&L BOOKING ══════════════════════════════════════════════════════════════════════════════════
def line_amount(row):
    """The figure a stored line books: the ADJUSTED amount where the report carries one (exclusions / commissions),
    else the amount."""
    v = row.get("adjusted_amount")
    return D(v if v is not None else row.get("amount"))


def pl_bookings(reports, vocab, pl_lines=None):
    """(bookings[(pl_line_key, store_ref|None, amount, detail_label)], coverage{booked, excluded, unmapped}).

    `reports` = [{"store_ref": …, "center_code": …, "lines": [stored line rows]}]. A line books to its vocabulary
    row's `pl_line_key`; a line whose key is not a P&L line (`pl_lines`, when given) is unmapped, never guessed. A
    line with no key books NOTHING and is reported: `excluded` when the vocabulary gives the reason (`pl_note`),
    `unmapped` when nobody decided. Totals, echoes and headers never book (they repeat the lines)."""
    by_key = {r["line_key"]: r for r in vocab}
    bookings, booked, excluded, unmapped = [], {}, {}, {}
    for rep in reports or []:
        store = _s(rep.get("store_ref")) or None
        for ln in rep.get("lines") or []:
            if (ln.get("role") or "line") != "line" or ln.get("section") not in BOOKABLE_SECTIONS:
                continue
            amt = line_amount(ln)
            if not amt:
                continue
            d = by_key.get(ln.get("line_key"))
            label = _s(ln.get("label")) or _s(ln.get("line_key"))
            if d and d["pl_line_key"] and (pl_lines is None or d["pl_line_key"] in pl_lines):
                bookings.append((d["pl_line_key"], store, f2(amt), d["label"]))
                booked[d["pl_line_key"]] = f2(D(booked.get(d["pl_line_key"], 0)) + amt)
            elif d and not d["pl_line_key"] and d["pl_note"]:
                e = excluded.setdefault(ln["line_key"], {"label": d["label"], "section": d["section"], "amount": 0.0,
                                                         "reason": d["pl_note"]})
                e["amount"] = f2(D(e["amount"]) + amt)
            else:
                why = ("its P&L line '%s' is not a line of the chart" % d["pl_line_key"]) if (d and d["pl_line_key"]) else \
                      ("not in the line vocabulary" if not d else "no P&L line and no reason configured")
                u = unmapped.setdefault(ln.get("line_key") or slug(label), {"label": label, "section": ln.get("section"),
                                                                          "amount": 0.0, "why": why})
                u["amount"] = f2(D(u["amount"]) + amt)
    return bookings, {"booked": booked, "excluded": excluded, "unmapped": unmapped}


# ══ THE RECONCILIATION — royalty (monthly, per line) vs the daily report(s) summed over the month ═══
def reconcile(royalty_lines, vocab, daily_rows, match_field="category", tolerance=0.004):
    """Per SALES line: the royalty figure, the daily figure (Σ the rows whose `match_field` value is one of the line's
    `daily_categories`, else — when none are configured — a category spelled like the line's own label), the variance,
    and the days + categories behind the daily figure. Daily categories no line claims, and a category two lines claim,
    are reported. `daily_rows` = [{date, category|department|product_desc, amount}] (voids already excluded)."""
    tol = Decimal(str(tolerance))
    sales_defs = [r for r in vocab if r["section"] in RECON_SECTIONS and r["role"] == "line"]
    claim, conflicts = {}, {}
    for r in sales_defs:
        cats = r["daily_categories"] or []
        for c in cats:
            k = norm_label(c)
            if k in claim and claim[k] != r["line_key"]:
                conflicts.setdefault(c, sorted({claim[k], r["line_key"]}))
                continue
            claim[k] = r["line_key"]
    by_name = {norm_label(n): r["line_key"] for r in sales_defs if not r["daily_categories"]
               for n in [r["label"]] + r["aliases"]}
    per_line = {r["line_key"]: {"daily": Decimal("0.00"), "days": {}, "categories": {}, "matched_by": None} for r in sales_defs}
    unclaimed = {}
    daily_total = Decimal("0.00")
    for row in daily_rows or []:
        cat = _s(row.get(match_field))
        amt = D(row.get("amount"))
        day = _s(row.get("date"))[:10]
        daily_total += amt
        k = norm_label(cat)
        lk = claim.get(k)
        how = "configured"
        if lk is None and k in by_name:
            lk, how = by_name[k], "same name"
        if lk is None:
            u = unclaimed.setdefault(cat or "(blank)", {"amount": Decimal("0.00"), "days": {}, "rows": 0})
            u["amount"] += amt
            u["days"][day] = f2(D(u["days"].get(day, 0)) + amt)
            u["rows"] += 1
            continue
        p = per_line[lk]
        p["daily"] += amt
        p["days"][day] = f2(D(p["days"].get(day, 0)) + amt)
        p["categories"][cat] = f2(D(p["categories"].get(cat, 0)) + amt)
        p["matched_by"] = p["matched_by"] or how
    roy = {}
    for ln in royalty_lines or []:
        if ln.get("section") in RECON_SECTIONS and (ln.get("role") or "line") == "line":
            roy[ln["line_key"]] = D(roy.get(ln["line_key"], 0)) + line_amount(ln)
    out = []
    for r in sales_defs:
        p = per_line[r["line_key"]]
        ra = roy.get(r["line_key"], Decimal("0.00"))
        var = ra - p["daily"]
        if not r["daily_categories"] and p["matched_by"] is None:
            status = "no_daily_map" if ra else "empty"
        elif abs(var) <= tol:
            status = "tie"
        else:
            status = "variance"
        if status == "empty":
            continue
        out.append({"line_key": r["line_key"], "label": r["label"], "royalty": f2(ra), "daily": f2(p["daily"]),
                    "variance": f2(var), "status": status, "matched_by": p["matched_by"],
                    "days": dict(sorted(p["days"].items())), "categories": p["categories"]})
    for k, ra in roy.items():                       # a royalty line the vocabulary does not know (unknown label)
        if k not in per_line and ra:
            out.append({"line_key": k, "label": k, "royalty": f2(ra), "daily": 0.0, "variance": f2(ra),
                        "status": "unknown_line", "matched_by": None, "days": {}, "categories": {}})
    roy_total = sum(roy.values(), Decimal("0.00"))
    return {"lines": out,
            "unmapped_daily": [{"category": c, "amount": f2(u["amount"]), "rows": u["rows"], "days": dict(sorted(u["days"].items()))}
                               for c, u in sorted(unclaimed.items(), key=lambda kv: -abs(kv[1]["amount"]))],
            "conflicts": [{"category": c, "lines": ls} for c, ls in conflicts.items()],
            "totals": {"royalty_sales": f2(roy_total), "daily_total": f2(daily_total),
                       "daily_mapped": f2(sum((p["daily"] for p in per_line.values()), Decimal("0.00"))),
                       "variance": f2(roy_total - daily_total)},
            "match_field": match_field}


def tender_crosscheck(gross_sales, tender_rows):
    """The month's register tenders (X-report, pos_tender_summary) against the report's gross sales — a TOTAL-level
    check only (a tender is not a product line). None when no tender row exists (not measured ≠ $0.00)."""
    if not tender_rows:
        return None
    t = sum((D(r.get("amount")) for r in tender_rows), Decimal("0.00"))
    return {"tenders": f2(t), "gross_sales": f2(gross_sales), "variance": f2(D(gross_sales) - t),
            "days": len({_s(r.get("close_date"))[:10] for r in tender_rows})}


def summary(reports, recon_by_report=None, coverage=None):
    """The operations-dashboard tile: the LATEST period's STR, fees due, recon variance, unmapped lines, flags."""
    if not reports:
        return {"has_data": False}
    def key(r):
        from app.modules.account._period import parse_period   # stdlib module; lazy so this file imports alone
        m, y = parse_period(r.get("period") or "")
        return y * 100 + m
    latest_key = max(key(r) for r in reports)
    latest = [r for r in reports if key(r) == latest_key]
    rv = [recon_by_report.get(r.get("id")) for r in latest] if recon_by_report else []
    rv = [x for x in rv if x]
    return {"has_data": True, "period": latest[0].get("period"), "centers": len(latest),
            "str": f2(sum((D(r.get("total_adjusted_str") if r.get("total_adjusted_str") is not None else r.get("total_str")) for r in latest), Decimal("0"))),
            "gross_sales": f2(sum((D(r.get("total_gross_sales")) for r in latest), Decimal("0"))),
            "fees_due": f2(sum((D(r.get("total_due")) for r in latest), Decimal("0"))),
            "flagged_reports": sum(1 for r in latest if (r.get("status") or "") == "flagged"),
            "recon_variance": f2(sum((D(x["totals"]["variance"]) for x in rv), Decimal("0"))) if rv else None,
            "unmapped_lines": len((coverage or {}).get("unmapped") or {}),
            "unmapped_amount": f2(sum((D(u["amount"]) for u in ((coverage or {}).get("unmapped") or {}).values()), Decimal("0")))}


# ══ I/O (org-scoped; every failure degrades to the mirror / the default, never to another org's rows) ═
def load_vocab(client, org_id, vertical_key=None):
    """(merged vocabulary, registry_ready)."""
    try:
        rows = (client.schema("commcalc").table("royalty_line_def").select("*")
                .in_("org_id", sorted({HOUSE_ORG, org_id})).execute().data) or []
        if rows:
            return merge_vocab(rows, org_id, vertical_key), True
    except Exception:
        pass
    return merge_vocab(house_seed_rows(), org_id, vertical_key), False


def house_seed_rows():
    """The mirror as the seed stores it: house org, scoped to wherever the royalty MODULE applies (mig 1020's
    module_catalog.applies_to_vertical, mirrored in core/verticals — dereferenced, never a vertical literal here)."""
    from app.modules.core.verticals import HOUSE_MODULE_VERTICALS   # stdlib-only mirror
    scope = list(HOUSE_MODULE_VERTICALS.get("royalty") or [])
    return [{**r, "org_id": HOUSE_ORG, "applies_to_vertical": scope} for r in HOUSE_ROYALTY_LINES]


def load_config(client, org_id):
    try:
        rows = (client.schema("commcalc").table("royalty_config").select("*")
                .eq("org_id", org_id).limit(1).execute().data) or []
        return resolve_config(rows[0] if rows else None)
    except Exception:
        return resolve_config(None)


def tenant_vertical_key(client, org_id):
    try:
        from app.modules.core import verticals as _v
        return _v.tenant_vertical(client, org_id).get("key")
    except Exception:
        return None


def load_reports(client, org_id, period_keys=None, with_lines=True):
    try:
        q = client.schema("commcalc").table("royalty_report").select("*").eq("org_id", org_id)
        if period_keys:
            q = q.in_("period", list(period_keys))
        reps = q.execute().data or []
    except Exception:
        return []
    if with_lines and reps:
        try:
            ids = [r["id"] for r in reps]
            lines = (client.schema("commcalc").table("royalty_report_line").select("*")
                     .eq("org_id", org_id).in_("report_id", ids).execute().data) or []
        except Exception:
            lines = []
        by = {}
        for ln in lines:
            by.setdefault(ln.get("report_id"), []).append(ln)
        for r in reps:
            r["lines"] = sorted(by.get(r["id"], []), key=lambda x: (SECTIONS.index(x["section"]) if x.get("section") in SECTIONS else 9,
                                                                   x.get("sort_order") or 0))
    return reps
