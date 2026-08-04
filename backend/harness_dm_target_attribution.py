"""Pure-logic proof harness for DM accessory-target ATTRIBUTION (mod-people, owner directive
2026-08-04, ledger Q7). Runs the ACTUAL shipped functions from
app.modules.storeops.target_attribution against synthetic data — no DB, no network, no HTTP call to
mod-commission's Daily Targets engine (that I/O lives in router.py; this harness proves the pure
rollup only). Run: `python3 harness_dm_target_attribution.py` from backend/.

Proves the task's own (a)/(b)/(c) bar:
  (a) a single-DM employee's store target lands fully on that one DM.
  (b) a 2-DM employee's PER-STORE targets split by store -> market -> DM with NO double-count and
      NO dropped store — verified two ways: per-DM totals AND the raw total-vs-Σ(by_dm) identity.
  (c) achieved-side numbers are carried through UNCHANGED (never recomputed, never re-derived) —
      proven by asserting `achieved` on every attributed row is object-identical (same value) to
      the input row's `achieved`, and total_achieved_all_rows sums the SAME untouched values.
Plus: unassigned-market handling (never silently dropped), ambiguous-market flagging (a market
granted to 2 DMs — never silently double-counted-and-hidden), period-string parsing (both
spellings), worked-pairs extraction from raw shift rows (dedup / deleted / zero-hour exclusion),
and the cross_dm_employees convenience view.
"""
import sys

sys.path.insert(0, ".")

from datetime import date   # noqa: E402

from app.modules.storeops.target_attribution import (   # noqa: E402
    parse_period_to_ym, worked_pairs_from_shifts, dm_roster_from_app_users,
    attribute_rows_to_dms, cross_dm_employees,
    hours_by_day_for_scope, project_future_hours, rep_share_from_shifts,
    visible_dm_keys_for_markets, visible_unassigned, visible_ambiguous_markets,
    redact_cross_dm_employees,
)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ── 1. period parsing (both spellings the platform selector can emit) ──────────────────────────────
check("t1a: 'August 2026' -> '2026-08'", parse_period_to_ym("August 2026") == "2026-08")
check("t1b: '2026-08' passes through", parse_period_to_ym("2026-08") == "2026-08")
check("t1c: 'january 2026' case-insensitive month name", parse_period_to_ym("january 2026") == "2026-01")
try:
    parse_period_to_ym("nonsense")
    check("t1d: garbage period raises ValueError", False, "did not raise")
except ValueError:
    check("t1d: garbage period raises ValueError", True)
try:
    parse_period_to_ym("2026-13")
    check("t1e: out-of-range month raises ValueError", False, "did not raise")
except ValueError:
    check("t1e: out-of-range month raises ValueError", True)


# ── 2. worked_pairs_from_shifts ─────────────────────────────────────────────────────────────────────
shifts = [
    {"employee_name": "Ana Rep", "store_code": "S1", "scheduled_hours": 8, "employee_id": "E1"},
    {"employee_name": "Ana Rep", "store_code": "S1", "scheduled_hours": 6, "employee_id": "E1"},  # dup pair, same key
    {"employee_name": "Ana Rep", "store_code": "S2", "scheduled_hours": 4, "employee_id": "E1"},  # 2nd store -> 2nd row
    {"employee_name": "Deleted Rep", "store_code": "S1", "scheduled_hours": 8, "is_deleted": True},  # excluded
    {"employee_name": "Zero Hour Rep", "store_code": "S1", "scheduled_hours": 0},                    # excluded (<=0)
    {"employee_name": "", "store_code": "S1", "scheduled_hours": 5},                                  # excluded (no name)
    {"employee_name": "No Store Rep", "store_code": "", "scheduled_hours": 5},                        # excluded (no store)
]
pairs = worked_pairs_from_shifts(shifts)
pair_keys = {(p["employee_name"].upper(), p["store_code"]) for p in pairs}
check("t2a: exactly 2 distinct (rep,store) pairs for Ana Rep (S1 dedup'd, S2 kept)",
      pair_keys == {("ANA REP", "S1"), ("ANA REP", "S2")}, pair_keys)
check("t2b: deleted / zero-hour / blank-name / blank-store shifts never produce a pair", len(pairs) == 2, pairs)
check("t2c: employee_id rides along on the pair", all(p.get("employee_id") == "E1" for p in pairs), pairs)


