"""PROOF: the DM daily visit quota, the market manager's assignments, and the Friday-evening fill.

OWNER ASK 2026-10-03, verbatim:
    "each Dm is required to do 2 store visists every day , confuhgurable by each tenant , set uo 2
     for now editable in the system, the Market manager to monitor the dm performance and assign
     them the stores, if there are no stores assigned by friday evening then assign stores to the dm
     based on where the performance is low in an order of priority specailly where the activations
     and accessories sales are low , then kpi , all these should be confgurable by the tenant ,
     nothing harcoded, create these for now and give options to assign other deliverables as a drop
     down menu to add to the priorty list, it could be sales items like edge in luxelink or xfinity
     in boost , the drop down will be carrier spefici"

WHY A HARNESS AND NOT A TEST OF THE SCREEN. Every failure mode here is a legal value that renders
perfectly:

  • a store with no target scores attainment 0/0. Call that 0% and it ranks FIRST, so the Friday fill
    sends two DMs a day to the stores nobody bothered to set a target for. Call it 100% and the store
    disappears from the priority list entirely. Neither raises, neither fails a build, and both read
    as a working priority order. §D pins that it is NEITHER.
  • the fill runs on an hourly tick. One missing uniqueness rule and a DM's Monday grows a new store
    every hour until the week is unreadable. §G3 pins idempotence by running the planner twice.
  • a manager assigns one of the two visits. A fill that "assigns the week" replaces their choice and
    nobody is told. §G1 pins top-up, not replace.
  • a store outside a DM's span is a perfectly valid store_code. §G4 pins that it is never assigned.
  • the seed in migration 1050 and HOUSE_PRIORITY_RULES are two spellings of the owner's order. §B
    parses the seed OUT of the migration, so this file cannot pass against a seed the migration does
    not contain.
  • "attainment" could be divided in two places and mean two things. §H pins it to ONE formula,
    `targets_engine.attainment_pct`, and fails the build if a second division appears.

WHAT THIS PINS
  A. the config: the owner's 2/day default, a tenant override, 0 as a legal quota, and every invalid
     value falling back rather than silently disabling the quota;
  B. the seed parsed OUT of migration 1050 == visit_plan.HOUSE_PRIORITY_RULES, in the owner's order;
  C. the ranking: activations outranks accessories outranks KPI, weights and directions are config;
  D. THE HONESTY RULE — an unmeasured store is neither a perfect store nor a failing one, it sorts
     last, and it is NAMED;
  E. the quota board: required / assigned / completed / shortfall, a DM with nothing still present,
     and a visit to an unassigned store still counted;
  F. the deadline: nothing fills before the tenant's own Friday evening, everything after;
  G. the fill: tops up without replacing, is idempotent, never leaves a DM's span, never repeats a
     store in the window, and reports the slots it could not fill;
  H. THE ONE-HOME LOCKS, which fail the build if a caller stops dereferencing: one attainment
     formula, one org-tree walk, one performance source, one carrier deliverable registry;
  I. RULE TWO — no carrier, tenant or product name in the module, the router, the migration or the
     page.

PURE / DB-FREE: imports the real `storevisit/visit_plan` and reads the real migration and router as
text. stdlib only.
"""
import os
import re
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.storevisit import visit_plan as vp   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIG = os.path.join(ROOT, "database", "migrations", "1050_dm_visit_quota_and_assignment.sql")
MOD = os.path.join(ROOT, "backend", "app", "modules", "storevisit", "visit_plan.py")
RTR = os.path.join(ROOT, "backend", "app", "modules", "storevisit", "visit_plan_router.py")
TE = os.path.join(ROOT, "backend", "app", "modules", "commcalc", "targets_engine.py")
PAGE = os.path.join(ROOT, "frontend", "src", "app", "(platform)", "storeops", "visits",
                    "plan", "page.tsx")

checks, failures = 0, []


def check(label, ok):
    global checks
    checks += 1
    if not ok:
        failures.append(label)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


mig_src = read(MIG)
mod_src = read(MOD)
rtr_src = read(RTR)
te_src = read(TE)
page_src = read(PAGE) if os.path.exists(PAGE) else ""

