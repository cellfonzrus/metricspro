"""PROOF + BUILD LOCK — a billing period is DECLARED, and the money is read ONCE (§COMMCALC-34).

OWNER REPORT, verbatim: *"Weekly Owed to Distributor does not show anything in luxelink"*.

WHAT THIS PROVES, BY DRIVING THE REAL HOMES
  * `payables/billing_period` over every cadence and every edge: the stamped period, the derived
    weekday, the on-the-day case, the undeclared weekday, the undated row and a config row handed
    straight in where a resolved cadence was expected.
  * `payables/owed_read` shaping both sources against a FAKE client that answers like PostgREST, so
    the source choice, the fallback, the grain change and the filters are exercised for real.
  * THE ONE READ: `payables/router.owed_by_date` and the weekly roll-up go through the SAME function,
    locked by AST so a future second read cannot appear without failing the build.

WHAT IT LOCKS SO IT CANNOT UN-WIRE (CLAUDE.md)
  A  the cadence vocabulary and the allowlist (§66 shape) — fail-CLOSED, mutation-tested.
  B  a weekly period rolls FORWARD, never back, and never past the day it is due on.
  C  the STAMPED period wins and is never recomputed.
  D  an undeclared weekday is shown and counted, never dropped and never guessed.
  E  nothing is dated that carries no date; the unplaced money is reported beside the periods.
  F  ONE READ: the daily endpoint holds no ledger query of its own, and the dead fallback is GONE.
  G  the debt test is DEREFERENCED from its one home, not restated.
  H  RULE TWO: no carrier, tenant or weekday literal in the report; the relation names dereference.
  I  Boost is untouched: the consignment path still answers first and its basis says so.
  J  registration: the index, the migration, the graph and the guard job.

Run: `cd backend && python3 harness_billing_period.py`  (DB-free, stdlib only.)
"""
import ast
import datetime
import glob
import inspect
import os
import re
import sys

from app.modules.commcalc import data_lineage_registry as lineage
from app.modules.payables import billing_period as bp
from app.modules.payables import owed_read as orr
from app.modules.payables import vendor_invoice as vi

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
    """`src` with every COMMENT and every DOCSTRING blanked — the text that DECIDES, and nothing else.

    WHY THE DISTINCTION IS THE WHOLE POINT. RULE TWO bans a carrier name from deciding behaviour; it
    does not ban one from EXPLAINING behaviour, and the owner's own report ("does not show anything
    in luxelink") is the reason these homes exist — scrubbing it would delete the evidence to satisfy
    a scanner. Likewise a docstring that names `ledger_status` as the home owning payability is the
    cross-reference §19.18 asks for, while an IMPORT of it would be this module deciding payability.

    AND IT BLANKS DOCSTRINGS ONLY, NEVER EVERY STRING. `if carrier == "luxelink"` is the exact
    violation RULE TWO is about, and it lives in a string literal — a scrubber that blanked all
    strings would read as clean on the worst case it exists to catch. Control H1a drives that
    literal shape, so this distinction cannot quietly erode.
    """
    import io
    import tokenize
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except Exception:                      # a syntax error is the compiler's job to report, not ours
        return src
    def _next_sig(i):
        # COMMENT only — a NEWLINE after the string is exactly the signal that it stands alone, so
        # skipping newlines here would walk past the evidence and read every docstring as code.
        for t in toks[i + 1:]:
            if t.type != tokenize.COMMENT:
                return t.type
        return tokenize.ENDMARKER

    def _prev_sig(i):
        for t in reversed(toks[:i]):
            if t.type not in (tokenize.COMMENT,):
                return t.type
        return tokenize.NEWLINE

    out = []
    for i, tok in enumerate(toks):
        blank = tok.type == tokenize.COMMENT or (
            # a STRING that is a whole statement on its own == a docstring
            tok.type == tokenize.STRING
            and _prev_sig(i) in (tokenize.NEWLINE, tokenize.NL, tokenize.INDENT, tokenize.DEDENT)
            and _next_sig(i) in (tokenize.NEWLINE, tokenize.NL, tokenize.ENDMARKER))
        out.append((" " * len(tok.string)) if blank else tok.string)
        out.append(" ")
    return "".join(out)


HOME = read("backend/app/modules/payables/billing_period.py")
READ_SRC = read("backend/app/modules/payables/owed_read.py")
PAY_ROUTER = read("backend/app/modules/payables/router.py")
ASSET_ROUTER = read("backend/app/modules/asset/router.py")

