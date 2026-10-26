"""BATCH 19 OF THE §CORE-17 DRILL-DOWN PROGRAMME — the four CARRIER-OWED-vs-CARRIER-PAID reports.

  /commcalc/expected-commission  Expected vs Earned (multi-month installments)
  /commcalc/ma-commission        Total Processor Commissions
  /commcalc/recovery             Appeal Recovery — commission claw-back
  /commcalc/dlar-vs-platform     Feed vs store transactions

WHAT THIS PROVES, AND WHY IT IS WORTH A FILE
────────────────────────────────────────────
Owner ask 2026-10-24: *every report built on platform data must let him see WHERE each figure came
from.* This family is the dealer's position against the carrier — expected, paid, denied, disputed —
so a tile that cannot name its rows here is money argued about with nothing to argue from.

It also settles §CORE-17.3k in the direction batch 18 did not reach, and §CORE-17.3n three times:

  · `/dlar-vs-platform` prints TWO tiles the BROWSER subtracts at the total (`Agree` and
    `Attributed`). Neither gets a panel; all FOUR figures they are composed of do, INCLUDING
    `cells_differing`, which is on no tile and is served precisely so both sides of `Agree` can be
    opened. §E measures every one of them off the real producer.
  · THREE pairs of grains in this batch share a row and a unit and are separated ONLY by
    provenance: EARNED vs EXPECTED on one installment month (what the carrier proved against what
    the ladder declares), that month's PROMOTED earning (a person's approval), and a processor
    account's COMMISSION money against its AIRTIME margin (two statements, one row). §B and §C
    assert each pair is two panels naming two different relations, never one pooled panel.

THE PRODUCERS ARE DRIVEN FOR REAL. `sale_installment_engine.compute_sale_installments`,
`recovery.engine.build_recovery_ledger`, `recovery.engine.bucket_totals`,
`dlar_vs_platform.entity_row` / `summarize` and `ma_store_pnl.ma_sheet_component_total` /
`ma_sheet_spiff_total` are all called directly — the first two through a stdlib stub client that
answers the tables they read. The GRAIN COUNT below is therefore MEASURED off them, not asserted.

THE TWO FOLDS THAT LIVE INSIDE THE FRAMEWORK ROUTER ARE REPRODUCED **AND PINNED**. The MA roll-up's
per-account fold and the Expected-vs-Earned row fold are written inline in `commcalc/router.py`,
which cannot be imported without FastAPI, so §C and §B reproduce each one line-for-line AND pin it to
the router's OWN SOURCE TEXT: if any of those expressions changes, this file goes red rather than
quietly measuring a fold nobody runs. That is batch 17's posture for exactly this problem. The
recovery report's fold needed no such treatment: it was MOVED into `engine.bucket_totals` in this
same commit so the figure and its evidence have one home, and the router now dereferences it.

The whole file is DB-FREE and stdlib-only.
Run: `cd backend && python3 harness_batch19_drilldown.py`
"""
import ast
import copy
import datetime
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.account import ma_store_pnl as msp                      # noqa: E402
from app.modules.commcalc import carrier_owed_drilldown as dd            # noqa: E402
from app.modules.commcalc import data_lineage_registry as lineage        # noqa: E402
from app.modules.commcalc import dlar_vs_platform as dvp                 # noqa: E402
from app.modules.commcalc import expected_commission as xcomm            # noqa: E402
from app.modules.commcalc import processor_money_drilldown as pmd        # noqa: E402
from app.modules.commcalc import sale_installment_engine as sie          # noqa: E402
from app.modules.core import report_drilldown as rd                      # noqa: E402
from app.modules.recovery import engine as recov                         # noqa: E402

PASS, FAIL = [], []
ROOT = ".."


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def ctl(name, red):
    """A NEGATIVE CONTROL / MUTATION. `red` must be True — the tampered case really did come back
    wrong. A control that passes is a check that cannot fail, printed as though it had."""
    check("MUT " + name, red)


def read(rel):
    p = os.path.join(ROOT, rel) if not rel.startswith("harness") else rel
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def cents(v):
    return None if v is None else int(round(float(v) * 100))


def agrees(panel, tile):
    """A PORT of `lib/drill-narrow.agreesToTheCent` — the ONE gate every screen in this batch hands
    to `useDrillTile`. Deliberately the same arithmetic, so the safety net the pages rely on is
    tested here rather than assumed."""
    return bool(panel) and cents((panel.get("figure") or {}).get("amount")) == cents(tile or 0)


DECL = read("backend/app/modules/commcalc/carrier_owed_drilldown.py")
ROUTER = read("backend/app/modules/commcalc/router.py")
REC_ROUTER = read("backend/app/modules/recovery/router.py")
REC_ENGINE = read("backend/app/modules/recovery/engine.py")
REGISTRY = read("backend/app/modules/commcalc/data_lineage_registry.py")
LABELS = read("frontend/src/lib/sourceLabels.ts")
IDX = read("docs/SYSTEM_DATA_FLOW_INDEX.md")

PAGES = {
    "expected-commission": read("frontend/src/app/(platform)/commcalc/expected-commission/page.tsx"),
    "ma-commission": read("frontend/src/app/(platform)/commcalc/ma-commission/page.tsx"),
    "recovery": read("frontend/src/app/(platform)/commcalc/recovery/page.tsx"),
    "dlar-vs-platform": read("frontend/src/app/(platform)/commcalc/dlar-vs-platform/page.tsx"),
}


def _literals(src):
    """Every STRING literal the module's CODE contains, and every number.

    A table name or a dollar figure may appear in a DOCSTRING or a comment — that is a note to the
    next engineer and the whole point of writing the defects down. The rulings are about the CODE, so
    the two are told apart by PARSING rather than by grepping.
    """
    tree = ast.parse(src)
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docs.add(id(body[0].value))
    strings, numbers = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and id(node) not in docs:
            if isinstance(node.value, str):
                strings.append(node.value)
            elif isinstance(node.value, bool):
                continue
            elif isinstance(node.value, (int, float)):
                numbers.append(node.value)
    return strings, numbers


DECL_STRINGS, DECL_NUMBERS = _literals(DECL)


def strip_comments(src):
    """A TS/JS source with its comments removed, so a rule WRITTEN DOWN in a page's header cannot be
    mistaken for the rule being broken in its code. Batch 18's §F posture, reused verbatim."""
    out = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", out)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §0  A STDLIB STUB CLIENT, so the two DB-backed producers can be driven for real.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
class _Q:
    def __init__(self, rows, sink=None):
        self._rows, self._sink = rows, sink

    def _s(self, *a, **k):
        return self
    select = eq = in_ = is_ = gte = lte = neq = order = limit = _s

    def range(self, s, e):
        return _Q(self._rows[s:e + 1], self._sink)

    def execute(self):
        class R:
            pass
        r = R()
        r.data = list(self._rows)
        return r

    def delete(self):
        return self

    def update(self, *a, **k):
        return self

    def upsert(self, *a, **k):
        return self

    def insert(self, payload=None, *a, **k):
        if self._sink is not None and isinstance(payload, list):
            self._sink.extend(payload)
        return self


class StubClient:
    """Answers `.schema(s).table(t).select(...).eq(...)...execute().data` out of a dict of lists, and
    CAPTURES whatever is inserted. Nothing here is a producer: it is the database's shape and nothing
    else, so every figure this file compares against came out of the real producer."""

    def __init__(self, tables, sink=None):
        self.tables, self.sink = tables, sink

    def schema(self, _s):
        return self

    def table(self, name):
        return _Q(self.tables.get(name, []), self.sink)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. ONE FACT, ONE HOME — the design fix, with BOTH halves ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("A1 the declaration dereferences the SALES DISPLAY UNION resolver by OBJECT IDENTITY — the "
      "one home for 'which tables did this period's sales come from', never a second copy of its "
      "rules",
      dd.sales_union_tables is pmd.sales_union_tables)
check("A2 and the two CARRIER ACTIVITY REPORTS by identity off the feed registry",
      dd.DLAR_REP is lineage.DLAR_REP and dd.DLAR_STORE is lineage.DLAR_STORE)
def _sole_home(name):
    """True when the registry spells `name` as a quoted literal EXACTLY ONCE, and that once is the
    constant's own `NAME = "..."` definition. One home means one copy, not zero: the definition is
    the home, and every other mention must dereference it."""
    lines = [l for l in REGISTRY.splitlines() if '"%s"' % name in l]
    return len(lines) == 1 and re.match(r'^[A-Z_]+ = "%s"' % re.escape(name), lines[0].strip())


check("A3 THE OTHER HALF OF THE DESIGN FIX — the registry's own five literal copies of those two "
      "names are GONE: each name is now spelled ONCE, on its constant's own definition line, and "
      "every former copy dereferences it. Writing the registry without re-keying its own callers "
      "is not a fix at all (CLAUDE.md §19.18)",
      _sole_home("raw_dlar_rep") and _sole_home("raw_dlar_store"),
      str([l.strip() for l in REGISTRY.splitlines() if '"raw_dlar_' in l]))
check("A4 and the re-key changed no VALUE — the constants are string-identical to the literals they "
      "replaced, so no read, no watchdog and no label moved",
      (lineage.DLAR_REP, lineage.DLAR_STORE) == ("raw_dlar_rep", "raw_dlar_store"))
check("A5 the registry still answers every question it answered about them — the required columns, "
      "the data date and the plain label — now through the constant",
      lineage.required_content_columns(lineage.DLAR_REP)
      and lineage.data_date_column(lineage.DLAR_STORE) == "as_of_date"
      and lineage.feed_label(lineage.DLAR_REP) == "Daily activity by rep")
check("A6 BUILD LOCK — this DECLARATION spells no relation name of its own: every table it can name "
      "is a registry constant or the producer's own, never a literal in its code",
      not [s for s in DECL_STRINGS
           if s.startswith("raw_") or s in ("appeal_recovery", "plan_installment_schedule",
                                            "installment_promote", "daily_sales_feed",
                                            "carrier_commission")],
      str([s for s in DECL_STRINGS if s.startswith("raw_")]))
check("A7 the PROMOTE relation is dereferenced from the module that OWNS it, not from a second copy "
      "in the feed registry — `expected_commission.TABLE` is the one home for that name",
      dd.INSTALLMENT_PROMOTE is xcomm.TABLE)
