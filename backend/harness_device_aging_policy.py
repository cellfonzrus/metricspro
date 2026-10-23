"""Harness — the CARRIER'S terms are declared, not written in code (module 095 + the asset aging report).

OWNER REPORTS 2026-10-22, verbatim:
  * "under Luxelink it is using Boost - it should not , also it should not say sell before 60 days as
     that is boost requirement , total is sell before 12 days"
  * "there is no data appearing for the phones which were purchased as per handset ordering and not
     sold before 12 days to be apparing in the aging ... if the imei is not sold then the device has
     to be paid out of pocket whch needs to be highlighted universally"
  * "simlarly Upcoming Invoice Payment Due does not sow any data - some thing is not working ??"

ONE CLASS, FOUR SYMPTOMS: a carrier's arrangement was hard-wired. The selling window was the literals
`< 45 / 45-60 / > 60`, written in `asset/router.get_aging` AND in `commcalc/device_history`, whose
comment asked the next reader to "keep the edges in lock-step" — the admission that there were two
copies. Inventory Aging read `asset_ledger` only, and Upcoming Invoice Payment Due `vip_invoices`
only, both Boost/VIP artefacts. Net terms were reachable only through a distributor link. And a
device's cost had to be a column on its own row.

MEASURED LIVE 2026-10-22, which is what makes these facts rather than opinions:
  Luxelink  asset_ledger 0 rows · vip_invoices 0 rows · device_payable_ledger 4,288 (real IMEIs,
            every `owed` NULL and every `due_date` NULL) · raw_ma_fulfillment 1,401 ·
            raw_ma_daily_tx 2,115 dated invoices worth $2,049,592.50
  the join  activation_order -> fulfillment order_number: 0 of 4,322.
            sku -> fulfillment product_name: 3,370 of 4,322 (78%); of the 952 misses, 903 are the
            distributor's own "Product Not Available" sku, which stays NULL rather than becoming $0
  house     1,755 unsold on-inventory devices, of which 890 carry $0 owed and were aged NOWHERE

§A-§D are DB-FREE over the pure policy home. §E-§H are wiring locks that fail the build if any of
the four surfaces re-grows a literal or a private copy.
"""
import ast
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))
PASS, FAIL = [], []


def ok(c, w):
    (PASS if c else FAIL).append(w)
    print(("  PASS " if c else "  FAIL ") + w)


from app.modules.commcalc import device_aging_policy as ap                     # noqa: E402
from app.modules.payables import unsold_inventory as ui                        # noqa: E402
from app.modules.payables import vendor_invoice as vi                          # noqa: E402

TODAY = date(2026, 10, 22)
BOOST = ap.resolve_policy({"sell_within_days": 60, "label": "Boost / VIP"})
TOTAL = ap.resolve_policy({"sell_within_days": 12, "label": "Total / MA"})
HOUSE = ap.resolve_policy()

# ── §A  the ladder is DERIVED, and Boost's is unchanged to the day ─────────────────────────────
print("\n§A  resolve_policy / bucket_ladder")
ok(HOUSE["sell_within_days"] == 60 and HOUSE["warn_after_days"] == 45,
   "A1 THE EQUIVALENCE THIS CHANGE RESTS ON: with nothing declared, the house default is 45/60 — "
   "exactly the literals it replaces (45/60 == 0.75), so no tenant's ladder moves")
ok(HOUSE["declared"] is False, "A2 ...and `declared` says the carrier did NOT tell us that number")
ok(BOOST["warn_after_days"] == 45 and BOOST["sell_within_days"] == 60,
   "A3 Boost declaring 60 reproduces 45/60 identically")
ok(TOTAL["warn_after_days"] == 9 and TOTAL["sell_within_days"] == 12,
   "A4 Total declaring 12 gets 9/12 — never 45/60 inherited from another carrier")
ok(ap.bucket_ladder(TOTAL)[1][2] == "9–12 days",
   "A5 the LEGEND is derived too, so no screen prints '45-60 days' over a 12-day carrier's devices")
ok(ap.bucket_ladder(HOUSE) == (("under45", "Fresh", "< 45 days"), ("warn", "Aging", "45–60 days"),
                               ("missed", "Overaged", "> 60 days")),
   "A6 ...and the house legend is byte-identical to the strings it replaces")
ok(ap.resolve_policy({"sell_within_days": 1})["warn_after_days"] == 0,
   "A7 a 1-day window has no room for a warning and never produces a warn edge at or past the window")
ok(ap.resolve_policy({"sell_within_days": 0})["sell_within_days"] == 60
   and ap.resolve_policy({"sell_within_days": "x"})["declared"] is False,
   "A8 a zero or unreadable window is NOT declared — it falls back, never to a zero-day deadline")
ok(ap.resolve_policy({"sell_within_days": 12, "warn_fraction": 0.5})["warn_after_days"] == 6,
   "A9 the warn edge follows a declared fraction")
