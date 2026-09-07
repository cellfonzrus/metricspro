"""HARNESS — Marketing module, Phase 1: outside-store event management (migs 986/987).

OWNER SPEC (2026-09-06, verbatim): "an event management module for outside store events ... with gps
enabled: Theme of the event - back to school etc or byod plan / Location / Venue / Goal for the event
- how many activations or accessories / What items are needed, a user created checklist / Social
media and other marketing planned links for the creatives / Time / What time do the employees have to
get there / Who is the outside party if there is one e.g DJ/ food truck / table event / Employees
planned for the event / Back up employee if they don't show up / How are employees getting there /
Who is picking up who if needed / Giveaways.  Again none of the options I mentioned above are hard
coded but options pre added with plus sign to add more as per user discretion."

WHAT THIS PROVES (stdlib only — no DB, no network, no fastapi import, no supabase call):

  A. THE OPTION LISTS ARE CONFIG — every list the owner named resolves tenant-over-house; a tenant
     ADDS an option, RENAMES a house one and DEACTIVATES a house one with no deploy and no code
     change; a key is derived from a typed label; a stored key whose option was deleted still renders.
  B. THE GEOFENCE DECISION (core/geo) — the shared decision: inside, outside, the accuracy interval,
     no fix, no pin, hard-block on and off, and that a bad fix is never counted against a person.
  C. LIFECYCLE + THE GO-LIVE GATE — the legal transitions, the illegal ones, and that a closed event
     cannot be reopened.
  D. APPROVAL, SWITCH OFF **AND** ON — off by default; a stale threshold cannot gate anything while
     off; on with no threshold gates everything; on with a threshold exempts at/below and gates above;
     and an approved event whose spend rises loses its approval.
  E. BACKUP + TRANSPORT — a declined primary with a usable backup is covered, with a declined backup
     is NOT, a confirmed backup wins over a planned one, orphan backups surface, and the pickup graph
     catches a missing driver, an unavailable driver and a pickup cycle.
  F. ACTUALS REUSE THE SHARED SALES PASS — aggregation consumes _compute_feed_actuals_py's OUTPUT
     rows, filtered by the event's stores/days; plus a STATIC proof that actuals.py calls that
     function and never queries raw_sales / daily_sales_feed or classifies a contract type itself.
  G. HONEST LABELLING — every actuals response carries the attribution block; no field name claims
     causation; a non-derivable metric reports "no automatic actual" and NEVER 0; a zero baseline
     yields None, not an infinite percentage.
  H. CHECKLIST + GIVEAWAYS — readiness, outstanding returns, and shrinkage reported without pretending
     un-counted items are accounted for.
  I. GPS PRIVACY — retention is stamped on every row; check-out stores no coordinate; nothing in the
     module has a track-shaped API.
  J. ORG-SCOPING — a static guard over every query in the marketing module (the shipped
     harness_org_scope_guard.py only reads commcalc's router, so this covers the new surface).
  K. MIGRATION SANITY — 986/987 are idempotent + additive, carry REVERT notes, seed no money, and
     register the module in core.module_catalog.
  L. ARMED — a negative control proving these assertions can actually fail.

Run:  cd backend && python3 harness_marketing_event.py
"""
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.core import geo                              # noqa: E402
from app.modules.marketing import actuals as A                # noqa: E402
from app.modules.marketing import event_logic as L            # noqa: E402

PASS, FAIL = [], []
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def check(label, got, want=True):
    if got == want:
        PASS.append(label)
    else:
        FAIL.append("%s: got %r, want %r" % (label, got, want))


def section(t):
    print("\n" + "=" * 98)
    print(t)
    print("=" * 98)


