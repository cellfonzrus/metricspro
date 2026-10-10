"""PROOF + LOCK — an entitlement is a count of months PER STORE, and a missing store-month is NAMED
(index §68).

OWNER, verbatim 2026-10-10: *"mdf / ramp up subsidy is being finsihed it is inly for 12 months, if we
did not get for 12 months for each store it should be flagged and the missing month and store to be
poimted out , make it into management overview"*.

WHAT THIS PROVES, and the two failures it exists to prevent:

  §A  THE ASK. A store inside a declared 12-month entitlement that did not receive a month is NAMED,
      store and month, and the report's verdict FAILS.
  §B  THE WORST FAILURE AVAILABLE: a store whose first entitled month is NOT declared must never be
      accused of 12 missing months. It is `not_measured` with the reason named, and the control is
      that the SAME rows flip to `missing` the moment a window is declared.
  §C  A MONTH THAT HAS NOT FINISHED IS NOT MISSING. `feed_period.month_state` is the only clock, and
      `today` is injected so this stands on a fixed date.
  §D  THE SPLIT (the §19.53 `unconfirmed_*` precedent): "this store did not get its month" and "we
      never received a feed for that month" are counted SEPARATELY and never netted. Collapsed, the
      report accuses the carrier every time a portal pull fails — the negative control measures
      exactly that difference.
  §E  PRICING. A gap is priced from the store's declaration or its own agreeing payments; a store
      whose payments disagree prices NOTHING and is reported UNPRICED, never $0.00.
  §F  RULE TWO. No store, carrier, category or month COUNT is a literal in a branch; the term, the
      category and every store's window are config, and the payment TYPES are dereferenced from the
      org's own declaration rather than listed here.
  §G  ONE HOME, DEREFERENCED. The three verdict strings are the SHIPPED ones (pinned equal to
      `pay_data_quality`'s own, so they cannot drift); the calendar is `feed_period`'s; there is
      exactly ONE `entitlement_store_months` under backend/app; the store key is never derived here;
      and the endpoint + the registered surfaces exist.
  §H  THE LIVE MEASUREMENT, as the oracle: the house org's 44 payments / $287,500.00 across 6 stores,
      the two stores that stopped, and the figure a declared-window run produces.

DB-FREE, stdlib only. Verified RED against the pre-change tree: without
`app/modules/commcalc/entitlement_coverage.py` every check in §A-§G fails BY NAME (the `_Missing`
shims below) instead of dying with a traceback.

Run:  cd backend && python3 harness_mdf_entitlement_coverage.py
"""
import ast
import datetime as dt
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import app.modules.commcalc.feed_period as FP
import app.modules.commcalc.pay_data_quality as PDQ

try:
    import app.modules.commcalc.entitlement_coverage as EC
except Exception:                                      # pre-change tree: fail by name, never error
    class EC:                                          # noqa: N801
        pass

EC_PATH = "app/modules/commcalc/entitlement_coverage.py"
ROUTER = "app/modules/commcalc/router.py"
INDEX = "../docs/SYSTEM_DATA_FLOW_INDEX.md"
FE_PAGE = "../frontend/src/app/(platform)/commcalc/mdf-entitlement-coverage/page.tsx"
FE_RBAC = "../frontend/src/lib/rbac.ts"
FE_REPORTS = "../frontend/src/lib/reports.ts"
FE_ROUTES = "../frontend/src/lib/route-index.ts"
MIGRATION = "../database/migrations/1068_mdf_entitlement_config.sql"


# ── A LOCK MUST FAIL, NEVER ERROR ─────────────────────────────────────────────────────────────────
class _Missing(dict):
    def __getitem__(self, k):
        return _Missing()

    def get(self, k, d=None):
        return _Missing()


def _fn(mod, name, empty):
    f = getattr(mod, name, None)
    return f if callable(f) else (lambda *a, **k: empty())


def _const(mod, name):
    return getattr(mod, name, "<the one home is missing>")


PASS, FAIL = 0, 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def _part(txt, marker, n=4000):
    """The `n` characters after `marker`, or '' when it is absent — so a scan FAILS, never raises."""
    parts = txt.split(marker, 1)
    return parts[1][:n] if len(parts) > 1 else ""