# ── A. THE CONFIG — the owner's 2, editable, and nothing hardcoded ────────────────────────────────
d = vp.resolve_config(None)
check("A1 the un-configured default is the owner's 2 visits a day", d["quota_per_day"] == 2)
check("A2 the default quota days are the working week", d["quota_days"] == (1, 2, 3, 4, 5))
check("A3 the default deadline is Friday evening", (d["deadline_dow"], d["deadline_time"]) == (5, "17:00"))
check("A4 the quota is on and the fill is on by default",
      d["quota_enabled"] is True and d["auto_enabled"] is True)

t = vp.resolve_config({"dm_visit_quota_per_day": 4, "dm_visit_quota_days": [1, 3, 5],
                       "dm_visit_assign_deadline_dow": 4,
                       "dm_visit_assign_deadline_time": "19:30",
                       "dm_visit_assign_horizon_days": 14})
check("A5 a tenant's own quota wins", t["quota_per_day"] == 4)
check("A6 a tenant's own quota days win", t["quota_days"] == (1, 3, 5))
check("A7 a tenant's own deadline wins", (t["deadline_dow"], t["deadline_time"]) == (4, "19:30"))
check("A8 a tenant's own horizon wins", t["horizon_days"] == 14)

# 0 is a REAL answer ("this tenant does not require a daily visit") and must survive. A negative or
# unreadable one is not an answer, and must fall back to the house 2 rather than to "no quota" —
# a quota nobody is measured on is indistinguishable from the feature being switched off.
check("A9 a quota of 0 survives as a real answer",
      vp.resolve_config({"dm_visit_quota_per_day": 0})["quota_per_day"] == 0)
for bad in (-1, "", "two", None, "   "):
    check("A10 an unreadable quota (%r) falls back to the house 2, never to no quota" % (bad,),
          vp.resolve_config({"dm_visit_quota_per_day": bad})["quota_per_day"] == 2)
check("A11 an out-of-range weekday falls back to Friday",
      vp.resolve_config({"dm_visit_assign_deadline_dow": 9})["deadline_dow"] == 5)
check("A12 a malformed time falls back to the default evening",
      vp.resolve_config({"dm_visit_assign_deadline_time": "25:99"})["deadline_time"] == "17:00")
check("A13 an empty quota-days list falls back to the working week, never to no quota day",
      vp.resolve_config({"dm_visit_quota_days": []})["quota_days"] == (1, 2, 3, 4, 5))
check("A14 a quota-days list of pure nonsense falls back too",
      vp.resolve_config({"dm_visit_quota_days": ["x", 0, 99]})["quota_days"] == (1, 2, 3, 4, 5))

# Every knob the migration adds has a house default here, and vice versa — "nothing hardcoded" means
# a column and a default, in pairs.
for col in ("dm_visit_quota_enabled", "dm_visit_quota_per_day", "dm_visit_quota_days",
            "dm_visit_assign_auto_enabled", "dm_visit_assign_deadline_dow",
            "dm_visit_assign_deadline_time", "dm_visit_assign_horizon_days"):
    check("A15 migration 1050 adds the column %s" % col,
          re.search(r"ADD COLUMN IF NOT EXISTS\s+%s\b" % col, mig_src) is not None)
    check("A16 visit_plan reads %s" % col, col in mod_src)

# ── B. THE SEED IS THE OWNER'S ORDER, PARSED OUT OF THE MIGRATION ─────────────────────────────────
# Parsed, not retyped: this section cannot pass against a seed the migration does not contain.
seed_block = mig_src.split("INSERT INTO storeops.dm_visit_priority_rule")[-1]
seed_rows = re.findall(
    r"\(\s*'[0-9a-f-]{36}'\s*,\s*'[0-9a-f-]{36}'\s*,\s*(\d+)\s*,\s*'([a-z_]+)'\s*,\s*"
    r"'([a-z_]*)'\s*,\s*([\d.]+)\s*,\s*'([a-z_]+)'\s*,\s*'([^']+)'\s*\)", seed_block)
check("B1 migration 1050 seeds exactly three priority rules (got %d)" % len(seed_rows),
      len(seed_rows) == 3)
parsed = [{"sort": int(s), "basis": b, "metric_key": k, "weight": float(w),
           "direction": dr, "label": lb} for (s, b, k, w, dr, lb) in seed_rows]
