"""Offline proof (no live DB, no network) for the CRM sales-pipeline package.

OWNER DIRECTIVE 2026-08-12 (sanjot@): a Salesforce-class pipeline + follow-up system with reminders,
dispositions, and assignment to teammates and outside agencies; plus a permission-gated phone lookup
that returns everything known about a customer.

SECTION A — phone/email normalization: the join key the whole Customer 360 depends on.
SECTION B — config resolution: defaults, partial override, garbage clamping, degrade on no row.
SECTION C — business hours: a 3 a.m. follow-up is a 9 a.m. follow-up; closed days skip forward;
            malformed config leaves the time alone rather than inventing a schedule.
SECTION D — lead scoring: every operator, clamping, empty rule set, inactive rules ignored.
SECTION E — assignment: rule priority, each strategy, ROUND-ROBIN cursor advance + wraparound,
            inactive members skipped, no-match leaves the lead in the pool.
SECTION F — disposition: reason refusal, auto-followup timing, closes_lead, stage auto-advance,
            and the close-needs-an-outcome gate.
SECTION G — cadence materialization: due vs not-yet, idempotency against already-booked steps,
            stage-enter scoping, no_activity idle gate.
SECTION H — the sweep's three lists: remind (window key + snooze), miss (grace), escalate (stale +
            escalate windows), plus a garbage timestamp being SKIPPED rather than crashing the pass.
SECTION I — dashboard math: funnel, weighted forecast (open only), conversion rates.
SECTION J — Customer 360 gates: default-closed, grant paths, tenant open-posture, money stripping
            (REMOVED and named, never zeroed), and suggested actions.
SECTION K — router wiring against an in-memory fake Supabase client whose .eq() ACTUALLY FILTERS
            (a no-op .eq proves nothing — [[fake-client-eq-noop-trap]]): org scoping on read AND
            insert, the config whitelist refusing unknown keys, and missing-table degrade.

Run: `python3 harness_crm_pipeline.py` from backend/.
"""
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


NOW = datetime(2026, 8, 12, 15, 0, tzinfo=timezone.utc)   # a Wednesday, 11:00 ET


# ══════════════════════════════════════════════════════════════════════════════════════════════
# SECTION A — normalization
# ══════════════════════════════════════════════════════════════════════════════════════════════
from app.modules.crm.pipeline_core import (  # noqa: E402
    DEFAULT_CONFIG, resolve_config, normalize_phone, normalize_email, mask_phone, display_name,
    is_duplicate, shift_to_business_hours, score_lead, priority_from_score, rule_matches,
    next_round_robin, pick_assignee, apply_disposition, stage_close_requires_disposition,
    due_cadence_steps, tasks_to_remind, tasks_to_miss, leads_to_escalate, stale_leads,
    reminder_window_key, funnel, weighted_forecast, conversion_rates, _dt,
)

check("A1 formatted number -> last 10", normalize_phone("(516) 555-0134") == "5165550134")
check("A2 +1 country code dropped", normalize_phone("+1 516 555 0134") == "5165550134")
check("A3 dashed 11-digit dropped to 10", normalize_phone("1-516-555-0134") == "5165550134")
check("A4 a trailing extension does NOT shift the key", normalize_phone("5165550134 x22") == "5165550134",
      normalize_phone("5165550134 x22"))
check("A4b 11 digits with a leading 1 drops the country code, keeps the rest",
      normalize_phone("15165550134") == "5165550134")
check("A4c 12+ digits keep the FIRST ten, not the last",
      normalize_phone("516555013422") == "5165550134", normalize_phone("516555013422"))
# ⚠️ PARITY: these are the exact strings verified against the SQL generated column
# core.crm_lead.phone_norm on 2026-08-12. A lead is STORED with the SQL key and LOOKED UP with the
# Python one — if they ever disagree, every lookup silently returns "never seen this customer".
SQL_PARITY = {"(516) 555-0134": "5165550134", "+1 516 555 0134": "5165550134",
              "1-516-555-0134": "5165550134", "5165550134 x22": "5165550134",
              "5550": "", "516555013422": "5165550134"}
check("A4d Python matches the SQL generated column on every verified case",
      all(normalize_phone(k) == v for k, v in SQL_PARITY.items()),
      {k: normalize_phone(k) for k, v in SQL_PARITY.items() if normalize_phone(k) != v})
check("A5 too short refuses rather than half-matching", normalize_phone("5550") == "")
check("A6 empty / None safe", normalize_phone(None) == "" and normalize_phone("") == "")
check("A7 mask shows only the last 4", mask_phone("(516) 555-0134") == "••••0134")
check("A8 mask of nothing is still a mask", mask_phone("") == "••••")
check("A9 email normalized", normalize_email("  Bob@Example.COM ") == "bob@example.com")
check("A10 display falls back name -> company -> phone",
      display_name({"first_name": "Ann", "last_name": "Lee"}) == "Ann Lee"
      and display_name({"company_name": "Acme"}) == "Acme"
      and display_name({"phone": "5165550134"}) == "5165550134")