ok(ap.resolve_policy({"sell_within_days": 12, "warn_fraction": 2})["warn_after_days"] == 9
   and ap.resolve_policy({"sell_within_days": 12, "warn_fraction": 0})["warn_after_days"] == 9,
   "A10 a fraction outside (0,1) is refused in favour of the default, so the ladder cannot invert")

# ── §B  bucketing ──────────────────────────────────────────────────────────────────────────────
print("\n§B  bucket — one comparison, two carriers")
for d, exp in [(0, "under45"), (44, "under45"), (45, "warn"), (60, "warn"), (61, "missed")]:
    ok(ap.bucket(d, BOOST) == exp, f"B1 Boost {d} days -> {exp} (unchanged from the literal ladder)")
for d, exp in [(0, "under45"), (8, "under45"), (9, "warn"), (12, "warn"), (13, "missed")]:
    ok(ap.bucket(d, TOTAL) == exp, f"B2 Total {d} days -> {exp}")
ok(ap.bucket(None, TOTAL) == ap.BUCKET_UNKNOWN,
   "B3 a device whose purchase date the feed does not carry is `unknown_age` — REAL inventory, "
   "never folded into a dated bucket (the 2026-07-28 reconciliation lesson, kept)")
ok(ap.days_held("2026-10-01", TODAY) == 21 and ap.days_held(None, TODAY) is None
   and ap.days_held("", TODAY) is None,
   "B4 days_held is one subtraction in one place, and no date is None rather than 0")

# ── §C  the net PAYING terms are a different fact from the SELLING window ──────────────────────
print("\n§C  due_on — terms, not the window")
ok(ap.due_on("2026-10-01", 12) == "2026-10-13", "C1 invoice date + declared net terms")
ok(ap.due_on("2026-10-01", 0) == "2026-10-01", "C2 zero-day terms are due the same day, not refused")
ok(ap.due_on("2026-10-01", None) is None and ap.due_on(None, 12) is None,
   "C3 a due date is NEVER invented from the selling window — on Boost they are different numbers")
ok(ap.due_on("not-a-date", 12) is None, "C4 an unparseable invoice date yields no due date")

# ── §D  the universal out-of-pocket verdict ────────────────────────────────────────────────────
print("\n§D  out_of_pocket — ONE meaning on every screen")
v = ap.out_of_pocket(sold=False, due_date="2026-10-13", policy=TOTAL, today=TODAY)
ok(v["out_of_pocket"] is True and v["basis"] == ap.BASIS_DUE_DATE and v["days_overdue"] == 9,
   "D1 unsold with a PAST due date -> out of pocket, dated, and the basis is named")
ok(ap.out_of_pocket(sold=False, due_date="2026-10-22", policy=TOTAL, today=TODAY)["out_of_pocket"] is True,
   "D2 due TODAY is payable today — the invoice does not wait for tomorrow")
ok(ap.out_of_pocket(sold=False, due_date="2026-11-01", policy=TOTAL, today=TODAY)["out_of_pocket"] is False,
   "D3 a future due date is not yet out of pocket")
ok(ap.out_of_pocket(sold=True, due_date="2026-01-01", policy=TOTAL, today=TODAY)["out_of_pocket"] is False,
   "D4 a SOLD device is never out of pocket, whatever its dates say — the sale funds the invoice, "
   "which is the entire point of the deadline")
v = ap.out_of_pocket(sold=False, days_held=20, policy=TOTAL, today=TODAY)
ok(v["out_of_pocket"] is True and v["basis"] == ap.BASIS_WINDOW and v["days_overdue"] == 8,
   "D5 no due date -> the carrier's SELLING window stands in, and says so")
ok(ap.out_of_pocket(sold=False, days_held=20, policy=BOOST, today=TODAY)["out_of_pocket"] is False,
   "D6 ...and 20 days is fine on a 60-day carrier — the same device, two carriers, two answers")
v = ap.out_of_pocket(sold=False, policy=TOTAL, today=TODAY)
ok(v["out_of_pocket"] is None and v["basis"] == ap.BASIS_NOT_MEASURED,
   "D7 NEITHER a due date nor a holding period -> None = NOT MEASURED. A device nothing can judge is "
   "never reported as settled")
ok(ap.out_of_pocket(sold=False, due_date="2026-10-13", days_held=1, policy=TOTAL,
                    today=TODAY)["basis"] == ap.BASIS_DUE_DATE,
   "D8 a real due date WINS over the window — the invoice is payable whatever the handset did")

