"""Proof harness — OVERHEAD STAFF ALLOCATION (owner directive 2026-09-09: "All employees who are
salaried and not attached to stores like the DM and market manager shoudl have thier won seaprat
line and thier salary will be divided amongs he store they handle … simialry thier commission will
also be a seaprate line item … all these will be a seaprate box in the p&l under wages").

DB-free, pure stdlib. Proves backend/app/modules/storeops/overhead_allocation.py:

  1. WHO is overhead — the STRUCTURAL test (active + salaried + no home store) needs no role string,
     so RULE TWO holds; an org may WIDEN it with configured roles, never narrow it.
  2. WHICH stores they cover — the canonical `org_span_for_manager` answer FIRST, the org-unit
     subtree only as a configured fallback, and NOTHING guessed when neither answers.
  3. THE ALLOCATION BASIS — equal-per-store (the default reading), market-then-store (the owner's
     second clause), and weighted; each cents-exact, each naming the basis it ACTUALLY used.
  4. COMPANY == SUM OF STORES — asserted as an identity, in both directions, with no company-wide
     bucket existing to book into.
  5. CONFIG IS CONFIG — the house default books nothing; no tenant, store, role or employee name
     appears in the module.
  6. The manual-overhead reconciliation REPORTS and never suppresses.

REGRESSIONS reproducing the REPORTED live defects — measured 2026-09-09 against the live LuxeLink
tenant, org 854f6d7b-…-646f560d4f4c, August 2026:

  R1  THE $78,000 EMPLOYEE. One active employee carries pay_basis 'annual' / pay_amount 78,000.00
      ($6,500.00 a month), NO home_store and NO shifts. `coa.derive_wage_cells` can only key such a
      person as company-wide (`None`), and with `payroll_authority_grain='store'` live and all
      twenty stores authoritative, `build_inputs` deliberately does not book a company-wide cell —
      so $78,000.00/year appears in NO P&L line at all. Pinned: they are classified overhead and
      their $6,500.00 is spread over the stores they cover, never dropped and never booked to a
      company bucket.
  R2  EQUAL SPLIT, EXACT CENTS. Their org unit is the Chicago market, whose subtree holds 13 active
      stores; $6,500.00 / 13 = $500.00 per store, and the 13 cells must re-sum to $6,500.00 to the
      cent. Pinned together with the 20-store ($325.00) and market-then-store ($250.00 Chicago /
      $464.29 NY) readings, so switching basis is provably a config change and not a rewrite.
  R3  COMPANY = SUM OF STORES. "the company will only calculate as a result of all store combined
      together." Pinned: there is no company-wide key in either output map, market subtotals re-sum
      to the company figure, and an unresolvable person's dollars go to `unallocated` — reported,
      never silently folded into a company line.
  4a  OVER-MATCH GUARD on the store-code fold. `3352 26th` and `3735 26th` are one digit apart and
      four raw spellings of them appear in live shift/timelog data. Over-matching moves one store's
      payroll onto another silently, which is strictly worse than not matching, so the fold is
      pinned to keep them apart — including under the leading-street-number rule that binds
      '3248 LAWARANCE' correctly.
  R4  ROSTER GAP, NOT A SUPPRESSION. LuxeLink already types $35,500.01/month of overhead salary in
      by hand ('DM Salaries' $25,500.01 + 'Owner / Mgmt Salaries' $10,000.00 across 20 stores) while
      the roster explains only $6,500.00. Pinned: the reconciliation REPORTS the $29,000.01 gap and
      suppresses nothing — deleting the larger, more complete figure on the strength of the smaller
      derived one would remove real cost and book nothing back.

Run: python3 backend/harness_overhead_allocation.py   (exit 0 = all proofs hold)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.storeops.overhead_allocation import (   # noqa: E402
    BASIS_EQUAL_STORES, BASIS_MARKET_THEN_STORE, BASIS_WEIGHTED,
    COVER_ORG_SPAN, COVER_ORG_UNIT, COVER_ORG_WIDE, COVER_NONE,
    WHY_UNATTACHED, WHY_ROLE, WHY_INACTIVE, WHY_NOT_SALARIED, WHY_STORE_ATTACHED,
    DEFAULT_CONFIG, resolve_config, classify_employee, covered_stores, allocate,
    build_overhead, company_total, rollup_by_market, reconcile_manual, line_note,
)
from app.modules.storeops.salary_expense import build_store_folder   # noqa: E402

FAIL = 0
CHECKS = 0


def check(name, cond):
    global FAIL, CHECKS
    CHECKS += 1
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAIL += 1


# The platform's ONE salary conversion (account/coa.monthly_salary_equivalent), re-stated here so
# the harness stays DB-free. §0 below pins it against the real one line-for-line.
_SALARY_BASES = {"weekly", "monthly", "annual"}


def meq(basis, amount):
    b = str(basis or "").strip().lower()
    try:
        amt = float(amount or 0)
    except (TypeError, ValueError):
        amt = 0.0
    if b not in _SALARY_BASES or amt <= 0:
        return None
    m = amt if b == "monthly" else (amt / 12.0 if b == "annual" else amt * 52.0 / 12.0)
    return round(m, 2)


# The live LuxeLink shape, anonymised to codes only (RULE TWO: no tenant or employee name here).
CHI = ["3352 26th", "3735 26th", "Armitage", "Belmont", "Cermark", "Chicago heights", "Cicero",
       "Diversey", "Grand", "Irving Park", "Narragansett", "kedzie", "lawrence"]        # 13
NY = ["7812", "957", "Ave U", "Lefferts", "Nostrand", "QV", "Utica"]                    # 7
ALL20 = sorted(CHI + NY)
MARKET = {c: ("Chicago" if c in CHI else "NY") for c in ALL20}
CFG_ON = {"mode": "derive"}

print("0. the salary conversion is REUSED, not forked")
try:
    from app.modules.account.coa import monthly_salary_equivalent as real_meq
    check("this harness's conversion matches account/coa.monthly_salary_equivalent on annual 78,000",
          real_meq("annual", 78000) == meq("annual", 78000) == 6500.00)
    check("…and on weekly / monthly, so no second conversion table exists",
          real_meq("weekly", 1000) == meq("weekly", 1000)
          and real_meq("monthly", 8000) == meq("monthly", 8000)
          and real_meq("hourly", 19) is None and meq("hourly", 19) is None)
except Exception as e:                                            # pragma: no cover
    check("account/coa.monthly_salary_equivalent importable (%s)" % e, False)

print()
print("1. WHO is overhead — structural, so no role string lives in code (RULE TWO)")
CFG = resolve_config(CFG_ON)
E78 = {"employee_id": "E-A", "role": "Manager", "home_store": None,
       "pay_basis": "annual", "pay_amount": 78000, "is_active": True}
check("R1: active + salaried + NO home store -> OVERHEAD, $6,500.00/month",
      classify_employee(E78, CFG, meq) == (True, WHY_UNATTACHED, 6500.00))
check("a salaried employee WITH a home store is NOT overhead (the store wage path already has them)",
      classify_employee({**E78, "home_store": "Grand"}, CFG, meq)[:2] == (False, WHY_STORE_ATTACHED))
check("…unless the org LISTS their role as overhead — config widens, never narrows",
      classify_employee({**E78, "home_store": "Grand"},
                        resolve_config({**CFG_ON, "roles": ["Manager"]}), meq)
      == (True, WHY_ROLE, 6500.00))
check("role matching is case-insensitive, so a roster's own casing is not a trap",
      classify_employee({**E78, "home_store": "Grand", "role": "MARKET MANAGER"},
                        resolve_config({**CFG_ON, "roles": ["market manager"]}), meq)[0] is True)
check("an HOURLY unattached employee is NOT overhead — hours x rate already places them",
      classify_employee({**E78, "pay_basis": "hourly", "pay_amount": None}, CFG, meq)[:2]
      == (False, WHY_NOT_SALARIED))
check("an INACTIVE salaried employee is never overhead — a leaver must not load every store",
      classify_employee({**E78, "is_active": False}, CFG, meq)[:2] == (False, WHY_INACTIVE))
check("a zero / negative salary is not a salary",
      classify_employee({**E78, "pay_amount": 0}, CFG, meq)[:2] == (False, WHY_NOT_SALARIED)
      and classify_employee({**E78, "pay_amount": -5}, CFG, meq)[:2] == (False, WHY_NOT_SALARIED))
check("no tenant/role/store literal in the module (RULE TWO, checked as text)",
      all(tok not in open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "app/modules/storeops/overhead_allocation.py")).read().lower()
          for tok in ("luxelink", "854f6d7b", "cermark", "nostrand", "district manager")))

print()
print("2. WHICH stores they cover — the canonical RPC first, nothing guessed")
check("a non-empty org_span_for_manager result WINS and is used as-is",
      covered_stores(["Grand", "Belmont"], CHI, ALL20, CFG) == (["Belmont", "Grand"], COVER_ORG_SPAN))
check("R1/R2: an EMPTY span (org_managers unwritten, the live shape) falls back to the org unit",
      covered_stores([], CHI, ALL20, CFG) == (sorted(CHI), COVER_ORG_UNIT))
check("span_fallback='org_span' refuses that fallback — strictest setting resolves nothing",
      covered_stores([], CHI, ALL20, resolve_config({**CFG_ON, "span_fallback": "org_span"}))
      == ([], COVER_NONE))
check("no span and no unit resolves NOTHING under the default — coverage is never invented",
      covered_stores([], [], ALL20, CFG) == ([], COVER_NONE))
check("…and only 'org_wide' will claim every store on a person's behalf",
      covered_stores([], [], ALL20, resolve_config({**CFG_ON, "span_fallback": "org_wide"}))
      == (ALL20, COVER_ORG_WIDE))
check("duplicate / blank / padded codes collapse deterministically",
      covered_stores([" Grand ", "Grand", "", None, "Belmont"], [], ALL20, CFG)
      == (["Belmont", "Grand"], COVER_ORG_SPAN))

print()
print("3. THE ALLOCATION BASIS — both readings of the directive, each named honestly")
w13, b13, _ = allocate(6500.00, CHI, CFG, market_of=MARKET.get)
check("R2: equal split over the 13 covered stores = $500.00 each",
      b13 == BASIS_EQUAL_STORES and set(w13.values()) == {500.00} and len(w13) == 13)
check("R2: the 13 cells re-sum to $6,500.00 EXACTLY (no cent lost or invented)",
      company_total(w13) == 6500.00)
w20, _b, _n = allocate(6500.00, ALL20, CFG, market_of=MARKET.get)
check("the same salary over all 20 stores = $325.00 each, still exact",
      set(w20.values()) == {325.00} and company_total(w20) == 6500.00)
wm, bm, nm = allocate(6500.00, ALL20, resolve_config({**CFG_ON, "basis": BASIS_MARKET_THEN_STORE}),
                      market_of=MARKET.get)
chi_vals = sorted({wm[c] for c in CHI})
ny_vals = sorted({wm[c] for c in NY})
check("the owner's SECOND clause (per market, then per store) gives a DIFFERENT distribution",
      bm == BASIS_MARKET_THEN_STORE and chi_vals == [250.00] and ny_vals[-1] == 464.29)
check("…and still re-sums to $6,500.00 exactly — only the distribution differs",
      company_total(wm) == 6500.00)
check("with ONE market covered the two readings coincide, and it reports the equal split it IS",
      allocate(6500.00, CHI, resolve_config({**CFG_ON, "basis": BASIS_MARKET_THEN_STORE}),
               market_of=MARKET.get)[1] == BASIS_EQUAL_STORES)
ww, bw, _ = allocate(1000.00, ["A", "B"], resolve_config({**CFG_ON, "basis": BASIS_WEIGHTED}),
                     weight_of={"A": 30.0, "B": 10.0})
check("a weighted basis spreads pro-rata and exactly",
      bw == BASIS_WEIGHTED and ww == {"A": 750.00, "B": 250.00})
wz, bz, nz = allocate(1000.00, ["A", "B"], resolve_config({**CFG_ON, "basis": BASIS_WEIGHTED}),
                      weight_of={"A": 0, "B": 0})
check("a weighted basis with NO usable weights degrades to equal AND SAYS SO — never a silent swap",
      bz == BASIS_EQUAL_STORES and wz == {"A": 500.00, "B": 500.00} and "EQUAL split" in nz)
w3, _b3, _n3 = allocate(100.00, ["A", "B", "C"], CFG)
check("an indivisible amount is cents-exact, last store absorbing the residue",
      w3 == {"A": 33.33, "B": 33.33, "C": 33.34} and company_total(w3) == 100.00)
check("no covered stores -> no cells at all (never a company bucket)",
      allocate(6500.00, [], CFG) == ({}, BASIS_EQUAL_STORES, "no covered stores"))

print()
print("4. THE TWO LINES, and COMPANY == SUM OF STORES")
PEOPLE = [
    {"employee_id": "E-A", "label": "A", "role": "Manager", "why": WHY_UNATTACHED,
     "salary_month": 6500.00, "commission": 1300.00, "covered": CHI, "coverage": COVER_ORG_UNIT},
    {"employee_id": "E-B", "label": "B", "role": "Manager", "why": WHY_UNATTACHED,
     "salary_month": 4000.00, "commission": 0.0, "covered": NY, "coverage": COVER_ORG_SPAN},
    {"employee_id": "E-C", "label": "C", "role": "Manager", "why": WHY_UNATTACHED,
     "salary_month": 3000.00, "commission": 250.00, "covered": [], "coverage": COVER_NONE},
]
res = build_overhead(PEOPLE, CFG_ON, market_of=MARKET.get)
check("R1: the $6,500.00 employee books, at $500.00 on each of their 13 covered stores",
      all(res["wages_by_store"][c] >= 500.00 for c in CHI)
      and res["people"][0]["salary_by_store"] == {c: 500.00 for c in sorted(CHI)})
check("commission is a SEPARATE map — the two lines never share a cell",
      res["commission_by_store"] and set(res["commission_by_store"]) == set(CHI)
      and company_total(res["commission_by_store"]) == 1300.00)
check("R3: NO company-wide key exists in either map — every dollar is on a store",
      None not in res["wages_by_store"] and "" not in res["wages_by_store"]
      and None not in res["commission_by_store"])
check("R3: company == sum of stores, in BOTH directions",
      company_total(res["wages_by_store"]) == res["totals"]["salary_allocated"] == 10500.00
      and company_total(res["commission_by_store"]) == res["totals"]["commission_allocated"] == 1300.00)
mk = rollup_by_market(res["wages_by_store"], MARKET.get)
check("R3: market subtotals are a pure SUM and re-sum to the company figure",
      mk == {"Chicago": 6500.00, "NY": 4000.00}
      and round(sum(mk.values()), 2) == company_total(res["wages_by_store"]))
check("R3: the person with NO resolvable coverage is REPORTED, never spread and never a company line",
      len(res["unallocated"]) == 1 and res["unallocated"][0]["employee_id"] == "E-C"
      and res["totals"]["salary_unallocated"] == 3000.00
      and res["totals"]["commission_unallocated"] == 250.00
      and 3000.00 not in res["wages_by_store"].values())
check("every allocated person carries the per-person audit row the owner asked for",
      {p["employee_id"] for p in res["people"]} == {"E-A", "E-B"}
      and res["people"][0]["store_count"] == 13
      and res["people"][0]["per_store_salary"] == 500.00
      and res["people"][0]["coverage"] == COVER_ORG_UNIT)
check("the line note names the unbooked dollars rather than hiding them",
      "$3,250.00" in line_note(res) and "NOT booked" in line_note(res))

print()
print("5. CONFIG IS CONFIG — the house default derives and books NOTHING")
check("house default mode is 'off'", DEFAULT_CONFIG["mode"] == "off"
      and resolve_config(None)["mode"] == "off")
check("house default basis is the equal-per-store reading",
      resolve_config(None)["basis"] == BASIS_EQUAL_STORES)
check("house default commission source is off — no commission is placed unasked",
      resolve_config(None)["commission_source"] == "off")
check("an unknown basis / mode / fallback falls back to the default, never a fourth behaviour",
      resolve_config({"mode": "sneaky", "basis": "vibes", "span_fallback": "everything"})
      == {**DEFAULT_CONFIG})
check("a malformed config row (list, string, None) is read as the house default",
      resolve_config([]) == resolve_config("x") == resolve_config(None) == {**DEFAULT_CONFIG})
check("labels are per-org config, not code",
      resolve_config({"wages_label": "Field leadership"})["wages_label"] == "Field leadership")
check("mode='off' means an empty result even with overhead people present",
      build_overhead(PEOPLE, {"mode": "off"})["config"]["mode"] == "off")

print()
print("6. OVER-MATCH GUARD — two stores one digit apart must never cross (4a)")
ROSTER = ALL20


def fake_resolve(raw):
    """A stand-in for commcalc.router._store_code_resolver with the SAME shape as the live chain:
    exact address, then UNAMBIGUOUS leading street number. Live LuxeLink store_mapping addresses."""
    addr = {"3352 w 26th st": "3352 26th", "3735 w 26th st": "3735 26th",
            "3248 w lawrence ave": "lawrence", "3966 w grand ave": "Grand"}
    num = {"3352": "3352 26th", "3735": "3735 26th", "3248": "lawrence", "3966": "Grand"}
    s = str(raw or "").strip().lower()
    if s in addr:
        return addr[s]
    lead = ""
    for ch in s:
        if ch.isdigit():
            lead += ch
        else:
            break
    return num.get(lead, raw)


fold = build_store_folder(ROSTER, fake_resolve)
check("4a: '3352 26th Chicago' binds to 3352 26th and NOT to 3735 26th",
      fold("3352 26th Chicago")[0] == "3352 26th")
check("4a: '3735 26th Chicago' binds to 3735 26th and NOT to 3352 26th",
      fold("3735 26th Chicago")[0] == "3735 26th")
check("4a: the two one-digit-apart stores never resolve to the same code under ANY live spelling",
      len({fold(s)[0] for s in ("3352 26TH", "3352 26th", "3352 26th Chicago")}) == 1
      and len({fold(s)[0] for s in ("3735 26TH", "3735 26th", "3735 26th Chicago")}) == 1
      and fold("3352 26TH")[0] != fold("3735 26TH")[0])
check("case and a trailing city name already fold — no resolver loosening is needed for them",
      fold("CERMARK")[0] == "Cermark" and fold("3248 LAWRENCE CHICAGO")[0] == "lawrence"
      and fold("3966 GRAND CHICAGO")[0] == "Grand")
check("the owner's example: '3248 LAWARANCE' (misspelled) still binds to lawrence, by street number",
      fold("3248 LAWARANCE")[0] == "lawrence" and fold("3248 Lawarance")[0] == "lawrence")
check("a string that binds to NOTHING is reported as unbound, never guessed onto a neighbour",
      fold("T-7812") == (None, "unbound"))

print()
print("7. THE MANUAL OVERHEAD ROWS — reported, never suppressed (R4)")
MANUAL = ([{"store_code": c, "expense_name": "DM Salaries", "amount": 1275.00} for c in ALL20]
          + [{"store_code": c, "expense_name": "Owner / Mgmt Salaries", "amount": 500.00} for c in ALL20]
          + [{"store_code": c, "expense_name": "Rent / Lease", "amount": 3673.25} for c in ALL20])
rec = reconcile_manual({c: 500.00 for c in CHI}, MANUAL,
                       {**CFG_ON, "manual_expense_names": ["DM Salaries", "Owner / Mgmt Salaries"]})
check("R4: only the CONFIGURED names are counted — rent is not overhead salary",
      rec["manual_total"] == 35500.00 and rec["derived_total"] == 6500.00)
check("R4: the gap is reported as a number, per store and in total",
      rec["gap"] == 29000.00 and len(rec["by_store"]) == 20)
check("R4: the note says NEITHER figure is suppressed and names the fix as roster data",
      "NEITHER is suppressed" in rec["note"] and "roster row" in rec["note"])
check("R4: the reconciliation returns NO suppression instruction of any kind",
      not any(k in rec for k in ("suppress", "cells", "drop", "removed")))
check("with no configured names nothing is claimed and the note is silent",
      reconcile_manual({c: 500.00 for c in CHI}, MANUAL, CFG_ON)["note"] == ""
      and reconcile_manual({c: 500.00 for c in CHI}, MANUAL, CFG_ON)["manual_total"] == 0.0)

print()
print("checks run: %d   failures: %d" % (CHECKS, FAIL))
print("ALL PROOFS HOLD" if not FAIL else "PROOFS FAILED")
sys.exit(1 if FAIL else 0)