def _src(rel):
    try:
        return open(os.path.join(_HERE, rel), encoding="utf-8").read()
    except Exception:
        return ""


def _code_only(src):
    """Source with comments and docstrings stripped, so a lock tests CODE and not prose."""
    out = []
    for ln in src.splitlines():
        if ln.strip().startswith(("#", "//")):
            continue
        out.append(ln.split("  #")[0])
    txt = "\n".join(out)
    try:
        tree = ast.parse(txt)
    except SyntaxError:
        return txt
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                txt = txt.replace(d, "")
    return txt


ESM = _fn(EC, "entitlement_store_months", _Missing)
EWIN = _fn(EC, "entitlement_window", list)
NORM = _fn(EC, "normalize_config", _Missing)
CFG_ROWS = _fn(EC, "config_from_rows", _Missing)
TYPE_MATCH = _fn(EC, "entitlement_type_matcher", lambda: (lambda _t: False))
MKEY = _fn(EC, "month_key", lambda: None)

R_RECEIVED = _const(EC, "ROW_RECEIVED")
R_MISSING = _const(EC, "ROW_MISSING")
R_UNCONF = _const(EC, "ROW_UNCONFIRMED_MISSING")
R_NOT_DUE = _const(EC, "ROW_NOT_DUE")
R_NM = _const(EC, "ROW_NOT_MEASURED")
RE_WINDOW = _const(EC, "REASON_WINDOW_NOT_DECLARED")
RE_NOCFG = _const(EC, "REASON_NOT_CONFIGURED")
RE_AMB = _const(EC, "REASON_STORE_AMBIGUOUS")
RE_FEED = _const(EC, "REASON_FEED_MONTH_ABSENT")

TODAY = dt.date(2026, 10, 10)          # the day of the owner's instruction; the only clock in here

# ── THE LIVE MEASUREMENT, read-only against the house org 2026-10-10, as the oracle ───────────────
# `commcalc.payment_categories` declares 'Ramp Up Subsidy' and '4.0 $1K Ramp Up Bonus' into category
# 'MDF'. `raw_payment_detail`: 44 rows / $287,500.00, March-October 2026. `raw_comp_report`: 40 rows /
# $260,500.00, March-September — the two agree store-for-store and dollar-for-dollar in every month
# BOTH carry, and the whole difference is October's 4 rows / $27,000.00 not yet in the statement.
LIVE_TOTAL = 287500.00
LIVE_ROWS = 44
LIVE_RATE = {                          # canonical store key (§64) -> monthly amount
    "509 Nostrand Ave": 7500.00,
    "151 E 170th St": 7500.00,
    "3565 Broadway": 7300.00,
    "1750 5th Ave": 7000.00,
    "11636 Springfield Blvd": 5000.00,
    "103 Fulton Ave": 5000.00,
}
LIVE_MONTHS = ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
LIVE_LAST = {                          # the last month each store was paid
    "509 Nostrand Ave": "2026-10", "151 E 170th St": "2026-10", "1750 5th Ave": "2026-10",
    "11636 Springfield Blvd": "2026-10", "103 Fulton Ave": "2026-09", "3565 Broadway": "2026-07",
}
# The two stores that STOPPED, and the store-months a declared window would name.
LIVE_STOPPED = {"3565 Broadway": ["2026-08", "2026-09", "2026-10"], "103 Fulton Ave": ["2026-10"]}
LIVE_STOPPED_AT_RATE = 7300.00 * 3 + 5000.00        # 26,900.00


def _live_received():
    """The 44 live payments as {(store, month): amount} — the shape the router hands the engine."""
    out = {}
    for st, rate in LIVE_RATE.items():
        for m in LIVE_MONTHS:
            if m <= LIVE_LAST[st]:
                out[(st, m)] = rate
    return out


def _cfg(**over):
    raw = {"entitlement_categories": ["MDF"]}
    raw.update(over)
    return NORM(raw)


