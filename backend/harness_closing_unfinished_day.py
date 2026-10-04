"""Offline proof harness — A STORE-DAY SOMEBODY STARTED AND DID NOT FINISH IS NEVER BLANK, and an
un-corrected entry never reaches a money report (index §29.12). Drives the PURE
`closing/unfinished_day` and the REAL `closing/router.closing_resume` / `_log_attempt` against the
house's shared in-memory client (`harness_intake_fakes.FakeDB`, reused — not a fourth fake client).
No live database, no network.

Run: `cd backend && python3 harness_closing_unfinished_day.py`

THE DEFECT (owner bug report 2026-10-04): *"If the stops the reform entering the 1st closing and
tells them to correct it, the rep tries again but stops at 2 or even after the 1st attempt, the
system should say to correct the entries like it does but also save the last entered data in thr
system so the system is not blank at any time like what happened with Abid"*

MEASURED LIVE FIRST (read-only, 2026-10-04). B-117 / 2026-10-01, employee "Rana": two
`closing_attempt` rows two seconds apart, both `blocked`; entered cash $2,826.00 vs POS $2,631.83
($194.17 over), entered credit $270.00 vs POS $146.59 ($123.41 over), `t_zelle` $225.00; and no
`daily_closing` row at all. The 3-try gate blocks tries 1-2 and auto-accepts the 3rd; he stopped at
two. The money was never lost — it was in the submit trail the whole time. What was wrong was that
every screen read "no closing row" as "nobody submitted", so the store-day rendered exactly like one
nobody worked, and on the DM-verify screen the store did not appear AT ALL (the card loop skips a
store where no rep clocked in or sold, and a DM covering the floor is not on that roster).

THE CLASS: "no closing row" was being read as "nobody submitted", when it can equally mean "somebody
submitted and the gate sent them back" — opposite operational facts, indistinguishable everywhere.

Proves:
  A. PURE — the state vocabulary: four states, labels for each, `OPEN_STATES`, and no
     database/table/hosting name in copy that reaches a screen (index §19.38).
  B. PURE — `state_for` is the one rule: finished beats everything; tries mean awaiting_correction;
     refused-only means turned_away; nothing means not_started.
  C. PURE — `entered_money` / `variance` / `resume_entry`: every tender key present even at zero
     (the `.get()` trap that cost the envelope receipt, §47.8), the variance is the gate's own
     recorded comparison and is `has_pos: False` rather than 0.0 when the feed had nothing.
  D. BEHAVIOURAL, THE REGRESSION — the exact Burnside shape: two blocked tries, no closing. The
     store-day reports `awaiting_correction` with $2,826 entered and $194.17 over. Before this
     package the same inputs produced no state at all.
  E. THE MONEY POINT — an unfinished day writes NO `daily_closing` row, so it cannot reach any of
     the 51 readers of that table. The owner chose "not until corrected" (2026-10-04) and this makes
     it structural rather than a rule to remember.
  F. BEHAVIOURAL — `_log_attempt` keeps the WHOLE entry (mig 1052), so a resumed form comes back
     filled in; and `closing_resume` hands back the rep's own numbers.
  G. DEGRADE pre-migration-1052 — the resume columns do not exist yet: the try is still recorded
     with its money and variance rather than lost.
  H. WIRING LOCKS — these FAIL THE BUILD if the design un-wires:
       H1  no provisional `daily_closing` row is ever written for a blocked submit;
       H2  the rep-facing resume endpoint never returns a POS figure, a variance or the gate's
           directions (the gate tells a rep the direction and never the amount);
       H3  the state is decided ONLY by unfinished_day — no caller re-derives it;
       H4  every reader that words the state has words for all four, and invents no fifth;
       H5  migration 1052 adds every column `_log_attempt` writes, and touches no money column.
"""
import sys
import os
import re
import ast
import asyncio

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"
ROUTER_PATH = "app/modules/closing/router.py"
ROUTER_SRC = open(ROUTER_PATH).read()
MOD_PATH = "app/modules/closing/unfinished_day.py"
MOD_SRC = open(MOD_PATH).read()
MIG_PATH = "../database/migrations/1052_closing_attempt_full_entry.sql"
VERIFY_PATH = "../frontend/src/components/DailyClosingVerify.tsx"