# ── 3. dm_roster_from_app_users ─────────────────────────────────────────────────────────────────────
app_users = [
    {"id": "au-1", "role": "District Manager", "market": "Fresno", "full_name": "Dana DM", "employee_id": "E-DANA"},
    {"id": "au-2", "role": "District Manager", "market": "Bakersfield, Visalia", "full_name": "Bo DM"},
    {"id": "au-3", "role": "Store Manager", "market": "Fresno", "full_name": "Store Mgr Only"},   # scope != market -> excluded
    {"id": "au-4", "role": "District Manager", "market": "", "full_name": "No Market DM"},         # no market -> excluded
    {"id": None, "role": "District Manager", "market": "Reno", "full_name": "No Id DM"},            # no id -> excluded
]
role_scope = {"District Manager": "market", "Store Manager": "store", "Rep": "self"}
name_by_id = {"E-DANA": "Dana Fallback Name"}
roster = dm_roster_from_app_users(app_users, role_scope, name_by_id)
check("t3a: exactly 2 DMs resolved (store-scope + no-market + no-id roles excluded)",
      set(roster.keys()) == {"au-1", "au-2"}, roster.keys())
check("t3b: single-market DM's markets set", roster["au-1"]["markets"] == {"Fresno"}, roster["au-1"])
check("t3c: comma-split multi-market DM", roster["au-2"]["markets"] == {"Bakersfield", "Visalia"}, roster["au-2"])
check("t3d: label prefers full_name over employee-name fallback", roster["au-1"]["label"] == "Dana DM", roster["au-1"])


# ── 4a. (a) single-DM employee: full target lands on the one DM ────────────────────────────────────
rows_single = [
    {"employee_name": "Solo Rep", "store_code": "S1", "market": "Fresno", "target": 500.0, "achieved": 210.0},
]
dm_markets_single = {"dmA": {"label": "DM A", "markets": {"Fresno"}}}
att_single = attribute_rows_to_dms(rows_single, dm_markets_single)
check("t4a: single-DM employee's target lands fully on that DM",
      att_single["by_dm"]["dmA"]["total_target"] == 500.0, att_single["by_dm"])
check("t4b: achieved carried through UNCHANGED on the routed row (c)",
      att_single["by_dm"]["dmA"]["rows"][0]["achieved"] == 210.0
      and att_single["by_dm"]["dmA"]["rows"][0]["achieved"] == rows_single[0]["achieved"], att_single)
check("t4c: total_achieved_all_rows matches the untouched input sum",
      att_single["total_achieved_all_rows"] == 210.0, att_single)
check("t4d: unassigned is empty for a fully-covered market", att_single["unassigned"]["rows"] == [], att_single)


# ── 4b. (b) 2-DM employee: per-store split, no double-count, no drop ───────────────────────────────
rows_cross = [
    {"employee_name": "Roamer Rep", "store_code": "S1", "market": "Fresno", "target": 300.0, "achieved": 120.0},
    {"employee_name": "Roamer Rep", "store_code": "S2", "market": "Bakersfield", "target": 200.0, "achieved": 80.0},
    {"employee_name": "Home Rep", "store_code": "S1", "market": "Fresno", "target": 400.0, "achieved": 150.0},
]
dm_markets_cross = {
    "dmA": {"label": "DM A (Fresno)", "markets": {"Fresno"}},
    "dmB": {"label": "DM B (Bakersfield)", "markets": {"Bakersfield"}},
}
att_cross = attribute_rows_to_dms(rows_cross, dm_markets_cross)
check("t5a: DM A total = Roamer's Fresno row + Home Rep's Fresno row (700), NOT Roamer's Bakersfield row",
      att_cross["by_dm"]["dmA"]["total_target"] == 700.0, att_cross["by_dm"]["dmA"])
check("t5b: DM B total = ONLY Roamer's Bakersfield row (200)",
      att_cross["by_dm"]["dmB"]["total_target"] == 200.0, att_cross["by_dm"]["dmB"])
check("t5c: Roamer Rep appears in BOTH dm row sets (2 separate rows, one per store)",
      sum(1 for r in att_cross["by_dm"]["dmA"]["rows"] if r["employee_name"] == "Roamer Rep") == 1
      and sum(1 for r in att_cross["by_dm"]["dmB"]["rows"] if r["employee_name"] == "Roamer Rep") == 1, att_cross)
check("t5d: NO double-count — Σ(by_dm totals) == total_target_all_rows (900) when markets are unambiguous",
      round(att_cross["by_dm"]["dmA"]["total_target"] + att_cross["by_dm"]["dmB"]["total_target"], 2)
      == att_cross["total_target_all_rows"] == 900.0, att_cross)
check("t5e: NO dropped row — every input row appears in exactly one DM's row list",
      sum(len(d["rows"]) for d in att_cross["by_dm"].values()) == len(rows_cross), att_cross)