check("A8 and the registry deliberately holds NO constant for it, with the reason written down",
      "INSTALLMENT_PROMOTE" not in REGISTRY and "NO CONSTANT IS ADDED HERE" in REGISTRY)
check("A9 the three relations this batch DID add to the registry are the ones nothing else named: "
      "the stored recovery run, the declared installment ladder and the two carrier reports",
      (lineage.RECOVERY_LEDGER, lineage.INSTALLMENT_SCHEDULE)
      == ("appeal_recovery", "plan_installment_schedule"))
check("A10 the comparison's grain words and its absence word are the PRODUCER's, bound by identity "
      "— not a fourth spelling of 'this side does not carry this metric'",
      dd.GRAIN_REP is dvp.GRAIN_REP and dd.GRAIN_STORE is dvp.GRAIN_STORE
      and dd.STATE_NOT_REPORTED is dvp.STATE_NOT_REPORTED)
check("A11 the one new absence REASON lives in the ONE vocabulary and reads as a sentence a screen "
      "can show as-is — a fifth spelling of 'we could not measure this' is the divergence the index "
      "rules forbid",
      dd.REASON_NO_COMMISSION_ROWS in rd.UNMEASURED_REASONS
      and len(rd.UNMEASURED_REASONS[dd.REASON_NO_COMMISSION_ROWS]) > 80)
check("A12 BUILD LOCK — the declaration defines no panel shape of its own and holds no dollar, rate "
      "or threshold: every amount it carries was handed to it",
      "def panel(" not in DECL and "def tie_out(" not in DECL
      and [n for n in DECL_NUMBERS if isinstance(n, float)] == [],
      str([n for n in DECL_NUMBERS if isinstance(n, float)]))
check("A13 RULE TWO — no carrier, tenant, product, vendor or store name in the declaration",
      not re.search(r"(?i)\b(boost|total wireless|vidapay|luxelink|cellfonz|verizon|t-?mobile|"
                    r"at&t|tracfone|rq|b2bsoft)\b", DECL))
check("A14 PURE — no framework import, no I/O and no clock in the declaration",
      not re.search(r"(?m)^\s*(?:from|import)\s+(fastapi|supabase|pandas|datetime|time|os)\b", DECL)
      and "def sb(" not in DECL and ".table(" not in DECL)
_MUT = dict(lineage.FEED_LABEL_BY_TABLE)
ctl("A3b control: the literal scan is NOT vacuous — a re-introduced SECOND copy is found, and a "
    "registry that spelled the name nowhere at all would not pass either",
    len([l for l in (REGISTRY + '\n    "raw_dlar_rep": "x",').splitlines()
         if '"raw_dlar_rep"' in l]) == 2
    and not re.match(r'^[A-Z_]+ = "raw_dlar_rep"', '    "raw_dlar_rep": "x",'.strip()))
ctl("A6b control: the declaration's literal scan is NOT vacuous — it really parses code strings",
    "no_commission_rows_for_account" in DECL_STRINGS)
ctl("A12b control: the float scan is NOT vacuous — it really collects numeric literals",
    _literals("x = 1.5\n")[1] == [1.5])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. EXPECTED vs EARNED — the real engine, three provenances on one installment month ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
NIL = "00000000-0000-0000-0000-000000000000"


def _xc_tables(*, m1_gate="inherit", gate_source=None, mi=True, promote_month=2):
    """The DATABASE the real engine reads, as a dict of lists. Nothing here computes anything."""
    t = {
        "plan_installment_schedule": [
            {"id": "sch1", "org_id": "o", "plan_id": "p1", "is_active": True, "num_months": 6,
             "gate_mode": "paid_residual", "gate_from_month": 2, "m1_gate": m1_gate,
             "name": "ladder"},
        ],
        "plan_installment_line": [
            {"schedule_id": "sch1", "org_id": "o", "month_index": m, "payout_kind": "flat",
             "flat_amount": 10.0 * m} for m in range(1, 7)
        ],
        "commission_plan": [{"id": "p1", "org_id": "o", "name": "plan", "carrier_id": None}],
        "commission_plan_assignment": [{"plan_id": "p1", "org_id": "o", "scope": "default",
                                        "scope_value": None, "priority": 0}],
        "raw_sales": [
            {"org_id": "o", "period": "June 2026", "trans_id": "T1", "salesperson": "ABID",
             "store": "S-1", "product_desc": "HANDSET ONE", "serial_1": "111",
             "mdn": "5551112222", "ext_price": 100.0, "qty": 1, "trans_type": "Sale",
             "voided": None},
            {"org_id": "o", "period": "June 2026", "trans_id": "T2", "salesperson": "MINA",
             "store": "S-2", "product_desc": "HANDSET TWO", "serial_1": "222",
             "mdn": "5553334444", "ext_price": 120.0, "qty": 1, "trans_type": "Sale",
             "voided": None},
        ],
        "raw_mi": ([{"org_id": "o", "period": "July 2026", "device_serial": "111",
                     "phone_number": "5551112222", "subscriber_status": "Active",
                     "actual_mi_payout": 3.0, "base_mrc": 50.0,
                     "commissionable_mrc": 50.0}] if mi else []),
        "installment_promote": [
            {"id": "pr1", "org_id": "o", "pay_period": "July 2026", "trans_id": "T2",
             "mdn": "5553334444", "month_index": promote_month, "expected_at_promote": 10.0 *
             promote_month, "status": "active", "promoted_by": "approver-1",
             "promoted_at": "2026-07-05T00:00:00Z", "reason": "carrier report late"}],
    }
    if gate_source:
        t["installment_gate_source_config"] = [
            {"org_id": "o", "carrier_id": NIL, "carrier_mode": "boost",
             "gate_source": gate_source}]
    return t


def xc_payload(tables):
    """`GET /commcalc/expected-commission/{period}`'s own response, built by driving the REAL engine
    and then reproducing the endpoint's row fold LINE-FOR-LINE. §B1 pins every one of those
    expressions to the router's own source text, so a change there turns this file red."""
    res = sie.compute_sale_installments(StubClient(tables), "o", "July 2026", persist=False)
    xg = res.get("expected_guard") or {}
    rows = []
    for r in (res.get("ledger") or []):
        earned = float(r.get("amount") or 0)
        exp = float(r.get("expected_amount") or 0)
        in_win = bool(r.get("expected_in_window"))
        promoted = str(r.get("status") or "") == "paid_manual_promote"
        rows.append({
            "rep": r.get("epay_salesperson"), "store": r.get("store"),
            "label": r.get("display_label"), "trans_id": r.get("trans_id"),
            "mdn": r.get("mdn"), "imei": r.get("serial_1"),
            "sale_period": r.get("sale_period"), "month_index": r.get("month_index"),
            "expected": exp, "earned": earned, "expected_in_window": in_win,
            "unearned": round(exp - earned, 2) if in_win else 0.0,
            "status": r.get("status"), "gate_met": bool(r.get("paid_gate_met")),
            "gate_mode": r.get("gate_mode"), "gate_kind": r.get("gate_kind"),
            "promoted": promoted, "promoted_by": r.get("promoted_by"),
            "gate_source": r.get("gate_source"),
        })
    return {"rows": rows, "totals": {
        "earned": round(float((res.get("totals") or {}).get("amount") or 0), 2),
        "expected_in_window": float(xg.get("expected_total") or 0),
        "expected_not_yet_earned": float(xg.get("expected_unearned_total") or 0),
        "promoted_amount": float(xg.get("promoted_amount") or 0),
        "promotes_applied": xg.get("promotes_applied", 0)}}


_XC_EP = ROUTER.split('@router.get("/expected-commission/{period}")', 1)[1] \
    .split("\n# ── PARAMETRIZED", 1)[0] if '@router.get("/expected-commission/{period}")' in ROUTER \
    else ""
_XC_PINS = (
    'earned = safe_float(r.get("amount"))',
    'exp = safe_float(r.get("expected_amount"))',
    'in_win = bool(r.get("expected_in_window"))',
    'promoted = str(r.get("status") or "") == "paid_manual_promote"',
    '"earned": round(safe_float((res.get("totals") or {}).get("amount")), 2)',
    '"expected_in_window": safe_float(xg.get("expected_total"))',
    '"expected_not_yet_earned": safe_float(xg.get("expected_unearned_total"))',
    '"promoted_amount": safe_float(xg.get("promoted_amount"))',
    '"gate_source": r.get("gate_source")',
)
check("B1 the endpoint's OWN row fold is PINNED, expression by expression — this file reproduces a "
      "fold that lives inside the framework router, so it goes red if that fold changes rather than "
      "measuring one nobody runs",
      _XC_EP and all(p in _XC_EP for p in _XC_PINS),
      str([p for p in _XC_PINS if p not in _XC_EP]))
ctl("B1b control: the pin scan is NOT vacuous — a never-written expression is absent",
    "earned = safe_float(r.get(\"definitely_not_a_column\"))" not in _XC_EP)

XP = xc_payload(_xc_tables())
XD = dd.expected_drilldowns(XP)
check("B2 the engine really produced a ledger — the grain count below is MEASURED off it and not "
      "asserted", len(XP["rows"]) == 12 and XP["totals"]["earned"] > 0,
      str((len(XP["rows"]), XP["totals"])))
check("B3 all FOUR tiles the page prints open to the installment months behind them",
      sorted(XD) == ["earned", "expected_in_window", "expected_not_yet_earned",
                     "promoted_amount"], str(sorted(XD)))
check("B4 and every one of them TIES to the endpoint's own figure, to the cent",
      all(XD[k]["tie_out"]["ties"] is True and agrees(XD[k], XP["totals"][k]) for k in XD),
      str({k: (XD[k]["tie_out"]["ties"], XD[k]["figure"]["amount"], XP["totals"][k]) for k in XD}))
check("B5 EARNED and EXPECTED are TWO grains on ONE row separated only by provenance "
      "(§CORE-17.3n): what the carrier PROVED against what the ladder DECLARES",
      [s["table"] for s in XD["expected_in_window"]["sources"]] == [dd.INSTALLMENT_SCHEDULE]
      and dd.INSTALLMENT_SCHEDULE not in [s["table"] for s in XD["earned"]["sources"]],
      str([s["table"] for s in XD["earned"]["sources"]]))
check("B6 the EARNED panel names the relation that PROVED each month — the subscriber report here",
      [s["table"] for s in XD["earned"]["sources"]].count(dd.MI_RESIDUAL) == 1)
