"""PROOF + BUILD LOCK — an EXPECTED RECEIPT is declared, and the cash calendar nets ONE in against
ONE out (§COMMCALC-56).

OWNER ASK, verbatim: *"i need a report for the expected payments for teh next payout cycle by the day
as acalender display for all tenants, also the foloowing report will be how much is owed, which we
already ahave and the thent eh irs will be after offseting the payments what isteh cash flow"*.

THE CLASS THIS IS BUILT AGAINST, not the instance. "When does the carrier pay us" has never been a
fact anywhere in this platform, and the obvious build would have put it in the report — a monthly
day-of-month literal, or a weekday guessed from the last few statements. That is §COMMCALC-26.1's
class arriving on a sixth surface: *a carrier's arrangement written into code instead of declared*.
So the rhythm is DECLARED per carrier (`receipt_schedule`), the money is read through the tenant's own
DECLARED source (`expected_receipts`), and the OUT side is not re-derived at all — it is the SAME
`payables/owed_read.dated_owed` the Daily Owed surface already reads, so the calendar cannot total
something that surface disagrees with.

WHAT THIS PROVES, BY DRIVING THE REAL HOMES
  * `payables/receipt_schedule` over every cadence and every edge: the stamped statement date, the
    declared weekday, the two semi-monthly days, the month day that does not exist in February, the
    net-days lag, and — the case that matters most — NOTHING DECLARED.
  * `payables/expected_receipts` against a FAKE client answering like PostgREST, so the declared
    source, the missing declaration, the unreadable declaration and the filters are exercised for
    real.
  * `payables/cash_calendar` netting in against out across a date span, with the running position.
  * ALL FOUR TENANTS' real shapes, including the negative: one tenant's declaration must never
    answer another tenant's read.

WHAT IT LOCKS SO IT CANNOT UN-WIRE (CLAUDE.md)
  A  the cadence vocabulary and the allowlist (§66 shape) — fail-CLOSED, mutation-tested.
  B  THERE IS NO HOUSE DEFAULT CADENCE on the inbound side, and that is the point: an undeclared
     carrier reads NOT MEASURED, never "the 1st" and never today.
  C  a declared day rolls FORWARD on or after the earning date, never back.
  D  the date COLUMN is dereferenced from `data_lineage_registry`, never spelled a second time.
  E  ONE OUT READ: the calendar holds no owed query of its own and calls the shared home.
  F  nothing is dated that no cadence could date; that money is reported beside the days.
  G  a day with no measurement is `not_measured`, never $0.00 (§COMMCALC-29 / §66).
  H  RULE TWO: no carrier, tenant or product name, and no day-of-month literal, in any of the three.
  I  CROSS-TENANT: each tenant's own declaration answers, and no declaration leaks across orgs.
  J  registration: the index, the migration and its grant, and the frontend's filters + send bar.

Run: `cd backend && python3 harness_cash_calendar.py`  (DB-free, stdlib only.)
"""
import ast
import datetime
import os
import re
import sys

from app.modules.commcalc import data_lineage_registry as lineage
from app.modules.payables import cash_calendar as cc
from app.modules.payables import expected_receipts as er
from app.modules.payables import receipt_schedule as rs

PASS, FAIL = [], []
ROOT = ".."


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def ctl(name, red):
    """A MUTATION CONTROL. `red` is True when the mutated world really does break the thing the
    check above asserts — a check that cannot fail is not a check."""
    check("MUT " + name, bool(red))


def read(rel):
    p = f"{ROOT}/{rel}"
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def code_only(src):
    """Source with comments and DOCSTRINGS blanked — never all strings, because a declared column
    name lives in a string literal and blanking those would make RULE TWO checks vacuous."""
    out = re.sub(r"(?m)#.*$", "", src)
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    drop = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                drop.append(body[0].value.value)
    for s in drop:
        out = out.replace(s, "")
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §A  THE CADENCE VOCABULARY, AND THE ALLOWLIST THAT FAILS CLOSED
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§A  cadence vocabulary + allowlist\n" + "-" * 94)

check("A1 the five ways a receipt date can be known are named once, in the home",
      set(rs.CADENCES) == {rs.CADENCE_STATEMENT_DATE, rs.CADENCE_WEEKLY, rs.CADENCE_SEMI_MONTHLY,
                           rs.CADENCE_MONTHLY, rs.CADENCE_NET_DAYS})

check("A2 is_measured is an ALLOWLIST: only a basis this module has ruled on is a measurement",
      all(rs.is_measured(b) for b in rs.MEASURED_BASES))