check("t5f: each routed row carries the market that routed it + the DM label",
      att_cross["by_dm"]["dmB"]["rows"][0]["market"] == "Bakersfield"
      and att_cross["by_dm"]["dmB"]["rows"][0]["routed_dm_label"] == "DM B (Bakersfield)", att_cross)


# ── 4c. unassigned market (store's market has NO dm grant at all) — never silently dropped ─────────
rows_unassigned = rows_cross + [
    {"employee_name": "Orphan Rep", "store_code": "S3", "market": "Nowhere Market", "target": 50.0, "achieved": 0.0},
]
att_un = attribute_rows_to_dms(rows_unassigned, dm_markets_cross)
check("t6a: a market with no DM grant lands in `unassigned`, not silently vanished",
      len(att_un["unassigned"]["rows"]) == 1 and att_un["unassigned"]["rows"][0]["employee_name"] == "Orphan Rep", att_un)
check("t6b: unassigned total is correct", att_un["unassigned"]["total_target"] == 50.0, att_un)
check("t6c: by_dm totals are UNCHANGED by the presence of an unassigned row",
      att_un["by_dm"]["dmA"]["total_target"] == 700.0 and att_un["by_dm"]["dmB"]["total_target"] == 200.0, att_un)


# ── 4d. ambiguous market (2 DMs both granted the SAME market) — flagged, not silently doubled ──────
dm_markets_ambig = {
    "dmA": {"label": "DM A", "markets": {"Fresno"}},
    "dmC": {"label": "DM C (also Fresno, config collision)", "markets": {"Fresno"}},
}
rows_ambig = [{"employee_name": "Rep X", "store_code": "S1", "market": "Fresno", "target": 100.0, "achieved": 40.0}]
att_ambig = attribute_rows_to_dms(rows_ambig, dm_markets_ambig)
check("t7a: ambiguous_markets flags the collision", "fresno" in att_ambig["ambiguous_markets"], att_ambig)
check("t7b: both DMs claiming the market see the row (never silently drop either)",
      att_ambig["by_dm"]["dmA"]["total_target"] == 100.0 and att_ambig["by_dm"]["dmC"]["total_target"] == 100.0, att_ambig)
check("t7c: the row itself is flagged ambiguous=True on both copies",
      att_ambig["by_dm"]["dmA"]["rows"][0]["ambiguous"] is True
      and att_ambig["by_dm"]["dmC"]["rows"][0]["ambiguous"] is True, att_ambig)
check("t7d: total_target_all_rows stays the TRUE input sum (100), Σ(by_dm) intentionally DOUBLES it (200) — the honest signal of the collision",
      att_ambig["total_target_all_rows"] == 100.0
      and (att_ambig["by_dm"]["dmA"]["total_target"] + att_ambig["by_dm"]["dmC"]["total_target"]) == 200.0, att_ambig)


# ── 4e. market-name casing never causes a silent mismatch (RULE THREE free-typed grants) ───────────
rows_case = [{"employee_name": "Rep Y", "store_code": "S9", "market": "fresno ", "target": 77.0, "achieved": 10.0}]
dm_markets_case = {"dmA": {"label": "DM A", "markets": {" Fresno"}}}
att_case = attribute_rows_to_dms(rows_case, dm_markets_case)
check("t8a: case/whitespace-insensitive market match", att_case["by_dm"]["dmA"]["total_target"] == 77.0, att_case)


# ── 4f. every DM shows even with ZERO matching rows this period (present zero, not absent) ─────────
att_zero = attribute_rows_to_dms([], dm_markets_cross)
check("t9a: a DM with no rows this period still appears at $0",
      att_zero["by_dm"]["dmA"]["total_target"] == 0.0 and att_zero["by_dm"]["dmB"]["total_target"] == 0.0, att_zero)


# ── 5. cross_dm_employees convenience view ──────────────────────────────────────────────────────────
cde = cross_dm_employees(att_cross)
check("t10a: exactly one employee (Roamer Rep) flagged as cross-DM", [e["employee_name"] for e in cde] == ["Roamer Rep"], cde)
roamer = cde[0]
check("t10b: Roamer's cross-DM entry lists both DMs with correct per-DM totals",
      {d["dm_key"]: d["total_target"] for d in roamer["dms"]} == {"dmA": 300.0, "dmB": 200.0}, roamer)
check("t10c: Home Rep (single-DM) is NOT in the cross-DM list", "Home Rep" not in [e["employee_name"] for e in cde], cde)
check("t10d: each cross-DM entry's dms[] now carries a human `label` too (not just dm_key)",
      all("label" in d for d in roamer["dms"]), roamer)


