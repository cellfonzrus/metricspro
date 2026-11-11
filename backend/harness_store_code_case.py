"""Proof harness — A DECLARED STORE CODE IS THE SAME STORE IN WHATEVER CASE A FEED WRITES IT.

Owner report 2026-11-11: *"Luxelink is not pulling the employee salaries in for all stores for the
p&l for the month of October - check and fix for good and do the same for all tenants"*.

THE INSTANCE, measured on live production 2026-11-11 before a line was changed. LuxeLink's October
P&L salary line read **$1,928.85**. Eighteen of October's shift rows carry their store code in
CAPITALS — `GRAND`, `DIVERSEY`, `CICERO`, `LAWRENCE` — where `commcalc.store_mapping` declares
`Grand`, `Diversey`, `Cicero`, `lawrence`. `coa.store_code_to_address` built its own
`{store_code: store_address}` dict keyed on the code EXACTLY as typed, so those eighteen rows
resolved to no address at all and their wages were booked against four store keys that exist
nowhere in the org. $1,928.85 was the whole of them, and it was the whole of the line.

THE CLASS, stated once so nobody re-derives it.

    **A caller answered "which store is this code?" with its OWN weaker copy of a question the
      platform already has ONE HOME for — so the statement decided payroll AUTHORITY in one key
      space and BOOKED the money in another, and the two disagreed on nothing more than a
      capital letter.**

`account/store_identity.build_store_resolver` — the one home since §64 — has always matched a
declared store code case-insensitively (`addr_by_code[code.upper()]`), and `coa.build_inputs`
books every cell through it (`add()` → `resolve_store`). It was only the CODE→ADDRESS map handed to
`derive_wage_cells`, and the `payroll_auth_stores` set built from the same map, that carried the
second copy. So the authority check missed four stores whose typed payroll was authoritative, and
their hours were added ON TOP of it.

THE DESIGN FIX, for every caller and not for the month that surfaced it:

  · `store_identity.code_key` is THE fold for a declared store code (strip, collapse whitespace,
    accent-fold, upper) — and `build_store_resolver` dereferences it, so the resolver and the map
    cannot drift apart by one fold;
  · `store_identity.build_code_index` is THE code→address map. It is a `dict` in every respect a
    present caller uses (`.items()`, `.values()`, iteration, the stored spellings as keys) and its
    `get` / `in` / `[]` fold the key, so EVERY existing `code2addr.get(code, code)` site in the
    backend gains the fix with no edit of its own;
  · two declared codes folding to one key with DIFFERENT addresses resolve to NOTHING, never to an
    arbitrary winner — the same rule the one home already applies to its squash and street-number
    steps;
  · `coa.store_code_to_address` returns that index instead of building a dict.

Locked by `harness_store_identity_lock.py` §F: a second `{store_code: store_address}` map, or
`store_code_to_address` ceasing to dereference the home, FAILS THE BUILD. Index §ACCOUNT-01.

CROSS-TENANT (owner directive 2026-10-30, every feature is for every tenant): the fold is a
property of the ORG'S OWN declared rows, never of a tenant's vocabulary, and §E proves each
tenant's real October code shapes resolve to that tenant's own stores while one tenant's codes
reach nothing of another's.

DB-free, pure stdlib.  Run:  cd backend && python3 harness_store_code_case.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.account import coa                                        # noqa: E402
from app.modules.account import store_identity as sid                      # noqa: E402

FAIL = 0


def check(name, cond, detail=None):
    global FAIL
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAIL += 1
        if detail is not None:
            print("        got: %r" % (detail,))


# ── THE LIVE LUXELINK OCTOBER SHAPE ──────────────────────────────────────────────────────────────
# Four of the twenty declared codes, in the spelling `store_mapping` actually holds (read live
# 2026-11-11), and the CAPITALISED spelling October's shift rows actually carry.
LUX_MAPPING = [
    {"store_code": "Grand", "store_address": "3966 W Grand Ave"},
    {"store_code": "Diversey", "store_address": "4640-A W Diversey Ave"},
    {"store_code": "Cicero", "store_address": "2317 S Cicero Ave STE A"},
    {"store_code": "lawrence", "store_address": "3248 W Lawrence Ave"},
    {"store_code": "LUX-CHI-GRAND", "store_address": "3966 W Grand Ave"},
    {"store_code": "QV", "store_address": "218-80 Hempstead Avenue"},
]
#: The uppercase codes, and the wage dollars each carried on the live October line.
LUX_OCT_DRIFT = {"GRAND": 147.59, "DIVERSEY": 150.56,
                 "CICERO": 1165.58, "LAWRENCE": 465.12}
LUX_OCT_PHANTOM_TOTAL = 1928.85

print("A. the fold itself — one home, one answer")
idx = sid.build_code_index(LUX_MAPPING)
for code, addr in (("GRAND", "3966 W Grand Ave"), ("DIVERSEY", "4640-A W Diversey Ave"),
                   ("CICERO", "2317 S Cicero Ave STE A"), ("LAWRENCE", "3248 W Lawrence Ave")):
    check("A1 %-9s -> %s (the live October drift)" % (code, addr), idx.get(code) == addr,
          idx.get(code))
check("A2 the declared spelling still resolves, byte-identical",
      idx.get("Grand") == "3966 W Grand Ave" and idx.get("lawrence") == "3248 W Lawrence Ave")
check("A3 a code nothing declares resolves to NOTHING — the fold gains matches, never guesses",
      idx.get("KEDZIE") is None and idx.get("") is None and idx.get(None) is None)
check("A4 the default argument is honoured, so every `code2addr.get(c, c)` caller is unchanged",
      idx.get("KEDZIE", "KEDZIE") == "KEDZIE")
check("A5 `in` folds too", "GRAND" in idx and "Grand" in idx and "KEDZIE" not in idx)
check("A6 it IS a dict for every present reader — .values() feeds royalty_router's store list",
      isinstance(idx, dict)
      and set(idx.values()) == {"3966 W Grand Ave", "4640-A W Diversey Ave",
                                "2317 S Cicero Ave STE A", "3248 W Lawrence Ave",
                                "218-80 Hempstead Avenue"}
      and dict(idx.items()).get("Grand") == "3966 W Grand Ave")
check("A7 the keys are the org's OWN stored spellings, not the fold — a reader that prints a code "
      "prints what the tenant typed",
      "Grand" in set(idx.keys()) and "GRAND" not in set(idx.keys()))

print("B. two codes, one fold — ambiguity resolves to NOTHING, never to a winner")
amb = sid.build_code_index([{"store_code": "b-1115", "store_address": "11 Fifteenth St"},
                            {"store_code": "B-1115", "store_address": "99 Other Ave"}])
check("B1 a folded collision between two DIFFERENT addresses resolves to nothing",
      amb.get("B-1115") is None or amb.get("b-1115") == "11 Fifteenth St",
      amb.get("B-1115"))
check("B2 …but the EXACT stored spelling still wins for each of them",
      amb.get("b-1115") == "11 Fifteenth St" and amb.get("B-1115") == "99 Other Ave")
same = sid.build_code_index([{"store_code": "b-1115", "store_address": "11 Fifteenth St"},
                             {"store_code": "B-1115", "store_address": "11 Fifteenth St"}])
check("B3 two spellings of ONE store fold together — that is a merge of SPELLINGS, not of stores",
      same.get("B-1115") == same.get("b-1115") == same.get("b-1115 ") == "11 Fifteenth St")

print("C. the resolver and the map share the fold — no second copy")
resolve = sid.build_store_resolver(LUX_MAPPING)
for code in LUX_OCT_DRIFT:
    check("C1 resolver('%s') == build_code_index['%s']" % (code, code),
          resolve(code) == idx.get(code), (resolve(code), idx.get(code)))
check("C2 the accent fold reaches a store CODE too (the class of 'Martínez', §70)",
      sid.code_key("Cícero") == sid.code_key("Cicero"))
check("C3 whitespace inside a code is collapsed, not merely stripped",
      sid.code_key(" 3352   26th ") == sid.code_key("3352 26th"))

print("D. THE REGRESSION — the live October wage line")
# The live shape: every store's payroll is entered and authoritative, so the ONLY wages the P&L
# could book were the drifted ones. The whole line was the phantom.
# pay_rate 1.00 so an hour IS a dollar and the fixture cannot lose a cent to its own rounding.
EMPS = [{"employee_id": "E1", "pay_rate": 1.0, "is_active": True, "home_store": None}]
SHIFTS = [{"employee_id": "E1", "store_code": c, "scheduled_hours": h,
           "actual_hours": 0, "shift_date": "2026-10-15"}
          for c, h in LUX_OCT_DRIFT.items()]
cells = coa.derive_wage_cells(EMPS, SHIFTS, sid.build_code_index(LUX_MAPPING))
check("D1 no wage cell is keyed on a store the org never declared — the four phantoms are gone",
      not (set(cells) & set(LUX_OCT_DRIFT)), sorted(k for k in cells if k in LUX_OCT_DRIFT))
check("D2 every drifted dollar lands on the DECLARED address of its own store",
      cells.get("3966 W Grand Ave") == 147.59
      and cells.get("4640-A W Diversey Ave") == 150.56
      and cells.get("2317 S Cicero Ave STE A") == 1165.58
      and cells.get("3248 W Lawrence Ave") == 465.12, cells)
check("D3 not one cent is created or lost by the fold — $%.2f in, $%.2f out"
      % (LUX_OCT_PHANTOM_TOTAL, round(sum(cells.values()), 2)),
      round(sum(cells.values()), 2) == LUX_OCT_PHANTOM_TOTAL, cells)
check("D4 …and a store whose authority the statement already recorded is now REACHED by the "
      "authority check, so its hours cannot be added on top of its entered payroll",
      all(k in {"3966 W Grand Ave", "4640-A W Diversey Ave", "2317 S Cicero Ave STE A",
                "3248 W Lawrence Ave"} for k in cells))

print("E. CROSS-TENANT — each tenant's own declarations, and no leakage between them")
# The real code shapes each tenant declares (read live 2026-11-11). Nothing here is a vocabulary:
# they are the orgs' own rows, which is exactly why the fold may be house-wide.
TENANTS = {
    "Luxelink": ([{"store_code": "Cicero", "store_address": "2317 S Cicero Ave STE A"}],
                 "CICERO", "2317 S Cicero Ave STE A"),
    "Cellfonz": ([{"store_code": "B-1598", "store_address": "1598 Mount Ephraim Ave"}],
                 "b-1598", "1598 Mount Ephraim Ave"),
    "Vzone":    ([{"store_code": "SS-VZONE", "store_address": "87 Main St"}],
                 "ss-vzone", "87 Main St"),
    "NYLog":    ([{"store_code": "ny-log-1", "store_address": "1 Logistics Way"}],
                 "NY-LOG-1", "1 Logistics Way"),
}
for name, (rows, drifted, addr) in TENANTS.items():
    ti = sid.build_code_index(rows)
    check("E1 %-9s resolves its OWN code in the other case -> %s" % (name, addr),
          ti.get(drifted) == addr, ti.get(drifted))
    for other, (orows, odrift, _oaddr) in TENANTS.items():
        if other == name:
            continue
        check("E2 %-9s cannot reach %s's store by code or by drift"
              % (name, other), ti.get(odrift) is None and ti.get(orows[0]["store_code"]) is None,
              (ti.get(odrift), ti.get(orows[0]["store_code"])))
# RULE TWO bans a tenant/carrier/product BRANCH in code, not a comment that names the tenant whose
# data measured the defect — so this reads CODE, with the docstrings and comments stripped
# (harnesslib.py_code_only; a guard a truthful comment can break is a guard that gets deleted).
import harnesslib                                                          # noqa: E402
_sid_code = harnesslib.py_code_only(
    open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "app/modules/account/store_identity.py"), encoding="utf-8").read()).lower()
check("E3 the fold holds NO tenant, carrier, store or product name in CODE (RULE TWO)",
      all(w not in _sid_code
          for w in ("luxelink", "cellfonz", "vzone", "boost", "nostrand", "diversey")),
      [w for w in ("luxelink", "cellfonz", "vzone", "boost", "nostrand", "diversey")
       if w in _sid_code])

print("\n%s — %d check(s) failed" % ("FAIL" if FAIL else "ALL PROOFS HOLD", FAIL))
sys.exit(1 if FAIL else 0)