# ══ §A THE ASK: a store-month inside a declared entitlement that never arrived is NAMED ═══════════
print("\n§A THE ASK — the missing month and the store are pointed out, and the report FAILS")
ok("A0 the one home EXISTS (a lock must fail by name, never error)",
   callable(getattr(EC, "entitlement_store_months", None))
   and callable(getattr(EC, "entitlement_window", None)))

recv = _live_received()
windows = {st: "2026-03" for st in LIVE_RATE}
res = ESM(recv, _cfg(store_windows=windows, term_months=12), today=TODAY)
ok("A1 the verdict FAILS when a declared store-month never arrived", res.get("verdict") == "failed")
ok("A2 ok is False, not None — a confirmed gap is a judged fact", res.get("ok") is False)
_miss = {(r["store"], r["month"]) for r in (res.get("rows") or []) if r["status"] == R_MISSING}
ok("A3 exactly the measured store-months are named, store AND month",
   _miss == {(st, m) for st, ms in LIVE_STOPPED.items() for m in ms}, f"got {sorted(_miss)}")
ok("A4 4 missing store-months, priced at 26,900.00 from the stores' own payments",
   res["totals"]["missing_store_months"] == 4
   and res["totals"]["missing_amount"] == LIVE_STOPPED_AT_RATE,
   f"got {res['totals']['missing_store_months']} / {res['totals']['missing_amount']}")
ok("A5 every one of the 44 live payments is accounted for as RECEIVED, to the cent",
   res["totals"]["received_store_months"] == LIVE_ROWS
   and res["totals"]["received_total"] == LIVE_TOTAL)
ok("A6 72 store-months judged (6 stores x a declared 12-month term) — the whole window, not the feed",
   res["totals"]["store_months_judged"] == 72)
ok("A7 the four stores still being paid name NO missing month",
   all(not t["missing_months"] for t in res["by_store"] if t["store"] not in LIVE_STOPPED))
ok("A8 the reason says the run DID happen — the fact that makes it the carrier's, not ours",
   "payment run did" in str(res.get("reason") or ""))
ok("A9 each missing row counts the OTHER stores paid that month, as its evidence",
   all(r["stores_paid_in_month"] and r["stores_paid_in_month"] > 0
       for r in res["rows"] if r["status"] == R_MISSING))
ok("A10 the per-store roll-up comes from the SAME rows, so the two cannot disagree",
   sum(t["missing"] for t in res["by_store"]) == res["totals"]["missing_store_months"]
   and sum(t["received"] for t in res["by_store"]) == res["totals"]["received_store_months"])
ok("A11 a 12-month window is 12 months, first and last named from the declared start",
   EWIN("2026-03", 12) == [f"2026-{m:02d}" for m in range(3, 13)] + ["2027-01", "2027-02"])
ok("A12 a window crossing a year end is right (no month-13, no year slip)",
   EWIN("2026-11", 4) == ["2026-11", "2026-12", "2027-01", "2027-02"])

# ══ §B THE WORST FAILURE: never 12 missing months for a window nobody declared ════════════════════
print("\n§B AN UNDECLARED WINDOW IS NOT MEASURED — it is never 12 missing months")
und = ESM(recv, _cfg(), today=TODAY)
ok("B1 with NO store window declared, NOT ONE store-month is called missing",
   und["totals"]["missing_store_months"] == 0 and und["totals"]["unconfirmed_missing_store_months"] == 0)
ok("B2 the verdict is the shipped not_measured, and ok is None — an absence is not a pass",
   und.get("verdict") == "not_measured" and und.get("ok") is None)
ok("B3 all six stores are reported with an undeclared window", und["totals"]["windows_undeclared"] == 6)
_abs = {(r["store"], r["month"]) for r in und["rows"]
        if r["status"] == R_NM and r["reason"] == RE_WINDOW and r["month"]}
ok("B4 what IS named is only what was OBSERVED: the 4 absences after a store's own first payment",
   _abs == {(st, m) for st, ms in LIVE_STOPPED.items() for m in ms}, f"got {sorted(_abs)}")
