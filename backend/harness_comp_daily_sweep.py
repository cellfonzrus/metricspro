"""Harness for the ePay comp/MI sweep rework (2026-08-09).

  * Comprehensive Comp is a DAILY report (Summarize by = Daily + Start/End Date), not a monthly one.
  * A zero-row day is normal and must not be an error.
  * Comp storage is DAY-keyed, so a one-day pull cannot replace a whole month.
  * The multi-month refresh is registry-driven (report_definitions.refresh_months) and now applies
    to MI, which is the leg that was actually frozen.

Offline sections run anywhere. Section L additionally drives the REAL portal (read-only: it
downloads reports and stores them in an in-memory fake client, never the database) and is opt-in:
    EPAY_LIVE=1 python3 backend/harness_comp_daily_sweep.py
EPAY_CAP_DIR points at previously captured workbooks for the parser sections.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from app.modules.commcalc import epay_sweep as ES                      # noqa: E402
from app.modules.commcalc.safe_replace import safe_replace             # noqa: E402
from harness_fakedb import FakeClient, FakeTable                        # noqa: E402

import uuid as _uuid
from datetime import timedelta as _td


def seed(db, name, tbl, rows):
    """Put pre-existing rows in a table, aged so any new insert is unambiguously newer."""
    db.tables[name] = tbl
    tbl.name = name
    for r in rows:
        db.clock += _td(milliseconds=1)
        rr = dict(r)
        rr.setdefault("id", str(_uuid.uuid4()))
        if tbl.has_created_at:
            rr.setdefault("created_at", db.clock.isoformat())
        tbl.rows.append(rr)
    db.clock += _td(seconds=3600)
    return tbl


PASS = FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {detail}")
        print(f"  FAIL {name} :: {detail}")


ORG = "00000000-0000-0000-0000-000000000001"
CAP = os.environ.get("EPAY_CAP_DIR", "")


def comp_tbl():
    return FakeTable({"org_id": "text", "period": "text", "begin_date": "text",
                      "quantity": "int", "payment_amount": "num"})


# ═══ D — date helpers ═══════════════════════════════════════════════════════════════════════
print("\n== D: date helpers ==")
check("D1 US date", ES._iso_day("04/15/2026") == "2026-04-15", ES._iso_day("04/15/2026"))
check("D2 ISO date", ES._iso_day("2026-04-15") == "2026-04-15")
check("D3 junk -> None", ES._iso_day("") is None and ES._iso_day("nope") is None)
check("D4 period of day", ES._period_of_day("2026-04-15") == ("April 2026", 4, 2026))
import datetime as _dt  # noqa: E402
check("D5 _recent_days(1) is today only",
      ES._recent_days(1, _dt.date(2026, 8, 9)) == ["2026-08-09"])
check("D6 _recent_days(3) is oldest-first and inclusive",
      ES._recent_days(3, _dt.date(2026, 8, 9)) == ["2026-08-07", "2026-08-08", "2026-08-09"])
check("D7 _recent_days crosses a month boundary",
      ES._recent_days(3, _dt.date(2026, 8, 1)) == ["2026-07-30", "2026-07-31", "2026-08-01"])


# ═══ E — registry-driven job expansion (Defect 1) ═══════════════════════════════════════════
print("\n== E: _expand_jobs is driven by report_definitions ==")
cfg3 = {"mi_report": {"refresh_months": 3}, "comp_report": {"refresh_days": 1}}
mi_jobs = ES._expand_jobs(["mi"], cfg3)
check("E1 MI fans out to 3 months from the registry", len(mi_jobs) == 3, str(len(mi_jobs)))
check("E2 MI months are current-first and distinct",
      len({j[1]["period"] for j in mi_jobs}) == 3, str([j[1]["period"] for j in mi_jobs]))
check("E3 MI targets are month-kind", all(j[1]["kind"] == "month" for j in mi_jobs))
check("E4 MI with NO registry row falls back to 1 month",
      len(ES._expand_jobs(["mi"], {})) == ES.DEFAULT_REFRESH_MONTHS == 1)
check("E5 registry refresh_months=1 means current month only",
      len(ES._expand_jobs(["mi"], {"mi_report": {"refresh_months": 1}})) == 1)
check("E6 the old hard-coded constant is gone",
      not hasattr(ES, "COMP_REFRESH_MONTHS"), "COMP_REFRESH_MONTHS still exists")

comp_jobs = ES._expand_jobs(["comp_report"], cfg3)
check("E7 comp is ONE job, not one per month", len(comp_jobs) == 1, str(len(comp_jobs)))
check("E8 comp job is a day range", comp_jobs[0][1]["kind"] == "day_range", str(comp_jobs[0][1]))
check("E9 refresh_days=1 -> begin == end == today",
      comp_jobs[0][1]["begin"] == comp_jobs[0][1]["end"], str(comp_jobs[0][1]))
wide = ES._expand_jobs(["comp_report"], {"comp_report": {"refresh_days": 5}})
check("E10 a wider window is still ONE portal run", len(wide) == 1)
check("E11 and it spans 5 days", len(wide[0][1]["days"]) == 5, str(wide[0][1]["days"]))
check("E12 payment_detail stays a single default pull",
      ES._expand_jobs(["payment_detail"], cfg3) == [("payment_detail", None)])
check("E13 comp registry_key/grain/filter wired",
      ES.REPORTS["comp_report"]["grain"] == "day"
      and ES.REPORTS["comp_report"]["filter"] == "daily_range"
      and ES.REPORTS["comp_report"]["empty_ok"] is True)
check("E14 MI is month-grain with a month filter",
      ES.REPORTS["mi"]["grain"] == "month" and ES.REPORTS["mi"]["filter"] == "month")
check("E15 MI is NOT empty_ok (an empty MI pull is still a real failure)",
      not ES.REPORTS["mi"].get("empty_ok"))


# ═══ F — the workbook reader tells the two failures apart ═══════════════════════════════════
print("\n== F: empty report vs unparseable download ==")
caps = sorted(os.listdir(CAP)) if CAP and os.path.isdir(CAP) else []
empties = [os.path.join(CAP, f) for f in caps if f.endswith(".xlsx")
           and os.path.getsize(os.path.join(CAP, f)) < 6000]
fulls = [os.path.join(CAP, f) for f in caps if f.endswith(".xlsx")
         and os.path.getsize(os.path.join(CAP, f)) > 20000]
if empties:
    try:
        ES._read_report_records(empties[0], "Comprehensive Comp")
        check("F1 a header-only workbook raises EpayEmptyReport", False, "no raise")
    except ES.EpayEmptyReport as e:
        check("F1 a header-only workbook raises EpayEmptyReport", True)
        check("F2 the message says EMPTY, not unparseable", "EMPTY report" in str(e), str(e)[:120])
        check("F3 it names the header columns it found", "Begin Date" in str(e), str(e)[:200])
        check("F4 it describes the file", "bytes" in str(e) and "PK zip header" in str(e),
              str(e)[-200:])
    except Exception as e:
        check("F1 a header-only workbook raises EpayEmptyReport", False, repr(e))
if fulls:
    recs, cols = ES._read_report_records(fulls[0], "Comprehensive Comp")
    check("F5 a populated workbook parses", len(recs) > 0 and "Begin Date" in cols, str(cols[:3]))
# a genuinely unparseable download
bad = os.path.join(HERE, "_harness_not_a_workbook.xlsx")
with open(bad, "w") as fh:
    fh.write("<html><body>Request Rejected</body></html>")
try:
    ES._read_report_records(bad, "Comprehensive Comp")
    check("F6 an HTML error page raises the PARSE error", False, "no raise")
except ES.EpayEmptyReport as e:
    check("F6 an HTML error page raises the PARSE error", False, f"got EpayEmptyReport: {e}")
except ES.EpayPortalError as e:
    check("F6 an HTML error page raises the PARSE error", "could NOT PARSE" in str(e), str(e)[:140])
    check("F7 and it identifies it as HTML", "HTML" in str(e), str(e)[:220])
finally:
    os.unlink(bad)
check("F8 EpayEmptyReport is a subclass of EpayPortalError (old handlers still catch it)",
      issubclass(ES.EpayEmptyReport, ES.EpayPortalError))


# ═══ G — day-keyed storage ══════════════════════════════════════════════════════════════════
print("\n== G: comp stores day by day ==")
if fulls:
    import pandas as pd
    month_file = max(fulls, key=os.path.getsize)     # the 30-day July range pull
    recs = pd.read_excel(month_file, dtype=str).fillna("").to_dict("records")
    days_in_file = sorted({ES._iso_day(r.get("Begin Date")) for r in recs})
    db = FakeClient()
    t = seed(db, "raw_comp_report", comp_tbl(), [])
    spec = ES.REPORTS["comp_report"]
    out = ES._store_day_grain(db, ORG, spec, "comp_report", recs, None)
    check(f"G1 stored every one of the {len(days_in_file)} days in the file",
          len(out["days"]) == len(days_in_file), f"{len(out['days'])} vs {len(days_in_file)}")
    check("G2 row count matches the file", out["rows"] == len(recs),
          f"{out['rows']} vs {len(recs)}")
    check("G3 each stored row carries its own day's period",
          all(r["period"] == ES._period_of_day(r["begin_date"])[0] for r in t.rows))
    check("G4 mode is replace_by_day", out["mode"] == "replace_by_day")

    # idempotency: the same pull again must not duplicate
    n1 = len(t.rows)
    out2 = ES._store_day_grain(db, ORG, spec, "comp_report", recs, None)
    check("G5 re-running the same pull is idempotent", len(t.rows) == n1,
          f"{len(t.rows)} vs {n1}")
    check("G6 and it reports replacing, not appending",
          all(d["prior"] > 0 for d in out2["days"]))

    # THE TRAP: a single day's pull must not wipe the rest of the month
    one_day = [r for r in recs if ES._iso_day(r.get("Begin Date")) == days_in_file[10]]
    before_total = len(t.rows)
    before_other = len([r for r in t.rows if r["begin_date"] != days_in_file[10]])
    out3 = ES._store_day_grain(db, ORG, spec, "comp_report", one_day, None)
    check("G7 a ONE-DAY pull touches exactly one day", len(out3["days"]) == 1, str(out3["days"]))
    check("G8 every OTHER day in the month survives",
          len([r for r in t.rows if r["begin_date"] != days_in_file[10]]) == before_other,
          f"other days went {before_other} -> "
          f"{len([r for r in t.rows if r['begin_date'] != days_in_file[10]])}")
    check("G9 the month is intact", len(t.rows) == before_total, f"{len(t.rows)}")

    # the per-day partial-collapse guard
    db2 = FakeClient()
    t2 = seed(db2, "raw_comp_report", comp_tbl(), [])
    ES._store_day_grain(db2, ORG, spec, "comp_report", recs, None)
    day0 = days_in_file[0]
    n_day0 = len([r for r in t2.rows if r["begin_date"] == day0])
    tiny = [r for r in recs if ES._iso_day(r.get("Begin Date")) == day0][:3]
    out4 = ES._store_day_grain(db2, ORG, spec, "comp_report", tiny, None)
    check("G10 a collapsed day is REFUSED, not written",
          len([r for r in t2.rows if r["begin_date"] == day0]) == n_day0,
          f"{len([r for r in t2.rows if r['begin_date'] == day0])} vs {n_day0}")
    check("G11 and it is reported as skipped_guard", bool(out4.get("skipped_guard")), str(out4))
else:
    print("  (skipped — needs EPAY_CAP_DIR with captured workbooks)")

# rows with no usable Begin Date must fail loudly, not vanish
db3 = FakeClient()
seed(db3, "raw_comp_report", comp_tbl(), [])
try:
    ES._store_day_grain(db3, ORG, ES.REPORTS["comp_report"], "comp_report",
                        [{"Retailer Account": "1", "Payment Amount": "5"}], None)
    check("G12 rows with no Begin Date raise", False, "no raise")
except ES.EpayPortalError as e:
    check("G12 rows with no Begin Date raise a NAMED error", "Begin Date" in str(e), str(e)[:120])


# ═══ H — scheduling ═════════════════════════════════════════════════════════════════════════
print("\n== H: per-report schedule slot ==")
sys.path.insert(0, os.path.join(HERE, "app", "modules", "commcalc"))
from app.modules.commcalc.router import _vip_next_run, _EPAY_REGISTRY_KEYS   # noqa: E402
n2330 = _vip_next_run("daily", None, None, 23, "America/New_York", minute=30)
n0600 = _vip_next_run("daily", None, None, 6, "America/New_York")
check("H1 23:30 slot lands on :30", n2330.endswith(":30:00+00:00") or ":30:" in n2330, n2330)
check("H2 06:00 slot still lands on :00", ":00:00" in n0600, n0600)
check("H3 default minute is unchanged for every existing caller",
      _vip_next_run("daily", None, None, 6, "America/New_York")
      == _vip_next_run("daily", None, None, 6, "America/New_York", minute=0))
check("H4 the two slots are different times", n2330 != n0600)
# Registry drift, not a defect. This pinned the map to exactly three reports; commit 8a5b419b
# (migs 935-939, daily closing cash lifecycle / billpay carve-out) registered a fourth,
# `epay_daily_tx`, which the product deliberately treats differently: its upsert is idempotent and
# the recon wants the freshest portal data, so it is due on EVERY run-due tick rather than on a
# daily slot. A hardcoded literal that silently stops matching the registry is the same drift class
# as the /health module list and the Exec MTD bucket vocabulary (see 564c171f), so this is expressed
# as a PROPERTY over the registry — registering a fifth report cannot break it.
#
# Reports due on every tick, as opposed to on their own daily slot. Kept as an explicit set so the
# scheduling assertions below can subtract it; H5c proves it is not merely asserted but true.
EVERY_TICK = {"epay_daily_tx"}
SLOT_SCHEDULED = {"mi": "mi_report", "comp_report": "comp_report", "payment_detail": "payment_detail"}
check("H5a the slot-scheduled reports are all still registered",
      all(_EPAY_REGISTRY_KEYS.get(k) == v for k, v in SLOT_SCHEDULED.items()),
      str(_EPAY_REGISTRY_KEYS))
check("H5b every registry key is either slot-scheduled or explicitly every-tick "
      "(a NEW report must be classified here, not silently ignored)",
      set(_EPAY_REGISTRY_KEYS) <= set(SLOT_SCHEDULED) | EVERY_TICK,
      f"unclassified={sorted(set(_EPAY_REGISTRY_KEYS) - set(SLOT_SCHEDULED) - EVERY_TICK)}")

# _epay_sub_schedule decides WHICH reports run on THIS tick. This is the part that actually
# reaches production: pg_cron has no epay/sweep/run-due job, it calls connectors/run-due hourly.
from app.modules.commcalc.router import _epay_sub_schedule                   # noqa: E402

NOW = "2026-08-09T23:35:00+00:00"
PAST = "2026-08-09T23:30:00+00:00"
FUTURE = "2026-08-10T23:30:00+00:00"


def sched_db(rows, with_cols=True):
    db = FakeClient()
    seed(db, "epay_sweep_config", FakeTable({"org_id": "text", "timezone": "text"}),
         [{"org_id": ORG, "timezone": "America/New_York"}])
    cols = {"org_id": "text", "report_key": "text", "auto": "bool", "sweep_hour": "int",
            "sweep_minute": "int", "sweep_timezone": "text", "sweep_next_run_at": "text",
            "sweep_last_run_at": "text"}
    if not with_cols:
        cols = {"org_id": "text", "report_key": "text", "auto": "bool"}
    seed(db, "report_definitions", FakeTable(cols), rows)
    return db


base_rows = [
    {"org_id": ORG, "report_key": "mi_report", "auto": True, "sweep_hour": None,
     "sweep_minute": 0, "sweep_timezone": None, "sweep_next_run_at": None},
    {"org_id": ORG, "report_key": "payment_detail", "auto": True, "sweep_hour": None,
     "sweep_minute": 0, "sweep_timezone": None, "sweep_next_run_at": None},
    {"org_id": ORG, "report_key": "comp_report", "auto": True, "sweep_hour": 23,
     "sweep_minute": 30, "sweep_timezone": None, "sweep_next_run_at": PAST},
]
db = sched_db([dict(r) for r in base_rows])
due, shared = _epay_sub_schedule(db, ORG, NOW)


def slot_due(d):
    """`due` with the every-tick reports removed — what the SLOT schedule decided."""
    return [k for k in d if k not in EVERY_TICK]


check("H5c the every-tick reports really are due on an ordinary tick (guard is non-vacuous)",
      all(k in due for k in EVERY_TICK if k in _EPAY_REGISTRY_KEYS), str(due))
check("H6 comp is due on its OWN 23:30 slot", slot_due(due) == ["comp_report"], str(due))
check("H7 MI + payment detail ride the connector slot",
      sorted(shared) == ["mi", "payment_detail"], str(shared))
row = [r for r in db.tables["report_definitions"].rows if r["report_key"] == "comp_report"][0]
check("H8 comp's slot is advanced so an hourly tick does not re-run it",
      row["sweep_next_run_at"] != PAST and row["sweep_next_run_at"] > NOW,
      str(row["sweep_next_run_at"]))
due2, _ = _epay_sub_schedule(db, ORG, NOW)
check("H9 a second tick in the same hour does NOT re-run comp", slot_due(due2) == [], str(due2))

db = sched_db([dict(r) for r in base_rows[:2]]
              + [{**base_rows[2], "sweep_next_run_at": FUTURE}])
due3, shared3 = _epay_sub_schedule(db, ORG, NOW)
check("H10 a slot in the future is not due", slot_due(due3) == [], str(due3))
check("H11 and comp is NOT handed to the connector slot either",
      "comp_report" not in shared3, str(shared3))

db = sched_db([dict(r) for r in base_rows[:2]]
              + [{**base_rows[2], "auto": False}])
due4, shared4 = _epay_sub_schedule(db, ORG, NOW)
check("H12 auto=false means the report is skipped entirely",
      slot_due(due4) == [] and "comp_report" not in shared4, f"{due4} {shared4}")

# pre-290 database: the schedule columns don't exist -> today's behaviour, nothing breaks
db = sched_db([{"org_id": ORG, "report_key": k, "auto": True}
               for k in ("mi_report", "payment_detail", "comp_report")], with_cols=False)
due5, shared5 = _epay_sub_schedule(db, ORG, NOW)
# pre-290 the schedule columns are absent, so _epay_sub_schedule returns ([], all registry keys) —
# every registered report rides the connector slot. Expressed over the registry rather than the
# three-name literal, for the same reason as H5.
check("H13 pre-290 degrades to the old behaviour (all reports on the connector slot)",
      due5 == [] and sorted(shared5) == sorted(_EPAY_REGISTRY_KEYS),
      f"{due5} {shared5}")


# ═══ L — LIVE portal, read-only (opt-in) ════════════════════════════════════════════════════
if os.environ.get("EPAY_LIVE") == "1":
    print("\n== L: LIVE portal end-to-end (no database writes) ==")
    import subprocess
    import tempfile

    def _creds():
        r = subprocess.run(
            ["python3", "tools/sbsql.py",
             "select portal_url, portal_user, portal_pass from commcalc.epay_sweep_config limit 1"],
            capture_output=True, text=True, cwd="/workspaces/commcalc")
        row = json.loads(r.stdout)[0]
        return row["portal_url"], row["portal_user"], row["portal_pass"]

    from playwright.sync_api import sync_playwright
    url, user, pw = _creds()
    base = ES._safe_base(url)
    db = FakeClient()
    tl = seed(db, "raw_comp_report", comp_tbl(), [])
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                    args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(user_agent=ES.UA, accept_downloads=True)
        page = ctx.new_page()
        try:
            page.goto(base, timeout=60000, wait_until="domcontentloaded")
            ES._login(page, user, pw)
            del pw, user

            # L1 a day that HAS data, through the real _process_report
            tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
            tmp.close()
            tgt = {"kind": "day_range", "begin": "2026-04-15", "end": "2026-04-15",
                   "days": ["2026-04-15"], "period": None}
            res = ES._process_report(db, ORG, page, "comp_report", tmp.name, target=tgt)
            os.unlink(tmp.name)
            print("   L1 result:", json.dumps({k: v for k, v in res.items() if k != "days"}))
            check("L1 a real day pulls its rows", res["rows"] == 381,
                  f"{res['rows']} rows (raw_comp_report holds 381 for 2026-04-15)")
            check("L2 stored under exactly one day",
                  len({r['begin_date'] for r in tl.rows}) == 1, str({r['begin_date'] for r in tl.rows}))
            check("L3 labelled April 2026", all(r["period"] == "April 2026" for r in tl.rows))
            amt = sum(float(r["payment_amount"] or 0) for r in tl.rows)
            check("L4 dollars match the database", abs(amt - 20698.61) < 0.01, f"{amt:,.2f}")
            check("L5 every quantity is an int (Bug A)",
                  all(isinstance(r["quantity"], int) or r["quantity"] is None for r in tl.rows))

            # L6 a day with NOTHING posted is a clean no-data, not an error
            page.goto(base, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
            tmp.close()
            before = len(tl.rows)
            tgt2 = {"kind": "day_range", "begin": "2026-08-07", "end": "2026-08-07",
                    "days": ["2026-08-07"], "period": None}
            res2 = ES._process_report(db, ORG, page, "comp_report", tmp.name, target=tgt2)
            os.unlink(tmp.name)
            print("   L6 result:", json.dumps(res2))
            check("L6 an unposted day returns mode=no_data, no exception",
                  res2["mode"] == "no_data", str(res2))
            check("L7 and it stores nothing / deletes nothing", len(tl.rows) == before)
        finally:
            browser.close()
else:
    print("\n== L: LIVE portal section skipped (set EPAY_LIVE=1) ==")

print(f"\n{'=' * 60}\nPASS {PASS}   FAIL {FAIL}")
for f in FAILURES:
    print("  -", f)
sys.exit(1 if FAIL else 0)