check("A3 and a basis it has never heard of reads as NOT measured, so a cadence added later cannot "
      "become a measurement merely by appearing",
      not rs.is_measured("whatever_cadence_someone_adds_next_year")
      and not rs.is_measured(None) and not rs.is_measured("") and not rs.is_measured(0))
check("A4 the three NOT-measured bases are excluded from the allowlist by name",
      not rs.is_measured(rs.BASIS_UNDECLARED) and not rs.is_measured(rs.BASIS_DAY_UNDECLARED)
      and not rs.is_measured(rs.BASIS_NO_DATE))
ctl("A4 an allowlist that contained the undeclared basis would call an unconfigured carrier measured",
    rs.BASIS_UNDECLARED not in rs.MEASURED_BASES)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §B  THERE IS NO HOUSE DEFAULT ON THE INBOUND SIDE — THE WHOLE POINT
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§B  nothing declared means nothing identified\n" + "-" * 94)

EARNED = "2026-11-09"       # a Monday
blank = rs.resolve_schedule(None)

check("B1 an org that declared nothing resolves to NO cadence at all — not a weekday, not the 1st",
      blank["cadence"] is None and blank["declared"] is False)
check("B2 and asking for its receipt date returns None with the undeclared basis, NOT today and NOT "
      "the earning date dressed up as a payment date",
      rs.receipt_date(EARNED, blank)["date"] is None
      and rs.receipt_date(EARNED, blank)["basis"] == rs.BASIS_UNDECLARED
      and rs.receipt_date(EARNED, blank)["measured"] is False)
ctl("B2 a home that fell back to the earning date would report an unconfigured carrier as paying us "
    "the day we sold",
    rs.receipt_date(EARNED, blank)["date"] != EARNED)

check("B3 a cadence the module has never ruled on is NOT honoured — acting on it would be acting on "
      "a typo, and the carrier never said it",
      rs.resolve_schedule({"payout_cadence": "whenever_they_feel_like_it"})["cadence"] is None)

check("B4 a cadence DECLARED but with its day left blank is reported as such, and still not dated: "
      "weekly with no weekday, monthly with no day of month",
      rs.receipt_date(EARNED, rs.resolve_schedule({"payout_cadence": rs.CADENCE_WEEKLY}))["basis"]
      == rs.BASIS_DAY_UNDECLARED
      and rs.receipt_date(EARNED, rs.resolve_schedule({"payout_cadence": rs.CADENCE_MONTHLY}))["basis"]
      == rs.BASIS_DAY_UNDECLARED
      and rs.receipt_date(EARNED, rs.resolve_schedule(
          {"payout_cadence": rs.CADENCE_WEEKLY}))["date"] is None)

check("B5 and NEITHER of those is measured, so a surface cannot print a dollar on a day nobody named",
      not rs.is_measured(rs.receipt_date(EARNED, rs.resolve_schedule(
          {"payout_cadence": rs.CADENCE_WEEKLY}))["basis"]))

check("B6 an earning date that is not a date is not a receipt date either",
      rs.receipt_date(None, rs.resolve_schedule({"payout_cadence": rs.CADENCE_MONTHLY,
                                                 "payout_day_of_month": 15}))["basis"]
      == rs.BASIS_NO_DATE
      and rs.receipt_date("not a date", rs.resolve_schedule(
          {"payout_cadence": rs.CADENCE_MONTHLY, "payout_day_of_month": 15}))["date"] is None)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §C  EVERY DECLARED CADENCE, AND IT ROLLS FORWARD
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§C  the declared cadences\n" + "-" * 94)

weekly_fri = rs.resolve_schedule({"payout_cadence": rs.CADENCE_WEEKLY, "payout_weekday": 4})
check("C1 WEEKLY rolls to the first declared weekday ON OR AFTER the earning date — Monday earns, "
      "Friday pays",
      rs.receipt_date("2026-11-09", weekly_fri)["date"] == "2026-11-13"
      and rs.receipt_date(EARNED, weekly_fri)["basis"] == rs.BASIS_WEEKLY)
check("C2 and money earned ON the pay day is paid THAT day, not pushed a week out",
      rs.receipt_date("2026-11-13", weekly_fri)["date"] == "2026-11-13")
ctl("C2 an 'after' rather than 'on or after' rule would postpone a real receipt by seven days",
    rs.receipt_date("2026-11-13", weekly_fri)["date"] != "2026-11-20")

check("C3 WEEKLY dereferences the forward-roll that already has a home on the outbound side rather "
      "than carrying a second copy of the same arithmetic",
      "next_weekday_on_or_after" in read("backend/app/modules/payables/receipt_schedule.py")
      and "billing_period" in read("backend/app/modules/payables/receipt_schedule.py"))