house = [dict(r) for r in vp.HOUSE_PRIORITY_RULES]
check("B2 the seed and visit_plan.HOUSE_PRIORITY_RULES are the same rules, in the same order",
      parsed == house)
check("B3 the first rule is activations, as the owner asked",
      parsed and parsed[0]["basis"] == "target_category" and parsed[0]["metric_key"] == "activations")
check("B4 the second rule is accessory sales",
      len(parsed) > 1 and parsed[1]["metric_key"] == "accessories")
check("B5 the third rule is KPI, and it is the whole registry, not a chosen KPI",
      len(parsed) > 2 and parsed[2]["basis"] == "kpi_all" and parsed[2]["metric_key"] == "")
check("B6 the weights put activations and accessories ahead of KPI",
      len(parsed) > 2 and parsed[0]["weight"] > parsed[1]["weight"] > parsed[2]["weight"])
check("B7 every seeded rule sends the DM where performance is LOW",
      all(r["direction"] == "low_first" for r in parsed))
# A tenant with no rows of its own still gets the owner's order.
rules, rejected = vp.normalize_rules([])
check("B8 a tenant that configured nothing inherits the owner's order", rules == house and not rejected)
check("B9 the order is ROWS, not code: the migration creates the rule table",
      "CREATE TABLE IF NOT EXISTS storeops.dm_visit_priority_rule" in mig_src)

# A stored rule that cannot be read is DROPPED and NAMED — never quietly scored on something else.
rules, rejected = vp.normalize_rules([
    {"sort": 1, "basis": "target_category", "metric_key": "activations"},
    {"sort": 2, "basis": "crystal_ball", "metric_key": "vibes"},
    {"sort": 3, "basis": "target_category", "metric_key": ""},
    {"sort": 4, "basis": "kpi_metric", "metric_key": "x", "direction": "sideways"},
])
check("B10 only the readable rule survives", [r["metric_key"] for r in rules] == ["activations"])
check("B11 the three unreadable rules are reported, not silently dropped", len(rejected) == 3)
check("B12 an inactive rule is skipped", vp.normalize_rules(
    [{"sort": 1, "basis": "kpi_all", "metric_key": "", "is_active": False},
     {"sort": 2, "basis": "target_category", "metric_key": "upgrades"}])[0][0]["metric_key"] == "upgrades")
check("B13 a zero or negative weight falls back to 1, never to 'this rule does not count'",
      vp.normalize_rules([{"sort": 1, "basis": "kpi_all", "weight": 0}])[0][0]["weight"] == 1.0)

# ── C. THE RANKING IS THE OWNER'S ORDER ───────────────────────────────────────────────────────────
def store(acts=None, accs=None, kpi=None):
    """A store's metrics in the shape the router hands the engine. Values are ATTAINMENT FRACTIONS —
    the engine never divides; `targets_engine.attainment_pct` already did (see §H)."""
    cats = {}
    if acts is not None:
        cats["activations"] = {"attainment": acts}
    if accs is not None:
        cats["accessories"] = {"attainment": accs}
    out = {"categories": cats, "kpi": {}}
    for k, v in (kpi or {}).items():
        out["kpi"][k] = {"attainment": v}
    return out


R = house
metrics = {
    # on target everywhere — should rank last of the measured stores
    "STAR": store(acts=1.0, accs=1.0, kpi={"a": 1.0, "b": 1.0}),
    # activations are the worst thing about this one
    "LOWACT": store(acts=0.2, accs=1.0, kpi={"a": 1.0, "b": 1.0}),
    # accessories are the worst thing about this one, by the same margin
    "LOWACC": store(acts=1.0, accs=0.2, kpi={"a": 1.0, "b": 1.0}),
    # only KPI is short
    "LOWKPI": store(acts=1.0, accs=1.0, kpi={"a": 0.2, "b": 0.2}),
}
ranked = vp.rank_stores(metrics, R)
order = [r["store_code"] for r in ranked]
check("C1 the same shortfall in activations outranks it in accessories (the owner's order)",
      order.index("LOWACT") < order.index("LOWACC"))
check("C2 a shortfall in accessories outranks one in KPI",
      order.index("LOWACC") < order.index("LOWKPI"))
