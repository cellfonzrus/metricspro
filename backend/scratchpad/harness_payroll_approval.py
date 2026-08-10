"""Pure-logic + live-SQL checks for the payroll approval package (mig 431).

Deliberately does NOT build a fake Supabase client for the filtering paths: a stub whose .eq() is a
no-op tests the WRONG thing (that class already cost two wrong counts on gp_category_map). Anything
needing a real filter is proven against the real database instead, via sbsql.
"""
import os, sys, subprocess, json
for k in ('SUPABASE_URL','SUPABASE_KEY','SUPABASE_SERVICE_KEY','SUPABASE_SERVICE_ROLE_KEY'):
    os.environ.setdefault(k, 'x')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import date
from app.modules.storeops import payroll_approval as P

ok = fail = 0
def check(name, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  PASS {name}")
    else: fail += 1; print(f"  FAIL {name}\n       got  {got}\n       want {want}")

print("1. work-week conversion (storeops stores 0=Sunday; Python weekday is 0=Monday)")
# The tenant column is the SQL convention. Sunday(0) must become Python 6, Monday(1) -> 0.
check("sql 0 (Sun) -> py 6", (0 - 1) % 7, 6)
check("sql 1 (Mon) -> py 0", (1 - 1) % 7, 0)
check("sql 6 (Sat) -> py 5", (6 - 1) % 7, 5)

print("2. previous_week — the LAST COMPLETE week, never the one in progress")
P._week_start_dow = lambda org_id: 6           # Sunday-start tenant (what all 3 tenants carry today)
# Mon 2026-08-10 -> the week that just closed is Sun 08-02 .. Sat 08-08
check("Mon 08-10, Sun weeks", P.previous_week("o", date(2026, 8, 10)),
      (date(2026, 8, 2), date(2026, 8, 8)))
# The Monday notice fires ON Monday; Sunday (the first day of the new week) must give the same answer
# a day earlier only for the week BEFORE it — proves the boundary isn't off by one.
check("Sun 08-09, Sun weeks", P.previous_week("o", date(2026, 8, 9)),
      (date(2026, 8, 2), date(2026, 8, 8)))
check("Sat 08-08 (mid-week), Sun weeks", P.previous_week("o", date(2026, 8, 8)),
      (date(2026, 7, 26), date(2026, 8, 1)))   # week of Jul 26 .. Aug 1
P._week_start_dow = lambda org_id: 0           # Monday-start tenant
check("Mon 08-10, Mon weeks", P.previous_week("o", date(2026, 8, 10)),
      (date(2026, 8, 3), date(2026, 8, 9)))

print("3. payer precedence: employee override > store default > org default")
payers = {"pa": {"id": "pa", "name": "AP"}, "pb": {"id": "pb", "name": "DM"}}
dflt = {"id": "pz", "name": "Default"}
check("employee override wins", P._resolve_payer("pa", "S1", {"S1": "pb"}, payers, dflt)[0]["id"], "pa")
check("store default next",     P._resolve_payer(None, "S1", {"S1": "pb"}, payers, dflt)[0]["id"], "pb")
check("org default last",       P._resolve_payer(None, "S9", {"S1": "pb"}, payers, dflt)[0]["id"], "pz")
check("nothing configured",     P._resolve_payer(None, "S9", {}, payers, None), (None, None))
check("stale payer id ignored", P._resolve_payer("gone", "S9", {}, payers, dflt)[0]["id"], "pz")
check("provenance label",       P._resolve_payer(None, "S1", {"S1": "pb"}, payers, dflt)[1], "store")

print("4. _num coercion")
check("blank -> None", P._num(""), None)
check("None -> None", P._num(None), None)
check("str -> float", P._num("38.456"), 38.46)
check("junk -> None", P._num("abc"), None)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