ok("B5 every ABSENT month on an undeclared store is not_measured with the reason NAMED",
   all(r["status"] == R_NM and r["reason"] == RE_WINDOW and r["reason_label"]
       for r in und["rows"] if r["month"] and r["store"] in LIVE_STOPPED
       and r["month"] > LIVE_LAST[r["store"]]))
ok("B5b ...and what the store DID receive is still listed, so the grid is readable",
   und["totals"]["received_store_months"] == LIVE_ROWS
   and und["totals"]["received_total"] == LIVE_TOTAL
   and len(und["by_store"]) == 6)
ok("B6 the undeclared absences are counted in their OWN total, apart from any gap",
   und["totals"]["undeclared_absence_store_months"] == 4
   and und["totals"]["undeclared_absence_amount_at_observed_rate"] == LIVE_STOPPED_AT_RATE)
ok("B7 the amount is named 'at the observed rate' — the field cannot be read as money owed",
   "at_observed_rate" in " ".join(und["totals"].keys()))
ok("B8 a store that never received anything and has no window is not_measured, not 12 gaps",
   [r["status"] for r in ESM({}, _cfg(store_windows={}), stores=["anystore"], today=TODAY)["rows"]]
   == [R_NM])
# THE CONTROL that B1-B6 are the GUARD's doing and not an empty report: the same rows, one window.
ctl = ESM(recv, _cfg(store_windows={"3565 Broadway": "2026-03"}, term_months=12), today=TODAY)
ok("B9 CONTROL — declaring ONE store's window flips its 3 absences to missing, the rest unchanged",
   ctl["totals"]["missing_store_months"] == 3 and ctl["totals"]["windows_declared"] == 1
   and ctl["totals"]["undeclared_absence_store_months"] == 1)
ok("B10 the reason text names the undeclared stores rather than implying they passed",
   "no declared first entitled month" in str(und.get("reason") or ""))
ok("B11 an unparseable declared start month is DROPPED, so the store stays not_measured",
   NORM({"entitlement_categories": ["MDF"], "store_windows": {"s": "whenever"}})["store_windows"] == {})
ok("B12 with NO category declared nothing is an entitlement and every store says why",
   [r["reason"] for r in ESM(recv, NORM({}), today=TODAY)["rows"]] == [RE_NOCFG] * 6)
ok("B13 an AMBIGUOUS store identity withholds the verdict with its own reason (§64)",
   [r["reason"] for r in ESM({("x", "2026-03"): 1.0}, _cfg(store_windows={"x": "2026-03"}),
                             ambiguous_stores={"x"}, today=TODAY)["rows"]] == [RE_AMB])
ok("B14 CONTROL — with no ambiguous set injected the same store IS judged",
   ESM({("x", "2026-03"): 1.0}, _cfg(store_windows={"x": "2026-03"}, term_months=2),
       today=TODAY)["totals"]["store_months_judged"] == 2)

# ══ §C A MONTH THAT HAS NOT FINISHED IS NOT MISSING ═══════════════════════════════════════════════
print("\n§C THE CALENDAR IS feed_period's, AND A MONTH IN FLIGHT IS NOT OWED")
fut = ESM(recv, _cfg(store_windows=windows, term_months=12), today=TODAY)
ok("C1 the 24 window months beyond the feed are not_due, not missing and not unconfirmed",
   fut["totals"]["not_due_store_months"] == 24)
ok("C2 not one of those future months appears as a gap",
   not [r for r in fut["rows"] if r["status"] in (R_MISSING, R_UNCONF) and r["month"] > "2026-10"])
# The current month with NO run yet: the subsidy posts early in a month, so a month in flight whose
# run has not arrived is not owed. Stand on the 1st of October, before the 10-07 run.
early = ESM({(st, m): r for (st, m), r in recv.items() if m < "2026-10"},
            _cfg(store_windows={"103 Fulton Ave": "2026-03"}, term_months=12),
            today=dt.date(2026, 10, 1))
ok("C3 the month in flight, before its payment run, is not_due — the report does not cry wolf",
   [r["status"] for r in early["rows"] if r["month"] == "2026-10"] == [R_NOT_DUE])