check("C3 a store on target everywhere ranks last", order[-1] == "STAR")
check("C4 the store on target scores zero", ranked[order.index("STAR")]["score"] == 0.0)
check("C5 ranks are 1..N in the order returned",
      [r["rank"] for r in ranked] == list(range(1, len(ranked) + 1)))

# Over-performing never earns negative priority: a store at 300% of its accessory target must not
# outvote the activations rule above it.
m2 = {"A": store(acts=0.9, accs=3.0), "B": store(acts=0.95, accs=0.1)}
o2 = [r["store_code"] for r in vp.rank_stores(m2, R[:2])]
check("C6 being 300% of one target never cancels a shortfall on the rule above it", o2[0] == "B")

# Direction is config, not code.
flip = [dict(R[0], direction="high_first")]
hi = [r["store_code"] for r in vp.rank_stores({"LOW": store(acts=0.1), "HIGH": store(acts=0.9)}, flip)]
check("C7 direction 'high_first' inverts the ranking, so the knob is real", hi[0] == "HIGH")

# Weight is config: raise the KPI rule's weight above the activations rule's and KPI decides.
heavy = [dict(R[0], weight=1.0), dict(R[2], weight=10.0)]
hv = [r["store_code"] for r in vp.rank_stores(
    {"ACT": store(acts=0.0, kpi={"a": 1.0}), "KPI": store(acts=1.0, kpi={"a": 0.0})}, heavy)]
check("C8 a tenant's weights decide the order, so the priority list is genuinely configurable",
      hv[0] == "KPI")

# "then kpi" means the whole active registry, averaged — not one KPI this module chose.
half = vp.metric_attainment(store(kpi={"a": 1.0, "b": 0.0}), {"basis": "kpi_all", "metric_key": ""})
check("C9 kpi_all is the mean across the registry's metrics, so no KPI is privileged", half == 0.5)
check("C10 a carrier-specific deliverable is scored like any other KPI metric",
      vp.metric_attainment(store(kpi={"tenant_specific_metric": 0.4}),
                           {"basis": "kpi_metric", "metric_key": "tenant_specific_metric"}) == 0.4)
check("C11 the reason a store was picked names the rule that picked it",
      "Activations" in vp.reason_text(vp.rank_stores({"X": store(acts=0.1, accs=1.0)}, R)[0]["detail"]))

# ── D. THE HONESTY RULE — an unmeasured store is neither perfect nor failing ──────────────────────
check("D1 no target means no attainment, never 0% and never 100%",
      vp.metric_attainment(store(acts=None), R[0]) is None)
only_unmeasured = {"NOTARGET": {"categories": {}, "kpi": {}},
                   "MEASURED": store(acts=0.5, accs=0.5, kpi={"a": 0.5})}
rk = vp.rank_stores(only_unmeasured, R)
check("D2 a store nothing measured sorts LAST, so the fill never sends a DM there first",
      rk[-1]["store_code"] == "NOTARGET")
check("D3 it is still PRESENT — never dropped from the plan",
      {r["store_code"] for r in rk} == {"NOTARGET", "MEASURED"})
check("D4 it is NAMED as unmeasured, with every rule it could not be read on",
      rk[-1]["detail"]["measured_rules"] == 0 and len(rk[-1]["detail"]["unmeasured"]) == len(R))
check("D5 it scores zero rather than the maximum — it is not a failing store",
      rk[-1]["score"] == 0.0)
# a PARTIALLY measured store scores on what it has, and says what it does not
part = vp.score_store(store(acts=0.0), R)
check("D6 a store measured on one rule of three scores on that one", part[0] == 3.0)
check("D7 and reports the two rules it could not be read on", len(part[1]["unmeasured"]) == 2)
check("D8 the reason line says so when nothing could be measured",
      "No target" in vp.reason_text({"parts": [], "unmeasured": ["a"]}))
# a store whose district has no DM is a FINDING, not a dropped row
spans, unowned = vp.invert_dm_by_store({
    "S1": {"dm": [{"employee_id": "E1", "name": "N", "email": "e@x"}]},
    "S2": {"dm": []},
    "S3": {},
})
check("D9 a store the org tree gives no DM comes back as a finding, not a silent drop",
      unowned == ["S2", "S3"])