from harness_intake_fakes import FakeDB                      # noqa: E402
from app.modules.closing import unfinished_day as ud         # noqa: E402
import app.modules.closing.router as cr                      # noqa: E402

#: The live Burnside numbers, so the regression is the reported defect and not a toy.
BURNSIDE = {
    "org_id": HOUSE, "close_date": "2026-10-01", "period": "2026-10", "store_code": "B-117",
    "store_address": "117 E Burnside Ave", "employee_name": "Rana",
    "entered_cash": 2826.0, "entered_credit": 270.0,
    "t_cash": 2826.0, "t_credit": 0.0, "t_ext_cc": 270.0, "t_gift": 0.0,
    "t_store_acct": 0.0, "t_zelle": 225.0, "t_acima": 0.0,
    "b2b_cash": 2631.83, "b2b_credit": 146.59,
    "cash_dir": "over", "credit_dir": "over",
    "blocked": True, "accepted": False, "auto_accepted": False, "refused": False,
}


def try_row(n, **over):
    r = dict(BURNSIDE)
    r["attempt_no"] = n
    r["created_at"] = f"2026-10-01T23:34:{10 + n:02d}+00:00"
    r.update(over)
    return r


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("== A. the vocabulary ==")
check("A1 four states, all distinct", len(set(ud.STATES)) == 4)
check("A2 every state has manager-facing words", all(ud.LABELS.get(s) for s in ud.STATES))
check("A3 the three unclosed states are the open ones",
      set(ud.OPEN_STATES) == {ud.NOT_STARTED, ud.AWAITING_CORRECTION, ud.TURNED_AWAY})
check("A4 a finished day is NOT open", ud.FINISHED not in ud.OPEN_STATES)
_BANNED = ("commcalc", "storeops", "postgres", "supabase", "daily_closing", "closing_attempt",
           "jsonb", "migration", "sql", "postgrest", "vercel", "render", "table")
_bad = [s for s, m in ud.LABELS.items() if any(b in m.lower() for b in _BANNED)]
check("A5 no storage or hosting name in words that reach a screen (§19.38)", not _bad, detail=str(_bad))
check("A6 the tender columns are declared once, and are the eight the form shows",
      ud.TENDER_COLUMNS == ("t_cash", "t_credit", "t_ext_cc", "t_gift", "t_store_acct", "t_zelle", "t_acima"))
check("A7 the resume columns name what the form keeps besides money",
      set(ud.ENTRY_COLUMNS) == {"acc_sale", "upgrade_count", "new_line_count", "postpaid_count",
                                "remarks", "envelope_picture"})

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. state_for is the one rule ==")
check("B1 a closing exists -> finished, whatever the history",
      ud.state_for({"id": "x"}, [try_row(1), try_row(2)]) == ud.FINISHED)
check("B2 nothing at all -> not_started", ud.state_for(None, []) == ud.NOT_STARTED)
check("B3 real tries, no closing -> awaiting_correction  (THE BURNSIDE STATE)",
      ud.state_for(None, [try_row(1), try_row(2)]) == ud.AWAITING_CORRECTION)
check("B4 only refusals -> turned_away, not awaiting_correction",
      ud.state_for(None, [try_row(0, refused=True, blocked=False),
                          try_row(0, refused=True, blocked=False)]) == ud.TURNED_AWAY)
check("B5 a refusal beside a real try does not hide the real try",
      ud.state_for(None, [try_row(0, refused=True, blocked=False), try_row(1)]) == ud.AWAITING_CORRECTION)