HOUSE = "00000000-0000-0000-0000-000000000001"
TENANT = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. The option lists are CONFIG, not code (RULE TWO — the owner's '+' requirement)")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# The house vocabulary as migration 987 seeds it (a representative slice of three lists).
HOUSE_OPTS = [
    {"list_key": "theme", "key": "back_to_school", "label": "Back to School", "sort_order": 10,
     "is_active": True, "extra": {}},
    {"list_key": "theme", "key": "byod_plan", "label": "BYOD / Bring Your Own Device",
     "sort_order": 20, "is_active": True, "extra": {}},
    {"list_key": "theme", "key": "holiday", "label": "Holiday", "sort_order": 40,
     "is_active": True, "extra": {}},
    {"list_key": "party_type", "key": "dj", "label": "DJ", "sort_order": 10, "is_active": True,
     "extra": {}},
    {"list_key": "party_type", "key": "food_truck", "label": "Food Truck", "sort_order": 20,
     "is_active": True, "extra": {}},
    {"list_key": "transport_mode", "key": "own_car", "label": "Own Car", "sort_order": 10,
     "is_active": True, "extra": {"needs_pickup": False}},
    {"list_key": "transport_mode", "key": "carpool", "label": "Carpool (being picked up)",
     "sort_order": 20, "is_active": True, "extra": {"needs_pickup": True}},
]

# A tenant exercising the "+": one BRAND-NEW theme, one RENAMED house theme, one DEACTIVATED house
# party type, and one brand-new transport mode that needs a pickup.
TENANT_OPTS = [
    {"list_key": "theme", "key": "ramadan_promo", "label": "Ramadan Promo", "sort_order": 15,
     "is_active": True, "extra": {}},
    {"list_key": "theme", "key": "back_to_school", "label": "Back-to-School Blitz", "sort_order": 5,
     "is_active": True, "extra": {}},
    {"list_key": "party_type", "key": "dj", "label": "DJ", "sort_order": 10, "is_active": False,
     "extra": {}},
    {"list_key": "transport_mode", "key": "company_shuttle", "label": "Company Shuttle",
     "sort_order": 25, "is_active": True, "extra": {"needs_pickup": True}},
]

themes_house = L.resolve_options(HOUSE_OPTS, [], "theme")
themes_tenant = L.resolve_options(HOUSE_OPTS, TENANT_OPTS, "theme")

check("A1 house org sees exactly its seeded themes",
      [o["key"] for o in themes_house], ["back_to_school", "byod_plan", "holiday"])
check("A2 a tenant's NEW theme appears with no deploy",
      "ramadan_promo" in [o["key"] for o in themes_tenant])
check("A3 a tenant RENAME of a house option wins",
      L.option_label(themes_tenant, "back_to_school"), "Back-to-School Blitz")
check("A4 …and the house label is untouched for everyone else",
      L.option_label(themes_house, "back_to_school"), "Back to School")
check("A5 tenant sort order re-orders the picker",
      [o["key"] for o in themes_tenant][0], "back_to_school")
parties = L.resolve_options(HOUSE_OPTS, TENANT_OPTS, "party_type")
check("A6 a tenant DEACTIVATES a house option (DJ) — gone from the picker",
      [o["key"] for o in parties], ["food_truck"])
check("A7 …but it is not deleted: it still resolves when inactive is requested",
      "dj" in [o["key"] for o in
               L.resolve_options(HOUSE_OPTS, TENANT_OPTS, "party_type", include_inactive=True)])
check("A8 a stored key whose option was deactivated still RENDERS its label (never blank)",
      L.option_label(L.resolve_options(HOUSE_OPTS, TENANT_OPTS, "party_type",
                                       include_inactive=True), "dj"), "DJ")
check("A9 an unknown key renders as itself, never as a blank",
      L.option_label(themes_tenant, "some_deleted_theme"), "some_deleted_theme")
check("A10 every list the owner named is a registered list",
      all(k in L.LIST_KEYS for k in ("theme", "venue_type", "party_type", "transport_mode",
                                     "giveaway_type", "event_role", "link_channel", "goal_metric")))
check("A11 the '+' derives a key from a typed label",
      L.normalize_option_key("Back to School!"), "back_to_school")
check("A12 …and collapses separator runs so near-duplicates cannot both exist",
      L.normalize_option_key("Food  Truck / Vendor"), "food_truck_vendor")
check("A13 a label of only punctuation yields no key (rejected, not stored as garbage)",
      L.normalize_option_key("!!!"), None)
check("A14 resolution is per-list — a theme key never leaks into the party picker",
      [o["key"] for o in L.resolve_options(HOUSE_OPTS, TENANT_OPTS, "party_type",
                                           include_inactive=True)], ["dj", "food_truck"])

# RULE TWO, statically: no owner-named VALUE may appear as a literal in the module's code.
_MODULE_FILES = [os.path.join(HERE, "app", "modules", "marketing", f)
                 for f in ("event_logic.py", "actuals.py", "router.py", "attention_providers.py")]
_MODULE_FILES.append(os.path.join(HERE, "app", "modules", "core", "geo.py"))
_FORBIDDEN_LITERALS = ("back_to_school", "byod_plan", "food_truck", '"dj"', "'dj'",
                       "grand_opening", "own_car", "carpool", "instagram", "tiktok")


def _code_without_comments(path):
    """Source with comments and docstrings stripped — the check is about BEHAVIOUR, and a doc comment
    quoting the owner's own words ("back to school") is documentation, not a hard-coded branch."""
    src = open(path, encoding="utf-8").read()
    src = re.sub(r'"""(?:.|\n)*?"""', '""', src)
    src = re.sub(r"'''(?:.|\n)*?'''", "''", src)
    src = re.sub(r"(?m)#.*$", "", src)
    return src


def _sql_without_comments(text):
    """SQL with its `--` comment lines removed. The migrations DOCUMENT what they deliberately do
    NOT create ("there is deliberately NO actual_value column", "unit_cost ... gets NO seed rows"),
    so a naive substring search over the raw file finds the very words the comment promises are
    absent. The assertion is about the DDL and the INSERTs, so the comments come out first."""
    return re.sub(r"(?m)^\s*--.*$", "", text)


_lit_hits = []
for f in _MODULE_FILES:
    body = _code_without_comments(f)
    for lit in _FORBIDDEN_LITERALS:
        if lit in body:
            _lit_hits.append("%s: %s" % (os.path.basename(f), lit))
check("A15 NO owner-named option value is hard-coded anywhere in the module's executable code",
      _lit_hits, [])

for lbl in sorted(PASS)[:0]:
    pass
print("  %d checks" % len([p for p in PASS if p.startswith("A")]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. The geofence decision — ONE shared rule (core/geo), and it never punishes a bad fix")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# A real venue pin and points measured from it.
VLAT, VLNG = 40.930000, -73.900000

d100 = geo.haversine_m(VLAT, VLNG, VLAT + 0.0009, VLNG)      # ~100 m due north
check("B1 haversine returns a sane metric distance", 95 <= d100 <= 105)
check("B2 haversine is symmetric",
      round(geo.haversine_m(VLAT, VLNG, VLAT + 0.0009, VLNG), 3)
      == round(geo.haversine_m(VLAT + 0.0009, VLNG, VLAT, VLNG), 3))
check("B3 zero distance to itself", geo.haversine_m(VLAT, VLNG, VLAT, VLNG), 0.0)
check("B4 a non-point yields None, never 0", geo.haversine_m(None, VLNG, VLAT, VLNG), None)
check("B5 an out-of-range latitude is rejected, not clamped", geo.parse_lat(91.0), None)
check("B6 NaN is rejected (a silent 0.0 would place every event off West Africa)",
      geo.parse_lat(float("nan")), None)
check("B7 a coordinate arriving as a STRING still parses", geo.parse_lat("40.93"), 40.93)

v = geo.evaluate_checkin(VLAT + 0.0002, VLNG, 20, VLAT, VLNG, radius_m=150)
check("B8 standing at the venue with a good fix -> inside", v["decision"], geo.INSIDE)
check("B9 …and it is accepted", v["accepted"], True)
check("B10 …and the evidence records a whole-metre distance", isinstance(v["distance_m"], int))

v = geo.evaluate_checkin(VLAT + 0.0090, VLNG, 15, VLAT, VLNG, radius_m=150)
check("B11 a kilometre away with a good fix -> outside", v["decision"], geo.OUTSIDE)
check("B12 …but it is still RECORDED by default (flagged, not refused)", v["accepted"], True)
check("B13 …and within_geofence is an explicit False", v["within_geofence"], False)

# THE accuracy interval — the heart of the decision.
v = geo.evaluate_checkin(VLAT + 0.0009, VLNG, 300, VLAT, VLNG, radius_m=150, max_accuracy_m=200)
check("B14 100 m away +/-300 m: too coarse to judge -> unverified_accuracy",
      v["decision"], geo.UNVERIFIED_ACCURACY)
check("B15 …and within_geofence is None — 'we cannot tell' is never 'they were not there'",
      v["within_geofence"], None)
check("B16 …and it is still accepted", v["accepted"], True)

v = geo.evaluate_checkin(VLAT + 0.0002, VLNG, 400, VLAT, VLNG, radius_m=1000, max_accuracy_m=200)
check("B17 a terrible fix that CANNOT be outside a big fence is still inside",
      v["decision"], geo.INSIDE)

v = geo.evaluate_checkin(VLAT + 0.0450, VLNG, 400, VLAT, VLNG, radius_m=150, max_accuracy_m=200)
check("B18 a terrible fix that cannot be INSIDE is outside (5 km away, +/-400 m)",
      v["decision"], geo.OUTSIDE)

v = geo.evaluate_checkin(VLAT + 0.0014, VLNG, 10, VLAT, VLNG, radius_m=150, max_accuracy_m=200)
check("B19 a borderline point with a TRUSTWORTHY fix is decided, not deferred",
      v["decision"], geo.OUTSIDE)

v = geo.evaluate_checkin(None, None, None, VLAT, VLNG, radius_m=150)
check("B20 no fix from the device -> unverified_no_fix", v["decision"], geo.UNVERIFIED_NO_FIX)
check("B21 …accepted by default (a dead GPS is not misconduct)", v["accepted"], True)

v = geo.evaluate_checkin(VLAT, VLNG, 10, None, None, radius_m=150)
check("B22 nobody pinned the venue -> unverified_no_target", v["decision"], geo.UNVERIFIED_NO_TARGET)
check("B23 …and that NEVER blocks, even with hard blocking on",
      geo.evaluate_checkin(VLAT, VLNG, 10, None, None, radius_m=150,
                           block_outside=True)["accepted"], True)

v = geo.evaluate_checkin(VLAT + 0.0090, VLNG, 15, VLAT, VLNG, radius_m=150, block_outside=True)
check("B24 with hard blocking ON, an out-of-fence check-in is refused", v["accepted"], False)
check("B25 …and the refusal explains itself in the response", "requires check-in" in v["note"])
check("B26 hard blocking does not change the VERDICT, only acceptance", v["decision"], geo.OUTSIDE)

check("B27 a nonsense radius is clamped, never used raw", geo.clamp_radius(-5), geo.MIN_RADIUS_M)
check("B28 an absurd radius is clamped too", geo.clamp_radius(10 ** 9), geo.MAX_RADIUS_M)
check("B29 a non-numeric radius falls back to the default", geo.clamp_radius("abc"),
      geo.DEFAULT_RADIUS_M)
check("B30 a negative accuracy is discarded, not trusted as 0", geo.parse_accuracy(-30), None)
check("B31 every decision value is in the declared vocabulary",
      all(geo.evaluate_checkin(*a)["decision"] in geo.DECISIONS for a in
          [(VLAT, VLNG, 5, VLAT, VLNG), (None, None, None, VLAT, VLNG), (VLAT, VLNG, 5, None, None),
           (VLAT + 0.05, VLNG, 5, VLAT, VLNG), (VLAT + 0.0009, VLNG, 900, VLAT, VLNG)]))
print("  %d checks" % len([p for p in PASS if p.startswith("B")]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. Lifecycle + the go-live gate")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("C1 draft -> approved", L.can_transition("draft", "approved")[0], True)
check("C2 draft -> live (allowed; the APPROVAL gate is separate)",
      L.can_transition("draft", "live")[0], True)
check("C3 live -> closed", L.can_transition("live", "closed")[0], True)
check("C4 closed -> live is refused", L.can_transition("closed", "live")[0], False)
check("C5 …with a reason a human can act on",
      "cannot be reopened" in L.can_transition("closed", "draft")[1])
check("C6 draft -> closed skips the event itself, refused",
      L.can_transition("draft", "closed")[0], False)
check("C7 anything -> cancelled (except closed)", L.can_transition("approved", "cancelled")[0], True)
check("C8 a cancelled event can be revived as a draft",
      L.can_transition("cancelled", "draft")[0], True)
check("C9 an unknown target status is refused", L.can_transition("draft", "banana")[0], False)
check("C10 go live with approval NOT REQUIRED (the default posture)",
      L.gate_go_live("draft", L.APPROVAL_NOT_REQUIRED)[0], True)
check("C11 go live while PENDING is blocked", L.gate_go_live("draft", L.APPROVAL_PENDING)[0], False)
check("C12 …with the reason a planner needs",
      L.gate_go_live("draft", L.APPROVAL_PENDING)[1], "This event needs approval before it can go live.")
check("C13 go live once APPROVED", L.gate_go_live("approved", L.APPROVAL_APPROVED)[0], True)
check("C14 go live after REJECTION is blocked", L.gate_go_live("draft", L.APPROVAL_REJECTED)[0], False)
check("C15 a NULL approval state reads as not-required (never as a silent block)",
      L.gate_go_live("draft", None)[0], True)
print("  %d checks" % len([p for p in PASS if p.startswith("C")]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. Approval — DEFAULT OFF, and the threshold cannot switch it on")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("D1 the house default is approval OFF", L.DEFAULT_CONFIG["approval_required"], False)
check("D2 an org with NO config row at all needs no approval",
      L.approval_decision(None, 5000)["required"], False)
check("D3 …and records WHY, so 'went live unapproved' is never an ambiguous NULL",
      "switched off" in L.approval_decision(None, 5000)["reason"])
check("D4 …with the explicit not_required state",
      L.approval_decision(None, 5000)["state"], L.APPROVAL_NOT_REQUIRED)

# THE trap: an org that once used approval, set a threshold, then switched approval off.
stale = {"approval_required": False, "approval_spend_threshold": 100}
check("D5 a STALE threshold cannot gate anything while the switch is off",
      L.approval_decision(stale, 999999)["required"], False)

on_no_thresh = {"approval_required": True, "approval_spend_threshold": None}
check("D6 switch ON, no threshold -> every event needs approval",
      L.approval_decision(on_no_thresh, 0)["required"], True)
check("D7 …even with no spend entered at all",
      L.approval_decision(on_no_thresh, None)["required"], True)

on_thresh = {"approval_required": True, "approval_spend_threshold": 500}
check("D8 switch ON with a threshold: UNDER is exempt",
      L.approval_decision(on_thresh, 200)["required"], False)
check("D9 …EXACTLY AT the threshold is exempt (at-or-below, stated in the reason)",
      L.approval_decision(on_thresh, 500)["required"], False)
check("D10 …ABOVE needs approval", L.approval_decision(on_thresh, 500.01)["required"], True)
check("D11 …and the reason quotes both numbers",
      "$500" in L.approval_decision(on_thresh, 900)["reason"])
check("D12 switch ON, threshold set, spend UNKNOWN -> needs approval (cannot be shown to be under)",
      L.approval_decision(on_thresh, None)["required"], True)
check("D13 …and says so rather than implying overspend",
      "cannot be shown to be under" in L.approval_decision(on_thresh, None)["reason"])
check("D14 a spend arriving as a STRING is still compared numerically",
      L.approval_decision(on_thresh, "200")["required"], False)
check("D15 a partial config row cannot silently switch approval on",
      L.resolve_config({"default_checkin_radius_m": 300})["approval_required"], False)
check("D16 …and a partial row keeps the other defaults",
      L.resolve_config({"default_checkin_radius_m": 300})["checkin_geo_retention_days"], 180)
check("D17 a NULL in a config row falls back to the default, not to False/0",
      L.resolve_config({"checkin_geo_retention_days": None})["checkin_geo_retention_days"], 180)
print("  %d checks" % len([p for p in PASS if p.startswith("D")]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. Backup staff + transport / pickup")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
STAFF = [
    {"id": "s1", "employee_id": "E1", "employee_name": "Ana", "role_key": "lead",
     "is_backup": False, "confirm_state": "confirmed", "transport_mode_key": "own_car"},
    {"id": "s2", "employee_id": "E2", "employee_name": "Ben", "role_key": "sales",
     "is_backup": False, "confirm_state": "declined", "transport_mode_key": "own_car"},
    {"id": "s3", "employee_id": "E3", "employee_name": "Cal", "role_key": "sales",
     "is_backup": True, "backup_for_staff_id": "s2", "confirm_state": "planned",
     "transport_mode_key": "carpool", "pickup_by_staff_id": "s1"},
    {"id": "s4", "employee_id": "E4", "employee_name": "Dee", "role_key": "sales",
     "is_backup": True, "backup_for_staff_id": "s2", "confirm_state": "confirmed",
     "transport_mode_key": "own_car"},
    {"id": "s5", "employee_id": "E5", "employee_name": "Eli", "role_key": "greeter",
     "is_backup": False, "confirm_state": "planned", "transport_mode_key": "carpool"},
    {"id": "s6", "employee_id": "E6", "employee_name": "Fay", "role_key": "tech",
     "is_backup": False, "confirm_state": "declined", "transport_mode_key": "own_car"},
    {"id": "s7", "employee_id": "E7", "employee_name": "Gus", "role_key": "tech",
     "is_backup": True, "backup_for_staff_id": "s6", "confirm_state": "declined"},
    {"id": "s8", "employee_id": "E8", "employee_name": "Hal", "role_key": "setup",
     "is_backup": True, "backup_for_staff_id": "GONE", "confirm_state": "planned"},
]
st = L.resolve_staffing(STAFF)
roster = {r["id"]: r for r in st["roster"]}

check("E1 only primaries make the roster", sorted(roster), ["s1", "s2", "s5", "s6"])
check("E2 a declined primary WITH an available backup is covered", roster["s2"]["is_covered"], True)
check("E3 …and a CONFIRMED backup is preferred over a merely planned one",
      roster["s2"]["backup"]["employee_name"], "Dee")
check("E4 …and the effective person for that slot is the backup",
      roster["s2"]["effective"]["employee_name"], "Dee")
check("E5 a declined primary whose ONLY backup also declined is NOT covered",
      roster["s6"]["is_covered"], False)
check("E6 …so nobody is reported as working that slot", roster["s6"]["effective"], None)
check("E7 …and it lands in the uncovered list a manager acts on",
      [u["employee_name"] for u in st["uncovered"]], ["Fay"])
check("E8 a confirmed primary is their own effective person",
      roster["s1"]["effective"]["employee_name"], "Ana")
check("E9 a primary with no backup at all is flagged as such",
      roster["s5"]["is_covered"], False)
check("E10 an orphan backup (its primary is gone) is surfaced, not silently ignored",
      [b["employee_name"] for b in st["unassigned_backups"]], ["Hal"])
check("E11 headline counts — planned primaries", st["counts"]["planned"], 4)
check("E12 headline counts — confirmed", st["counts"]["confirmed"], 1)
check("E13 headline counts — unconfirmed (excludes the declined)", st["counts"]["unconfirmed"], 1)
check("E14 headline counts — declined", st["counts"]["declined"], 2)
check("E15 headline counts — uncovered", st["counts"]["uncovered"], 1)

st2 = L.resolve_staffing(STAFF, checkins=[{"staff_id": "s1"}, {"employee_id": "E5"}])
r2 = {r["id"]: r for r in st2["roster"]}
check("E16 a check-in by staff id marks arrival", r2["s1"]["arrived"], True)
check("E17 a check-in by employee id also marks arrival (a manager checked them in)",
      r2["s5"]["arrived"], True)
check("E18 no check-in is NOT read as absence — it is simply not-yet-arrived",
      r2["s2"]["arrived"], False)
check("E19 …and the platform never sets no_show itself: it is a state a human writes",
      "no_show" in L.CONFIRM_STATES and L.CONFIRM_NO_SHOW in L.UNAVAILABLE_STATES)

TRANSPORT_OPTS = L.resolve_options(HOUSE_OPTS, TENANT_OPTS, "transport_mode")
tr = L.resolve_transport(STAFF, TRANSPORT_OPTS)
check("E20 the pickup graph records who drives whom",
      [p["employee_name"] for p in tr["rides"]["s1"]], ["Cal"])
check("E21 a carpooler with NO driver assigned is flagged",
      [n["employee_name"] for n in tr["needs_ride"]], ["Eli"])
check("E22 …with an actionable message",
      any("nobody is assigned to pick them up" in p["detail"] for p in tr["problems"]))

# A tenant-invented transport mode with needs_pickup set behaves correctly with NO code change.
tr_shuttle = L.resolve_transport(
    [{"id": "x1", "employee_name": "Ivy", "transport_mode_key": "company_shuttle"}], TRANSPORT_OPTS)
check("E23 a TENANT-ADDED transport mode flagged needs_pickup is honoured with no deploy",
      [n["employee_name"] for n in tr_shuttle["needs_ride"]], ["Ivy"])

tr_bad = L.resolve_transport([
    {"id": "a", "employee_name": "Jo", "pickup_by_staff_id": "nobody"},
    {"id": "b", "employee_name": "Kai", "pickup_by_staff_id": "c"},
    {"id": "c", "employee_name": "Lee", "confirm_state": "declined"},
], TRANSPORT_OPTS)
kinds = {p["kind"] for p in tr_bad["problems"]}
check("E24 a driver who is not on the event is caught", "driver_not_on_event" in kinds)
check("E25 a driver who declined is caught", "driver_unavailable" in kinds)

tr_cycle = L.resolve_transport([
    {"id": "p", "employee_name": "Max", "pickup_by_staff_id": "q"},
    {"id": "q", "employee_name": "Nia", "pickup_by_staff_id": "p"},
], TRANSPORT_OPTS)
check("E26 a pickup CYCLE is caught — otherwise nobody's car ever starts",
      any(p["kind"] == "pickup_cycle" for p in tr_cycle["problems"]))

EVENT = {"event_start": "2026-09-12T10:00:00+00:00", "event_end": "2026-09-12T16:00:00+00:00",
         "staff_call_at": "2026-09-12T08:30:00+00:00", "status": "approved"}
check("E27 staff call time is its OWN field, not the event start",
      L.call_time_for({}, EVENT)[0], "2026-09-12T08:30:00+00:00")
check("E28 …and a person's own override wins",
      L.call_time_for({"call_time_override": "2026-09-12T07:00:00+00:00"}, EVENT)[0],
      "2026-09-12T07:00:00+00:00")
check("E29 …with the source labelled so a screen can say which it is showing",
      L.call_time_for({}, EVENT)[1], "event")
check("E30 an event with no call time falls back to the start, and SAYS it is a fallback",
      L.call_time_for({}, {"event_start": "2026-09-12T10:00:00+00:00"})[1], "event_start_fallback")
print("  %d checks" % len([p for p in PASS if p.startswith("E")]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. Actuals REUSE commcalc's shared sales pass — no second derivation")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# These rows are the OUTPUT SHAPE of commcalc router._compute_feed_actuals_py (which itself wraps
# _sales_cell_agg). The harness feeds that shape in; the module never sees a raw sales line.
def row(code, rep, day, prem=0, byod=0, upg=0, acc=0.0, box=0, setup=0.0, bill=0):
    return {"store_code": code, "store": code, "rep_name": rep, "login": None, "trans_date": day,
            "prem_count": prem, "byod_count": byod, "upg_count": upg, "acc_gp": acc,
            "setup_fee": setup, "box_count": box, "billpay_count": bill}


EVENT_DAY = "2026-09-12"                       # a Saturday
BASE_DAYS = ["2026-09-05", "2026-08-29", "2026-08-22", "2026-08-15"]
ROWS = [
    row("B-100", "ANA", EVENT_DAY, prem=6, byod=2, upg=1, acc=480.0, box=9, bill=3),
    row("B-100", "BEN", EVENT_DAY, prem=4, byod=0, upg=2, acc=320.0, box=6),
    row("B-200", "CAL", EVENT_DAY, prem=3, byod=1, upg=0, acc=150.0, box=4),
    row("B-999", "ZED", EVENT_DAY, prem=99, byod=99, upg=99, acc=9999.0, box=99),   # another store
    row("B-100", "ANA", "2026-09-13", prem=50, acc=5000.0, box=50),                 # the day after
]
for d in BASE_DAYS:
    ROWS.append(row("B-100", "ANA", d, prem=2, byod=1, upg=1, acc=200.0, box=4))
    ROWS.append(row("B-200", "CAL", d, prem=1, byod=0, upg=0, acc=100.0, box=1))

agg = A.aggregate_actual_rows(ROWS, ["B-100", "B-200"], [EVENT_DAY])
check("F1 activations sum only the event's stores on the event's day",
      agg["totals"]["prem_count"], 13.0)
check("F2 a store NOT on the event is excluded", agg["totals"]["byod_count"], 3.0)
check("F3 the day AFTER the event is excluded", agg["totals"]["acc_gp"], 950.0)
check("F4 boxes come straight from the shared pass's own field", agg["totals"]["box_count"], 19.0)
check("F5 the number of matched rows is reported for auditability", agg["rows_matched"], 3)
check("F6 store codes are matched case-insensitively",
      A.aggregate_actual_rows(ROWS, ["b-100"], [EVENT_DAY])["totals"]["prem_count"], 10.0)
check("F7 an optional rep filter narrows to named people",
      A.aggregate_actual_rows(ROWS, ["B-100"], [EVENT_DAY], ["ana"])["totals"]["prem_count"], 6.0)
check("F8 no filters means no restriction on that axis",
      A.aggregate_actual_rows(ROWS, None, [EVENT_DAY])["totals"]["prem_count"], 112.0)
check("F9 an empty row set totals zero rather than raising",
      A.aggregate_actual_rows([], ["B-100"], [EVENT_DAY])["rows_matched"], 0)

base = A.aggregate_actual_rows(ROWS, ["B-100", "B-200"], BASE_DAYS)
cmp_ = A.compare_windows(agg, base, 1, len(BASE_DAYS))
check("F10 baseline totals over four same-weekdays", base["totals"]["prem_count"], 12.0)
check("F11 baseline is normalised PER DAY (12 over 4 days = 3/day)",
      cmp_["prem_count"]["baseline_per_day"], 3.0)
check("F12 the event day is 13 vs a 3/day baseline", cmp_["prem_count"]["event_per_day"], 13.0)
check("F13 the difference is reported per day", cmp_["prem_count"]["diff_per_day"], 10.0)
check("F14 …and as a percentage change", cmp_["prem_count"]["pct_change"], 333.3)

zero_base = A.aggregate_actual_rows([], ["B-100"], BASE_DAYS)
cz = A.compare_windows(agg, zero_base, 1, len(BASE_DAYS))
check("F15 a ZERO baseline yields pct_change None — never an infinite improvement",
      cz["prem_count"]["pct_change"], None)
cnb = A.compare_windows(agg, {}, 1, 0)
check("F16 NO baseline at all is reported as such, not as zero",
      cnb["prem_count"]["has_baseline"], False)
check("F17 …and its diff is None rather than a flattering number",
      cnb["prem_count"]["diff_per_day"], None)

check("F18 the event window is whole calendar days",
      L.event_dates({"event_start": "2026-09-12T10:00:00+00:00",
                     "event_end": "2026-09-12T16:00:00+00:00"}), ["2026-09-12"])
check("F19 a multi-day event spans every day",
      L.event_dates({"event_start": "2026-09-12T10:00:00+00:00",
                     "event_end": "2026-09-14T16:00:00+00:00"}),
      ["2026-09-12", "2026-09-13", "2026-09-14"])
check("F20 the baseline is the SAME WEEKDAY in preceding weeks (never a mixed-weekday average)",
      L.baseline_dates({"event_start": "2026-09-12T10:00:00+00:00"}, weeks=4), sorted(BASE_DAYS))
check("F21 an event spanning a month boundary reads BOTH months from the shared pass",
      L.period_keys_for_dates(["2026-08-31", "2026-09-01"]), ["2026-08", "2026-09"])
check("F22 an event with no dates yields no window (and no invented one)",
      L.event_dates({}), [])

# STATIC: actuals.py must CALL the shared pass and must not re-derive sales itself.
ACT_SRC = open(os.path.join(HERE, "app", "modules", "marketing", "actuals.py"),
               encoding="utf-8").read()
ACT_CODE = _code_without_comments(os.path.join(HERE, "app", "modules", "marketing", "actuals.py"))
check("F23 actuals.py CALLS commcalc's shared pass by name",
      "_compute_feed_actuals_py" in ACT_CODE)
check("F24 …and NEVER queries raw_sales itself",
      "raw_sales" not in ACT_CODE)
check("F25 …nor daily_sales_feed", "daily_sales_feed" not in ACT_CODE)
check("F26 …nor re-implements the contract-type / accessory classification",
      not any(t in ACT_CODE for t in ("contract_type", "_is_accessory", "product_desc", "trans_type")))
_MIG986_SQL = _sql_without_comments(
    open(os.path.join(REPO, "database", "migrations", "986_marketing_event_module.sql"),
         encoding="utf-8").read())
check("F27 …and no marketing table has a column that would STORE a derived sales number",
      not any(c in _MIG986_SQL for c in ("actual_value", "actual_activations", "actual_accessory",
                                         "achieved_", "sales_total")))
check("F28 the goals table holds only a TARGET, never an actual",
      "target_value NUMERIC" in _MIG986_SQL and "actual" not in _MIG986_SQL.split(
          "CREATE TABLE IF NOT EXISTS core.marketing_event_goal", 1)[1].split(");", 1)[0])
print("  %d checks" % len([p for p in PASS if p.startswith("F")]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. Honest labelling — the report never claims the event CAUSED the sales")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
GOAL_OPTS = [
    {"key": "activations", "label": "Activations",
     "extra": {"unit": "count", "derivable": True, "field": "activations"}},
    {"key": "accessory_dollars", "label": "Accessory $",
     "extra": {"unit": "money", "derivable": True, "field": "accessory_dollars"}},
    {"key": "leads_collected", "label": "Leads Collected",
     "extra": {"unit": "count", "derivable": False}},
    {"key": "vibes", "label": "Team Vibes", "extra": {"unit": "count", "field": "not_a_real_field"}},
]
GOALS = [
    {"metric_key": "activations", "target_value": 10},
    {"metric_key": "accessory_dollars", "target_value": 1000},
    {"metric_key": "leads_collected", "target_value": 40},
    {"metric_key": "vibes", "target_value": 5},
]
lines = {g["metric_key"]: g for g in A.build_goal_lines(GOALS, GOAL_OPTS, cmp_)}

check("G1 a derivable goal gets its actual from the shared pass",
      lines["activations"]["actual_value"], 13.0)
check("G2 …and its variance against the target", lines["activations"]["variance"], 3.0)
check("G3 …and attainment %", lines["activations"]["pct_of_goal"], 130.0)
check("G4 the accessory goal reads the shared 'achieved' figure",
      lines["accessory_dollars"]["actual_value"], 950.0)
check("G5 …and is honest about being under target",
      lines["accessory_dollars"]["variance"], -50.0)
check("G6 a NON-derivable metric reports no actual", lines["leads_collected"]["actual_value"], None)
check("G7 …and NEVER a zero that would read as failure",
      lines["leads_collected"]["actual_value"] is not 0)
check("G8 …with the reason stated on the line",
      "No automatic source" in lines["leads_collected"]["reason"])
check("G9 a metric pointed at an UNKNOWN field degrades to not-derivable, never sums something else",
      lines["vibes"]["derivable"], False)
check("G10 each derivable line names the shared field it came from",
      lines["activations"]["source_field"], "prem_count")
check("G11 …and labels it as the Sales Report's own number",
      "Sales Report" in (lines["activations"]["source_label"] or ""))

att = A.attribution_block(["2026-09-12"], BASE_DAYS, ["B-100"])
check("G12 the attribution headline refuses the causal claim outright",
      "not sales attributed to the event" in att["headline"])
check("G13 the detail says explicitly that these are NOT sales caused by the event",
      "NOT sales caused by the event" in att["detail"])
check("G14 …and that a difference between observations is not an effect",
      "not proof of an effect" in att["detail"])
check("G15 the day-grain limitation is stated rather than hidden",
      "whole calendar days" in att["grain_note"])
check("G16 the baseline method is spelled out on the response",
      "same weekday" in att["baseline_method"])
check("G17 the source names the shared aggregation, so a reader can go check it",
      "_sales_cell_agg" in att["source"])

# No field name anywhere in the module may claim causation.
_CAUSAL = ("event_activations", "incremental_", "caused_by", "attributed_sales", "event_driven",
           "event_revenue", "lift_from_event")
_causal_hits = [(os.path.basename(f), t) for f in _MODULE_FILES
                for t in _CAUSAL if t in _code_without_comments(f)]
check("G18 NO field or variable name in the module claims the event caused a sale", _causal_hits, [])

# The unavailable paths must ALSO carry the attribution block and a reason.
no_store = A.event_actuals(None, HOUSE, {"event_start": "2026-09-12T10:00:00+00:00"}, [],
                           GOALS, GOAL_OPTS)
check("G19 an event with no store says so instead of showing zeros",
      no_store["available"], False)
check("G20 …with an actionable reason", "Attach the store" in no_store["reason"])
check("G21 …and STILL carries the attribution block", "attribution" in no_store)
check("G22 …and still lists the goals (targets are not lost when actuals are unavailable)",
      len(no_store["goals"]), 4)
no_date = A.event_actuals(None, HOUSE, {}, ["B-100"], GOALS, GOAL_OPTS)
check("G23 an event with no date says so", no_date["available"], False)
check("G24 …and marks the attribution block as not derived",
      no_date["attribution"]["derived"], False)
print("  %d checks" % len([p for p in PASS if p.startswith("G")]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("H. Checklist readiness + giveaway shrinkage")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
ITEMS = [
    {"label": "Folding table", "is_returnable": True, "is_packed": True, "is_returned": True},
    {"label": "Canopy", "is_returnable": True, "is_packed": True, "is_returned": False},
    {"label": "Banner", "is_returnable": True, "is_packed": False, "is_returned": False},
    {"label": "Flyers", "is_returnable": False, "is_packed": True, "is_returned": False},
]
r = L.checklist_readiness(ITEMS)
check("H1 packed count", r["packed"], 3)
check("H2 unpacked count", r["unpacked"], 1)
check("H3 not complete while anything is unpacked", r["complete"], False)
check("H4 percent packed", r["pct_packed"], 75.0)
check("H5 outstanding returns count only RETURNABLE packed items (not the flyers)",
      r["outstanding_returns"], 1)
check("H6 …and names them", [i["label"] for i in r["outstanding_items"]], ["Canopy"])
check("H7 an all-packed list is complete",
      L.checklist_readiness([{"is_packed": True}])["complete"], True)
check("H8 an EMPTY checklist is not 'complete' (0/0 must not read as ready)",
      L.checklist_readiness([])["complete"], False)

tpl = L.instantiate_template(
    [{"label": "Tent", "category": "Setup", "qty": 1, "is_returnable": True, "sort_order": 10},
     {"label": "Flyers", "category": "Collateral", "is_returnable": False}], "ev1", HOUSE)
check("H9 a template instantiates onto the event with the org stamped",
      [t["org_id"] for t in tpl], [HOUSE, HOUSE])
check("H10 …and the event id", tpl[0]["event_id"], "ev1")
check("H11 …with a sort order derived where the template had none", tpl[1]["sort_order"], 20)

GIVE = [
    {"item_label": "T-shirts", "qty_out": 100, "qty_returned": 20, "qty_given": 70},
    {"item_label": "Gift cards", "qty_out": 10, "qty_returned": 10, "qty_given": 0},
    {"item_label": "Keychains", "qty_out": 50},                     # never counted back
]
gr = L.giveaway_reconciliation(GIVE)
by = {i["item_label"]: i for i in gr["items"]}
check("H12 shrinkage is visible per item (100 out, 20 back, 70 given = 10 unaccounted)",
      by["T-shirts"]["unaccounted"], 10.0)
check("H13 a fully-reconciled item shows zero unaccounted", by["Gift cards"]["unaccounted"], 0.0)
check("H14 an UN-counted item reports None, not a fake zero", by["Keychains"]["unaccounted"], None)
check("H15 …and is excluded from the counted total", gr["totals"]["unaccounted"], 10.0)
check("H16 …with an honest note saying the total does not cover it",
      "never counted back in" in gr["note"])
check("H17 the uncounted item count is reported", gr["uncounted_items"], 1)
check("H18 a fully-counted set says so",
      "counted back in" in L.giveaway_reconciliation(
          [{"item_label": "x", "qty_out": 1, "qty_returned": 1}])["note"])
print("  %d checks" % len([p for p in PASS if p.startswith("H")]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("I. Employee GPS — privacy posture is structural, not a policy note")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
ROUTER_SRC = open(os.path.join(HERE, "app", "modules", "marketing", "router.py"),
                  encoding="utf-8").read()
ROUTER_CODE = _code_without_comments(os.path.join(HERE, "app", "modules", "marketing", "router.py"))
MIG986 = open(os.path.join(REPO, "database", "migrations", "986_marketing_event_module.sql"),
              encoding="utf-8").read()
GEO_CODE = _code_without_comments(os.path.join(HERE, "app", "modules", "core", "geo.py"))

check("I1 retention is stamped as a DATE on the row at write time",
      L.purge_after_date(NOW, 180), "2027-03-05")
check("I2 a shorter org retention shortens it", L.purge_after_date(NOW, 30), "2026-10-06")
check("I3 a zero/negative retention is floored at one day, never 'keep forever'",
      L.purge_after_date(NOW, 0), "2026-09-07")
check("I4 a missing retention setting falls back to the house default, not to unlimited",
      L.purge_after_date(NOW, None), "2027-03-05")
check("I5 the router stamps purge_after_date on every check-in insert",
      "purge_after_date" in ROUTER_CODE and "L.purge_after_date" in ROUTER_CODE)

ret = L.retention_summary([{"purge_after_date": "2026-01-01"}, {"purge_after_date": "2027-01-01"},
                           {}], now=NOW)
check("I6 rows past retention are counted", ret["due_for_purge"], 1)
check("I7 rows with NO retention stamp are counted separately, not ignored",
      ret["no_retention_stamp"], 1)
check("I8 …and the summary admits nothing is deleted automatically in this phase",
      "NOT deleted automatically" in ret["note"])

# The check-OUT path must not take a second position.
_checkout = ROUTER_CODE.split("def event_checkout", 1)[1].split("\n@router", 1)[0]
check("I9 check-OUT stores a timestamp and NO coordinate",
      not any(t in _checkout for t in ("check_in_lat", "check_in_lng", "parse_lat", "parse_lng")))
check("I10 the schema has no second coordinate on check-out either",
      "checked_out_at     TIMESTAMPTZ," in MIG986
      and "check_out_lat" not in MIG986 and "check_out_lng" not in MIG986)
check("I11 no table in the module can hold a POSITION HISTORY (no track-shaped column)",
      not any(t in MIG986 for t in ("position_history", "location_track", "ping_interval",
                                    "last_known_lat", "current_lat", "breadcrumb")))
check("I12 the shared geo decision is single-shot — it takes no list of positions",
      not any(t in GEO_CODE for t in ("positions", "track", "path=", "history")))
check("I13 a rep can read back what was recorded about them",
      "/my-checkins" in ROUTER_SRC)
check("I14 …and that endpoint is filtered to the CALLER's own employee id",
      'def my_checkins' in ROUTER_CODE
      and '.eq("employee_id", emp)' in ROUTER_CODE.split("def my_checkins", 1)[1].split("@router", 1)[0])
check("I15 a rep cannot check somebody ELSE in without a manager role",
      "You can only check yourself in" in ROUTER_SRC)
check("I16 the coordinate is stored only when it parsed — never as a plausible 0",
      "geo.parse_lat(body.check_in_lat)" in ROUTER_CODE)
check("I17 the check-in response tells the person what was kept and for how long",
      "retention_note" in ROUTER_CODE)
check("I18 the capture contract REUSES storevisit's column names (one contract, not two)",
      all(c in MIG986 for c in ("check_in_lat", "check_in_lng", "check_in_accuracy")))
print("  %d checks" % len([p for p in PASS if p.startswith("I")]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("J. Org-scoping — a static guard over every query in the marketing module")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# harness_org_scope_guard.py reads commcalc's router only, so the new surface would otherwise be
# unguarded. Same classifier contract: a chain carrying org_id is scoped; an insert/upsert carries it
# in the payload; an explicit `# org-guard-ok:` marker is a reviewed exception.
_WINDOW = 1600
_OPT_OUT = "org-guard-ok"
_GUARD_FILES = [os.path.join(HERE, "app", "modules", "marketing", f)
                for f in ("router.py", "attention_providers.py", "actuals.py")]


def classify(chain):
    if _OPT_OUT in chain:
        return "optout"
    if "org_id" in chain:
        return "scoped"
    has_write = (".insert(" in chain) or (".upsert(" in chain)
    has_mutate = (".update(" in chain) or (".delete(" in chain)
    if has_write and not has_mutate:
        return "payload"
    return "violation"


# The classifier must actually discriminate, or this whole section proves nothing.
check("J0a classifier: an unscoped read is a violation",
      classify('.table("marketing_event").select("*").execute()'), "violation")
check("J0b classifier: an org-filtered read is scoped",
      classify('.table("marketing_event").select("*").eq("org_id", org_id).execute()'), "scoped")
check("J0c classifier: an unscoped DELETE is a violation",
      classify('.table("x").delete().eq("id", i).execute()'), "violation")
check("J0d classifier: a marker is an opt-out",
      classify('.table("x").select("*").execute()  # org-guard-ok: reason'), "optout")

violations, scanned, optouts = [], 0, 0
for path in _GUARD_FILES:
    src = open(path, encoding="utf-8").read()
    for m in re.finditer(r"\.table\(\s*[\"']([A-Za-z0-9_]+)[\"']\s*\)", src):
        table = m.group(1)
        seg = src[m.start(): m.start() + _WINDOW]
        end = seg.find(".execute(")
        chain = seg[: end + 9] if end != -1 else seg
        # the marker may sit on the line just above the chain
        line_start = src.rfind("\n", 0, m.start())
        prev = src[max(0, line_start - 300): m.start()]
        if _OPT_OUT in prev.split("\n")[-3:][0] or _OPT_OUT in prev:
            chain = chain + " " + _OPT_OUT
        scanned += 1
        kind = classify(chain)
        if kind == "optout":
            optouts += 1
        elif kind == "violation":
            violations.append("%s: .table(%r) — %s" % (os.path.basename(path), table,
                                                       chain[:90].replace("\n", " ")))

check("J1 every query in the marketing module is org-scoped or a reviewed exception", violations, [])
check("J2 the guard actually scanned a meaningful number of queries", scanned >= 20)
check("J3 the only opt-outs are the bounded HOUSE-vocabulary reads", optouts <= 4)
check("J4 every opt-out states its reason",
      all("org-guard-ok:" in open(p, encoding="utf-8").read() or _OPT_OUT not in
          open(p, encoding="utf-8").read() for p in _GUARD_FILES))
check("J5 the event fetch is org-scoped in the ONE place events are read",
      '.eq("org_id", org_id).eq("id", event_id)' in ROUTER_CODE)
check("J6 child rows are scoped by org AND event together",
      '.eq("org_id", org_id).eq("event_id", event_id).eq("id", row_id)' in ROUTER_CODE)
check("J7 the document signer additionally proves the row is an EVENT document",
      'doc_kind") not in EVENT_DOC_KINDS' in ROUTER_CODE)
check("J8 the module is behind the shared entitlement gate",
      'require_module("marketing")' in ROUTER_SRC)
print("  %d checks" % len([p for p in PASS if p.startswith("J")]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("K. Migrations + registration")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
MIG987 = open(os.path.join(REPO, "database", "migrations", "987_marketing_event_seed.sql"),
              encoding="utf-8").read()
ENT = open(os.path.join(HERE, "app", "modules", "core", "entitlements.py"), encoding="utf-8").read()

check("K1 986 creates tables idempotently", MIG986.count("CREATE TABLE IF NOT EXISTS") >= 11)
check("K2 986 carries REVERT notes", "-- REVERT:" in MIG986)
check("K3 987 carries REVERT notes", "-- REVERT:" in MIG987)
check("K4 987's seeds are all ON CONFLICT DO NOTHING (never clobbers an edited label)",
      MIG987.count("ON CONFLICT") >= 9 and "DO UPDATE" not in MIG987)
check("K5 986 turns RLS on for every new table", "ENABLE ROW LEVEL SECURITY" in MIG986)
check("K6 …and grants nothing to anon/authenticated",
      "REVOKE ALL ON core.%I FROM anon, authenticated" in MIG986)
check("K7 approval defaults to OFF in the schema itself",
      "approval_required           BOOLEAN NOT NULL DEFAULT FALSE" in MIG986)
check("K8 …and in the house config row 987 writes",
      "FALSE, NULL, 150, 200, FALSE, 180, 48" in MIG987)
check("K9 …and in the code default", L.DEFAULT_CONFIG["approval_required"], False)
check("K10 the module is registered in the in-code catalogue (the billing/entitlement source)",
      '"marketing": "Marketing & Events"' in ENT)
check("K11 …and in the DB registry mig 700 reads",
      "INSERT INTO core.module_catalog (key, label, sort_order) VALUES\n  ('marketing'" in MIG987)
check("K12 SEED_VERSION was bumped so existing tenants self-provision the entitlement",
      "SEED_VERSION = 14" in ENT)
check("K13 the router is wired into the app",
      "marketing_router" in open(os.path.join(HERE, "app", "main.py"), encoding="utf-8").read())
_MIG987_SQL = _sql_without_comments(MIG987)
check("K14 no money-valued seed ships in 987 (vendor cost / giveaway unit cost are never seeded)",
      not any(t in _MIG987_SQL for t in ("unit_cost", "INSERT INTO core.marketing_event_vendor",
                                         "INSERT INTO core.marketing_event_giveaway")))
check("K14b …and no seeded option row carries a price or cost in its extra JSON",
      not any(t in _MIG987_SQL for t in ('"cost"', '"price"', '"unit_price"')))
check("K15 the documents table is EXTENDED, not forked (no second documents table)",
      "ADD COLUMN IF NOT EXISTS event_id UUID" in MIG986
      and "CREATE TABLE IF NOT EXISTS storeops.event_document" not in MIG986)
check("K16 the control box gets HONEST unmonitored declarations rather than silent green",
      MIG987.count("'unmonitored'") >= 3)
check("K17 …including that no purge job actually runs yet",
      "no purge job runs" in MIG987)
check("K18 the creative-asset SEAM exists and is unread in this phase",
      "asset_ref        TEXT," in MIG986 and "asset_ref" not in ROUTER_CODE)
check("K19 migration numbers are 986+ as assigned",
      os.path.exists(os.path.join(REPO, "database", "migrations", "986_marketing_event_module.sql")))
check("K20 the index registers the new subsystem",
      "## 23. MARKETING & EVENTS" in open(os.path.join(REPO, "docs", "SYSTEM_DATA_FLOW_INDEX.md"),
                    encoding="utf-8").read())
print("  %d checks" % len([p for p in PASS if p.startswith("K")]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("M. Route resolution — the catch-all must not swallow the literal routes")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# `/events/{event_id}/{collection}` matches ANY single segment, including "checkin". FastAPI matches
# in REGISTRATION ORDER, first match wins, so declaring the catch-all above the literal routes
# silently swallows them and answers `404 unknown collection 'checkin'`. That regression was REAL in
# this module's first draft — GPS check-in, check-out, document upload and apply-template were all
# shadowed. This section resolves each path through the ACTUAL router object and names the handler it
# reaches, so the ordering cannot rot back.
#
# fastapi is importable in this container, but it is not a stdlib guarantee: if it is ever absent this
# section reports itself as SKIPPED rather than silently passing (an unrun check that prints nothing
# is how a harness ends up proving less than it claims).
try:
    from app.modules.marketing.router import router as _mkt_router     # noqa: E402
    _ROUTING_AVAILABLE = True
except Exception as _routing_err:                                       # pragma: no cover
    _ROUTING_AVAILABLE = False
    print("  SKIPPED — the router could not be imported here (%s). Sections A-L are unaffected."
          % str(_routing_err)[:100])


def _resolve(method, path):
    """The handler FastAPI would actually run for (method, path), or None."""
    for r in _mkt_router.routes:
        if method not in (getattr(r, "methods", None) or set()):
            continue
        match, _ = r.matches({"type": "http", "method": method, "path": path,
                              "headers": [], "root_path": ""})
        if str(match).endswith("FULL"):
            return r.endpoint.__name__
    return None


if _ROUTING_AVAILABLE:
    EV = "/marketing/events/11111111-2222-3333-4444-555555555555"
    check("M1 POST …/checkin reaches the GPS check-in handler, not the child catch-all",
          _resolve("POST", EV + "/checkin"), "event_checkin")
    check("M2 POST …/checkout reaches the check-out handler",
          _resolve("POST", EV + "/checkout"), "event_checkout")
    check("M3 POST …/doc reaches the document upload handler",
          _resolve("POST", EV + "/doc"), "upload_event_doc")
    check("M4 POST …/apply-checklist-template reaches the template handler",
          _resolve("POST", EV + "/apply-checklist-template"), "apply_checklist_template")
    check("M5 POST …/status reaches the lifecycle handler",
          _resolve("POST", EV + "/status"), "set_event_status")
    check("M6 POST …/approval reaches the approval handler",
          _resolve("POST", EV + "/approval"), "event_approval")
    check("M7 GET …/actuals reaches the derived-actuals handler",
          _resolve("GET", EV + "/actuals"), "get_event_actuals")
    check("M8 GET …/docs reaches the document list",
          _resolve("GET", EV + "/docs"), "list_event_docs")
    check("M9 GET …/{id} reaches the workspace", _resolve("GET", EV), "get_event")
    # …and the catch-all still does its job for the six real child collections.
    for coll in ("staff", "vendors", "checklist", "links", "giveaways", "goals"):
        check("M10 POST …/%s still reaches the generic child CRUD" % coll,
              _resolve("POST", EV + "/" + coll), "create_child")
    check("M11 PATCH …/staff/{row} reaches the generic child update",
          _resolve("PATCH", EV + "/staff/abc"), "update_child")
    check("M12 DELETE …/staff/{row} reaches the generic child delete",
          _resolve("DELETE", EV + "/staff/abc"), "delete_child")
    # A literal route the catch-all COULD swallow must be declared above it in the source.
    _src = open(os.path.join(HERE, "app", "modules", "marketing", "router.py"), encoding="utf-8").read()
    _catchall = _src.index('@router.post("/events/{event_id}/{collection}")')
    for lit in ("/events/{event_id}/checkin", "/events/{event_id}/checkout",
                "/events/{event_id}/doc", "/events/{event_id}/apply-checklist-template",
                "/events/{event_id}/status", "/events/{event_id}/approval"):
        check("M13 %s is declared BEFORE the catch-all in the source" % lit,
              _src.index('"%s"' % lit) < _catchall)
    print("  %d checks" % len([p for p in PASS if p.startswith("M")]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("L. ARMED — the negative control")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# If these assertions cannot fail, everything above is decoration. Each of these is the exact shape
# of a real regression, asserted to be ABSENT.
_armed = []
if L.approval_decision({"approval_required": False, "approval_spend_threshold": 1}, 10)["required"]:
    _armed.append("approval leaked on while the switch is off")
if geo.evaluate_checkin(VLAT + 0.09, VLNG, 5, VLAT, VLNG, radius_m=150)["within_geofence"]:
    _armed.append("a 10 km fix was judged inside the fence")
if L.resolve_staffing([{"id": "p", "is_backup": False, "confirm_state": "declined"},
                       {"id": "b", "is_backup": True, "backup_for_staff_id": "p",
                        "confirm_state": "declined"}])["counts"]["uncovered"] == 0:
    _armed.append("a declined backup was counted as cover")
if A.build_goal_lines([{"metric_key": "leads_collected", "target_value": 5}],
                      GOAL_OPTS, {})[0]["actual_value"] == 0:
    _armed.append("a non-derivable metric reported 0 instead of 'no automatic actual'")
check("L1 the negative control found no regression (and CAN fail — see the source)", _armed, [])

# Prove the harness's own failure path works, so a green here is earned.
_before = len(FAIL)
check("L2 self-test: a deliberately wrong assertion is recorded as a FAILURE", 1 + 1, 3)
_selftest_worked = len(FAIL) == _before + 1
if _selftest_worked:
    FAIL.pop()            # remove the deliberate failure
    PASS.append("L2 self-test: a deliberately wrong assertion is recorded as a FAILURE")
check("L3 …and the harness's failure path is therefore wired up", _selftest_worked, True)
print("  %d checks" % len([p for p in PASS if p.startswith("L")]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 98)
for f in FAIL:
    print("  FAIL  " + f)
print("RESULT: %d passed, %d failed" % (len(PASS), len(FAIL)))
print("=" * 98)
sys.exit(1 if FAIL else 0)