# ── §E  stamp_aging — the flag and the exposure, computed once ──────────────────────────────
print("\n§E  stamp_aging")
POL = {"c-total": TOTAL, "c-boost": BOOST}
ROWS = [
    {"carrier_id": "c-total", "acquired_date": "2026-10-01", "due_date": "2026-10-13", "owed_to_vip": 529.0},
    {"carrier_id": "c-total", "acquired_date": "2026-10-20", "due_date": None, "owed_to_vip": 129.0},
    {"carrier_id": "c-total", "acquired_date": None, "due_date": None, "owed_to_vip": 99.0},
    {"carrier_id": "c-boost", "acquired_date": "2026-09-01", "due_date": None, "date_sold": "2026-09-05", "owed_to_vip": 400.0},
    {"carrier_id": "unknown-carrier", "acquired_date": "2026-01-01", "due_date": None, "owed_to_vip": 50.0},
]
summ = ui.stamp_aging(ROWS, POL, TODAY)
# The two honesty keys arrived with §COMMCALC-29: both out-of-pocket rows here carry a price, so
# nothing is unpriced and the amount is a real total. The whole dict is compared on purpose — a new
# key appearing unannounced is exactly the drift this harness exists to catch.
ok(summ == {"devices": 5, "out_of_pocket": 2, "out_of_pocket_amount": 579.0, "not_measured": 1,
            "out_of_pocket_unpriced": 0, "amount_incomplete": False},
   "E1 the exposure is counted AND priced, and the unjudgeable row is reported apart from both")
ok(ui.stamp_aging([dict(ROWS[0], owed_to_vip=None)], POL, TODAY) ==
   {"devices": 1, "out_of_pocket": 1, "out_of_pocket_amount": 0.0, "not_measured": 0,
    "out_of_pocket_unpriced": 1, "amount_incomplete": True},
   "E1b ...and a flagged device with NO price makes the amount say it is incomplete, rather than "
   "presenting $0.00 as a total (§COMMCALC-29, the 2026-10-23 defect)")
ok(ROWS[0]["out_of_pocket"] is True and ROWS[1]["out_of_pocket"] is False,
   "E2 per-row flags match the summary (one computation, not two)")
ok(ROWS[2]["aging_bucket"] == ap.BUCKET_UNKNOWN and ROWS[2]["out_of_pocket"] is None,
   "E3 a dateless device is unknown-age and unmeasured, not $0 and not fine")
ok(ROWS[3]["out_of_pocket"] is False, "E4 the sold Boost device is not out of pocket")
ok(ROWS[4]["sell_within_days"] == 60 and ROWS[4]["window_declared"] is False,
   "E5 a row whose carrier has NO map is held to the house default and SAYS it was not declared — "
   "never refused, never silently given another carrier's terms")
ok(ROWS[0]["days_aged"] == ROWS[0]["days_held"] == 21,
   "E6 the aging report's existing field name is kept, so nothing downstream has to migrate")
ok(ui.policy_for({}, None)["sell_within_days"] == 60,
   "E7 an unreadable config degrades to the house default, which is what the platform showed before "
   "any of this was declarable")

# ── §F  the vendor-invoice home — Upcoming Invoice Payment Due's missing source ─────────────
print("\n§F  vendor_invoice — invoice grain over the processor's own feed")
FEED = [
    {"merchant_invoice": 365966773.0, "due_date": "2026-10-22", "retail_cost": 100.0, "order_type": "Sales Order", "product_name": "iPhone"},
    {"merchant_invoice": 365966773.0, "due_date": "2026-10-22", "retail_cost": 50.0, "order_type": "Fee"},
    {"merchant_invoice": 0.0, "due_date": "2026-10-22", "retail_cost": 25.0, "order_type": "Fee"},
    {"merchant_invoice": 0.0, "due_date": "2026-11-02", "retail_cost": 75.0},
    {"merchant_invoice": 1.0, "due_date": "2026-10-22", "retail_cost": -5.0},
    {"merchant_invoice": 2.0, "due_date": None, "retail_cost": 10.0},
]
g = vi.group_invoices(FEED)
ok(len(g) == 3,
   "F1 THE KEY IS (invoice number, due date). Grouping by number alone would render Luxelink's 2,536 "
   "numberless lines as ONE $606,525.41 invoice due on 44 different days — a figure the feed never "
   "stated")
numbered = [e for e in g if e["numbered"]]
ok(len(numbered) == 1 and numbered[0]["lines"] == 2 and numbered[0]["amount_due"] == 150.0,
   "F2 a numbered invoice sums its own lines")
ok(numbered[0]["order_types"] == ["Fee", "Sales Order"],
   "F3 ...and names the order types inside it, so a device charge is distinguishable from a fee")
ok(all(e["invoice_label"] == vi.NO_INVOICE_LABEL for e in g if not e["numbered"]),
   "F4 a numberless group is LABELLED as such — never blank, and never an invoice called '0'")