check("B6 an EMPTY closing row is not a closing (falsy, not None)",
      ud.state_for({}, [try_row(1)]) == ud.AWAITING_CORRECTION)
check("B7 the try/refusal rule is the refusal registry's own, not a second copy",
      "submit_refusal" in MOD_SRC and "is_real_try" in MOD_SRC)
from app.modules.closing import submit_refusal as sr          # noqa: E402
check("B8 and it answers the same as the registry for both shapes",
      ud.is_real_try(try_row(1)) == sr.is_real_try(try_row(1))
      and ud.is_real_try(try_row(0, refused=True)) == sr.is_real_try(try_row(0, refused=True)))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. the money and the variance, read not recomputed ==")
_m = ud.entered_money(try_row(1))
check("C1 every tender key is present even at zero (never 'zero' vs 'not fetched')",
      all(k in _m for k in ud.TENDER_COLUMNS))
check("C2 the amounts are the rep's own entry", _m["t_cash"] == 2826.0 and _m["t_zelle"] == 225.0
      and _m["declared_cash"] == 2826.0 and _m["declared_credit"] == 270.0)
_v = ud.variance(try_row(1))
check("C3 the variance is declared minus POS, positive meaning OVER — the live Burnside figures",
      _v["has_pos"] and _v["cash"] == 194.17 and _v["credit"] == 123.41)
check("C4 and carries the gate's own recorded directions",
      _v["cash_dir"] == "over" and _v["credit_dir"] == "over")
_vn = ud.variance(try_row(1, b2b_cash=None, b2b_credit=None))
check("C5 no POS for the day -> has_pos False, variances None, NEVER 0.0",
      _vn["has_pos"] is False and _vn["cash"] is None and _vn["credit"] is None)
check("C6 latest_try is the last REAL try by attempt order",
      ud.latest_try([try_row(1), try_row(2), try_row(0, refused=True)])["attempt_no"] == 2)
check("C7 latest_try of nothing real is empty, not an exception",
      ud.latest_try([try_row(0, refused=True)]) == {} and ud.latest_try([]) == {})
_r = ud.resume_entry(try_row(1, acc_sale=434.0, upgrade_count=3, remarks="4 trade ins"))
check("C8 resume_entry hands back the tenders AND the rest of the form",
      _r["t_cash"] == 2826.0 and _r["acc_sale"] == 434.0 and _r["upgrade_count"] == 3
      and _r["remarks"] == "4 trade ins")
check("C9 a column the try does not carry is ABSENT, not a fabricated zero",
      "postpaid_count" not in _r and "envelope_picture" not in _r)
check("C10 resume_entry reveals NO POS figure or variance",
      not any(k in _r for k in ("b2b_cash", "b2b_credit", "cash_dir", "credit_dir",
                                "declared_cash", "declared_credit")))
check("C11 configured (custom) tenders resume too",
      ud.resume_entry(try_row(1, tenders={"crypto": 10.0}))["tenders"] == {"crypto": 10.0})

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. THE REGRESSION — the reported store-day is no longer blank ==")
_d = ud.describe(None, [try_row(1), try_row(2)])
check("D1 B-117 / 2026-10-01 reports awaiting_correction", _d["state"] == ud.AWAITING_CORRECTION)
check("D2 with manager-facing words, not a code", _d["label"] == "Entered, awaiting correction")
check("D3 the day is still OPEN — entering something is not closing it", _d["is_open"] is True)
check("D4 both tries are counted, and none was a refusal", _d["tries"] == 2 and _d["turned_away"] == 0)
check("D5 the money he entered is on it", _d["entered"]["declared_cash"] == 2826.0)
check("D6 and the variance somebody must act on", _d["variance"]["cash"] == 194.17)
check("D7 and who entered it", _d["employee_name"] == "Rana")
check("D8 and when they last tried", str(_d["last_try_at"]).startswith("2026-10-01T23:34"))
_dn = ud.describe(None, [])
check("D9 a day nobody touched stays plainly 'not submitted' — the nag's one true case",
      _dn["state"] == ud.NOT_STARTED and _dn["label"] == "Not submitted" and "entered" not in _dn)
