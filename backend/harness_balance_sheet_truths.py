"""Proof harness — the balance-sheet truths (owner report 2026-09-02). Stdlib only, no DB.

Proves the pure logic in app/modules/account/balance_sheet.py:
  A. journal company matcher — run on the OWNER'S REAL ROWS (journal_entries, org 854f6d7b,
     created 2026-09-02T03:05Z): 'Novawave' → Nova Wave Communications, 'Luxelink' → Luxlink
     Wireless; ambiguity fails CLOSED (None), never a guess between two companies.
  B. journal scoping — the owner's $250k/$100k contributions + $210k loan land on their company
     scopes AND stay on consolidated; store scopes stay exact-match.
  C. handset payables — due-date window math (tx_date <= as_of < due_date), case-insensitive
     order-type families, RMA/credit netting, EMPTY config books NOTHING (byte-identity), and the
     money-column guard names retail_cost alone.
  D. unsold-phone inventory — snapshot coherence: store-NULL ghosts and superseded-as_of rows are
     EXCLUDED and REPORTED, never summed; per-store values tie to the fresh snapshot.
  E. basis precedence — manual > basis source > the other source; 'report' basis reproduces the
     swept values exactly (default byte-identity).

Run:  cd backend && python3 harness_balance_sheet_truths.py
"""
import sys

sys.path.insert(0, "app")
from app.modules.account import balance_sheet as B  # noqa: E402

FAIL = 0


def ok(name, cond, detail=None):
    global FAIL
    mark = "PASS" if cond else "FAIL"
    if not cond:
        FAIL += 1
    print(f"  [{mark}] {name}" + ("" if cond else f"  << {detail}"))


# ── A. company matcher on the owner's real rows ────────────────────────────────────────────────
print("A. journal company matcher (owner's live companies + typed designations)")
COMPANIES = [  # the live org-854f6d7b companies table, verbatim
    {"id": "1e7ff323", "name": "Default Company"},
    {"id": "e0e28bd6", "name": "Luxlink Wireless"},
    {"id": "bccc049e", "name": "Nova Wave Communications"},
]
m = B.journal_company_matcher(COMPANIES)
ok("'Novawave' (owner's typed text) → Nova Wave Communications", m("Novawave") == "bccc049e", m("Novawave"))
ok("'Luxelink' (owner's typed text, 1-letter drift) → Luxlink Wireless", m("Luxelink") == "e0e28bd6", m("Luxelink"))
ok("exact name matches case-insensitively", m("nova wave communications") == "bccc049e")
ok("squashed spelling matches", m("Luxlink-Wireless") == "e0e28bd6")
ok("blank / None → None", m("") is None and m(None) is None)
ok("a store address does not match a company", m("531 Utica Ave") is None, m("531 Utica Ave"))
ok("unrelated text → None", m("Boost Mobile") is None)

amb = B.journal_company_matcher([{"id": "a", "name": "Metro One"}, {"id": "b", "name": "Metro Two"}])
ok("ambiguous prefix between two companies fails CLOSED", amb("Metro") is None, amb("Metro"))
ok("full unique name still resolves next to its sibling", amb("Metro One") == "a")

e1 = B.journal_company_matcher([{"id": "x", "name": "Acme Wireless"}, {"id": "y", "name": "Acne Wireless"}])
ok("1-edit tolerance with a UNIQUE candidate resolves", e1("Acmee") == "x", e1("Acmee"))
ok("1-edit tolerance equidistant from TWO companies fails CLOSED", e1("Acxe W") is None, e1("Acxe W"))

# ── B. journal scoping with the owner's real entries ───────────────────────────────────────────
print("B. journal scoping (the owner's three August 2026 rows, verbatim)")
J = [
    {"statement": "balance_sheet", "account_type": "equity", "account_line": "Owner capital / contributions",
     "amount": 250000.0, "company_id": None, "store_address": "Luxelink", "memo": None},
    {"statement": "balance_sheet", "account_type": "equity", "account_line": "Owner capital / contributions",
     "amount": 100000.0, "company_id": None, "store_address": "Novawave", "memo": None},
    {"statement": "balance_sheet", "account_type": "liability", "account_line": "Loan",
     "amount": 210000.0, "company_id": None, "store_address": "Luxelink", "memo": None},
]
cons = B.journal_scope_entries(J, "consolidated", None, m)
ok("consolidated keeps all three entries", len(cons) == 3)
lux = B.journal_scope_entries(J, "company:e0e28bd6", {"531 Utica Ave"}, m)
ok("Luxlink company scope gets the $250k contribution + $210k loan",
   sorted(e["amount"] for e in lux) == [210000.0, 250000.0], [e["amount"] for e in lux])
