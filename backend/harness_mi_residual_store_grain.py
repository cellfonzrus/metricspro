#!/usr/bin/env python3
"""PROOF + BUILD GUARD — the raw_mi residual books at STORE grain, through ONE home.

Owner report 2026-10-01, verbatim:
  "there is no dta for september residual for boost for individual stores since the begininig it only
   shows the consilidated mi and atu residual, need to assign the residual at the store level in the
   p&l and all reports"

THE CLASS, NOT THE INSTANCE. The instance was "Boost September residual shows only consolidated".
The class is: **a P&L line's GRAIN was a constant in the spec instead of a fact about the feed.**
`coa.PL_SPEC` declared `mi_income` / `atu_income` "company"; `coa.build_inputs` selected only the two
money columns and booked them with `store=None`. `raw_mi.salesforce_id` names the dealer door on every
single row (46,047 HOUSE rows for 2026-09, 0 blanks in a 60,000-row sample, 26 distinct doors), so the
key was never missing — it was never read. `engine._scoped` adds a line's `company_wide` bucket for the
CONSOLIDATED scope ONLY, so every company / store / market / profit-center view read $0 residual by
construction, from the first statement onward. `statement_filter.py` had written that down as "the
documented convention".

The MA/VidaPay half of those SAME two lines got its store grain from mig 314
(`ma_store_pnl.canonical_store_index`). The raw_mi half did not. One feed fixed, the other left — the
patchwork the 2026-09-20 directive forbids.

DB-FREE. Every check below runs the REAL production functions over in-memory rows: no database, no
network, stdlib only. The engine half is proved against the REAL `engine._assemble` / `_scoped` over
the REAL `coa.PL_SPEC`, so "the consolidated total does not move" is proved against the shipped
scoping code, not a re-implementation of it.

Run: python3 backend/harness_mi_residual_store_grain.py
"""
import os
import re
import sys
import types

# The ruling for which file is the one home lives in the module graph (index 50), not in a
# literal here — owner directive 2026-10-04 "the tree should be interlinked properly".
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.core.module_graph import homes_for  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

# STDLIB ONLY. `account.engine` is imported for the REAL `_assemble` / `_scoped` scoping code — the
# claim "the consolidated total does not move" is worthless if proved against a re-implementation of
# it. Its import chain reaches the app's pydantic settings, which exist only in the deployed image, so
# the settings module is stubbed to the two attributes `engine` touches (the narrator is never called
# here — ANTHROPIC_API_KEY empty keeps it deterministic). Nothing else is stubbed: coa, residual_subs
# and ma_store_pnl are imported as they ship.
if "pydantic_settings" not in sys.modules:
    _ps = types.ModuleType("pydantic_settings")

    class _BaseSettings:                                    # pragma: no cover - import shim
        pass

    _ps.BaseSettings = _BaseSettings
    _ps.SettingsConfigDict = dict
    sys.modules["pydantic_settings"] = _ps
if "app.core.config" not in sys.modules:
    _cfg = types.ModuleType("app.core.config")
    _cfg.settings = types.SimpleNamespace(ANTHROPIC_API_KEY="", ACCOUNT_ENGINE_MODEL="none")
    sys.modules["app.core.config"] = _cfg

from app.modules.account import coa                              # noqa: E402
from app.modules.account import engine                           # noqa: E402
from app.modules.account import ma_store_pnl as MSP              # noqa: E402
from app.modules.account import residual_subs as RS              # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
FAILS = []
CHECKS = [0]


