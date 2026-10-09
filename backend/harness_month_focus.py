#!/usr/bin/env python3
"""PROOF — the month's declared focus and the weekly check-in (owner ask 2026-10-09, index §63).

DB-FREE, stdlib only, network-free. Drives the REAL pure functions with fixtures, so what is proved
here is what runs in production.

  §A  the calendar — every check-in day, and which one a reminder is ABOUT
  §B  the declaration — partial saves, bounds, and a blank rate that is not a $0 rate
  §C  the check-in — idempotent, and never writable from the client payload
  §D  declared against live — both directions, and `proposed` raising no gap
  §E  the due list — every item fires on its cause and clears when it is fixed
  §F  measurement vs failure — None is never zero
  §G  the plays — each fires on a measured number, carries it, and no filler play is ever invented
  §H  who may declare — dereferenced from `core.scope.roster_reach`, never a fourth copy
  §I  THE UN-WIRE LOCK — this module may not measure, send, or pay; the homes it reads must still be
      dereferenced; and every control here is ARMED against a planted violation
"""
import ast
import os
import sys
from datetime import date

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from app.modules.commcalc import month_focus as M              # noqa: E402

FAIL = []


def ck(name, cond, got=None):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}" + (f"  got={got!r}" if got is not None else "")); FAIL.append(name)


ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(ROOT, "app/modules/commcalc/month_focus.py")).read()
TREE = ast.parse(SRC)

# ── §A  THE CALENDAR ──────────────────────────────────────────────────────────────────────────────
print("\nA. the month, its check-in days, and which one the reminder is about")
ck("A1. October 2026's Mondays are the four real ones",
   [d.isoformat() for d in M.checkin_days(2026, 10)]
   == ["2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26"],
   [d.isoformat() for d in M.checkin_days(2026, 10)])
ck("A2. a month whose 1st IS the check-in weekday includes the 1st",
   M.checkin_days(2026, 6)[0] == date(2026, 6, 1), M.checkin_days(2026, 6)[0])
ck("A3. the weekday is a setting, not Monday in code — Thursday asked for, Thursdays returned",
   all(d.weekday() == 3 for d in M.checkin_days(2026, 10, 3)))
ck("A4. the reminder is about the LATEST check-in day on or before today",
   M.current_checkin("2026-10-09", 2026, 10) == date(2026, 10, 5))
ck("A5. on the check-in day itself, it is that day — not last week's",
   M.current_checkin("2026-10-12", 2026, 10) == date(2026, 10, 12))
ck("A6. before the month's first check-in day NOTHING is due",
   M.current_checkin("2026-10-02", 2026, 10) is None)
ck("A7. a PAST month is not nagged about its Mondays",
   M.current_checkin("2026-11-09", 2026, 10) is None)
ck("A8. a FUTURE month is not asked to confirm a week that has not happened",
   M.current_checkin("2026-09-30", 2026, 10) is None)
ck("A9. the declaration window ends N days in, and never past the month",
   M.declaration_window_end(2026, 10, 7) == date(2026, 10, 7)
   and M.declaration_window_end(2026, 2, 60) == date(2026, 2, 28))
ck("A10. junk in, nothing out — never a crash and never a guessed month",
   M.month_bounds("x", 99) == (None, None) and M.checkin_days(2026, 13) == []
   and M.current_checkin("not-a-day", 2026, 10) is None)

# ── §B  THE DECLARATION ───────────────────────────────────────────────────────────────────────────
print("\nB. the declaration — partial saves, bounds, and a blank rate")
full = M.normalise_declaration({
    "headline": "  Tablets   and attach  ", "categories": ["Tablets", "tablets", "Accessories"],
    "spiff_initiative": {"pay_type": "alpha spiff", "label": "Alpha", "rationale": "largest"},
    "temp_spiffs": [{"name": "Attach ladder", "rate": "$7.50", "unit": "per box",
                     "window_start": "2026-10-10", "window_end": "2026-10-31",
                     "cap": "2,000", "stores": ["S-1", "S-2"], "status": "approved"},
                    {"name": "attach ladder", "rate": 1},          # same name folded — dropped
                    {"name": "Unpriced push", "rate": "", "status": "nonsense"}],
    "target_note": "every store", "note": "n"}, actor="Ada", now_iso="2026-10-09T10:00:00Z")
ck("B1. whitespace is folded and the headline is the manager's own words",
   full["headline"] == "Tablets and attach", full["headline"])