_a = {"phone": "(516) 555-0134", "email": "a@x.com"}
_b = {"phone": "516-555-0134", "email": "b@x.com"}
check("A11 dedupe by phone across formats", is_duplicate(_a, _b, "phone"))
check("A12 dedupe mode email does not fire on a phone match", not is_duplicate(_a, _b, "email"))
check("A13 dedupe mode none never fires", not is_duplicate(_a, _b, "none"))
check("A14 dedupe mode both = either identifier", is_duplicate(_a, _b, "both"))
check("A15 blank phone never matches blank phone",
      not is_duplicate({"phone": ""}, {"phone": ""}, "phone"))


# ══════════════════════════════════════════════════════════════════════════════════════════════
# SECTION B — config
# ══════════════════════════════════════════════════════════════════════════════════════════════
check("B1 no row -> pure defaults", resolve_config(None) == DEFAULT_CONFIG)
check("B2 partial override keeps the other defaults",
      resolve_config({"stale_lead_hours": 12})["escalate_after_hours"] == DEFAULT_CONFIG["escalate_after_hours"]
      and resolve_config({"stale_lead_hours": 12})["stale_lead_hours"] == 12)
check("B3 None values do not blank a default", resolve_config({"timezone": None})["timezone"] == DEFAULT_CONFIG["timezone"])
check("B4 garbage int falls back to the default", resolve_config({"stale_lead_hours": "abc"})["stale_lead_hours"] == 48)
check("B5 negative hours clamp to 0", resolve_config({"miss_grace_hours": -5})["miss_grace_hours"] == 0)
check("B6 numeric string is accepted", resolve_config({"stale_lead_hours": "24"})["stale_lead_hours"] == 24)
check("B7 non-dict business_hours degrades to the default",
      resolve_config({"business_hours": "nope"})["business_hours"] == DEFAULT_CONFIG["business_hours"])
check("B8 non-list reminder_channels degrades",
      resolve_config({"reminder_channels": "email"})["reminder_channels"] == DEFAULT_CONFIG["reminder_channels"])

CFG = resolve_config({"timezone": "America/New_York",
                      "business_hours": {"start": "09:00", "end": "20:00", "days": [1, 2, 3, 4, 5]}})


# ══════════════════════════════════════════════════════════════════════════════════════════════
# SECTION C — business hours
# ══════════════════════════════════════════════════════════════════════════════════════════════
# 2026-08-12 07:00 UTC = 03:00 ET Wednesday -> should shift to 09:00 ET = 13:00 UTC
_early = datetime(2026, 8, 12, 7, 0, tzinfo=timezone.utc)
check("C1 a 3am follow-up becomes a 9am follow-up",
      shift_to_business_hours(_early, CFG).hour == 13, shift_to_business_hours(_early, CFG).isoformat())
# 2026-08-12 15:00 UTC = 11:00 ET, inside hours -> untouched
check("C2 inside business hours is untouched", shift_to_business_hours(NOW, CFG) == NOW)
# Friday 2026-08-14 23:00 ET = Sat 03:00 UTC -> config excludes Sat/Sun -> next Monday 09:00 ET
_fri_night = datetime(2026, 8, 15, 3, 0, tzinfo=timezone.utc)
_shifted = shift_to_business_hours(_fri_night, CFG)
check("C3 a weekend drop lands Monday morning",
      _shifted.astimezone(timezone.utc).isoformat().startswith("2026-08-17T13:00"), _shifted.isoformat())
check("C4 time only ever moves forward", shift_to_business_hours(_early, CFG) >= _early)
check("C5 malformed hours leave the time alone",
      shift_to_business_hours(_early, {"business_hours": {"days": "x"}, "timezone": "UTC"}) == _early)
check("C6 start >= end is refused rather than looping",
      shift_to_business_hours(_early, {"timezone": "UTC",
                                       "business_hours": {"start": "20:00", "end": "09:00", "days": [0, 1, 2, 3, 4, 5, 6]}}) == _early)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# SECTION D — scoring
# ══════════════════════════════════════════════════════════════════════════════════════════════
RULES = [
    {"field": "email", "op": "exists", "points": 10, "is_active": True},
    {"field": "source_key", "op": "eq", "value": "walk_in", "points": 20, "is_active": True},
    {"field": "source_key", "op": "in", "value": "referral,employee", "points": 25, "is_active": True},
    {"field": "value_estimate", "op": "gt", "value": "500", "points": 15, "is_active": True},
    {"field": "interest_key", "op": "contains", "value": "port", "points": 5, "is_active": True},
    {"field": "notes", "op": "ne", "value": "", "points": 1, "is_active": False},
]
check("D1 empty rule set scores zero", score_lead({"email": "a@b.c"}, []) == 0)
check("D2 exists + eq + gt add up",
      score_lead({"email": "a@b.c", "source_key": "walk_in", "value_estimate": 900}, RULES) == 45,
      score_lead({"email": "a@b.c", "source_key": "walk_in", "value_estimate": 900}, RULES))
