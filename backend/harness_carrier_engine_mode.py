"""HARNESS — carrier pay-engine selection is CONFIG-driven, not carrier-NAME-driven (audit fix #1).

Proves _resolve_carrier_mode prefers an explicit carrier.engine_mode (mig 303) and falls back to the
legacy name-substring heuristic ONLY when no carrier carries an explicit value — so pre-303 rows behave
byte-identically while a renamed carrier can no longer misroute a tenant's pay.

  python3 backend/harness_carrier_engine_mode.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.commcalc.router import _resolve_carrier_mode, _norm_engine_mode, _clean_engine_mode_in  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}   {extra}")


print("── A. legacy fallback (no engine_mode anywhere) is byte-identical to pre-303 ──")
check("no carriers -> boost", _resolve_carrier_mode([]) == 'boost')
check("default Boost carrier -> boost",
      _resolve_carrier_mode([{"name": "Boost", "is_default": True}]) == 'boost')
check("default non-Boost carrier -> plan",
      _resolve_carrier_mode([{"name": "Total Wireless", "is_default": True}]) == 'plan')
check("no default, a Boost present -> boost",
      _resolve_carrier_mode([{"name": "Boost"}, {"name": "Cricket"}]) == 'boost')
check("no default, none Boost -> plan",
      _resolve_carrier_mode([{"name": "Total"}, {"name": "Cricket"}]) == 'plan')

print("── B. explicit engine_mode WINS over the name (the whole point) ──")
# a carrier NAMED Boost but explicitly configured to the plan engine -> plan
check("name=Boost but engine_mode=plan -> plan (name no longer decides)",
      _resolve_carrier_mode([{"name": "Boost", "is_default": True, "engine_mode": "plan"}]) == 'plan')
# a carrier NOT named boost but explicitly legacy_boost -> boost
check("name=Acme but engine_mode=legacy_boost -> boost",
      _resolve_carrier_mode([{"name": "Acme Wireless", "is_default": True,
                              "engine_mode": "legacy_boost"}]) == 'boost')
# the renamed-carrier bug: default carrier renamed to not contain 'boost', but still the Boost engine
check("renamed Boost (name has no 'boost') still pays on boost via engine_mode",
      _resolve_carrier_mode([{"name": "House Mobile", "is_default": True,
                              "engine_mode": "legacy_boost"}]) == 'boost')

print("── C. default carrier's engine_mode is consulted before a non-default's ──")
carriers = [
    {"name": "Total", "is_default": True, "engine_mode": "plan"},
    {"name": "Boost", "is_default": False, "engine_mode": "legacy_boost"},
]
check("default(plan) wins over non-default(legacy_boost) -> plan",
      _resolve_carrier_mode(carriers) == 'plan')
# default has NO explicit mode, a non-default does -> use the non-default's explicit value
carriers2 = [
    {"name": "Mystery", "is_default": True},                                  # no engine_mode
    {"name": "Whatever", "is_default": False, "engine_mode": "plan"},
]
check("default blank, non-default explicit plan -> plan (explicit beats name-heuristic)",
      _resolve_carrier_mode(carriers2) == 'plan')

print("── D. NULL / blank engine_mode = 'no explicit choice' -> fall back to name ──")
check("engine_mode None -> falls back (Boost name -> boost)",
      _resolve_carrier_mode([{"name": "Boost", "is_default": True, "engine_mode": None}]) == 'boost')
check("engine_mode '' -> falls back (Total name -> plan)",
      _resolve_carrier_mode([{"name": "Total", "is_default": True, "engine_mode": ""}]) == 'plan')

print("── E. normalizers ──")
check("_norm 'legacy_boost' -> boost", _norm_engine_mode("legacy_boost") == 'boost')
check("_norm 'boost' -> boost", _norm_engine_mode("boost") == 'boost')
check("_norm 'plan' -> plan", _norm_engine_mode("plan") == 'plan')
check("_norm junk -> None", _norm_engine_mode("weird") is None)
check("_norm None -> None", _norm_engine_mode(None) is None)
check("_clean input 'plan' -> plan", _clean_engine_mode_in("plan") == 'plan')
check("_clean input 'boost' -> legacy_boost (stored form)", _clean_engine_mode_in("boost") == 'legacy_boost')
check("_clean input '' -> None (explicit clear)", _clean_engine_mode_in("") is None)
try:
    _clean_engine_mode_in("nonsense")
    check("_clean rejects junk", False, "no raise")
except Exception:
    check("_clean rejects junk", True)

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