monthly_15 = rs.resolve_schedule({"payout_cadence": rs.CADENCE_MONTHLY, "payout_day_of_month": 15})
check("C4 MONTHLY pays on the declared day of month, this month when it is still ahead and next "
      "month when it has passed",
      rs.receipt_date("2026-11-09", monthly_15)["date"] == "2026-11-15"
      and rs.receipt_date("2026-11-16", monthly_15)["date"] == "2026-12-15"
      and rs.receipt_date("2026-11-15", monthly_15)["date"] == "2026-11-15")

monthly_31 = rs.resolve_schedule({"payout_cadence": rs.CADENCE_MONTHLY, "payout_day_of_month": 31})
check("C5 a declared 31st in a month that has no 31st pays on that month's LAST day — it does not "
      "skip the month and it does not spill into the next one",
      rs.receipt_date("2026-02-20", monthly_31)["date"] == "2026-02-28"
      and rs.receipt_date("2026-04-05", monthly_31)["date"] == "2026-04-30")

semi = rs.resolve_schedule({"payout_cadence": rs.CADENCE_SEMI_MONTHLY, "payout_days": [5, 20]})
check("C6 SEMI-MONTHLY takes the nearest declared day on or after, in order, and wraps to the first "
      "declared day of next month once both have passed",
      rs.receipt_date("2026-11-01", semi)["date"] == "2026-11-05"
      and rs.receipt_date("2026-11-06", semi)["date"] == "2026-11-20"
      and rs.receipt_date("2026-11-21", semi)["date"] == "2026-12-05")
check("C7 and the declared days are read in whatever order they were saved — a tenant that typed "
      "them backwards gets the same answer",
      rs.receipt_date("2026-11-01", rs.resolve_schedule(
          {"payout_cadence": rs.CADENCE_SEMI_MONTHLY, "payout_days": [20, 5]}))["date"]
      == "2026-11-05")

net21 = rs.resolve_schedule({"payout_cadence": rs.CADENCE_NET_DAYS, "payout_net_days": 21})
check("C8 NET DAYS adds the declared lag to the earning date and says that is what it did",
      rs.receipt_date("2026-11-09", net21)["date"] == "2026-11-30"
      and rs.receipt_date("2026-11-09", net21)["basis"] == rs.BASIS_NET_DAYS)
check("C9 a zero lag is a DECLARED same-day payment, not an undeclared one",
      rs.receipt_date("2026-11-09", rs.resolve_schedule(
          {"payout_cadence": rs.CADENCE_NET_DAYS, "payout_net_days": 0}))["date"] == "2026-11-09")

stamped = rs.resolve_schedule({"payout_cadence": rs.CADENCE_STATEMENT_DATE})
check("C10 STATEMENT DATE takes the date the feed itself stamped and NEVER recomputes one — the "
      "carrier's own answer wins, exactly as the outbound stamped period does",
      rs.receipt_date_of_row({"paid_on": "2026-11-21"}, stamped, date_field="paid_on")["date"]
      == "2026-11-21"
      and rs.receipt_date_of_row({"paid_on": "2026-11-21"}, stamped,
                                 date_field="paid_on")["basis"] == rs.BASIS_STATEMENT_FIELD)
check("C11 and a statement-dated row with no stamp is NOT dated by any other rule — the cadence "
      "said the feed answers, so an unstamped row is unmeasured rather than estimated",
      rs.receipt_date_of_row({"paid_on": None}, stamped, date_field="paid_on")["date"] is None
      and not rs.receipt_date_of_row({"paid_on": None}, stamped,
                                     date_field="paid_on")["measured"])

check("C12 a derived cadence still reads the row's EARNING date through the field it was given, so "
      "a feed that names its day differently needs no code change",
      rs.receipt_date_of_row({"tx_date": "2026-11-09"}, weekly_fri,
                             date_field="tx_date")["date"] == "2026-11-13")

check("C13 a config ROW handed in where a resolved schedule was expected still works — a config row "
      "is also a dict, which is the §COMMCALC-34 trap this module inherits",
      rs.receipt_date("2026-11-09", {"payout_cadence": rs.CADENCE_WEEKLY,
                                     "payout_weekday": 4})["date"] == "2026-11-13")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §D  THE ROLL-UP: NOTHING DATED THAT NO CADENCE COULD DATE
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§D  the roll-up reports what it could not place\n" + "-" * 94)

ROWS = [{"tx_date": "2026-11-09", "amount": 100.0},
        {"tx_date": "2026-11-10", "amount": 50.0},
        {"tx_date": "2026-11-16", "amount": 25.0},
        {"tx_date": None, "amount": 999.0}]

