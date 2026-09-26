"""PROOF — the franchise royalty report, cost centers, profit centers, the unclaimed sale line (mig 1022, index §37).

Stdlib only, DB-free — runs on bare Python in the carrier-vocab-guard CI job. The P&L byte-identity proof that needs
the app's own engine (engine._assemble over coa.build_inputs with an in-memory client) is harness_royalty_pl.py.

THE FIXTURE IS SYNTHETIC: the same five-section structure as the franchisor's report, with invented figures (center
9001, March 2031). The owner's real sample is verified locally only and is NOT committed (its figures are the
tenant's). The synthetic STR (40,000.45) exercises the SAME remainder the real sample does: a straight round of the
1% fee lands one cent below the figure the rule produces.

  §A the house line vocabulary: the mirror IS the mig-1022 seed (parsed back), the config is sane
  §B the report-kind registry's VERTICAL axis (backend twin; the frontend twin is pinned by source)
  §C the rounding rule — the absorbing fee, the 1-cent remainder, the total
  §D the parser — text (PDF-extracted shape), saved HTML with <input> values, pasted text; truncation; unknown labels
  §E validation — clean report ok; a straight-rounded fee is FLAGGED and the report's figure is kept
  §F the P&L mapping — bookings, excluded (with reason), unmapped (never silent)
  §G the reconciliation — per line, days, categories, unclaimed categories, conflicts, tenders
  §H cost centers + profit centers — scopes, the cost-center view ties to the statement to the cent
  §I the unclaimed sale line map — precedence, revenue-only, suppression by the royalty report, the tally
  §J manual entry = the same shape, derived totals marked
  §K landing identity: the royalty kind lands through its module page and says where it shows
  §L many months in one go (index §37.10): the lookback is config (house default 24, bounds), the window, the batch
     plan's rules (out-of-window, undated, unreadable, duplicate center × month, replace), coverage, the outcome
     sentence; negative controls

  python3 backend/harness_royalty.py
"""
import io
import os
import re
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)

from app.modules.account import royalty as R            # noqa: E402
from app.modules.account import centers as C            # noqa: E402
from app.modules.account import sales_line_map as S     # noqa: E402
from app.modules.commcalc import report_kinds as RK     # noqa: E402
from app.modules.commcalc import landing_identity as LI  # noqa: E402
from app.modules.core import verticals as V             # noqa: E402

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  " + label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:500]))


def section(t):
    print("\n── " + t)


MIG = os.path.join(ROOT, "database", "migrations", R.MIGRATION)
SQL = io.open(MIG, encoding="utf-8").read()

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. THE LINE VOCABULARY — the mirror IS the mig-1022 seed")


def seed_rows():
    body = SQL.split("absorbs_remainder, daily_categories, sort_order) VALUES", 1)[1].split("ON CONFLICT", 1)[0]
    tok = re.compile(r"'((?:[^']|'')*)'|(-?\d+\.\d+)|(\d+)|(NULL)|(true|false)")
    cols = ("org_id", "line_key", "label", "section", "role", "aliases", "pl_line_key", "pl_note", "rate",
            "absorbs_remainder", "daily_categories", "sort_order")
    out = []
    for line in body.strip().split("\n"):
        line = line.strip().rstrip(",")
        if not line.startswith("("):
            continue
        vals = []
        for m in tok.finditer(line[1:-1]):
            if m.group(1) is not None:
                vals.append(m.group(1).replace("''", "'"))
            elif m.group(2) is not None:
                vals.append(float(m.group(2)))
            elif m.group(3) is not None:
                vals.append(int(m.group(3)))
            elif m.group(4) is not None:
                vals.append(None)
            else:
                vals.append(m.group(5) == "true")
        out.append(dict(zip(cols, vals)))
    return out


seed = seed_rows()
check("the seed parses %d rows = the mirror's %d, in order" % (len(seed), len(R.HOUSE_ROYALTY_LINES)),
      [r["line_key"] for r in seed] == [r["line_key"] for r in R.HOUSE_ROYALTY_LINES])
diffs = []
for s_row, m_row in zip(seed, R.HOUSE_ROYALTY_LINES):
    a, b = R.normalise_line(s_row), R.normalise_line({**m_row, "org_id": R.HOUSE_ORG})
    for k in ("label", "section", "role", "aliases", "pl_line_key", "pl_note", "rate", "absorbs_remainder",
              "daily_categories", "sort_order"):
        if a[k] != b[k]:
            diffs.append((m_row["line_key"], k, a[k], b[k]))
check("every column of every seeded row equals the mirror", not diffs, diffs[:3])
check("the seed scopes the house rows by SUB-SELECT on the royalty module (no vertical literal in the migration)",
      "SELECT applies_to_vertical FROM core.module_catalog WHERE key = 'royalty'" in SQL
      and not any(v["key"] in SQL for v in V.HOUSE_VERTICALS))
hv = R.house_vocab()
check("the house vocabulary has no configuration problem (one absorbing fee, every fee rated, no label twice per section)",
      R.vocab_problems(hv) == [], R.vocab_problems(hv))