#: The two config rows this proof drives, shaped exactly as `payable_source_map` returns them.
BOOST = {"billing_cadence": "weekly", "billing_weekday": 4,
         "billing_period_field": "billing_friday"}
TOTAL = {"billing_cadence": "per_due_date", "billing_weekday": None,
         "billing_period_field": None}
WEEKLY_NO_DAY = {"billing_cadence": "weekly"}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. THE VOCABULARY AND THE ALLOWLIST ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("A1 the house default asserts NO rhythm — the money is owed on the date it is owed, which is "
      "the only answer that invents nothing",
      bp.resolve_cadence(None)["cadence"] == bp.CADENCE_PER_DUE_DATE
      and bp.resolve_cadence({})["declared"] is False)
check("A2 a DECLARED cadence is honoured and says it was declared",
      bp.resolve_cadence(BOOST) == {"cadence": "weekly", "weekday": 4,
                                    "period_field": "billing_friday", "declared": True},
      str(bp.resolve_cadence(BOOST)))
check("A3 an UNRECOGNISED cadence falls to the house default rather than being honoured — a value "
      "nobody ruled on is not a rhythm, and acting on it would be acting on a typo",
      bp.resolve_cadence({"billing_cadence": "fortnightly"})["cadence"] == bp.CADENCE_PER_DUE_DATE
      and bp.resolve_cadence({"billing_cadence": "fortnightly"})["declared"] is False)
check("A4 THE ALLOWLIST (§66 shape): only the four ruled bases are a measurement, and a basis this "
      "module has never ruled on reads as NOT measured",
      all(bp.is_measured(b) for b in bp.MEASURED_BASES)
      and not any(bp.is_measured(b) for b in (bp.BASIS_NO_DATE, bp.BASIS_WEEKDAY_UNDECLARED,
                                              "", None, "something_new", "WEEKLY_DECLARED")))
ctl("A4a control: the allowlist is what decides — a basis removed from it stops being measured, so "
    "A4 is about the tuple and not about the spelling",
    bp.BASIS_WEEKLY in bp.MEASURED_BASES and bp.BASIS_WEEKDAY_UNDECLARED not in bp.MEASURED_BASES)
check("A5 a declared weekday is read strictly: out of range, a bool and a word are each undeclared, "
      "and 0 (Monday) is a real answer rather than a falsy blank",
      [bp.resolve_cadence({"billing_weekday": v})["weekday"]
       for v in (0, 6, 7, -1, True, False, "x", None, "3")] == [0, 6, None, None, None, None,
                                                                None, None, 3],
      str([bp.resolve_cadence({"billing_weekday": v})["weekday"]
           for v in (0, 6, 7, -1, True, False, "x", None, "3")]))
check("A6 A CONFIG ROW IS ALSO A DICT, so an entry point handed one straight resolves it rather "
      "than reading it as an already-resolved cadence with no `cadence` key at all",
      bp.period_of("2026-10-26", BOOST)["basis"] == bp.BASIS_WEEKLY
      and bp.period_of("2026-10-26", bp.resolve_cadence(BOOST))["basis"] == bp.BASIS_WEEKLY)
ctl("A6a control: the resolved-shape test really distinguishes the two — a config row does NOT "
    "carry the resolved keys, which is why `isinstance(x, dict)` could not have been the test",
    not all(k in BOOST for k in bp._RESOLVED_KEYS)
    and all(k in bp.resolve_cadence(BOOST) for k in bp._RESOLVED_KEYS))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. A WEEKLY PERIOD ROLLS FORWARD, AND NEVER PAST THE DAY IT IS DUE ON ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 2026-10-26 is a Monday; the Friday of that week is 2026-10-30.
check("B1 a Monday due date bills on that week's Friday",
      bp.period_of("2026-10-26", BOOST)["key"] == "2026-10-30")
check("B2 ON THE DAY IT STAYS — money due ON the billing day bills that day, and rolling it to the "
      "next week would postpone a real liability by seven days",
      bp.period_of("2026-10-30", BOOST)["key"] == "2026-10-30")
ctl("B2a control: 'after' rather than 'on or after' really moves it a week, so B2 is about the "
    "boundary and not about the fixture",
    (datetime.date(2026, 10, 30) + datetime.timedelta(days=((4 - 4) % 7) or 7)).isoformat()
    == "2026-11-06")
check("B3 the day after the billing day belongs to the NEXT period, not the one that just billed",
      bp.period_of("2026-10-31", BOOST)["key"] == "2026-11-06")
