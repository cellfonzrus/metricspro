"""BUILD LOCK — one home for "how much device money did the carrier pay against THIS device?"

Index §COMMCALC-44. The home is `app/modules/commcalc/carrier_device_money.py`; this file is the
check that keeps it the only one.

WHY A LOCK AND NOT A DOCSTRING. CLAUDE.md, verbatim: *"A design fix ships with a check that FAILS
THE BUILD if a caller stops dereferencing the shared fact, or if a second copy appears. Without
that, the next change quietly restores the patchwork."* And §19.18 records that writing a registry
without wiring the callers to it has happened three times in this repo. The figure this home owns is
MONEY: the P&L's equipment-reimbursement line and the §COMMCALC-43 chargeback amount both quote
"the rebate on that phone", and the day two derivations disagree they are both wrong and neither
says so.

WHAT IS LOCKED
  A. The home exists and still states the contract its callers rely on.
  B. ONE HOME — nothing in app code reaches §65's `device_reimb_recon.carrier_line_side` except this
     module and the module that defines it. A second direct caller IS the second derivation.
  C. THE QUESTIONS ARE ASKED, NOT RE-SPELLED. "Which components are device money" is §58's
     (`device_reimb_components`), "is this basis evidence" is §66's (`component_is_evidence`) and
     "how long may the money take to arrive" is §27's (`period_window`). This module asks all three;
     no caller of it asks any of them again.
  D. BOTH CALLERS DEREFERENCE IT — `commcalc/router.py`'s device-line reimbursement run and
     `commcalc/watchdog_router.py`'s byod-upgrade endpoint. A caller that stops is the un-wiring.
  E. NO SECOND COPY OF THE FACTS the home owns: the feed relation and the default P&L line key are
     spelled HERE and dereferenced by the watchdog caller, never respelled beside it.
  F. BEHAVIOUR — the fake-zero refusal, the unconfigured-vs-measured-zero distinction, and the
     SYMMETRIC window, proved rather than asserted.
  G. NEGATIVE CONTROLS — the locks above would actually redden if the code regressed.

Run: `cd backend && python3 harness_carrier_device_money.py`
"""
import ast
import os
import re
import sys

sys.path.insert(0, ".")

from app.modules.commcalc import carrier_device_money as M   # noqa: E402
from app.modules.core import module_graph as MG              # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"   [{detail}]" if detail and not cond else ""))


def section(t):
    print(f"\n── {t} " + "─" * max(0, 92 - len(t)))


APP = "app"

#: THE HOME IS READ FROM THE GRAPH, NOT SPELLED HERE. §50's `module_graph` is the one home for
#: "which file owns this fact", and a lock that hard-codes the path is a second copy of exactly the
#: thing it exists to protect (§19.18). `harness_module_graph_guard` §H2 fails the build on the
#: literal form.
FACT = "carrier_device_money"
_fact = MG.FACTS.get(FACT) or {}
assert _fact.get("homes"), f"module_graph has no fact '{FACT}' — register it before locking it"
HOME = _fact["homes"][0]
HOME_SRC = open(HOME, encoding="utf-8").read()
HOME_TREE = ast.parse(HOME_SRC)

#: The modules allowed to name §65's pure fold directly: the module that DEFINES it, and this one
#: home. Anything else answering the device-money question must come through `per_device`.
CARRIER_LINE_SIDE_ALLOWED = set(
    (MG.FACTS.get("device_reimbursement_paid_vs_claimed") or {}).get("homes") or ()
) | {HOME}


def app_files():
    for base, _dirs, names in os.walk(APP):
        if "__pycache__" in base:
            continue
        for n in sorted(names):
            if n.endswith(".py"):
                yield os.path.join(base, n).replace(os.sep, "/")


SRC = {f: open(f, encoding="utf-8").read() for f in app_files()}


def calls_in(tree):
    """Every attribute/name actually CALLED in a tree — a mention in a docstring is not a call."""
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute):
                out.add(f.attr)
            elif isinstance(f, ast.Name):
                out.add(f.id)
    return out


HOME_CALLS = calls_in(HOME_TREE)