ck("B2. a category repeated in another case is ONE category",
   full["categories"] == ["Tablets", "Accessories"], full["categories"])
ck("B3. a money string is a number", full["temp_spiffs"][0]["rate"] == 7.5
   and full["temp_spiffs"][0]["cap"] == 2000.0)
ck("B4. two spiffs with the same folded name are one spiff",
   len(full["temp_spiffs"]) == 2, [s["name"] for s in full["temp_spiffs"]])
ck("B5. A BLANK RATE IS NONE, NEVER 0.00 — a spiff nobody priced is not a free spiff",
   full["temp_spiffs"][1]["rate"] is None, full["temp_spiffs"][1]["rate"])
ck("B6. an unrecognised status falls to 'proposed', never to a paying one",
   full["temp_spiffs"][1]["status"] == "proposed", full["temp_spiffs"][1]["status"])
ck("B7. the actor and the time are recorded", full["declared_by"] == "Ada"
   and full["declared_at"] == "2026-10-09T10:00:00Z")
partial = M.normalise_declaration({"spiff_initiative": {"pay_type": "beta"}},
                                  actor="Bo", now_iso="t2", existing=full)
ck("B8. A PARTIAL SAVE CANNOT BLANK THE MONTH — the headline survives saving the initiative",
   partial["headline"] == "Tablets and attach" and partial["temp_spiffs"] == full["temp_spiffs"],
   partial["headline"])
ck("B9. the field that WAS sent is the one that changes",
   partial["spiff_initiative"]["pay_type"] == "beta")
ck("B10. bounded — a flood of spiffs and categories is cut, not stored",
   len(M.normalise_declaration({"temp_spiffs": [{"name": f"s{i}"} for i in range(50)],
                                "categories": [f"c{i}" for i in range(50)]})["temp_spiffs"])
   == M.MAX_TEMP_SPIFFS)
ck("B11. a row that exists with no headline is NOT declared — a half-filled form is not a focus",
   M.is_declared({"categories": ["x"], "headline": ""}) is False and M.is_declared(full) is True)
ck("B12. junk in, a valid declaration out", isinstance(M.normalise_declaration(None), dict)
   and M.normalise_declaration("nope")["headline"] == "")
ck("B13. a bad date is dropped, never guessed",
   M.normalise_declaration({"temp_spiffs": [{"name": "x", "window_start": "31/10/2026"}]}
                           )["temp_spiffs"][0]["window_start"] == "")

# ── §C  THE CHECK-IN ──────────────────────────────────────────────────────────────────────────────
print("\nC. the weekly check-in — idempotent, and not writable from the payload")
d1, ch1 = M.confirm_checkin({}, "2026-10-05", actor="Ada", now_iso="t1", changes=["new spiff"])
ck("C1. confirming a week records it", ch1 and M.confirmed_checkins(d1) == {"2026-10-05"})
d2, ch2 = M.confirm_checkin(d1, "2026-10-05", actor="Bo", now_iso="t9")
ck("C2. IDEMPOTENT — a second confirmation writes nothing and moves no timestamp",
   ch2 is False and d2 == d1)
d3, ch3 = M.confirm_checkin(d1, "2026-10-12", actor="Bo", now_iso="t2")
ck("C3. the next week is its own confirmation",
   ch3 and M.confirmed_checkins(d3) == {"2026-10-05", "2026-10-12"})
d4, ch4 = M.confirm_checkin(d1, "whenever")
ck("C4. a junk week changes nothing", ch4 is False and d4 == d1)
ck("C5. THE PAYLOAD CANNOT MARK A WEEK CONFIRMED — normalise never reads `checkins` from the body",
   M.normalise_declaration({"checkins": [{"week_start": "2026-10-19"}]},
                           existing=d1)["checkins"] == d1["checkins"])

# ── §D  DECLARED AGAINST LIVE ─────────────────────────────────────────────────────────────────────
print("\nD. the declaration against the pay config — both directions")
decl = M.normalise_declaration({"headline": "h", "temp_spiffs": [
    {"name": "Wired one", "rate": 5, "status": "live"},
    {"name": "Approved but unwired", "rate": 5, "status": "approved"},
    {"name": "Just an idea", "rate": 5, "status": "proposed"}]})
rec = M.spiff_reconciliation(decl, ["wired  ONE", "Mystery money"])
ck("D1. a spiff in both is matched (folded, so spelling does not split it)",
   rec["matched"] == ["Wired one"], rec["matched"])