_df = ud.describe({"id": "x"}, [try_row(1)])
check("D10 a finished day carries no entered/variance block to mislead a reader",
      _df["state"] == ud.FINISHED and "entered" not in _df and "variance" not in _df)
_sum = ud.summarize([_d, _dn, _df])
check("D11 summarize counts every state, including the zeroes",
      _sum == {ud.NOT_STARTED: 1, ud.AWAITING_CORRECTION: 1, ud.TURNED_AWAY: 0, ud.FINISHED: 1})

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. THE MONEY POINT — an un-corrected entry reaches no money report ==")
check("E1 is_finished is the predicate, and an unfinished day is not finished",
      ud.is_finished({"id": "x"}) is True and ud.is_finished(None) is False and ud.is_finished({}) is False)
check("E2 the module writes nothing at all — no insert/update/upsert anywhere in it",
      not re.search(r"\.(insert|update|upsert|delete)\(", MOD_SRC))
check("E3 and performs no I/O: it imports no client, no framework, no network",
      not re.search(r"^\s*(import|from)\s+(fastapi|httpx|requests|supabase)", MOD_SRC, re.M))
# E4 is about COUPLING, not wording: the module explains the closings table in prose (it must, to
# say why it deliberately writes nothing there) but may never reach a table through a client.
check("E4 the module touches no table through any client — it cannot write a provisional row",
      not re.search(r"\.(table|schema|rpc)\(", MOD_SRC))
check("E5 and takes no client: every function is handed rows the caller already read",
      not re.search(r"def \w+\(\s*client", MOD_SRC))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. the real endpoint hands back what the rep typed ==")


def db_with(tries, closing=None, resume_cols=True):
    db = FakeDB()
    cols = ["id", "org_id", "close_date", "period", "store_code", "store_address", "sfid",
            "employee_name", "attempt_no", "entered_cash", "entered_credit",
            "t_cash", "t_credit", "t_ext_cc", "t_gift", "t_store_acct", "t_zelle", "t_acima",
            "b2b_cash", "b2b_credit", "cash_dir", "credit_dir",
            "blocked", "accepted", "auto_accepted", "refused", "refusal_code", "refusal_detail",
            "tenders", "created_at"]
    if resume_cols:
        cols += list(ud.ENTRY_COLUMNS)
    db.declared["closing_attempt"] = cols
    db.declared["daily_closing"] = ["id", "org_id", "close_date", "store_code", "employee_name",
                                    "t_cash", "released_at", "correction_count", "dedup_key"]
    db.tables["closing_attempt"] = [dict(t) for t in tries]
    db.tables["daily_closing"] = [dict(closing)] if closing else []
    cr.sb = lambda: db
    cr.get_supabase = lambda: db
    return db


db_with([try_row(1, acc_sale=434.0, upgrade_count=3, remarks="4 trade ins"), try_row(2)])
_res = cr.closing_resume(store_code="B-117", employee_name="Rana", close_date="2026-10-01")
check("F1 the endpoint reports awaiting_correction for the reported store-day",
      _res["state"] == ud.AWAITING_CORRECTION)
check("F2 and hands back the money the rep entered", _res["resume"]["t_cash"] == 2826.0
      and _res["resume"]["t_zelle"] == 225.0)
check("F3 and how many tries they made", _res["tries"] == 2)
check("F4 THE REP IS NEVER SHOWN THE POS FIGURE OR THE VARIANCE",
      not any(k in str(_res) for k in ("b2b_cash", "b2b_credit", "2631.83", "146.59", "194.17", "123.41")))
check("F5 nor the gate's over/short direction",
      "cash_dir" not in str(_res) and "credit_dir" not in str(_res))