roll = rs.roll_up(ROWS, weekly_fri, date_field="tx_date", amount_of=lambda r: r.get("amount"))
check("D1 the two rows earned in one week land on ONE pay date and their money adds up",
      [(d["date"], d["amount"]) for d in roll["days"]]
      == [("2026-11-13", 150.0), ("2026-11-20", 25.0)])
check("D2 the undated row is NOT dated: its money is reported BESIDE the days, never folded into the "
      "nearest one",
      roll["unplaced_amount"] == 999.0 and roll["unplaced_rows"] == 1
      and all(d["amount"] != 999.0 for d in roll["days"]))
ctl("D2 folding it in would have made the first pay date 1,149.00",
    roll["days"][0]["amount"] != 1149.0)
check("D3 every basis that answered is named, so a screen can say whose rhythm this is",
      rs.BASIS_WEEKLY in roll["bases"] and rs.BASIS_NO_DATE in roll["bases"])

undec = rs.roll_up(ROWS, blank, date_field="tx_date", amount_of=lambda r: r.get("amount"))
check("D4 an UNDECLARED carrier's rows produce NO days and NO total — every dollar is unplaced and "
      "the roll-up is not measured. A $0.00 total would read as 'the carrier owes us nothing'",
      undec["days"] == [] and undec["total"] == 0.0
      and undec["unplaced_amount"] == 1174.0 and undec["measured"] is False)
check("D5 and the roll-up says WHY in words rather than leaving the reader to infer it",
      isinstance(undec.get("note"), str) and "declare" in undec["note"].lower())

# A COMPLETE DECLARATION THAT PLACED NOTHING IS A REAL ZERO, and that is a DIFFERENT answer from an
# undeclared carrier. Found by this feature's own integration tests: reading ROW COUNT as measurement
# printed "not measured" over an honest $0.00 the moment a store filter matched nothing, so a store
# with genuinely nothing coming in looked like a setup fault forever.
check("D6 `can_date` answers whether a DECLARATION could place a dollar at all — complete cadences "
      "can, an undeclared one cannot, and a cadence declared with its day left blank cannot either",
      rs.can_date(weekly_fri) and rs.can_date(monthly_15) and rs.can_date(semi)
      and rs.can_date(net21) and rs.can_date(stamped)
      and not rs.can_date(blank)
      and not rs.can_date(rs.resolve_schedule({"payout_cadence": rs.CADENCE_WEEKLY}))
      and not rs.can_date(rs.resolve_schedule({"payout_cadence": rs.CADENCE_MONTHLY}))
      and not rs.can_date(rs.resolve_schedule({"payout_cadence": rs.CADENCE_NET_DAYS})))
check("D7 so a roll-up over NO ROWS under a complete declaration is MEASURED at 0.00 \u2014 nothing "
      "was there, which is a measurement \u2014 while the same empty roll-up under no declaration is "
      "NOT measured",
      rs.roll_up([], weekly_fri)["measured"] is True
      and rs.roll_up([], weekly_fri)["total"] == 0.0
      and rs.roll_up([], blank)["measured"] is False)
ctl("D7 reading row count as measurement would have called both of them unmeasured",
    rs.roll_up([], weekly_fri)["measured"] != rs.roll_up([], blank)["measured"])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §E  THE DECLARED SOURCE READ — AND THE FAILURE MODES THAT LOOK LIKE ZERO
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§E  expected_receipts against a fake PostgREST\n" + "-" * 94)


class FakeQ:
    def __init__(self, store, schema, table):
        self.store, self.schema, self.table, self.filters = store, schema, table, []
        if table not in store.get(schema, {}):
            raise KeyError(f"relation {schema}.{table} does not exist")

    def select(self, cols):
        self.cols = [c.strip() for c in cols.split(",")]
        have = self.store[self.schema][self.table]
        missing = [c for c in self.cols if have and c not in have[0]]
        if missing:
            raise KeyError(f"column {missing[0]} does not exist")
        return self

    def eq(self, k, v):
        self.filters.append((k, v)); return self

    def gte(self, k, v):
        self.filters.append((">=" + k, v)); return self

    def lte(self, k, v):
        self.filters.append(("<=" + k, v)); return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def range(self, a, b):
        self._range = (a, b); return self

    def execute(self):
        rows = list(self.store[self.schema][self.table])
        for k, v in self.filters:
            if k.startswith(">="):
                rows = [r for r in rows if str(r.get(k[2:]) or "") >= str(v)]
            elif k.startswith("<="):
                rows = [r for r in rows if str(r.get(k[2:]) or "") <= str(v)]
            else:
                rows = [r for r in rows if r.get(k) == v]
        return type("R", (), {"data": rows})()