ck("D2. an APPROVED spiff the pay config lacks is reported — nobody is being paid it",
   rec["declared_not_live"] == ["Approved but unwired"], rec["declared_not_live"])
ck("D3. THE REVERSE DEFECT — a spiff being paid that the month never named is reported too",
   rec["live_not_declared"] == ["Mystery money"], rec["live_not_declared"])
ck("D4. a PROPOSED spiff raises NO gap — it is on the table, not in the money",
   rec["proposed"] == ["Just an idea"] and "Just an idea" not in rec["declared_not_live"])
ck("D5. an empty pay config means EVERY approved spiff is unwired, and still no proposed one",
   M.spiff_reconciliation(decl, [])["declared_not_live"]
   == ["Approved but unwired", "Wired one"]
   and M.spiff_reconciliation(decl, [])["live_not_declared"] == [],
   M.spiff_reconciliation(decl, []))

# ── §E  THE DUE LIST ──────────────────────────────────────────────────────────────────────────────
print("\nE. the due list — each item fires on its cause and CLEARS when it is fixed")
KW = dict(year=2026, month=10, declaration_days=7, checkin_weekday=0)


def keys(decl, today, **kw):
    return [i["key"] for i in M.outstanding(decl, today, **{**KW, **kw})]


ck("E1. an undeclared month inside the window is a WARNING to declare",
   [i["severity"] for i in M.outstanding({}, "2026-10-03", **KW) if i["key"] == "focus_undeclared"]
   == ["warning"])
ck("E2. past the window it is an ERROR — a month half gone is not a reminder",
   [i["severity"] for i in M.outstanding({}, "2026-10-20", **KW) if i["key"] == "focus_undeclared"]
   == ["error"])
ck("E3. declaring it CLEARS the item",
   "focus_undeclared" not in keys({"headline": "Tablets"}, "2026-10-20"))
ck("E4. a focus with no initiative named is its own item, and naming one clears it",
   "initiative_unnamed" in keys({"headline": "h"}, "2026-10-20")
   and "initiative_unnamed" not in keys(
       {"headline": "h", "spiff_initiative": {"pay_type": "alpha"}}, "2026-10-20"))
ck("E5. a declared spiff with no rate cannot be costed, so it is an item",
   "spiff_unpriced" in keys({"headline": "h", "spiff_initiative": {"pay_type": "a"},
                             "temp_spiffs": [{"name": "x", "rate": None}]}, "2026-10-20"))
ck("E6. THE MONDAY REMINDER — due on the check-in day and still due on the Friday after",
   "checkin_due" in keys({"headline": "h"}, "2026-10-12")
   and "checkin_due" in keys({"headline": "h"}, "2026-10-16"))
ck("E7. doing the check-in clears it, and only for THAT week",
   "checkin_due" not in keys({"headline": "h", "checkins": [{"week_start": "2026-10-12"}]},
                             "2026-10-16")
   and "checkin_due" in keys({"headline": "h", "checkins": [{"week_start": "2026-10-12"}]},
                             "2026-10-19"))
ck("E8. stores with no target is an item, and covering them clears it",
   "targets_unassigned" in keys({"headline": "h"}, "2026-10-12", stores_total=29,
                                stores_with_target=6)
   and "targets_unassigned" not in keys({"headline": "h"}, "2026-10-12", stores_total=29,
                                        stores_with_target=29))
ck("E9. an approved-but-unwired spiff is an ERROR — the incentive exists on the screen only",
   [i["severity"] for i in M.outstanding(
       {"headline": "h", "spiff_initiative": {"pay_type": "a"},
        "temp_spiffs": [{"name": "x", "rate": 5, "status": "approved"}]}, "2026-10-12", **KW)
    if i["key"] == "spiff_declared_not_live"] == ["error"])
ck("E10. EVERY item lands somewhere it can be fixed from",
   all(i["deep_link"] in (M.PAGE, M.TARGETS_PAGE, M.PAY_CONFIG_PAGE) and i["deep_link_label"]
       for i in M.outstanding({}, "2026-10-20", **{**KW, "stores_total": 2,
                                                   "stores_with_target": 0,
                                                   "live_spiff_names": ["z"]})))
ck("E11. a fully-kept month is SILENT — zero items, so the popup renders nothing",
   M.outstanding({"headline": "h", "spiff_initiative": {"pay_type": "a"},
                  "temp_spiffs": [{"name": "x", "rate": 5, "status": "live"}],
                  "checkins": [{"week_start": "2026-10-05"}]},
                 "2026-10-09", **{**KW, "stores_total": 3, "stores_with_target": 3,
                                  "live_spiff_names": ["x"]}) == [])