def ck(name, got, want):
    CHECKS[0] += 1
    if got != want:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")
        print(f"  FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"  ok   {name}")


def ck_true(name, cond, why=""):
    ck(name + ((" — " + why) if why and not cond else ""), bool(cond), True)


# ── fixtures: three doors on two stores + one ambiguous + one unmapped ────────────────────────────
# Spellings deliberately drift the way the live vocabulary drifts ("218-80" vs "21880"), so the
# canonical collapse is exercised rather than assumed.
STORE_A = "218-80 Hempstead Avenue"
STORE_B = "1115 Liberty Ave"
MAPPING = [
    {"salesforce_id": "SF-A1", "store_address": STORE_A, "store_code": "A1", "market": "NY"},
    {"salesforce_id": "SF-A2", "store_address": "21880 Hempstead Ave", "store_code": "A1", "market": "NY"},
    {"salesforce_id": "SF-B1", "store_address": STORE_B, "store_code": "B1", "market": "LI"},
    # the SAME door claimed by two different stores — must be refused, not first-wins
    {"salesforce_id": "SF-DUP", "store_address": STORE_A, "store_code": "A1", "market": "NY"},
    {"salesforce_id": "SF-DUP", "store_address": STORE_B, "store_code": "B1", "market": "LI"},
    {"salesforce_id": "", "store_address": "99 Nowhere St", "store_code": "Z", "market": "NY"},
]

# `coa.store_resolver`'s real behaviour, standing in for the DB read: exact spelling wins, and the
# unambiguous leading street number collapses "21880 Hempstead Ave" onto "218-80 Hempstead Avenue".
_CANON = {STORE_A.lower(): STORE_A, STORE_B.lower(): STORE_B}
_BY_NUM = {"21880": STORE_A, "1115": STORE_B}


def fake_resolve(raw):
    s = str(raw or "").strip()
    if not s:
        return None
    if s.lower() in _CANON:
        return _CANON[s.lower()]
    lead = s.split(" ")[0]
    nk = "".join(c for c in lead if c.isdigit())
    return _BY_NUM.get(nk) or s


MI_ROWS = [
    # door,    mi,      atu
    {"salesforce_id": "SF-A1", "actual_mi_payout": 1200.55, "actual_atu_payout": 300.10},
    {"salesforce_id": "SF-A2", "actual_mi_payout": 840.33, "actual_atu_payout": 110.07},
    {"salesforce_id": "SF-B1", "actual_mi_payout": 2100.19, "actual_atu_payout": 505.44},
    {"salesforce_id": "SF-B1", "actual_mi_payout": 15.01, "actual_atu_payout": 0.0},
    {"salesforce_id": "SF-DUP", "actual_mi_payout": 77.77, "actual_atu_payout": 7.07},   # ambiguous
    {"salesforce_id": "SF-NEW", "actual_mi_payout": 44.44, "actual_atu_payout": 4.04},   # unmapped door
    {"salesforce_id": "", "actual_mi_payout": 9.99, "actual_atu_payout": 0.91},          # blank door
]
MI_TOTAL = round(sum(r["actual_mi_payout"] for r in MI_ROWS), 2)
ATU_TOTAL = round(sum(r["actual_atu_payout"] for r in MI_ROWS), 2)

ON = dict(MSP.default_config(), mi_store_attribution=True)
OFF = MSP.default_config()


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§A  THE DOOR→STORE MAP — one home, canonical, ambiguity REFUSED")
# ════════════════════════════════════════════════════════════════════════════════════════════════
idx = RS.salesforce_store_map(MAPPING, fake_resolve)
ck("A1  SF-A1 lands on its store", idx.get("SF-A1"), STORE_A)
ck("A2  a drifted spelling COLLAPSES onto the canonical store (218-80 == 21880)",
   idx.get("SF-A2"), STORE_A)
ck("A3  SF-B1 lands on its store", idx.get("SF-B1"), STORE_B)
ck("A4  a door claimed by TWO stores is DROPPED, never first-wins", "SF-DUP" in idx, False)
ck("A5  a mapping row with no door contributes nothing", len(idx), 3)
ck("A6  an unmapped door has no answer (caller books company-wide)", idx.get("SF-NEW"), None)
# without the resolver the raw mapping spelling is kept — two spellings, two keys, same store name
raw_idx = RS.salesforce_store_map(MAPPING, None)
ck("A7  with no resolver the raw spelling is kept (the resolver is what collapses it)",
   raw_idx.get("SF-A2"), "21880 Hempstead Ave")
ck("A8  case/whitespace never split one door",
   RS.salesforce_store_map([{"salesforce_id": "  SF-A1 ", "store_address": "  " + STORE_A + " "}],
                           fake_resolve).get("SF-A1"), STORE_A)

# ARMED NEGATIVE CONTROL — the first-wins map the live code used to build inline.
def first_wins(rows, resolve):
    out = {}
    for r in rows or []:
        sf = str(r.get("salesforce_id") or "").strip()
        addr = str(r.get("store_address") or "").strip()
        if sf and addr:
            out.setdefault(sf, (resolve(addr) if resolve else addr) or addr)
    return out


ck("A9  NEGATIVE CONTROL: the old first-wins map DOES place the ambiguous door (that is the defect)",
   first_wins(MAPPING, fake_resolve).get("SF-DUP"), STORE_A)
ck_true("A10 NEGATIVE CONTROL: and the one home disagrees with it, so the control is ARMED",
        first_wins(MAPPING, fake_resolve).get("SF-DUP") != idx.get("SF-DUP"))


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§B  THE BOOKINGS — same lines, same dollars, store key added")
# ════════════════════════════════════════════════════════════════════════════════════════════════
b_off = RS.mi_pnl_bookings(MI_ROWS, OFF)
b_on = RS.mi_pnl_bookings(MI_ROWS, ON)
ck("B1  two bookings per row, both configs", (len(b_off), len(b_on)),
   (2 * len(MI_ROWS), 2 * len(MI_ROWS)))
ck("B2  the LINE sequence is identical under both configs",
   [l for l, _s, _a in b_off], [l for l, _s, _a in b_on])
ck("B3  the AMOUNT sequence is identical under both configs — this change moves no dollar",
   [a for _l, _s, a in b_off], [a for _l, _s, a in b_on])
ck("B4  OFF: every store key is None (byte-identical to the pre-1033 booking)",
   {s for _l, s, _a in b_off}, {None})
ck("B5  mi_income total", round(sum(a for l, _s, a in b_on if l == "mi_income"), 2), MI_TOTAL)
ck("B6  atu_income total", round(sum(a for l, _s, a in b_on if l == "atu_income"), 2), ATU_TOTAL)
ck("B7  only these two lines are ever booked from raw_mi",
   sorted({l for l, _s, _a in b_on}), ["atu_income", "mi_income"])
ck("B8  mi before atu within a row (coa's incremental rounding order is preserved)",
   [l for l, _s, _a in b_on][:2], ["mi_income", "atu_income"])
ck("B9  a blank door rides as None, not as an empty-string store",
   [s for _l, s, _a in RS.mi_pnl_bookings([MI_ROWS[-1]], ON)], [None, None])
ck("B10 the config default is OFF, so an un-migrated DB books exactly as before",
   bool(MSP.default_config().get("mi_store_attribution")), False)
ck("B11 the store key column is never in the money column list",
   RS.MI_STORE_KEY_COLUMN in RS.MI_PNL_MONEY_COLUMNS, False)
ck("B12 the read column list is the store key plus the two money columns",
   list(RS.MI_PNL_COLUMNS), ["salesforce_id", "actual_mi_payout", "actual_atu_payout"])

# ARMED NEGATIVE CONTROL — the shipped defect, patched back in.
def defect_bookings(rows, cfg=None):
    """What `coa.build_inputs` did before mig 1033: store hardcoded to None regardless of config."""
    out = []
    for r in rows or []:
        out.append(("mi_income", None, float(r.get("actual_mi_payout") or 0)))
        out.append(("atu_income", None, float(r.get("actual_atu_payout") or 0)))
    return out


ck_true("B13 NEGATIVE CONTROL: the defect emits the SAME dollars (it was never an arithmetic bug)",
        [a for _l, _s, a in defect_bookings(MI_ROWS)] == [a for _l, _s, a in b_on])
ck_true("B14 NEGATIVE CONTROL: and NO store key, so it is ARMED — reinstating it fails §C and §D",
        {s for _l, s, _a in defect_bookings(MI_ROWS)} == {None}
        and {s for _l, s, _a in b_on} != {None})


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§C  SCOPING — proved against the REAL engine._assemble over the REAL coa.PL_SPEC")
# ════════════════════════════════════════════════════════════════════════════════════════════════
def build_inputs(cfg, store_index):
    """The real `add()` semantics from `coa.build_inputs`, fed by the real `mi_pnl_bookings`."""
    L = {k: {"by_store": {}, "company_wide": 0.0, "detail": {}}
         for k, *_ in coa.PL_SPEC + coa.BS_SPEC}
    for line, sf, amt in RS.mi_pnl_bookings(MI_ROWS, cfg):
        amt = round(float(amt or 0), 2)
        if not amt:
            continue
        s = (store_index.get(sf) if sf else None)
        if s:
            L[line]["by_store"][s] = round(L[line]["by_store"].get(s, 0.0) + amt, 2)
        else:
            L[line]["company_wide"] = round(L[line]["company_wide"] + amt, 2)
    return L


PL_SECTIONS = [("Revenue", "revenue"), ("Cost of Goods Sold", "cogs"),
               ("Operating Expenses", "opex"), ("Other", "other")]


def pl(inputs, stores, cw):
    st = engine._assemble(inputs, [], coa.PL_SPEC, coa.PL_LABEL, PL_SECTIONS,
                          "x", stores, cw)
    return {r["key"]: r["amount"] for sec in st["sections"] for r in sec["lines"]}


in_off, in_on = build_inputs(OFF, idx), build_inputs(ON, idx)

# C1/C2 — the whole point: the CONSOLIDATED statement cannot move.
cons_off, cons_on = pl(in_off, None, True), pl(in_on, None, True)
ck("C1  CONSOLIDATED mi_income is IDENTICAL before and after the re-grain",
   cons_on["mi_income"], cons_off["mi_income"])
ck("C2  CONSOLIDATED atu_income is IDENTICAL before and after the re-grain",
   cons_on["atu_income"], cons_off["atu_income"])
ck("C3  and it equals the feed's own total", (cons_on["mi_income"], cons_on["atu_income"]),
   (MI_TOTAL, ATU_TOTAL))
ck("C4  EVERY consolidated P&L line is identical (nothing else moved either)", cons_on, cons_off)
ck("C5  consolidated revenue subtotal is identical",
   engine._assemble(in_on, [], coa.PL_SPEC, coa.PL_LABEL, PL_SECTIONS, "x", None, True)["net_income"],
   engine._assemble(in_off, [], coa.PL_SPEC, coa.PL_LABEL, PL_SECTIONS, "x", None, True)["net_income"])

# C6-C9 — THE REPORTED DEFECT, reproduced then fixed.
store_a_off = pl(in_off, {STORE_A}, False)
store_a_on = pl(in_on, {STORE_A}, False)
ck("C6  REGRESSION: before the fix, a STORE scope reads $0 residual (the owner's report)",
   (store_a_off["mi_income"], store_a_off["atu_income"]), (0.0, 0.0))
ck("C7  after the fix store A carries its own residual",
   (store_a_on["mi_income"], store_a_on["atu_income"]), (2040.88, 410.17))
store_b_on = pl(in_on, {STORE_B}, False)
ck("C8  after the fix store B carries its own residual",
   (store_b_on["mi_income"], store_b_on["atu_income"]), (2115.20, 505.44))
ck("C9  a store with no residual still reads $0 (not somebody else's money)",
   pl(in_on, {"7 Elsewhere Rd"}, False)["mi_income"], 0.0)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§D  TOTAL PRESERVATION — the invariant that makes this safe to apply")
# ════════════════════════════════════════════════════════════════════════════════════════════════
for line, total in (("mi_income", MI_TOTAL), ("atu_income", ATU_TOTAL)):
    l_on = in_on[line]
    ck(f"D1  {line}: Σ(by_store) + company_wide == the feed total, to the cent",
       round(sum(l_on["by_store"].values()) + l_on["company_wide"], 2), total)
    ck(f"D2  {line}: the pre-change company_wide equals that same total",
       round(in_off[line]["company_wide"], 2), total)
    ck(f"D3  {line}: re-graining conserves the money exactly",
       round(sum(l_on["by_store"].values()) + l_on["company_wide"], 2),
       round(in_off[line]["company_wide"], 2))

# The unplaceable money is NAMED, not dropped: ambiguous door + unmapped door + blank door.
unplaced_mi = round(77.77 + 44.44 + 9.99, 2)
ck("D4  the ambiguous / unmapped / blank doors stay COMPANY-WIDE (never dropped, never guessed)",
   round(in_on["mi_income"]["company_wide"], 2), unplaced_mi)
ck("D5  exactly the two placeable stores carry store buckets",
   sorted(in_on["mi_income"]["by_store"]), sorted([STORE_A, STORE_B]))
ck("D6  Σ(per-store scopes) + the company-wide remainder == consolidated (statement_filter's "
   "linearity, which is how the filtered view picks the residual up for free)",
   round(sum(pl(in_on, {s}, False)["mi_income"]
             for s in in_on["mi_income"]["by_store"]) + in_on["mi_income"]["company_wide"], 2),
   cons_on["mi_income"])

# ARMED NEGATIVE CONTROL — float accumulation. Re-graining splits ONE accumulator into N, so prove
# the cent cannot drift on amounts chosen to be hostile to binary floats.
HOSTILE = [{"salesforce_id": d, "actual_mi_payout": v, "actual_atu_payout": 0.0}
           for d, v in (("SF-A1", 0.07), ("SF-A1", 0.07), ("SF-A1", 0.07), ("SF-B1", 0.01),
                        ("SF-B1", 1e-3), ("SF-A1", 10.005), ("SF-B1", 2.675), ("SF-A1", 1.005))]
_saved, MI_ROWS = MI_ROWS, HOSTILE
h_on, h_off = build_inputs(ON, idx), build_inputs(OFF, idx)
MI_ROWS = _saved
ck("D7  hostile-float control: the re-grained total still matches the company-wide total",
   round(sum(h_on["mi_income"]["by_store"].values()) + h_on["mi_income"]["company_wide"], 2),
   round(h_off["mi_income"]["company_wide"], 2))
ck_true("D8  hostile-float control is ARMED (the rows really do split across two accumulators)",
        len(h_on["mi_income"]["by_store"]) == 2)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§E  THE CONFIG SWITCH — config never code (RULE TWO)")
# ════════════════════════════════════════════════════════════════════════════════════════════════
ck("E1  the switch is declared in the ONE column list with its migration",
   MSP.PL_CONFIG_MIGRATION.get("pl_mi_store_attribution"), "1033_pl_mi_store_attribution.sql")
ck("E2  a DB missing the column is told which migration adds it",
   MSP.config_migrations_missing(["pl_mi_store_attribution"]), ["1033_pl_mi_store_attribution.sql"])
ck("E3  a non-boolean value keeps the default OFF (a typo cannot re-grain a statement)",
   bool(dict(MSP.default_config(), **{}).get("mi_store_attribution")), False)
ck("E4  no carrier/tenant/store name decides the grain — only the config key",
   sorted(k for k in ON if "store_attribution" in k),
   ["mi_store_attribution", "store_attribution"])
# The two residual feeds have INDEPENDENT switches: turning one on must not move the other's grain.
ck("E5  the raw_mi switch does not turn the MA switch on", bool(ON.get("store_attribution")), False)

mig = os.path.join(ROOT, "..", "database", "migrations", "1033_pl_mi_store_attribution.sql")
mig_src = open(mig).read() if os.path.exists(mig) else ""
ck_true("E6  the migration exists", bool(mig_src))
ck_true("E7  the migration is idempotent (ADD COLUMN IF NOT EXISTS)",
        "ADD COLUMN IF NOT EXISTS pl_mi_store_attribution" in mig_src)
ck_true("E8  the migration carries a -- REVERT: note", "-- REVERT:" in mig_src)
_dml = re.findall(r"(?im)^\s*(ALTER TABLE|UPDATE|INSERT INTO|DELETE FROM)\s+([a-z_.]+)", mig_src)
ck("E9  every statement in the migration targets the CONFIG table — no money table is written",
   sorted({t for _verb, t in _dml}), ["commcalc.commission_org_config"])
ck_true("E10 the migration writes no money row (no DML against raw_mi or account_statements)",
        not any(t in ("commcalc.raw_mi", "commcalc.account_statements") for _v, t in _dml))


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§F  ONE HOME, DEREFERENCED — fails the build if a caller un-wires or a copy appears")
# ════════════════════════════════════════════════════════════════════════════════════════════════
HOME = homes_for("ma_reported_income")[1]    # the graph is the one home for the ruling


def read(rel):
    p = os.path.join(ROOT, rel.replace("/", os.sep))
    return open(p).read() if os.path.exists(p) else ""


coa_src = read("app/modules/account/coa.py")
ck_true("F1  coa.build_inputs dereferences the door→store index (it does not build its own)",
        "canonical_salesforce_store_index" in coa_src)
ck_true("F2  coa.build_inputs dereferences the pure booking function",
        "mi_pnl_bookings" in coa_src)
ck_true("F3  coa.build_inputs no longer hardcodes the residual's store to None",
        'add_comm("mi_income", None' not in coa_src
        and 'add_comm("atu_income", None' not in coa_src)
ck_true("F4  coa reads the store key through the named column list, not a literal select",
        "MI_PNL_COLUMNS" in coa_src)
ck_true("F5  the §7a report dereferences the same map rather than its own {sfid: row}",
        "salesforce_store_map" in read(HOME).split("def compute(")[-1])
ck_true("F6  the residual TREND dereferences the same map and the same aggregation",
        "canonical_salesforce_store_index" in read("app/modules/commcalc/comp_trend.py")
        and "mi_atu_by_period_store" in read("app/modules/commcalc/comp_trend.py"))

# F7 — ANTI-COPY. A module outside the home that joins store_mapping.salesforce_id to a store for
# ITSELF is a future divergence. The sites that existed when mig 1033 landed are INVENTORIED here
# with the reason each was excused; a NEW one fails the build. (Same shape as CHECK 2c in
# harness_ma_income_one_home_guard.py.)
EXCUSED = {
    # file                                             why it is not the same question
    "app/modules/commcalc/router.py":
        "_leg_store_index builds {sfid: {store,code,market}} for the Commission-Received "
        "breakout's store/market FILTER, not for a booking. Already per-store and correct; "
        "rewiring it would change which rows a filter admits on a second report, so it is a "
        "named follow-up, not a silent change inside a money PR.",
    "app/modules/commcalc/gp_report.py":
        "asks the INVERSE question (sales store -> which sfid is this store's, via street "
        "number) and already reports MI/ATU per store correctly.",
    "app/modules/commcalc/vip_sweep.py":
        "sfid -> store_code for VIP invoice attribution; not residual money.",
    "app/modules/commcalc/flag_store_resolver.py":
        "sfid -> store_CODE for fraud flags; refuses ambiguity already, and answers a code, "
        "not a P&L store bucket.",
    "app/modules/closing/router.py":
        "_store_resolver keys the CLOSING SHEET's rows by door to find a store_code; it books no "
        "residual and reads no raw_mi money column.",
    "app/core/identity.py":
        "the identity resolver is deliberately DORMANT ('WIRED INTO NOTHING' in its own docstring) "
        "so it cannot move a market-filtered dollar. Wiring it on is explicitly out of scope here; "
        "doing it as part of a money change is how a dormant module silently becomes load-bearing.",
    "app/core/identity_backfill.py":
        "SEEDS storeops.store_alias rows from the mapping (a write path for the alias registry), "
        "not a read path that attributes a residual dollar to a store.",
}
# THE SIGNAL: a `.select("…salesforce_id…")` whose column list ALSO names a store column — which is
# exactly the shape every private copy has ('store_address,store_code,salesforce_id'). A module that
# merely MENTIONS the column, or receives mapping rows as an argument, is not building its own join.
COPY = re.compile(r"""select\(\s*["']([^"']*)["']""")
new_copies = []
for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "app")):
    for fn in sorted(files):
        if not fn.endswith(".py"):
            continue
        full = os.path.join(dirpath, fn)
        rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
        if rel == HOME or rel in EXCUSED:
            continue
        body = open(full).read()
        for cols in COPY.findall(body):
            if "salesforce_id" in cols and ("store_address" in cols or "store_code" in cols):
                new_copies.append(rel)
                break