check("D3 `in` matches a member", score_lead({"source_key": "referral"}, RULES) == 25)
check("D4 `contains` is substring", score_lead({"interest_key": "port_in"}, RULES) == 5)
check("D5 inactive rules are ignored", score_lead({"notes": "x"}, RULES) == 0)
check("D6 non-numeric value never crashes gt", score_lead({"value_estimate": "abc"}, RULES) == 0)
check("D7 score clamps to 100",
      score_lead({"email": "a@b.c"}, [{"field": "email", "op": "exists", "points": 900}]) == 100)
check("D8 score floors at 0",
      score_lead({"email": "a@b.c"}, [{"field": "email", "op": "exists", "points": -900}]) == 0)
check("D9 priority bands", priority_from_score(70) == "hot" and priority_from_score(30) == "warm"
      and priority_from_score(5) == "cold")


# ══════════════════════════════════════════════════════════════════════════════════════════════
# SECTION E — assignment
# ══════════════════════════════════════════════════════════════════════════════════════════════
check("E1 empty match matches everything", rule_matches({"store_code": "B-01"}, {}))
check("E2 all keys must match (AND)",
      rule_matches({"source_key": "walk_in", "market": "NY"}, {"source_key": "walk_in", "market": "NY"})
      and not rule_matches({"source_key": "walk_in", "market": "NJ"},
                           {"source_key": "walk_in", "market": "NY"}))
check("E3 match is case-insensitive", rule_matches({"market": "ny"}, {"market": "NY"}))
check("E4 list match = any-of", rule_matches({"market": "NJ"}, {"market": ["NY", "NJ"]}))
check("E5 min_value is numeric, not lexical",
      rule_matches({"value_estimate": 900}, {"min_value": 500})
      and not rule_matches({"value_estimate": 90}, {"min_value": 500}))
check("E6 blank match values are skipped, not treated as ''",
      rule_matches({"market": "NY"}, {"market": "", "source_key": None}))

MEMBERS = [
    {"employee_id": "E1", "sort_order": 1, "is_active": True},
    {"employee_id": "E2", "sort_order": 2, "is_active": True},
    {"employee_id": "E3", "sort_order": 3, "is_active": False},
]
check("E7 round-robin starts at the cursor", next_round_robin(MEMBERS, 0) == ("E1", 1))
check("E8 round-robin advances", next_round_robin(MEMBERS, 1) == ("E2", 0))
check("E9 round-robin wraps", next_round_robin(MEMBERS, 2) == ("E1", 1))
check("E10 inactive members are skipped", "E3" not in [next_round_robin(MEMBERS, i)[0] for i in range(6)])
check("E11 no active members -> nobody, cursor untouched", next_round_robin([], 3) == (None, 3))

CTX = {"queues": {"Q1": {"rr_cursor": 1}}, "queue_members": {"Q1": MEMBERS},
       "store_owner": {"B-01": "MGR1"}}
RULESET = [
    {"id": "R1", "priority": 10, "match": {"source_key": "agency"}, "strategy": "agency",
     "target_agency_id": "AG1", "is_active": True},
    {"id": "R2", "priority": 20, "match": {"interest_key": "business"}, "strategy": "round_robin",
     "target_queue_id": "Q1", "is_active": True},
    {"id": "R3", "priority": 30, "match": {"market": "NY"}, "strategy": "specific_user",
     "target_employee_id": "E9", "is_active": True},
    {"id": "R0", "priority": 5, "match": {"source_key": "walk_in"}, "strategy": "store_owner",
     "is_active": False},
    {"id": "R9", "priority": 900, "match": {}, "strategy": "store_owner", "is_active": True},
]
p = pick_assignee({"source_key": "agency"}, RULESET, CTX)
check("E12 agency strategy sets the agency", p["agency_id"] == "AG1" and p["employee_id"] is None)
p = pick_assignee({"interest_key": "business"}, RULESET, CTX)
check("E13 round-robin picks from the cursor and reports the next",
      p["employee_id"] == "E2" and p["rr_cursor_update"] == ("Q1", 0), p)
p = pick_assignee({"market": "NY"}, RULESET, CTX)
check("E14 specific_user", p["employee_id"] == "E9")
p = pick_assignee({"store_code": "B-01"}, RULESET, CTX)
check("E15 catch-all store_owner resolves the store's manager", p["employee_id"] == "MGR1")
p = pick_assignee({"store_code": "UNKNOWN"}, RULESET, CTX)
check("E16 unknown store leaves the lead in the pool (never a silent default)",
      p["employee_id"] is None and p["rule_id"] == "R9")
p = pick_assignee({"source_key": "walk_in", "store_code": "B-01"}, RULESET, CTX)
check("E17 an INACTIVE rule never wins even at the lowest priority", p["rule_id"] == "R9")
check("E18 no rules at all -> nobody assigned",
      pick_assignee({"store_code": "B-01"}, [], CTX)["employee_id"] is None)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# SECTION F — disposition
# ══════════════════════════════════════════════════════════════════════════════════════════════
LEAD = {"id": "L1", "status": "open", "stage_id": "S1", "created_at": NOW.isoformat(),
        "last_activity_at": NOW.isoformat()}