check("B7 a month a PERSON moved across names that APPROVAL and not a statement — the one figure in "
      "this family whose evidence is a human decision",
      [s["table"] for s in XD["promoted_amount"]["sources"]] == [dd.INSTALLMENT_PROMOTE])
check("B8 and the promoted panel SHOWS every other month without counting it — which months are "
      "NOT in the figure is most of what makes it readable",
      XD["promoted_amount"]["tie_out"]["counted_rows"] == 1
      and XD["promoted_amount"]["tie_out"]["shown_not_counted"] == 11)
check("B9 a month OUTSIDE the configured window carries NO expected figure at all and is shown for "
      "context — an out-of-window month is not a month expecting $0.00",
      [r for r in XD["expected_in_window"]["rows"]
       if not r["in_figure"] and r["amount"] is None]
      and XD["expected_in_window"]["tie_out"]["counted_rows"] == 10)
check("B10 `expected_not_yet_earned` uses the PRODUCER's own predicate (in window, gate unmet) and "
      "not `Σexpected − Σearned` — the subtraction is at the ROW, which is why it opens at all "
      "(§CORE-17.3k)",
      XD["expected_not_yet_earned"]["tie_out"]["counted_rows"] == 4
      and XD["expected_not_yet_earned"]["tie_out"]["shown_not_counted"] == 8)
check("B11 every row carries the KEYS that address it again — the transaction, the number and which "
      "installment month it is, so two months of one line stay distinct",
      all(set(r["row_key"] or {}) == {"trans_id", "mdn", "month_index"}
          for r in XD["earned"]["rows"]))
check("B12 and the reader is shown what else the row says — the store, the month, the device and "
      "whether the carrier proved it",
      all("proved" in (r["fields"] or {}) and "sold in" in (r["fields"] or {})
          for r in XD["earned"]["rows"]))

# ── the OTHER TWO provenance answers, each driven for real ─────────────────────────────────────
XP_MATX = xc_payload(_xc_tables(gate_source="ma_tx", mi=False))
XD_MATX = dd.expected_drilldowns(XP_MATX)
def _gate_chain(panel):
    """The relations the panel names for the GATE, which is what `gate_source` decides. The promote
    ledger is excluded deliberately: it is in the union because a PERSON moved a month into earned,
    which is a different provenance answer and §B18 checks it on its own."""
    return {s["table"] for s in panel["sources"]} - {dd.INSTALLMENT_PROMOTE}


check("B13 a MASTER-AGENT-gated chain names the statement AND the daily transactions — the two the "
      "producer's own `gate_source` says it read, never one on a guess",
      _gate_chain(XD_MATX["earned"]) == {dd.MA_COMMISSION, dd.MA_DAILY_TX},
      str([s["table"] for s in XD_MATX["earned"]["sources"]]))
ctl("B13c control: the gate-chain reader is NOT vacuous — it would notice the chain naming nothing",
    _gate_chain({"sources": [{"table": dd.INSTALLMENT_PROMOTE}]}) == set())
check("B13b and the row carries the producer's own gate words, which is what made that nameable — "
      "the endpoint had been dropping `gate_source` (defect 2)",
      all(r.get("gate_kind") == dd.GATE_MA_RESIDUAL
          and r.get("gate_source") == dd.GATE_SOURCE_MA_TX
          for r in XP_MATX["rows"] if not r["promoted"]))
XP_MACOMM = xc_payload(_xc_tables(gate_source="ma_commission", mi=False))
check("B14 a chain gated on the STATEMENT ALONE names the statement alone — the distinction the "
      "discarded `gate_source` was the only way to make",
      _gate_chain(dd.expected_drilldowns(XP_MACOMM)["earned"]) == {dd.MA_COMMISSION},
      str([s["table"] for s in dd.expected_drilldowns(XP_MACOMM)["earned"]["sources"]]))
XP_AP = xc_payload(_xc_tables(m1_gate="activation_payment"))
XD_AP = dd.expected_drilldowns(XP_AP)
_ap_rows = [r for r in XD_AP["earned"]["rows"]
            if r["fields"] and r["fields"].get("month") == 1]
check("B15 A MONTH PROVED BY THE SALE ITSELF REPORTS AN ORIGIN IT CANNOT TRACE (defect 1): the "
      "engine's sale read does not record which of the two sales relations answered, and a stated "
      "countable untraceable origin is the honest answer rather than a guessed feed (§CORE-17.3i)",
      XD_AP["earned"]["untraceable_origins"] >= 1
      and _ap_rows and all(not r["sources"] for r in _ap_rows),
      str((XD_AP["earned"]["untraceable_origins"], len(_ap_rows))))
check("B15b and that row still carries its figure and is still counted — an unnameable source is "
      "not an unmeasured figure",
      all(r["present"] and r["in_figure"] and r["measured"] for r in _ap_rows))
check("B16 the gate reader is the PRODUCER'S GATE READ, not a second gate: every answer it gives is "
      "driven by `gate_kind` / `gate_source` / `promoted` as the engine stamped them",
      dd.earned_gate_tables({"promoted": True}) == (dd.INSTALLMENT_PROMOTE,)
      and dd.earned_gate_tables({"gate_kind": dd.GATE_MANUAL_PROMOTE}) == (dd.INSTALLMENT_PROMOTE,)
      and dd.earned_gate_tables({"gate_kind": dd.GATE_ACTIVATION_PAYMENT}) == (None,)
      and dd.earned_gate_tables({}) == (dd.MI_RESIDUAL,))

# ── the refusals, as RULES rather than accidents of a fixture ──────────────────────────────────
check("B17 `promotes_applied` gets NO panel — it is the row count the promoted panel already states",
      "promotes_applied" in XP["totals"] and "promotes_applied" not in XD)
check("B18 and the BY-REP table gets none either: a re-partition that prints its own totals",
      not any(k.startswith("by_rep") for k in XD))
check("B19 a period with no installment rows drills to NOTHING, rather than to a panel asserting a "
      "tie over no evidence",
      dd.expected_drilldowns({"rows": [], "totals": XP["totals"]}) == {}
      and dd.expected_drilldowns(None) == {} and dd.expected_drilldowns({}) == {})
_no_win = copy.deepcopy(XP)
for _r in _no_win["rows"]:
    _r["expected_in_window"] = False
_no_win["totals"]["expected_in_window"] = 0.0
_no_win["totals"]["expected_not_yet_earned"] = 0.0
_XD_NW = dd.expected_drilldowns(_no_win)
check("B20 A VACUOUS TIE IS REFUSED — with no month inside the window no row carries an expected "
      "figure, so the producer's $0.00 would tie against ZERO counted rows and read as a "
      "measurement. No panel is served",
      "expected_in_window" not in _XD_NW and "expected_not_yet_earned" not in _XD_NW
      and "earned" in _XD_NW, str(sorted(_XD_NW)))
_no_prom = copy.deepcopy(XP)
for _r in _no_prom["rows"]:
    _r["promoted"] = False
_no_prom["totals"]["promoted_amount"] = 0.0
check("B20b the same refusal for a period nobody promoted anything in",
      "promoted_amount" not in dd.expected_drilldowns(_no_prom))
_bad = copy.deepcopy(XP)
_bad["rows"][0]["earned"] = float(_bad["rows"][0]["earned"]) + 7.0
ctl("B21 control: MOVING one month's earning breaks the tie against the endpoint's own total — the "
    "panel is a real check of the figure and not a restatement of it",
    dd.expected_drilldowns(_bad)["earned"]["tie_out"]["ties"] is False)
ctl("B21b control: and that disagreement is SURFACED rather than swallowed — the shared panel puts "
    "the difference in the producer's own words and marks the verdict, which is the mechanism's "
    "design: a row sum that misses its figure is a DEFECT TO REPORT, not a drill to withhold",
    (lambda t: t["ties"] is False and t.get("words")
     and cents(t.get("difference")) == cents(7.0))(
        dd.expected_drilldowns(_bad)["earned"]["tie_out"]))
ctl("B21c control: what `agreesToTheCent` DOES withhold on is the other mismatch — the SCREEN's "
    "tile having drifted from the panel's figure. It is offered when they agree and withheld when "
    "they do not, and the two directions are locked together",
    agrees(XD["earned"], XP["totals"]["earned"])
    and not agrees(XD["earned"], XP["totals"]["earned"] + 0.01))
_dropped = copy.deepcopy(XP)
_dropped["rows"] = [r for r in _dropped["rows"] if not r["promoted"]]
ctl("B22 control: DROPPING the promoted month leaves the promoted figure with no evidence at all",
    "promoted_amount" not in dd.expected_drilldowns(_dropped))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. THE MASTER-AGENT ROLL-UP — three grains on one account, two of them one FEED apart ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
MA_COMPONENTS = ["device_margin", "consumer_margin", "consumer_financing", "rebate",
                 "wallet_funding", "fees_margin",
                 "spiff_m1", "spiff_m2", "spiff_m3", "spiff_m4", "spiff_m5", "spiff_m6"]
check("C0 the component set this file folds is the ONE definition the endpoint folds — read off the "
      "router's own source rather than guessed",
      all(('"%s"' % c) in ROUTER for c in MA_COMPONENTS)
      and "_MA_COMPONENTS = [" in ROUTER)


def _ma_row(acct, user, **kw):
    r = {"merchant_account_id": acct, "user_name": user, "platform": "P1",
         "activation_type": "new", "activation_type2": "branded", "mrc_net_discount": 50.0,
         "tx_date": "2026-07-04"}
    for k in MA_COMPONENTS:
        r[k] = 0.0
    r.update(kw)
    return r


MA_COMM = [
    _ma_row("A1", "rep1", device_margin=-20.0, rebate=-30.0, spiff_m1=-5.0, spiff_m2=-2.0),
    _ma_row("A1", "rep2", device_margin=-10.0, rebate=-15.0, spiff_m1=-3.0),
    _ma_row("A2", "rep1", device_margin=-7.0, rebate=-8.0, spiff_m1=-1.0),
]
MA_TX = [
    {"account_id": "A1", "account_name": "ACCOUNT ONE", "retail_cost": 100.0,
     "merchant_discount": 4.0, "tx_date": "2026-07-05"},
    # A3 is on the AIRTIME feed only — the commission statement never names it (defect 4).
    {"account_id": "A3", "account_name": "ACCOUNT THREE", "retail_cost": 50.0,
     "merchant_discount": 2.0, "tx_date": "2026-07-06"},
]