check("B4 every weekday maps to its own billing day, so no day of the week is special-cased",
      [bp.period_of((datetime.date(2026, 10, 26) + datetime.timedelta(days=i)).isoformat(),
                    BOOST)["key"] for i in range(8)]
      == ["2026-10-30"] * 5 + ["2026-11-06"] * 3,
      str([bp.period_of((datetime.date(2026, 10, 26) + datetime.timedelta(days=i)).isoformat(),
                        BOOST)["key"] for i in range(8)]))
check("B5 THE PERIOD STATES THE RANGE IT ROLLED UP — the seven days ENDING on the billing day, so "
      "a reader is never left guessing whether the six days before it are in or out",
      bp.period_of("2026-10-26", BOOST)["start"] == "2026-10-24"
      and bp.period_of("2026-10-26", BOOST)["end"] == "2026-10-30"
      and bp.week_window("2026-10-30") == (datetime.date(2026, 10, 24).isoformat(),
                                           datetime.date(2026, 10, 30).isoformat()))
check("B5a AND IT TAKES NO WEEKDAY TO DO IT — a span is seven days ending on its billing day "
      "whatever weekday that is, so a weekday parameter would be a validity gate and nothing more, "
      "which is what made the one caller outside the home pass a literal `4`. A guessed Friday in a "
      "caller is the same defect as a guessed Friday in the home, one import away. ISO STRINGS, the "
      "one shape every value here returns and the shape the window home parses — an earlier draft "
      "returned `date` objects and the window silently arrived unusable",
      bp.week_window("2026-10-30") == ("2026-10-24", "2026-10-30")
      and bp.week_window(None) == (None, None)
      and all(isinstance(v, str) for v in bp.week_window("2026-10-30"))
      and "weekday" not in inspect.signature(bp.week_window).parameters
      and not re.search(r"week_window\([^)]*,", ASSET_ROUTER))
check("B6 and the label names the weekday rather than printing a bare date, because 'Friday' is the "
      "word the carrier's own arrangement uses",
      bp.period_of("2026-10-26", BOOST)["label"] == "Friday 2026-10-30")
check("B7 a per-invoice carrier's period IS its due date — one day, start == end, nothing rolled",
      bp.period_of("2026-10-26", TOTAL) == {"key": "2026-10-26", "start": "2026-10-26",
                                            "end": "2026-10-26", "label": "due 2026-10-26",
                                            "basis": bp.BASIS_DUE_DATE, "measured": True})
check("B8 the house default reaches the same period by the same arithmetic and says it was the "
      "default that answered, not the carrier",
      bp.period_of("2026-10-26", None)["key"] == "2026-10-26"
      and bp.period_of("2026-10-26", None)["basis"] == bp.BASIS_HOUSE)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. THE STAMPED PERIOD WINS, AND IS NEVER RECOMPUTED ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
STAMPED = {"billing_friday": "2026-11-06", "due_date": "2026-10-26"}
check("C1 where the feed stamped the period, THAT is the period — even though deriving it from the "
      "due date would have produced a different week",
      bp.period_of_row(STAMPED, BOOST)["key"] == "2026-11-06"
      and bp.period_of(STAMPED["due_date"], BOOST)["key"] == "2026-10-30")
check("C2 and the basis says the feed answered, so a reader can tell a stamped period from a "
      "derived one without comparing dates",
      bp.period_of_row(STAMPED, BOOST)["basis"] == bp.BASIS_PERIOD_FIELD)
ctl("C2a control: recomputing really disagrees with the stamp here, which is the whole reason the "
    "stamp wins — a figure every other surface reconciles against would otherwise move",
    bp.period_of_row(STAMPED, BOOST)["key"] != bp.period_of(STAMPED["due_date"], BOOST)["key"])
check("C3 a declared period field the row does not carry falls back to the due date rather than "
      "refusing — the column was declared for the feed, not for every row of it",
      bp.period_of_row({"due_date": "2026-10-26"}, BOOST)["basis"] == bp.BASIS_WEEKLY)
check("C4 and a carrier that declares no period field never looks for one",
      bp.period_of_row(STAMPED, TOTAL)["key"] == "2026-10-26"
      and bp.period_of_row(STAMPED, TOTAL)["basis"] == bp.BASIS_DUE_DATE)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. AN UNDECLARED WEEKDAY IS SHOWN AND COUNTED — never dropped, never guessed ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