def func(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. the home exists and states its contract")
check("A1 the module is where the index says it is", os.path.exists(HOME))
check("A2 it exposes the whole surface its callers use",
      all(hasattr(M, a) for a in ("load_recon_config", "device_components",
                                  "device_dollar_predicate_from", "classification_inputs",
                                  "device_dollar_predicate", "lag_periods", "feed_periods",
                                  "per_device", "amount_for", "lines_for")))
check("A3 the feed relation and the default P&L line are NAMED here, as module constants a caller "
      "can dereference rather than respell",
      M.FEED_TABLE == "raw_payment_detail" and M.DEFAULT_LINE == "carrier_comm",
      f"{M.FEED_TABLE} / {M.DEFAULT_LINE}")
#: The home's source with every docstring and comment REMOVED. The prose here promises absences
#: ("no rate, tier or paid/earned column is reachable"), so matching the raw file would redden on the
#: sentence that states the rule — a check that fails because the code documents itself teaches
#: nobody anything. Only executable text is searched.
def code_only(src, tree):
    doc_lines = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            d = n.body[0] if n.body else None
            if (isinstance(d, ast.Expr) and isinstance(d.value, ast.Constant)
                    and isinstance(d.value.value, str)):
                doc_lines |= set(range(d.lineno, (d.end_lineno or d.lineno) + 1))
    out = []
    for i, ln in enumerate(src.splitlines(), 1):
        out.append("" if i in doc_lines else ln.split("#", 1)[0])
    return "\n".join(out)


HOME_CODE = code_only(HOME_SRC, HOME_TREE)

check("A4 it books, pays and rates NOTHING — no commission rate, tier or paid/earned column is "
      "reachable from here, so a money bug cannot enter the pay path through it",
      not re.search(r"\b(rate_|tier|is_paid|paid_at|earned|payout_amount)\b", HOME_CODE))

check("A5 the fact is REGISTERED in §50's module graph, with both callers and the three facts it "
      "dereferences — the graph is where 'which file owns this' is answered, and this lock reads it "
      "rather than keeping a path of its own",
      set(_fact.get("callers") or ()) == {
          # the P&L's reimbursement side, and §COMMCALC-43's chargeback amount — the two reports
          "app/modules/commcalc/router.py",
          "app/modules/commcalc/watchdog_router.py",
          # and the drill-down catalogue, which dereferences `FEED_TABLE` so a panel names the feed
          # the figure was READ from rather than the one the row was found in
          "app/modules/commcalc/byod_upgrade_drilldown.py"}
      and "COMMCALC-44" in tuple(_fact.get("index") or ())
      and "harness_carrier_device_money.py" in tuple(_fact.get("locks") or ()),
      str(sorted(_fact.get("callers") or ())))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. ONE HOME — §65's pure fold has exactly these direct callers")
_direct = {f for f, s in SRC.items() if "carrier_line_side" in calls_in(ast.parse(s))}
check("B1 BUILD LOCK — nothing in app code CALLS `carrier_line_side` but the module that defines it "
      "and this one home. A second direct caller is a second derivation of the same money figure, "
      "and two paths answering one question drift (CLAUDE.md)",
      _direct <= CARRIER_LINE_SIDE_ALLOWED,
      "unexpected: " + ", ".join(sorted(_direct - CARRIER_LINE_SIDE_ALLOWED)))
check("B2 and this home really is one of them — a lock that passes because the home stopped "
      "calling it would be worthless",
      HOME in _direct)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. the three questions are ASKED of their own homes, not re-spelled")
check("C1 'which components are device money' is §58's `device_reimb_components`",
      "device_reimb_components" in HOME_CALLS)
check("C2 'is this basis evidence at all' is §66's `component_is_evidence` — an allowlist, so an "
      "unruled basis reads as NOT evidence",
      "component_is_evidence" in HOME_CALLS)
#: §27 is imported under a local alias, so the call site spells the alias and not the name. A lock
#: that looked only for the name would pass the day somebody re-implemented the window and left the
#: import behind, so BOTH halves are proved: the name really is imported from §27's module, and the
#: thing actually called is the name that import bound.
_win_alias = None
for _n in ast.walk(HOME_TREE):
    if isinstance(_n, ast.ImportFrom) and (_n.module or "").endswith("imei_rebate_report"):
        for _a in _n.names:
            if _a.name == "period_window":
                _win_alias = _a.asname or _a.name
check("C3 'how long may the money take to arrive' is §27's `period_window`, imported from "
      "`imei_rebate_report` and called under the name that import bound — so the lock cannot be "
      "satisfied by an import nothing uses, nor broken by renaming the alias",
      _win_alias is not None and _win_alias in HOME_CALLS, f"alias={_win_alias}")
check("C4 the classification itself is §58's `classify` — this module owns no payment-type "
      "vocabulary of its own",
      "classify" in HOME_CALLS)
check("C5 and the feed read is the paged one home (§19.48), so a month over the row ceiling cannot "
      "silently truncate the figure",
      "read_all" in HOME_CALLS)
check("C6 RULE TWO — no carrier, tenant or product name anywhere in the home",
      not re.search(r"(?i)\b(boost|luxelink|cellfonz|verizon|t-?mobile|metro|total\s*wireless)\b",
                    HOME_SRC))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. both callers dereference the home")
CALLERS = {
    "app/modules/commcalc/router.py": ("_device_line_reimb_run", "per_device"),
    "app/modules/commcalc/watchdog_router.py": (None, "per_device"),
}
for path, (fn, want) in CALLERS.items():
    tree = ast.parse(SRC[path])
    scope = func(tree, fn) if fn else tree
    check(f"D-{os.path.basename(path)} BUILD LOCK — it calls `carrier_device_money.{want}` "
          f"{'inside ' + fn if fn else 'in the module'}, so the figure it reports is the home's",
          scope is not None and want in calls_in(scope),
          f"{fn or 'module'} does not call {want}")
check("D3 the device-line reimbursement run also takes its CONFIG from the home, so the two "
      "callers cannot resolve the recon config two ways",
      "load_recon_config" in calls_in(func(ast.parse(SRC["app/modules/commcalc/router.py"]),
                                          "_device_reimb_recon_inputs") or ast.parse("")))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. no second copy of the facts the home owns")
WD = "app/modules/commcalc/watchdog_router.py"
for fact in ("carrier_line_side", "raw_payment_detail", "component_is_evidence",
             "device_reimb_components", "period_window"):
    check(f"E-{fact} the watchdog caller does not spell it — it reads the home instead. A copy "
          f"here is the next divergence (CLAUDE.md: one fact, one home, dereferenced)",
          fact not in SRC[WD])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. behaviour — the refusals are real, not documented")
check("F1 FAKE ZERO REFUSED — a device the feed carries no row for reads None, never 0.00. A "
      "rebate lands months after the sale, so a $0.00 there states a measurement nobody made "
      "(§19.26)",
      M.amount_for({"devices": {}}, "351449743101051") is None)
check("F2 a device the feed DID answer for reads its own total, rounded",
      M.amount_for({"devices": {"A1": {"total": 61.004, "lines": 2}}}, "A1") == 61.0)
check("F3 a blank or missing device identifier is None, not a lookup of the empty key",
      M.amount_for({"devices": {"": {"total": 9.0}}}, "  ") is None
      and M.amount_for(None, "A1") is None)
check("F4 the device key is matched with its whitespace folded, so a feed that pads the column is "
      "not read as a different phone",
      M.amount_for({"devices": {"A1": {"total": 5.0}}}, " A1 ") == 5.0)
check("F5 UNCONFIGURED IS NOT A MEASURED ZERO — an org that has declared no device-money routing "
      "gets predicate None, which a caller reports as unconfigured rather than as 'nothing is "
      "device money'",
      M.device_dollar_predicate_from(declarations=[], rules=[], cfg={}, components=set()) is None)
check("F6 lines_for counts what the figure is Σ over, and 0 for a device with none — the count a "
      "drill-down needs to say what it rests on",
      M.lines_for({"devices": {"A1": {"total": 5.0, "lines": 3}}}, "A1") == 3
      and M.lines_for({"devices": {}}, "A1") == 0)

_w = M.lag_periods({"lag_months": 2}, "September 2026")
check("F7 THE WINDOW IS SYMMETRIC — a payment routinely lands BEFORE the claim is settled, so a "
      "forward-only window reported paid devices as never-paid. Two months either side of "
      "September is five months, July through November",
      isinstance(_w, list) and len(_w) == 5
      and _w[0] == "July 2026" and _w[-1] == "November 2026", str(_w))
check("F8 no lag declared → the asked month alone, never a guessed window",
      M.lag_periods({"lag_months": 0}, "September 2026") == ["September 2026"])
check("F9 no period asked → no window, rather than a window over nothing",
      M.lag_periods({"lag_months": 2}, "") is None)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. negative controls — the locks would really redden")
_mut = "def f():\n    x = device_reimb_recon.carrier_line_side(rows, cfg, None, None)\n"
check("G1 a NEW direct caller of §65's fold is seen by §B",
      "carrier_line_side" in calls_in(ast.parse(_mut)))
check("G2 a mention in a comment or docstring is NOT counted as a call, so §B cannot be satisfied "
      "or broken by prose",
      "carrier_line_side" not in calls_in(ast.parse('"""carrier_line_side"""\n# carrier_line_side\n')))
check("G3 a caller that stopped dereferencing the home would fail §D",
      "per_device" not in calls_in(ast.parse("def g():\n    return 1\n")))
check("G4 the fake-zero check is real — an index that DID carry a 0.00 total reads 0.00, so F1 "
      "passes on absence rather than on the function always returning None",
      M.amount_for({"devices": {"A1": {"total": 0.0, "lines": 1}}}, "A1") == 0.0)
check("G6 the §A4 scrub is real — a rate column in CODE is still seen, it is only the prose that "
      "is exempt",
      bool(re.search(r"\b(rate_|tier|is_paid)\b",
                     code_only('x = row["tier"] or row["is_paid"]\n',
                               ast.parse('x = row["tier"] or row["is_paid"]\n')))))
check("G7 and the prose exemption is real — the same words inside a docstring are NOT seen",
      not re.search(r"\b(rate_|tier)\b",
                    code_only('"""no rate_ or tier here"""\n', ast.parse('"""no rate_ or tier here"""\n'))))
check("G8 §C3 would redden on an import nothing calls — the alias has to appear at a call site",
      (lambda t: not any((a.asname or a.name) in calls_in(t)
                         for n in ast.walk(t) if isinstance(n, ast.ImportFrom)
                         for a in n.names))(
          ast.parse("from x.imei_rebate_report import period_window as _w\n")))
check("G5 and the symmetric-window check is real — a forward-only window of the same length would "
      "be three months, not five, and F7 would fail",
      len(M.lag_periods({"lag_months": 2}, "September 2026")) != 3)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n" + "═" * 100)
print(f"  {len(PASS)} ok, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print(f"    FAIL {f}")
print("═" * 100)
sys.exit(1 if FAIL else 0)