check("D10 the DM's span is the stores the one walk gave them",
      spans["E1"]["store_codes"] == {"S1"})
check("D11 a store owned by two DMs lands in BOTH spans rather than one at random",
      vp.invert_dm_by_store({"S": {"dm": [{"employee_id": "A"}, {"employee_id": "B"}]}})[0].keys()
      >= {"A", "B"})

# ── E. THE MARKET MANAGER'S MONITOR ───────────────────────────────────────────────────────────────
cfg = vp.resolve_config(None)
days = [date(2026, 10, 5), date(2026, 10, 6)]        # a Monday and a Tuesday
dms = [{"key": "E1", "name": "One", "email": "one@x"},
       {"key": "E2", "name": "Two", "email": "two@x"}]
assigns = [{"dm_key": "E1", "visit_date": "2026-10-05", "store_code": "A"},
           {"dm_key": "E1", "visit_date": "2026-10-05", "store_code": "B"},
           {"dm_key": "E1", "visit_date": "2026-10-06", "store_code": "C"}]
visits = [{"dm_key": "E1", "visit_date": "2026-10-05", "store_code": "A"},
          {"dm_key": "E1", "visit_date": "2026-10-05", "store_code": "A"},   # the same store twice
          {"dm_key": "E1", "visit_date": "2026-10-06", "store_code": "Z"}]   # never assigned
st = vp.quota_status(assigns, visits, cfg, dms, days)
by = {(r["dm_key"], r["visit_date"]): r for r in st["rows"]}
check("E1 the required number is the tenant's quota", by[("E1", "2026-10-05")]["required"] == 2)
check("E2 two assigned on Monday", by[("E1", "2026-10-05")]["assigned"] == 2)
check("E3 the same store logged twice is ONE visit against the quota",
      by[("E1", "2026-10-05")]["completed"] == 1)
check("E4 one of two done leaves a shortfall of one", by[("E1", "2026-10-05")]["shortfall"] == 1)
check("E5 the store assigned but not visited is named",
      by[("E1", "2026-10-05")]["assigned_not_visited"] == ["B"])
check("E6 a visit to a store nobody assigned STILL counts — the DM did the work",
      by[("E1", "2026-10-06")]["completed"] == 1)
check("E7 and it is named, so the paperwork gap is visible",
      by[("E1", "2026-10-06")]["unassigned_visits"] == ["Z"])
check("E8 a DM with nothing assigned and nothing done is still on the board as a present zero",
      ("E2", "2026-10-05") in by and by[("E2", "2026-10-05")]["assigned"] == 0)
check("E9 that DM's whole quota shows as a shortfall rather than as nothing owed",
      by[("E2", "2026-10-05")]["shortfall"] == 2)
check("E10 the board totals add up over every DM and day",
      st["totals"]["required"] == 2 * len(dms) * len(days))
off = vp.quota_status(assigns, visits, vp.resolve_config({"dm_visit_quota_enabled": False}), dms, days)
check("E11 a tenant with the quota off requires nothing and so owes no shortfall",
      off["required_per_day"] == 0 and off["totals"]["shortfall"] == 0)
# the quota only lands on the tenant's own quota days
qd = vp.quota_dates(date(2026, 10, 3), 7, cfg)        # Sat 3rd → Fri 9th
check("E12 the weekend carries no quota under the default working-week config",
      [d.isoweekday() for d in qd] == [1, 2, 3, 4, 5])
check("E13 a tenant that works Saturdays gets Saturdays",
      6 in [d.isoweekday() for d in vp.quota_dates(
          date(2026, 10, 3), 7, vp.resolve_config({"dm_visit_quota_days": [6]}))])