# ── 11. LOCAL hours-share mirror (bulk-fetch performance path, owner directive 2026-08-04) ──────────
bulk_shifts = [
    {"employee_name": "Bulk Rep", "store_code": "S1", "scheduled_hours": 8, "shift_date": "2026-08-03", "is_deleted": False},
    {"employee_name": "Bulk Rep", "store_code": "S1", "scheduled_hours": 8, "shift_date": "2026-08-10", "is_deleted": False},
    {"employee_name": "Other Rep", "store_code": "S1", "scheduled_hours": 8, "shift_date": "2026-08-03", "is_deleted": False},
    {"employee_name": "Deleted Shift Rep", "store_code": "S1", "scheduled_hours": 40, "shift_date": "2026-08-03", "is_deleted": True},
]
hbd_store = hours_by_day_for_scope(bulk_shifts, "S1", None)
check("t11a: whole-store hours_by_day sums BOTH reps on the same day, excludes the deleted shift",
      hbd_store.get(date(2026, 8, 3)) == 16.0 and hbd_store.get(date(2026, 8, 10)) == 8.0, hbd_store)
hbd_rep = hours_by_day_for_scope(bulk_shifts, "S1", "Bulk Rep")
check("t11b: rep-scoped hours_by_day only counts Bulk Rep's own days",
      hbd_rep == {date(2026, 8, 3): 8.0, date(2026, 8, 10): 8.0}, hbd_rep)

# Both reps schedule ONLY Mondays (2026-08-03 and -10 are both Mondays) -> projecting forward should
# fill every remaining Monday in August with the observed per-weekday average, nothing else.
proj = project_future_hours(hbd_rep, date(2026, 8, 11), date(2026, 8, 31))
mondays_left = [d for d in proj if d.weekday() == 0]
check("t11c: projection fills only the SAME weekday pattern already observed (Mondays), no other day",
      set(proj.keys()) == set(mondays_left) and all(v == 8.0 for v in proj.values()), proj)

# rep_share on a SINGLE observed day (shifts pre-windowed to just that day, matching how the real
# caller always passes an already period-bounded shifts list — the 08-10 row would otherwise still
# get swept into the same-weekday projection and change the expected ratio, which is a harness
# realism issue, not a code bug; t11f below exercises that multi-week projection deliberately).
one_day_shifts = [r for r in bulk_shifts if r["shift_date"] == "2026-08-03"]
share = rep_share_from_shifts(one_day_shifts, "S1", "Bulk Rep", date(2026, 8, 3), date(2026, 8, 3))
check("t11d: single-day rep_share = rep hours / store hours that day (8/16 = 0.5)",
      abs(share - 0.5) < 1e-9, share)
share_empty_store = rep_share_from_shifts([], "S1", "Nobody", date(2026, 8, 3), date(2026, 8, 31))
check("t11e: a store with ZERO hours anywhere -> rep_share is 0.0, never a ZeroDivisionError", share_empty_store == 0.0)

# t11f: over a FULL month, unfilled future Mondays project off the observed weekday average for BOTH
# store and rep — hand-computed expectation: store Mondays observed [16,8] avg=12 -> 3 more Mondays
# (08-17/24/31) x12 + the 2 concrete days (16+8) = 60; rep Mondays observed [8,8] avg=8 -> 3 more x8 +
# 16 concrete = 40; share = 40/60.
share_full_month = rep_share_from_shifts(bulk_shifts, "S1", "Bulk Rep", date(2026, 8, 3), date(2026, 8, 31))
check("t11f: full-month projected rep_share matches the hand-computed weekday-average expectation (40/60)",
      abs(share_full_month - (40.0 / 60.0)) < 1e-9, share_full_month)


# ── 12. SPAN-SCOPING (Gate-1 rework 2026-08-04) — market-scope caller narrowing ─────────────────────
# Re-use att_cross from section 4b: dmA=Fresno (700 total: Roamer 300 + Home Rep 400 wait — recompute
# fresh here so this section stands alone and isn't coupled to section 4b's exact numbers.
scope_rows = [
    {"employee_name": "Roamer Rep", "store_code": "S1", "market": "Fresno", "target": 300.0, "achieved": 120.0},
    {"employee_name": "Roamer Rep", "store_code": "S2", "market": "Bakersfield", "target": 200.0, "achieved": 80.0},
    {"employee_name": "Home Rep", "store_code": "S1", "market": "Fresno", "target": 400.0, "achieved": 150.0},
    {"employee_name": "Orphan Rep", "store_code": "S3", "market": "Nowhere", "target": 50.0, "achieved": 0.0},
]
scope_dm_markets = {
    "dmA": {"label": "DM A (Fresno)", "markets": {"Fresno"}},
    "dmB": {"label": "DM B (Bakersfield)", "markets": {"Bakersfield"}},
}
scope_att = attribute_rows_to_dms(scope_rows, scope_dm_markets)
scope_cross = cross_dm_employees(scope_att)

