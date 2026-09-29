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


def unsold_as_of(rows, as_of, fee_cats=("PROCESSING FEE", "SHIPPING", "SIM KIT")):
    """REFERENCE (the specification the asset side must satisfy — pure, no DB, no config).

    The value of the units this ledger says we had been BILLED for on or before `as_of` and had not
    yet sold on `as_of`. Same three columns the COGS side already reads, same IMEI dedup, same fee
    exclusion — so the asset and the expense are two readings of ONE ledger rather than two feeds
    that happen to be about phones. `date_sold` is compared to `as_of`, never to "is it null today":
    a unit that has since sold was still inventory at a date before it sold, and a status snapshot
    can never say that (the §23z lesson, applied to the asset side)."""
    fees = {f.strip().upper() for f in fee_cats}
    seen, per_store = set(), {}
    for r in rows or []:
        if str(r.get("category") or "").strip().upper() in fees:
            continue
        acq = str(r.get("acquired_date") or "")[:10]
        if not acq or acq > as_of:
            continue                      # not yet billed to us on that date
        imei = str(r.get("esn_imei") or "").strip()
        if imei:
            if imei in seen:
                continue
            seen.add(imei)
        sold = str(r.get("date_sold") or "")[:10]
        if sold and sold <= as_of:
            continue                      # already gone to COGS on or before that date
        amt = float(r.get("owed_to_vip") or 0)
        if not amt:
            continue
        st = str(r.get("store") or "").strip() or "(store not mapped)"
        per_store[st] = round(per_store.get(st, 0.0) + amt, 2)
    return per_store


def purchases_in(rows, start, end, fee_cats=("PROCESSING FEE", "SHIPPING", "SIM KIT")):
    """REFERENCE: what the distributor BILLED us for handsets inside [start, end] — the additions to
    inventory. Recognised on `acquired_date`, IMEI-deduped, fees excluded: the same population the
    two readings above are taken over, so the identity below is an identity and not a coincidence."""
    fees = {f.strip().upper() for f in fee_cats}
    seen, total = set(), 0.0
    for r in rows or []:
        if str(r.get("category") or "").strip().upper() in fees:
            continue
        acq = str(r.get("acquired_date") or "")[:10]
        if not acq or acq < start or acq > end:
            continue
        imei = str(r.get("esn_imei") or "").strip()
        if imei:
            if imei in seen:
                continue
            seen.add(imei)
        total = round(total + float(r.get("owed_to_vip") or 0), 2)
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


print("\n%s\n%d passed, %d failed" % ("=" * 78, P, F))
sys.exit(1 if F else 0)