# ── F. THE DEADLINE IS THE TENANT'S OWN FRIDAY EVENING ───────────────────────────────────────────
check("F1 Thursday is before the deadline", not vp.deadline_reached(datetime(2026, 10, 8, 23, 59), cfg))
check("F2 Friday lunchtime is before the deadline", not vp.deadline_reached(datetime(2026, 10, 9, 12, 0), cfg))
check("F3 one minute before is still before", not vp.deadline_reached(datetime(2026, 10, 9, 16, 59), cfg))
check("F4 Friday 17:00 exactly IS the deadline", vp.deadline_reached(datetime(2026, 10, 9, 17, 0), cfg))
check("F5 Friday evening is past it", vp.deadline_reached(datetime(2026, 10, 9, 20, 0), cfg))
check("F6 Saturday is past it", vp.deadline_reached(datetime(2026, 10, 10, 9, 0), cfg))
tcfg = vp.resolve_config({"dm_visit_assign_deadline_dow": 4, "dm_visit_assign_deadline_time": "20:00"})
check("F7 a tenant that assigns by Thursday 20:00 gets Thursday 20:00",
      vp.deadline_reached(datetime(2026, 10, 8, 20, 0), tcfg)
      and not vp.deadline_reached(datetime(2026, 10, 8, 19, 59), tcfg))
check("F8 the deadline is read from the tenant's clock, not the server's — the module takes no clock",
      "datetime.now" not in mod_src and "utcnow" not in mod_src)
check("F9 the router reads the TENANT's business timezone for that clock",
      "_biz_tz_for" in rtr_src)

# ── G. THE FILL — tops up, never replaces, never leaves a span, never repeats ─────────────────────
spans = {"E1": {"name": "One", "email": "one@x", "store_codes": {"A", "B", "C", "D"}},
         "E2": {"name": "Two", "email": "two@x", "store_codes": {"E"}}}
mx = {"A": store(acts=0.1, accs=0.1), "B": store(acts=0.3, accs=0.3),
      "C": store(acts=0.5, accs=0.5), "D": store(acts=0.9, accs=0.9),
      "E": store(acts=0.2, accs=0.2),
      # a store in NOBODY's span — a perfectly valid store_code, and the worst performer of all
      "FOREIGN": store(acts=0.0, accs=0.0)}
rk = vp.rank_stores(mx, R)
one_day = [date(2026, 10, 12)]

# G1 the manager assigned ONE of the two — the fill adds the second, it does not replace the first
plan = vp.plan_auto_assignments(
    dm_spans=spans, ranked=rk, config=cfg,
    existing=[{"dm_key": "E1", "visit_date": "2026-10-12", "store_code": "D"}], dates=one_day)
e1 = [a["store_code"] for a in plan["assignments"] if a["dm_key"] == "E1"]
check("G1 a day with one manual assignment is topped up to the quota, not replaced", len(e1) == 1)
check("G2 and the manager's own store is left exactly where they put it", "D" not in e1)

# G3 idempotence: feed the first plan back as existing and the second run plans nothing
first = vp.plan_auto_assignments(dm_spans=spans, ranked=rk, config=cfg, existing=[], dates=one_day)
again = vp.plan_auto_assignments(
    dm_spans=spans, ranked=rk, config=cfg,
    existing=[{"dm_key": a["dm_key"], "visit_date": a["visit_date"], "store_code": a["store_code"]}
              for a in first["assignments"]], dates=one_day)
check("G3 running the fill twice plans nothing the second time (the hourly tick is safe)",
      first["assignments"] and not again["assignments"])
check("G4 the worst store in the estate is NEVER assigned to a DM who does not own it",
      all(a["store_code"] != "FOREIGN" for a in first["assignments"]))
check("G5 each DM gets exactly their quota on a day they own enough stores",
      len([a for a in first["assignments"] if a["dm_key"] == "E1"]) == 2)
check("G6 the worst store in a DM's own span is picked first",
      [a["store_code"] for a in first["assignments"] if a["dm_key"] == "E1"][0] == "A")
check("G7 every auto row carries the reason it was picked",
      all(a.get("priority_reason") for a in first["assignments"]))
check("G8 a DM whose span has one store is short by one, and SAYS so rather than looking met",
      any(s.get("dm_key") == "E2" and s.get("slots") == 1 for s in first["short"]))

# G9 across a whole week, a DM never gets the same store twice — one worst store must not become
# five visits while the second-worst is never seen
week = vp.quota_dates(date(2026, 10, 12), 7, cfg)     # Mon–Fri
wk = vp.plan_auto_assignments(dm_spans=spans, ranked=rk, config=cfg, existing=[], dates=week)
e1w = [a["store_code"] for a in wk["assignments"] if a["dm_key"] == "E1"]
check("G9 no store repeats in a DM's filled week", len(e1w) == len(set(e1w)))
check("G10 the week is filled in the priority order", e1w == ["A", "B", "C", "D"])
check("G11 the slots the span could not cover are reported, not left looking met",
      sum(s.get("slots", 0) for s in wk["short"] if s.get("dm_key") == "E1") == 2 * len(week) - 4)