db_with([try_row(1)], closing={"id": "c1", "org_id": HOUSE, "close_date": "2026-10-01",
                               "store_code": "B-117", "employee_name": "Rana"})
_res2 = cr.closing_resume(store_code="B-117", employee_name="Rana", close_date="2026-10-01")
check("F6 a day already closed offers nothing to resume",
      _res2["state"] == ud.FINISHED and _res2["resume"] == {})

db_with([try_row(0, refused=True, blocked=False)])
_res3 = cr.closing_resume(store_code="B-117", employee_name="Rana", close_date="2026-10-01")
check("F7 a submit turned away before the gate has nothing of the rep's to hand back",
      _res3["state"] == ud.TURNED_AWAY and _res3["resume"] == {})

db_with([])
check("F8 no store or no name -> a clean not_started, never an exception",
      cr.closing_resume(store_code=None, employee_name=None)["state"] == ud.NOT_STARTED)
check("F9 a store-day nobody touched -> not_started",
      cr.closing_resume(store_code="B-117", employee_name="Rana",
                        close_date="2026-10-01")["state"] == ud.NOT_STARTED)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== G. degrade — migration 1052 not run ==")
db = db_with([], resume_cols=False)
_tenders = {"cash": 2826.0, "credit": 0.0, "ext_cc": 270.0, "gift": 0.0, "store_acct": 0.0,
            "zelle": 225.0, "acima": 0.0}
_body = {"store_code": "B-117", "store_address": "117 E Burnside Ave", "sfid": "SF1",
         "employee_name": "Rana", "acc_sale": 434.0, "upgrade_count": 3, "remarks": "4 trade ins"}
cr._log_attempt(db, HOUSE, "2026-10-01", _body, _tenders, 2826.0, 270.0,
                {"cash": 2631.83, "card": 146.59}, {"cash": "over", "credit": "over"},
                attempt_no=1, blocked=True, accepted=False, auto_accepted=False)
_logged = db.tables["closing_attempt"]
check("G1 the try is STILL RECORDED when the resume columns do not exist — degrade, never drop",
      len(_logged) == 1, detail=str(len(_logged)))
check("G2 and it keeps the money and the variance the gate acted on",
      _logged[0]["t_cash"] == 2826.0 and _logged[0]["b2b_cash"] == 2631.83
      and _logged[0]["blocked"] is True)
check("G3 the non-money fields are simply absent, so the form falls back to empty",
      not any(k in _logged[0] for k in ud.ENTRY_COLUMNS))
_res4 = cr.closing_resume(store_code="B-117", employee_name="Rana", close_date="2026-10-01")
check("G4 and the state is still right pre-migration", _res4["state"] == ud.AWAITING_CORRECTION)

db = db_with([])
cr._log_attempt(db, HOUSE, "2026-10-01", _body, _tenders, 2826.0, 270.0,
                {"cash": 2631.83, "card": 146.59}, {"cash": "over", "credit": "over"},
                attempt_no=1, blocked=True, accepted=False, auto_accepted=False)
_logged = db.tables["closing_attempt"]
check("G5 WITH the migration the whole entry is kept (mig 1052)",
      _logged[0].get("acc_sale") == 434.0 and _logged[0].get("upgrade_count") == 3
      and _logged[0].get("remarks") == "4 trade ins")
check("G6 a blocked try writes NO closing row — the money point, through the real logger",
      db.tables["daily_closing"] == [])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== H. the locks: the design cannot quietly un-wire ==")
_tree = ast.parse(ROUTER_SRC)
_lines = ROUTER_SRC.splitlines()


def fn_src(name):
    n = next((x for x in ast.walk(_tree)
              if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)) and x.name == name), None)
    if n is None:
        return ""
    return "\n".join(_lines[n.lineno - 1:(n.end_lineno or n.lineno)])