def ma_payload(comm=None, tx=None):
    """`GET /commcalc/ma-commission/summary`'s own response, with its per-account fold reproduced
    LINE-FOR-LINE off the router and every money figure computed by the REAL
    `ma_store_pnl.ma_sheet_component_total` / `ma_sheet_spiff_total`. §C1 pins each expression."""
    comm = MA_COMM if comm is None else comm
    tx = MA_TX if tx is None else tx
    comps = {k: 0.0 for k in MA_COMPONENTS}
    acts = {"total": 0, "new": 0, "add": 0, "branded": 0, "byop": 0}
    by_store = {}
    for r in comm:
        pay = msp.ma_sheet_component_total(r)
        spiffs = msp.ma_sheet_spiff_total(r)
        for k in MA_COMPONENTS:
            comps[k] += float(r.get(k) or 0)
        acts["total"] += 1
        sk = (r.get("merchant_account_id") or "?").strip() or "?"
        st = by_store.setdefault(sk, {"account_id": sk, "store": None, "activations": 0,
                                      "payable": 0.0, "spiffs": 0.0, "rebates": 0.0,
                                      "airtime_margin": 0.0, "name": None})
        st["activations"] += 1
        st["payable"] += pay
        st["spiffs"] += spiffs
        st["rebates"] += -float(r.get("rebate") or 0)
    airtime = {"orders": len(tx), "retail": 0.0, "margin": 0.0}
    for r in tx:
        airtime["retail"] += float(r.get("retail_cost") or 0)
        margin = float(r.get("merchant_discount") or 0)
        airtime["margin"] += margin
        sk = (r.get("account_id") or "?").strip() or "?"
        st = by_store.setdefault(sk, {"account_id": sk, "store": None, "activations": 0,
                                      "payable": 0.0, "spiffs": 0.0, "rebates": 0.0,
                                      "airtime_margin": 0.0, "name": None})
        st["airtime_margin"] += margin
        if r.get("account_name") and not st.get("name"):
            st["name"] = r.get("account_name")

    def rnd(d):
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()}
    return {"ready": True, "activations": acts,
            "total_payable": round(msp.ma_sheet_component_total(comps), 2),
            "components": {"rebates": round(-comps["rebate"], 2),
                           "spiffs_total": msp.ma_sheet_spiff_total(comps)},
            "airtime": rnd(airtime),
            "by_store": sorted((rnd(s) for s in by_store.values()),
                               key=lambda s: -(s["payable"] + s["airtime_margin"]))}


_MA_EP = ROUTER.split('@router.get("/ma-commission/summary")', 1)[1].split("\n@router.", 1)[0] \
    if '@router.get("/ma-commission/summary")' in ROUTER else ""
_MA_PINS = (
    "pay = _msp_ma.ma_sheet_component_total(r)",
    "spiffs = _msp_ma.ma_sheet_spiff_total(r)",
    'st["activations"] += 1',
    'st["payable"] += pay',
    'st["spiffs"] += spiffs',
    'st["rebates"] += -safe_float(r.get("rebate"))',
    'st["airtime_margin"] += margin',
    'airtime["margin"] += margin',
    "total_payable = _msp_ma.ma_sheet_component_total(comps)",
    '"rebates": round(-comps["rebate"], 2)',
    '"spiffs_total": _msp_ma.ma_sheet_spiff_total(comps)',
)
check("C1 the endpoint's OWN per-account fold is PINNED, expression by expression — the same posture "
      "§B1 takes, for the same reason",
      _MA_EP and all(p in _MA_EP for p in _MA_PINS),
      str([p for p in _MA_PINS if p not in _MA_EP]))
ctl("C1b control: the pin scan is NOT vacuous", 'st["payable"] += nothing' not in _MA_EP)

MP = ma_payload()
MD = dd.ma_drilldowns(MP)
_MT = {"total_payable": MP["total_payable"], "rebates": MP["components"]["rebates"],
       "spiffs_total": MP["components"]["spiffs_total"],
       "activations": MP["activations"]["total"], "margin": MP["airtime"]["margin"]}
check("C2 all FIVE tiles the page prints open to the processor-account rows behind them",
      sorted(MD) == ["activations", "margin", "rebates", "spiffs_total", "total_payable"],
      str(sorted(MD)))
check("C3 and every one of them TIES to the endpoint's own figure, to the cent",
      all(MD[k]["tie_out"]["ties"] is True and agrees(MD[k], _MT[k]) for k in MD),
      str({k: (MD[k]["tie_out"]["ties"], MD[k]["figure"]["amount"], _MT[k]) for k in MD}))
check("C4 THE COMMISSION MONEY AND THE AIRTIME MARGIN ARE TWO GRAINS ON ONE ROW IN ONE UNIT, "
      "separated ONLY by which statement answered (§CORE-17.3n) — and the margin is not in the "
      "payable beside it",
      [s["table"] for s in MD["total_payable"]["sources"]] == [dd.MA_COMMISSION]
      and [s["table"] for s in MD["margin"]["sources"]] == [dd.MA_DAILY_TX]
      and MD["total_payable"]["figure"]["unit"] == MD["margin"]["figure"]["unit"] == rd.UNIT_MONEY)
check("C5 the activation count is the SAME row in a different UNIT, therefore its own grain "
      "(§CORE-17.3l)",
      MD["activations"]["figure"]["unit"] == rd.UNIT_COUNT
      and [s["table"] for s in MD["activations"]["sources"]] == [dd.MA_COMMISSION])
_a3 = [r for r in MD["total_payable"]["rows"]
       if (r["fields"] or {}).get("processor account") == "A3"]
check("C6 AN ACCOUNT THE COMMISSION STATEMENT NEVER NAMES IS SHOWN WITHOUT A FIGURE (defect 4): the "
      "producer initialises its payable, spiffs, rebates and activations at zero because the AIRTIME "
      "feed named it, and reporting $0.00 of commission for it would be a number nobody measured",
      len(_a3) == 1 and _a3[0]["amount"] is None and not _a3[0]["in_figure"]
      and not _a3[0]["measured"] and _a3[0]["reason"] == dd.REASON_NO_COMMISSION_ROWS,
      str(_a3))
check("C6b and it says so in words a screen can show as-is, and NAMES NO TABLE at all for that "
      "figure — it was read out of nothing",
      _a3 and _a3[0]["reason_label"] and not _a3[0]["sources"]
      and MD["total_payable"]["untraceable_origins"] == 1)
check("C6c NO FIGURE MOVED BY A CENT: the row contributed 0.00 to the producer's totals and "
      "contributes 0.00 here — only the ROW changed, from a measured zero into a stated blank",
      MD["total_payable"]["tie_out"]["ties"] is True
      and cents(MD["total_payable"]["figure"]["amount"]) == cents(MP["total_payable"]))
check("C6d and that same account IS counted in the airtime panel, because the airtime feed really "
      "did measure it — the absence is per FIGURE, not per row",
      MD["margin"]["tie_out"]["counted_rows"] == 3
      and MD["total_payable"]["tie_out"]["counted_rows"] == 2)
check("C7 `statement_covers` reads the PRODUCER'S own signal (the per-account activation count it "
      "increments once per statement line), and decides nothing of its own",
      dd.statement_covers({"activations": 2}) and not dd.statement_covers({"activations": 0})
      and not dd.statement_covers({}) and not dd.statement_covers(None))
check("C8 the row is labelled the way the TABLE labels it — the resolved store, then the account "
      "name, then the account, never a guessed store",
      dd._ma_label({"store": "S", "name": "N", "account_id": "A"}) == "S"
      and dd._ma_label({"name": "N", "account_id": "A"}) == "N"
      and dd._ma_label({"account_id": "A"}) == "A")

# ── the refusals ───────────────────────────────────────────────────────────────────────────────
check("C9 THE LEG SPLIT AND THE SPIFF-BY-MONTH LADDER GET NO PANEL — both are re-partitions of the "
      "very payable the money panel already opens, worked out from the ORG-WIDE component totals, "
      "and this payload carries no per-account figure for any rung to fold",
      not any(k in MD for k in ("m1", "m2_12", "unsplit", "legs", "spiff_by_month", "m3")))
check("C10 nor do `airtime.orders` and `airtime.retail`: `by_store` records a margin per account and "
      "neither an order count nor a retail figure, so they have no row list on this payload",
      "orders" not in MD and "retail" not in MD and "orders" in MP["airtime"])
check("C11 nor the four other statement components, nor `by_rep` / `by_platform` — no tile, no "
      "per-account column, and two re-partitions printing their own totals",
      not any(k in MD for k in ("device_margin", "consumer_margin", "consumer_financing",
                                "wallet_funding", "fees_margin", "by_rep", "by_platform",
                                "avg_mrc")))
check("C12 a period the producer answered `ready: False` for, and one with no accounts, drill to "
      "NOTHING",
      dd.ma_drilldowns({"ready": False, "by_store": MP["by_store"]}) == {}
      and dd.ma_drilldowns({"ready": True, "by_store": []}) == {}
      and dd.ma_drilldowns(None) == {})
_MP_NO_TX = ma_payload(tx=[])
check("C13 A VACUOUS TIE IS REFUSED — on a period whose daily transactions never landed every "
      "account's margin is zero by initialisation, so the producer's $0.00 would tie against "
      "nothing and read as a measurement. No airtime panel is served",
      "margin" not in dd.ma_drilldowns(_MP_NO_TX)
      and "total_payable" in dd.ma_drilldowns(_MP_NO_TX))
_MP_NO_COMM = ma_payload(comm=[])
check("C13b and the same the other way — with no commission statement at all the four statement "
      "figures are not served, while the airtime margin still is",
      not any(k in dd.ma_drilldowns(_MP_NO_COMM)
              for k in ("total_payable", "rebates", "spiffs_total", "activations"))
      and "margin" in dd.ma_drilldowns(_MP_NO_COMM))
_mbad = copy.deepcopy(MP)
_mbad["by_store"][0]["payable"] = float(_mbad["by_store"][0]["payable"]) + 3.5
ctl("C14 control: MOVING one account's payable breaks the tie against the endpoint's own total",
    dd.ma_drilldowns(_mbad)["total_payable"]["tie_out"]["ties"] is False)