class FakeClient:
    def __init__(self, store):
        self.store, self._schema = store, "public"

    def schema(self, s):
        c = FakeClient(self.store); c._schema = s; return c

    def table(self, t):
        return FakeQ(self.store, self._schema, t)


HOUSE = "00000000-0000-0000-0000-000000000001"
LUXE = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
VZONE = "f4f1c16e-2acf-4221-854a-c29a605754a7"

STORE = {"commcalc": {
    # Two tenants declare a receipt source; one feed each, with the tenant's OWN table and column.
    "receipt_source_map": [
        {"org_id": HOUSE, "label": "carrier statement", "source_table": lineage.PAYMENT_DETAIL,
         "amount_field": "amount", "payout_cadence": rs.CADENCE_SEMI_MONTHLY,
         "payout_days": [6, 21], "payout_weekday": None, "payout_day_of_month": None,
         "payout_net_days": None, "is_active": True},
        {"org_id": LUXE, "label": "processor statement", "source_table": lineage.MA_COMMISSION,
         "amount_field": "total_commission", "payout_cadence": rs.CADENCE_NET_DAYS,
         "payout_days": None, "payout_weekday": None, "payout_day_of_month": None,
         "payout_net_days": 30, "is_active": True},
    ],
    lineage.PAYMENT_DETAIL: [
        {"org_id": HOUSE, "payment_date": "2026-11-02", "amount": 400.0, "business_address": "B-1"},
        {"org_id": HOUSE, "payment_date": "2026-11-03", "amount": 100.0, "business_address": "B-2"},
        {"org_id": LUXE, "payment_date": "2026-11-03", "amount": 7777.0, "business_address": "L-1"},
    ],
    lineage.MA_COMMISSION: [
        {"org_id": LUXE, "tx_date": "2026-11-01", "total_commission": 250.0, "business_address": "L-1"},
        {"org_id": HOUSE, "tx_date": "2026-11-01", "total_commission": 3333.0, "business_address": "B-1"},
    ],
}}

WIN = {"start": "2026-11-01", "end": "2026-11-30"}

got_house = er.dated_receipts(FakeClient(STORE), HOUSE, WIN)
check("E1 the HOUSE tenant's read answers off ITS OWN declared table and column, and names both",
      got_house["source"] == lineage.PAYMENT_DETAIL
      and got_house["declared"] is True and got_house["total"] == 500.0)
check("E2 and its two earning days land on the two declared semi-monthly pay days",
      [(d["date"], d["amount"]) for d in got_house["days"]] == [("2026-11-06", 500.0)])

got_luxe = er.dated_receipts(FakeClient(STORE), LUXE, WIN)
check("E3 the LUXELINK tenant's read answers off a DIFFERENT table, a different column and a "
      "different cadence — no code branches on either tenant",
      got_luxe["source"] == lineage.MA_COMMISSION and got_luxe["total"] == 250.0
      and [(d["date"], d["amount"]) for d in got_luxe["days"]] == [("2026-12-01", 250.0)])

check("E4 CROSS-TENANT NEGATIVE: the house read never sees Luxelink's 7,777.00 even though it sits "
      "in the very table the house declared, and the Luxelink read never sees the house's 3,333.00",
      got_house["total"] == 500.0 and got_luxe["total"] == 250.0)
ctl("E4 an unscoped read would have totalled 8,277.00 for the house",
    got_house["total"] != 8277.0)

got_vzone = er.dated_receipts(FakeClient(STORE), VZONE, WIN)
check("E5 a tenant that has DECLARED NOTHING gets source None and declared False — never a 0.00 "
      "total dressed as a measurement (§COMMCALC-29)",
      got_vzone["source"] is None and got_vzone["declared"] is False
      and got_vzone["measured"] is False and got_vzone["days"] == [])
check("E6 and it says in words what is missing and who fixes it, because the person reading an empty "
      "calendar must be able to tell setup from a quiet month",
      isinstance(got_vzone.get("note"), str) and "declare" in got_vzone["note"].lower())

nothing_there = er.dated_receipts(FakeClient(STORE), HOUSE, WIN, want_store=lambda _s: False)
check("E6b a DECLARED tenant whose rows a filter narrowed to none reads a MEASURED 0.00, not 'not "
      "measured' \u2014 the declaration answered and nothing was there, which is a different fact "
      "from nobody having declared anything",
      nothing_there["declared"] is True and nothing_there["measured"] is True
      and nothing_there["total"] == 0.0 and nothing_there["days"] == [])