ck("E12. a month that is not a month raises nothing rather than crashing",
   M.outstanding({}, "2026-10-09", year=0, month=0) == []
   and M.outstanding({}, "junk", **KW) == [])

# ── §F  MEASUREMENT VS FAILURE ────────────────────────────────────────────────────────────────────
print("\nF. a measurement that did not happen is NOT a zero")
ck("F1. unmeasured targets raise NO item — 'no store has a target' and 'I could not count' differ",
   "targets_unassigned" not in keys({"headline": "h"}, "2026-10-12", stores_total=None,
                                    stores_with_target=None))
ck("F2. a measured zero DOES raise it — that is a real management gap",
   "targets_unassigned" in keys({"headline": "h"}, "2026-10-12", stores_total=10,
                                stores_with_target=0))
ck("F3. a roster of zero stores raises nothing — there is nothing to target",
   "targets_unassigned" not in keys({"headline": "h"}, "2026-10-12", stores_total=0,
                                    stores_with_target=0))
ck("F4. a cost cannot be printed from an unknown rate or unknown units",
   M.cost_at(10, None) is None and M.cost_at(None, 5) is None and M.cost_at(10, 5) == 50.0)

# ── §G  THE PLAYS ─────────────────────────────────────────────────────────────────────────────────
print("\nG. the plays — a rule over a measured number, and never a filler play")
ck("G1. NO SIGNAL, NO PLAY — nothing is invented to fill the panel",
   M.plays({}) == [] and M.plays(None) == [] and M.plays({"lagging": [], "spiff": {}}) == [])
p_t = M.plays({"stores_without_target": ["S-1", "S-2"], "stores_total": 10})
ck("G2. missing targets is the FIRST play — it costs nothing and every other play needs it",
   [p["key"] for p in p_t] == ["target_first"] and p_t[0]["rank"] == 1)
ck("G3. it carries the number behind it", p_t[0]["evidence"]["without_target"] == 2
   and p_t[0]["evidence"]["stores_total"] == 10)
p_f = M.plays({"spiff": {"label": "alpha", "rate_per_unit": 11.0, "zero_stores": ["A", "B"],
                         "median_units_per_100_boxes": 10.0, "zero_store_boxes": 400}})
ck("G4. money the carrier already pays outranks anything that spends",
   p_f[0]["key"] == "free_money" and p_f[0]["rank"] == 2)
ck("G5. the forgone estimate is at the PEER MEDIAN and says so — 400 boxes x 10/100 x $11",
   p_f[0]["evidence"]["estimated_forgone"] == 440.0
   and p_f[0]["evidence"]["estimate_basis"] == "peer median units per 100 boxes",
   p_f[0]["evidence"])
ck("G6. with no median measured there is no estimate — and the play still stands on the rate",
   M.plays({"spiff": {"label": "a", "rate_per_unit": 11.0, "zero_stores": ["A"]}}
           )[0]["evidence"]["estimated_forgone"] is None)
ck("G7. a $0-rate pay type is NOT offered as free money",
   M.plays({"spiff": {"label": "a", "rate_per_unit": 0, "zero_stores": ["A"]}}) == [])
p_c = M.plays({"lagging": [{"store": "A", "metric": "boxes", "gap_pct": 12.0, "median": 100},
                           {"store": "B", "metric": "boxes", "gap_pct": 41.0, "median": 100}]})
ck("G8. the catch-up play names the stores and the WORST gap",
   p_c[0]["key"] == "catch_up_band" and p_c[0]["evidence"]["worst_store"] == "B"
   and p_c[0]["evidence"]["worst_gap_pct"] == 41.0)
p_a = M.plays({"lagging": [{"store": "A", "metric": "accessory_per_box", "gap_pct": 9.0,
                            "median": 42.0}]})
ck("G9. an accessory gap gets the LADDER, not the per-box flat spiff",
   p_a[0]["key"] == "attach_ladder" and "ABOVE the band median" in p_a[0]["move"])
ck("G10. concentration fires only when one store holds most of it AND others earn some",
   [p["key"] for p in M.plays({"spiff": {"top_store": "A", "top_store_share_pct": 81.0,
                                         "earning_stores": 4}})] == ["concentration"]
   and M.plays({"spiff": {"top_store": "A", "top_store_share_pct": 100.0,
                          "earning_stores": 1}}) == []
   and M.plays({"spiff": {"top_store": "A", "top_store_share_pct": 40.0,
                          "earning_stores": 4}}) == [])