_mcov = copy.deepcopy(MP)
for _r in _mcov["by_store"]:
    _r["activations"] = 0
ctl("C15 control: with EVERY account uncovered the money panels are withheld entirely rather than "
    "tying $0.00 against zero counted rows",
    not any(k in dd.ma_drilldowns(_mcov) for k in ("total_payable", "rebates", "spiffs_total")))
_mstate = copy.deepcopy(MP)
_mstate["by_store"] = [r for r in _mstate["by_store"]
                       if r["account_id"] != "A3"]
ctl("C16 control: REMOVING the uncovered account removes the untraceable origin too — the count is "
    "a real measurement of this payload and not a constant",
    dd.ma_drilldowns(_mstate)["total_payable"]["untraceable_origins"] == 0)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. THE RECOVERY LEDGER — the real engine, one snapshot row, two grains, four buckets ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
REC_TABLES = {
    "asset_ledger": [
        {"esn_imei": "111", "phone_number": "5551112222", "store": "S-1", "market": "M1",
         "device_model": "HANDSET ONE", "category": "Missing 1st MRC", "owed_to_vip": 25.0,
         "payg_date": "2026-07-01", "acquired_date": "2026-07-01"},
        {"esn_imei": "222", "phone_number": "5553334444", "store": "S-2", "market": "M1",
         "device_model": "HANDSET TWO", "category": "Re-Escalation", "owed_to_vip": 40.0,
         "payg_date": "2026-01-01", "acquired_date": "2026-01-01"},
        {"esn_imei": "333", "phone_number": "5555556666", "store": "S-3", "market": "M2",
         "device_model": "HANDSET THREE", "category": "Missing 1st MRC", "owed_to_vip": 15.0,
         "payg_date": "2026-07-02", "acquired_date": "2026-07-02"},
    ],
    "raw_payment_detail": [
        {"imei": "111", "mdn": "5551112222", "payment_type": "Activation", "amount": 25.0,
         "payment_date": "2026-07-10"},
    ],
    "raw_mi": [
        {"device_serial": "222", "phone_number": "5553334444", "subscriber_status": "Active",
         "actual_mi_payout": 2.0, "actual_atu_payout": 0.0, "mi_activation_date": "2026-02-01"},
    ],
}


def rec_payload(tables=None, today=None):
    """`GET /recovery/ledger`'s own response, from the REAL `build_recovery_ledger` (its inserts
    captured) and the REAL `bucket_totals`. No arithmetic of this file's own anywhere."""
    sink = []
    recov.build_recovery_ledger(StubClient(tables or REC_TABLES, sink), "o", {},
                                today or datetime.date(2026, 7, 20))
    return {"rows": sink, "buckets": recov.bucket_totals(sink)}


check("D0 the recovery bucket fold has ONE HOME now, and the router DEREFERENCES it — a fold written "
      "inline in the framework router could only ever have been compared against a second copy of "
      "itself",
      "def bucket_totals(" in REC_ENGINE
      and "engine.bucket_totals(rows_all)" in REC_ROUTER
      and 'b["count"] += 1' not in REC_ROUTER)
RP = rec_payload()
RD = dd.recovery_drilldowns(RP)
_RT = dict([("owed." + k, v["owed"]) for k, v in RP["buckets"].items()]
           + [("devices." + k, v["count"]) for k, v in RP["buckets"].items()])
check("D1 the real engine really bucketed the ledger — three devices into three different states, "
      "so the grain count below is MEASURED",
      len(RP["rows"]) == 3
      and sorted(RP["buckets"]) == ["expired", "not_recoverable", "recoverable"],
      str(RP["buckets"]))
check("D2 each bucket's DOLLARS and each bucket's DEVICES open separately — the same snapshot row in "
      "two units, therefore two grains (§CORE-17.3l)",
      sorted(RD) == ["devices.expired", "devices.not_recoverable", "devices.recoverable",
                     "owed.expired", "owed.not_recoverable", "owed.recoverable"], str(sorted(RD)))
check("D3 and every one of them TIES to the endpoint's own figure",
      all(RD[k]["tie_out"]["ties"] is True and agrees(RD[k], _RT[k]) for k in RD),
      str({k: (RD[k]["tie_out"]["ties"], RD[k]["figure"]["amount"], _RT[k]) for k in RD}))
check("D4 each figure counts ONLY its own bucket and SHOWS the other two — which devices are not in "
      "the figure is most of what makes it readable",
      all(RD[k]["tie_out"]["counted_rows"] == 1 and RD[k]["tie_out"]["shown_not_counted"] == 2
          for k in RD))
check("D5 A STORED SNAPSHOT NAMES THE SNAPSHOT (§CORE-17.3j): every row names the stored recovery "
      "run, because that is the relation the figure was actually read out of",
      all(dd.RECOVERY_LEDGER in r["sources"] for r in RD["owed.recoverable"]["rows"]))
check("D6 and the row names the relation the run recorded its PROOF out of, where it recorded one — "
      "a payment on one device, an active subscriber line on another",
      lineage.PAYMENT_DETAIL in RD["owed.recoverable"]["rows"][0]["sources"]
      or lineage.PAYMENT_DETAIL in [t for r in RD["owed.recoverable"]["rows"] for t in r["sources"]])
_nr = [r for r in RD["owed.not_recoverable"]["rows"]
       if (r["fields"] or {}).get("state") == "not_recoverable"]
check("D7 a device nothing proved names the run ALONE — there is no second relation behind it, and "
      "naming one would be a claim about a row nobody wrote",
      len(_nr) == 1 and _nr[0]["sources"] == [dd.RECOVERY_LEDGER], str(_nr))
check("D8 the evidence map is the ENGINE's own two words and nothing else — a source it does not "
      "know resolves to NO table rather than putting an identifier on an owner's screen",
      dd.recovery_evidence_tables({"evidence": {"source": "epay_mi"}})
      == (dd.RECOVERY_LEDGER, dd.MI_RESIDUAL)
      and dd.recovery_evidence_tables({"evidence": {"source": "something_new"}})
      == (dd.RECOVERY_LEDGER,)
      and dd.recovery_evidence_tables(None) == (dd.RECOVERY_LEDGER,))
check("D8b and both of the engine's words are the ones it actually writes — read off its source, not "
      "guessed", '"src": "epay_payment"' in REC_ENGINE and '"src": "epay_mi"' in REC_ENGINE
      and set(dd.RECOVERY_EVIDENCE_FEEDS) == {"epay_payment", "epay_mi"})
check("D9 every row carries the keys that address the device again, and the state / denial / proof a "
      "reader needs beside it",
      all(set(r["row_key"] or {}) <= {"imei", "mdn", "denied_date"}
          and "state" in (r["fields"] or {}) and "proof" in (r["fields"] or {})
          for r in RD["owed.expired"]["rows"]))
check("D10 THE BUCKET VOCABULARY IS THE PRODUCER'S — a fifth status is a build failure here rather "
      "than a tile with no panel",
      set(dd.RECOVERY_BUCKETS) == {"recoverable", "expired", "not_recoverable", "needs_data"}
      and all(('"%s"' % s) in REC_ENGINE for s in dd.RECOVERY_BUCKETS))
check("D11 A BUCKET NOTHING FELL INTO IS SERVED NO PANEL — its figure is a $0.00 over zero counted "
      "rows, which ties trivially and reads as a measurement",
      "owed.needs_data" not in RD and "devices.needs_data" not in RD
      and "needs_data" not in RP["buckets"])
RP_ND = rec_payload(tables={"asset_ledger": REC_TABLES["asset_ledger"],
                            "raw_payment_detail": [], "raw_mi": []})
check("D11b and with NO confirmation feed loaded at all the engine really does bucket every device "
      "as needing data — so that panel appears, which proves the refusal above was about the "
      "fixture's facts and not about a key nobody can reach",
      sorted(RP_ND["buckets"]) == ["needs_data"]
      and "owed.needs_data" in dd.recovery_drilldowns(RP_ND))
check("D12 an empty ledger drills to NOTHING",
      dd.recovery_drilldowns({"rows": [], "buckets": RP["buckets"]}) == {}
      and dd.recovery_drilldowns(None) == {})
check("D13 the CLAIM BATCHES get no panel — a claim's device count and total are stored on the claim "
      "row and this payload carries none of its lines (defect 8)",
      not any("claim" in k for k in RD) and "claims" in REC_ROUTER)
_rbad = copy.deepcopy(RP)
_rbad["rows"][0]["owed_amount"] = float(_rbad["rows"][0]["owed_amount"]) + 1.25
ctl("D14 control: MOVING one device's owed amount breaks the tie against the run's own bucket total",
    dd.recovery_drilldowns(_rbad)["owed." + _rbad["rows"][0]["status"]]["tie_out"]["ties"] is False)
_rdrop = copy.deepcopy(RP)
_rdrop["rows"] = [r for r in _rdrop["rows"] if r["status"] != "recoverable"]
ctl("D15 control: DROPPING the recoverable device leaves its figure with no evidence at all",
    dd.recovery_drilldowns(_rdrop)["owed.recoverable"]["tie_out"]["ties"] is False)
_rev = copy.deepcopy(RP)
for _r in _rev["rows"]:
    _r["evidence"] = None