ok("C4 once the run HAS arrived, the same open month IS a gap for the store left out of it",
   [r["status"] for r in fut["rows"]
    if r["month"] == "2026-10" and r["store"] == "103 Fulton Ave"] == [R_MISSING])
ok("C5 an open-month gap is counted in its own subtotal so a reader sees the month is live",
   fut["totals"]["missing_open_month_store_months"] == 2)
ok("C6 every row carries the calendar's own verdict for its month",
   {r["month_state"] for r in fut["rows"] if r["month"] and r["month"] < "2026-10"} == {FP.CLOSED})
ok("C7 the clock is INJECTED — the same rows on a later date judge more months",
   ESM(recv, _cfg(store_windows=windows, term_months=12),
       today=dt.date(2027, 6, 1))["totals"]["not_due_store_months"] == 0)
ok("C8 month_state is DEREFERENCED, not re-derived: no date arithmetic on today in this module",
   "date.today()" not in _code_only(_src(EC_PATH))
   and "month_state(" in _code_only(_src(EC_PATH)))
ok("C9 the month PARSE is feed_period's one home — both spellings resolve through it",
   MKEY("2026-10-07") == "2026-10" and MKEY("October 2026") == "2026-10" and MKEY("nonsense") is None)
ok("C10 a period that is not a month yields NO window, so nothing can be judged against it",
   EWIN("nonsense", 12) == [] and EWIN("2026-03", 0) == [])

# ══ §D THE SPLIT: a feed we never received is not the carrier withholding ═════════════════════════
print("\n§D THE SPLIT — 'this store missed its month' and 'we never got that month' are two facts")
# August's run never reached us at all: no store has an August row. Every other month is as live.
no_aug = {k: v for k, v in recv.items() if k[1] != "2026-08"}
split = ESM(no_aug, _cfg(store_windows=windows, term_months=12), today=TODAY)
ok("D1 the 6 August store-months are UNCONFIRMED, not missing — our ingest, not the carrier",
   split["totals"]["unconfirmed_missing_store_months"] == 6
   and all(r["status"] == R_UNCONF for r in split["rows"] if r["month"] == "2026-08"))
ok("D2 each unconfirmed row names the reason: no payment reached us for ANY store that month",
   {r["reason"] for r in split["rows"] if r["month"] == "2026-08"} == {RE_FEED})
ok("D3 the two buckets are NEVER netted — August is absent from the missing total",
   split["totals"]["missing_store_months"] == 3
   and not [r for r in split["rows"] if r["status"] == R_MISSING and r["month"] == "2026-08"])
ok("D4 and the two amounts are two fields, each priced on its own rows",
   split["totals"]["unconfirmed_missing_amount"] == round(sum(LIVE_RATE.values()), 2)
   and split["totals"]["missing_amount"] != split["totals"]["unconfirmed_missing_amount"])
# THE NEGATIVE CONTROL — what collapsing them would cost, measured.
ok("D5 CONTROL — collapsed, the same month would accuse the carrier of 6 more store-months",
   split["totals"]["missing_store_months"]
   + split["totals"]["unconfirmed_missing_store_months"] == 9)
ok("D6 a month with no run and no gap anywhere cannot PASS — ok stays None",
   ESM({("x", "2026-03"): 1.0}, _cfg(store_windows={"x": "2026-03"}, term_months=3),
       today=TODAY)["ok"] is None)
ok("D7 `months_with_feed` can be injected, so a caller that knows a run arrived says so",
   ESM(no_aug, _cfg(store_windows=windows, term_months=12),
       months_with_feed=LIVE_MONTHS, today=TODAY)["totals"]["missing_store_months"] == 9)
ok("D8 the five row statuses are exactly the declared vocabulary",
   set(_const(EC, "ROW_STATUSES") or ()) == {R_RECEIVED, R_MISSING, R_UNCONF, R_NOT_DUE, R_NM})
ok("D9 the four reasons are exactly the declared vocabulary",
   set(_const(EC, "ROW_REASONS") or ()) == {RE_NOCFG, RE_WINDOW, RE_AMB, RE_FEED})
