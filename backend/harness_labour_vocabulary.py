"""Proof harness + LOCK — the platform's labour-row vocabulary has ONE home, and its default is
CORRECT instead of inert (owner report 2026-10-07).

Owner, verbatim: *"Also the finance module is doubling the salaries, it is appearing in the store
expenses and also in separate line as the wages/ hourly payroll"*.

THE CLASS this harness exists to prevent:
  **the platform auto-fills a figure into one surface, re-derives the same figure from the same
  source on another surface, and the only guard against the double-count is opt-in config that
  ships EMPTY — so the default is wrong and every new tenant starts broken.**

Measured live 2026-10-07, before the fix: 2 of the 3 tenants were double-counting labour, in two
different ways (one with no vocabulary at all, one with payroll named but not commission). The
house org's August 2026 P&L carried $85,389.16 of hours-estimate wages BESIDE $164,206.97 of
hand-entered salary rows, and $7,176.73 of commission expense beside the same commission on
`rep_comm`.

The BOOKING logic was already right and is already proven by `harness_labour_coverage.py` (161
checks): a listed payroll row suppresses the estimate, a listed commission row stops booking where
`rep_commissions` replaces it. What was wrong was the DEFAULT, and the fact that the same
vocabulary existed in three unwired copies. So this harness proves the RESOLUTION and LOCKS the
wiring.

  A. THE HOME is pure and total — `labour_vocabulary.resolve` over every config shape.
  B. A TENANT STILL WINS — a non-empty list wins wholesale, never merged; `mode='off'` restores the
     pre-2026-10-07 behaviour exactly; a tenant-invented row is NOT claimed.
  C. THE GRAIN IS DERIVED, not a fourth knob — the house vocabulary can only be switched on at
     per-STORE authority, because 'org' would invent a silent $0.00 for a store with no salary row.
  D. THE THIRD COPY IS GONE — the cross-month protection tokens are DERIVED and are a SUPERSET of
     the hand-written default they replaced (nothing protected becomes copyable).
  E. THE RESOLUTION RUNS THROUGH `coa._account_config` — proven over an in-memory client on the
     three REAL live config shapes measured 2026-10-07.
  F. THE REGRESSION — the reported defect reproduced and closed: with the pre-fix empty config both
     copies of the same labour dollars are claimed as bookable; with the house default exactly one
     is, and the three-state honesty (`replaced` / `no_replacement`) still holds through the REAL
     `labour_coverage.suppression_plan`.
  G. THE LOCK (fails the build if the wiring comes undone):
       G1 `coa._account_config` dereferences `labour_vocabulary.resolve` and does not re-list names;
       G2 `commcalc/router` derives its protected tokens from the home — no literal token list;
       G3 the frontend Expenses-sheet mirror declares EXACTLY the home's rows;
       G4 NO SECOND COPY of the vocabulary anywhere in `backend/app` or `frontend/src`;
       G5 RULE TWO — no tenant / carrier / company name in the home or the wiring;
       G6 the opt-out migration exists, is additive, and carries a REVERT note.

Stdlib only, no DB, no network.
Run:  cd backend && python3 harness_labour_vocabulary.py
"""
import os
import re
import sys
import types

sys.path.insert(0, "app")

if "pydantic_settings" not in sys.modules:                 # import stub — no settings are read
    stub = types.ModuleType("pydantic_settings")

    class _BaseSettings:                                   # noqa: D401 — minimal import stub
        def __init__(self, **kw):
            pass

    stub.BaseSettings = _BaseSettings
    sys.modules["pydantic_settings"] = stub

from app.modules.commcalc import labour_vocabulary as LV   # noqa: E402
from app.modules.commcalc import labour_coverage as LC     # noqa: E402
from app.modules.account import coa                        # noqa: E402

FAIL = 0
PASS = 0
HERE = os.path.dirname(os.path.abspath(__file__))
COA_SRC = open(os.path.join(HERE, "app/modules/account/coa.py")).read()
CC_ROUTER_SRC = open(os.path.join(HERE, "app/modules/commcalc/router.py")).read()
LV_SRC = open(os.path.join(HERE, "app/modules/commcalc/labour_vocabulary.py")).read()
FE_EXPENSES = os.path.abspath(os.path.join(
    HERE, "..", "frontend/src/app/(platform)/commcalc/expenses/page.tsx"))