ctl("E6b and it is a DIFFERENT answer from the undeclared tenant's",
    nothing_there["measured"] != got_vzone["measured"]
    and nothing_there["total"] != got_vzone["total"])

BAD = {"commcalc": dict(STORE["commcalc"])}
BAD["commcalc"] = dict(STORE["commcalc"])
BAD["commcalc"]["receipt_source_map"] = [
    {"org_id": HOUSE, "label": "typo", "source_table": lineage.PAYMENT_DETAIL,
     "amount_field": "column_that_does_not_exist", "payout_cadence": rs.CADENCE_MONTHLY,
     "payout_day_of_month": 15, "payout_days": None, "payout_weekday": None,
     "payout_net_days": None, "is_active": True}]
bad = er.dated_receipts(FakeClient(BAD), HOUSE, WIN)
check("E7 a DECLARED source that cannot be READ is REPORTED, never read as 'nothing was declared' — "
      "the §COMMCALC-29 class, and the two outcomes must not look identical",
      bad["declared"] is True and bad["measured"] is False and bad["error"]
      and "column_that_does_not_exist" in str(bad["error"]))
ctl("E7 swallowing the read failure would have produced the same empty result as an undeclared org",
    bad["declared"] != got_vzone["declared"])

check("E8 an INACTIVE declaration is not a declaration — a tenant can turn one off without deleting "
      "the row that records what it was",
      er.dated_receipts(FakeClient({"commcalc": dict(
          STORE["commcalc"],
          receipt_source_map=[dict(STORE["commcalc"]["receipt_source_map"][0], is_active=False)])}),
          HOUSE, WIN)["declared"] is False)

gone = er.dated_receipts(FakeClient({"commcalc": {lineage.PAYMENT_DETAIL: []}}), HOUSE, WIN)
check("E9 the DECLARATION TABLE itself being absent is 'nobody has declared anything', not a bad\n      declaration — the migration is simply not applied, and nothing is a 500",
      gone["declared"] is False and gone["measured"] is False
      and gone["table_missing"] is True and isinstance(gone.get("note"), str))
ctl("E9 and that is a DIFFERENT answer from a declaration that names a bad column",
    gone["declared"] != bad["declared"])

check("E10 the DATE COLUMN is dereferenced from the lineage registry, so 'which column dates this "
      "feed' keeps the one home it already has (§D)",
      er.earning_date_field(lineage.PAYMENT_DETAIL)
      == lineage.DATA_DATE_COLUMN_BY_TABLE[lineage.PAYMENT_DETAIL]
      and er.earning_date_field(lineage.MA_COMMISSION)
      == lineage.DATA_DATE_COLUMN_BY_TABLE[lineage.MA_COMMISSION])
check("E11 and a feed the registry declares has NO single dating column cannot be a receipt source — "
      "a None there is a declaration, so it is honoured rather than guessed around",
      er.earning_date_field(lineage.MI_RESIDUAL) is None
      and lineage.DATA_DATE_COLUMN_BY_TABLE[lineage.MI_RESIDUAL] is None)
check("E12 a source table the registry has never heard of is refused too, for the same reason",
      er.earning_date_field("some_table_nobody_registered") is None)
ctl("E12 a reader that fell back to a literal date column would have accepted it",
    er.earning_date_field("some_table_nobody_registered") != "payment_date")

src = read("backend/app/modules/payables/expected_receipts.py")
check("E13 and the registry is dereferenced rather than copied: no second map of feed -> date column "
      "exists in this module",
      "DATA_DATE_COLUMN_BY_TABLE" in src
      and not re.search(r'"payment_date"\s*:', code_only(src)))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §F  THE CALENDAR: ONE IN, ONE OUT, AND A NET THAT CANNOT DISAGREE
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§F  cash_calendar\n" + "-" * 94)

IN = [{"date": "2026-11-06", "amount": 500.0, "count": 2},
      {"date": "2026-11-21", "amount": 300.0, "count": 1}]
OUT = [{"due_date": "2026-11-06", "owed": 120.0, "count": 3},
       {"due_date": "2026-11-10", "owed": 80.0, "count": 1}]

cal = cc.build(IN, OUT, start="2026-11-05", end="2026-11-22", opening_balance=1000.0)
days = {d["date"]: d for d in cal["days"]}

check("F1 every day in the span is present, so a calendar can be drawn without the page inventing "
      "the gaps",
      len(cal["days"]) == 18 and cal["days"][0]["date"] == "2026-11-05"
      and cal["days"][-1]["date"] == "2026-11-22")