check("G12 a DM the org tree gives no store is reported rather than skipped in silence",
      any(s.get("dm_key") == "E3" for s in vp.plan_auto_assignments(
          dm_spans={"E3": {"name": "Three", "store_codes": set()}}, ranked=rk, config=cfg,
          existing=[], dates=one_day)["short"]))
check("G13 a tenant with the quota off has nothing filled",
      not vp.plan_auto_assignments(
          dm_spans=spans, ranked=rk, config=vp.resolve_config({"dm_visit_quota_enabled": False}),
          existing=[], dates=one_day)["assignments"])
check("G14 a tenant whose quota is 0 has nothing filled",
      not vp.plan_auto_assignments(
          dm_spans=spans, ranked=rk, config=vp.resolve_config({"dm_visit_quota_per_day": 0}),
          existing=[], dates=one_day)["assignments"])
check("G15 a day already at quota is skipped WITH the reason",
      any(s.get("reason", "").startswith("already at quota") for s in vp.plan_auto_assignments(
          dm_spans=spans, ranked=rk, config=cfg,
          existing=[{"dm_key": "E1", "visit_date": "2026-10-12", "store_code": "A"},
                    {"dm_key": "E1", "visit_date": "2026-10-12", "store_code": "B"}],
          dates=one_day)["skipped_days"]))
check("G16 the planner writes nothing — it returns a plan",
      "insert" not in mod_src.lower().replace("insertion", "") and "upsert" not in mod_src.lower())
check("G17 the DB-side idempotence key exists: one store, one DM, one day, once",
      re.search(r"CREATE UNIQUE INDEX IF NOT EXISTS dm_visit_assignment_uniq\s*\n?\s*ON "
                r"storeops\.dm_visit_assignment \(org_id, visit_date, dm_employee_id, store_code\)",
                mig_src) is not None)
check("G18 and the writer targets that key, so the hourly tick cannot duplicate a row",
      'on_conflict="org_id,visit_date,dm_employee_id,store_code"' in rtr_src
      and "ignore_duplicates=True" in rtr_src)
check("G19 the manual assign endpoint refuses a store outside the DM's span",
      "not in this DM's span" in rtr_src)
check("G20 the fill's default is a DRY RUN, so a manager sees it before it happens",
      "send: bool = False" in rtr_src and "dry_run=not send" in rtr_src)

# ── H. THE ONE-HOME LOCKS (these fail the build when a caller stops dereferencing) ────────────────
# H1 ONE attainment formula. It lives in targets_engine and the area roll-up dereferences it.
check("H1 targets_engine defines the one attainment formula",
      "def attainment_pct(" in te_src and "def attainment_fraction(" in te_src)
check("H2 the area roll-up DEREFERENCES it rather than dividing again",
      "acc['attainment_pct'] = attainment_pct(" in te_src)
check("H3 the achieved-over-target division happens EXACTLY once in targets_engine",
      len(re.findall(r"\*\s*safe_float\(achieved\)\s*/", te_src)) == 1)
check("H3b and the area roll-up no longer divides its own totals",
      "achieved_mtd'] /" not in te_src)
check("H4 the visit plan never computes an attainment of its own — it is handed fractions",
      not re.search(r"achieved\w*\s*/\s*\w*monthly", mod_src)
      and not re.search(r"/\s*monthly", mod_src))
check("H5 the router gets its attainments from that one formula",
      "attainment_fraction" in rtr_src)
# H6 ONE org-tree walk. The router must dereference it and must not climb the tree itself.
check("H6 the router dereferences the one org-tree walk", "org_chain.dm_by_store" in rtr_src)
for word in ("org_units", "parent_id", "org_levels"):
    check("H7 the router does not re-walk the org tree (%s)" % word, word not in rtr_src)