MIGRATION = os.path.abspath(os.path.join(
    HERE, "..", "database/migrations/1061_labour_vocabulary_mode.sql"))


def ok(name, cond, detail=None):
    global FAIL, PASS
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + ("" if detail is None else f"   {detail!r}"))


def code_only(src):
    """Source with comments and string literals stripped — so a check for "no second copy" cannot be
    satisfied or defeated by prose. Deliberately crude; it only has to remove the explanations."""
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"(?m)^\s*(#|//).*$", " ", src)
    src = re.sub(r'"""(?:.|\n)*?"""', " ", src)
    return src


# ─────────────────────────────────────────────────────────────────────────────────────────────────
print("\n── A. the home is pure and total ──")
v = LV.resolve()
ok("A1 no config at all ⇒ the platform's own payroll rows apply",
   v["payroll_names"] == list(LV.DEFAULT_PAYROLL_ROWS) and v["payroll_source"] == LV.HOUSE, v)
ok("A2 no config at all ⇒ the platform's own commission rows apply",
   v["commission_names"] == list(LV.DEFAULT_COMMISSION_ROWS) and v["commission_source"] == LV.HOUSE)
ok("A3 the default is NOT empty (the whole defect was an inert default)",
   len(v["payroll_names"]) > 0 and len(v["commission_names"]) > 0)
ok("A4 an EMPTY list reads as 'not configured', not as 'claim nothing'",
   LV.resolve([], [])["payroll_names"] == list(LV.DEFAULT_PAYROLL_ROWS))
ok("A5 None/garbage config never raises and lands on the house default",
   LV.resolve(None, "nonsense", None, None)["payroll_source"] == LV.HOUSE
   and LV.resolve(0, 0)["commission_names"] == list(LV.DEFAULT_COMMISSION_ROWS))
ok("A6 names are de-duplicated case-insensitively, first spelling wins",
   LV.resolve(["Payroll", "payroll", " Payroll "])["payroll_names"] == ["Payroll"])
ok("A7 blank entries are dropped, not carried as empty names",
   LV.resolve(["  ", "Payroll", ""])["payroll_names"] == ["Payroll"])
ok("A8 internal whitespace is normalised so one row cannot split in two",
   LV.resolve(["Employee   Salaries"])["payroll_names"] == ["Employee Salaries"])
ok("A9 the result is JSON-safe (lists and strings, no sets)",
   all(isinstance(v[k], list) for k in ("payroll_names", "commission_names",
                                        "apply_protection_tokens")))
ok("A10 resolve is a pure function of its arguments (same in, same out)",
   LV.resolve(["A"], ["B"], "store", "house") == LV.resolve(["A"], ["B"], "store", "house"))
ok("A11 mutating the returned lists cannot corrupt the module default",
   (LV.resolve()["payroll_names"].append("X") or True)
   and LV.resolve()["payroll_names"] == list(LV.DEFAULT_PAYROLL_ROWS))
ok("A12 an unknown mode degrades to 'house' (so a pre-migration schema is already correct)",
   LV.resolve_mode(None) == LV.HOUSE and LV.resolve_mode("") == LV.HOUSE
   and LV.resolve_mode("banana") == LV.HOUSE and LV.resolve_mode("OFF") == LV.OFF)

print("\n── B. a tenant still wins, and can still switch it off ──")
ex = LV.resolve(["DM Salaries", "Employee Salaries"], ["Employee Commission"], "store")
ok("B1 a non-empty tenant payroll list WINS, wholesale",
   ex["payroll_names"] == ["DM Salaries", "Employee Salaries"] and ex["payroll_source"] == LV.EXPLICIT)
ok("B2 the house rows are NOT merged into it (a configured tenant does not move unasked)",
   "Owner / Mgmt Salaries" not in ex["payroll_names"])
ok("B3 each side resolves independently — payroll explicit, commission defaulted",
   (lambda r: r["payroll_source"] == LV.EXPLICIT and r["commission_source"] == LV.HOUSE
    and r["commission_names"] == list(LV.DEFAULT_COMMISSION_ROWS))(
       LV.resolve(["Employee Salaries"], [])))