check("F2 a day's NET is its own in minus its own out, folded per day and then added up — not a "
      "difference of two grand totals, which no set of records adds up to",
      days["2026-11-06"]["expected_in"] == 500.0 and days["2026-11-06"]["owed_out"] == 120.0
      and days["2026-11-06"]["net"] == 380.0
      and days["2026-11-10"]["net"] == -80.0)
check("F3 the RUNNING position carries the opening balance forward day by day, which is the thing "
      "the owner asked for: what is left after offsetting",
      days["2026-11-05"]["running"] == 1000.0
      and days["2026-11-06"]["running"] == 1380.0
      and days["2026-11-10"]["running"] == 1300.0
      and days["2026-11-21"]["running"] == 1600.0)
check("F4 the totals are the sum of the day rows, so the header cannot disagree with the grid",
      cal["total_in"] == 800.0 and cal["total_out"] == 200.0 and cal["total_net"] == 600.0
      and cal["total_net"] == round(cal["total_in"] - cal["total_out"], 2)
      and cal["closing_balance"] == 1600.0)
check("F5 a day with neither in nor out is a REAL zero — nothing happens that day, and that is a "
      "measurement rather than an absence",
      days["2026-11-07"]["expected_in"] == 0.0 and days["2026-11-07"]["owed_out"] == 0.0
      and days["2026-11-07"]["quiet"] is True)

check("F6 money dated OUTSIDE the span is reported as out-of-span, never dropped and never dragged "
      "onto the edge day",
      cc.build(IN, OUT, start="2026-11-07", end="2026-11-09")["out_of_span_in"] == 800.0
      and cc.build(IN, OUT, start="2026-11-07", end="2026-11-09")["out_of_span_out"] == 200.0)
ctl("F6 clamping to the edge would have put 500.00 on the 7th",
    cc.build(IN, OUT, start="2026-11-07", end="2026-11-09")["days"][0]["expected_in"] == 0.0)

unmeasured = cc.build(None, OUT, start="2026-11-05", end="2026-11-07",
                      opening_balance=0.0, in_measured=False)
check("G1 when the IN side was never measured, the calendar says so and REFUSES to net: a net "
      "computed against an unmeasured in is a wrong answer printed confidently",
      unmeasured["in_measured"] is False
      and all(d["expected_in"] is None for d in unmeasured["days"])
      and all(d["net"] is None for d in unmeasured["days"])
      and unmeasured["total_in"] is None and unmeasured["total_net"] is None)
ctl("G1 treating the unmeasured in as 0.00 would have reported a net of -200.00",
    unmeasured["total_net"] != -200.0)
check("G2 the OUT side it DID measure is still shown in full — one side being unknown must not hide "
      "the side that is known",
      unmeasured["total_out"] == 120.0 and unmeasured["out_measured"] is True
      and unmeasured["out_of_span_out"] == 80.0)
check("G3 and the same holds the other way round",
      cc.build(IN, None, start="2026-11-05", end="2026-11-07", out_measured=False)["total_in"]
      == 500.0
      and cc.build(IN, None, start="2026-11-05", end="2026-11-07",
                   out_measured=False)["total_out"] is None)
check("G4 the running position is withheld whenever either side is unmeasured, because a balance is "
      "a net and a net nobody could compute is not a balance",
      all(d["running"] is None for d in unmeasured["days"])
      and unmeasured["closing_balance"] is None)

check("G5 a span given backwards, or no span at all, yields no days rather than an exception",
      cc.build(IN, OUT, start="2026-11-22", end="2026-11-05")["days"] == []
      and cc.build(IN, OUT, start="", end="")["days"] == [])
check("G6 the span is capped so one request cannot be asked for ten years of day rows",
      len(cc.build([], [], start="2020-01-01", end="2030-01-01")["days"]) == cc.MAX_DAYS
      and cc.build([], [], start="2020-01-01", end="2030-01-01")["span_truncated"] is True)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §H  ONE OUT READ, RULE TWO, AND NO SECOND DERIVATION
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§H  one read, and no carrier names\n" + "-" * 94)

ROUTER = read("backend/app/modules/payables/router.py")
tree = ast.parse(ROUTER)
cal_fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "cash_calendar"), None)
check("H1 the endpoint exists",
      cal_fn is not None and "/cash-calendar" in ROUTER)
cal_src = ast.get_source_segment(ROUTER, cal_fn) if cal_fn else ""
check("H2 ONE OUT READ: the calendar endpoint calls the SHARED owed home and holds no ledger or "
      "vendor-feed query of its own — two paths answering 'what do I owe' is the duplicate defect",
      "dated_owed" in cal_src
      and "device_payable_ledger" not in cal_src
      and ".table(" not in cal_src)