# H8 ONE performance source. The plan must not recount activations from the sales feed.
for word in ("daily_sales_feed", "raw_sales", "_compute_feed_actuals", "gp_report"):
    check("H8 nothing here recounts a store's performance from sales (%s)" % word,
          word not in mod_src and word not in rtr_src)
check("H9 the performance numbers come from the Daily Targets summary, the one home",
      "get_targets_summary" in rtr_src)
# H10 ONE carrier deliverable registry — the owner's dropdown reads it, it does not list its own.
check("H10 the dropdown reads the existing per-carrier KPI registry",
      "carrier_kpi_metric" in rtr_src)
check("H11 the dropdown's target categories come from targets_engine, not a list here",
      "targets_engine" in rtr_src and "_te.CATEGORIES" in rtr_src)
check("H12 the carrier the dropdown is specific TO is read from the one config row",
      "commission_org_config" in rtr_src)
check("H13 a rule may not score on a metric no registry offers",
      "not in this tenant's target categories or carrier KPI" in rtr_src)
check("H14 the module names its one homes so the next reader cannot miss them",
      "org_chain.dm_by_store" in mod_src and "targets_engine" in mod_src)
# H15 the divergence this change does NOT fix is stated, not hidden
check("H15 the second store-to-DM mechanism is reported as a divergence rather than ignored",
      "span_divergence" in rtr_src and "target_attribution" in rtr_src)
check("H16 and the board carries that finding to the screen",
      "span_divergence" in page_src or not page_src)

# ── I. RULE TWO — no carrier, tenant or product name anywhere in this change ──────────────────────
BRANDS = ("boost", "luxelink", "vidapay", "t-cetra", "tcetra", "verizon", "at&t", "at-t",
          "t-mobile", "tmobile", "metro", "cricket", "straight talk",
          "tracfone", "xfinity", "edge", "vaccessorize", "cellfonz", "paramount")
def strip_owner_quote(src):
    """Everything EXCEPT the owner's own verbatim words. The owner named two tenants and two products
    when they asked for this; quoting them is provenance, and deleting the quote would lose why the
    feature exists. RULE TWO is about BEHAVIOUR, so the scan below reads every line of the file apart
    from the marked quote — and the markers are checked to exist, so a file cannot evade the scan by
    wrapping its code in them."""
    return re.sub(r"OWNER-QUOTE-BEGIN.*?OWNER-QUOTE-END", "", src, flags=re.S)


for name, src in (("visit_plan.py", mod_src), ("visit_plan_router.py", rtr_src),
                  ("migration 1050", mig_src), ("the plan page", page_src)):
    body = strip_owner_quote(src)
    # Word-boundary matching: 'attainment' is not AT&T, and 'ledger' is not Edge.
    hits = sorted({b for b in BRANDS
                   if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(b), body.lower())})
    check("I1 %s names no carrier, tenant or product; hits=%r" % (name, hits), not hits)
# The marked quote is small and is only ever the ask — not a hiding place for code.
for name, src in (("visit_plan.py", mod_src), ("migration 1050", mig_src)):
    q = re.search(r"OWNER-QUOTE-BEGIN(.*?)OWNER-QUOTE-END", src, flags=re.S)
    check("I1b %s marks the owner's quote so the brand scan can read everything else" % name,
          q is not None)
    check("I1c %s's marked region is the ask alone — under 1,500 characters, no code" % name,
          q is not None and len(q.group(1)) < 1500 and "def " not in q.group(1)
          and "CREATE TABLE" not in q.group(1).upper()
          and "ADD COLUMN" not in q.group(1).upper())
check("I2 the quota, the deadline and the order are all config, so no tenant needs code",
      "HOUSE_CONFIG" in mod_src and "HOUSE_PRIORITY_RULES" in mod_src)
check("I3 the migration says in words that the dropdown is a dereference, not a new list",
      "carrier_kpi_metric" in mig_src)
check("I4 no money is moved or written by this change",
      not re.search(r"\b(payout|commission|rep_commissions)\b", mod_src)
      and "rep_commissions" not in rtr_src)

print("%s  harness_dm_visit_plan: %d checks, %d failed"
      % ("FAIL" if failures else "OK  ", checks, len(failures)))
for f in failures:
    print("   FAILED: %s" % f)
sys.exit(1 if failures else 0)
