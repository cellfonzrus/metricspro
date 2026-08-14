"""Pure-logic harness for commission_engine.audit_flags — the core value of the Plan Assignment Audit.

Feeds fake `winner` + `considered` structures (the shape _resolve_plan_for(explain=True) returns) and
asserts the flag decision. No DB, no network — pure. Run: python backend/tests/test_assignment_audit_flags.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.modules.commcalc.commission_engine import audit_flags  # noqa: E402


def _names(flags):
    return {f["flag"] for f in flags}


def test_by_name_override_fires_when_pin_beats_a_location_plan():
    # Silvia: employee-scope pin on the NY plan wins; a DIFFERENT market plan also matched (Chicago).
    winner = {"plan_id": "ny", "plan_name": "Total Employee Comp NY", "scope": "employee",
              "scope_value": "Silvia Nava", "priority": 0, "rank": 4}
    considered = [
        {"plan_id": "ny", "plan_name": "Total Employee Comp NY", "scope": "employee",
         "scope_value": "Silvia Nava", "matched": True},
        {"plan_id": "chi", "plan_name": "Chicago Store Plan", "scope": "market",
         "scope_value": "Chicago", "matched": True},
    ]
    flags = audit_flags(winner, considered)
    assert "by_name_override" in _names(flags), flags
    ov = next(f for f in flags if f["flag"] == "by_name_override")["overridden_plans"]
    assert [o["plan_name"] for o in ov] == ["Chicago Store Plan"], ov
    assert ov[0]["scope"] == "market"


def test_by_name_override_fires_on_store_scope_too():
    winner = {"plan_id": "ny", "plan_name": "NY Plan", "scope": "employee",
              "scope_value": "Rep A", "priority": 0, "rank": 4}
    considered = [
        {"plan_id": "ny", "plan_name": "NY Plan", "scope": "employee", "matched": True},
        {"plan_id": "st", "plan_name": "Local Store Plan", "scope": "store",
         "scope_value": "123 Main St", "matched": True},
    ]
    assert "by_name_override" in _names(audit_flags(winner, considered))


def test_no_override_when_pin_is_the_only_match():
    # Employee pin wins, but no store/market plan matched -> NOT the dangerous pattern.
    winner = {"plan_id": "ny", "plan_name": "NY Plan", "scope": "employee", "rank": 4}
    considered = [
        {"plan_id": "ny", "plan_name": "NY Plan", "scope": "employee", "matched": True},
        {"plan_id": "chi", "plan_name": "Chicago Plan", "scope": "market",
         "scope_value": "Chicago", "matched": False},
    ]
    assert "by_name_override" not in _names(audit_flags(winner, considered))


def test_no_override_when_same_plan_has_both_assignments():
    # Same plan pinned by name AND by market -> nothing is actually overridden.
    winner = {"plan_id": "p1", "plan_name": "Plan One", "scope": "employee", "rank": 4}
    considered = [
        {"plan_id": "p1", "plan_name": "Plan One", "scope": "employee", "matched": True},
        {"plan_id": "p1", "plan_name": "Plan One", "scope": "market",
         "scope_value": "Chicago", "matched": True},
    ]
    assert "by_name_override" not in _names(audit_flags(winner, considered))


def test_no_override_when_winner_is_a_store_plan():
    # Store-scope winner (no by-name pin) is the healthy case.
    winner = {"plan_id": "st", "plan_name": "Store Plan", "scope": "store", "rank": 2}
    considered = [
        {"plan_id": "st", "plan_name": "Store Plan", "scope": "store", "matched": True},
        {"plan_id": "mk", "plan_name": "Market Plan", "scope": "market", "matched": True},
    ]
    assert _names(audit_flags(winner, considered)) & {"by_name_override"} == set()


def test_no_plan_flag():
    flags = audit_flags(None, [])
    assert _names(flags) == {"no_plan"}


def test_location_mismatch_low_confidence_hint():
    # Winning plan names a market ("NY") that is not the rep's own market (Chicago).
    winner = {"plan_id": "ny", "plan_name": "Total Employee Comp NY", "scope": "employee", "rank": 4}
    considered = [{"plan_id": "ny", "plan_name": "Total Employee Comp NY",
                   "scope": "employee", "matched": True}]
    flags = audit_flags(winner, considered, rep_market="Chicago",
                        known_markets=["NY", "Chicago", "Dallas"])
    assert "location_mismatch" in _names(flags), flags


def test_no_location_mismatch_when_plan_names_own_market():
    winner = {"plan_id": "chi", "plan_name": "Chicago Comp", "scope": "employee", "rank": 4}
    flags = audit_flags(winner, [{"plan_id": "chi", "plan_name": "Chicago Comp",
                                  "scope": "employee", "matched": True}],
                        rep_market="Chicago", known_markets=["NY", "Chicago"])
    assert "location_mismatch" not in _names(flags)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll {len(fns)} assignment-audit flag tests passed.")