ok(not vi.is_debt_line({"retail_cost": -5.0}) and not vi.is_debt_line({"retail_cost": 0}),
   "F5 a credit or a zero is not a payable — ONE debt test, shared with the Daily Owed tab's "
   "date-grain read so the two grains cannot disagree about the same money")
ok(vi.is_debt_line({"retail_cost": 0.01}), "F6 ...and a charge is")
s = vi.summarize(g)
ok(s["invoice_count"] == 3 and s["amount_due"] == 250.0 and s["unnumbered_invoices"] == 2
   and s["unnumbered_amount"] == 100.0,
   "F7 the summary names how much of the total the feed left unnumbered, rather than burying it")
ok(len(vi.group_invoices(FEED, "2026-11-01", "2026-11-30")) == 1,
   "F8 the window is the SAME one home every other surface reads (both ends inclusive)")
ok(all(e["due_date"] for e in g),
   "F9 an undated row is fail-closed out, exactly as it is on every other windowed surface")

# ── §G  WIRING — no surface keeps a literal or a private copy ──────────────────────────────
print("\n§G  the four surfaces dereference the one homes")


def code_only(src):
    """Source minus comments and docstrings: the ban is on CODE, not on the prose explaining it."""
    docs = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d is not None:
                docs.add(d)
    body = "\n".join(ln.split("  #")[0] for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    for d in docs:
        body = body.replace(d, "")
    return body


ASSET = code_only(open("app/modules/asset/router.py").read())
DH = code_only(open("app/modules/commcalc/device_history.py").read())
ENGINE = code_only(open("app/modules/payables/engine.py").read())
IDUE = code_only(open("app/modules/asset/invoice_due.py").read())

for name, src in (("asset/router.get_aging", ASSET), ("commcalc/device_history", DH)):
    for banned in ("< 45", "<= 60", "days < 45", "d <= 60"):
        ok(banned not in src, f"G1 {name} keeps no literal ladder ({banned!r})")
ok("_aging.bucket(" in ASSET and "_aging.bucket(" in DH,
   "G2 both bucket through the one home, so the two copies that had to be 'kept in lock-step' are one")
ok("bucket_ladder" in DH, "G3 ...and the legend is derived there too")
ok("unsold_inventory" in ASSET and "ledger_sourced_maps" in ASSET,
   "G4 the aging report's SOURCE is each carrier's declared one, not asset_ledger assumed")
ok("stamp_aging(" in ASSET,
   "G5 ...and the out-of-pocket flag it shows is the shared verdict, not a local test")
ok("net_terms_days" in ENGINE,
   "G6 the engine reads the map's own net terms — which is what Luxelink's 4,288 NULL due dates "
   "were missing")
ok("_unit_price_map(" in ENGINE and "device_cogs" in ENGINE,
   "G7 a device with no price column is priced through the P&L's OWN price home, never a second "
   "derivation that could disagree with it about the same handset")
ok("vendor_invoice" in IDUE and "group_invoices" in IDUE,
   "G8 Upcoming Invoice Payment Due falls back to the processor feed through the shared home")
# The ban is on CODE that touches the feed, not on the NOTE that names it: the user-facing note
# SHOULD say which source answered (the §15v D5b convention). So these are the code forms — a
# column read and a table read — never the prose.
for banned in ('.get("merchant_invoice")', '"merchant_invoice,', 'table("raw_ma_daily_tx")'):
    ok(banned not in IDUE,
       f"G9 ...and touches no feed column of its own ({banned}) — the grain and the debt test stay "
       f"in one place")
ok("merchant_invoice" in open("app/modules/asset/invoice_due.py").read(),
   "G9b ...while the NOTE still names the source that answered, so a reader is never left guessing")

# ── §H  negative controls: the lock must be able to go RED ─────────────────────────────────────
print("\n§H  negative controls")
ok(ap.bucket(13, BOOST) != ap.bucket(13, TOTAL),
   "H1 the two carriers genuinely disagree about 13 days — if they did not, §B would pass on a "
   "single hard-coded ladder and prove nothing")
ok(ap.out_of_pocket(sold=False, days_held=20, policy=TOTAL, today=TODAY)["out_of_pocket"]
   is not ap.out_of_pocket(sold=False, days_held=20, policy=BOOST, today=TODAY)["out_of_pocket"],
   "H2 ...and so do the verdicts, so §D is not passing on a constant")
ok(ap.resolve_policy({"sell_within_days": 12})["warn_after_days"] != 45,
   "H3 a declared window really does displace the historical edge")
ok(ui.ledger_sourced_maps([{"unsold_source": "payable_ledger"}, {"unsold_source": "source_table"},
                           {}]) == [{"unsold_source": "payable_ledger"}],
   "H4 only a map that DECLARES the ledger reads it; an unset map keeps its own snapshot, so Boost "
   "cannot be switched sources by accident")

print("\n" + "=" * 70)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
print("=" * 70)
sys.exit(1 if FAIL else 0)