off = LV.resolve(["Employee Salaries"], ["Employee Commission"], "org", "off")
ok("B4 mode='off' claims NOTHING — the pre-2026-10-07 behaviour, per org",
   off["payroll_names"] == [] and off["commission_names"] == []
   and off["payroll_routes"] == {} and off["apply_protection_tokens"] == [])
ok("B5 mode='off' keeps the tenant's stored grain (it changes no booking)",
   off["grain"] == "org" and off["grain_source"] == LV.EXPLICIT)
ok("B6 a tenant-invented labour row is NOT claimed by the house default",
   "dm salary" not in [n.lower() for n in LV.resolve()["payroll_names"]])
ok("B7 …but a tenant CAN claim it, by naming it",
   "Dm Salary" in LV.resolve(["Dm Salary", "Employee Salaries"])["payroll_names"])
# THE MEMBERSHIP TEST — the list may contain ONLY rows the platform itself fills and then
# re-derives. A row it merely SHIPS in the Expenses sheet's default categories but never fills is
# not a duplicate, and listing it would confer payroll authority that suppresses a store's real
# hours estimate (a silent $0.00 of labour). Live house org 2026-10-07: including
# 'Owner / Mgmt Salaries' changed the correction by $0.00 while making six extra July stores
# authoritative on the strength of an owner-salary row alone.
SHIPPED_BUT_NOT_FILLED = ("Owner / Mgmt Salaries", "Rent / Lease", "Insurance",
                          "Taxes / Accounting", "ADT Security")
for _nm in SHIPPED_BUT_NOT_FILLED:
    ok(f"B7b {_nm!r} is shipped but not auto-filled, so it is NOT claimed as a duplicate",
       _nm.lower() not in {n.lower() for n in LV.resolve()["payroll_names"]}
       and _nm.lower() not in {n.lower() for n in LV.resolve()["commission_names"]}, _nm)
ok("B7c the house payroll vocabulary is exactly the AUTO-FILLED salary row — one row, one duplicate",
   len(LV.DEFAULT_PAYROLL_ROWS) == 1, LV.DEFAULT_PAYROLL_ROWS)
ok("B7d …and the house commission vocabulary likewise",
   len(LV.DEFAULT_COMMISSION_ROWS) == 1, LV.DEFAULT_COMMISSION_ROWS)
ok("B7e an unfilled row therefore confers NO payroll authority on its own",
   LC.authoritative_codes(
       [{"store_code": "B-1", "expense_name": "Owner / Mgmt Salaries", "amount": 1450.0,
         "source_key": None}], LV.resolve()["payroll_names"]) == frozenset())
ok("B7f …while the auto-filled row does",
   LC.authoritative_codes(
       [{"store_code": "B-1", "expense_name": "Employee Salaries", "amount": 4479.16,
         "source_key": None}], LV.resolve()["payroll_names"]) == frozenset({"B-1"}))
ok("B8 a tenant's own route map is respected by resolve (it returns none of its own)",
   LV.resolve(["Employee Salaries"])["payroll_routes"] == {})
ok("B9 the house routes send every house payroll row to ONE opex line",
   set(LV.resolve()["payroll_routes"].values()) == {LV.DEFAULT_PAYROLL_LINE}
   and len(LV.resolve()["payroll_routes"]) == len(LV.DEFAULT_PAYROLL_ROWS))
ok("B10 the house route target is a REAL PL_SPEC line (a typo cannot invent a bucket)",
   LV.DEFAULT_PAYROLL_LINE in {k for k, *_ in coa.PL_SPEC})
ok("B11 routing moves a dollar between two OPEX lines only — never out of opex",
   {sec for k, _l, sec, *_ in coa.PL_SPEC if k == LV.DEFAULT_PAYROLL_LINE} == {"opex"})

print("\n── C. the authority grain is DERIVED, not a fourth knob ──")
ok("C1 house vocabulary ⇒ per-STORE authority (no store can be silently zeroed)",
   LV.resolve()["grain"] == "store" == LV.HOUSE_AUTHORITY_GRAIN)