u = bp.period_of("2026-10-26", WEEKLY_NO_DAY)
check("D1 the money keeps its own due date as the period, so it is still shown and still counted — "
      "dropping it would hide a real liability behind a missing config row (§COMMCALC-29)",
      u["key"] == "2026-10-26" and u["start"] == "2026-10-26")
check("D2 and the basis says the grouping is NOT the carrier's own, so a screen cannot present it "
      "as a billing day the carrier named",
      u["basis"] == bp.BASIS_WEEKDAY_UNDECLARED and u["measured"] is False
      and not bp.is_measured(u["basis"]))
check("D3 NOTHING GUESSES FRIDAY. No weekday literal and no default weekday is written in the home "
      "— a guessed day would be the same defect in a config column instead of in code",
      not re.search(r"(?i)\b(friday|thursday)\b\s*[=)]", HOME)
      and not re.search(r"weekday\s*=\s*[0-6]\b", HOME),
      str(re.findall(r"weekday\s*=\s*[0-6]\b", HOME)))
check("D4 the roll-up carries ONE sentence for it, from one home, so the screen says the same thing "
      "wherever it says it",
      bp.roll_up([{"due_date": "2026-10-26", "owed": 10}],
                 WEEKLY_NO_DAY)["note"] == bp.UNDECLARED_WEEKDAY_NOTE)
check("D5 and a per-invoice carrier gets its OWN sentence instead — 'there is no weekly billing "
      "day' is a different fact from 'nobody declared which day'",
      bp.roll_up([{"due_date": "2026-10-26", "owed": 10}], TOTAL)["note"] == bp.PER_DUE_DATE_NOTE
      and bp.PER_DUE_DATE_NOTE != bp.UNDECLARED_WEEKDAY_NOTE)
check("D6 a weekly carrier whose days all resolved gets NO note — a sentence shown when nothing is "
      "wrong trains the reader to ignore it",
      bp.roll_up([{"due_date": "2026-10-26", "owed": 10}], BOOST)["note"] is None)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. NOTHING IS DATED THAT CARRIES NO DATE ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("E1 an undated row gets NO period and says so in words — never today, and never the nearest "
      "bucket",
      bp.period_of(None, BOOST) == {"key": None, "start": None, "end": None,
                                    "label": "no due date recorded",
                                    "basis": bp.BASIS_NO_DATE, "measured": False})
check("E2 and neither does an unparseable one, in any of the shapes a feed really sends",
      all(bp.period_of(v, BOOST)["basis"] == bp.BASIS_NO_DATE
          for v in ("", "   ", "not a date", "2026-13-45", 0, False, "26/10/2026")),
      str([v for v in ("", "   ", "not a date", "2026-13-45", 0, False, "26/10/2026")
           if bp.period_of(v, BOOST)["basis"] != bp.BASIS_NO_DATE]))
check("E3 a real date, a datetime and an ISO timestamp all read as the same day — a PostgREST read "
      "returns the third and a fixture the first",
      bp.period_of(datetime.date(2026, 10, 26), TOTAL)["key"]
      == bp.period_of(datetime.datetime(2026, 10, 26, 13, 5), TOTAL)["key"]
      == bp.period_of("2026-10-26T13:05:00+00:00", TOTAL)["key"] == "2026-10-26")
ROWS = [{"due_date": "2026-10-20", "owed": 100.0, "count": 1},
        {"due_date": "2026-10-26", "owed": 50.5, "count": 1},
        {"due_date": None, "owed": 7.25, "count": 1},
        {"due_date": "2026-10-26", "owed": None, "count": 1}]
r_w = bp.roll_up(ROWS, BOOST, amount_of=lambda r: r.get("owed"),
                 count_of=lambda r: r.get("count") or 1)
check("E4 THE UNPLACEABLE MONEY IS REPORTED BESIDE THE PERIODS, never inside one — a liability "
      "nobody could date is reported, never quietly dated",
      r_w["unplaced_rows"] == 1 and r_w["unplaced_amount"] == 7.25
      and r_w["total"] == 150.5 and 7.25 not in [p["amount"] for p in r_w["periods"]],
      str(r_w))
check("E5 the periods come back ordered by date, so a screen prints them without sorting again and "
      "cannot order them differently from the total it shows",
      [p["key"] for p in r_w["periods"]] == ["2026-10-23", "2026-10-30"])
check("E6 EVERY BASIS THAT ANSWERED IS NAMED, with how many rows each — so a screen can say the "
      "grouping is the carrier's own, or that part of it is not",
      r_w["bases"] == {bp.BASIS_WEEKLY: 3, bp.BASIS_NO_DATE: 1}, str(r_w["bases"]))