ok("D10 every status and every reason has words a human reads (no table names, §19.38)",
   all(_const(EC, "ROW_STATUS_LABELS").get(s) for s in (R_RECEIVED, R_MISSING, R_UNCONF, R_NOT_DUE))
   and all(_const(EC, "ROW_REASON_LABELS").get(r) for r in (RE_NOCFG, RE_WINDOW, RE_AMB, RE_FEED))
   and not [w for w in ("raw_payment_detail", "raw_comp_report", "commcalc.", "payment_categories")
            if w in " ".join(list(_const(EC, "ROW_STATUS_LABELS").values())
                             + list(_const(EC, "ROW_REASON_LABELS").values()))])

# ══ §E PRICING: an absence nothing can price is UNPRICED, never $0.00 ═════════════════════════════
print("\n§E PRICING — a gap nothing can price is reported unpriced, never valued at zero")
wobbly = {("w", "2026-03"): 5000.0, ("w", "2026-04"): 6200.0, ("o", "2026-05"): 100.0}
wp = ESM(wobbly, _cfg(store_windows={"w": "2026-03"}, term_months=3), today=TODAY)
ok("E1 a store whose own payments DISAGREE prices nothing",
   wp["totals"]["unpriced_missing_store_months"] == 1 and wp["totals"]["missing_amount"] == 0.0)
ok("E2 ...and the missing store-month is still COUNTED and NAMED",
   wp["totals"]["missing_store_months"] == 1
   and [r["month"] for r in wp["rows"] if r["status"] == R_MISSING] == ["2026-05"])
ok("E3 the report's own words say how many it could not price",
   "nothing can price" in str(wp.get("reason") or ""))
ok("E4 a DECLARED monthly amount prices it, and says the basis is the declaration",
   ESM(wobbly, _cfg(store_windows={"w": "2026-03"}, term_months=3,
                    monthly_amounts={"w": 6000.0}), today=TODAY)["totals"]["missing_amount"] == 6000.0)
ok("E5 a store whose payments AGREE is priced from them, and says so",
   [r["monthly_rate_basis"] for r in res["rows"]
    if r["store"] == "103 Fulton Ave" and r["status"] == R_MISSING] == ["the store's own payments"])
ok("E6 a declaration BEATS the observed rate (the owner's number wins)",
   [r["monthly_rate"] for r in ESM(recv, _cfg(store_windows=windows, term_months=12,
                                              monthly_amounts={"103 Fulton Ave": 5500.0}),
                                   today=TODAY)["rows"]
    if r["store"] == "103 Fulton Ave" and r["status"] == R_MISSING] == [5500.0])

# ══ §F RULE TWO — config, never code ══════════════════════════════════════════════════════════════
print("\n§F RULE TWO — the term, the category and every store's window are CONFIG")
_ec_code = _code_only(_src(EC_PATH))
ok("F1 no live store key appears anywhere in the module's code",
   not [s for s in LIVE_RATE if s in _ec_code], "a store name is in a branch")
ok("F2 no carrier, tenant or payment-type name appears in the module at all",
   not [w for w in ("Ramp Up", "Boost", "boost", "Total Wireless", "luxelink", "MDF")
        if w in _ec_code], "a carrier/product/category name is in code")
ok("F3 the term is a DECLARED number read from config, not a literal in a condition",
   "term_months" in _ec_code and "== 12" not in _ec_code and "range(12" not in _ec_code)
ok("F4 the house term default is 12, and it is overridable",
   _const(EC, "CODE_DEFAULT").get("term_months") == 12
   and NORM({"term_months": 6})["term_months"] == 6)
ok("F5 the house default declares NO category, so an org that declared nothing flags nobody",
   _const(EC, "CODE_DEFAULT").get("entitlement_categories") == []
   and _const(EC, "CODE_DEFAULT").get("store_windows") == {})
ok("F6 two-level config: house blob then the tenant's own, key by key (mig 207's shape)",
   CFG_ROWS({"term_months": 18}, {"entitlement_categories": ["X"], "term_months": 12})
   == NORM({"entitlement_categories": ["X"], "term_months": 18}))