# H1 — no provisional closing row. `create_row` must still return before its write when the gate
#      blocks: a half-finished closing row would put known-wrong money in front of all 51 readers of
#      daily_closing, and the owner's answer on 2026-10-04 was "not until corrected".
_create = fn_src("create_row")
check("H1a create_row still returns on a blocked submit, before any closing is written",
      re.search(r"if not accept:\s*\n(?:\s*#.*\n)*\s*return \{\"accepted\": False", _create) is not None)
_after_gate = _create.split("if not accept:", 1)
check("H1b nothing writes to the closings table before that return",
      not re.search(r'table\("daily_closing"\)\s*\.\s*(insert|upsert)', _after_gate[0]))
check("H1c and the unfinished-state module is never asked to write one",
      "unfinished" not in _create.split("if not accept:")[0].replace("_unfinished.describe", ""))

# H2 — the rep-facing endpoint reveals nothing the gate withholds.
_resume_src = fn_src("closing_resume")
check("H2a the resume endpoint exists", bool(_resume_src))
check("H2b it never calls unfinished_day.variance",
      "_unfinished.variance" not in _resume_src and ".variance(" not in _resume_src)
check("H2c it returns no POS or direction column by name",
      not any(k in _resume_src.split('"""', 2)[-1] for k in ("b2b_cash", "b2b_credit", "cash_dir", "credit_dir")))

# H3 — the state has ONE home. No caller may re-derive "no closing row means nobody submitted".
_mod_fns = ("state_for", "describe", "resume_entry", "latest_try", "entered_money", "variance",
            "is_finished", "summarize")
check("H3a the router dereferences the registry rather than restating the rule",
      "_unfinished.describe(" in ROUTER_SRC and "_unfinished.state_for(" in ROUTER_SRC)
_spelled = [s for s in ud.STATES if f'"{s}"' in ROUTER_SRC or f"'{s}'" in ROUTER_SRC]
check("H3b the router spells no state string of its own — it uses the module's constants",
      not _spelled, detail=str(_spelled))
for _f in ("_closing_summary_for_date", "closing_rollup", "_run_closing_missing_alerts"):
    _s = fn_src(_f)
    check(f"H3c {_f} asks unfinished_day for the state", "_unfinished." in _s)

# H4 — every reader that words the state has words for all four and invents no fifth.
_verify = open(VERIFY_PATH).read() if os.path.exists(VERIFY_PATH) else ""
check("H4a the DM-verify screen reads the state the server sends", "unfinished?.state" in _verify)
_worded = {s for s in ud.STATES if f"'{s}'" in _verify}
check("H4b it words every state a store-day with no closing can be in",
      {ud.AWAITING_CORRECTION, ud.TURNED_AWAY, ud.NOT_STARTED} <= _worded,
      detail=str(sorted(_worded)))
_invented = set(re.findall(r"unfinished\?\.state === '([a-z_]+)'", _verify)) - set(ud.STATES)
check("H4c and words no state the server cannot send", not _invented, detail=str(_invented))
check("H4d the screen shows the rep's entered cash on an awaiting-correction day",
      "unfinished.entered?.declared_cash" in _verify)

# H5 — the migration matches what the code writes, and moves no money.
_mig = open(MIG_PATH).read() if os.path.exists(MIG_PATH) else ""
check("H5a migration 1052 exists", bool(_mig))
for _c in ud.ENTRY_COLUMNS:
    check(f"H5b migration 1052 adds the column the logger writes: {_c}",
          re.search(rf"add column if not exists\s+{_c}\b", _mig) is not None)
check("H5c every statement is additive and idempotent — no drop, no destructive rewrite",
      not re.search(r"^\s*(drop|truncate|delete)\s", _mig, re.M | re.I))
check("H5d it writes no row and updates no money column",
      not re.search(r"^\s*(update|insert)\s+", _mig, re.M | re.I))
check("H5e it carries REVERT notes", _mig.count("-- REVERT") >= 2)
check("H5f it never touches the closings table",
      "alter table commcalc.daily_closing" not in _mig.lower())

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