D_NOANS = {"id": "D1", "key": "no_answer", "name": "No answer", "outcome": "no_contact",
           "requires_followup": True, "default_followup_hours": 4}
D_LOST = {"id": "D2", "key": "not_interested", "name": "Not interested", "outcome": "lost",
          "requires_reason": True, "closes_lead": True}
D_WON = {"id": "D3", "key": "sold", "name": "Sold", "outcome": "won", "closes_lead": True}
D_STAGE = {"id": "D4", "key": "spoke", "name": "Spoke", "outcome": "connected",
           "sets_stage_id": "S2", "requires_followup": True, "default_followup_hours": 24}

r = apply_disposition(LEAD, None, CFG, NOW)
check("F1 unknown disposition is refused, not silently applied", r["errors"])
r = apply_disposition(LEAD, D_LOST, CFG, NOW)
check("F2 requires_reason without one is refused", r["errors"])
r = apply_disposition(LEAD, D_LOST, CFG, NOW, reason_code_id="RC1")
check("F3 with a reason it closes as lost",
      not r["errors"] and r["lead_updates"]["status"] == "lost" and r["lead_updates"]["closed_at"])
check("F4 a closed lead gets no next action", r["lead_updates"]["next_action_at"] is None
      and r["followup"] is None)
r = apply_disposition(LEAD, D_WON, CFG, NOW)
check("F5 won closes as won", r["lead_updates"]["status"] == "won")
check("F6 a connect stamps first_contacted_at once", r["lead_updates"].get("first_contacted_at") is not None)
check("F7 already-contacted lead is not re-stamped",
      "first_contacted_at" not in apply_disposition({**LEAD, "first_contacted_at": NOW.isoformat()},
                                                    D_WON, CFG, NOW)["lead_updates"])
r = apply_disposition(LEAD, D_NOANS, CFG, NOW)
due = _dt(r["followup"]["due_at"])
check("F8 no-answer books a follow-up 4h out, inside business hours",
      r["followup"] is not None and due is not None and due >= NOW + timedelta(hours=3), r["followup"])
check("F9 the follow-up is also the lead's next action",
      r["lead_updates"]["next_action_at"] == r["followup"]["due_at"])
r = apply_disposition(LEAD, D_STAGE, CFG, NOW)
check("F10 a disposition can auto-advance the stage", r["lead_updates"]["stage_id"] == "S2")
explicit = NOW + timedelta(days=3)
r = apply_disposition(LEAD, D_NOANS, CFG, NOW, followup_at=explicit)
check("F11 an explicit follow-up date wins over the default",
      r["followup"]["due_at"] == explicit.isoformat())
r = apply_disposition(LEAD, {**D_NOANS, "default_followup_hours": "junk"}, CFG, NOW)
check("F12 garbage follow-up hours falls back to 24h, never crashes", r["followup"] is not None)

check("F13 a won stage demands an outcome",
      stage_close_requires_disposition({"is_won": True}, CFG))
check("F14 an ordinary stage does not",
      not stage_close_requires_disposition({"is_won": False, "is_lost": False}, CFG))
check("F15 the tenant can turn the gate off entirely",
      not stage_close_requires_disposition({"is_won": True}, {**CFG, "require_disposition_on_close": 0}))


# ══════════════════════════════════════════════════════════════════════════════════════════════
# SECTION G — cadence materialization
# ══════════════════════════════════════════════════════════════════════════════════════════════
CAD = {"id": "C1", "trigger": "on_create", "pipeline_id": "P1"}
STEPS = [
    {"step_no": 1, "offset_hours": 1, "task_type": "call", "title": "Call", "is_active": True},
    {"step_no": 2, "offset_hours": 24, "task_type": "text", "title": "Text", "is_active": True},
    {"step_no": 3, "offset_hours": 72, "task_type": "call", "title": "Last try", "is_active": True},
    {"step_no": 4, "offset_hours": 0, "title": "Disabled", "is_active": False},
]
created = NOW - timedelta(hours=30)
lead_g = {"id": "L1", "created_at": created.isoformat(), "pipeline_id": "P1"}
out = due_cadence_steps(lead_g, CAD, STEPS, set(), CFG, NOW)
check("G1 only the steps whose offset has passed are booked",
      [o["cadence_step_no"] for o in out] == [1, 2], [o["cadence_step_no"] for o in out])
check("G2 an inactive step is never booked", all(o["cadence_step_no"] != 4 for o in out))
out2 = due_cadence_steps(lead_g, CAD, STEPS, {("C1", 1)}, CFG, NOW)
check("G3 an already-booked step is not booked twice (idempotent sweep)",
      [o["cadence_step_no"] for o in out2] == [2])
check("G4 a lead with no created_at is skipped, not crashed",
      due_cadence_steps({"id": "L2"}, CAD, STEPS, set(), CFG, NOW) == [])
check("G5 a garbage timestamp is skipped, not crashed",
      due_cadence_steps({"id": "L3", "created_at": "not-a-date"}, CAD, STEPS, set(), CFG, NOW) == [])