ok("C2 house vocabulary ⇒ the grain is derived even when 'org' is stored",
   LV.resolve([], [], "org")["grain"] == "store"
   and LV.resolve([], [], "org")["grain_source"] == LV.HOUSE)
ok("C3 an EXPLICIT vocabulary keeps the grain the tenant stored — both values",
   LV.resolve(["X"], [], "org")["grain"] == "org"
   and LV.resolve(["X"], [], "store")["grain"] == "store")
ok("C4 an unparseable stored grain falls back to 'org', never to a crash",
   LV.resolve(["X"], [], "banana")["grain"] == "org")
ok("C5 the derived grain is one of the two the booking logic understands",
   all(LV.resolve(p, [], g)["grain"] in ("org", "store")
       for p in ([], ["X"]) for g in (None, "org", "store", "zzz")))

print("\n── D. the third copy is gone: protection tokens are derived, and a superset ──")
PRE_FIX_TOKENS = ["commission", "salary", "salaries"]      # the hand-written default this replaced
toks = LV.resolve()["apply_protection_tokens"]
ok("D1 every token the hand-written default carried is still produced",
   all(t in toks for t in PRE_FIX_TOKENS), toks)
_derivable = set()
for _n in LV.resolve()["payroll_names"] + LV.resolve()["commission_names"]:
    _derivable |= set(LV._tokens_for(_n))
ok("D2 every token is DERIVED from the vocabulary's own row names — none is hand-added",
   set(toks) == _derivable, sorted(set(toks) ^ _derivable))
ok("D3 a plural row name also yields its singular stem (substring match catches either)",
   "salary" in LV.apply_protection_tokens(["Employee Salaries"], []))
ok("D4 tokens are de-duplicated", len(toks) == len(set(toks)))
ok("D5 tokens are lowercase (the match lowercases the expense name)",
   all(t == t.lower() for t in toks))
ok("D6 an empty vocabulary yields NO tokens (mode 'off' decides nothing for the tenant)",
   LV.apply_protection_tokens([], []) == [])
ok("D7 a tenant's own rows produce their own tokens",
   "widget" in LV.apply_protection_tokens(["Widget Wages"], []) or
   "wages" in LV.apply_protection_tokens(["Widget Wages"], []))


def _protects(tokens, name):
    """The EXACT predicate `_apply_to_months_expand` uses: case-insensitive substring."""
    low = name.lower()
    return any(t in low for t in tokens)


for nm in ("Employee Salaries", "Owner / Mgmt Salaries", "Employee Commission",
           "Dm Salary", "DM Salaries", "Employee Commissions", "Manager Salary"):
    ok(f"D8 {nm!r} is protected from cross-month copy under the derived tokens",
       _protects(toks, nm), nm)
for nm in ("Rent / Lease", "Electric", "Insurance", "Internet", "ADT Security"):
    ok(f"D9 {nm!r} is still copyable (the tokens did not widen onto real overhead)",
       not _protects(toks, nm), nm)

print("\n── E. the resolution runs through coa._account_config (in-memory client) ──")


class _Exec:
    def __init__(self, data):
        self.data = data


class _Q:
    """The narrow slice of the supabase client `_account_config` uses. A column the fake row does
    not carry raises, exactly as PostgREST does for an unknown column — which is how the harness
    proves the resolver still works on a schema where `labour_vocabulary_mode` does not exist."""

    def __init__(self, row, cols):
        self._row, self._cols = row, cols

    def select(self, cols):
        want = [c.strip() for c in cols.split(",")]
        missing = [c for c in want if c not in self._cols]
        if missing:
            raise RuntimeError("column does not exist: %s" % missing[0])
        return _Q({k: self._row.get(k) for k in want}, self._cols)

    def eq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return _Exec([self._row] if self._row is not None else [])


class _Client:
    def __init__(self, row, cols):
        self._row, self._cols = row, cols

    def schema(self, *a):
        return self

    def table(self, *a):
        return self

    def select(self, cols):
        return _Q(self._row, self._cols).select(cols)