check("every pl_line_key the vocabulary names is a key the migration's P&L heads document (coa.PL_SPEC is pinned in "
      "harness_royalty_pl.py)", {r["pl_line_key"] for r in R.HOUSE_ROYALTY_LINES if r["pl_line_key"]} ==
      {"service_sales", "shipping_sales", "merchandise_sales", "commission_income", "royalty_fee", "marketing_fee", "ad_fund_fee"})
check("a line with no P&L key carries its reason (nothing is silently unbooked by the house default)",
      all(r["pl_note"] for r in R.HOUSE_ROYALTY_LINES if r["role"] == "line" and not r["pl_line_key"]))
scope = V.HOUSE_MODULE_VERTICALS.get("royalty")
mv = R.merge_vocab(R.house_seed_rows(), "org-franchise", scope[0])
mw = R.merge_vocab(R.house_seed_rows(), "org-other", "some_other_vertical")
check("house rows reach a tenant of the module's vertical, and NOT a tenant of another vertical", len(mv) == len(hv) and mw == [])
own = [{"org_id": "org-franchise", "line_key": "copies", "label": "Copies", "section": "sales", "pl_line_key": "merchandise_sales"},
       {"org_id": "org-franchise", "line_key": "pagers", "label": "Pagers", "section": "sales", "is_active": False},
       {"org_id": "org-third", "line_key": "notary", "label": "Notary", "section": "sales", "pl_line_key": "royalty_fee"}]
m2 = {r["line_key"]: r for r in R.merge_vocab(R.house_seed_rows() + own, "org-franchise", scope[0])}
check("a tenant row overrides the house row per key; an inactive override removes the line; a THIRD org's row is never read",
      m2["copies"]["pl_line_key"] == "merchandise_sales" and m2["copies"]["_source"] == "override"
      and "pagers" not in m2 and m2["notary"]["pl_line_key"] == "service_sales")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. THE REPORT-KIND REGISTRY — the vertical axis ('{}' = any; every existing kind unchanged)")
rows = RK.house_mirror()
roy = next(r for r in rows if r["key"] == R.REPORT_KIND)
check("the royalty kind's vertical scope is the royalty MODULE's (dereferenced from core/verticals, not a literal)",
      roy["applies_to_vertical"] == list(scope) and "applies_to_vertical=list(_HOUSE_MODULE_VERTICALS.get(\"royalty\")"
      in io.open(os.path.join(HERE, "app", "modules", "commcalc", "report_kinds.py"), encoding="utf-8").read())
check("every OTHER house kind applies to any vertical (byte-identical visibility)",
      all(r["applies_to_vertical"] == [] for r in rows if r["key"] != R.REPORT_KIND))
dk = V.default_vertical(V.HOUSE_VERTICALS)["key"]
d_wire = RK.declaration_from("", "", [], [{"code": "boost"}], vertical=dk)
d_fr = RK.declaration_from("", "", [], [], vertical=scope[0])
d_unknown = RK.declaration_from("", "", [], [{"code": "boost"}])
vis = lambda d: [r["key"] for r in RK.visible_kinds(rows, d)]
check("a default-vertical tenant does NOT see the royalty kind; the reason says why",
      R.REPORT_KIND not in vis(d_wire) and any(h["key"] == R.REPORT_KIND and "kind of business" in h["why"]
                                               for h in RK.hidden_kinds(rows, d_wire)))
check("a tenant of the module's vertical sees it", R.REPORT_KIND in vis(d_fr))
check("an UNKNOWN vertical shows less, never more (the royalty kind hidden)", R.REPORT_KIND not in vis(d_unknown))
pre = [r for r in rows if r["key"] != R.REPORT_KIND]
check("the vertical axis changes nothing else: the other kinds' visibility is identical with and without a vertical",
      [r["key"] for r in RK.visible_kinds(pre, d_wire)] == [r["key"] for r in RK.visible_kinds(pre, d_unknown)])
check("a connector-shaped row with no applies_to_vertical key applies (the shared predicate reads it with .get)",
      RK.applies({"applies_to_pos": [], "applies_to_carrier": []}, d_unknown))
check("the declaration carries the vertical and says where it came from",
      d_fr["vertical"] == scope[0] and d_fr["vertical_source"] == "tenant_vertical" and d_unknown["vertical"] is None)
check("the seed row (1022, 18-column 1010 shape) exists and its vertical is set by the module sub-select UPDATE",
      "'royalty_report', 'Franchise royalty report (monthly)'" in SQL and "AND key = 'royalty_report';" in SQL
      and R.MIGRATION in RK.SEED_MIGRATIONS)
cs_src = io.open(os.path.join(ROOT, "frontend", "src", "lib", "carrier-scope.ts"), encoding="utf-8").read()
check("the frontend twin (kindApplies) carries the same axis: no applies_to_vertical = any; unknown vertical hides",
      "const verOk = av.length === 0 || (!!dv && av.includes(dv))" in cs_src and "return posOk && carOk && verOk" in cs_src)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. THE ROUNDING RULE — total = round(basis × Σ rates); one fee absorbs the remainder")
fees = [r for r in hv if r["section"] == "fee" and r["role"] == "line"]
sch = R.fee_schedule(Decimal("40000.45"), fees)
check("synthetic STR 40,000.45 → total due 3,400.04 (8.5%, half-up)", sch["__total__"] == Decimal("3400.04"), sch)
check("the royalty fee = round(5%) = 2,000.02; the advertising-fund fee = round(2.5%) = 1,000.01",
      sch["fee_royalty"] == Decimal("2000.02") and sch["fee_naf"] == Decimal("1000.01"))