CAD_STAGE = {"id": "C2", "trigger": "on_stage_enter", "stage_id": "S2"}
check("G6 stage-enter cadence ignores a lead in another stage",
      due_cadence_steps({"id": "L1", "stage_id": "S1", "stage_entered_at": created.isoformat()},
                        CAD_STAGE, STEPS, set(), CFG, NOW) == [])
check("G7 stage-enter cadence fires for the right stage",
      len(due_cadence_steps({"id": "L1", "stage_id": "S2", "stage_entered_at": created.isoformat()},
                            CAD_STAGE, STEPS, set(), CFG, NOW)) == 2)

CAD_IDLE = {"id": "C3", "trigger": "no_activity", "idle_hours": 48}
check("G8 no_activity waits out the idle window",
      due_cadence_steps({"id": "L1", "last_activity_at": (NOW - timedelta(hours=10)).isoformat()},
                        CAD_IDLE, STEPS, set(), CFG, NOW) == [])
check("G9 no_activity fires once the window passes",
      len(due_cadence_steps({"id": "L1", "last_activity_at": (NOW - timedelta(hours=80)).isoformat()},
                            CAD_IDLE, STEPS, set(), CFG, NOW)) > 0)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# SECTION H — the sweep's three lists
# ══════════════════════════════════════════════════════════════════════════════════════════════
T_DUE = {"id": "T1", "status": "open", "due_at": (NOW - timedelta(hours=1)).isoformat(),
         "remind_at": (NOW - timedelta(hours=1)).isoformat()}
T_FUTURE = {"id": "T2", "status": "open", "due_at": (NOW + timedelta(hours=5)).isoformat(),
            "remind_at": (NOW + timedelta(hours=5)).isoformat()}
T_DONE = {"id": "T3", "status": "done", "due_at": (NOW - timedelta(hours=9)).isoformat()}
T_SNOOZED = {"id": "T4", "status": "open", "due_at": (NOW - timedelta(hours=2)).isoformat(),
             "remind_at": (NOW - timedelta(hours=2)).isoformat(),
             "snooze_until": (NOW + timedelta(hours=3)).isoformat()}
T_BAD = {"id": "T5", "status": "open", "due_at": "garbage", "remind_at": "garbage"}
ALL_TASKS = [T_DUE, T_FUTURE, T_DONE, T_SNOOZED, T_BAD]

rem = tasks_to_remind(ALL_TASKS, set(), NOW)
check("H1 only the due, open, un-snoozed task is reminded", [t["id"] for t in rem] == ["T1"],
      [t["id"] for t in rem])
check("H2 the reminder carries its window key", rem[0]["window_key"] == reminder_window_key(T_DUE))
check("H3 an already-sent window is not re-sent",
      tasks_to_remind(ALL_TASKS, {reminder_window_key(T_DUE)}, NOW) == [])
check("H4 moving the due date re-arms the reminder",
      tasks_to_remind([{**T_DUE, "due_at": (NOW - timedelta(hours=2)).isoformat()}],
                      {reminder_window_key(T_DUE)}, NOW) != [])

miss = tasks_to_miss(ALL_TASKS, {**CFG, "miss_grace_hours": 4}, NOW)
check("H5 inside the grace window nothing is missed yet", [t["id"] for t in miss] == [],
      [t["id"] for t in miss])
miss = tasks_to_miss(ALL_TASKS, {**CFG, "miss_grace_hours": 0}, NOW)
check("H6 past due with no grace, the open task is missed (snoozed/done are not)",
      [t["id"] for t in miss] == ["T1"], [t["id"] for t in miss])

LEADS_H = [
    {"id": "LA", "status": "open", "last_activity_at": (NOW - timedelta(hours=100)).isoformat()},
    {"id": "LB", "status": "open", "last_activity_at": (NOW - timedelta(hours=50)).isoformat()},
    {"id": "LC", "status": "won", "last_activity_at": (NOW - timedelta(hours=500)).isoformat()},
    {"id": "LD", "status": "open", "created_at": (NOW - timedelta(hours=200)).isoformat()},
    {"id": "LE", "status": "open", "last_activity_at": "junk"},
]
esc = leads_to_escalate(LEADS_H, CFG, NOW)          # stale 48 + escalate 24 = 72h
check("H7 only leads quiet past stale+escalate escalate",
      sorted(l["id"] for l in esc) == ["LA", "LD"], [l["id"] for l in esc])
check("H8 a closed lead never escalates", all(l["id"] != "LC" for l in esc))
check("H9 a garbage timestamp is skipped, not crashed", all(l["id"] != "LE" for l in esc))
check("H10 quiet_hours is reported for the manager", esc[0].get("quiet_hours") is not None)
check("H11 already-escalated leads are excluded",
      [l["id"] for l in leads_to_escalate(LEADS_H, CFG, NOW, already={"LA"})] == ["LD"])
st = stale_leads(LEADS_H, CFG, NOW)
check("H12 stale is a superset of escalated", {l["id"] for l in esc} <= {l["id"] for l in st})
check("H13 stale=0 disables the sweep", stale_leads(LEADS_H, {**CFG, "stale_lead_hours": 0}, NOW) == [])