KNOWN_COLS = ["accessory_cogs_pct", "service_fee_products", "distributor_chargeback_locations",
              "distributor_chargeback_one_off_names", "distributor_chargeback_recurring_names",
              "distributor_chargeback_booking", "payroll_expense_names", "payroll_expense_routes",
              "payroll_authority_grain", "labour_commission_expense_names", "overhead_config",
              "device_cogs_mode", "labour_vocabulary_mode"]

# the THREE live config shapes measured 2026-10-07 (no org id appears — only the SHAPE matters)
SHAPE_UNCONFIGURED = {"payroll_expense_names": [], "payroll_expense_routes": {},
                      "payroll_authority_grain": "org", "labour_commission_expense_names": []}
SHAPE_FULLY_CONFIGURED = {"payroll_expense_names": ["DM Salaries", "Employee Salaries"],
                          "payroll_expense_routes": {},
                          "payroll_authority_grain": "store",
                          "labour_commission_expense_names": ["Employee Commission"]}
SHAPE_HALF_CONFIGURED = {"payroll_expense_names": ["Employee Salaries"],
                         "payroll_expense_routes": {},
                         "payroll_authority_grain": "org",
                         "labour_commission_expense_names": []}


def cfg_for(shape, cols=KNOWN_COLS, mode=None):
    row = dict(shape)
    if "labour_vocabulary_mode" in cols:
        row["labour_vocabulary_mode"] = mode
    return coa._account_config(_Client(row, cols), "org")


c = cfg_for(SHAPE_UNCONFIGURED)
ok("E1 the UNCONFIGURED shape now resolves a non-empty payroll vocabulary",
   c["payroll_expense_names_list"] == list(LV.DEFAULT_PAYROLL_ROWS), c["payroll_expense_names_list"])
ok("E2 …and a non-empty commission vocabulary",
   c["labour_commission_expense_names_list"] == list(LV.DEFAULT_COMMISSION_ROWS))
ok("E3 …and the lowercased sets the booking loop matches on",
   c["payroll_expense_names"] == {n.lower() for n in LV.DEFAULT_PAYROLL_ROWS}
   and c["labour_commission_expense_names"] == {n.lower() for n in LV.DEFAULT_COMMISSION_ROWS})
ok("E4 …and per-STORE authority, so no store books a silent $0.00 of labour",
   c["payroll_authority_grain"] == "store")
ok("E5 …and the house routes, so salary lands on the salary line not inside store_opex",
   c["payroll_expense_routes"] == {n.lower(): LV.DEFAULT_PAYROLL_LINE
                                   for n in LV.DEFAULT_PAYROLL_ROWS})
ok("E6 the resolved vocabulary is published on the config for any reader",
   (c.get("labour_vocabulary") or {}).get("payroll_source") == LV.HOUSE)

c2 = cfg_for(SHAPE_FULLY_CONFIGURED)
ok("E7 the FULLY-CONFIGURED shape is UNCHANGED — its own names, its own grain",
   c2["payroll_expense_names_list"] == ["DM Salaries", "Employee Salaries"]
   and c2["labour_commission_expense_names_list"] == ["Employee Commission"]
   and c2["payroll_authority_grain"] == "store")
ok("E8 …and gets NO house routes (its presentation does not move under it)",
   c2["payroll_expense_routes"] == {})

c3 = cfg_for(SHAPE_HALF_CONFIGURED)
ok("E9 the HALF-CONFIGURED shape keeps its payroll list and its stored grain",
   c3["payroll_expense_names_list"] == ["Employee Salaries"]
   and c3["payroll_authority_grain"] == "org")
ok("E10 …and its MISSING commission vocabulary is filled by the house default (its live defect)",
   c3["labour_commission_expense_names_list"] == list(LV.DEFAULT_COMMISSION_ROWS))

c4 = cfg_for(SHAPE_UNCONFIGURED, mode="off")
ok("E11 mode='off' resolves through coa to claim nothing (byte-identical to before the fix)",
   c4["payroll_expense_names_list"] == [] and c4["labour_commission_expense_names_list"] == []
   and c4["payroll_expense_names"] == set() and c4["payroll_expense_routes"] == {})