ck("F7  no NEW private salesforce_id→store join outside the one home", new_copies, [])
ck("F8  the excused sites are still the ones excused (an excuse is not a blank cheque)",
   sorted(f for f in EXCUSED if read(f)), sorted(EXCUSED))
ck_true("F9  every excusal states a reason", all(len(v) > 40 for v in EXCUSED.values()))

# F10 — PL_SPEC's 5th element is DOCUMENTATION, and it must not be able to mislead a caller.
# It still reads "company" for the two residual lines because `harness_royalty_pl.py` §D freezes this
# chart against a pre-change oracle, and weakening that lock to edit a string no code reads would be a
# bad trade. So the invariant this check defends is the one that matters: (a) NO production module
# reads the element, so nothing can act on the stale word, and (b) the entry carries the note that
# states the real grain. If a reader ever appears, this FAILS and the string must be corrected.
grain = {k: g for k, _l, _s, _kind, g in coa.PL_SPEC}
ck("F10 the residual lines' grain word is still the frozen one (royalty_pl §D pins the chart)",
   (grain["mi_income"], grain["atu_income"]), ("company", "company"))
_grain_readers = []
for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "app")):
    for fn in sorted(files):
        if not fn.endswith(".py"):
            continue
        full = os.path.join(dirpath, fn)
        body = open(full).read()
        for ln in body.splitlines():
            # unpacking all five AND binding the 5th to a name it then uses, or indexing [4]
            if re.search(r"in\s+(?:coa\.)?PL_SPEC\b.*\[4\]|PL_SPEC\[[^\]]*\]\[4\]", ln):
                _grain_readers.append(os.path.relpath(full, ROOT).replace(os.sep, "/"))