ctl("H2 a query of its own would have shown up as a .table( call inside the endpoint",
    ".table(" in ROUTER)
check("H3 ONE IN READ: and it reaches the inbound money through the declared-source home rather "
      "than reading a feed directly",
      "dated_receipts" in cal_src)
check("H4 the netting itself is done by the pure home, so the arithmetic the harness proves is the "
      "arithmetic the endpoint runs",
      "cash_calendar" in cal_src or "_cal.build" in cal_src or "cc.build" in cal_src)

for rel in ("backend/app/modules/payables/receipt_schedule.py",
            "backend/app/modules/payables/expected_receipts.py",
            "backend/app/modules/payables/cash_calendar.py"):
    body = code_only(read(rel))
    check(f"H5 RULE TWO in {os.path.basename(rel)}: no carrier, tenant or product name in code",
          not re.search(r"(?i)\b(boost|luxelink|vzone|cellfonz|vidapay|t-?mobile|verizon|"
                        r"metro|at&t|total\s*wireless)\b", body))
    check(f"H6 and no day-of-month or weekday LITERAL stands in for a declaration in "
          f"{os.path.basename(rel)}",
          not re.search(r"day_of_month\s*(=|==)\s*\d|weekday\s*(=|==)\s*[0-6]\b", body))

sched = read("backend/app/modules/payables/receipt_schedule.py")
check("H7 the cadence home is PURE: no client, no framework, no clock of its own — `today` is never "
      "read, because a receipt date derived from when the report happened to run is not a fact",
      "import supabase" not in sched and "fastapi" not in sched
      and not re.search(r"date\.today\(\)|datetime\.now\(", sched))
ctl("H7 a module that called today() would have matched that pattern",
    bool(re.search(r"date\.today\(\)|datetime\.now\(", "x = datetime.date.today()")))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §I  REGISTRATION — THE MIGRATION, ITS GRANT, THE INDEX, AND THE PAGE
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§I  registration\n" + "-" * 94)

MIG = read("database/migrations/1115_receipt_source_map.sql")
check("I1 the migration exists, is idempotent and carries its REVERT note",
      "create table if not exists" in MIG.lower() and "-- REVERT:" in MIG)
check("I2 A MIGRATION GRANTS WHAT IT CREATES — service_role only, because the browser never reads a "
      "table here",
      re.search(r"(?i)grant\s+select,\s*insert,\s*update,\s*delete\s+on\s+"
                r"commcalc\.receipt_source_map\s+to\s+service_role", MIG)
      and not re.search(r"(?i)to\s+anon", MIG))
check("I3 the declared cadence is CONSTRAINED to the vocabulary the home rules on, so a typo cannot "
      "be stored and then read as a rhythm",
      all(c in MIG for c in rs.CADENCES) and "check" in MIG.lower())
check("I4 and NOTHING is seeded: a house default on the inbound side would be one tenant's "
      "arrangement standing in for every tenant's, which is exactly what this is built against",
      "insert into commcalc.receipt_source_map" not in MIG.lower())

IDX = read("docs/SYSTEM_DATA_FLOW_INDEX.md")
check("I5 registered in the index, in its own section",
      "COMMCALC-56" in IDX)
check("I6 and the three new homes, the new table and the new endpoint are all named there",
      all(s in IDX for s in ("receipt_schedule", "expected_receipts", "cash_calendar",
                             "receipt_source_map", "/payables/cash-calendar")))

PAGE = read("frontend/src/app/(platform)/commcalc/cash-calendar/page.tsx")
check("I7 the page exists and is a CALENDAR — day cells across a month grid, which is what was asked "
      "for rather than another table of dates",
      bool(PAGE) and "grid" in PAGE and "weekday" in PAGE.lower())
check("I8 THE REPORT RULE: the standard filters and the send bar, both by default (§CORE-27) — and "
      "neither is re-implemented, both are the shared components",
      "ReportShell" in code_only(PAGE) or
      ("StandardFilterBar" in code_only(PAGE) and "SendReportButton" in code_only(PAGE)))
check("I9 a day whose in side was never measured prints the words, never 0.00 — a spreadsheet and a "
      "screen must agree that nothing was measured",
      "not measured" in PAGE.lower() or "not_measured" in PAGE)
check("I10 and the page states WHOSE rhythm dated the money, by dereferencing the basis rather than "
      "spelling a sentence per cadence",
      "basis" in PAGE)
check("I11 the tenant's own declaration is reachable from the report, so an empty calendar has a "
      "next step on it instead of a support ticket",
      "receipt-sources" in PAGE or "receipt_source" in PAGE)


print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