ck("G11. a pay type nobody earned at all is its own, free play",
   M.plays({"unearned_pay_types": [{"label": "gamma", "rate_per_unit": 3.0}]}
           )[0]["key"] == "unearned_type")
every = M.plays({"stores_without_target": ["S"], "stores_total": 2,
                 "unearned_pay_types": [{"label": "g"}],
                 "spiff": {"label": "a", "rate_per_unit": 9.0, "zero_stores": ["A"],
                           "top_store": "A", "top_store_share_pct": 90.0, "earning_stores": 3},
                 "lagging": [{"store": "A", "metric": "boxes", "gap_pct": 5.0, "median": 1},
                             {"store": "B", "metric": "accessory_per_box", "gap_pct": 5.0,
                              "median": 1}]})
ck("G12. ranked free-things-first, spending after",
   [p["key"] for p in every] == list(M.PLAY_KEYS), [p["key"] for p in every])
ck("G13. EVERY play carries evidence and a page to act on",
   all(p["evidence"] and p["deep_link"] and p["why"] and p["move"] for p in every))
ck("G14. EVERY play's reason carries a figure — a suggestion with no number behind it is a horoscope",
   all(any(c.isdigit() for c in p["why"]) for p in every),
   [p["why"] for p in every if not any(c.isdigit() for c in p["why"])])

# ── §H  WHO MAY DECLARE ───────────────────────────────────────────────────────────────────────────
print("\nH. market manager or above — dereferenced, not re-decided")
ck("H1. a market manager may", M.may_declare({"scope": "market"}) is True)
ck("H2. a region / company-wide role may",
   M.may_declare({"scope": "region"}) and M.may_declare({"scope": "all"}))
ck("H3. A REP AND A STORE MANAGER MAY NOT — the defect `roster_reach` would have shipped",
   M.may_declare({"scope": "store"}) is False and M.may_declare({"scope": "self"}) is False)
ck("H4. a platform super admin always may", M.may_declare({"__super_admin": True,
                                                           "scope": "self"}) is True)
ck("H5. an explicit per-role page grant is the override, either way — config, never code",
   M.may_declare({"scope": "self", "pages": {M.PAGE: True}}) is True
   and M.may_declare({"scope": "all", "pages": {M.PAGE: False}}) is False)
ck("H6. NO PERMS AT ALL IS A REFUSAL — an unresolved caller never declares the company's month",
   M.may_declare(None) is False and M.may_declare({}) is False
   and M.may_declare("admin") is False)
ck("H6b. a resolved role with no scope recorded IS company-wide — the live convention, not a guess",
   M.may_declare({"__super_admin": False, "role": "admin"}) is True)
ck("H7. THE TIERS ARE NOT COPIED HERE — the module dereferences core.scope and holds no tuple",
   "is_market_or_wider(" in SRC and '"regional"' not in SRC and "'regional'" not in SRC)

# ── §I  THE UN-WIRE LOCK ──────────────────────────────────────────────────────────────────────────
print("\nI. the design lock — this module may not measure, send or pay, and stays wired")
CALLS = {n.func.id for n in ast.walk(TREE) if isinstance(n, ast.Call)
         and isinstance(n.func, ast.Name)}
ATTRS = {n.func.attr for n in ast.walk(TREE) if isinstance(n, ast.Call)
         and isinstance(n.func, ast.Attribute)}
IMPORTS = {a.name for n in ast.walk(TREE) if isinstance(n, ast.Import) for a in n.names} | \
          {n.module or "" for n in ast.walk(TREE) if isinstance(n, ast.ImportFrom)}
IO_CALLS = {"execute", "select", "insert", "upsert", "delete", "rpc", "table", "schema",
            "post", "put", "request", "urlopen", "connect"}
ck("I1. NO I/O — no client, no table read, no http, no socket ('get' is excluded: dicts have one)",
   not (IO_CALLS & ATTRS) and "open(" not in SRC, sorted(IO_CALLS & ATTRS))
ck("I2. stdlib only, plus the ONE home it dereferences for the scope tiers",
   IMPORTS <= {"calendar", "datetime", "app.core.scope", "__future__"}, sorted(IMPORTS))