straight = (Decimal("40000.45") * Decimal("0.01")).quantize(Decimal("0.01"))
check("the ABSORBING fee = total − the others = 400.01, one cent above a straight round of 1% (400.00) — the owner's "
      "sample's shape reproduced", sch["fee_marketing"] == Decimal("400.01") and straight == Decimal("400.00"))
check("the fees always add to the total (by construction)", sum(v for k, v in sch.items() if k != "__total__") == sch["__total__"])
swapped = [dict(r, absorbs_remainder=(r["line_key"] == "fee_royalty")) for r in fees]
s2 = R.fee_schedule(Decimal("40000.45"), swapped)
check("which fee absorbs is CONFIG: moving the flag moves the remainder, the total is unchanged",
      s2["fee_marketing"] == Decimal("400.00") and s2["fee_royalty"] == Decimal("2000.03") and s2["__total__"] == sch["__total__"])
check("two absorbing fees / none are a reported configuration problem",
      any("exactly one fee" in p for p in R.vocab_problems([dict(r, absorbs_remainder=True) for r in fees])))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. THE PARSER — text, saved HTML, pasted text")
FIX = """Center Management > Royalty > Submit Royalty
Submit Royalties for Center 9001 (Royalty Period: March 2031)
Products / Services $ Amount
Mailbox Service $1,000.00
Copies $500.25
Color Copies $0.00
Shipping Charge (UPS) $10,000.00
No Limit Shipping $0.00
Packaging Materials $2,000.10
Notary $50.00
Money Orders $0.00
Products / Services $ Amount
Deposits $1.00
Sales Tax $400.00
Total Gross Sales $13,951.35
Exclusions $ Amount $ Adjustment $ Adjusted Amount Adjustment Reason
Stamp Cost $0.00
Public Service Payment C… $0.00
Sales Tax $400.00
Deposits $1.00
Other 1 $0.00 $0.00 $0.00
iShip Proc Fee $50.90
Total Exclusions $451.90 $0.00 $451.90
Commissions $ Amount $ Adjustment $ Adjusted Amount Adjustment Reason
Money Transfer $0.00
Other 1 $26,501.00
Other 2 $0.00 $0.00 $0.00
Total Commissions $26,501.00 $0.00 $26,501.00
Subject to Royalty $ Amount $ Adjustment $ Adjusted Amount
N/A N/A
Total Gross Sales $13,951.35
Total Exclusions ($451.90) $0.00 ($451.90)
Total Commissions $26,501.00 $0.00 $26,501.00
Total STR $40,000.45 $0.00 $40,000.45
Total Adjusted STR $40,000.45
Royalty Fees $ Fee Amount
Royalty Due (5%) $2,000.02
Marketing Due (1%) $400.01
NAF Due (2.5%) $1,000.01
Total Due $3,400.04
Your last sign in was on Monday | Contact Support | Terms of Use
"""
p1 = R.parse(FIX, hv)
lk = {(l["section"], l["line_key"]): l for l in p1["lines"]}
check("center + period read from the header (the 'Center Management' breadcrumb is not a center code)",
      p1["center"] == "9001" and p1["period_label"] == "March 2031", (p1["center"], p1["period_label"]))
check("all five sections found, in order", p1["sections_seen"] == ["sales", "exclusion", "commission", "str", "fee"], p1["sections_seen"])
check("the same label in three sections lands on three keys (Sales Tax / Deposits / Money Transfer / Other 1)",
      ("sales", "sales_tax") in lk and ("exclusion", "excl_sales_tax") in lk and ("commission", "comm_other_1") in lk
      and ("exclusion", "excl_other_1") in lk)
check("a truncated label ('Public Service Payment C…') resolves to the ONE line it can be",
      ("exclusion", "excl_public_service_payment_cost") in lk)
check("three figures read as amount / adjustment / adjusted; one figure = amount = adjusted",
      lk[("exclusion", "excl_other_1")]["adjustment"] == 0 and lk[("commission", "comm_other_1")]["adjusted_amount"] == Decimal("26501.00"))
check("the STR echo prints exclusions in parentheses → read as NEGATIVE", p1["totals"]["str_exclusions"]["amount"] == Decimal("-451.90"))
check("a fee line's printed rate is read ('(2.5%)' → 0.025)", lk[("fee", "fee_naf")]["printed_rate"] == 0.025)
check("totals are totals, not lines (Total Gross Sales twice: the sales total and the STR echo)",
      "gross_sales_total" in p1["totals"] and "str_gross_sales" in p1["totals"] and not any(l["role"] != "line" for l in p1["lines"]))
check("nothing unknown in a clean report; footer text ignored", p1["unknown"] == [])
HTML = ("<html><head><style>td{}</style><script>var x='$9.99';</script></head><body><h2>Submit Royalties for Center 9001 "
        "(Royalty Period: March 2031)</h2><table>" + "".join(
            "<tr><td>%s</td>%s</tr>" % (ln.split(" $")[0].split(" (")[0] if "$" in ln else ln,
                                        "".join("<td><input type='text' value='%s'></td>" % v.replace("$", "").replace(",", "")
                                                for v in re.findall(r"\(?\$[\d,]+\.\d{2}\)?", ln)))
            for ln in FIX.split("\n") if ln.strip()) + "</table></body></html>")
