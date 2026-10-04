"""PROOF: the phone goes INVENTORY → COGS when it sells, once, and nothing pays for it twice.

OWNER REPORT 2026-09-29, verbatim: *"Profit & Loss - device purchase cost is not coming in boost
this should come from teh assest landing , teh asset landing report for everyweel charges for teh
phones due , cogs will be those phones which are activated and sold , the rest of the phones will
become a part of the inventory till they are sold and automatically move to cogs and out of the
inventory cost"*

THREE CLAIMS, AND WHAT IS ACTUALLY TRUE (house org 00000000-…-0001, measured 2026-09-29)
────────────────────────────────────────────────────────────────────────────────────────
(1) "device purchase cost is not coming in boost" — HALF TRUE, and the half that is false is the
    dangerous half. `device_cost` is NOT absent: the consolidated P&L books it every month
    ($75,172.69 in August 2026, $80,413.50 in September), from the POS as `ext − gp`. What is
    absent is the DISTRIBUTOR's per-unit charge, because `account_config.device_cogs_mode` is
    `'off'` for this org (`'auto'` on LuxeLink — which is why the owner sees it on one tenant and
    not the other). §A pins that mechanism.

(2) "it should come from the asset landing" — the asset ledger IS already wired as the consignment
    COGS source (`device_cogs._vip_sold_cost`) and IS already the configured balance-sheet payable
    basis for this org (`distributor_payable_basis = 'asset_ledger'`). Nothing new is needed to
    source it. §B drives the REAL resolver over that ledger's own columns.

(3) "sold ⇒ COGS, the rest stay in inventory and move automatically" — the COGS HALF IS BUILT AND
    CORRECT; THE INVENTORY HALF IS NOT BUILT FROM THIS LEDGER. `_vip_sold_cost` recognises
    `owed_to_vip` in the month of `date_sold`, so an unsold unit never reaches COGS and the same
    unit reaches it in the month it sells, exactly once (§B). But nothing books the unsold units of
    THAT ledger as an asset: `coa.build_inputs` books `inventory` only from `asset_ledger` rows
    whose **status** reads `'on inventory'`, and that column carries only `'Open'` / `'Paid In
    Full'` / NULL — 0 of 35,346 live rows match (the dead predicate already recorded in index §23n).
    The balance-sheet `inventory` line comes from a DIFFERENT feed entirely (the emailed b2bsoft
    report, `inventory_value` / `inventory_aging_device`). So the two halves of the owner's sentence
    are measured by two unrelated feeds at two unrelated cost bases, and "leaves inventory at the
    same moment it enters COGS" is not something any code guarantees. §C is the specification for
    the missing half, written as a pure reference derivation over the ledger's OWN columns, with the
    periodic-inventory identity proved to close.

⚠️ AND THE THING NOBODY ASKED ABOUT, WHICH WOULD HAVE MIS-STATED PROFIT BY ~$400k A MONTH
─────────────────────────────────────────────────────────────────────────────────────────
The house P&L ALREADY expenses these handsets — on a CASH basis, on a different line.
`coa` books `vip_device_pay` ("Distributor device payments (PayGo, paid)") from
`vip_paygo_payments` approved batches, unconditionally, with NO gate on whether `device_cogs` is
active. Measured, August 2026 house consolidated: `vip_device_pay` $398,355.01 in COGS, against an
asset-ledger sale-time cost of $424,668.36 for the same month. Those are the same phones counted
two ways. Switching `device_cogs_mode` to `'auto'` without suppressing the cash line books BOTH.
§D demonstrates that arithmetic on the fixture and pins that both lines live in the same COGS
section of the spec with nothing arbitrating between them.

DATED SNAPSHOTS, EVERYTHING ELSE SYNTHETIC
──────────────────────────────────────────
The live dollar figures above are in this docstring, not in an assertion — they are what the data
said on 2026-09-29 and they will legitimately move. Every number ASSERTED below is synthetic, and
every IMEI is synthetic (an IMEI is a device identifier tied to a customer; real ones never enter a
fixture). What is asserted is the MECHANISM.

WHAT IS ASSERTED
  §A  THE REPORTED DEFECT, REPRODUCED. `'off'` and `'pos'` return active=False with a stated
      reason, so the caller books the POS figure and the distributor's charge never lands. `'auto'`
      over the same ledger lands it. One config row is the whole difference.
  §B  THE TRANSITION, END TO END, over the REAL `device_cogs._vip_sold_cost`: a unit invoiced in
      month A and unsold is in NO month's COGS; the same unit sold in month B is in B's COGS and
      B's alone; the year's COGS equals the cost of the units that sold and not a cent more; a
      second ledger row for the same handset is charged once; fee categories never enter.
  §C  THE MISSING HALF, SPECIFIED. A pure as-of derivation over the ledger's own columns
      (`acquired_date`, `date_sold`, `owed_to_vip`) and the periodic-inventory identity
      `inventory(end) = inventory(start) + purchases − COGS`, proved to close to the cent on the
      same fixture the COGS side is proved on. The ledger carries everything the asset side needs.
  §D  THE DOUBLE COUNT. Cash-basis `vip_device_pay` and sale-time `device_cost` are both COGS lines
      in `coa.PL_SPEC`, and on the fixture they sum to twice the handsets' cost.
  §E  ORG SCOPE. Every read the resolver makes carries org_id; another tenant's rows never appear.
  §F  RULE TWO. No tenant, carrier, distributor or company name decides anything in device_cogs.py.

DB-FREE BY CONSTRUCTION: `_harness_dbfree.install()` patches the client chokepoint and tripwires the
real constructor; every read goes through the in-memory FakeClient below. stdlib only.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _harness_dbfree                                                          # noqa: E402

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, detail))


def section(t):
    print("\n" + t)
    print("-" * len(t))


ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "11111111-1111-1111-1111-111111111111"


# ── the in-memory client (no sockets, no credentials, no supabase) ────────────────────────────────
class _Q:
    def __init__(self, rows, seen):
        self._rows, self._seen = list(rows), seen

    def select(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, _n):
        return self

    def eq(self, col, val):
        self._seen.add(col)
        return _Q([r for r in self._rows if str(r.get(col, "")) == str(val)], self._seen)

    def in_(self, col, vals):
        self._seen.add(col)
        want = {str(v) for v in vals}
        return _Q([r for r in self._rows if str(r.get(col, "")) in want], self._seen)

    def range(self, a, b):
        return _Q(self._rows[a:b + 1], self._seen)

    def execute(self):
        return type("R", (), {"data": self._rows})()


class FakeClient:
    """Serves the fixture tables. An unknown table is an EMPTY table, never an exception — the same
    degradation the real readers are written against. Records every filter column so §E can PROVE
    org scoping rather than assume it."""

    def __init__(self, tables):
        self.tables, self.filters, self._schema = tables, set(), "commcalc"

    def schema(self, name):
        self._schema = name
        return self

    def table(self, name):
        return _Q(self.tables.get("%s.%s" % (self._schema, name), []), self.filters)


_harness_dbfree.install(FakeClient({}))

from app.modules.account import coa                                             # noqa: E402
from app.modules.account import device_cogs                                     # noqa: E402
from app.modules.account import balance_sheet                                  # noqa: E402


# ── the fixture: one consignment ledger, five handsets, told as a story ──────────────────────────
# SYNTHETIC IMEIs. The shape is the live feed's: one row per unit, `acquired_date` = the week the
# distributor billed it ("the asset landing report for every week charges for the phones due"),
# `date_sold` = the day it was activated and sold, `owed_to_vip` = what the distributor charged.
STORE_A = "1 S 60th St"
STORE_B = "2701 Germantown Ave"

#             imei                store    category               owed    acquired      sold
LEDGER = [
    # U1 — billed in JANUARY, still on the shelf at January month-end, SOLD IN FEBRUARY.
    #      This one row is the owner's whole sentence.
    ("990000000000001", STORE_A, "Sold and Reimbursed",   629.99, "2026-01-08", "2026-02-14"),
    # U2 — billed and sold inside JANUARY.
    ("990000000000002", STORE_A, "Sold and Reimbursed",   529.99, "2026-01-09", "2026-01-22"),
    # U3 — billed in JANUARY and STILL UNSOLD at the end of the year. Never COGS; always inventory.
    ("990000000000003", STORE_B, "On Inventory. NET60",   449.99, "2026-01-15", None),
    # U4 — billed in FEBRUARY, sold in MARCH, at the other store.
    ("990000000000004", STORE_B, "Sold and Reimbursed",   799.99, "2026-02-03", "2026-03-11"),
    # U5 — a SECOND ledger row for the SAME physical handset as U1 (the live feed carries these:
    #      an amendment/re-bill). One handset, one charge — ever.
    ("990000000000001", STORE_A, "Sold with $0.00 Discount", 629.99, "2026-01-08", "2026-02-14"),
    # fee rows — already booked on `vip_fees` by coa; they are NOT device cost and must never enter.
    ("990000000000006", STORE_A, "SIM KIT",                 15.00, "2026-01-08", "2026-01-22"),
    ("990000000000007", STORE_A, "PROCESSING FEE",           9.50, "2026-02-01", "2026-02-14"),
    ("990000000000008", STORE_B, "SHIPPING",                12.25, "2026-02-01", "2026-03-11"),
]

# The four HANDSETS (U5 is U1 again) and what each cost. The arithmetic every section checks against.
U1, U2, U3, U4 = 629.99, 529.99, 449.99, 799.99


def _ledger_rows(org=ORG):
    return [{"org_id": org, "esn_imei": i, "store": s, "category": c, "status": "Open",
             "owed_to_vip": o, "acquired_date": a, "date_sold": d,
             "selling_price": round(o * 1.1, 2)}
            for (i, s, c, o, a, d) in LEDGER]


def _client(extra=None):
    t = {"commcalc.asset_ledger": _ledger_rows()}
    t.update(extra or {})
    return FakeClient(t)


def _in_period(date_str, pm, py):
    """The caller's period predicate — coa._in_period verbatim in behaviour, kept local so this
    harness proves device_cogs against the contract it is CALLED with, not against a copy of it."""
    return coa._in_period(date_str, pm, py)


def _resolve_store(s):
    return s


def _cogs_for(month, year, mode="auto", client=None, org=ORG):
    """The REAL entry point `coa.build_inputs` calls, for one month."""
    keys = ["%04d-%02d" % (year, month)]
    return device_cogs.resolve(client or _client(), org, keys, month, year,
                               _in_period, _resolve_store, mode)


# ══ §A — THE REPORTED DEFECT, REPRODUCED ═════════════════════════════════════════════════════════
section("A. The reported defect: one config row is the whole of 'not coming in boost'")

for off_mode in ("off", "pos"):
    r = _cogs_for(2, 2026, mode=off_mode)
    check("mode=%r → active=False (caller keeps the POS figure; the distributor's charge never lands)"
          % off_mode, r["active"] is False, repr(r))
    check("mode=%r says WHY, so a $0 is never mistaken for a measurement" % off_mode,
          "skipped" in r["meta"] and off_mode in str(r["meta"]["skipped"]), repr(r["meta"]))
    check("mode=%r books nothing at all" % off_mode,
          not r["by_store"] and not r["company_wide"], repr(r))

r_auto = _cogs_for(2, 2026, mode="auto")
check("mode='auto' over the SAME ledger lands the distributor's charge",
      r_auto["active"] is True and round(sum(r_auto["by_store"].values()), 2) == U1,
      repr(r_auto))
check("the only difference between them is the config row, not the data",
      _cogs_for(2, 2026, mode="off")["meta"]["mode"] == "off"
      and r_auto["meta"]["mode"] == "auto")


# ══ §B — THE TRANSITION, END TO END ══════════════════════════════════════════════════════════════
section("B. Unsold sits in inventory; sold moves to COGS; once, in the month it sold")

jan = _cogs_for(1, 2026)
feb = _cogs_for(2, 2026)
mar = _cogs_for(3, 2026)
apr = _cogs_for(4, 2026)


def _total(res):
    return round(sum(res["by_store"].values()) + res["company_wide"], 2)


# B1 — the owner's sentence, one unit at a time.
check("B1  U1 billed in January and unsold at month-end is NOT in January COGS",
      _total(jan) == U2, "%s (expected only U2=%s)" % (_total(jan), U2))
check("B2  U1 sold in February IS in February COGS, automatically, with no upload and no flag",
      _total(feb) == U1, _total(feb))
check("B3  U4 billed in February but sold in March is in MARCH, not February",
      _total(mar) == U4 and _total(feb) == U1, "feb=%s mar=%s" % (_total(feb), _total(mar)))
check("B4  a month in which nothing sold books nothing (and says so, rather than inventing a zero)",
      _total(apr) == 0.0 and apr["meta"]["vip"]["sold_in_period"] == 0, repr(apr["meta"].get("vip")))

# B5 — EXACTLY ONCE, across the whole boundary. The sum over every month of the year must equal the
# cost of the units that actually sold — no unit counted in two months, none lost between them.
year = [_total(_cogs_for(m, 2026)) for m in range(1, 13)]
check("B5  Σ(every month of 2026) == the cost of the units that SOLD, to the cent",
      round(sum(year), 2) == round(U1 + U2 + U4, 2),
      "%s vs %s  months=%s" % (round(sum(year), 2), round(U1 + U2 + U4, 2), year))
check("B6  no unit is in two months at once (each month's total is disjoint by construction)",
      sorted(v for v in year if v) == sorted([U2, U1, U4]), year)

# B7 — U3 never sells. It is in no month's COGS, ever. That is the balance-sheet half's whole job.
check("B7  the unit that never sold is in NO month's COGS",
      round(sum(year), 2) == round(U1 + U2 + U4, 2) and U3 not in year, year)

# B8 — the duplicate ledger row for U1 is charged once, not twice.
check("B8  a second ledger row for the same handset is charged ONCE (dedup by IMEI)",
      _total(feb) == U1 and feb["meta"]["vip"]["dedup_dropped"] >= 1,
      "feb=%s meta=%s" % (_total(feb), feb["meta"].get("vip")))

# B9 — fee categories are vip_fees, never device cost. Booking them here would double-count them.
check("B9  SIM KIT / PROCESSING FEE / SHIPPING never enter device COGS",
      _total(jan) == U2 and _total(feb) == U1 and _total(mar) == U4,
      "jan=%s feb=%s mar=%s" % (_total(jan), _total(feb), _total(mar)))

# B10 — the money lands at the store the ledger names, per month.
check("B10 February's cost lands at U1's store and nowhere else",
      feb["by_store"] == {STORE_A: U1}, repr(feb["by_store"]))
check("B11 March's cost lands at U4's store and nowhere else",
      mar["by_store"] == {STORE_B: U4}, repr(mar["by_store"]))


# ══ §C — THE MISSING HALF, SPECIFIED ═════════════════════════════════════════════════════════════
section("C. The asset side the ledger already supports and nothing yet books")

# `coa` books the BS `inventory` line from this ledger under ONE predicate: status == 'on inventory'.
# The live column carries 'Open' / 'Paid In Full' / NULL. Pin the predicate against that vocabulary
# so the reason the asset side reads $0 is a proved fact and not a claim.
LIVE_STATUS_VOCAB = ("Open", "Paid In Full", None, "")
check("C1  the shipped asset-side predicate (status=='on inventory') matches NOTHING the feed carries",
      not any((str(s or "").strip().lower() == "on inventory") for s in LIVE_STATUS_VOCAB),
      repr(LIVE_STATUS_VOCAB))
check("C2  'On Inventory' is a CATEGORY value, not a status — which is why the predicate never fires",
      any("on inventory" in (c or "").lower() for (_i, _s, c, _o, _a, _d) in LEDGER)
      and all(str(r["status"]).strip().lower() != "on inventory" for r in _ledger_rows()))


def unsold_as_of(rows, as_of):
    """The REAL asset side (mig 1041): `balance_sheet.asset_ledger_unsold_cells`, flattened to
    {store: value} so §C reads as it did when this was a local reference.

    It WAS a local reference in this file — the specification §42.3 asked for. It is now production
    code, and this harness drives the production function, because a specification that stays in a
    harness is a specification nothing books from."""
    cells, _meta = balance_sheet.asset_ledger_unsold_cells(rows, as_of)
    return {st: c["value"] for st, c in cells.items()}


def purchases_in(rows, start, end):
    """The REAL purchases term: `balance_sheet.asset_ledger_purchases`."""
    total, _meta = balance_sheet.asset_ledger_purchases(rows, start, end)
    return total


rows = _ledger_rows()


def inv(as_of):
    return round(sum(unsold_as_of(rows, as_of).values()), 2)


# C3 — the story, told from the asset side, must be the mirror image of §B.
check("C3  at 2026-01-31 inventory holds U1 (billed, not yet sold) and U3",
      inv("2026-01-31") == round(U1 + U3, 2), inv("2026-01-31"))
check("C4  at 2026-02-28 U1 has LEFT inventory (it sold on the 14th) and U4 has arrived",
      inv("2026-02-28") == round(U3 + U4, 2), inv("2026-02-28"))
check("C5  at 2026-03-31 only the unit that never sold remains",
      inv("2026-03-31") == U3, inv("2026-03-31"))
check("C6  U2 (billed and sold inside January) is in NO month-end inventory",
      inv("2026-01-31") == round(U1 + U3, 2) and U2 not in (inv("2026-01-31"), inv("2026-02-28")))

# C7 — THE PERIODIC-INVENTORY IDENTITY. This is the whole of "moves to cogs and out of the
# inventory cost": every dollar that leaves inventory in a month is a dollar that entered COGS in
# that month, and every dollar billed either sits or is expensed. It must close to the cent, every
# month, or one of the two halves is lying.
MONTH_ENDS = {1: ("2025-12-31", "2026-01-31", "2026-01-01"),
              2: ("2026-01-31", "2026-02-28", "2026-02-01"),
              3: ("2026-02-28", "2026-03-31", "2026-03-01"),
              4: ("2026-03-31", "2026-04-30", "2026-04-01")}
for m, (prev_end, this_end, first) in sorted(MONTH_ENDS.items()):
    opening, closing = inv(prev_end), inv(this_end)
    bought = purchases_in(rows, first, this_end)
    sold = _total(_cogs_for(m, 2026))
    check("C7.%d  inventory(open) + purchases − COGS == inventory(close)   [%s + %s − %s == %s]"
          % (m, opening, bought, sold, closing),
          round(opening + bought - sold, 2) == closing,
          "%s vs %s" % (round(opening + bought - sold, 2), closing))

# C8 — a unit is never in both places on the same date. The transition is a move, not a copy.
feb_cogs_imeis = {"990000000000001"}
check("C8  the unit in February's COGS is absent from February's closing inventory",
      inv("2026-02-28") == round(U3 + U4, 2)
      and all(i not in unsold_as_of(rows, "2026-02-28") for i in feb_cogs_imeis))

# C9 — the reference reads the ledger's own columns, so no new feed, no new upload, no new table.
check("C9  the asset side needs only acquired_date / date_sold / owed_to_vip — columns the ledger has",
      all(k in _ledger_rows()[0] for k in ("acquired_date", "date_sold", "owed_to_vip")))
check("C10 the asset side is PRODUCTION code, not a reference in this file",
      callable(getattr(balance_sheet, "asset_ledger_unsold_cells", None))
      and callable(getattr(balance_sheet, "asset_ledger_purchases", None)))
_c_cells, _c_meta = balance_sheet.asset_ledger_unsold_cells(rows, "2026-02-28")
check("C11 the asset side takes its population from device_cogs.FEE_CATEGORIES — ONE vocabulary, so "
      "the identity above is an identity and not a coincidence",
      round(sum(c["value"] for c in _c_cells.values()), 2) == round(U3 + U4, 2)
      and _c_meta["units"] == 2, "%s %s" % (_c_cells, _c_meta))
_unplaced = balance_sheet.asset_ledger_unsold_cells(
    rows + [{"org_id": ORG, "esn_imei": "990000000000099", "store": "", "category": "Open",
             "owed_to_vip": 111.11, "acquired_date": "2026-01-05", "date_sold": None}],
    "2026-02-28")[1]
check("C12 a unit the ledger cannot place is REPORTED, never folded into a store and never dropped",
      _unplaced["unplaced_value"] == 111.11 and _unplaced["unplaced_units"] == 1, repr(_unplaced))
check("C13 no as-of date books nothing and SAYS so, rather than guessing today",
      balance_sheet.asset_ledger_unsold_cells(rows, "")[1].get("reason"),
      repr(balance_sheet.asset_ledger_unsold_cells(rows, "")[1]))
check("C14 'is it null TODAY' is NOT the predicate: U1 sold on 2026-02-14 is still inventory on "
      "2026-02-13 (the §23z as-of discipline on a wipe-and-reinsert snapshot)",
      inv("2026-02-13") == round(U1 + U3 + U4, 2), inv("2026-02-13"))
check("C15 basis 'asset_ledger' takes the ledger as the ONLY source — no report fallback mixing two "
      "cost bases on one line",
      balance_sheet.apply_inventory_basis(
          [{"store": "ghost store", "swept_value": 9999.0}],
          {STORE_B: {"value": U3}}, "asset_ledger")
      == {STORE_B: {"value": U3, "source": "asset_ledger"}},
      repr(balance_sheet.apply_inventory_basis(
          [{"store": "ghost store", "swept_value": 9999.0}],
          {STORE_B: {"value": U3}}, "asset_ledger")))
check("C16 a manual per-store override still wins on the ledger basis (the mig-933 precedence)",
      balance_sheet.apply_inventory_basis(
          [{"store": STORE_B, "manual_value": 5.0}], {STORE_B: {"value": U3}}, "asset_ledger")
      == {STORE_B: {"value": 5.0, "source": "manual"}})


# ══ §D — THE DOUBLE COUNT NOBODY ASKED ABOUT ═════════════════════════════════════════════════════
section("D. The cash-basis line already expenses these phones")

spec = {k: (label, kind) for (k, label, kind, *_rest) in coa.PL_SPEC}
check("D1  `device_cost` and `vip_device_pay` are BOTH cogs lines in the one spec",
      spec.get("device_cost", ("", ""))[1] == "cogs"
      and spec.get("vip_device_pay", ("", ""))[1] == "cogs",
      repr({k: spec.get(k) for k in ("device_cost", "vip_device_pay")}))

src = open(os.path.join(HERE, "app", "modules", "account", "coa.py"), encoding="utf-8").read()
paygo = re.search(r'add\("vip_device_pay".*', src)
check("D2  vip_device_pay is booked company-wide from the approved PayGo batches",
      paygo is not None and 'add("vip_device_pay", None,' in (paygo.group(0) if paygo else ""),
      paygo.group(0) if paygo else "not found")

# D3 — the arithmetic, on the fixture. The distributor is paid for these handsets (cash, whenever
# the batch settles) AND the handsets are expensed at sale. Two readings of one purchase.
cash_paid_feb = U1          # the batch that settled U1's invoice, in February
sale_time_feb = _total(feb)
check("D3  cash-paid + sale-time == TWICE one handset's cost when both lines book",
      round(cash_paid_feb + sale_time_feb, 2) == round(2 * U1, 2),
      "%s + %s" % (cash_paid_feb, sale_time_feb))
check("D4  nothing in the spec arbitrates between them — the two keys are independent lines",
      "device_cost" in spec and "vip_device_pay" in spec and spec["device_cost"] != spec["vip_device_pay"])


# ══ §E — ORG SCOPE ═══════════════════════════════════════════════════════════════════════════════
section("E. Every read is org-scoped and another tenant's rows never appear")

c = FakeClient({"commcalc.asset_ledger": _ledger_rows(ORG) + _ledger_rows(OTHER_ORG)})
mixed = _cogs_for(2, 2026, client=c)
check("E1  org_id is a filter on every read the resolver makes", "org_id" in c.filters, repr(c.filters))
check("E2  a second tenant's identical ledger does not change this tenant's COGS by a cent",
      _total(mixed) == U1, _total(mixed))
c2 = FakeClient({"commcalc.asset_ledger": _ledger_rows(ORG) + _ledger_rows(OTHER_ORG)})
check("E3  and the other tenant sees its own, not ours",
      _total(_cogs_for(2, 2026, client=c2, org=OTHER_ORG)) == U1)


# ══ §F — RULE TWO ════════════════════════════════════════════════════════════════════════════════
section("F. No tenant, carrier, distributor or company name decides anything")

dc_src = open(os.path.join(HERE, "app", "modules", "account", "device_cogs.py"),
              encoding="utf-8").read()
BANNED = ("boost", "luxelink", "luxlink", "novawave", "nova wave", "cellfonz", "vidapay",
          "t-cetra", "b2bsoft", "acima", "epay")


def _deciding_strings(source):
    """Every string constant device_cogs.py could DECIDE on: the AST's string constants, minus the
    docstrings (prose, which is where the carrier names legitimately are — the module explains WHY
    it is carrier-agnostic) and minus the arguments of `_log.*` calls (an operator-facing message,
    which steers nothing). What is left is what a branch, a comparison or a lookup table can read."""
    import ast
    tree = ast.parse(source)
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None and node.body and isinstance(node.body[0], ast.Expr):
                skip.add(id(node.body[0].value))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "_log":
            for a in node.args:
                skip.add(id(a))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip]


deciding = " | ".join(_deciding_strings(dc_src)).lower()
hits = sorted({w for w in BANNED if w in deciding})
check("F1  no tenant/carrier/distributor name is readable by a branch, comparison or lookup table",
      not hits, "found: %s" % hits)
check("F1b the banned-word list is not vacuous (a planted control the check must catch)",
      _deciding_strings('X = "boost"\n') == ["boost"]
      and _deciding_strings('"""boost"""\nimport os\n') == [])
check("F2  the module's only gate is a per-org config value, not a name",
      "device_cogs_mode" in dc_src and "account_config" in dc_src)



# ══ §G — THE LOCK: ONE BASIS HOME, DEREFERENCED, AND IT CANNOT UN-WIRE ═══════════════════════════
# Owner directive 2026-09-20: "if one thing is fixed for one tenant it should be a design fix not a
# temporary fix". A design fix ships with a check that FAILS THE BUILD if a caller stops
# dereferencing the shared fact, or if a second copy appears. This is that check.
section("G. One device-cost basis home, every caller dereferences it, and it cannot un-wire")

import ast                                                                      # noqa: E402

ACCT_DIR = os.path.join(HERE, "app", "modules", "account")
BASIS_HOME = "device_cogs.py"
BASIS_FNS = ("resolve_device_cost_basis", "load_basis")


def _acct_sources():
    out = {}
    for fn in sorted(os.listdir(ACCT_DIR)):
        if fn.endswith(".py"):
            out[fn] = open(os.path.join(ACCT_DIR, fn), encoding="utf-8").read()
    return out


SRCS = _acct_sources()

# G1 — THE HOME EXISTS AND IS PURE. The resolver takes values, not a client: a decision that needs a
# database cannot be proved, and cannot be reused by the honest-zero path or by a config screen.
check("G1  the one home exists and the decision itself is PURE (values in, decision out)",
      all(hasattr(device_cogs, f) for f in BASIS_FNS)
      and device_cogs.resolve_device_cost_basis("asset_ledger")["accrual"] is True)

# G2 — THE THREE CONSEQUENCES ARE INSEPARABLE. Exhaustive over every input the resolver accepts:
# accrual is NEVER true without BOTH the cash suppression and the ledger inventory. This is the
# check that makes "one basis, never two" a property rather than a comment.
_INPUTS = list(device_cogs.DEVICE_COST_BASES) + ["", None, "off", "ASSET_LEDGER", "nonsense"]
_LEGACY = ["off", "pos", "auto", "invoice", "", None, "nonsense"]
_bad = []
for ob in _INPUTS:
    for cp in _INPUTS:
        for lg in _LEGACY:
            d = device_cogs.resolve_device_cost_basis(ob, cp, lg)
            if d["accrual"] != d["suppress_cash_cogs"] or d["accrual"] != d["ledger_inventory"]:
                _bad.append((ob, cp, lg, d))
            if d["accrual"] and d["cogs_mode"] != "invoice":
                _bad.append((ob, cp, lg, d))
check("G2  accrual ⇒ cash line suppressed AND ledger inventory, for EVERY input (%d combinations)"
      % (len(_INPUTS) * len(_INPUTS) * len(_LEGACY)), not _bad, repr(_bad[:3]))

# G3 — UNDECLARED IS BYTE-IDENTICAL. No declaration anywhere ⇒ the legacy mode passes through
# untouched and nothing is suppressed or forced. This is what makes the change inert on every live
# org until the owner declares a basis (house 'off', LuxeLink 'auto', Vzone 'off' as at 2026-10-03).
_bad = []
for lg in ("off", "pos", "auto", "invoice"):
    d = device_cogs.resolve_device_cost_basis(None, "", lg)
    if (d["cogs_mode"] != lg or d["suppress_cash_cogs"] or d["ledger_inventory"]
            or d["basis"] != device_cogs.BASIS_UNDECLARED):
        _bad.append((lg, d))
check("G3  no declaration ⇒ the legacy device_cogs_mode passes through and NOTHING is suppressed "
      "or forced (every live org is byte-identical until it is declared)", not _bad, repr(_bad))
check("G3b an UNDECLARED org already on 'auto'/'invoice' has its double-book REPORTED, not silently "
      "corrected (correcting it unasked would restate that org's books)",
      device_cogs.resolve_device_cost_basis(None, "", "auto")["double_book_risk"] is True
      and device_cogs.resolve_device_cost_basis(None, "", "off")["double_book_risk"] is False)

# G4 — PRECEDENCE IS THE mig-954 PRECEDENCE, not a second one.
check("G4  ORG OVERRIDE > CARRIER PRESET > legacy floor",
      device_cogs.resolve_device_cost_basis("pos", "asset_ledger", "auto")["basis"] == "pos"
      and device_cogs.resolve_device_cost_basis(None, "asset_ledger", "off")["basis"] == "asset_ledger"
      and device_cogs.resolve_device_cost_basis("nonsense", "nonsense", "auto")["basis"]
      == device_cogs.BASIS_UNDECLARED)
check("G4b an unknown value at either level is IGNORED, never trusted (mig 954's own rule)",
      device_cogs.resolve_device_cost_basis("marketplace_due", "", "off")["basis"]
      == device_cogs.BASIS_UNDECLARED)

def _names_in(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | \
           {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)} | \
           {n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _decides_on(src, needle):
    """Every string constant the module could DECIDE on (docstrings and comments excluded — prose is
    where the explanation legitimately lives)."""
    tree = ast.parse(src)
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if ast.get_docstring(node, clean=False) is not None and node.body \
                    and isinstance(node.body[0], ast.Expr):
                skip.add(id(node.body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in skip and needle in n.value]


# G5 — NOBODY ELSE READS `device_cogs_mode`. The legacy column is the thing that would let a caller
# reach a different answer, so reading it outside the home is a build failure. (The config LOADER in
# coa still carries the column for the settings screen; what is banned is deciding on it.)
# The legacy column may only be NAMED by the one home, by the mig-611 config LOADER (coa) and by the
# config WRITER (router) — a setter has to name the column it saves. Everywhere else, naming it is a
# second decision waiting to diverge. G5b is the sharper half: inside coa the name may be BOUND once
# and only from the resolved decision, so no edit can quietly restore a direct branch.
_MODE_READ_ALLOWED = {BASIS_HOME, "coa.py", "router.py"}
_viol = [fn for fn, src in SRCS.items()
         if fn not in _MODE_READ_ALLOWED and _decides_on(src, "device_cogs_mode")]
check("G5  `device_cogs_mode` is decided on in ONE home — no other module in the account package "
      "can branch on it and reach a different answer", not _viol, repr(_viol))
_binds = [n for n in ast.walk(ast.parse(SRCS["coa.py"]))
          if isinstance(n, ast.Assign)
          and any(isinstance(t, ast.Name) and t.id == "device_cogs_mode" for t in n.targets)]
check("G5b inside coa the legacy mode is BOUND once, and only from the resolved decision",
      len(_binds) == 1 and "cogs_mode" in _names_in(_binds[0].value)
      and "_basis" in _names_in(_binds[0].value),
      "%d bindings" % len(_binds))


# G6 — NO SECOND COPY OF THE DECLARATION. Only the one home may select the column out of
# account_config; a second reader is a second resolution waiting to diverge.
_viol = [fn for fn, src in SRCS.items()
         if fn != BASIS_HOME and 'select("device_cost_basis")' in src]
check("G6  only the one home SELECTS `device_cost_basis` out of account_config — a second reader is "
      "a second resolution waiting to diverge", not _viol, repr(_viol))
check("G6b and the home really does select it (the check is not vacuous)",
      'select("device_cost_basis")' in SRCS[BASIS_HOME])

# G7 — THE CASH LINE CANNOT BE BOOKED ALONGSIDE THE ACCRUAL LINE. The `add("vip_device_pay", ...)`
# call must be lexically INSIDE a guard that reads the resolved suppression. This is the check that
# would have gone red on the shipped code, and it is the one the owner's "no bandaid" depends on:
# the two lines can never both book, whatever anybody edits later.
coa_tree = ast.parse(SRCS["coa.py"])
_cash_calls, _guarded = 0, 0


def _is_cash_booking(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "add"
            and node.args and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "vip_device_pay")


def _walk_guards(node, guards):
    """Walk every node, carrying the list of `If` tests it is lexically INSIDE. An `If` passes its
    own test down to its body (and NOT to its orelse, which is the unguarded branch)."""
    global _cash_calls, _guarded
    if _is_cash_booking(node):
        _cash_calls += 1
        if any("suppress_cash_cogs" in _names_in(t) for t in guards):
            _guarded += 1
    if isinstance(node, ast.If):
        for sub in node.body:
            _walk_guards(sub, guards + [node.test])
        for sub in node.orelse:
            _walk_guards(sub, guards)
        _walk_guards(node.test, guards)
        return
    for child in ast.iter_child_nodes(node):
        _walk_guards(child, guards)


_walk_guards(coa_tree, [])
check("G7  every `vip_device_pay` booking in coa.py is inside a guard that reads the resolved "
      "`suppress_cash_cogs` — the cash line cannot be booked alongside the accrual line",
      _cash_calls >= 1 and _guarded == _cash_calls, "%d bookings, %d guarded" % (_cash_calls, _guarded))
# planted control: the walker must actually catch an UNGUARDED booking, or G7 proves nothing.
_ctl = ast.parse('def f():\n    add("vip_device_pay", None, 1)\n')
_cash_calls, _guarded = 0, 0
_walk_guards(_ctl, [])
check("G7b the guard walker is not vacuous (a planted unguarded booking must be caught)",
      _cash_calls == 1 and _guarded == 0, "%d/%d" % (_guarded, _cash_calls))

# G8 — EVERY CALLER DEREFERENCES THE HOME. The three seams that act on the basis — the P&L booking
# (coa), the balance sheet (statement_engine) and the config screen (router) — must each call it.
for fn in ("coa.py", "statement_engine.py", "router.py"):
    check("G8  %s dereferences the one home (load_basis) rather than deciding for itself" % fn,
          "load_basis(" in SRCS[fn], fn)

# G9 — ONE FEE VOCABULARY. The asset side and the COGS side must take the same population, or the
# periodic-inventory identity §C proves is a coincidence. A second literal copy is a build failure.
_viol = [fn for fn, src in SRCS.items()
         if fn != BASIS_HOME and _decides_on(src, "PROCESSING FEE")]
check("G9  the fee-category vocabulary has ONE home (device_cogs.FEE_CATEGORIES) and the asset side "
      "dereferences it — no second literal copy in the account package", not _viol, repr(_viol))
# The SIM KIT row (U6) was billed 2026-01-08 and sold 2026-01-22, so at 2026-01-10 it is "unsold".
# Excluding the fee vocabulary it contributes nothing; including it, it contributes its $15. That
# difference is the proof the asset side is reading the vocabulary rather than carrying its own.
_fees_in = round(sum(c["value"] for c in balance_sheet.asset_ledger_unsold_cells(
    rows, "2026-01-10", fee_categories=())[0].values()), 2)
_fees_out = round(sum(c["value"] for c in
                      balance_sheet.asset_ledger_unsold_cells(rows, "2026-01-10")[0].values()), 2)
check("G9b the asset side really does read that home (dropping the exclusion changes its answer by "
      "exactly the fee rows)", round(_fees_in - _fees_out, 2) == 15.00,
      "%s vs %s" % (_fees_in, _fees_out))

# G10 — A STALE LANDING READS A DECLARED ZERO, NOT A MEASURED ONE. Live, 2026-10-03: the ledger's
# newest `acquired_date` and `date_sold` are both September, so October accrual COGS is $0.00. The
# accrual basis therefore resolves to cogs_mode 'invoice', whose existing ruling-K3(b) `honest_zero`
# → `L["device_cost"]["note"]` passthrough is the ONE note mechanism — no second one is invented.
_stale = _cogs_for(10, 2026, mode=device_cogs.resolve_device_cost_basis("asset_ledger")["cogs_mode"])
check("G10 a month the landing has not reached books $0.00 BY DECLARATION, through the EXISTING "
      "honest-zero passthrough", _stale["active"] is True and _total(_stale) == 0.0
      and "honest_zero" in _stale["meta"], repr(_stale["meta"]))
check("G10b and it is NOT the POS fallback dressed up as a measurement (the accrual basis never "
      "substitutes a point-of-sale figure for a stale feed)",
      device_cogs.resolve_device_cost_basis("asset_ledger")["cogs_mode"] == "invoice")

# G10c — A LATE FEED NEVER ZEROES AN ASSET LINE. `asset_ledger_unsold_cells` reporting 0 units is a
# feed problem, not a $0 inventory, and `statement_engine` must keep the configured basis and REPORT
# it rather than booking the zero (CLAUDE.md: a defect in live data is reported, never hidden).
_se_src = SRCS["statement_engine.py"]
check("G10c statement_engine refuses to book a $0 inventory off a landing that produced no units, "
      "and reports the fallback instead",
      'if not amet.get("units"):' in _se_src
      and "fell_back_to_configured_basis" in _se_src
      and "_LedgerInventoryEmpty" in _se_src)
check("G10d …and the empty case really is empty-by-measurement, not by accident",
      balance_sheet.asset_ledger_unsold_cells([], "2026-09-30")[1]["units"] == 0
      and balance_sheet.asset_ledger_unsold_cells(rows, "2026-09-30")[1]["units"] > 0)

# G11 — RULE TWO on the new surface too.
check("G11 the basis declaration is a per-org CONFIG value, never a tenant or carrier branch",
      "account_config" in SRCS[BASIS_HOME]
      and not [w for w in BANNED if w in " | ".join(_deciding_strings(SRCS[BASIS_HOME])).lower()])


print("\n%s\n%d passed, %d failed" % ("=" * 78, P, F))
sys.exit(1 if F else 0)