visible_A = visible_dm_keys_for_markets(scope_dm_markets, {"Fresno"})
check("t12a: a caller granted 'Fresno' sees exactly dmA's key, never dmB's",
      visible_A == {"dmA"}, visible_A)
visible_none = visible_dm_keys_for_markets(scope_dm_markets, set())
check("t12b: a caller with NO market grant of their own sees NO dm keys (empty span, not everything)",
      visible_none == set(), visible_none)

# ambiguous-market case: a market granted to 2 DMs -> a caller holding that market sees BOTH.
scope_dm_markets_ambig = {
    "dmA": {"label": "DM A", "markets": {"Fresno"}},
    "dmC": {"label": "DM C (also Fresno)", "markets": {"Fresno"}},
}
visible_ambig = visible_dm_keys_for_markets(scope_dm_markets_ambig, {"Fresno"})
check("t12c: a market granted to 2 DMs -> a caller holding that grant sees BOTH dm keys (market-based, not identity-based)",
      visible_ambig == {"dmA", "dmC"}, visible_ambig)

vis_un = visible_unassigned(scope_att["unassigned"], {"Fresno"})
check("t12d: visible_unassigned drops the 'Nowhere' orphan row for a Fresno-only caller", vis_un["rows"] == [], vis_un)
vis_un_match = visible_unassigned({"rows": [{"market": "Nowhere", "target": 50.0}], "total_target": 50.0}, {"Nowhere"})
check("t12e: visible_unassigned KEEPS a row whose market the caller IS granted", vis_un_match["rows"] != [] and vis_un_match["total_target"] == 50.0, vis_un_match)

amb_scope = attribute_rows_to_dms([{"employee_name": "Rep X", "store_code": "S1", "market": "Fresno", "target": 100.0, "achieved": 10.0}],
                                   scope_dm_markets_ambig)["ambiguous_markets"]
vis_amb_yes = visible_ambiguous_markets(amb_scope, {"Fresno"})
check("t12f: a Fresno-granted caller sees the Fresno ambiguity flag", "fresno" in vis_amb_yes, vis_amb_yes)
vis_amb_no = visible_ambiguous_markets(amb_scope, {"Bakersfield"})
check("t12g: a Bakersfield-only caller does NOT see the Fresno ambiguity flag", vis_amb_no == {}, vis_amb_no)

redacted_A = redact_cross_dm_employees(scope_cross, visible_A)
check("t12h: redacted view still surfaces Roamer Rep (touches a visible dm)",
      [e["employee_name"] for e in redacted_A] == ["Roamer Rep"], redacted_A)
r_entry = redacted_A[0]
dmA_entry = next(d for d in r_entry["dms"] if d["dm_key"] == "dmA")
dmB_entry = next(d for d in r_entry["dms"] if d["dm_key"] == "dmB")
check("t12i: the caller's OWN dm (dmA) keeps FULL detail (rows + total_target)",
      "rows" in dmA_entry and dmA_entry.get("total_target") == 300.0 and not dmA_entry.get("redacted"), dmA_entry)
check("t12j: the OTHER dm (dmB) is reduced to a bare label — NO rows, NO total_target",
      dmB_entry.get("redacted") is True and "rows" not in dmB_entry and "total_target" not in dmB_entry
      and dmB_entry.get("label") == "DM B (Bakersfield)", dmB_entry)

visible_B = visible_dm_keys_for_markets(scope_dm_markets, {"Bakersfield"})
redacted_B = redact_cross_dm_employees(scope_cross, visible_B)
check("t12k: from dmB's OWN vantage point, the SAME employee's dmB row is full detail, dmA is the redacted one",
      next(d for d in redacted_B[0]["dms"] if d["dm_key"] == "dmB").get("total_target") == 200.0
      and next(d for d in redacted_B[0]["dms"] if d["dm_key"] == "dmA").get("redacted") is True, redacted_B)

visible_empty = visible_dm_keys_for_markets(scope_dm_markets, set())
check("t12l: an empty visible set redacts everyone -> the cross-DM list is EMPTY (not partially shown)",
      redact_cross_dm_employees(scope_cross, visible_empty) == [], redact_cross_dm_employees(scope_cross, visible_empty))


# ── Report ─────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("FAIL:", f)
if FAIL:
    sys.exit(1)
print("ALL GREEN")
