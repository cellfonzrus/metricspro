"""HARNESS — a manager-incentive pre-fill FAILS CLOSED when the manager has no stores.

`_mi_resolve_numbers` (router) pre-fills a manager's actuals + qualifier metrics by rolling each one up
ACROSS THE STORES THEY MANAGE, and its contract is explicit: a metric with no data source "is left
UNRESOLVED … never silently guessed."

Every one of those roll-ups is a sum/average over the store set, so an EMPTY set makes each aggregation
land on a vacuous 0 instead of failing — and the per-metric blocks then recorded it as `resolved`. A
caller that forwards the pre-fill straight into /compute (the Compute tab does) turned that into a $0
payout that read as legitimately earned rather than "could not be determined".

LIVE PROOF that this was real (org 854f6d7b, period 2026-08, 2026-08-30): the org tree resolved 0 stores
for every manager, yet resolve returned accessory_gp=0 under `resolved` with `unresolved` empty.

All PURE — no DB, no network.

  python3 backend/harness_mi_resolve_fail_closed.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.commcalc import management_incentive as mi  # noqa: E402

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


def prefill():
    """The shape _mi_resolve_numbers builds after its per-metric blocks ran over an EMPTY store set:
    every roll-up summed nothing, produced 0, and was recorded as resolved."""
    return {
        "store_codes": [], "manager_store_count": 0,
        "actuals": {"accessory_gp": 0.0, "edge_count": 0.0},
        "qualifier_values": {"cash_deposit": 0.0, "Inventory": 0.0},
        "derived": {}, "resolved": ["accessory_gp", "edge_count", "cash_deposit", "Inventory"],
        "unresolved": [], "notes": {},
    }


print("── A. no stores → every vacuous roll-up is demoted to unresolved ──")
out = mi.demote_vacuous_when_no_stores(prefill(), has_stores=False)
check("nothing is left marked resolved", out["resolved"] == [], out["resolved"])
check("every key moved to unresolved",
      sorted(out["unresolved"]) == ["Inventory", "accessory_gp", "cash_deposit", "edge_count"],
      out["unresolved"])
check("the vacuous $0 actuals are DROPPED (not handed on as authoritative)",
      out["actuals"] == {}, out["actuals"])
check("the vacuous qualifier values are DROPPED", out["qualifier_values"] == {}, out["qualifier_values"])
check("every demoted key carries an explanatory note",
      all(k in out["notes"] for k in out["unresolved"]), out["notes"])
check("the note says WHY and what to do",
      "no store resolved" in out["notes"]["accessory_gp"]
      and "manually" in out["notes"]["accessory_gp"], out["notes"].get("accessory_gp"))

print("── B. the regression it guards: a $0 pre-fill can no longer be forwarded as a real actual ──")
# The Compute tab forwards resolve's `actuals` straight into /compute. Before the fix that carried
# accessory_gp=0 as a RESOLVED number; now the key is absent, so compute has nothing to silently pay on.
check("accessory_gp is absent from actuals, so /compute cannot receive a fabricated 0",
      "accessory_gp" not in out["actuals"])
check("accessory_gp is reported as unresolved to the UI", "accessory_gp" in out["unresolved"])

print("── C. no-op whenever the manager DOES have stores (byte-identical passthrough) ──")
have = prefill()
have["store_codes"] = ["CHI-01", "CHI-02"]
have["manager_store_count"] = 2
have["actuals"] = {"accessory_gp": 10000.0}
have["resolved"] = ["accessory_gp"]
before = {k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in have.items()}
after = mi.demote_vacuous_when_no_stores(have, has_stores=True)
check("resolved untouched when stores exist", after["resolved"] == before["resolved"], after["resolved"])
check("a real actual is never dropped", after["actuals"] == {"accessory_gp": 10000.0}, after["actuals"])
check("unresolved untouched", after["unresolved"] == before["unresolved"], after["unresolved"])
check("notes untouched", after["notes"] == before["notes"], after["notes"])

print("── D. degenerate inputs never raise ──")
check("nothing resolved + no stores → no-op",
      mi.demote_vacuous_when_no_stores(
          {"resolved": [], "unresolved": ["x"], "actuals": {}, "qualifier_values": {}, "notes": {}},
          False)["unresolved"] == ["x"])
check("empty dict is tolerated", mi.demote_vacuous_when_no_stores({}, False) == {})
check("a key already in unresolved is not duplicated",
      mi.demote_vacuous_when_no_stores(
          {"resolved": ["a"], "unresolved": ["a"], "actuals": {"a": 0},
           "qualifier_values": {}, "notes": {}}, False)["unresolved"] == ["a"])

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