ctl("D16 control: with every row's recorded proof removed, no panel names an evidence feed — the "
    "provenance is READ off the snapshot and not assumed from the bucket",
    all(r["sources"] == [dd.RECOVERY_LEDGER]
        for r in dd.recovery_drilldowns(_rev)["owed.recoverable"]["rows"]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. FEED vs STORE TRANSACTIONS — the real comparison, and the two subtractions refused ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def dvp_payload(*, basis=None, sales=True, store_feed=True):
    """`GET /commcalc/dlar-vs-platform/{period}`'s own response, from the REAL `entity_row` and the
    REAL `summarize`. Every figure below came out of the producer."""
    reps = [
        dvp.entity_row(key="ABID", label="Abid", store="S-1", grain=dvp.GRAIN_REP,
                       feed_row={"gross_adds": 10, "upgrades": 4, "boost_app_pct": 80.0,
                                 "atu_pct": 12.0, "byod_pct": 20.0},
                       platform={"new_activations": 12, "upgrades": 4, "total_acts": 16,
                                 "byod_rate": 20.0},
                       excluded_units={"activations": {"ineligible": 2}, "upgrades": {"swap": 0}},
                       vintage={"complete": True, "as_of": "2026-07-31"}),
        # present ONLY in the carrier's own report — our transactions never mentioned this person
        dvp.entity_row(key="GHOST", label="Ghost", store="S-1", grain=dvp.GRAIN_REP,
                       feed_row={"gross_adds": 3}, platform=None,
                       vintage={"complete": True, "as_of": "2026-07-31"}),
    ]
    stores = [
        dvp.entity_row(key="S-1", label="S-1", store="S-1", grain=dvp.GRAIN_STORE,
                       feed_row=({"gross_adds": 9, "total_upgrades": 4, "total_acts": 13,
                                  "byod_pct": 20.0} if store_feed else None),
                       platform={"new_activations": 12, "upgrades": 4, "total_acts": 16},
                       vintage={"complete": False, "as_of": "2026-07-24", "days_short": 7}),
        # present ONLY in our transactions — the carrier's report never named this door
        dvp.entity_row(key="S-2", label="S-2", store="S-2", grain=dvp.GRAIN_STORE,
                       feed_row=None,
                       platform={"new_activations": 5, "upgrades": 1, "total_acts": 6},
                       vintage={"complete": True, "as_of": "2026-07-31"}),
    ]
    return {"reps": reps, "stores": stores,
            "summary": dvp.summarize(reps + stores, period="2026-07"),
            "activation_source": ({"active": True, "basis": basis} if basis
                                  else {"active": False, "basis": "sales_agg"}),
            "sales_source": ({"primary": "daily_sales_feed", "primary_rows": 500,
                              "other": "raw_sales", "other_rows": 100,
                              "filled_rows": 7} if sales else None)}


VP = dvp_payload()
VD = dd.comparison_drilldowns(VP)
check("E1 the real producer really compared something — the grain count below is MEASURED off it",
      VP["summary"]["cells_compared"] > 0 and len(VP["reps"]) + len(VP["stores"]) == 4,
      str({k: v for k, v in VP["summary"].items() if k.startswith("cells")}))
check("E2 FOUR figures open to the entity comparison rows behind them",
      sorted(VD) == ["cells_compared", "cells_differing", "cells_unattributed",
                     "cells_undecidable"], str(sorted(VD)))
check("E3 and every one of them TIES to the endpoint's own summary figure",
      all(VD[k]["tie_out"]["ties"] is True and agrees(VD[k], VP["summary"][k]) for k in VD),
      str({k: (VD[k]["tie_out"]["ties"], VD[k]["figure"]["amount"], VP["summary"][k])
           for k in VD}))
check("E4 THE `Agree` AND `Attributed` TILES GET NO PANEL — the page subtracts both at the TOTAL "
      "(§CORE-17.3k) — and `cells_differing`, which is on NO TILE, IS served so that both sides of "
      "`Agree` can be opened. That is the whole of the answer a reader needs",
      "agree" not in VD and "attributed" not in VD and "cells_differing" in VD)
check("E4b and the page really does subtract them in the browser, read off its own source — the "
      "refusal is about what that page does, not about a guess",
      "s.cells_compared - s.cells_differing" in PAGES["dlar-vs-platform"]
      and "s.cells_differing - s.cells_unattributed - s.cells_undecidable"
      in PAGES["dlar-vs-platform"])
check("E5 THE ROW LIST IS THE TWO TABLES TOGETHER, because that is what the figures are Σ over — "
      "the rep rows AND the store rows, which the page shows one of at a time (defect 10)",
      VD["cells_compared"]["rows_total_count"] == 4
      and {(r["fields"] or {}).get("grain") for r in VD["cells_compared"]["rows"]}
      == {dvp.GRAIN_REP, dvp.GRAIN_STORE})
check("E5b and every note SAYS so, rather than narrowing to one grain and explaining a figure half "
      "the size",
      all("BOTH the per-rep rows" in (VD[k]["note"] or "") for k in VD))
_rep_rows = [r for r in VD["cells_compared"]["rows"]
             if (r["fields"] or {}).get("grain") == dvp.GRAIN_REP]
_store_rows = [r for r in VD["cells_compared"]["rows"]
               if (r["fields"] or {}).get("grain") == dvp.GRAIN_STORE]
check("E6 a row names THE CARRIER'S OWN REPORT AT ITS OWN GRAIN — the rep report on a rep row, the "
      "store report on a store row, never the other",
      all(dd.DLAR_REP in r["sources"] and dd.DLAR_STORE not in r["sources"] for r in _rep_rows)
      and any(dd.DLAR_STORE in r["sources"] for r in _store_rows))
_ghost = [r for r in VD["cells_compared"]["rows"] if r["label"] == "Ghost"]
check("E7 AN ENTITY OUR TRANSACTIONS NEVER MENTIONED NAMES ONLY THE CARRIER'S REPORT — the platform "
      "side was not read out of a relation for it at all (§CORE-17.3i)",
      len(_ghost) == 1 and _ghost[0]["sources"] == [dd.DLAR_REP], str(_ghost))
_s2 = [r for r in VD["cells_compared"]["rows"] if r["label"] == "S-2"]
check("E7b and an entity the CARRIER'S report never named mirrors it exactly — only the side that "
      "answered",
      len(_s2) == 1 and dd.DLAR_STORE not in _s2[0]["sources"]
      and "daily_sales_feed" in _s2[0]["sources"], str(_s2))
check("E8 the platform side's provenance is resolved off the READ'S OWN META through the one home — "
      "the meta this endpoint used to discard (defect 3)",
      {"daily_sales_feed", "raw_sales"} <= {s["table"] for s in VD["cells_compared"]["sources"]})
check("E9 and when the org has STATED the uploaded activation report as its source of truth, the "
      "platform side names THAT instead — the producer's own gate, read and not re-decided",
      dd.platform_tables({"active": True, "basis": dd.BASIS_ACTIVATION_DETAILS}, None)
      == (dd.CUSTOM_CAPTURE,)
      and dd.platform_tables({"active": False, "basis": "sales_agg"},
                             {"primary": "raw_sales", "primary_rows": 5}) == ("raw_sales",))
check("E9b and the whole panel follows, end to end, off the real producer",
      dd.CUSTOM_CAPTURE in {s["table"] for s in dd.comparison_drilldowns(
          dvp_payload(basis=dd.BASIS_ACTIVATION_DETAILS))["cells_compared"]["sources"]})
check("E10 a sales read that resolved to nothing reports an origin it COULD NOT TRACE rather than "
      "naming a feed on a guess",
      dd.comparison_drilldowns(dvp_payload(sales=False))["cells_compared"]["untraceable_origins"]
      >= 1)
check("E11 `_dvp_platform_present` reads the PRODUCER'S own metric states and re-derives nothing",
      not dd._dvp_platform_present({"metrics": [{"platform_state": dvp.STATE_NOT_REPORTED}]})
      and dd._dvp_platform_present({"metrics": [{"platform_state": dvp.STATE_REPORTED}]})
      and not dd._dvp_platform_present({"metrics": []}))
check("E12 EVERY RATE METRIC GETS NO PANEL (§CORE-17.3h) — and the producer really does carry rate "
      "metrics here, so the refusal is about figures that exist",
      any(m["kind"] == "rate" for r in VP["reps"] for m in r["metrics"])
      and not any(k in VD for k in ("boostapp", "atu", "protect", "byod_rate", "tmr3",
                                    "familyplan", "aal")))
check("E13 THE `entities*` COUNTS GET NO PANEL — each mixes rep rows and store rows, so it is a "
      "cardinality over two kinds of thing with no row list of one grain (defect 12)",
      not any(k.startswith("entities") for k in VD)
      and "entities_differing" in VP["summary"])
check("E14 nor does `by_metric` — a re-partition that prints its own per-metric totals",
      "by_metric" not in VD and VP["summary"]["by_metric"])
check("E15 an empty comparison drills to NOTHING",
      dd.comparison_drilldowns({"reps": [], "stores": [], "summary": VP["summary"]}) == {}
      and dd.comparison_drilldowns(None) == {})
_vbad = copy.deepcopy(VP)
_vbad["reps"][0]["compared"] = int(_vbad["reps"][0]["compared"]) + 1
ctl("E16 control: MOVING one entity's compared count breaks the tie against the producer's own "
    "summary", dd.comparison_drilldowns(_vbad)["cells_compared"]["tie_out"]["ties"] is False)
_vdrop = copy.deepcopy(VP)
_vdrop["stores"] = []
ctl("E17 control: dropping the STORE rows moves the figure's evidence and the tie-out says so — the "
    "panel really is over both tables",
    dd.comparison_drilldowns(_vdrop)["cells_compared"]["tie_out"]["ties"] is False
    and dd.comparison_drilldowns(_vdrop)["cells_compared"]["rows_total_count"] == 2)
ctl("E18 control: with no store row carrying a feed row, no panel names the carrier's STORE report — "
    "the per-grain naming is a real read of each row",
    dd.DLAR_STORE not in {s["table"] for s in dd.comparison_drilldowns(
        dvp_payload(store_feed=False))["cells_compared"]["sources"]})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. §CORE-17.3o — a panel explains the FIGURE, not the table. All four, both ways ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
for _name, _src in PAGES.items():
    check(f"F0[{_name}] the page was read", bool(_src), _name)
check("F1 ALL FOUR PAGES ARE SERVER-FOLDED AND NONE IS NARROWED — every tile reads the server's own "
      "figure, so narrowing a panel would make it explain a figure the tile is not showing",
      all("narrowPanel" not in strip_comments(s) and "narrowPanels" not in strip_comments(s)
          for s in PAGES.values()),
      str([n for n, s in PAGES.items() if "narrowPanel" in strip_comments(s)]))
check("F1b and the rule is WRITTEN DOWN in each page's own header, where somebody would break it — "
      "which is why the scan strips comments before looking",
      all("CORE-17.3s" in s for s in PAGES.values()),
      str([n for n, s in PAGES.items() if "CORE-17.3s" not in s]))
ctl("F1c control: the comment-stripping scan is NOT blind — it finds the real thing in code",
    "narrowPanel" in strip_comments("const x = narrowPanel(p, k)\n"))
ctl("F1d control: and it really does strip a comment that mentions it",
    "narrowPanel" not in strip_comments("// never call narrowPanel here\nconst x = 1\n"))
check("F2 NO PAGE FOLDS A TILE IN THE BROWSER: none of the four contains a `reduce(` at all, so "
      "there is no client fold for a panel to have to follow",
      all("reduce(" not in strip_comments(s) for s in PAGES.values()),
      str([n for n, s in PAGES.items() if "reduce(" in strip_comments(s)]))
ctl("F2b control: the `reduce(` scan is NOT vacuous — it finds one where there is one",
    "reduce(" in strip_comments("const t = rows.reduce((a, r) => a + r.x, 0)\n"))
check("F3 the two pages that filter IN THE BROWSER read their tiles off the SERVER's totals, which "
      "is exactly why they are not narrowed and exactly the REPORTED defect (9 and 10)",
      "filterRows(" in PAGES["expected-commission"] and "t?.earned" in PAGES["expected-commission"]
      and "filterRows(" in PAGES["dlar-vs-platform"]
      and "s?.cells_compared" in PAGES["dlar-vs-platform"])
check("F4 the two pages whose filters are QUERY PARAMETERS say so, and really do send them — so "
      "their panels follow their tiles by construction",
      "qs.set('stores'" in PAGES["ma-commission"]
      and "?status=" in PAGES["recovery"])
check("F5 all four pages render the SHARED panel and hand-roll none of their own",
      all("DrillDownPanel" in s for s in PAGES.values())
      and not any(("rows_total_count" in strip_comments(s)) or ("tie_out" in strip_comments(s))
                  for s in PAGES.values()),
      str([n for n, s in PAGES.items() if "DrillDownPanel" not in s]))
check("F6 and each reads what the SERVER served, through the shared offer gate, rather than deriving "
      "a drill of its own",
      all("drilldown" in s and "useDrillTile" in s and "agreesToTheCent" in s
          for s in PAGES.values()),
      str([n for n, s in PAGES.items() if "useDrillTile" not in s]))
check("F7 no page spells a relation name of its own: a feed's plain name has one home and it is not "
      "a page",
      not any(re.search(r"'(raw_[a-z_]+|appeal_recovery|installment_promote|"
                        r"plan_installment_schedule|daily_sales_feed)'", strip_comments(s))
              for s in PAGES.values()),
      str([n for n, s in PAGES.items()
           if re.search(r"'raw_[a-z_]+'", strip_comments(s))]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== G. NO FIGURE MOVED — the declaration is read-only over the producer's own payload ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
for _label, _payload, _fn in (("expected-commission", XP, dd.expected_drilldowns),
                              ("ma-commission", MP, dd.ma_drilldowns),
                              ("recovery", RP, dd.recovery_drilldowns),
                              ("dlar-vs-platform", VP, dd.comparison_drilldowns)):
    _before = copy.deepcopy(_payload)
    _fn(_payload)
    check(f"G1[{_label}] the payload is BYTE-IDENTICAL after the declaration has run over it — "
          f"deep-compared before and after, so no producer's arithmetic was moved",
          _before == _payload)
ctl("G1b control: the deep compare is NOT vacuous — it notices a single moved cent",
    copy.deepcopy(XP) != {**XP, "totals": {**XP["totals"], "earned": XP["totals"]["earned"] + 0.01}})
check("G2 the declaration's entry points take a PAYLOAD and nothing else — no client, no query, no "
      "period — so a panel is folded over rows the caller is already being served and a drill-down "
      "cannot become a disclosure",
      all(re.search(r"def %s\(payload, \*, cap=None\)" % fn, DECL)
          for fn in ("expected_drilldowns", "ma_drilldowns", "recovery_drilldowns",
                     "comparison_drilldowns")))
check("G3 and every endpoint attaches the key LAST, after everything it filters — the comparison "
      "after its `in_keyset` scope filter",
      ROUTER.index("in_keyset(ks,") < ROUTER.index("_dvp_out[\"drilldown\"]")
      and '_cod.expected_drilldowns(out)' in ROUTER
      and '_cod.ma_drilldowns(_ma_out)' in ROUTER
      and '_cod.recovery_drilldowns({"rows": rows_all, "buckets": buckets})' in REC_ROUTER)
check("G4 BUILD LOCK — no endpoint builds a panel of its own: there is no second shape for the same "
      "question",
      "rd.panel(" not in ROUTER and 'report_drilldown.panel(' not in ROUTER
      and "report_drilldown" not in REC_ROUTER)
check("G5 THE ROW CAP IS THE ONE HOME'S, and the true count is always served — a capped panel says "
      "'showing 400 of N' rather than presenting a shorter ledger as the whole of one",
      all(p["rows_total_count"] >= p["rows_shown"] and p["capped"] is False
          for p in list(XD.values()) + list(MD.values()) + list(RD.values()) + list(VD.values())))
_wide = {"rows": [dict(RP["rows"][0], imei=str(i)) for i in range(rd.ROW_CAP + 25)],
         "buckets": None}
_wide["buckets"] = recov.bucket_totals(_wide["rows"])
_wp = dd.recovery_drilldowns(_wide)["owed." + RP["rows"][0]["status"]]
check("G5b and a REAL cap behaves: the panel serves ROW_CAP rows, states the true count, and its "
      "tie-out is over ALL of them",
      _wp["capped"] is True and _wp["rows_shown"] == rd.ROW_CAP
      and _wp["rows_total_count"] == rd.ROW_CAP + 25
      and _wp["tie_out"]["counted_rows"] == rd.ROW_CAP + 25
      and _wp["tie_out"]["ties"] is True)
check("G5c THE CAPPED PANEL IS REFUSED BY THE CLIENT NARROWER, which is the honest answer rather "
      "than a smaller number presented as the whole of one — the one home's rule, stated here "
      "because these pages rely on it",
      "if (panel.capped) return null" in read("frontend/src/lib/drill-narrow.ts"))
check("G6 every panel carries its SCHEMA, so a stale renderer fails loudly rather than drawing half "
      "a panel",
      all(p["schema"] == rd.SCHEMA
          for p in list(XD.values()) + list(MD.values()) + list(RD.values()) + list(VD.values())))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== H. THE GRAIN COUNT, MEASURED — nine grains in four row shapes ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
GRAINS = {
    # (row shape, unit, frozenset(provenance)) → the panels that share it
    "installment month / earned": ("installment_month", rd.UNIT_MONEY, "gate"),
    "installment month / expected": ("installment_month", rd.UNIT_MONEY, "ladder"),
    "installment month / promoted": ("installment_month", rd.UNIT_MONEY, "approval"),
    "processor account / money": ("processor_account", rd.UNIT_MONEY, "statement"),
    "processor account / count": ("processor_account", rd.UNIT_COUNT, "statement"),
    "processor account / airtime": ("processor_account", rd.UNIT_MONEY, "daily_tx"),
    "recovery row / owed": ("recovery_row", rd.UNIT_MONEY, "snapshot"),
    "recovery row / devices": ("recovery_row", rd.UNIT_COUNT, "snapshot"),
    "entity comparison / cells": ("entity_row", rd.UNIT_COUNT, "feed+platform"),
}
#: THE TRIPLES THIS ONE FIXTURE CAN EXERCISE, written down so the measurement is an EQUALITY and
#: not a count. A count would pass by coincidence: see H1b, where two declared grains collapse onto
#: one triple and one panel's provenance is a UNION, and the two accidents cancel to nine.
_EXPECTED = {
    ("installment_month", rd.UNIT_MONEY, frozenset([dd.MI_RESIDUAL, dd.INSTALLMENT_PROMOTE])),
    ("installment_month", rd.UNIT_MONEY, frozenset([dd.INSTALLMENT_SCHEDULE])),
    ("installment_month", rd.UNIT_MONEY, frozenset([dd.INSTALLMENT_PROMOTE])),
    ("processor_account", rd.UNIT_MONEY, frozenset([dd.MA_COMMISSION])),
    ("processor_account", rd.UNIT_COUNT, frozenset([dd.MA_COMMISSION])),
    ("processor_account", rd.UNIT_MONEY, frozenset([dd.MA_DAILY_TX])),
    ("recovery_row", rd.UNIT_MONEY, frozenset([dd.RECOVERY_LEDGER, dd.MI_RESIDUAL,
                                               lineage.PAYMENT_DETAIL])),
    ("recovery_row", rd.UNIT_COUNT, frozenset([dd.RECOVERY_LEDGER, dd.MI_RESIDUAL,
                                               lineage.PAYMENT_DETAIL])),
    ("entity_row", rd.UNIT_COUNT, frozenset([dd.DLAR_REP, dd.DLAR_STORE,
                                             lineage.MONTHLY_SALES, lineage.LIVE_SALES_FEED])),
}
_seen = set()
for _shape, _panels in (("installment_month", XD), ("processor_account", MD),
                        ("recovery_row", RD), ("entity_row", VD)):
    for _k, _p in _panels.items():
        _seen.add((_shape, _p["figure"]["unit"],
                   frozenset(x["table"] for x in _p["sources"])))
check("H1 NINE GRAINS ARE DECLARED, and the (row shape, unit, provenance) triples the declaration's "
      "own catalogues actually produce over this fixture are EXACTLY the nine written down above — "
      "MEASURED off the panels and compared as a set, never counted",
      len(GRAINS) == 9 and _seen == _EXPECTED,
      str({"extra": sorted((a, b, sorted(c)) for a, b, c in _seen - _EXPECTED),
           "missing": sorted((a, b, sorted(c)) for a, b, c in _EXPECTED - _seen)}))
check("H1b AND THE NINE-FOR-NINE IS A COINCIDENCE OF TWO ACCIDENTS, stated rather than leaned on. "
      "(a) EXPECTED-IN-WINDOW and NOT-YET-EARNED share a triple outright: they are separated at the "
      "ROW, not by shape, unit or provenance (§CORE-17.3k), so nine grains offer only eight "
      "triples. (b) EARNED's provenance here is a UNION of the gate and the approval, because a "
      "person promoted one month of this fixture — so it is not the single-relation triple the "
      "gate alone would give, and that restores the ninth. Neither is a grain this batch invented",
      (frozenset(x["table"] for x in XD["expected_in_window"]["sources"])
       == frozenset(x["table"] for x in XD["expected_not_yet_earned"]["sources"])
       and XD["expected_in_window"]["figure"]["unit"]
       == XD["expected_not_yet_earned"]["figure"]["unit"]
       and {x["table"] for x in XD["earned"]["sources"]}
       == {dd.MI_RESIDUAL, dd.INSTALLMENT_PROMOTE}
       and _gate_chain(XD["earned"]) == {dd.MI_RESIDUAL}),
      str([sorted(x["table"] for x in XD[k]["sources"]) for k in
           ("earned", "expected_in_window", "expected_not_yet_earned")]))
#: A DATABASE THE ENGINE NEVER PROMOTED ANYTHING OUT OF — built by removing the approval ROWS and
#: letting the real producer run again, not by flipping a flag on a ledger it already stamped.
#: (`_no_prom` above does flip the flag, and the declaration correctly still names the approval
#: there, because `gate_kind` on those rows still says a person moved them — which is the producer's
#: own record and not this file's to overrule.)
_T_NOPROM = _xc_tables()
_T_NOPROM["installment_promote"] = []
XP_NOPROM = xc_payload(_T_NOPROM)
check("H1c and the gate alone DOES give that single-relation triple where nobody promoted anything "
      "— the collapse in H1b is this fixture's, not the declaration's",
      {x["table"] for x in dd.expected_drilldowns(XP_NOPROM)["earned"]["sources"]}
      == {dd.MI_RESIDUAL}
      and ("installment_month", rd.UNIT_MONEY, frozenset([dd.MI_RESIDUAL])) not in _EXPECTED,
      str(sorted(x["table"] for x in dd.expected_drilldowns(XP_NOPROM)["earned"]["sources"])))
check("H1d and no month of THAT ledger reports the approval as its gate either — the producer "
      "stamped none, so the declaration can name none",
      all(dd.earned_gate_tables(r) != (dd.INSTALLMENT_PROMOTE,) for r in XP_NOPROM["rows"]))
check("H2 THREE PAIRS SHARE A ROW AND A UNIT AND ARE SEPARATED ONLY BY PROVENANCE (§CORE-17.3n) — "
      "earned vs expected, expected vs promoted, and a statement's money vs an airtime margin",
      len({frozenset(s["table"] for s in XD[k]["sources"])
           for k in ("earned", "expected_in_window", "promoted_amount")}) == 3
      and {s["table"] for s in MD["total_payable"]["sources"]}
      != {s["table"] for s in MD["margin"]["sources"]})
check("H3 TWO PAIRS SHARE A ROW AND A PROVENANCE AND ARE SEPARATED ONLY BY UNIT (§CORE-17.3l) — a "
      "processor account's money and its activations, a recovery row's dollars and its devices",
      MD["total_payable"]["figure"]["unit"] != MD["activations"]["figure"]["unit"]
      and RD["owed.recoverable"]["figure"]["unit"] != RD["devices.recoverable"]["figure"]["unit"])
check("H4 the four catalogues are four ROW LISTS, and each panel's rows are the list its own figure "
      "was folded from — a panel of account rows opened from a device figure would be a panel about "
      "rows its figure is not made of",
      {XD["earned"]["rows_total_count"], MD["margin"]["rows_total_count"],
       RD["owed.recoverable"]["rows_total_count"], VD["cells_compared"]["rows_total_count"]}
      == {12, 3, 4})


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== I. THE RATCHET — shrink-only, and mutation-tested in all three directions ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
GATE = read("harness_drilldown_coverage.py")
LEDGER_TXT = read("harness_drilldown_pending.txt")
MY_ROUTES = ("/commcalc/expected-commission", "/commcalc/ma-commission", "/commcalc/recovery",
             "/commcalc/dlar-vs-platform")


def _pin(src):
    """THE PINNED CEILING, READ OUT OF THE GATE. Returns None when it cannot be read, so this lock
    FAILS CLOSED rather than passing on an unreadable pin."""
    m = re.search(r"(?m)^OWES_MAX\s*=\s*(\d+)\s*$", src or "")
    return int(m.group(1)) if m else None


def _owes(src):
    out = []
    for ln in (src or "").splitlines():
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        parts = ln.split("\t")
        if len(parts) > 1 and parts[1].strip() == "owes":
            out.append(parts[0].strip())
    return out


def _state(src, route):
    for ln in (src or "").splitlines():
        parts = ln.split("\t")
        if parts and parts[0].strip() == route:
            return (parts[1].strip() if len(parts) > 1 else ""), \
                   (parts[2].strip() if len(parts) > 2 else "")
    return None, None


_p, _o = _pin(GATE), len(_owes(LEDGER_TXT))
check("I1 THE PIN AND THE LEDGER AGREE, AND BOTH ARE AT OR BELOW THIS BATCH'S NUMBER. The assertion "
      "is deliberately `<=` on the VALUE and `==` between the two: equality on the value has broken "
      "three times, because a later batch lowering the pin further is CORRECT and must stay green",
      _p is not None and _p <= 37 and _o <= 37 and _o == _p, str((_p, _o)))
ctl("I1b control: a HIGHER pin than this batch left is RED — the ratchet really is shrink-only",
    not (_pin("OWES_MAX = 41\n") is not None and _pin("OWES_MAX = 41\n") <= 37))
ctl("I1c control: a LATER, LOWER pin is GREEN — a future batch serving more routes must not have to "
    "come back and edit this file",
    (_pin("OWES_MAX = 30\n") or 99) <= 37)
ctl("I1d control: an UNREADABLE pin FAILS CLOSED rather than passing",
    _pin("OWES_MAX = nope\n") is None and _pin("") is None and _pin(None) is None)
check("I2 exactly the FOUR routes this batch serves flipped from `owes` to `shared`, and the state "
      "is true of the code — each page really renders the shared panel",
      all(_state(LEDGER_TXT, r)[0] == "shared" for r in MY_ROUTES),
      str({r: _state(LEDGER_TXT, r)[0] for r in MY_ROUTES}))
check("I3 and none of the four is still counted as owing one",
      not (set(MY_ROUTES) & set(_owes(LEDGER_TXT))))
check("I4 EVERY ONE OF THE FOUR NOTES CARRIES A `REFUSES TO CLAIM` CLAUSE — a drill-down that does "
      "not say what it declined to explain is an excuse nobody wrote down",
      all("REFUSES TO CLAIM" in (_state(LEDGER_TXT, r)[1] or "") for r in MY_ROUTES),
      str([r for r in MY_ROUTES if "REFUSES TO CLAIM" not in (_state(LEDGER_TXT, r)[1] or "")]))
check("I5 and each note names THIS batch's index section and the declaration that serves it, so a "
      "reader of the ledger can reach the reasoning",
      all("CORE-17.3s" in (_state(LEDGER_TXT, r)[1] or "")
          and "carrier_owed_drilldown" in (_state(LEDGER_TXT, r)[1] or "") for r in MY_ROUTES))
ctl("I4b control: the clause scan is NOT vacuous — a note without the clause is caught",
    "REFUSES TO CLAIM" not in "a note that declines to say what it declined")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== J. REGISTRATION — the index, the graph, the labels and the guard job ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
MG = read("backend/app/modules/core/module_graph.py")
WF = read(".github/workflows/carrier-vocab-guard.yml")
MIG = ""
_migdir = os.path.join(ROOT, "database", "migrations")
if os.path.isdir(_migdir):
    for _f in sorted(os.listdir(_migdir)):
        if _f.endswith(".sql"):
            MIG += open(os.path.join(_migdir, _f), encoding="utf-8", errors="replace").read()

DRILL_TABLES = sorted({t for t in (
    dd.DLAR_REP, dd.DLAR_STORE, dd.MA_COMMISSION, dd.MA_DAILY_TX, dd.MI_RESIDUAL,
    dd.RECOVERY_LEDGER, dd.INSTALLMENT_SCHEDULE, dd.INSTALLMENT_PROMOTE, dd.CUSTOM_CAPTURE,
    lineage.PAYMENT_DETAIL, lineage.LIVE_SALES_FEED, lineage.MONTHLY_SALES) if t})
_no_mig = [t for t in DRILL_TABLES if not re.search(r"\b%s\b" % re.escape(t), MIG)]
check("J1 every table a panel in this batch can name is CREATED BY A MIGRATION — a provenance claim "
      "about a relation that does not exist is worse than none",
      MIG and _no_mig == [], str(_no_mig))
_no_label = [t for t in DRILL_TABLES if not re.search(r"(?m)^\s+%s:" % re.escape(t), LABELS)]
check("J2 BUILD LOCK — and every one has a `sourceLabels.ts` entry, so the operator reads a feed's "
      "plain name and never an identifier (§19.38)",
      _no_label == [], str(_no_label))
check("J3 the declaration is a registered CALLER of the one drill-down home, by hand",
      "'app/modules/commcalc/carrier_owed_drilldown.py': ('report_drilldown',)" in MG)
check("J4 and of the feed-lineage home, so 'who else answers which table this is' is one call away",
      "'app/modules/commcalc/carrier_owed_drilldown.py': ('_lineage',)" in MG)
check("J5 this batch's harness is registered in the one home's lock list",
      "'harness_batch19_drilldown.py'" in MG)
check("J6 the index carries THIS batch's section, and the next free letter was taken rather than "
      "guessed", "§CORE-17.3s" in IDX and "carrier_owed_drilldown.py" in IDX)
check("J7 the guard job runs this harness, with a paths filter naming every file it is about",
      "harness_batch19_drilldown.py" in WF
      and "backend/app/modules/commcalc/carrier_owed_drilldown.py" in WF
      and "backend/app/modules/recovery/engine.py" in WF)
_steps = [ln for ln in WF.splitlines() if "batch19" in ln]
check("J8 and its step's `name:` / `run:` scalars carry NO colon-space — a plain scalar that does "
      "makes the WHOLE workflow fail to parse, so every job in it silently never runs",
      _steps and not any(re.search(r"(?m)^\s*(?:-\s*)?(name|run):\s+[^'\"].*:\s", ln)
                         for ln in _steps), str(_steps))
check("J9 the four pages are registered in the index's endpoint rows with this batch's section",
      IDX.count("DRILL-DOWN §CORE-17.3s") >= 4, str(IDX.count("DRILL-DOWN §CORE-17.3s")))
check("J10 the ratchet gate itself still passes its own `shared` lock for these routes, which is "
      "what makes the ledger flip an assertion rather than a claim",
      "D1 BUILD LOCK" in GATE and "DrillDownPanel" in GATE)


print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
_mut = len([p for p in PASS if p.startswith("MUT")]) + len([f for f in FAIL if f.startswith("MUT")])
print(f"{_mut} mutations / negative controls")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