ok("F7 a typo can only make the report say LESS: an out-of-range term keeps the default",
   NORM({"term_months": 0})["term_months"] == 12 and NORM({"term_months": "x"})["term_months"] == 12)
ok("F8 every feed column is re-pointable with one config row",
   set(_const(EC, "CODE_DEFAULT").get("columns") or ())
   == {"line_store", "line_type", "line_amount", "line_date",
       "statement_store", "statement_type", "statement_amount", "statement_date"}
   and NORM({"columns": {"line_store": "loc"}})["columns"]["line_store"] == "loc")
ok("F9 WHICH PAYMENT TYPES are the entitlement is DEREFERENCED from the org's own declaration",
   TYPE_MATCH(_cfg(), {"Ramp Up Subsidy": "MDF"})("Ramp Up Subsidy") is True
   and TYPE_MATCH(_cfg(), {"Ramp Up Subsidy": "MDF"})("Some Commission") is False)
ok("F10 ...and the module keeps no list of payment types of its own",
   TYPE_MATCH(NORM({}), {"Ramp Up Subsidy": "MDF"})("Ramp Up Subsidy") is False)
ok("F11 category matching is case- and spelling-insensitive on the org's own label",
   TYPE_MATCH(_cfg(), {"x": "mdf"})("X") is True)
ok("F12 the config column and its migration are NAMED, so a surface can say what to apply",
   _const(EC, "CONFIG_COLUMN") == "mdf_entitlement_config"
   and _const(EC, "CONFIG_MIGRATION") == "1068_mdf_entitlement_config.sql")

# ══ §G ONE HOME, DEREFERENCED — and it cannot un-wire ═════════════════════════════════════════════
print("\n§G ONE HOME — the verdicts, the calendar and the store key all come from somewhere else")
ok("G1 the three report verdicts ARE pay_data_quality's shipped strings, pinned equal",
   (PDQ.VERDICT_PASSED, PDQ.VERDICT_FAILED, PDQ.VERDICT_NOT_MEASURED)
   == ("passed", "failed", "not_measured") and R_NM == PDQ.VERDICT_NOT_MEASURED)
ok("G2 ...and they are IMPORTED from that home, never respelled here",
   "from app.modules.commcalc.pay_data_quality import" in _src(EC_PATH)
   and '"failed"' not in _ec_code and '"passed"' not in _ec_code)
ok("G3 every verdict this report emits is one of those three",
   {ESM(recv, _cfg(store_windows=windows, term_months=12), today=TODAY)["verdict"],
    ESM(recv, _cfg(), today=TODAY)["verdict"],
    ESM({}, _cfg(), today=TODAY)["verdict"]} <= {"passed", "failed", "not_measured"})
ok("G4 a fully covered declared window PASSES with ok True",
   ESM({("x", "2026-03"): 1.0, ("x", "2026-04"): 1.0},
       _cfg(store_windows={"x": "2026-03"}, term_months=2), today=TODAY)["ok"] is True)
ok("G5 there is exactly ONE entitlement_store_months under backend/app",
   sum(1 for root, _d, fs in os.walk(os.path.join(_HERE, "app")) for f in fs
       if f.endswith(".py")
       and "def entitlement_store_months(" in _src(os.path.join(root, f))) == 1)
ok("G6 this module derives NO store key of its own (§64 — the identity home does that)",
   not [w for w in (".split()", ".lower()", "street", "squash", "store_code")
        if w in _ec_code], "a store key is being derived here")
ok("G7 the module imports no DB client and is pure (stdlib + the pure period parser)",
   not [w for w in ("supabase", "psycopg", "create_client", "requests")
        if w in _src(EC_PATH)])
_rt = _src(ROUTER)
ok("G8 the endpoint exists and DEREFERENCES this home",
   '@router.get("/mdf-entitlement-coverage")' in _rt and "_ec.entitlement_store_months(" in _rt)
ok("G9 the endpoint is org-scoped and READ-ONLY (no writer, no flag, no payout)",
   "require_org(org_id)" in _part(_rt, '@router.get("/mdf-entitlement-coverage")', 1400))