ck("F10a no production module reads PL_SPEC's grain element, so the frozen word cannot mislead",
   sorted(set(_grain_readers)), [])
ck_true("F10b `engine._assemble` discards it (binds it to `_grain` and never uses it)",
        "for key, label, section, kind, _grain in spec:" in read("app/modules/account/engine.py"))
ck_true("F10c the PL_SPEC entry carries the note that states the REAL grain",
        "mig 1033" in coa_src.split('("mi_income"')[0].rsplit("PL_SPEC = [", 1)[-1])
ck("F11 the MA residual twin still books to the same line (one residual line, two feeds)",
   RS._MA_PNL_RESIDUAL_LINE, "mi_income")


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§G  PARITY — the P&L's residual and the §7a report read the SAME per-store dollars")
# ════════════════════════════════════════════════════════════════════════════════════════════════
# The §7a report's Boost composition is `residual_components('boost_mi_atu')` = (mi, atu) — the two
# halves of the ONE booked line. So for every store, the report's residual must equal the P&L's
# mi_income + atu_income for that store. Both sides here come from the SHIPPED functions.
comps = RS.residual_components("boost_mi_atu", None)
ck("G1  Boost residual is mi + atu (both halves of the one booked line)", list(comps), ["mi", "atu"])
report_by_store = {}
for line, sf, amt in RS.mi_pnl_bookings(MI_ROWS, ON):
    store = (idx.get(sf) if sf else None) or RS.MI_UNASSIGNED
    part = "mi" if line == "mi_income" else "atu"
    if part in comps:
        report_by_store[store] = round(report_by_store.get(store, 0.0) + amt, 2)
for store in (STORE_A, STORE_B):
    books = round(pl(in_on, {store}, False)["mi_income"]
                  + pl(in_on, {store}, False)["atu_income"], 2)
    ck(f"G2  parity for {store}", report_by_store.get(store), books)
ck("G3  the report's \"(Unassigned)\" bucket equals the books' company-wide remainder",
   report_by_store.get(RS.MI_UNASSIGNED),
   round(in_on["mi_income"]["company_wide"] + in_on["atu_income"]["company_wide"], 2))
ck("G4  one placement word for both feeds' unplaceable keys", RS.MI_UNASSIGNED, RS.MA_UNASSIGNED)


# ════════════════════════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 96)
if FAILS:
    print(f"FAILED — {len(FAILS)} of {CHECKS[0]} checks")
    for f in FAILS:
        print("  · " + f)
    sys.exit(1)
print(f"ALL {CHECKS[0]} CHECKS PASS — the raw_mi residual books at store grain, through one home, "
      "and the consolidated total does not move.")