p2 = R.parse(HTML, hv)
check("saved HTML with the amounts in <input value=…> cells parses to the same lines as the text",
      [(l["line_key"], l["amount"]) for l in p2["lines"] if "rate" not in l["line_key"]]
      == [(l["line_key"], l["amount"]) for l in p1["lines"]], [(l["line_key"], str(l["amount"])) for l in p2["lines"]][:6])
check("script/style bodies are not read as figures", not any(l["amount"] == Decimal("9.99") for l in p2["lines"]))
SPLIT = FIX.replace("Mailbox Service $1,000.00", "Mailbox Service\n$1,000.00")
check("a label on its own line followed by a figures-only line joins", any(l["line_key"] == "mailbox_service" and l["amount"] == Decimal("1000.00")
                                                                         for l in R.parse(SPLIT, hv)["lines"]))
UNK = FIX.replace("Notary $50.00", "Notary $50.00\nDrone Delivery $12.34")
pu = R.parse(UNK, hv)
check("a label the vocabulary does not know is KEPT as its own line (never dropped) and listed",
      pu["unknown"] == ["Drone Delivery"] and any(l["line_key"] == "unknown_sales_drone_delivery" for l in pu["lines"]))
check("no report lines → nothing parsed (the endpoint refuses in words)", R.parse("hello world", hv)["lines"] == [])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. VALIDATION — the report's own figures, and the rule")
v1 = R.validate(p1, hv)
check("the clean synthetic report validates ok (sections add, STR = GS − excl + comm, fees by the rule)", v1["status"] == "ok", v1["flags"])
check("computed == reported for STR and total due", v1["computed"]["str"] == 40000.45 and v1["computed"]["total_due"] == 3400.04
      and v1["reported"]["str"] == 40000.45)
BAD = FIX.replace("Marketing Due (1%) $400.01", "Marketing Due (1%) $400.00")
pb = R.parse(BAD, hv)
vb = R.validate(pb, hv)
codes = sorted({f["code"] for f in vb["flags"]})
check("a STRAIGHT-rounded 1%% fee is FLAGGED (fee + fee lines vs total), with expected / reported / the cent", codes == ["fee", "fee_sum"]
      and next(f for f in vb["flags"] if f["code"] == "fee")["diff"] == -0.01, vb["flags"])
check("…and the REPORT's figure is what is stored (never corrected)",
      next(r for r in R.line_rows(pb) if r["line_key"] == "fee_marketing")["amount"] == 400.00
      and R.header_fields(pb, vb)["status"] == "flagged")
BAD2 = FIX.replace("Copies $500.25", "Copies $500.35").replace("NAF Due (2.5%)", "NAF Due (3%)")
vb2 = R.validate(R.parse(BAD2, hv), hv)
c2 = {f["code"] for f in vb2["flags"]}
check("a line that does not add to its section total and a printed rate that differs from config are flagged",
      {"section_sum", "fee_rate"} <= c2, c2)
check("an unknown label is itself a flag (it books nothing until mapped)", "unknown_label" in {f["code"] for f in R.validate(pu, hv)["flags"]})
NOTOT = "\n".join(l for l in FIX.split("\n") if not l.startswith("Total Due"))
check("a missing total is reported, not assumed", "missing_total" in {f["code"] for f in R.validate(R.parse(NOTOT, hv), hv)["flags"]})
cfg_str = R.resolve_config({"fee_basis": "str"})
check("the fee basis is config ('str' or 'adjusted_str'; an unknown value falls back to the default)",
      cfg_str["fee_basis"] == "str" and R.resolve_config({"fee_basis": "nonsense"})["fee_basis"] == "adjusted_str")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. THE P&L MAPPING — every line books, is excluded with its reason, or is reported unmapped")
stored = R.line_rows(p1)
bk, cov = R.pl_bookings([{"store_ref": "S1", "lines": stored}], hv)
bsum = {}
for k, st, amt, dl in bk:
    bsum[k] = round(bsum.get(k, 0) + amt, 2)
check("sales book to their heads: service 1,550.25 · shipping 10,000.00 · merchandise 2,000.10",
      bsum.get("service_sales") == 1550.25 and bsum.get("shipping_sales") == 10000.00 and bsum.get("merchandise_sales") == 2000.10, bsum)
check("commissions book their ADJUSTED amount to commission income (26,501.00)", bsum.get("commission_income") == 26501.00)
check("the fees book the REPORT's figures (2,000.02 / 400.01 / 1,000.01)",
      (bsum.get("royalty_fee"), bsum.get("marketing_fee"), bsum.get("ad_fund_fee")) == (2000.02, 400.01, 1000.01))
check("every booking carries the store and the report's line label as its drill-down detail",
      all(st == "S1" for _k, st, _a, _d in bk) and ("service_sales", "S1", 1000.0, "Mailbox Service") in bk)