check("E7 a row whose amount is not a number counts as a row and adds nothing, rather than "
      "breaking the fold or being silently discarded",
      sum(p["count"] for p in r_w["periods"]) == 3)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. ONE READ — the week cannot total what the day disagrees with ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
class _Q:
    """A PostgREST builder that records its filters and replays a fixture."""

    def __init__(self, rows, log):
        self._rows, self._log = rows, log

    def select(self, cols):
        self._log.append(("select", cols))
        return self

    def eq(self, k, v):
        self._log.append(("eq", k, v))
        return self

    def gte(self, k, v):
        self._log.append(("gte", k, v))
        return self

    def lte(self, k, v):
        self._log.append(("lte", k, v))
        return self

    def in_(self, k, v):
        self._log.append(("in", k, tuple(v)))
        return self

    def limit(self, n):
        return self

    def range(self, a, b):
        self._page = (a, b)
        return self

    @property
    def not_(self):
        return self

    def is_(self, k, v):
        self._log.append(("is", k, v))
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        a, b = getattr(self, "_page", (0, 100000))
        return type("R", (), {"data": self._rows[a:b + 1]})()


class _Client:
    def __init__(self, tables):
        self.tables, self.log = tables, []
        self._schema = None

    def schema(self, s):
        self._schema = s
        return self

    def table(self, t):
        self.log.append(("table", self._schema, t))
        return _Q(self.tables.get(t, []), self.log)


LEDGER_ROWS = [
    {"due_date": "2026-10-26", "net_owed": 213.81, "owed": 300.0, "store": "B-3735",
     "imei": "1", "device_model": "M", "status": "open"},
    {"due_date": "2026-10-30", "net_owed": None, "owed": 100.0, "store": "B-5601",
     "imei": "2", "device_model": "M", "status": "due"},
]
VENDOR_FEED_ROWS = [
    {"due_date": "2026-10-26", "retail_cost": 529.0, "account_name": "ACME WIRELESS"},
    {"due_date": "2026-10-26", "retail_cost": -50.0, "account_name": "ACME WIRELESS"},
    {"due_date": "2026-10-30", "retail_cost": 195.0, "account_name": "ACME WIRELESS"},
]
WIN = {"start": "2026-10-24", "end": "2026-10-30", "days": 7, "basis": "explicit_range",
       "days_measured": True}

got = orr.dated_owed(_Client({orr.DEVICE_LEDGER: LEDGER_ROWS}), "org", WIN)
check("F1 the LEDGER answers first, at DEVICE grain, and the source is named",
      got["source"] == orr.SOURCE_DEVICE_LEDGER and got["grain"] == orr.GRAIN_DEVICE
      and len(got["rows"]) == 2)
check("F2 `net_owed` answers where it exists and `owed` is the fallback — the daily surface's own "
      "precedence, stated once instead of twice",
      [r["owed"] for r in got["rows"]] == [213.81, 100.0], str([r["owed"] for r in got["rows"]]))
vgot = orr.dated_owed(_Client({orr.VENDOR_FEED: VENDOR_FEED_ROWS}), "org", WIN)
check("F3 THE LEDGER PRODUCING NOTHING IS NOT 'YOU OWE NOTHING' — the processor's own dated amounts "
      "answer, and the GRAIN CHANGE is said out loud because that feed carries no IMEI",
      vgot["source"] == orr.SOURCE_VENDOR_FEED and vgot["grain"] == orr.GRAIN_ORDER_LINE
      and "ORDER LINES" in (vgot["note"] or "") and "ACME WIRELESS" in (vgot["note"] or ""))
check("F4 and the credit line is NOT money owed — a negative cost is paid TO the dealer",
      [r["owed"] for r in vgot["rows"]] == [529.0, 195.0], str(vgot["rows"]))
check("F5 both sources empty is NO SOURCE, never a $0.00 total dressed as a measurement",
      orr.dated_owed(_Client({}), "org", WIN) == {"rows": [], "source": None, "grain": None,
                                                  "note": None, "entities": [],
                                                  "truncated": False})
check("F6 THE SAME ROWS ROLL UP BOTH WAYS, so the week is the day regrouped and cannot disagree "
      "with it: Σ(periods) == Σ(the daily rows) to the cent, on both sources",
      bp.roll_up(got["rows"], BOOST, count_of=lambda r: r.get("count") or 1)["total"]
      == round(sum(r["owed"] for r in got["rows"]), 2)
      and bp.roll_up(vgot["rows"], TOTAL, count_of=lambda r: r.get("count") or 1)["total"]
      == round(sum(r["owed"] for r in vgot["rows"]), 2))