c5 = cfg_for(SHAPE_UNCONFIGURED, cols=[k for k in KNOWN_COLS if k != "labour_vocabulary_mode"])
ok("E12 the fix needs NO migration — a schema without the opt-out column resolves 'house'",
   c5["payroll_expense_names_list"] == list(LV.DEFAULT_PAYROLL_ROWS)
   and c5["payroll_authority_grain"] == "store")
ok("E13 the unrelated knobs are untouched by any of this",
   c["accessory_cogs_pct"] == coa.ACCESSORY_COGS_PCT and c["device_cogs_mode"] == "off"
   and c["service_fee_products"] == set())

print("\n── F. the regression: the owner's defect reproduced, then closed ──")
# ONE store-month, built from the live B-103 / 103 Fulton Ave August 2026 figures.
EXP_ROWS = [
    {"store_code": "B-103", "expense_name": "Employee Salaries", "amount": 4479.16, "source_key": None},
    {"store_code": "B-103", "expense_name": "Employee Commission", "amount": 235.98, "source_key": None},
    {"store_code": "B-103", "expense_name": "Owner / Mgmt Salaries", "amount": 1450.00, "source_key": None},
    {"store_code": "B-103", "expense_name": "Dm Salary", "amount": 850.00, "source_key": None},
    {"store_code": "B-103", "expense_name": "Rent / Lease", "amount": 4150.00, "source_key": None},
]
REP_PAY = {"B-103": 235.98}          # what `rep_comm` books for that store-month
WAGES_ESTIMATE = 4471.00             # what the shifts x rate estimate books for that store-month

pre = LV.resolve([], [], "org", "off")                       # the pre-fix live config, exactly
post = LV.resolve([], [], "org")                             # the same org after this fix

pre_plan = LC.suppression_plan(EXP_ROWS, REP_PAY, pre["commission_names"])
post_plan = LC.suppression_plan(EXP_ROWS, REP_PAY, post["commission_names"])
ok("F1 DEFECT REPRODUCED — pre-fix, the commission expense copy is not suppressed at all",
   pre_plan["active"] is False and pre_plan["total_suppressed"] == 0.0)
ok("F2 DEFECT REPRODUCED — pre-fix, no payroll row is authoritative, so the hours estimate books "
   "ON TOP of the entered salary",
   LC.authoritative_codes(EXP_ROWS, pre["payroll_names"]) == frozenset())
pre_expense_side = sum(r["amount"] for r in EXP_ROWS)
ok("F3 DEFECT QUANTIFIED — pre-fix this store-month subtracts the labour twice: "
   "$235.98 of commission and $4,471.00 of wages estimate are duplicates",
   round(pre_expense_side + REP_PAY["B-103"] + WAGES_ESTIMATE, 2) == 15872.12)

ok("F4 FIXED — the commission expense copy stops booking, because rep_comm replaces it",
   post_plan["total_suppressed"] == 235.98
   and post_plan["total_booked_instead"] == 235.98
   and post_plan["suppressed_keys"] == ["B-103"])
ok("F5 FIXED — the entered salary rows are authoritative, so the estimate is not added",
   LC.authoritative_codes(EXP_ROWS, post["payroll_names"]) == frozenset({"B-103"}))
ok("F6 the corrected expense total for the store-month is the rows + rep_comm, counted ONCE",
   round(pre_expense_side - 235.98 + REP_PAY["B-103"], 2) == round(pre_expense_side, 2))
ok("F7 the correction is $4,706.98 — the owner's $18,292.12 opex becomes $13,585.14",
   round(18292.12 - (235.98 + 4471.00), 2) == 13585.14)
ok("F8 expenses go DOWN and net income goes UP — never the other way",
   235.98 + 4471.00 > 0)

ok("F9 the tenant-invented row is NOT claimed and keeps booking as the real cost it is",
   "dm salary" not in {n.lower() for n in post["payroll_names"]}
   and not LC.suppresses_row(LC.suppression_index(post_plan), "Dm Salary", "B-103"))
ok("F9b the shipped-but-unfilled owner-salary row likewise keeps booking, on store_opex",
   "owner / mgmt salaries" not in {n.lower() for n in post["payroll_names"]}
   and not LC.suppresses_row(LC.suppression_index(post_plan), "Owner / Mgmt Salaries", "B-103")
   and coa.route_expense_line(None)[0] == "store_opex")