# ══════════════════════════════════════════════════════════════════════════════════════════════
# SECTION I — dashboard math
# ══════════════════════════════════════════════════════════════════════════════════════════════
STAGES_I = [
    {"id": "S1", "name": "New", "sort_order": 10, "probability": 10},
    {"id": "S2", "name": "Qualified", "sort_order": 20, "probability": 50},
    {"id": "S3", "name": "Sold", "sort_order": 30, "probability": 100, "is_won": True},
]
LEADS_I = [
    {"id": "1", "stage_id": "S1", "value_estimate": 100, "status": "open"},
    {"id": "2", "stage_id": "S2", "value_estimate": 200, "status": "open"},
    {"id": "3", "stage_id": "S2", "value_estimate": 300, "status": "open"},
    {"id": "4", "stage_id": "S3", "value_estimate": 400, "status": "won"},
    {"id": "5", "stage_id": "S1", "value_estimate": 500, "status": "lost"},
    {"id": "6", "stage_id": "SX", "value_estimate": 999, "status": "open"},   # orphan stage
]
f = funnel(LEADS_I, STAGES_I)
check("I1 funnel is in stage order", [b["stage"] for b in f] == ["New", "Qualified", "Sold"])
check("I2 empty stages are still reported", len(f) == 3)
check("I3 counts and values roll up", f[1]["count"] == 2 and f[1]["value"] == 500.0)
check("I4 a lead pointing at an unknown stage does not crash or invent a bucket",
      sum(b["count"] for b in f) == 5)
check("I5 forecast weights by probability, OPEN only",
      weighted_forecast(LEADS_I, STAGES_I) == round(100 * .1 + 200 * .5 + 300 * .5, 2),
      weighted_forecast(LEADS_I, STAGES_I))
c = conversion_rates(LEADS_I)
check("I6 conversion counts", c["total"] == 6 and c["won"] == 1 and c["lost"] == 1 and c["open"] == 4)
check("I7 win rate is over CLOSED, not total", c["win_rate"] == 50.0)
check("I8 no leads -> zeros, not a divide-by-zero",
      conversion_rates([])["win_rate"] == 0.0)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# SECTION J — Customer 360 gates
# ══════════════════════════════════════════════════════════════════════════════════════════════
from app.modules.crm import customer360 as c360  # noqa: E402

SUPER = {"super_admin": True, "perms": {}}
ADMIN_SCOPE = {"perms": {"scope": "all"}}
REP = {"perms": {"scope": "store"}}
REP_GRANTED = {"perms": {"scope": "store", "data": {"customer_360": True}}}
REP_MODULES = {"perms": {"scope": "store", "modules": {"customer_360": True}}}
REP_MONEY = {"perms": {"scope": "store", "data": {"customer_360": True, "customer_360_financial": True}}}

check("J1 nobody (unauthenticated) is refused", not c360.customer_360_allowed(None))
check("J2 a plain rep is refused by default", not c360.customer_360_allowed(REP))
check("J3 super-admin passes", c360.customer_360_allowed(SUPER))
check("J4 company-wide scope passes", c360.customer_360_allowed(ADMIN_SCOPE))
check("J5 the data grant passes", c360.customer_360_allowed(REP_GRANTED))
check("J6 the grant is honored under `modules` too", c360.customer_360_allowed(REP_MODULES))
check("J7 a tenant can open the lookup to everyone",
      c360.customer_360_allowed(REP, {"lookup_requires_grant": False}))
check("J8 open posture still requires SOME caller",
      not c360.customer_360_allowed(None, {"lookup_requires_grant": False}))
check("J9 money is default-closed even for a lookup-granted rep",
      not c360.customer_360_financial_allowed(REP_GRANTED))
check("J10 the money grant passes", c360.customer_360_financial_allowed(REP_MONEY))
check("J11 there is NO tenant toggle for money",
      not c360.customer_360_financial_allowed(REP))

ROWS = [{"trans_date": "2026-01-01", "store": "B-01", "product_desc": "iPhone", "ext_price": 999, "gp": 120}]
kept, withheld = c360.strip_money(ROWS, True)
check("J12 with the grant nothing is stripped", kept == ROWS and withheld == [])
kept, withheld = c360.strip_money(ROWS, False)
check("J13 without it money keys are REMOVED, not zeroed",
      "gp" not in kept[0] and "ext_price" not in kept[0])
check("J14 the operational columns survive",
      kept[0]["store"] == "B-01" and kept[0]["product_desc"] == "iPhone")
check("J15 what was withheld is NAMED", withheld == ["ext_price", "gp"], withheld)

old = (NOW - timedelta(days=700)).isoformat()
sections = {
    "devices": {"rows": [{"date_sold": old}]},
    "purchases": {"rows": [{"department": "PHONES", "trans_date": old}]},
    "activations": {"rows": [{"plan_code": "X"}]},
    "crm": {"rows": []},
}
acts = {a["key"] for a in c360.suggested_actions(sections, NOW)}
check("J16 an old device suggests an upgrade", "upgrade" in acts, acts)
check("J17 no accessory on record is surfaced", "accessory" in acts)
check("J18 a single line suggests add-a-line", "add_line" in acts)
acts = {a["key"] for a in c360.suggested_actions(
    {"devices": {"rows": []}, "purchases": {"rows": []}, "activations": {"rows": []},
     "crm": {"rows": []}}, NOW)}