ok("G10 the router reads the store key through the ONE identity home, never its own split",
   "store_resolver(" in _part(_rt, "def _mdf_entitlement_run", 6000)
   and "ambiguous_store_keys(" in _part(_rt, "def _mdf_entitlement_run", 6000))
_rrun = _part(_rt, "def _mdf_entitlement_run", 6000)
ok("G11 the router dereferences §57's own READ of the org's declaration, not a tenth copy of it",
   "_pc.load_map(" in _rrun and "_pc.category_of" in _rrun
   and "payment_categories" not in _rrun)
ok("G11b both feeds are read and UNIONED, so a feed that lags cannot read as an absence",
   "raw_payment_detail" in _rrun and "raw_comp_report" in _rrun and "max(" in _rrun)
ok("G12 the page exists and is registered in the nav, the report directory and the route index",
   bool(_src(FE_PAGE)) and "/commcalc/mdf-entitlement-coverage" in _src(FE_RBAC)
   and "/commcalc/mdf-entitlement-coverage" in _src(FE_REPORTS)
   and "/commcalc/mdf-entitlement-coverage" in _src(FE_ROUTES))
_fe = _src(FE_PAGE)
ok("G13 the page RENDERS the backend's own verdict and reason, and computes neither",
   "data.reason" in _fe and "data.verdict" in _fe
   and not [w for w in ("entitlement_window", "term_months +", "term_months -",
                        "months_with_feed.") if w in _fe])
ok("G13b the page never fills an unpriceable amount with 0 — em dash, never $0.00",
   "=== null || v === undefined ? '\u2014'" in _fe
   and "money(totals.missing_amount)" in _fe)
ok("G13c the page keeps the two absence facts in SEPARATE tiles, never one number",
   "totals.missing_store_months" in _fe and "totals.unconfirmed_missing_store_months" in _fe
   and "missing_store_months +" not in _fe)
ok("G14 the Management Overview tile is a jsonb APPEND with a containment guard (mig 1029's rule)",
   "management-overview" in _src(MIGRATION) and "jsonb_set(" in _src(MIGRATION)
   and "@>" in _src(MIGRATION) and "replace(label" not in _src(MIGRATION))
ok("G15 the migration is additive, idempotent and carries a REVERT note",
   "ADD COLUMN IF NOT EXISTS" in _src(MIGRATION) and "-- REVERT:" in _src(MIGRATION))
ok("G16 the migration declares no store's window — that is the owner's knowledge, not ours",
   not [s for s in LIVE_RATE if s in _part(_src(MIGRATION), "BEGIN;", 100000)])
_idx = _src(INDEX)
ok("G17 the report is registered in the index: its own section, the TOC and the cross-references",
   "## 68." in _idx and "| 68 |" in _idx
   and _idx.count("/commcalc/mdf-entitlement-coverage") >= 3
   and "entitlement_coverage" in _idx)
ok("G18 the proof harness is named in the index beside the report",
   "harness_mdf_entitlement_coverage.py" in _idx)

# ══ §H THE LIVE MEASUREMENT, as the oracle ════════════════════════════════════════════════════════
print("\n§H THE LIVE NUMBERS — what the house org's feed actually says today")
ok("H1 44 payments / $287,500.00 across 6 stores, March-October 2026",
   len(recv) == LIVE_ROWS and round(sum(recv.values()), 2) == LIVE_TOTAL and len(LIVE_RATE) == 6)
ok("H2 6 stores paid through July, 5 in August and September, 4 in October",
   [sum(1 for (_s2, m) in recv if m == mm) for mm in LIVE_MONTHS] == [6, 6, 6, 6, 6, 5, 5, 4])
ok("H3 the two stores that stopped are named, with the months",
   {st: ms for st, ms in LIVE_STOPPED.items()} == LIVE_STOPPED)
ok("H4 TODAY, with no window declared, the report calls NOTHING missing and says why",
   und["totals"]["missing_store_months"] == 0 and und["verdict"] == "not_measured")
ok("H5 ...and it still NAMES all four store-months, which is what the owner asked to see",
   und["totals"]["undeclared_absence_store_months"] == 4)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