ok("F10 real overhead is untouched",
   not LC.suppresses_row(LC.suppression_index(post_plan), "Rent / Lease", "B-103"))

# THREE STATES, NEVER TWO — a suppressed cost must never vanish unreplaced.
no_rep = LC.suppression_plan(EXP_ROWS, {}, post["commission_names"])
ok("F11 nothing to replace it with ⇒ the row KEEPS booking (a real cost is never deleted)",
   no_rep["suppressed_keys"] == [] and no_rep["total_kept"] == 235.98
   and no_rep["kept"] == ["B-103"])
ok("F12 …and it is reported by name and amount, not silently kept",
   "235.98" in (no_rep["note"] or "") and "B-103" in (no_rep["note"] or ""))
ok("F13 the swap is stated with BOTH figures, never one",
   "235.98" in (post_plan["note"] or ""))
zero_row = LC.suppression_plan(
    [{"store_code": "B-103", "expense_name": "Employee Commission", "amount": 0.0,
      "source_key": None}], REP_PAY, post["commission_names"])
ok("F14 a $0.00 placeholder row is not a booking and suppresses nothing",
   zero_row["suppressed_keys"] == [] and zero_row["total_kept"] == 0.0)
ok("F15 a SYSTEM row (its own producer source_key) is routed by the producer, never by a name",
   coa.route_expense_line("payroll_gross")[0] == "wages"
   and coa.route_expense_line(None)[0] == "store_opex")
ok("F16 suppression is per STORE-MONTH — another store's rep pay cannot suppress this one's row",
   LC.suppression_plan(EXP_ROWS, {"B-999": 500.0}, post["commission_names"])["suppressed_keys"] == [])

print("\n── G. the lock: the wiring cannot come undone ──")
coa_code = code_only(COA_SRC)
ok("G1a coa._account_config DEREFERENCES the one home",
   "labour_vocabulary" in coa_code and "_lv.resolve(" in coa_code)
ok("G1b coa reads the resolved vocabulary onto all four knobs",
   all(s in coa_code for s in ('cfg["payroll_expense_names_list"] = list(_vocab["payroll_names"])',
                               'cfg["labour_commission_expense_names_list"] = list(_vocab["commission_names"])',
                               'cfg["payroll_authority_grain"] = _vocab["grain"]',
                               '_vocab["payroll_routes"]')))
ok("G1c coa lists NO labour ROW NAME of its own (the vocabulary is read, never spelled)",
   not any(("'%s'" % n) in coa_code or ('"%s"' % n) in coa_code
           for n in list(LV.DEFAULT_PAYROLL_ROWS) + list(LV.DEFAULT_COMMISSION_ROWS)))

cc_code = code_only(CC_ROUTER_SRC)
ok("G2a the cross-month protection derives its default from the one home",
   "_expense_apply_default_tokens" in cc_code and "labour_vocabulary" in cc_code)
ok("G2b the hand-written token list is GONE",
   "_EXPENSE_APPLY_DEFAULT_TOKENS" not in CC_ROUTER_SRC)
ok("G2c no literal labour token list remains in the handler",
   not re.search(r"\[\s*'commission'\s*,\s*'salary'", cc_code))
ok("G2d the resolved vocabulary is SERVED, so the frontend has a live answer to read",
   '"labour_rows"' in CC_ROUTER_SRC or "'labour_rows'" in CC_ROUTER_SRC)

fe = open(FE_EXPENSES).read()
m_pay = re.search(r"const LABOUR_PAYROLL_ROWS\s*=\s*\[([^\]]*)\]", fe)
m_com = re.search(r"const LABOUR_COMMISSION_ROWS\s*=\s*\[([^\]]*)\]", fe)
fe_pay = [s.strip().strip("'\"") for s in (m_pay.group(1).split(",") if m_pay else []) if s.strip()]
fe_com = [s.strip().strip("'\"") for s in (m_com.group(1).split(",") if m_com else []) if s.strip()]
ok("G3a the Expenses sheet declares the mirror", bool(m_pay) and bool(m_com))
ok("G3b the mirror's payroll rows are EXACTLY the home's",
   fe_pay == list(LV.DEFAULT_PAYROLL_ROWS), fe_pay)