check("sales tax, deposits and the exclusions book nothing — EXCLUDED, each with its reason",
      set(cov["excluded"]) == {"sales_tax", "deposits", "excl_sales_tax", "excl_deposits", "excl_iship_proc_fee"}
      and all(e["reason"] for e in cov["excluded"].values()), cov["excluded"])
check("totals / echoes / zero lines never book", not any(k.endswith("_total") for k in cov["booked"]) and len(bk) == 9, len(bk))   # 5 sales + 1 commission + 3 fees
bku, covu = R.pl_bookings([{"store_ref": None, "lines": R.line_rows(pu)}], hv)
check("an unknown line is UNMAPPED and reported with its amount (the class: silence is closed)",
      covu["unmapped"].get("unknown_sales_drone_delivery", {}).get("amount") == 12.34)
nokey = [dict(r, pl_note=None) if r["line_key"] == "sales_tax" else r for r in hv]
_b, covn = R.pl_bookings([{"store_ref": None, "lines": stored}], nokey)
check("a line with neither a P&L key nor a reason is UNMAPPED (nobody decided), not excluded",
      "sales_tax" in covn["unmapped"] and covn["unmapped"]["sales_tax"]["why"] == "no P&L line and no reason configured")
badkey = [dict(r, pl_line_key="not_a_line") if r["line_key"] == "copies" else r for r in hv]
_b, covk = R.pl_bookings([{"store_ref": None, "lines": stored}], badkey, pl_lines={"service_sales"})
check("a key that is not a line of the chart is unmapped, never guessed", "copies" in covk["unmapped"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. THE RECONCILIATION — royalty (monthly, per line) vs the daily report(s)")
vr = [dict(r, daily_categories=["SHIP-UPS", "Shipping"]) if r["line_key"] == "shipping_charge" else r for r in hv]
daily = [
    {"date": "2031-03-01", "category": "SHIP-UPS", "amount": 6000.00},
    {"date": "2031-03-02", "category": "Shipping", "amount": 3999.00},
    {"date": "2031-03-01", "category": "Copies", "amount": 500.25},          # matched by name (no categories configured)
    {"date": "2031-03-03", "category": "Mailbox Service", "amount": 900.00},
    {"date": "2031-03-04", "category": "Gift Wrap", "amount": 25.00},        # nobody claims it
]
rc = R.reconcile(stored, vr, daily, "category")
byl = {l["line_key"]: l for l in rc["lines"]}
check("a configured line sums its categories: shipping royalty 10,000.00 vs daily 9,999.00 → variance 1.00",
      byl["shipping_charge"]["daily"] == 9999.00 and byl["shipping_charge"]["variance"] == 1.00 and byl["shipping_charge"]["status"] == "variance")
check("…with the days and categories behind the figure",
      byl["shipping_charge"]["days"] == {"2031-03-01": 6000.0, "2031-03-02": 3999.0}
      and byl["shipping_charge"]["categories"] == {"SHIP-UPS": 6000.0, "Shipping": 3999.0})
check("a line with no configured categories matches a category spelled like its label (and says so)",
      byl["copies"]["status"] == "tie" and byl["copies"]["matched_by"] == "same name")
check("mailbox: royalty 1,000.00 vs daily 900.00 → variance 100.00", byl["mailbox_service"]["variance"] == 100.00)
check("a royalty line with no daily match at all is 'no_daily_map', not a false tie", byl["notary"]["status"] == "no_daily_map")
check("a category no line claims is REPORTED with its days", rc["unmapped_daily"] == [{"category": "Gift Wrap", "amount": 25.0, "rows": 1,
                                                                                       "days": {"2031-03-04": 25.0}}], rc["unmapped_daily"])
check("totals: royalty sales 13,951.35 vs daily 11,424.25", rc["totals"]["royalty_sales"] == 13951.35 and rc["totals"]["daily_total"] == 11424.25)
conf = [dict(r, daily_categories=["Shipping"]) if r["line_key"] in ("shipping_charge", "rapid_air") else r for r in hv]
check("a category two lines claim is a reported conflict", R.reconcile(stored, conf, daily)["conflicts"] ==
      [{"category": "Shipping", "lines": ["rapid_air", "shipping_charge"]}])
check("the match field is config (department instead of category)",
      R.reconcile(stored, vr, [{"date": "2031-03-01", "department": "SHIP-UPS", "amount": 10000}], "department")["lines"][0]["status"] in ("tie", "no_daily_map"))
check("tenders are a TOTAL-level cross-check; none = not measured (None), never $0.00",
      R.tender_crosscheck(13951.35, []) is None and R.tender_crosscheck(13951.35, [{"close_date": "2031-03-01", "amount": 13000}])["variance"] == 951.35)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("H. COST CENTERS + PROFIT CENTERS — a scope family and a regrouping, never a second P&L")
centers = [C.normalise_center(x) for x in [
    {"center_type": "profit", "code": "PC-REGION", "name": "Region"},
    {"center_type": "profit", "code": "PC-9001", "name": "Center 9001", "parent_code": "PC-REGION", "external_ref": "9001"},
    {"center_type": "profit", "code": "PC-9002", "name": "Center 9002", "parent_code": "PC-REGION", "external_ref": "9002"},
    {"center_type": "profit", "code": "PC-OLD", "name": "Closed", "is_active": False},
    {"center_type": "cost", "code": "CC-OCC", "name": "Occupancy"},
    {"center_type": "cost", "code": "CC-RENT", "name": "Rent", "parent_code": "CC-OCC"},
    {"center_type": "cost", "code": "CC-FRAN", "name": "Franchise fees"},
    {"center_type": "cost", "code": "CC-BAD", "name": "Orphan", "parent_code": "NOPE"}]]
resolve = lambda s: {"store a": "100 Main St", "100 main st": "100 Main St", "b": "200 Oak Ave"}.get(str(s).lower(), s)
idx, dupes = C.store_map_index([{"store_ref": "store a", "profit_center_code": "PC-9001"},
                                {"store_ref": "b", "profit_center_code": "PC-9002"},
                                {"store_ref": "100 MAIN ST", "profit_center_code": "PC-9002"}], resolve)
check("stores resolve through the P&L's resolver; one store mapped to two centers is a reported conflict",
      idx == {"100 Main St": "PC-9001", "200 Oak Ave": "PC-9002"} and dupes == {"100 Main St": ["PC-9001", "PC-9002"]})
sc = C.profit_center_scopes(centers, idx)
scd = {k: (lbl, st, cw) for k, lbl, st, cw in sc}
check("one scope per ACTIVE profit center (`profit_center:<code>`, never company-wide); a parent holds its children's stores",
      set(scd) == {"profit_center:PC-REGION", "profit_center:PC-9001", "profit_center:PC-9002"}
      and scd["profit_center:PC-REGION"][1] == {"100 Main St", "200 Oak Ave"} and scd["profit_center:PC-9001"][1] == {"100 Main St"}
      and not any(cw for _l, _s, cw in scd.values()))
check("no profit center ⇒ no scope (the engine's list is byte-identical)", C.profit_center_scopes([c for c in centers if c["center_type"] == "cost"], idx) == [])
check("a report's center number finds its store through the profit center's external reference",
      C.store_for_center("9001", centers, idx) == ("100 Main St", None) and C.store_for_center("7777", centers, idx)[0] is None)
check("tree problems are reported (a missing parent), never re-parented", any("NOPE" in p for p in C.tree_problems(C.centers_of(centers, "cost"))))
pl = {"sections": [
    {"type": "revenue", "lines": [{"key": "service_sales", "label": "Service sales", "amount": 1550.25,
                                   "detail": {"Mailbox Service": 1000.0, "Copies": 550.25}},
                                  {"key": "shipping_sales", "label": "Shipping", "amount": 10000.0, "detail": {}}]},
    {"type": "cogs", "lines": []},
    {"type": "opex", "lines": [{"key": "store_opex", "label": "Store opex", "amount": 3000.0, "detail": {"Rent": 2500.0, "Power": 500.0}},
                               {"key": "royalty_fee", "label": "Royalty", "amount": 2000.02, "detail": {"Royalty Due": 2000.02}}]},
    {"type": "other", "lines": []}]}
tags = C.tag_index([{"pl_line_key": "store_opex", "detail_label": "Rent", "cost_center_code": "CC-RENT"},
                    {"pl_line_key": "store_opex", "detail_label": "", "cost_center_code": "CC-OCC"},
                    {"pl_line_key": "royalty_fee", "detail_label": "", "cost_center_code": "CC-FRAN"}])
cv = C.cost_center_view(pl, tags, centers)
cvd = {c["code"]: c for c in cv["centers"]}
check("a detail-level tag takes its detail; the line tag takes the rest; untagged lines go to 'untagged'",
      cvd["CC-RENT"]["own"]["opex"] == 2500.0 and cvd["CC-OCC"]["own"]["opex"] == 500.0
      and cvd["CC-FRAN"]["own"]["opex"] == 2000.02 and cvd[None]["own"]["revenue"] == 11550.25)
check("the view TIES to the statement to the cent (every dollar in exactly one center or untagged)",
      cv["tie"] and cv["statement"]["net_income"] == round(11550.25 - 5000.02, 2))
check("a parent rolls up its children (Occupancy = its own 500 + Rent 2,500)", cvd["CC-OCC"]["rolled_up"]["opex"] == 3000.0)
cv2 = C.cost_center_view(pl, C.tag_index([{"pl_line_key": "shipping_sales", "cost_center_code": "CC-NOPE"}]), centers)
check("a tag naming a center that does not exist is reported, and still ties", cv2["unknown_codes"] == ["CC-NOPE"] and cv2["tie"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("I. THE UNCLAIMED SALE LINE — config routes it, the rest is REPORTED (every tenant)")
REV = {"service_sales", "shipping_sales", "merchandise_sales", "accessory_rev"}
ix, probs = S.normalise_rules([
    {"match_field": "department", "match_value": "Services", "pl_line_key": "service_sales"},
    {"match_field": "category", "match_value": "Shipping", "pl_line_key": "shipping_sales"},
    {"match_field": "product", "match_value": "Gift Wrap", "pl_line_key": "merchandise_sales"},
    {"match_field": "category", "match_value": "Rent", "pl_line_key": "store_opex"},
    {"match_field": "category", "match_value": "Old", "pl_line_key": "service_sales", "is_active": False}], REV)
check("a rule naming a non-revenue line is REJECTED and reported", any("store_opex" in p for p in probs) and "rent" not in ix["category"])
check("precedence: product > category > department; case-insensitive",
      S.match(ix, "Services", "shipping", "Gift Wrap")[0] == "merchandise_sales"
      and S.match(ix, "services", "SHIPPING", "x")[0] == "shipping_sales" and S.match(ix, "SERVICES", "", "")[0] == "service_sales")
check("no rule matches → None (the caller tallies it as unbooked); an inactive rule never matches",
      S.match(ix, "Other", "Old", "") is None)
t = S.Tally()
t.unclaimed("Shipping Dept", "UPS Ground", 10.0, "S1")
t.unclaimed("Shipping Dept", "UPS Ground", 5.5, "S2")
t.suppressed_by_royalty("shipping_sales", "Shipping Dept", "Shipping", 99.0, "S1")
t.booked_by_map("service_sales", "department", "Services", 20.0, "S1")
side = t.side_line()
check("the tally is line-shaped (no dollars on by_store / company_wide — no reader can book it)",
      side["by_store"] == {} and side["company_wide"] == 0.0 and side["detail"] == {})
check("unbooked lines are grouped by department × category with amount, rows and stores",
      side["unbooked"] == [{"department": "Shipping Dept", "category": "UPS Ground", "amount": 15.5, "rows": 2, "stores": ["S1", "S2"]}]
      and side["unbooked_total"] == 15.5)
check("a line suppressed because the royalty report books its target is reported, not booked",
      side["suppressed"][0]["pl_line_key"] == "shipping_sales" and side["suppressed"][0]["amount"] == 99.0)
check("rule-booked lines are listed too (what the map did is visible)", side["mapped"][0]["pl_line_key"] == "service_sales")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("J. MANUAL ENTRY — the same shape, one validation, one writer")
ent = [{"line_key": "mailbox_service", "amount": 1000}, {"line_key": "copies", "amount": 500.25},
       {"line_key": "shipping_charge", "amount": 10000}, {"line_key": "packaging_materials", "amount": 2000.10},
       {"line_key": "notary", "amount": 50}, {"line_key": "deposits", "amount": 1}, {"line_key": "sales_tax", "amount": 400},
       {"line_key": "excl_sales_tax", "amount": 400}, {"line_key": "excl_deposits", "amount": 1},
       {"line_key": "excl_iship_proc_fee", "amount": 50.90}, {"line_key": "comm_other_1", "amount": 26501},
       {"line_key": "str_adjusted", "amount": 40000.45},
       {"line_key": "fee_royalty", "amount": 2000.02}, {"line_key": "fee_marketing", "amount": 400.01}, {"line_key": "fee_naf", "amount": 1000.01},
       {"line_key": "copies_blank", "amount": ""}]
pm = R.fill_totals(R.from_manual(ent, hv), hv)
vm = R.validate(pm, hv)
check("a manual entry with blank totals validates ok — and the derived totals are MARKED derived",
      vm["status"] == "ok" and set(pm["derived_totals"]) == {"gross_sales_total", "exclusions_total", "commissions_total", "fee_total", "str_total"},
      (vm["flags"], pm["derived_totals"]))
check("a blank amount is skipped; the manual lines equal the parsed lines' bookings",
      sorted(R.pl_bookings([{"store_ref": "S1", "lines": R.line_rows(pm)}], hv)[0]) == sorted(bk))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("K. LANDING IDENTITY — the royalty kind names its page and where it shows")
check("the kind lands in royalty_report_line through its module page (not Upload Files)",
      LI.landing_table_for(roy, {}) == "royalty_report_line" and LI.where_to_upload(roy)["screen"] == "royalty_report")
si = LI.shows_in(roy, {})
check("'this upload will show in': the report page, the P&L, the royalty recon",
      [c["screen"] for c in si["consumers"]] == ["royalty_report", "pl_statement", "royalty_recon"])
check("the module page's screen is a ScreenLink key the lock pins", "royalty_report" in LI.screen_keys() and "royalty_recon" in LI.screen_keys())
check("the kind is on no generic upload surface (no route key, no intake landing) — its page is the one place it is uploaded",
      all(R.REPORT_KIND not in [r["key"] for r in RK.for_surface([roy], s)] for s in ("intake", "upload", "wizard", "email_imports", "tiles")))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("L. MANY MONTHS IN ONE GO — the lookback is config; the plan is pure (index §37.10)")
check("the lookback is a house-default config key: 24", R.CONFIG_DEFAULT["lookback_months"] == 24 and R.resolve_config(None)["lookback_months"] == 24)
check("an org row overrides it; blank / non-number / out-of-range reads the default",
      R.resolve_config({"lookback_months": 6})["lookback_months"] == 6 and R.resolve_config({"lookback_months": "12"})["lookback_months"] == 12
      and all(R.resolve_config({"lookback_months": v})["lookback_months"] == 24 for v in (None, "", "abc", 0, -3, 121, 9999)))
mig27 = io.open(os.path.join(ROOT, "database", "migrations", "1027_royalty_lookback_months.sql"), encoding="utf-8").read()
check("the migration's CHECK bounds are the code's bounds (one fact, mirrored and pinned)",
      "BETWEEN %d AND %d" % R.LOOKBACK_BOUNDS in mig27 and "ADD COLUMN IF NOT EXISTS lookback_months" in mig27 and "-- REVERT:" in mig27)
W = R.lookback_window("September 2026", 24)
check("the window: this month and the 24 before it, oldest first, canonical (September 2024 … September 2026)",
      len(W) == 25 and W[0] == "September 2024" and W[-1] == "September 2026" and W[16] == "January 2026")
check("the window crosses a year boundary cleanly and accepts either spelling of 'this month'",
      R.lookback_window("2026-02", 3) == ["November 2025", "December 2025", "January 2026", "February 2026"])
try:
    R.lookback_window("soon", 24)
    check("a non-month 'this month' raises (never guessed)", False)
except ValueError:
    check("a non-month 'this month' raises (never guessed)", True)


def it(i, name, center="9001", period="June 2026", error=None):
    return {"index": i, "file_name": name, "center": center, "period": period, "error": error, "status": "ok", "flags": []}


items = [it(0, "a"), it(1, "b", period="July 2026"), it(2, "c", period="August 2024"), it(3, "d", period="October 2026"),
         it(4, "e", period="May 2026"), it(5, "f", period="May 2026"), it(6, "g", period=None), it(7, "h", center=None),
         it(8, "i", error="no report lines were found"), it(9, "j", center="9002", period="May 2026"), it(10, "k", period="September 2024")]
on = {("9001", "July 2026"): {"id": "r1", "total_due": 10, "status": "ok"}}
pl = {r["file_name"]: r for r in R.batch_plan(items, W, on)}
check("a clean in-window file is ready and new", pl["a"]["ready"] and not pl["a"]["replace"])
check("an on-file center × month is ready and flagged REPLACE with the existing report", pl["b"]["ready"] and pl["b"]["replace"] and pl["b"]["existing"]["id"] == "r1")
check("older than the window → refused naming the window; the window's first month → ready",
      not pl["c"]["ready"] and "older than the 24-month window (September 2024 – September 2026)" in pl["c"]["refusals"][0] and pl["k"]["ready"])
check("a future month → refused", not pl["d"]["ready"] and "future" in pl["d"]["refusals"][0])
check("the same center × month twice → BOTH refused, each naming the other; another center's same month is fine",
      not pl["e"]["ready"] and not pl["f"]["ready"] and "in f in this batch" in pl["e"]["refusals"][0]
      and "in e in this batch" in pl["f"]["refusals"][0] and pl["j"]["ready"])
check("no period / no center / an unreadable file → refused in words (never skipped, never guessed)",
      not pl["g"]["ready"] and "period could not be read" in pl["g"]["refusals"][0]
      and not pl["h"]["ready"] and "center could not be read" in pl["h"]["refusals"][0]
      and pl["i"]["refusals"] == ["no report lines were found"])
check("one row out per file in, in order (a refused file is never dropped)", [r["index"] for r in R.batch_plan(items, W, on)] == list(range(11)))
check("a validation flag never blocks (a flagged report is stored as printed, as a single import)",
      R.batch_plan([dict(it(0, "a"), status="flagged", flags=[{"code": "x"}])], W, {})[0]["ready"])
cv = R.coverage(W, [{"id": "r1", "center_code": "9001", "period": "July 2026", "status": "ok"},
                    {"id": "r2", "center_code": "9002", "period": "2026-07", "status": "flagged"},
                    {"id": "r3", "center_code": "9001", "period": "June 2026"},
                    {"id": "r4", "center_code": "9001", "period": "June 2020"}])
jl = next(m for m in cv["months"] if m["period"] == "July 2026")
jn = next(m for m in cv["months"] if m["period"] == "June 2026")
check("coverage: every window month listed; either stored spelling counts; a month outside the window is ignored",
      len(cv["months"]) == 25 and [x["center_code"] for x in jl["on_file"]] == ["9001", "9002"] and cv["centers"] == ["9001", "9002"]
      and not any(x["id"] == "r4" for m in cv["months"] for x in m["on_file"]))
check("coverage names the centers MISSING a month", jn["missing"] == ["9002"] and cv["months"][0]["missing"] == ["9001", "9002"])
out = R.batch_outcome([{"ok": True, "file_name": "a"}, {"ok": False, "file_name": "b", "error": "x"}])
check("the outcome sentence names every file not imported and says the others stay imported",
      out.startswith("Imported 1 of 2 file(s).") and "b — x" in out and "stay imported" in out)
# negative controls — the rules must be able to go RED
check("NEGATIVE: a window of 3 refuses what the default accepted", not R.batch_plan([it(0, "a", period="May 2026")], R.lookback_window("September 2026", 3), {})[0]["ready"])
check("NEGATIVE: with the duplicate removed, the other file is ready", R.batch_plan([it(4, "e", period="May 2026")], W, {})[0]["ready"])
check("NEGATIVE: an empty on-file index flags no replace", not R.batch_plan([it(1, "b", period="July 2026")], W, {})[0]["replace"])

print("\n══ franchise royalty / centers: %d passed, %d failed ══" % (P, F))
sys.exit(1 if F else 0)