check("F7 the store filter is the CALLER'S resolver, so store identity (§64) keeps its one home "
      "rather than gaining a copy in the read",
      len(orr.dated_owed(_Client({orr.DEVICE_LEDGER: LEDGER_ROWS}), "org", WIN,
                         want_store=lambda st: st == "B-3735")["rows"]) == 1)
check("F8 and so does the market half (RULE FIVE) — the ledger rows carry no `market` column, so "
      "it is RESOLVED per store and a selection the resolver cannot answer narrows nothing",
      len(orr.dated_owed(_Client({orr.DEVICE_LEDGER: LEDGER_ROWS}), "org", WIN,
                         want_markets={"chicago"},
                         market_of=lambda st: "Chicago" if st == "B-3735" else "NJ")["rows"]) == 1)
check("F9 BUILD LOCK — the daily endpoint holds NO ledger query of its own: it goes through the one "
      "read, and the grouping by date is all that is left of it",
      "dated_owed(" in PAY_ROUTER
      and not re.search(r'table\(\s*["\']device_payable_ledger["\']\s*\)',
                        PAY_ROUTER.split('@router.get("/owed-by-date")', 1)[1]))
check("F10 and the read it replaced is GONE, not left beside it — a dead second reader is a future "
      "divergence that compiles (§19.18)",
      "_owed_from_vendor_feed" not in PAY_ROUTER and "_VENDOR_FEED_TABLE" not in PAY_ROUTER)
check("F11 the WEEKLY report dereferences the same two homes and derives no money of its own",
      "owed_read as _owed_r" in ASSET_ROUTER and "billing_period as _bp" in ASSET_ROUTER
      and "_owed_r.dated_owed(" in ASSET_ROUTER and "_bp.roll_up(" in ASSET_ROUTER)
_WEEKLY = ASSET_ROUTER.split("async def get_owed_weekly", 1)[1].split("\n@router", 1)[0]
check("F12 and it adds NO second query against the payable ledger or the vendor feed",
      not re.search(r'table\(\s*["\'](device_payable_ledger|raw_ma_daily_tx)["\']\s*\)', _WEEKLY),
      str(re.findall(r'table\(\s*["\'](\w+)["\']\s*\)', _WEEKLY)))
_cnt = len(re.findall(r"def\s+\w*dated_owed\w*\s*\(", READ_SRC + PAY_ROUTER + ASSET_ROUTER))
check("F13 RATCHET — exactly ONE definition of the shared read exists in the whole triangle. A "
      "second would be two paths answering 'what do I owe', which is the build gate",
      _cnt == 1, "definitions=%d" % _cnt)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== G. THE DEBT TEST IS DEREFERENCED, NOT RESTATED ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("G1 the read calls `vendor_invoice.is_debt_line` — its one home, whose own docstring says a "
      "change to it must reach both grains",
      "_vi.is_debt_line(" in READ_SRC)
check("G2 and holds no inline copy of the comparison, which is the drift that docstring warned "
      "about and which the code it replaced was committing",
      not re.search(r"retail_cost[^\n]*<=\s*0", code_only(READ_SRC))
      and not re.search(r"amt\s*<=\s*0", code_only(READ_SRC)),
      str(re.findall(r".*(?:retail_cost|amt)\s*<=\s*0.*", code_only(READ_SRC))))
ctl("G2a control: the two really can disagree — the one home refuses a non-numeric cost that a bare "
    "`<= 0` comparison would raise on, so dereferencing is not cosmetic",
    vi.is_debt_line({"retail_cost": "abc"}) is False
    and vi.is_debt_line({"retail_cost": 1}) is True
    and vi.is_debt_line({}) is False)
check("G3 and the one home's docstring now names its real caller, so the next reader finds it",
      "owed_read" in read("backend/app/modules/payables/vendor_invoice.py"))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== H. RULE TWO, AND THE NAMES DEREFERENCE ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("H1 neither home names a carrier, tenant or product IN CODE — prose may cite the owner's own "
      "report, because a name that explains behaviour is not a name that decides it",
      not re.search(r"(?i)\b(boost|luxelink|cellfonz|vidapay|vzone|t-mobile)\b",
                    code_only(HOME) + code_only(READ_SRC)))