nova = B.journal_scope_entries(J, "company:bccc049e", set(), m)
ok("Nova Wave company scope gets the $100k contribution",
   [e["amount"] for e in nova] == [100000.0], [e["amount"] for e in nova])
store = B.journal_scope_entries(J, "store:531 Utica Ave", {"531 Utica Ave"}, m)
ok("a store scope gets none of them (company designation is not a store)", store == [])
ok("an explicit company_id always wins over the typed text",
   B.journal_scope_entries([{"company_id": "bccc049e", "store_address": "Luxelink", "amount": 1.0}],
                           "company:bccc049e", set(), m)[0]["amount"] == 1.0)
ok("without a matcher the old behaviour holds (consolidated-only)",
   B.journal_scope_entries(J, "company:e0e28bd6", set(), None) == [])

# ── C. handset payables per the vendor's due dates ─────────────────────────────────────────────
print("C. handset payable due-date window (raw_ma_daily_tx families)")
TX = [
    # outstanding: transacted before as-of, due after it
    {"account_id": "170401", "order_type": "Postpaid Branded MarketPlace",
     "retail_cost": 300.0, "tx_date": "2026-08-20", "due_date": "2026-09-10"},
    {"account_id": "170402", "order_type": "POSTPAID BRANDED MARKETPLACE",   # case drift
     "retail_cost": 200.0, "tx_date": "2026-08-25", "due_date": "2026-09-15"},
    # settled: due on/before as-of
    {"account_id": "170401", "order_type": "Postpaid Branded MarketPlace",
     "retail_cost": 999.0, "tx_date": "2026-07-01", "due_date": "2026-08-01"},
    # not yet transacted at as-of
    {"account_id": "170401", "order_type": "Postpaid Branded MarketPlace",
     "retail_cost": 500.0, "tx_date": "2026-09-05", "due_date": "2026-09-25"},
    # RMA credit inside the window nets against the balance
    {"account_id": "170402", "order_type": "Postpaid Branded MarketPlace",
     "retail_cost": -50.0, "tx_date": "2026-08-26", "due_date": "2026-09-15"},
    # different family — never booked
    {"account_id": "170401", "order_type": "Postpaid Residual Order",
     "retail_cost": 400.0, "tx_date": "2026-08-20", "due_date": "2026-09-10"},
    # missing dates — honest skip
    {"account_id": "170401", "order_type": "Postpaid Branded MarketPlace",
     "retail_cost": 100.0, "tx_date": None, "due_date": "2026-09-10"},
]
bk, meta = B.handset_payable_bookings(TX, ["Postpaid Branded MarketPlace"], "2026-09-02")
ok("books exactly the outstanding rows (window + family + case-insensitive)",
   meta["rows"] == 3 and meta["total"] == 450.0, meta)
ok("credit rows keep their sign (net against the balance)",
   any(a == -50.0 for _acct, a, _d in bk))
ok("per-booking account ids carried for the store grain",
   {acct for acct, _a, _d in bk} == {"170401", "170402"})
bk0, meta0 = B.handset_payable_bookings(TX, [], "2026-09-02")
ok("EMPTY config books NOTHING (every org's default — byte-identical BS)",
   bk0 == [] and meta0["total"] == 0.0)
ok("row due exactly ON as-of counts as settled",
   B.handset_payable_bookings([{"account_id": "1", "order_type": "X", "retail_cost": 10.0,
                                "tx_date": "2026-09-01", "due_date": "2026-09-02"}],
                              ["x"], "2026-09-02")[1]["total"] == 0.0)
ok("money-column guard names retail_cost alone",
   B.HANDSET_PAYABLE_MONEY_COLUMNS == ("retail_cost",))

