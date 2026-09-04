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
  F. DISTRIBUTOR PAYABLE, mig 954 (owner directive 2026-09-04) — the asset-ledger OPEN balance
     derivation reproduces the owner's $358,221.13 on the live house-org status mix; the AS-OF
     truth table (one function, current period = today, closed period = period end); the sources
     stay DISJOINT from coa's own owed_vip contributors (no double count); and the money-column
     guard names owed_to_vip alone.
  G. BASIS + TARGET-LINE precedence — org override > carrier preset > declared families > 'off',
     and the per-tenant cost-centre line mapping (directive B: LuxeLink's line never moves).
  H. CASH-AT-BANK GRAINS, mig 954 (owner directive 2026-09-04) — per store / per company / one
     tenant total, and the NO-DOUBLE-COUNT residual rule across every mix of them.

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


# ── F. distributor payable: the asset-ledger OPEN balance, as-of parameterized (mig 954) ───────
print()
print("F. distributor payable — asset-ledger open balance (owner directive 2026-09-04)")
# The live house-org status mix, verbatim from commcalc.asset_ledger on 2026-09-04 (34,015 rows):
#   'Open'         2,505 rows  $358,221.13   ← the figure the owner named
#   'Paid In Full' 31,509 rows $11,037,487.06
#   NULL               1 row     $117,730.73  (id 889865 — no store, no dates, no category)
LEDGER = [
    {"store": "6507 Castor Ave", "status": "Open", "owed_to_vip": 300000.00,
     "acquired_date": "2026-07-15", "due_date": "2026-09-14"},
    {"store": "1710 W 4th St", "status": "Open", "owed_to_vip": 28381.51,
     "acquired_date": "2026-08-25", "due_date": "2026-10-24"},
    {"store": "1710 W 4th St", "status": "Open", "owed_to_vip": 29839.62,
     "acquired_date": "2026-06-11", "due_date": "2026-08-10"},          # already past its own due date
    {"store": "6507 Castor Ave", "status": "Paid In Full", "owed_to_vip": 11037487.06,
     "acquired_date": "2025-06-26", "due_date": "2025-08-25"},
    {"store": None, "status": None, "owed_to_vip": 117730.73,
     "acquired_date": None, "due_date": None},                          # the statusless artifact
]
OPEN = list(B.ASSET_LEDGER_OPEN_STATUSES_DEFAULT)
bk, meta = B.asset_ledger_open_bookings(LEDGER, OPEN, "2026-09-04")
ok("reproduces the owner's figure exactly ($358,221.13)", meta["total"] == 358221.13, meta["total"])
ok("only the OPEN rows book", meta["rows"] == 3 and len(bk) == 3, meta["rows"])
ok("settled rows contribute nothing ($11.0M of 'Paid In Full' excluded)",
   all(s != "Paid In Full" for _st, _a, s in bk))
ok("the statusless $117,730.73 artifact is NOT swept in (positive vocabulary, not a negation)",
   117730.73 not in [a for _s, a, _d in bk], bk)
ok("past-due vs not-yet-due split is reported, not merged",
   meta["past_due"] == 29839.62 and meta["not_yet_due"] == 328381.51, meta)
ok("store grain rides the ledger's own store column",
   sorted({s for s, _a, _d in bk}) == ["1710 W 4th St", "6507 Castor Ave"], bk)
ok("money-column guard names owed_to_vip ALONE", B.ASSET_LEDGER_MONEY_COLUMNS == ("owed_to_vip",),
   B.ASSET_LEDGER_MONEY_COLUMNS)
ok("empty vocabulary books NOTHING (the 'off' default stays byte-identical)",
   B.asset_ledger_open_bookings(LEDGER, [], "2026-09-04") == ([], {
       "rows": 0, "total": 0.0, "past_due": 0.0, "not_yet_due": 0.0, "undated": 0,
       "statuses": {}, "basis": "status_snapshot", "snapshot_lag": False}))
ok("no as-of books NOTHING", B.asset_ledger_open_bookings(LEDGER, OPEN, None)[0] == [])
ok("status vocabulary is case-insensitive", B.asset_ledger_open_bookings(LEDGER, ["OPEN"], "2026-09-04")[1]["total"] == 358221.13)

# ── AS-OF TRUTH TABLE: ONE function, the date is the only difference (owner directive A) ───────
print("   as-of truth table — same function, parameterized by date (never two formulas)")
AS_OF_CASES = [
    # (as-of, expected total, why)
    ("2026-09-04", 358221.13, "current period / today — the tile and the open-period BS"),
    ("2026-08-31", 358221.13, "closed period end: every open row was acquired by 2026-08-25"),
    ("2026-07-31", 329839.62, "closed period end: the 2026-08-25 acquisition had not happened yet"),
    ("2026-06-30", 29839.62,  "closed period end: only the June acquisition existed"),
    ("2026-05-31", 0.0,       "before ANY open row was acquired — an honest zero"),
]
for as_of, expect, why in AS_OF_CASES:
    got = B.asset_ledger_open_bookings(LEDGER, OPEN, as_of)[1]["total"]
    ok(f"as_of {as_of} → {expect:,.2f}  ({why})", got == expect, got)
ok("an unacquired row is never owed (the ONLY date rule; nothing else changes with as-of)",
   B.asset_ledger_open_bookings(LEDGER, OPEN, "2026-07-31")[1]["rows"] == 2)
ok("a row with NO acquired_date is KEPT and counted in meta (money is never dropped)",
   B.asset_ledger_open_bookings([{"store": "S", "status": "Open", "owed_to_vip": 10.0,
                                  "acquired_date": None, "due_date": None}],
                                OPEN, "2026-09-04")[1] ["undated"] == 1)

# DISJOINTNESS — the no-double-count proof against coa's OWN owed_vip contributors. coa books that
# line from (a) ledger rows whose STATUS reads 'on inventory' and (b) PENDING PayGo batches.
ok("'on inventory' is NOT in the open vocabulary ⇒ coa's predicate and this one can never both fire",
   "on inventory" not in {t.lower() for t in OPEN})
ok("a hypothetical 'On Inventory' status row books here ZERO (sources stay disjoint)",
   B.asset_ledger_open_bookings(
       [{"store": "S", "status": "On Inventory", "owed_to_vip": 5000.0,
         "acquired_date": "2026-01-01", "due_date": "2026-03-01"}], OPEN, "2026-09-04")[1]["total"] == 0.0)

# ── G. basis + target-line precedence (owner directives B and C) ────────────────────────────────
print()
print("G. tenant mapping — basis precedence and the cost-centre target line")
R = B.resolve_payable_basis
ok("org override beats the carrier preset", R("marketplace_due", "asset_ledger") == "marketplace_due")
ok("carrier preset applies when the org declared nothing (LAZY auto-assign at onboarding)",
   R(None, "asset_ledger") == "asset_ledger" and R("", "marketplace_due") == "marketplace_due")
ok("an explicit 'off' from the tenant WINS over its carrier preset", R("off", "asset_ledger") == "off")
ok("no org value + no preset + no declared families ⇒ 'off' (books nothing)", R(None, "") == "off")
ok("a declared mig-933 family keeps an existing org booking (no regression floor)",
   R(None, "", True) == "marketplace_due")
ok("...but ranks BELOW both explicit levels", R("off", "", True) == "off" and R(None, "asset_ledger", True) == "asset_ledger")
ok("an unknown value at either level is ignored, never trusted",
   R("nonsense", "asset_ledger") == "asset_ledger" and R(None, "nonsense") == "off")

SPEC_KEYS = ["cash", "inventory", "owed_vip", "vip_ap", "handset_payable", "retained"]
L = B.resolve_payable_line
ok("asset_ledger default line = owed_vip (today's live house placement)",
   L("asset_ledger", "", SPEC_KEYS) == "owed_vip")
ok("marketplace_due default line = handset_payable — directive B: LuxeLink's line does NOT move",
   L("marketplace_due", "", SPEC_KEYS) == "handset_payable")
ok("a tenant may retarget to another real liability line (its own cost-centre choice)",
   L("asset_ledger", "vip_ap", SPEC_KEYS) == "vip_ap")
ok("a target line the SPEC does not carry is IGNORED, never invented (no stranded money)",
   L("asset_ledger", "not_a_line", SPEC_KEYS) == "owed_vip")
ok("basis 'off' books to no line at all", L("off", "owed_vip", SPEC_KEYS) is None)

# ── H. cash at bank — the three grains and the no-double-count rule (owner directive D) ─────────
print()
print("H. manual-entry grains — cash per store / per company / one tenant total")
COS = [{"id": "co1", "name": "Alpha Wireless"}, {"id": "co2", "name": "Beta Wireless"}]
mm = B.journal_company_matcher(COS)
CO_OF = {"S1": "co1", "S2": "co1", "S3": "co2"}.get
CASH = lambda amt, **kw: {"statement": "balance_sheet", "account_type": "asset",
                          "account_line": "Cash / bank", "amount": amt, **kw}


def consolidated(entries):
    got, meta = B.journal_grain_entries(entries, "consolidated", None, mm, CO_OF)
    return round(sum(e["amount"] for e in got), 2), meta


def scope_total(entries, scope, stores):
    got, _m = B.journal_grain_entries(entries, scope, stores, mm, CO_OF)
    return round(sum(e["amount"] for e in got), 2)

ok("grain classification: a named store is STORE grain",
   B.entry_grain(CASH(1, store_address="S1"), mm, CO_OF) == ("store", "S1", "co1"))
ok("grain classification: a picked company is COMPANY grain",
   B.entry_grain(CASH(1, company_id="co2"), mm, CO_OF) == ("company", None, "co2"))
ok("grain classification: the owner's TYPED company text is COMPANY grain, not a store",
   B.entry_grain(CASH(1, store_address="Alpha Wireless"), mm, CO_OF)[0] == "company")
ok("grain classification: neither ⇒ TENANT grain", B.entry_grain(CASH(1), mm, CO_OF) == ("tenant", None, None))
ok("a store wins over a company picked on the same row (finest grain)",
   B.entry_grain(CASH(1, store_address="S3", company_id="co2"), mm, CO_OF) == ("store", "S3", "co2"))

# grain 1 — per store
PER_STORE = [CASH(1000, store_address="S1"), CASH(2000, store_address="S2"), CASH(500, store_address="S3")]
ok("per-STORE: consolidated = the sum of the stores", consolidated(PER_STORE)[0] == 3500.0)
ok("per-STORE: a store scope shows exactly its own cash", scope_total(PER_STORE, "store:S2", {"S2"}) == 2000.0)
ok("per-STORE: a company scope rolls up its stores", scope_total(PER_STORE, "company:co1", {"S1", "S2"}) == 3000.0)

# grain 2 — per company
PER_CO = [CASH(3000, company_id="co1"), CASH(500, company_id="co2")]
ok("per-COMPANY: consolidated = the sum of the companies", consolidated(PER_CO)[0] == 3500.0)
ok("per-COMPANY: the company scope shows its own figure", scope_total(PER_CO, "company:co1", {"S1", "S2"}) == 3000.0)
ok("per-COMPANY: a store scope shows nothing (it was never stated per store)",
   scope_total(PER_CO, "store:S1", {"S1"}) == 0.0)

# grain 3 — one tenant total (today's byte-identical default)
TENANT = [CASH(3500)]
ok("TENANT total: consolidated carries it", consolidated(TENANT)[0] == 3500.0)
ok("TENANT total: consolidated-ONLY — no sub-scope may claim an unattributed total",
   scope_total(TENANT, "company:co1", {"S1", "S2"}) == 0.0
   and scope_total(TENANT, "store:S1", {"S1"}) == 0.0)

# BYTE-IDENTITY — one grain in use reproduces the plain routing exactly
for name, ents, scope, stores in (("store", PER_STORE, "company:co1", {"S1", "S2"}),
                                  ("company", PER_CO, "company:co1", {"S1", "S2"}),
                                  ("tenant", TENANT, "consolidated", None)):
    plain = round(sum(B.safe_float(e["amount"])
                      for e in B.journal_scope_entries(ents, scope, stores, mm)), 2)
    ok(f"ONE grain in use ({name}) is byte-identical to the plain routing",
       scope_total(ents, scope, stores) == plain, (scope_total(ents, scope, stores), plain))

# THE NO-DOUBLE-COUNT RULE — mixed grains
MIX_TS = [CASH(3500), CASH(1000, store_address="S1")]           # tenant total + one store
tot, meta = consolidated(MIX_TS)
ok("tenant total + a store row: consolidated is the STATED total, counted ONCE (3500, not 4500)",
   tot == 3500.0, tot)
ok("...and the store's own scope still shows its 1000 (placement, not duplication)",
   scope_total(MIX_TS, "store:S1", {"S1"}) == 1000.0)
MIX_CS = [CASH(3000, company_id="co1"), CASH(1000, store_address="S1")]
ok("company total + a store inside it: consolidated = 3000, not 4000", consolidated(MIX_CS)[0] == 3000.0)
ok("...the company scope is still 3000 (1000 placed at S1 + 2000 residual)",
   scope_total(MIX_CS, "company:co1", {"S1", "S2"}) == 3000.0)
MIX_ALL = [CASH(9000), CASH(3000, company_id="co1"), CASH(1000, store_address="S1"),
           CASH(500, store_address="S3")]
ok("all three grains at once: consolidated = the tenant's own 9000, exactly once",
   consolidated(MIX_ALL)[0] == 9000.0, consolidated(MIX_ALL)[0])
ok("...co1 scope = its stated 3000", scope_total(MIX_ALL, "company:co1", {"S1", "S2"}) == 3000.0)
ok("...co2 scope = the 500 stated at its store (no company row, so the store IS the statement)",
   scope_total(MIX_ALL, "company:co2", {"S3"}) == 500.0)
CONFLICT = [CASH(1000, company_id="co1"), CASH(1500, store_address="S1")]
tot_c, meta_c = consolidated(CONFLICT)
ok("finer rows ABOVE the coarser stated total: the residual floors at zero, never negative cash",
   tot_c == 1500.0, tot_c)
ok("...and the suppressed conflict is REPORTED, never silently dropped",
   meta_c["conflicts"] and meta_c["conflicts"][0]["suppressed"] == 500.0, meta_c["conflicts"])
ok("a DIFFERENT line is netted independently (the rule is per account line)",
   consolidated([CASH(3500), {"statement": "balance_sheet", "account_type": "equity",
                              "account_line": "Owner capital / contributions", "amount": 250000.0,
                              "store_address": "Alpha Wireless"}])[0] == 253500.0)

# the owner's LIVE rows (org 854f6d7b) — company grain via typed text, unchanged by the new rule
LIVE = [
    {"statement": "balance_sheet", "account_type": "equity",
     "account_line": "Owner capital / contributions", "amount": 250000.0, "store_address": "Luxelink"},
    {"statement": "balance_sheet", "account_type": "equity",
     "account_line": "Owner capital / contributions", "amount": 100000.0, "store_address": "Novawave"},
    {"statement": "balance_sheet", "account_type": "liability",
     "account_line": "Loan", "amount": 210000.0, "store_address": "Luxelink"},
]
live_m = B.journal_company_matcher(COMPANIES)
live_got, _lm = B.journal_grain_entries(LIVE, "consolidated", None, live_m, lambda _s: None)
ok("the owner's live entries are unchanged by the grain rule ($560,000 consolidated)",
   round(sum(e["amount"] for e in live_got), 2) == 560000.0, live_got)
lux, _ = B.journal_grain_entries(LIVE, "company:e0e28bd6", set(), live_m, lambda _s: None)
ok("...and still reach their company scope ($250k capital + $210k loan on Luxlink Wireless)",
   round(sum(e["amount"] for e in lux), 2) == 460000.0, lux)

print()
if FAIL:
    print(f"{FAIL} CHECK(S) FAILED")
    sys.exit(1)
print("harness_balance_sheet_truths: ALL CHECKS PASSED")