ck("I3. NO SENDING — this reminder is computed on the platform, never delivered",
   not any(w in SRC.lower() for w in ("smtp", "whatsapp", "twilio", "resend", "sendgrid",
                                      "alert_log", "digest_delivery", "pg_cron", "cron.schedule")))
# The pay config is NAMED in the prose — that is the point of the module, which says it only reads
# it. So the check is on CODE, with every docstring and comment stripped: no executable line may
# mention a pay table or a payout column at all.
CODE = "\n".join(l.split("#")[0] for l in ast.unparse(TREE).splitlines())
for _n in ast.walk(TREE):
    if isinstance(_n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(_n):
        _n.body = _n.body[1:] or [ast.Pass()]
CODE = ast.unparse(TREE)
ck("I4. NO PAY — no executable line names a pay table or a payout column",
   not any(w in CODE for w in ("payout_config", "rep_commissions", "total_payout", "custom_spiffs")),
   [w for w in ("payout_config", "rep_commissions", "total_payout", "custom_spiffs") if w in CODE])
ck("I5. IT STILL DEREFERENCES core.scope — the gate is not a private copy of the scope tiers",
   any(isinstance(n, ast.ImportFrom) and n.module == "app.core.scope"
       and "is_market_or_wider" in {a.name for a in n.names} for n in ast.walk(TREE))
   and "is_market_or_wider(p" in SRC)
# THE OTHER CALLER OF THE SAME FACT. CLAUDE.md: "a design fix ships with a check that FAILS THE BUILD
# if a caller stops dereferencing the shared fact, or if a second copy appears." `closer_pick` asked
# the same question with its own tuple; it was wired to `core.scope` in the same change, and this is
# what stops it drifting back.
CP = open(os.path.join(ROOT, "app/modules/closing/closer_pick.py")).read()
SCOPE = open(os.path.join(ROOT, "app/core/scope.py")).read()
ck("I5b. the OTHER caller still dereferences it too — no second tier tuple in closer_pick",
   "from app.core.scope import" in CP and "is_market_or_wider(" in CP
   and '"regional"' not in CP and "'regional'" not in CP, CP.count("regional"))
ck("I5c. the tuple lives in core.scope, once",
   SCOPE.count("MARKET_OR_WIDER_SCOPES = ") == 1 and '"regional"' in SCOPE)
ck("I6. RULE TWO — no carrier, tenant or product name anywhere in the module",
   not any(w in SRC.lower() for w in ("boost", "luxelink", "cellfonz", "total wireless",
                                      "vidapay", "t-mobile", "verizon", "iphone", "samsung")))
ck("I7. it measures NOTHING — the plays read `signals`; no feed, no aggregation, no classifier",
   not any(w in SRC for w in ("daily_sales_feed", "raw_sales", "raw_comp_report", "raw_dlar",
                              "_sales_cell_agg", "carrier_dollar_class", "payment_category")))
ck("I8. the two knobs have house defaults, so an un-configured tenant behaves as shipped",
   M.DECLARATION_DAYS_DEFAULT == 7 and M.CHECKIN_WEEKDAY_DEFAULT == 0)
# ARMED: every control above is shown to FAIL on a planted violation, so a lock that has quietly
# stopped looking at anything cannot pass.
print("  -- armed: each control fails on a planted violation --")
ck("I9. (armed) a planted client read would fail I1",
   bool({"execute"} & {n.func.attr for n in ast.walk(ast.parse("c.table('x').execute()"))
                       if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}))
ck("I10. (armed) a planted send would fail I3", "whatsapp" in "x = whatsapp_send()".lower())
ck("I11. (armed) a planted pay write would fail I4",
   "payout_config" in ast.unparse(ast.parse("x = c.table('payout_config')")))
ck("I12. (armed) a planted carrier name would fail I6", "boost" in "BOOST_RATE = 1".lower())
ck("I13. (armed) a planted feed read would fail I7", "daily_sales_feed" in "t('daily_sales_feed')")
ck("I14. (armed) dropping the core.scope import would fail I5",
   not any(isinstance(n, ast.ImportFrom) and n.module == "app.core.scope"
           for n in ast.walk(ast.parse("import calendar"))))
ck("I15. (armed) a re-copied tier tuple in a caller would fail I5b",
   "'regional'" in "PICK_ANY = ('market', 'region', 'regional', 'all')")

print(f"\n{'FAILED: ' + str(len(FAIL)) if FAIL else 'all checks passed'}")
for f in FAIL:
    print(f"  - {f}")
sys.exit(1 if FAIL else 0)