check("J19 an unknown number is offered as a new lead", "unknown" in acts)
acts = {a["key"] for a in c360.suggested_actions(
    {"devices": {"rows": []}, "purchases": {"rows": []}, "activations": {"rows": []},
     "crm": {"rows": [{"status": "open"}]}}, NOW)}
check("J20 an existing open lead is called out instead of duplicating it", "open_lead" in acts)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# SECTION K — router wiring against a fake client whose .eq() ACTUALLY FILTERS
# ══════════════════════════════════════════════════════════════════════════════════════════════
# [[fake-client-eq-noop-trap]]: a stub .eq that returns self without filtering will pass an
# org-scoping test that the real code fails. This one filters for real.
class FakeQuery:
    def __init__(self, rows, store, missing=False):
        self.rows, self.store, self.missing = list(rows), store, missing
        self._pending = None

    def select(self, *_a, **_k):
        if self.missing:
            raise RuntimeError("relation does not exist")
        return self

    def eq(self, col, val):
        self.rows = [r for r in self.rows if r.get(col) == val]
        return self

    def in_(self, col, vals):
        self.rows = [r for r in self.rows if r.get(col) in set(vals)]
        return self

    def gte(self, col, val):
        self.rows = [r for r in self.rows if str(r.get(col) or "") >= str(val)]
        return self

    def lte(self, col, val):
        self.rows = [r for r in self.rows if str(r.get(col) or "") <= str(val)]
        return self

    def ilike(self, *_a, **_k):
        return self

    def or_(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def insert(self, row):
        if self.missing:
            raise RuntimeError("relation does not exist")
        self._pending = ("insert", dict(row))
        return self

    def update(self, row):
        self._pending = ("update", dict(row))
        return self

    def upsert(self, row, **_k):
        self._pending = ("upsert", dict(row))
        return self

    def delete(self):
        self._pending = ("delete", {})
        return self

    def execute(self):
        if self._pending:
            kind, row = self._pending
            if kind in ("insert", "upsert"):
                row.setdefault("id", f"new-{len(self.store)}")
                self.store.append(row)
                return type("R", (), {"data": [row], "count": 1})()
            return type("R", (), {"data": [row], "count": 1})()
        return type("R", (), {"data": list(self.rows), "count": len(self.rows)})()


class FakeSchema:
    def __init__(self, tables, missing=()):
        self.tables, self.missing = tables, set(missing)

    def table(self, name):
        self.tables.setdefault(name, [])
        return FakeQuery(self.tables[name], self.tables[name], missing=name in self.missing)

    def rpc(self, *_a, **_k):
        return type("R", (), {"execute": lambda _s=None: type("X", (), {"data": []})()})()


class FakeClient:
    def __init__(self, tables, missing=()):
        self.tables, self.missing = tables, missing

    def schema(self, _name):
        return FakeSchema(self.tables, self.missing)


ORG_A = "00000000-0000-0000-0000-000000000001"
ORG_B = "00000000-0000-0000-0000-0000000000ff"
TABLES = {
    "crm_lead": [
        {"id": "LA", "org_id": ORG_A, "lead_no": 1, "first_name": "Ann", "phone": "5165550134",
         "phone_norm": "5165550134", "status": "open", "store_code": "B-01",
         "created_at": NOW.isoformat(), "value_estimate": 100},
        {"id": "LB", "org_id": ORG_B, "lead_no": 1, "first_name": "OtherTenant",
         "phone_norm": "5165550134", "status": "open", "store_code": "B-01",
         "created_at": NOW.isoformat(), "value_estimate": 100},
    ],
    "crm_stage": [{"id": "S1", "org_id": ORG_A, "name": "New", "sort_order": 10, "probability": 10}],
    "crm_source": [], "crm_interest": [], "crm_disposition": [], "crm_pipeline": [], "crm_agency": [],
    "crm_config": [{"org_id": ORG_A, "duplicate_match": "phone"}],
    "crm_task": [], "crm_activity": [], "crm_assignment": [], "crm_score_rule": [],
    "crm_assignment_rule": [], "crm_queue": [], "crm_queue_member": [], "crm_lookup_audit": [],
}

import app.modules.crm.router as crm_router  # noqa: E402


def _body(model, d):
    """Build the request model FastAPI hands the handler, instead of a plain dict.

    These endpoints were migrated from `body: dict` to a declared pydantic model, so the handler
    reads `body.<field>`. A probe passing a dict dies with AttributeError BEFORE reaching the logic
    under test — the harness then reads as "failing" while proving nothing. `model_validate`
    reproduces FastAPI's own call shape, including which fields count as explicitly set
    (`model_fields_set`), which several handlers branch on.
    """
    return model.model_validate(d)

_fake = FakeClient(TABLES)
crm_router.get_supabase = lambda: _fake                      # patch the module's own accessor
crm_router.sb = lambda: _fake.schema("core")
crm_router._caller = lambda *_a, **_k: {"org_id": ORG_A, "employee_id": "E1", "id": "U1",
                                        "perms": {"scope": "all"}, "super_admin": False}
crm_router._keyset = lambda *_a, **_k: None                  # unrestricted caller

res = crm_router.list_leads(org_id=ORG_A)
check("K1 the lead list is org-scoped (the other tenant's lead is NOT returned)",
      [r["id"] for r in res["rows"]] == ["LA"], [r["id"] for r in res["rows"]])
res_b = crm_router.list_leads(org_id=ORG_B)
check("K2 the other tenant sees only its own", [r["id"] for r in res_b["rows"]] == ["LB"])

res = crm_router.list_leads(org_id=ORG_A, q="5165550134")
check("K3 phone search matches across formatting", len(res["rows"]) == 1)
res = crm_router.list_leads(org_id=ORG_A, q="nobody")
check("K4 a non-matching search returns nothing (not everything)", res["rows"] == [])

dupes = crm_router.dedupe_check(_body(crm_router.DedupeCheckIn, {"phone": "(516) 555-0134"}), org_id=ORG_A)
check("K5 dedupe-check is org-scoped too",
      [d["id"] for d in dupes["duplicates"]] == ["LA"], dupes)

before = len(TABLES["crm_lead"])
created = crm_router.create_lead(_body(crm_router.CreateLeadIn, {"phone": "5165559999", "first_name": "New"}), org_id=ORG_A)
check("K6 a new lead is created", len(TABLES["crm_lead"]) == before + 1)
check("K7 the INSERT stamps org_id (write-side scoping, not just reads)",
      TABLES["crm_lead"][-1]["org_id"] == ORG_A, TABLES["crm_lead"][-1].get("org_id"))
check("K8 org_id is never taken from the request body",
      crm_router.create_lead(_body(crm_router.CreateLeadIn, {"phone": "5165558888", "org_id": ORG_B}), org_id=ORG_A)
      and TABLES["crm_lead"][-1]["org_id"] == ORG_A)

from fastapi import HTTPException  # noqa: E402

try:
    crm_router.create_lead(_body(crm_router.CreateLeadIn, {"first_name": "No contact details"}), org_id=ORG_A)
    check("K9 a lead with no phone AND no email is refused", False, "it was accepted")
except HTTPException as e:
    check("K9 a lead with no phone AND no email is refused", e.status_code == 400)

crm_router._caller = lambda *_a, **_k: {"org_id": ORG_A, "perms": {"scope": "all"}, "employee_id": "E1"}
try:
    crm_router.put_config({"stale_lead_hours": 12, "not_a_real_setting": 1}, org_id=ORG_A)
    check("K10 an unknown config key is REFUSED, not silently dropped", False, "it was accepted")
except HTTPException as e:
    check("K10 an unknown config key is REFUSED, not silently dropped",
          e.status_code == 400 and "not_a_real_setting" in str(e.detail), e.detail)

# A scoped caller must not see another store's leads.
crm_router._keyset = lambda *_a, **_k: {"B-02"}
res = crm_router.list_leads(org_id=ORG_A)
check("K11 a store-scoped caller does not see another store's lead",
      all(r.get("store_code") != "B-01" for r in res["rows"]), [r.get("store_code") for r in res["rows"]])
check("K12 an unrouted lead (no store yet) stays visible to a scoped caller",
      any(not r.get("store_code") for r in res["rows"]))
crm_router._keyset = lambda *_a, **_k: None

# A missing table must degrade, never 500.
_missing = FakeClient({k: [] for k in TABLES}, missing=("crm_lead",))
crm_router.get_supabase = lambda: _missing
crm_router.sb = lambda: _missing.schema("core")
res = crm_router.list_leads(org_id=ORG_A)
check("K13 an un-run migration degrades to an empty list with a note, never a crash",
      res["rows"] == [] and "note" in res, res)

# 360 denial still writes an audit row.
_audit = FakeClient({**{k: [] for k in TABLES}})
c360.write_audit(_audit, ORG_A, phone="5165550134", caller={"id": "U1"}, allowed=False,
                 sections={"denied": {"available": False, "count": 0}})
check("K14 a DENIED lookup is audited",
      len(_audit.tables["crm_lookup_audit"]) == 1
      and _audit.tables["crm_lookup_audit"][0]["allowed"] is False)
check("K15 the audit stores only the masked number, never the full one",
      _audit.tables["crm_lookup_audit"][0]["phone_masked"] == "••••0134"
      and "5165550134" not in str(_audit.tables["crm_lookup_audit"][0]))
check("K16 the audit row is org-stamped", _audit.tables["crm_lookup_audit"][0]["org_id"] == ORG_A)


# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n".join(f"  ✔ {p}" for p in PASS))
if FAIL:
    print("\nFAILURES:")
    print("\n".join(f"  ✘ {f}" for f in FAIL))
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