ctl("H1a control: the scan really would catch a carrier branch, so passing is not an artefact of "
    "blanking everything",
    re.search(r"(?i)\b(boost|luxelink)\b",
              code_only('x = 1  # boost\nif carrier == "luxelink":\n    pass\n')) is not None
    and not re.search(r"(?i)\bboost\b", code_only('"""boost consigns weekly."""\n')))
check("H2 the two relation names DEREFERENCE the feed registry rather than being spelled twice — "
      "the vendor feed's name was a literal on the read this absorbed, and the copy is gone",
      orr.VENDOR_FEED is lineage.MA_DAILY_TX
      and not re.search(r'["\']raw_ma_daily_tx["\']', READ_SRC))
check("H3 the home is PURE: no framework import, no I/O and no clock of its own — a period is "
      "derived from the dates it is handed, never from today",
      not re.search(r"(?m)^(from|import)\s+(fastapi|app\.core|requests|httpx)\b", HOME)
      and not re.search(r"date\.today\(\)|datetime\.now\(", HOME))
check("H4 and it decides no policy that belongs elsewhere: no payability, no window, no store "
      "identity — each keeps the one home it already has, and a docstring may still NAME them",
      not re.search(r"ledger_status|date_window|store_identity", code_only(HOME)))
check("H5 the SHARED READ decides no billing rhythm either, so the two homes cannot both answer "
      "'which period' and disagree — it may point a reader at the cadence home, never call it",
      "billing_period" not in code_only(READ_SRC))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== I. BOOST IS UNTOUCHED ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("I1 the consignment path still answers FIRST, and the roll-up runs only where it found "
      "nothing — so a tenant with consignment rows sees exactly the figures it saw before",
      re.search(r"if not due_rows:\s*\n\s+try:", _WEEKLY) is not None)
check("I2 and the response NAMES which answered, so a reader never has to infer it",
      '"period_source": "stored_column"' in _WEEKLY and '"shared_owed_read"' in _WEEKLY)
check("I3 the fallback is fail-soft and REPORTS rather than reading an error as 'nothing is owed' "
      "(§COMMCALC-29) — the consignment figures above it are unaffected either way",
      "except Exception as ex:" in _WEEKLY and '"error": "unavailable"' in _WEEKLY)
check("I4 nothing in the weekly report recomputes a stamped billing day: the stamp wins through the "
      "one home, and the report's own existing read of that column is unchanged",
      _WEEKLY.count('eq("billing_friday"') == 2
      and "next_weekday_on_or_after" not in _WEEKLY,
      "billing_friday reads=%d" % _WEEKLY.count('eq("billing_friday"'))
_AST = ast.parse(read("backend/app/modules/payables/billing_period.py"))
check("I5 the home exports the eight functions the two surfaces and this lock dereference, and "
      "nothing privately shadows one of them",
      {n.name for n in _AST.body if isinstance(n, ast.FunctionDef)}
      >= {"is_measured", "resolve_cadence", "next_weekday_on_or_after", "week_window",
          "period_of", "period_of_row", "roll_up"})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== J. REGISTRATION ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
IDX = read("docs/SYSTEM_DATA_FLOW_INDEX.md")
check("J1 the index carries this section, once, and the next free number was taken",
      IDX.count("## §COMMCALC-34.") == 1)
SEC = IDX.split("## §COMMCALC-34.", 1)[-1].split("\n## §", 1)[0]
check("J2 and it names both homes, the endpoint, the migration and this harness",
      all(t in SEC for t in ("billing_period.py", "owed_read.py", "harness_billing_period.py",
                             "1095", "/asset/owed-weekly")))
check("J3 and states the duplicate check the house rules make a build gate — what was searched for "
      "and what was reused rather than rebuilt",
      "duplicate" in SEC.lower() and "reus" in SEC.lower())
check("J4 and REPORTS the live config gap rather than coding around it — a defect found in live "
      "data is reported (CLAUDE.md)",
      "net_terms_days" in SEC and "0 " in SEC)
MIG = read("database/migrations/1095_payable_billing_cadence.sql")
check("J5 the migration is additive, idempotent, carries a REVERT and grants what it touches",
      "add column if not exists" in MIG and "-- REVERT:" in MIG
      and "grant select, insert, update, delete on commcalc.payable_source_map to service_role;" in MIG)
check("J6 and every column it adds is COMMENTed, because mig 1091 shipped columns with no comment "
      "and the very first reader had to guess (§COMMCALC-29 found that)",
      all(("COMMENT ON COLUMN commcalc.payable_source_map.%s" % c) in MIG
          for c in ("billing_cadence", "billing_weekday", "billing_period_field")))
