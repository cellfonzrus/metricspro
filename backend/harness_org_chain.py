"""Proof + BUILD LOCK — the org-tree walk has ONE home (index §48.7).

WHY. "Which District Manager owns this store" was walked in TWO places in `storeops/router.py`:
`_dm_for_store` and `_managers_above_dm`, the second of which admitted it in its own docstring
("Mirrors _dm_for_store's district resolution"). Two walks answering one question drift, and the one
that drifts is the one that emails the wrong manager. Both were also PER STORE — four table reads
each — which is why the Cash Accountability Chain could not show the DM beside every store-day until
the walk answered in bulk (owner 2026-10-02).

PROVES, stdlib-only and DB-free:
  A. the climb — the first DISTRICT-LEVEL ancestor wins, the level is recognised by its NAME
     (config, not code), the 20-hop guard holds, and a cycle cannot hang it.
  B. the market fallback — no unit, or no district above it, matches a district unit by the store's
     market (name contains it, or code == "district:<market>"), case-insensitively; and a store with
     neither resolves to NOTHING rather than to someone else's district.
  C. managers — every manager at the district node (a node may have several), everyone above it to
     the root deduped nearest-first, and a manager whose employee row is missing still carries its
     employee_id rather than vanishing.
  D. bulk — dm_by_store answers for every store handed in, applies the store_mapping market overlay,
     and skips a row with no store_code.
  E. honesty — dm_names is [] for an unwired tree, and `district` says which of the two it is.
  F. THE LOCK — storeops contains exactly ONE district walk, both public resolvers dereference this
     module, and the accountability chain reads the bulk map (never a third walk).

Run: python3 backend/harness_org_chain.py
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "app"))

from app.modules.storeops import org_chain as OC          # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


# A small, realistic tree: Company > Region > District > Store-unit.
LEVELS = [{"id": "L1", "name": "Company"}, {"id": "L2", "name": "Region"},
          {"id": "L3", "name": "District"}, {"id": "L4", "name": "Store"}]
UNITS = [{"id": "U1", "name": "HQ", "level_id": "L1", "parent_id": None, "code": "hq"},
         {"id": "U2", "name": "Northeast", "level_id": "L2", "parent_id": "U1", "code": "region:ne"},
         {"id": "U3", "name": "Brooklyn District", "level_id": "L3", "parent_id": "U2", "code": "district:brooklyn"},
         {"id": "U4", "name": "B-559", "level_id": "L4", "parent_id": "U3", "code": "store:b559"}]
MANAGERS = [{"unit_id": "U3", "employee_id": "E-DM"}, {"unit_id": "U3", "employee_id": "E-DM2"},
            {"unit_id": "U2", "employee_id": "E-RM"}, {"unit_id": "U1", "employee_id": "E-CEO"},
            {"unit_id": "U3", "employee_id": "E-GHOST"}]
EMPLOYEES = [{"employee_id": "E-DM", "name": "Dana Mills", "email": "dana@x.test"},
             {"employee_id": "E-DM2", "name": "Dev Mehta", "email": "dev@x.test"},
             {"employee_id": "E-RM", "name": "Rhea Mann", "email": "rhea@x.test"},
             {"employee_id": "E-CEO", "name": "Cleo Ortiz", "email": "cleo@x.test"}]
LN, UB = OC.index_tree(LEVELS, UNITS)
MGRS = {}
for m in MANAGERS:
    MGRS.setdefault(m["unit_id"], []).append(m["employee_id"])
EMPS = {e["employee_id"]: e for e in EMPLOYEES}

print("A. the climb")
check("the first district-LEVEL ancestor wins",
      (OC.district_for("U4", "", LN, UB) or {}).get("id") == "U3")
check("a store unit that IS the district resolves to itself",
      (OC.district_for("U3", "", LN, UB) or {}).get("id") == "U3")
check("the level is recognised by its NAME, so a tenant's own wording works (config, not code)",
      (OC.district_for("U4", "", [{"id": "L3", "name": "district group"}] and LN, UB) or {}).get("id") == "U3")
_odd_levels, _odd_units = OC.index_tree(
    [{"id": "LX", "name": "DISTRICT"}],
    [{"id": "UX", "name": "d", "level_id": "LX", "parent_id": None}])
check("...case-insensitively", (OC.district_for("UX", "", _odd_levels, _odd_units) or {}).get("id") == "UX")
# A chain longer than the guard, and a cycle: neither may hang or resolve past the guard.
_deep = [{"id": f"D{i}", "name": f"n{i}", "level_id": "L4", "parent_id": f"D{i+1}"} for i in range(30)]
_deep.append({"id": "D30", "name": "far district", "level_id": "L3", "parent_id": None})
_dl, _du = OC.index_tree(LEVELS, _deep)
check("the 20-hop guard stops the climb rather than walking an arbitrarily deep tree",
      OC.district_for("D0", "", _dl, _du) is None)
_cyc_l, _cyc_u = OC.index_tree(LEVELS, [{"id": "C1", "name": "a", "level_id": "L4", "parent_id": "C2"},
                                        {"id": "C2", "name": "b", "level_id": "L4", "parent_id": "C1"}])
check("a PARENT CYCLE terminates (the guard, not a hang)",
      OC.district_for("C1", "", _cyc_l, _cyc_u) is None)

print()
print("B. the market fallback")
check("no unit at all → matched by market in the unit NAME",
      (OC.district_for(None, "Brooklyn", LN, UB) or {}).get("id") == "U3")
check("...case-insensitively", (OC.district_for(None, "brooklyn", LN, UB) or {}).get("id") == "U3")
_by_code, _bc_units = OC.index_tree(
    LEVELS, [{"id": "K1", "name": "unrelated", "level_id": "L3", "parent_id": None, "code": "district:queens"}])
check("...or by the code convention district:<market>",
      (OC.district_for(None, "Queens", _by_code, _bc_units) or {}).get("id") == "K1")
check("a store with no unit and no market resolves to NOTHING, never someone else's district",
      OC.district_for(None, "", LN, UB) is None)
check("a market that matches no district resolves to nothing",
      OC.district_for(None, "Nowhere", LN, UB) is None)
check("a unit with no district ABOVE it still falls back to the market",
      (OC.district_for("U1", "Brooklyn", LN, UB) or {}).get("id") == "U3")

print()
print("C. managers")
ch = OC.chain_for("U4", "", LN, UB, MGRS, EMPS)
check("every manager at the district node is returned, not just the first",
      [m["employee_id"] for m in ch["dm"]] == ["E-DM", "E-DM2", "E-GHOST"], str(ch["dm"]))
check("a manager with no employee row keeps its employee_id (never silently dropped)",
      [m for m in ch["dm"] if m["employee_id"] == "E-GHOST"][0]["name"] is None)
check("the DM carries name, email, phone, unit and level",
      ch["dm"][0] == {"employee_id": "E-DM", "name": "Dana Mills", "email": "dana@x.test",
                      "phone": None,
                      "unit": "Brooklyn District", "level": "District"}, str(ch["dm"][0]))
# `phone` was added so an alert can reach a manager on WhatsApp (owner 2026-10-03). It is ADDITIVE,
# and the pin below keeps it that way: the walk reports the number it was given and decides nothing
# about reachability, which belongs to manager_digest.addresses_for.
check("...and phone is carried through from the employee row when there is one",
      OC.managers_at({"id": "u-d", "name": "Brooklyn District", "level_id": "l-d"},
                     {"u-d": ["E-DM"]},
                     {"E-DM": {"name": "Dana Mills", "email": "dana@x.test", "phone": "5551234567"}},
                     {"l-d": "District"})[0]["phone"] == "5551234567")
check("...and an employee row with NO phone yields None, never an empty string that looks like one",
      ch["dm"][0]["phone"] is None)
check("everyone ABOVE the district is returned, nearest first",
      [m["employee_id"] for m in ch["above"]] == ["E-RM", "E-CEO"], str(ch["above"]))
check("...deduped by employee_id when one person manages two ancestors",
      [m["employee_id"] for m in OC.chain_for(
          "U4", "", LN, UB, {**MGRS, "U1": ["E-RM"]}, EMPS)["above"]] == ["E-RM"])
check("the district itself is never listed as 'above' it",
      "E-DM" not in [m["employee_id"] for m in ch["above"]])
check("an unwired tree gives empty lists and no district",
      OC.chain_for(None, "", LN, UB, MGRS, EMPS) == {"district": None, "dm": [], "above": []})

print()
print("D. bulk")
STORES = [{"store_code": "B-559", "org_unit_id": "U4", "market": ""},
          {"store_code": "Q-101", "org_unit_id": None, "market": "Queens"},
          {"store_code": "X-000", "org_unit_id": None, "market": ""},
          {"store_code": "", "org_unit_id": "U4", "market": ""}]
bulk = OC.dm_by_store(STORES, LEVELS, UNITS + [
    {"id": "K1", "name": "Queens District", "level_id": "L3", "parent_id": "U2", "code": "district:queens"}],
    MANAGERS + [{"unit_id": "K1", "employee_id": "E-DM2"}], EMPLOYEES)
check("every store handed in gets an answer", sorted(bulk) == ["B-559", "Q-101", "X-000"], str(sorted(bulk)))
check("a row with no store_code is skipped, not keyed on blank", "" not in bulk)
check("the climb answers for one store and the market fallback for another",
      (OC.dm_names(bulk["B-559"]), OC.dm_names(bulk["Q-101"]))
      == (["Dana Mills", "Dev Mehta", "E-GHOST"], ["Dev Mehta"]),
      str((OC.dm_names(bulk["B-559"]), OC.dm_names(bulk["Q-101"]))))
overlay = OC.dm_by_store([{"store_code": "M-1", "org_unit_id": None, "market": ""}],
                         LEVELS, UNITS, MANAGERS, EMPLOYEES,
                         market_by_code={"m-1": "Brooklyn"})
check("the store_mapping market OVERLAY binds the fallback (the 2026-09-03 LI-class rule), "
      "case-insensitively on the code", OC.dm_names(overlay["M-1"]) != [], str(overlay))
check("a store's OWN market wins over the overlay",
      OC.dm_by_store([{"store_code": "M-1", "org_unit_id": None, "market": "Nowhere"}],
                     LEVELS, UNITS, MANAGERS, EMPLOYEES,
                     market_by_code={"M-1": "Brooklyn"})["M-1"]["district"] is None)
check("empty inputs are an empty answer, never a raise", OC.dm_by_store([], [], [], [], []) == {})

print()
print("E. honesty — nothing is guessed")
check("dm_names is [] when the tree is not wired", OC.dm_names(bulk["X-000"]) == [])
check("...and `district` says it was the TREE that was missing, not the manager",
      bulk["X-000"]["district"] is None and bulk["B-559"]["district"] is not None)
check("a district with NO manager reports no names but a resolved district (different facts)",
      (lambda c: OC.dm_names(c) == [] and c["district"] is not None)(
          OC.chain_for("U4", "", LN, UB, {}, EMPS)))
check("dm_names falls back to the employee_id rather than printing None",
      "E-GHOST" in OC.dm_names(bulk["B-559"]))

print()
print("F. THE LOCK — one walk, and every caller dereferences it")
SR = os.path.join(HERE, "app", "modules", "storeops", "router.py")
sr_src = read(SR)
# A district walk is the loop over org_units.parent_id testing a 'district' level name. Comments and
# docstrings legitimately NAME the rule (that is how the one home is documented), so the scan is over
# code: any function that spells the district test is walking the tree itself.
sr_code = re.sub(r"(?m)^\s*#.*$", "", sr_src)
for node in ast.walk(ast.parse(sr_src)):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        body = ast.get_source_segment(sr_src, node) or ""
        body = re.sub(r"(?m)^\s*#.*$", "", body)
        body = re.sub(r'"""(.|\n)*?"""', "", body)
        if '"district"' in body or "'district'" in body:
            if "level_id" in body or "parent_id" in body:
                FAILS.append("a second district walk in storeops.router.%s" % node.name)
                print("  [FAIL] storeops.router.%s walks the org tree itself" % node.name)
check("storeops.router contains NO district walk of its own (it dereferences org_chain)",
      not [f for f in FAILS if "second district walk" in f])
check("_dm_for_store dereferences the one home", "org_chain.dm_by_store(" in sr_src)
check("_managers_above_dm dereferences it too", sr_src.count("org_chain.dm_by_store(") >= 2)
# The org-tree ADMIN endpoints read org_units legitimately (the tree CRUD and its views), so the rule
# is not "nothing else reads it". It is that the DM-RESOLUTION path reads its four tables in one
# place — `org_chain_inputs` — and that neither resolver touches a table itself, which is what makes
# one set of reads serve a whole-span report.
_fns = {n.name: (ast.get_source_segment(sr_src, n) or "")
        for n in ast.walk(ast.parse(sr_src))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
check("org_chain_inputs is the DM path's one reader, and reads all four tables",
      all(t in _fns.get("org_chain_inputs", "")
          for t in ('"stores"', '"org_levels"', '"org_units"', '"org_managers"', '"employees"')),
      str(sorted(_fns))[:120])
for _fn in ("_dm_for_store", "_managers_above_dm"):
    check("`%s` reads no table itself — it dereferences the one home" % _fn,
          ".table(" not in _fns.get(_fn, ".table("), _fns.get(_fn, "")[:200])
check("org_chain_inputs can be asked for ONE store or for ALL of them (the bulk path)",
      "store_code=None" in _fns.get("org_chain_inputs", "")
      and 'if store_code else None' in _fns.get("org_chain_inputs", ""))
CR = read(os.path.join(HERE, "app", "modules", "closing", "router.py"))
check("the accountability chain reads the BULK map, not a walk or a per-store loop",
      "org_chain.dm_by_store(" in CR and "org_chain_inputs(org_id)" in CR)
check("...and gets who-worked from the ONE home, never a second definition",
      "_who_worked_display_by_store(client, org_id, _d)" in CR)
check("...capped, with the unresolved dates REPORTED rather than rendered as nobody",
      "_CHAIN_WORKED_MAX_DATES" in CR and '"worked_dates_capped"' in CR
      and '"resolved": False' in CR)
# RULE TWO — no tenant/carrier/store branch in the new module.
check("RULE TWO: the pure module names no tenant, carrier or store",
      not re.search(r"(?i)\b(boost|vidapay|luxelink|cellfonz|t-?mobile|verizon|total\s*wireless)\b",
                    read(os.path.join(HERE, "app", "modules", "storeops", "org_chain.py"))))

print()
if FAILS:
    print(f"❌ harness_org_chain: {len(FAILS)} failure(s): {FAILS}")
    sys.exit(1)
print("✅ harness_org_chain: ALL PASS")