# LuxeLink live shape (measured 2026-09-02): 643 marketplace rows / $169,013.57 not yet due.
# The harness proves the RULE; the migration header records the measured figure.

# ── D. unsold-phone inventory coherence ────────────────────────────────────────────────────────
print("D. unsold-phone set (inventory_aging_device) — snapshot coherence")
DEV = [
    {"store": "Store A", "unit_cost": 100.0, "on_hand": True, "as_of_date": "2026-09-02"},
    {"store": "Store A", "unit_cost": 150.0, "on_hand": True, "as_of_date": "2026-09-02"},
    {"store": "Store A", "unit_cost": 999.0, "on_hand": True, "as_of_date": "2026-07-25"},  # superseded
    {"store": "Store A", "unit_cost": 80.0, "on_hand": False, "as_of_date": "2026-09-02"},  # sold
    {"store": "Store B", "unit_cost": 60.0, "on_hand": True, "as_of_date": "2026-08-30"},   # own latest
    {"store": None, "unit_cost": 70.0, "on_hand": True, "as_of_date": "2026-07-20"},        # ghost
    {"store": "", "unit_cost": 30.0, "on_hand": True, "as_of_date": "2026-07-20"},          # ghost
]
cells, dmeta = B.device_inventory_cells(DEV)
ok("per-store value = fresh on-hand rows only",
   cells["Store A"]["value"] == 250.0 and cells["Store A"]["devices"] == 2, cells.get("Store A"))
ok("a store whose file lags counts at its OWN latest snapshot",
   cells["Store B"]["value"] == 60.0 and cells["Store B"]["as_of"] == "2026-08-30")
ok("store-NULL ghosts excluded AND reported (the live $129,454.66 July set)",
   dmeta["unplaced_devices"] == 2 and dmeta["unplaced_value"] == 100.0, dmeta)
ok("superseded-as_of rows excluded AND reported",
   dmeta["superseded_devices"] == 1 and dmeta["superseded_value"] == 999.0, dmeta)

# ── E. basis precedence + recon tie-out ────────────────────────────────────────────────────────
print("E. inventory basis precedence + reconciliation rows")
INVVAL = [
    {"store": "Store A", "swept_value": 260.0, "manual_value": None, "as_of_date": "2026-09-02"},
    {"store": "Store B", "swept_value": 55.0, "manual_value": 999.99, "as_of_date": "2026-09-02"},
    {"store": "Store C", "swept_value": 40.0, "manual_value": None, "as_of_date": "2026-09-02"},
]
eff_rep = B.apply_inventory_basis(INVVAL, cells, "report")
ok("'report' basis reproduces the swept values (default byte-identity)",
   eff_rep["Store A"] == {"value": 260.0, "source": "report"}
   and eff_rep["Store C"]["value"] == 40.0, eff_rep)
ok("manual override wins under EITHER basis",
   eff_rep["Store B"] == {"value": 999.99, "source": "manual"}
   and B.apply_inventory_basis(INVVAL, cells, "devices")["Store B"]["source"] == "manual")
eff_dev = B.apply_inventory_basis(INVVAL, cells, "devices")
ok("'devices' basis reads the unsold-phone ledger", eff_dev["Store A"] == {"value": 250.0, "source": "devices"})
ok("a store with no device rows keeps the report value (coverage never regresses)",
   eff_dev["Store C"] == {"value": 40.0, "source": "report"}, eff_dev.get("Store C"))
rows, totals = B.inventory_recon_rows(INVVAL, cells, "devices")
ra = next(r for r in rows if r["store"] == "Store A")
ok("recon row carries report vs device vs effective + the delta",
   ra["report_value"] == 260.0 and ra["device_value"] == 250.0 and ra["delta"] == -10.0
   and ra["effective_source"] == "devices", ra)
ok("recon totals tie (device 310 − report 355 = −45)",
   totals["device_value"] == 310.0 and totals["report_value"] == 355.0 and totals["delta"] == -45.0, totals)

print()
if FAIL:
    print(f"{FAIL} CHECK(S) FAILED")
    sys.exit(1)
print("harness_balance_sheet_truths: ALL CHECKS PASSED")