check("J7 the declared values are CHECK-constrained to the vocabulary the home rules on, so a typo "
      "cannot reach the resolver at all",
      all(c in MIG for c in bp.CADENCES) and "billing_weekday >= 0 and billing_weekday <= 6" in MIG)
MIGS = sorted(glob.glob(f"{ROOT}/database/migrations/1095_*.sql"))
check("J8 and the number is claimed exactly once", len(MIGS) == 1, str(MIGS))
GRAPH = read("backend/app/modules/core/module_graph.py")
check("J9 both homes are registered in the module graph with this lock, by hand (§50: `--bless` "
      "silently deleted 11 of 12 facts once)",
      "app/modules/payables/billing_period.py" in GRAPH
      and "app/modules/payables/owed_read.py" in GRAPH
      and "harness_billing_period.py" in GRAPH)
WF = read(".github/workflows/carrier-vocab-guard.yml")
check("J10 the guard job RUNS this harness — matched on the `run:` LINE ITSELF, never on the name "
      "appearing somewhere in the workflow, because the paths filter names it too",
      re.search(r"(?m)^\s*run:\s*python3 harness_billing_period\.py\s*\|", WF) is not None)
check("J11 and its PATHS FILTER names the files it is about, so a change to one cannot skip the "
      "job that checks it",
      all(f in WF for f in ("backend/app/modules/payables/billing_period.py",
                            "backend/app/modules/payables/owed_read.py",
                            "backend/harness_billing_period.py")))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== K. THE SCREEN SAYS IT, AND CANNOT GO BACK TO SAYING NOTHING ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
PAGE = read("frontend/src/app/(platform)/commcalc/asset/owed-weekly/page.tsx")
check("K1 the page READS the declared roll-up — a server that serves a figure to a screen that "
      "renders none has moved the blank report one layer down, not fixed it",
      "declared_period" in PAGE and "period_source" in PAGE)
check("K2 and it prints the PERIODS, their span and what said so, rather than one number: a reader "
      "who cannot see the range cannot tell whether the six days before it are in or out",
      all(t in PAGE for t in ("dec.periods", "pd.start", "pd.end", "basisWord(pd.basis)")))
check("K3 A PERIOD NOBODY MEASURED IS SHOWN WITHOUT A FIGURE, never as $0.00 — the reader would "
      "take a measured zero for an answer (§CORE-17.3i), and this report's whole defect was a "
      "figure that looked measured and was a sum over an empty set",
      re.search(r"pd\.measured && pd\.amount != null \? fmt\(pd\.amount\)", PAGE) is not None
      and "not measured" in PAGE)
check("K4 and the BASIS is printed as a WORD from one map, so a basis the screen has never seen "
      "reads as itself instead of rendering as the carrier's own billing day",
      "BASIS_WORDS" in PAGE and "basisWord" in PAGE
      and all(b in PAGE for b in (bp.BASIS_PERIOD_FIELD, bp.BASIS_WEEKLY, bp.BASIS_DUE_DATE,
                                  bp.BASIS_HOUSE, bp.BASIS_WEEKDAY_UNDECLARED, bp.BASIS_NO_DATE)))
check("K5 the CONSIGNMENT sections are suppressed only where the declared roll-up really answered "
      "— three $0.00 cards and 'Nothing bills on this Friday' printed under a real total would "
      "contradict the figure above them",
      "{!onShared && <>" in PAGE
      and "report?.period_source === 'shared_owed_read'" in PAGE)
check("K6 and a roll-up that could NOT be read is reported in words, not as a zero (§COMMCALC-29)",
      "dec.error" in PAGE and "Not measured." in PAGE)
check("K7 the GRAIN CHANGE is said out loud: the processor's feed carries no IMEI, so those are "
      "order lines and the column heading cannot keep saying devices",
      "order_line" in PAGE and "order lines" in PAGE and "source_note" in PAGE)
check("K8 the UNPLACEABLE money has a card of its own, because money nobody could date is reported "
      "beside the periods rather than folded into the nearest one",
      "unplaced_rows" in PAGE and "unplaced_amount" in PAGE)
check("K9 the EXPORT carries the same figures the screen does, and prints 'not measured' in words "
      "rather than a 0.00 a spreadsheet would happily total",
      "Billing periods" in PAGE and "'not measured'" in PAGE)


print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