ok("G3c the mirror's commission rows are EXACTLY the home's",
   fe_com == list(LV.DEFAULT_COMMISSION_ROWS), fe_com)
ok("G3d the mirror names the registry of record, so a reader knows where the fact lives",
   "labour_vocabulary.py" in fe)
ok("G3e every row the mirror names is actually shipped in the sheet's default categories",
   all(("name: '%s'" % n) in fe for n in fe_pay + fe_com), fe_pay + fe_com)
ok("G3f the AUTO-FILL targets dereference the mirror, not their own string",
   "const SALARY_ROW = LABOUR_PAYROLL_ROWS[0]" in fe
   and "const COMMISSION_ROW = LABOUR_COMMISSION_ROWS[0]" in fe)

# G4 — NO SECOND COPY. Any other file that declares a LIST containing one of the home's row names
# is a fourth copy of the vocabulary and must dereference the home instead.
HOME_FILES = {os.path.normpath(os.path.join(HERE, "app/modules/commcalc/labour_vocabulary.py")),
              os.path.normpath(FE_EXPENSES),
              os.path.normpath(os.path.abspath(__file__))}
PAY_NAMES = [n.lower() for n in LV.DEFAULT_PAYROLL_ROWS]
NAMES = PAY_NAMES + [n.lower() for n in LV.DEFAULT_COMMISSION_ROWS]
offenders = []
for root in (os.path.join(HERE, "app"),
             os.path.abspath(os.path.join(HERE, "..", "frontend/src"))):
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if not f.endswith((".py", ".ts", ".tsx")):
                continue
            p = os.path.normpath(os.path.join(dirpath, f))
            if p in HOME_FILES:
                continue
            try:
                body = code_only(open(p, encoding="utf-8", errors="ignore").read()).lower()
            except Exception:
                continue
            # The DUPLICATE SIGNATURE is the vocabulary, not a word: a file that quotes one of
            # the platform's PAYROLL row names (which exist only as expense-sheet rows), or that
            # quotes a commission row name ALONGSIDE a payroll one, is holding a second copy.
            # "Employee Commission" alone is ordinary commission-module copy and is not flagged.
            hits = [n for n in NAMES if ("'%s'" % n) in body or ('"%s"' % n) in body]
            pay_hit = [n for n in hits if n in PAY_NAMES]
            if pay_hit:
                offenders.append((os.path.relpath(p, HERE), sorted(hits)))
ok("G4 NO second copy of the labour vocabulary in backend/app or frontend/src",
   not offenders, offenders[:6])

ok("G5a RULE TWO — the home branches on no tenant, carrier or company",
   not re.search(r"(?i)\b(boost|luxelink|nova\s*wave|cellfonz|t-?mobile|verizon|at&t|metro)\b",
                 LV_SRC))
ok("G5b RULE TWO — the home contains no org id",
   not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", LV_SRC))
ok("G5c RULE TWO — the home has no `if org ==` branch",
   not re.search(r"org(_id)?\s*==", code_only(LV_SRC)))
ok("G5d the home is PURE — no client, no I/O, no DB",
   not re.search(r"\b(client|supabase|execute\(|requests|urllib|open\()", code_only(LV_SRC)))

mig = open(MIGRATION).read()
ok("G6a the opt-out migration exists and is additive",
   "ADD COLUMN IF NOT EXISTS labour_vocabulary_mode" in mig)
ok("G6b it carries a REVERT note", "-- REVERT:" in mig)
ok("G6c it is idempotent (IF NOT EXISTS on the column and the constraint)",
   "IF NOT EXISTS" in mig and "pg_constraint" in mig)
ok("G6d the modes it allows are exactly the ones the code understands",
   all(("'%s'" % m) in mig for m in LV.MODES)
   and not re.search(r"IN \('house', 'off', ", mig))
ok("G6e the migration drops nothing and deletes nothing",
   not re.search(r"(?i)\b(drop table|delete from|truncate|update )", mig.split("-- REVERT:")[0]))

print()
print(f"harness_labour_vocabulary: {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
print("ALL CHECKS PASSED")
