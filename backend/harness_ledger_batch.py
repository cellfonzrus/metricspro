"""DB-FREE PROOF — many monthly commission statements in one go, each under its own month, through the
single import's own path (owner 2026-09-24; index §30.17).

    python3 harness_ledger_batch.py        (from backend/; no network, no DB)

Owner, verbatim: *"give me an option to upload commisison for multiple periods at the same time since tehy
only give monthly commision reports , on teh upload commssion received page"*

WHAT THIS PROVES (the REAL router endpoints over the shared in-memory client, harness_intake_fakes.FakeDB):
  A. the pure plan — the month from the statement's own dates (the intake's detector) in THE canonical
     spelling; a tie / no dates is a question, never a guess; a typed month canonicalised or refused
  B. BYTE IDENTITY — N files through /import-batch land EXACTLY the ledger rows (and upload_log rows) that N
     single /import calls land, into two separate fakes; with the house default mapping AND with a saved
     mapping that declares the opposite sign (the mapping + convention learned once applies to every file)
  C. the same month twice is REFUSED — nothing written
  D. an already-landed month follows the existing replace rule (the family × every spelling × this origin),
     needs confirm_replace, and never double-lands; an orphan spelling of the month is replaced too; a
     landing from ANOTHER origin needs confirm_other_origin; another statement type is never touched
  E. a month that cannot be determined is refused until set; a typed non-month is refused
  F. a per-file failure is ISOLATED — the other files land, the result names exactly which did not
  G. columns that differ from the batch's are named and refused (never mapped short)
  H. negative controls — each rule removed turns its check RED
  J. ONE MAPPING PER STATEMENT (§30.17a): a global-only tenant gets exactly today's rules / lines / rows; with both
     sets (the live shape) the Ledger page and the batch land the INTAKE's rows for the same file, the store-stamped
     total dropped by the one footer rule; /upload-mapped refuses the ledger layout
  K. an intake-landed month re-uploaded by the batch is REPLACED (one copy), never doubled
  I. wiring, RULE TWO, registration, CI, no migration
"""
import asyncio
import copy
import inspect
import json
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from fastapi import HTTPException

from app.modules.commcalc import commission_ledger as CL
from app.modules.commcalc import column_mapping as CM
from app.modules.commcalc import ledger_batch as LB
from app.modules.commcalc import ledger_ma_sync as LMS
from app.modules.commcalc import router as R
from harness_intake_fakes import FakeDB, FakeUpload

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ORG = "b47c0a11-0000-4000-8000-000000000001"
OTHER_ORG = "b47c0a11-0000-4000-8000-000000000002"
CODE = "northwind"                                   # a made-up carrier; no real carrier is named anywhere
LONG = f"{CODE}__commission_statement"
RK = CL.mapping_report_key("")

_pass = _fail = 0
_failures = []


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        _failures.append(name)
        print(f"  FAIL  {name}{(' — ' + str(extra)[:400]) if extra else ''}")
    return bool(cond)


def section(t):
    print(f"\n{t}\n" + "─" * len(t))


def run(coro):
    return asyncio.run(coro)


def money(x):
    return round(float(x or 0.0), 2)


def use(db):
    R.sb = lambda: db
    R._TABLE_COL_PRESENT.clear()
    return db


def fresh_db(cls=FakeDB, **kw):
    db = cls(**kw)
    db.seed("carrier", [{"id": "c0ffee00-0000-0000-0000-000000000001", "org_id": ORG, "name": "Northwind Cellular", "code": CODE}])
    return use(db)


def ledger(db, org=ORG):
    return [r for r in (db.tables.get("commission_ledger") or []) if r.get("org_id") == org]


def strip(rows, drop=("id", "created_at", "synced_at")):
    return [{k: v for k, v in r.items() if k not in drop} for r in rows]


# ── THE STATEMENTS — one per month, the default layout (or a saved one), CSV bytes ─────────────────────
DEFAULT_HDR = {d["target_field"]: d["source_header"] for d in CM.default_mapping(RK)}
COLS = ["account_id", "account_name", "store", "rep_user", "order_number", "order_type", "product_name",
        "trans_date", "due_date", "raw_amount"]
PRODUCTS = ["TBV MONTH 1 New Activation Commission", "TBV MONTH 2 New Activation Commission",
            "Spiff Bonus Activation", "Device Rebate Promo", "Deactivation Chargeback"]


def statement(month, n=12, seed=1.0, trailing=2, hdr=None, cols=None, blank_dates=False, split_even=None, sign=-1.0):
    """One month's statement: `n` lines dated in `month` ('2026-06'), `trailing` lines dated in the month
    before (the trailing legs a monthly statement carries), amounts varied by `seed`; `sign` = the sign
    of money earned in this file."""
    hdr = hdr or DEFAULT_HDR
    cols = cols or COLS
    y, m = int(month[:4]), int(month[5:7])
    prev = f"{y - (m == 1):04d}-{(12 if m == 1 else m - 1):02d}"
    lines = [",".join(hdr[c] for c in cols)]
    dates = []
    if split_even:
        dates = [f"{split_even[0]}-0{(i % 9) + 1}" for i in range(n // 2)] + [f"{split_even[1]}-0{(i % 9) + 1}" for i in range(n - n // 2)]
    else:
        dates = [f"{month}-{(i % 27) + 1:02d}" for i in range(n)] + [f"{prev}-{(i % 27) + 1:02d}" for i in range(trailing)]
    for i, d in enumerate(dates):
        prod = PRODUCTS[i % len(PRODUCTS)]
        amt = round((seed * 10 + i * 1.37) * (sign if "Chargeback" not in prod else -sign), 2)
        val = {"account_id": f"A{month}{i}", "account_name": "Acct", "store": "10 Main St" if i % 2 else "22 Oak Ave",
               "rep_user": ["alice", "bob", "carol"][i % 3], "order_number": f"O{month}{i}", "order_type": "Postpaid Order",
               "product_name": prod, "trans_date": "" if blank_dates else d, "due_date": "" if blank_dates else d,
               "raw_amount": f"{amt:.2f}"}
        lines.append(",".join(val[c] for c in cols))
    return ("\n".join(lines) + "\n").encode("utf-8")


MONTHS = [("june.csv", "2026-06", 1.0), ("july.csv", "2026-07", 2.0), ("august.csv", "2026-08", 3.0)]


def uploads(spec):
    return [FakeUpload(statement(mo, seed=sd), filename=fn) for fn, mo, sd in spec]


def batch_preview(files, periods="", source_report=CODE, statement_type=""):
    return run(R.commission_ledger_import_batch_preview(files=files, source_report=source_report, carrier_id="",
                                                        statement_type=statement_type, periods=periods, org_id=ORG))


def batch(files, periods="", confirm_replace="", confirm_other_origin="", source_report=CODE, statement_type=""):
    return run(R.commission_ledger_import_batch(files=files, source_report=source_report, carrier_id="",
                                                statement_type=statement_type, periods=periods,
                                                confirm_replace=confirm_replace, confirm_other_origin=confirm_other_origin,
                                                org_id=ORG))


def single(data, filename, period, source_report=CODE, statement_type=""):
    return run(R.commission_ledger_import(file=FakeUpload(data, filename=filename), source_report=source_report,
                                          period=period, carrier_id="", statement_type=statement_type, org_id=ORG))


def refused(fn):
    try:
        fn()
        return None
    except HTTPException as e:
        return str(e.detail)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. THE PURE PLAN — the month from the statement's own dates, in THE canonical spelling")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("resolve_period spells every typed form the ONE way the ledger stores it ('aug 2026' / '2026-8' / 'Sept 2026' / 'August 2026')",
      LB.resolve_period("aug 2026") == LB.resolve_period("2026-08") == LB.resolve_period("2026-8") == LB.resolve_period("August 2026")
      == CL.canonical_period("Aug 2026") == "August 2026" and LB.resolve_period("Sept 2026") == "September 2026")
check("resolve_period refuses what is not a month of a four-digit year (never invents one): 'Agust 2026', 'Aug 26', '2026-13', ''",
      all(LB.resolve_period(x) is None for x in ("Agust 2026", "Aug 26", "2026-13", "", "  ", "statement")))
mapped = [{"trans_date": f"2026-07-{d:02d}"} for d in range(1, 11)] + [{"trans_date": "2026-06-30"}] * 3
det = LB.detect_period(mapped)
check("detect_period: the month with the most lines, canonical, and says the dates SPAN two months (the trailing legs)",
      det["proposed"] == "July 2026" and det["spans"] and det["months"][0] == {"period": "July 2026", "rows": 10}
      and det["months"][1] == {"period": "June 2026", "rows": 3}, det)
check("detect_period dereferences the INTAKE's detector (onboarding_intake.period_proposal) — the proposal 3.9 pre-fills",
      "OI.period_proposal(" in inspect.getsource(LB.detect_period))
tie = LB.detect_period([{"trans_date": "2026-07-01"}] * 4 + [{"trans_date": "2026-08-01"}] * 4)
check("a TIE between the two leading months is a question, never a guess (proposed None, tied True)",
      tie["proposed"] is None and tie["tied"], tie)
none = LB.detect_period([{"trans_date": ""}, {"trans_date": None}, {}])
check("no dated line → no proposal (never an invented month)", none["proposed"] is None and none["dated_rows"] == 0)
check("the residual layout's period field is its own (column_mapping.period_source_field) — the batch asks the registry, never assumes trans_date",
      CM.period_source_field(RK) == "trans_date" and CM.period_source_field(CL.mapping_report_key("residual")) == "trans_date"
      and "period_source_field(" in inspect.getsource(R._ledger_batch_plan))
shape = LB.mapping_shape(["Product Name", "retail cost", "Other"], [{"target_field": "product_name", "source_header": "Product Name"},
                                                                     {"target_field": "raw_amount", "source_header": "Retail Cost"},
                                                                     {"target_field": "order_type", "source_header": "Order Type"}])
check("mapping_shape matches headers case-insensitively, as apply_mapping does, and names what the file lacks",
      shape["present"] == ["product_name", "raw_amount"] and shape["missing"] == [{"target_field": "order_type", "header": "Order Type"}])


def entry(fn, per, rows=5, total=-50.0, shape_present=("product_name", "raw_amount"), error=None):
    return {"filename": fn, "error": error, "rows": rows, "payout_total": total, "net_total": total, "footer_rows": 0,
            "detection": {"proposed": per, "months": [{"period": per, "rows": rows}] if per else [], "spans": False,
                          "tied": False, "dated_rows": rows if per else 0, "date_field": "trans_date"},
            "shape": {"present": list(shape_present), "missing": []}}


pl = LB.plan_batch([entry("a.csv", "June 2026"), entry("b.csv", "July 2026")])
check("a clean plan: two files, two months, both 'land', nothing refused", not pl["refused"] and [p["action"] for p in pl["files"]] == ["land", "land"]
      and pl["to_land"] == [0, 1])
pl = LB.plan_batch([entry("a.csv", "July 2026"), entry("b.csv", "July 2026")])
check("two files → one month: BOTH are blocked, each naming the other, and the batch is refused",
      pl["refused"] and all(p["action"] == "refused" for p in pl["files"]) and "b.csv" in pl["files"][0]["blocking"][0]
      and "a.csv" in pl["files"][1]["blocking"][0], pl["refusals"])
pl = LB.plan_batch([entry("a.csv", "July 2026"), entry("b.csv", "July 2026")], overrides=["", "aug 2026"])
check("…and setting the second file's month ('aug 2026') resolves it: August 2026 (set), canonical, nothing refused",
      not pl["refused"] and pl["files"][1]["period"] == "August 2026" and pl["files"][1]["period_source"] == LB.SOURCE_SET)
check("commit_refusals: an already-landed month needs confirm_replace; another origin needs confirm_other_origin; a refused plan stays refused",
      LB.commit_refusals({"refusals": ["x"]}, True, True) == ["x"]
      and LB.commit_refusals({"refusals": [], "replace_periods": ["June 2026"], "other_origin_periods": []})[0].startswith("1 month(s) are already landed")
      and LB.commit_refusals({"refusals": [], "replace_periods": ["June 2026"], "other_origin_periods": []}, confirm_replace=True) == []
      and "another source" in LB.commit_refusals({"refusals": [], "replace_periods": [], "other_origin_periods": ["May 2026"]})[0])
check("no files → refused", LB.plan_batch([])["refused"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. BYTE IDENTITY — the batch lands EXACTLY what N single imports land (two separate fakes)")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
dbA = fresh_db()
typed = {"june.csv": "Jun 2026", "july.csv": "2026-07", "august.csv": "august 2026"}   # as a person might type them
singles = [single(statement(mo, seed=sd), fn, typed[fn]) for fn, mo, sd in MONTHS]
rowsA, logA = strip(ledger(dbA)), strip(dbA.tables.get("upload_log") or [])
dbB = fresh_db()
prev = batch_preview(uploads(MONTHS))
check("the preview lands NOTHING (the ledger is empty after it)", ledger(dbB) == [] and not (dbB.tables.get("upload_log") or []))
check("the preview detects each file's month from its OWN dates, canonical, despite the trailing lines of the month before",
      [(p["filename"], p["period"], p["period_source"]) for p in prev["files"]]
      == [("june.csv", "June 2026", "detected"), ("july.csv", "July 2026", "detected"), ("august.csv", "August 2026", "detected")]
      and all(p["detection"]["spans"] for p in prev["files"]), [(p["filename"], p["period"]) for p in prev["files"]])
check("the preview shows rows and total per file — the same the single import reports (its summary's payout_total)",
      [p["rows"] for p in prev["files"]] == [s["saved"] for s in singles]
      and [money(p["payout_total"]) for p in prev["files"]] == [money(s["summary"]["payout_total"]) for s in singles],
      ([p["payout_total"] for p in prev["files"]], [s["summary"]["payout_total"] for s in singles]))
check("the preview says the months are NOT already landed and warns that the dates span two months",
      all(p["already"] is None and p["action"] == "land" and p["warnings"] for p in prev["files"]) and not prev["refused"])
res = batch(uploads(MONTHS))
rowsB, logB = strip(ledger(dbB)), strip(dbB.tables.get("upload_log") or [])
check("the batch landed all three, each under its own month, and says so",
      [(r["filename"], r["period"], r["ok"]) for r in res["results"]]
      == [("june.csv", "June 2026", True), ("july.csv", "July 2026", True), ("august.csv", "August 2026", True)]
      and res["sentence"].startswith("Landed 3 of 3 file(s)"), res.get("sentence"))
check(f"BYTE IDENTITY: the {len(rowsB)} ledger rows the batch landed == the {len(rowsA)} rows three single imports landed (same order, every column)",
      rowsA == rowsB and len(rowsA) == sum(s["saved"] for s in singles) > 0,
      next(((a, b) for a, b in zip(rowsA, rowsB) if a != b), (len(rowsA), len(rowsB))))
check("…and the upload_log rows are identical too (file type, canonical period, filename, rows saved)",
      logA == logB and [l["period"] for l in logB] == ["June 2026", "July 2026", "August 2026"], (logA, logB))
check("every landed row carries the ONE stored key and a canonical period (no new spelling)",
      {r["source_report"] for r in rowsB} == {LONG} and {r["period"] for r in rowsB} == {"June 2026", "July 2026", "August 2026"})
check("the per-file result carries what the single import reports (saved, stored period, stored key, payout)",
      [(r["saved"], r["period"], r["source_report"], money(r["payout_total"])) for r in res["results"]]
      == [(s["saved"], s["period"], s["source_report"], money(s["summary"]["payout_total"])) for s in singles])
check("the batch is summed by the ledger's own readers exactly like the singles (/summary per month, no landing conflict)",
      all((use(dbA) and R.commission_ledger_summary(source_report=CODE, period=per, org_id=ORG)["payout_total"])
          == (use(dbB) and R.commission_ledger_summary(source_report=CODE, period=per, org_id=ORG)["payout_total"])
          for per in ("June 2026", "July 2026", "August 2026"))
      and not R.commission_ledger_summary(source_report=CODE, period="July 2026", org_id=ORG).get("refused"))
# file order does not decide the month
dbC = fresh_db()
resC = batch(uploads(list(reversed(MONTHS))))
check("uploaded in reverse order, each file still lands under ITS OWN month (the month comes from the file, never the position)",
      [(r["filename"], r["period"]) for r in resC["results"]] == [("august.csv", "August 2026"), ("july.csv", "July 2026"), ("june.csv", "June 2026")]
      and sorted(json.dumps(r, sort_keys=True) for r in strip(ledger(dbC))) == sorted(json.dumps(r, sort_keys=True) for r in rowsB))

# the SAVED mapping (per statement type) + its sign convention, learned once, applies to every file
SAVED_HDR = {"account_id": "Acct No", "account_name": "Customer", "store": "Dealer", "rep_user": "Agent", "order_number": "Ref",
             "order_type": "Kind", "product_name": "Description", "trans_date": "Paid On", "due_date": "Due On", "raw_amount": "Amount Paid"}


def saved_mapping_db():
    db = fresh_db()
    for tf, h in SAVED_HDR.items():
        row = {"org_id": ORG, "report_key": RK, "carrier_id": None, "target_field": tf, "source_header": h,
               "transform": "date10" if tf in ("trans_date", "due_date") else ("number" if tf == "raw_amount" else "text"),
               "is_active": True, "priority": 100}
        if tf == "raw_amount":
            row["sign_convention"] = "payout_positive"
        db.seed("column_mapping", [row])
    return db


def saved_files():
    return [FakeUpload(statement(mo, seed=sd, hdr=SAVED_HDR, sign=1.0), filename=fn) for fn, mo, sd in MONTHS]


dbS1 = saved_mapping_db()
for (fn, mo, sd), f in zip(MONTHS, saved_files()):
    single(f.data, fn, typed[fn])
rowsS1 = strip(ledger(dbS1))
dbS2 = saved_mapping_db()
prevS = batch_preview(saved_files())
resS = batch(saved_files())
rowsS2 = strip(ledger(dbS2))
check("a SAVED mapping (other headers) + its sign convention ('earned is positive'): the preview reads every file by it — no file re-asked",
      not prevS["refused"] and all(not p["headers_differ"] for p in prevS["files"])
      and [p["period"] for p in prevS["files"]] == ["June 2026", "July 2026", "August 2026"])
check(f"BYTE IDENTITY under the saved mapping + convention: batch rows ({len(rowsS2)}) == single-import rows ({len(rowsS1)})",
      rowsS1 == rowsS2 and len(rowsS1) > 0 and all(r["ok"] for r in resS["results"]))
check("…and the declared convention was applied (earned-positive lines book as payouts with a positive payout_total)",
      money(sum(r["payout_total"] for r in rowsS2)) > 0 and resS["plan"]["files"][0]["payout_total"] > 0)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. THE SAME MONTH TWICE — refused, nothing written")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
dbD = fresh_db()
twice = [("july.csv", "2026-07", 2.0), ("july-corrected.csv", "2026-07", 2.5), ("august.csv", "2026-08", 3.0)]
pv = batch_preview(uploads(twice))
check("the preview blocks BOTH July files, each naming the other; August is fine; the batch is refused",
      pv["refused"] and [p["action"] for p in pv["files"]] == ["refused", "refused", "land"]
      and "july-corrected.csv" in " ".join(pv["files"][0]["blocking"]) and "july.csv" in " ".join(pv["files"][1]["blocking"]))
msg = refused(lambda: batch(uploads(twice)))
check("the commit is REFUSED (400) naming the month and the files — and NOTHING landed, not even August",
      msg is not None and "July 2026 is also the month of" in msg and ledger(dbD) == [], msg)
pv2 = batch_preview(uploads(twice), periods=json.dumps(["", "2026-09", ""]))
check("setting the second file's month on the preview ('2026-09' → September 2026) clears it",
      not pv2["refused"] and [p["period"] for p in pv2["files"]] == ["July 2026", "September 2026", "August 2026"]
      and pv2["files"][1]["period_source"] == "set")
pv3 = batch_preview(uploads(twice), periods=json.dumps(["", "July 2026", ""]))
check("setting a month that collides with another file is refused just the same (a set month is checked like a detected one)",
      pv3["refused"] and pv3["files"][1]["action"] == "refused")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. AN ALREADY-LANDED MONTH — the existing replace rule, confirmed, never double-landed")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
dbE = fresh_db()
first = single(statement("2026-06", seed=9.0, n=7), "june-old.csv", "June 2026")
old_june = [r for r in ledger(dbE) if r["period"] == "June 2026"]
# a residual statement in the same month — ANOTHER statement type, never touched by a commission landing
res_rows = [dict(r, source_report=f"{CODE}__residual_statement", product_name="Residual") for r in old_june[:3]]
for r in res_rows:
    r.pop("id", None)
dbE.seed("commission_ledger", res_rows)
pvE = batch_preview(uploads(MONTHS[:2]))
jf = pvE["files"][0]
check("the preview says June is ALREADY landed — rows, net and the landing (the lander's own measure) — action 'replace'",
      jf["already"] and jf["already"]["rows"] == first["saved"] and jf["action"] == "replace"
      and money(jf["already"]["payout_total"]) == money(first["summary"]["payout_total"])
      and jf["replaces"][0]["source_report"] == LONG and pvE["replace_periods"] == ["June 2026"], jf.get("already"))
check("…and it counts only THIS statement's family: the residual statement's June rows are not 'already landed' here",
      jf["already"]["rows"] == len(old_june))
msgE = refused(lambda: batch(uploads(MONTHS[:2])))
check("without confirm_replace the batch is REFUSED, naming the month — nothing written (old June intact, July not landed)",
      msgE is not None and "already landed and would be REPLACED (June 2026)" in msgE
      and strip([r for r in ledger(dbE) if r["source_report"] == LONG]) == strip(old_june), msgE)
resE = batch(uploads(MONTHS[:2]), confirm_replace="true")
june_now = [r for r in ledger(dbE) if r["period"] == "June 2026" and r["source_report"] == LONG]
check("confirmed: June is REPLACED (the new file's rows only — one landing, never two) and July lands",
      resE["results"][0]["ok"] and len(june_now) == resE["results"][0]["saved"] and resE["results"][1]["ok"]
      and not R.commission_ledger_summary(source_report=CODE, period="June 2026", org_id=ORG).get("refused")
      and all(r["account_id"].startswith("A2026-06") for r in june_now))
check("the result carries the lander's own replaced note for June ('replaced 7 rows …')",
      (resE["results"][0]["replaced_note"] or "").startswith(f"replaced {first['saved']} rows"), resE["results"][0].get("replaced_note"))
check("the residual statement's June rows are untouched (another statement type is never a casualty of a commission landing)",
      len([r for r in ledger(dbE) if r["source_report"] == f"{CODE}__residual_statement"]) == 3)
# an ORPHAN spelling of the month (the §30.15 class) is found and replaced by the lander's measure
dbF = fresh_db()
orphan = [dict(r) for r in strip(ledger(dbE)) if r["period"] == "July 2026" and r["source_report"] == LONG][:4]
for r in orphan:
    r.update(period="jul 2026", source_report=CODE)
dbF.seed("commission_ledger", orphan)
pvF = batch_preview(uploads(MONTHS[1:2]))
check("a landing under an orphan spelling ('jul 2026', the older bare key) is found as July already landed",
      pvF["files"][0]["action"] == "replace" and pvF["files"][0]["replaces"][0]["period"] == "jul 2026"
      and pvF["files"][0]["replaces"][0]["legacy_key"] and pvF["files"][0]["replaces"][0]["orphan_period"])
batch(uploads(MONTHS[1:2]), confirm_replace="1")
check("…and replaced by the batch's July landing (no 'jul 2026' left; July holds ONE landing)",
      not any(r["period"] == "jul 2026" for r in ledger(dbF))
      and len(CL.landings_for(ledger(dbF), "July 2026")[0]["landings"]) == 1)
# another ORIGIN in the same month (the MA refresh) — landing beside it makes two landings
dbG = fresh_db()
dbG.seed("commission_ledger", [{"org_id": ORG, "source_report": LONG, "period": "August 2026", "origin": LMS.ORIGIN_SYNC,
                                "category": "commission", "payout_total": 5.0, "raw_amount": -5.0, "is_payout": True}])
pvG = batch_preview(uploads(MONTHS[2:]))
check("a month holding an MA-refresh landing: the preview names it as ANOTHER origin, not something the file replaces",
      pvG["files"][0]["other_origin"] and not pvG["files"][0]["replaces"] and pvG["other_origin_periods"] == ["August 2026"])
msgG = refused(lambda: batch(uploads(MONTHS[2:])))
check("without confirm_other_origin the batch is refused (it would make two landings every reader refuses) — nothing written",
      msgG is not None and "another source" in msgG and len(ledger(dbG)) == 1, msgG)
resG = batch(uploads(MONTHS[2:]), confirm_other_origin="yes")
check("confirmed: the file lands BESIDE the synced rows (the lander's origin scope keeps them) — the existing two-landing rule then refuses the sum",
      resG["results"][0]["ok"] and any(r.get("origin") == LMS.ORIGIN_SYNC for r in ledger(dbG))
      and R.commission_ledger_summary(source_report=CODE, period="August 2026", org_id=ORG).get("refused") is True)
# never double-land: the same batch twice leaves ONE landing per month
dbH = fresh_db()
batch(uploads(MONTHS))
batch(uploads(MONTHS), confirm_replace="true")
check("the same batch twice (confirmed) leaves exactly one landing per month — never a double",
      all(len(g["landings"]) == 1 for g in CL.landings_for(ledger(dbH))) and len(CL.landings_for(ledger(dbH))) == 3
      and len(ledger(dbH)) == sum(s["saved"] for s in singles))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. A MONTH THAT CANNOT BE DETERMINED — refused until set")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
dbI = fresh_db()
undated = [FakeUpload(statement("2026-06", seed=1.0), filename="june.csv"),
           FakeUpload(statement("2026-07", seed=2.0, blank_dates=True), filename="statement (1).csv")]
pvI = batch_preview(undated)
check("a file whose lines carry no date: the preview says the month could not be read and asks for it — the batch is refused",
      pvI["refused"] and pvI["files"][1]["period"] is None and "could not be read" in pvI["files"][1]["blocking"][0]
      and pvI["files"][0]["action"] == "land")
check("…the commit refuses it too, and writes nothing (not even the dated June file)",
      refused(lambda: batch(undated)) is not None and ledger(dbI) == [])
resI = batch(undated, periods=json.dumps(["", "Jul 2026"]))
check("once the month is SET ('Jul 2026') it lands under the canonical 'July 2026' — the file's own lines, the set month",
      [(r["period"], r["period_source"], r["ok"]) for r in resI["results"]] == [("June 2026", "detected", True), ("July 2026", "set", True)]
      and {r["period"] for r in ledger(dbI)} == {"June 2026", "July 2026"})
tied = [FakeUpload(statement("2026-07", n=8, split_even=("2026-07", "2026-08")), filename="odd.csv")]
pvT = batch_preview(tied)
check("dates split EVENLY between two months: refused, naming both months — never a coin toss",
      pvT["refused"] and "split evenly between July 2026 (4) and August 2026 (4)" in pvT["files"][0]["blocking"][0], pvT["refusals"])
pvN = batch_preview(uploads(MONTHS[:1]), periods=json.dumps(["Agust 2026"]))
check("a typed month that is not a month ('Agust 2026') is refused, never stored as typed",
      pvN["refused"] and "'Agust 2026' is not a month" in pvN["files"][0]["blocking"][0])
check("a malformed periods field is a 400 naming it, never a 500",
      "periods" in (refused(lambda: batch_preview(uploads(MONTHS[:1]), periods="{not json")) or "")
      and "JSON list" in (refused(lambda: batch_preview(uploads(MONTHS[:1]), periods='{"a": 1}')) or ""))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. A PER-FILE FAILURE IS ISOLATED — the others land, the result names exactly which did not")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
class FailingDB(FakeDB):
    """The ledger insert fails for ONE month (an outage mid-batch) — every other write behaves."""
    fail_period = "July 2026"

    def table(self, name):
        q = super().table(name)
        if name == "commission_ledger":
            orig = q.insert

            def ins(rows, _orig=orig):
                rs = rows if isinstance(rows, list) else [rows]
                if any(r.get("period") == self.fail_period for r in rs):
                    raise RuntimeError("simulated outage")
                return _orig(rows)
            q.insert = ins
        return q


dbJ = fresh_db(FailingDB)
resJ = batch(uploads(MONTHS))
check("June and August land; July fails with the lander's own error — and the batch does not stop at it",
      [(r["filename"], r["ok"]) for r in resJ["results"]] == [("june.csv", True), ("july.csv", False), ("august.csv", True)]
      and "simulated outage" in resJ["results"][1]["error"], resJ["results"])
check("the ledger holds June and August exactly as their single imports would (July absent, nothing rolled back)",
      {r["period"] for r in ledger(dbJ)} == {"June 2026", "August 2026"}
      and strip([r for r in ledger(dbJ) if r["period"] != "July 2026"]) == [r for r in rowsA if r["period"] != "July 2026"])
check("the sentence says exactly which files landed and which did not, and that nothing was rolled back",
      resJ["sentence"].startswith("Landed 2 of 3 file(s): june.csv → June 2026")
      and "NOT landed: july.csv — Insert into commission_ledger failed" in resJ["sentence"]
      and "rolls back no other" in resJ["sentence"] and [r["filename"] for r in resJ["failed"]] == ["july.csv"], resJ["sentence"])
FailingDB.fail_period = "June 2026"
dbK = fresh_db(FailingDB)
resK = batch(uploads(MONTHS))
check("the FIRST file failing does not stop the rest (July and August land)",
      [r["ok"] for r in resK["results"]] == [False, True, True] and {r["period"] for r in ledger(dbK)} == {"July 2026", "August 2026"})

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. COLUMNS THAT DIFFER — named, and refused rather than mapped short")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
dbL = fresh_db()
no_type = [c for c in COLS if c != "order_type"]
diff = uploads(MONTHS[:2]) + [FakeUpload(statement("2026-08", seed=3.0, cols=no_type), filename="august-new-layout.csv")]
pvL = batch_preview(diff)
check("a file lacking a column the other files (and the mapping) have is NAMED — 'Order Type' (order_type) — and blocked",
      pvL["refused"] and pvL["files"][2]["headers_differ"] == ["order_type"]
      and "'Order Type' (order_type)" in pvL["files"][2]["blocking"][0] and pvL["files"][0]["action"] == "land", pvL["refusals"])
no_amt = [c for c in COLS if c != "raw_amount"]
pvM = batch_preview(uploads(MONTHS[:1]) + [FakeUpload(statement("2026-07", cols=no_amt), filename="no-amount.csv")])
check("a file with no amount column: refused with the reason ('Retail Cost' (raw_amount) … every line needs)",
      pvM["refused"] and any("'Retail Cost' (raw_amount)" in b for b in pvM["files"][1]["blocking"]), pvM["refusals"])
check("nothing landed from either refused batch", refused(lambda: batch(diff)) is not None and ledger(dbL) == [])
bad = uploads(MONTHS[:1]) + [FakeUpload(b"\x00\x01\x02 not a sheet", filename="broken.xlsx")]
pvB = batch_preview(bad)
check("an unreadable file is refused with the single import's own message ('Could not read file')",
      pvB["refused"] and pvB["files"][1]["blocking"][0].startswith("Could not read file"), pvB["refusals"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("H. NEGATIVE CONTROLS — each rule removed turns its check RED")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# (1) a batch that reads the month from the file's POSITION (a sibling derivation) lands reversed files wrong
wrong = {i: p for i, p in enumerate(["June 2026", "July 2026", "August 2026"])}
check("a sibling that assigns months by upload order would land august.csv as June (the proof would see it) → RED",
      [wrong[i] for i, _ in enumerate(reversed(MONTHS))][0] == "June 2026" != resC["results"][0]["period"])
# (2) the duplicate-month rule removed: the second July would replace the first — a silent loss
_keep = LB.plan_batch
src_plan = inspect.getsource(LB.plan_batch)
check("without the one-statement-per-month rule the two July files would both be 'land' (the later silently replacing the earlier) → RED",
      "one statement per month" in src_plan
      and [p["action"] for p in LB.plan_batch([entry("a", "July 2026"), entry("b", "July 2026")])["files"]] == ["refused", "refused"])
# (3) a literal period spelling: storing the typed 'Jul 2026' is an orphan no reader finds
check("storing the typed spelling would be an orphan (CL.is_orphan_period('Jul 2026')) — the batch stores the canonical form → RED avoided",
      CL.is_orphan_period("Jul 2026") and not CL.is_orphan_period(resI["results"][1]["period"]))
# (4) byte identity is a MEASUREMENT: perturb one row and the comparison fails
perturbed = copy.deepcopy(rowsB)
perturbed[0]["payout_total"] = money(perturbed[0]["payout_total"]) + 0.01
check("ARMED — one cent on one row makes the byte-identity comparison RED", perturbed != rowsA)
# (5) a batch that skipped confirm_replace would have destroyed June silently
check("ARMED — commit_refusals with the replace gate removed returns nothing for an already-landed month (what would have been silent)",
      LB.commit_refusals({"refusals": [], "replace_periods": ["June 2026"], "other_origin_periods": []}, confirm_replace=True) == []
      and LB.commit_refusals({"refusals": [], "replace_periods": ["June 2026"], "other_origin_periods": []}) != [])
# (6) org scope: another org's ledger is never "already landed" here and is never touched
dbO = fresh_db()
dbO.seed("commission_ledger", [dict(r, org_id=OTHER_ORG) for r in rowsB])
pvO = batch_preview(uploads(MONTHS))
batch(uploads(MONTHS))
check("ORG SCOPE: another org's June–August are not 'already landed' for this org, and survive this org's batch byte for byte",
      all(p["already"] is None for p in pvO["files"]) and strip(ledger(dbO, OTHER_ORG)) == [dict(r, org_id=OTHER_ORG) for r in rowsB]
      and strip(ledger(dbO)) == rowsB)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("J. ONE MAPPING PER STATEMENT — the Ledger page reads the mapping the intake mapped it with (index §30.17a)")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE LIVE SHAPE (org f4f1c16e…, read-only 2026-09-24): under the same key the intake saved 7 CARRIER rows and the
# older Ledger-page setup 5 GLOBAL rows; they disagree on the label / sub-label columns and on which sign is earned.
CID = "c0ffee00-0000-0000-0000-000000000001"          # fresh_db's carrier row (code = CODE)
OWN_HDR = ["Store", "Report Section", "Report SubSection", "Customer Name", "Mobile Number", "Master Service Date", "Gross", "AgentSSOID"]
CARRIER_MAP = {"store": "Store", "order_type": "Report Section", "product_name": "Report SubSection",
               "account_name": "Customer Name", "order_number": "Mobile Number", "trans_date": "Master Service Date",
               "raw_amount": "Gross"}
GLOBAL_MAP = {"order_type": "Report SubSection", "product_name": "Report Section", "rep_user": "AgentSSOID",
              "trans_date": "Master Service Date", "raw_amount": "Gross"}
LINES = [("Activations", "Phone Activation", 155.0, 9), ("Activations", "Phone Activation Deactivations", -155.0, 2),
         ("Incentives", "New Unlimited Plan", 25.0, 11), ("Upgrades", "Phone Upgrade", 125.0, 6),
         ("Upgrades", "DPP Service Fee - Upgrade", -39.3, 4)]
BUCKET_OF = {"Phone Activation": "commission", "Phone Activation Deactivations": "chargebacks",
             "New Unlimited Plan": "spiff", "Upgrade": "commission", "Phone Upgrade": "commission",
             "DPP Service Fee - Upgrade": "vendor_fee"}


def own_statement(month="2026-07", total_store="Commission Grand Totals"):
    """The carrier's own layout, header on the first row, and its total row STAMPED with a store cell (as the
    live statement writes it: 'Commission Grand Totals' in Store, every other identity blank)."""
    out, n, tot = [",".join(OWN_HDR)], 0, 0.0
    for sec, sub, amt, cnt in LINES:
        for _ in range(cnt):
            n += 1
            tot += amt
            out.append(f"Store {1 + n % 2},{sec},{sub},Cust {n},555{n:04d},{month}-{1 + n % 27:02d},{amt:.2f},SSO{n % 3}")
    if total_store is not None:
        out.append(f"{total_store},,,,,,{round(tot, 2):.2f},")
    return ("\n".join(out) + "\n").encode("utf-8"), round(tot, 2)


def seed_rules(db, mapping, carrier_id=None, sign=None):
    for tf, h in mapping.items():
        row = {"org_id": ORG, "report_key": RK, "carrier_id": carrier_id, "target_field": tf, "source_header": h,
               "transform": "date10" if tf == "trans_date" else ("number" if tf == "raw_amount" else "text"),
               "is_active": True, "priority": 100}
        if tf == "raw_amount" and sign:
            row["sign_convention"] = sign
        db.seed("column_mapping", [row])


STMT, STMT_TOTAL = own_statement()
# (1) the resolver, pure: the carrier's own set WHOLE; else the global set; never a per-field merge
g_rules = [{"target_field": "product_name", "source_header": "A", "carrier_id": None},
           {"target_field": "rep_user", "source_header": "R", "carrier_id": None}]
c_rules = [{"target_field": "product_name", "source_header": "B", "carrier_id": CID}]
check("statement_rules: the carrier's own set, WHOLE — the global rep_user row is NOT merged in (load_rules' per-field merge was a third answer)",
      CM.statement_rules(g_rules[1:] + c_rules, CID) == (c_rules, "carrier"))
check("statement_rules: a carrier with no rows of its own, or no carrier → exactly the global set (today's answer)",
      CM.statement_rules(g_rules, CID) == (g_rules, "global") and CM.statement_rules(g_rules, "") == (g_rules, "global")
      and CM.statement_rules([], CID) == ([], "none") and CM.carrier_rules(g_rules + c_rules, "") == [])
check("the template's carrier is the INVERSE of the intake's key: bare '<code>' and '<code>__commission_statement' both → the carrier; 'ma_daily_tx' → none",
      R._ledger_carrier_for([{"id": CID, "code": CODE, "name": "Northwind Cellular"}], CODE) == CID
      and R._ledger_carrier_for([{"id": CID, "code": CODE, "name": "Northwind Cellular"}], LONG) == CID
      and R._ledger_carrier_for([{"id": CID, "code": CODE, "name": "Northwind Cellular"}], "ma_daily_tx") == "")

# (2) GLOBAL-ONLY tenant: byte-identical to today's code path (load_rules(None) + the mig-1004 footer rule)
dbG1 = fresh_db()
seed_rules(dbG1, GLOBAL_MAP)
glob_file = statement("2026-07", seed=4.0, hdr={**DEFAULT_HDR, **{k: v for k, v in GLOBAL_MAP.items()}}, sign=-1.0)
prepG = R._ledger_prepare_file(dbG1, ORG, glob_file, "g.csv", CODE, "", "")
old_rules = CM.load_rules(dbG1, ORG, RK, None)
old_mapped = R._ledger_map_records(R._read_upload_df(glob_file, "g.csv").to_dict("records"), old_rules)
old_mapped, old_foot = CM.drop_footer_rows(old_mapped, RK, None, dbG1, ORG, fields=CM.identity_fields(RK, dbG1, ORG))
check("GLOBAL-ONLY tenant (its carrier exists, no carrier rows): the resolver returns EXACTLY today's rules (load_rules without a carrier)",
      prepG["hdr_rules"] == old_rules and prepG["mapping_source"] == "global" and prepG["carrier_id"] == CID)
check("…and EXACTLY today's mapped lines after the footer rule (the shared rule's rule 1 is the mig-1004 predicate)",
      prepG["mapped"] == old_mapped and prepG["footer_rows"] == old_foot)
impG = single(glob_file, "g.csv", "July 2026")
rows_old = [CL.build_row(src, {"org_id": ORG, "source_report": CODE, "period": "July 2026"}, prepG["cat_rules"], prepG["conv"], prepG["buckets"])
            for src in old_mapped]
for r in rows_old:
    r["source_report"], r["period"] = LONG, "July 2026"
check("…so the ledger rows the Ledger page lands for a global-only tenant are byte-identical to today's (built from today's rules)",
      strip(ledger(dbG1)) == strip(rows_old) and impG["mapping_source"] == "global")
dbG3 = fresh_db()
seed_rules(dbG3, GLOBAL_MAP)
single(glob_file, "g.csv", "July 2026", source_report="ma_daily_tx")
check("a template that names no carrier ('ma_daily_tx') reads the global set too — unchanged",
      R._ledger_prepare_file(dbG3, ORG, glob_file, "g.csv", "ma_daily_tx", "", "")["mapping_source"] == "global")

# (3) BOTH sets (the live shape): the intake lands the statement; the Ledger page now lands it IDENTICALLY
COMPANY = json.dumps({"store": {"Store 1": {"action": "company_level"}, "Store 2": {"action": "company_level"}}})
ASSIGNS = json.dumps([{"label": k, "bucket": v, "is_reversal": False} for k, v in BUCKET_OF.items()])


def intake_commit(db, data, period):
    use(db)
    return run(R.onboarding_intake_commit(file=FakeUpload(data, filename="own-july.csv"), source_kind="commission",
                                          carrier_id=CID, statement_type="", pos_source="", layout="", name="",
                                          period=period, column_map=json.dumps(CARRIER_MAP), sign_answer="positive",
                                          assignments=ASSIGNS, identity=COMPANY, attestation="", typed_total="",
                                          as_of_date="", verified_by="tester", sheet="", header_row="", footer="auto",
                                          instance_key="", use_stored="", role="", report_kind="",
                                          confirm_replace_other_kinds="", tender_columns="", tie_field="", tender_basis="",
                                          org_id=ORG))


dbI1 = fresh_db()
seed_rules(dbI1, GLOBAL_MAP)                                   # the older Ledger-page setup (5 global rows)
ic = intake_commit(dbI1, STMT, "July 2026")
intake_rows = strip(ledger(dbI1), drop=("id", "created_at", "synced_at", "origin"))
carrier_rows_saved = [r for r in dbI1.tables.get("column_mapping") or [] if r.get("carrier_id") == CID]
check("the intake lands the statement (ok, its total row dropped, Σ = the file's total) and saves the CARRIER set beside the global set",
      ic.get("ok") is True and len(intake_rows) == sum(c for *_x, c in LINES)
      and money(sum(r["payout_total"] for r in intake_rows)) == STMT_TOTAL and len(carrier_rows_saved) == len(CARRIER_MAP),
      (ic.get("problems"), len(intake_rows)))
# the same file on the Ledger page (the template is the bare carrier code; the page sends no carrier — the template names it)
dbI2 = copy.deepcopy(dbI1)
dbI2.tables["commission_ledger"] = []
use(dbI2)
impI = single(STMT, "own-july.csv", "July 2026")
page_rows = strip(ledger(dbI2), drop=("id", "created_at", "synced_at", "origin"))
check("the Ledger page reads the CARRIER's set (mapping_source 'carrier', the template's carrier) — not the 5 global rows",
      impI["mapping_source"] == "carrier" and impI["carrier_id"] == CID)
check("BYTE IDENTITY with the intake: the Ledger page lands the SAME rows the intake landed for the same file (every column)",
      page_rows == intake_rows, next(((a, b) for a, b in zip(page_rows, intake_rows) if a != b), (len(page_rows), len(intake_rows))))
check("…including the total row STAMPED with a store: dropped by the shared footer rule (not booked a second time)",
      impI["footer_rows_dropped"] == 1 and money(sum(r["payout_total"] for r in page_rows)) == STMT_TOTAL)
dbI3 = copy.deepcopy(dbI1)
dbI3.tables["commission_ledger"] = []
use(dbI3)
resI3 = batch([FakeUpload(STMT, filename="own-july.csv")])
check("the BATCH lands the same rows too (one file, the detected month July 2026, the carrier's set)",
      resI3["results"][0]["ok"] and strip(ledger(dbI3), drop=("id", "created_at", "synced_at", "origin")) == intake_rows)
# NEGATIVE CONTROLS: the pre-fix page (the global set) and the pre-fix footer rule
use(dbI2)
hdr_old = CM.load_rules(dbI2, ORG, RK, None)
conv_old, meta_old = CL.convention_from_mapping(hdr_old)
old_m = R._ledger_map_records(R._read_upload_df(STMT, "x.csv").to_dict("records"), hdr_old)
old_m, _f = CM.drop_footer_rows(old_m, RK, None, dbI2, ORG, fields=CM.identity_fields(RK, dbI2, ORG))
pre = [CL.build_row(s, {"org_id": ORG, "source_report": CODE}, CL.load_rules(dbI2, ORG, CODE), conv_old,
                    CL.builtin_buckets()) for s in old_m]
check("RED without the resolver: the global set maps the label columns swapped and reads the sign the other way — a different Σ",
      meta_old["name"] == "payout_negative" and money(sum(r["payout_total"] for r in pre)) != STMT_TOTAL,
      money(sum(r["payout_total"] for r in pre)))
cr = CM.carrier_rules(CM.load_rules(dbI2, ORG, RK, CID), CID)
m1 = R._ledger_map_records(R._read_upload_df(STMT, "x.csv").to_dict("records"), cr)
kept1, dropped1 = CM.drop_footer_rows(m1, RK, None, dbI2, ORG, fields=CM.identity_fields(RK, dbI2, ORG))
check("RED without the shared footer rule: rule 1 alone keeps the store-stamped total as a line (the live July statement: 974 rows, 331,995.18)",
      dropped1 == 0 and len(kept1) == len(m1) == sum(c for *_x, c in LINES) + 1)
merged = CM.load_rules(dbI2, ORG, RK, CID)
check("RED with the per-field MERGE: load_rules(carrier) mixes in the global rep_user row the intake never mapped",
      any(r["target_field"] == "rep_user" and not r.get("carrier_id") for r in merged)
      and not any(r["target_field"] == "rep_user" for r in cr))
# the setup wizard's preview says which set the import reads and where to SAVE so the import reads it
anaI = run(R.commission_ledger_analyze(file=FakeUpload(STMT, filename="own-july.csv"), source_report=CODE, carrier_id="",
                                       statement_type="", org_id=ORG))
check("the setup wizard's preview reads the SAME set and tells the page to save into it (save_carrier_id = the carrier)",
      anaI["mapping_source"] == "carrier" and anaI["save_carrier_id"] == CID and anaI["carrier_id"] == CID)
tm = R.commission_ledger_templates(org_id=ORG)
check("the template picker carries each template's carrier for the page to send (the bare code → the carrier; 'ma_daily_tx' → none)",
      next(t for t in tm["templates"] if t["key"] == CODE)["carrier_id"] == CID
      and next(t for t in tm["templates"] if t["key"] == "ma_daily_tx")["carrier_id"] is None)
# the sibling ingest path: /upload-mapped could send a statement into the ledger raw — refused
use(fresh_db())
um = refused(lambda: run(R.upload_mapped(report_key=RK, target_table="", carrier_id=CID, period="July 2026",
                                         file=FakeUpload(STMT, filename="x.csv"), replace_other_kinds="", org_id=ORG)))
check("SIBLING: /upload-mapped with the ledger layout is REFUSED (one lander for a statement) — nothing written",
      um is not None and "Commission Ledger page" in um and not ledger(R.sb()), um)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("K. ONE STATEMENT IDENTITY, WHICHEVER PAGE — an intake-landed month re-uploaded by the batch is REPLACED, never doubled")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
dbK = copy.deepcopy(dbI1)                                      # July landed by the INTAKE (key <code>__commission_statement)
use(dbK)
pvK = batch_preview([FakeUpload(STMT, filename="own-july.csv")])
check("the batch preview sees the intake's July as ALREADY landed (same statement identity: the intake's key is the batch's family)",
      pvK["files"][0]["action"] == "replace" and pvK["files"][0]["replaces"][0]["source_report"] == LONG
      and pvK["files"][0]["already"]["rows"] == len(intake_rows))
check("…and refuses to replace it until confirmed — nothing written",
      refused(lambda: batch([FakeUpload(STMT, filename="own-july.csv")])) is not None
      and strip(ledger(dbK), drop=("id", "created_at", "synced_at", "origin")) == intake_rows)
resK = batch([FakeUpload(STMT, filename="own-july.csv")], confirm_replace="true")
kRows = strip(ledger(dbK), drop=("id", "created_at", "synced_at", "origin"))
check("confirmed: ONE copy of July — the intake's landing replaced by the batch's, row for row the same, one landing, the readers sum it",
      resK["results"][0]["ok"] and kRows == intake_rows and len(CL.landings_for(ledger(dbK), "July 2026")[0]["landings"]) == 1
      and not R.commission_ledger_summary(source_report=CODE, period="July 2026", org_id=ORG).get("refused")
      and money(R.commission_ledger_summary(source_report=CODE, period="July 2026", org_id=ORG)["payout_total"]) == STMT_TOTAL)
check("the result carries the lander's replaced note for the intake's rows",
      (resK["results"][0]["replaced_note"] or "").startswith(f"replaced {len(intake_rows)} rows"))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("I. WIRING, RULE TWO, REGISTRATION, CI, NO MIGRATION")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
src_batch = inspect.getsource(R.commission_ledger_import_batch)
src_plan_r = inspect.getsource(R._ledger_batch_plan)
check("the batch lands every file through _ledger_import_prepared (the single import's own land step) and nothing else",
      "_ledger_import_prepared(" in src_batch and ".insert(" not in src_batch and "_ledger_land_rows(" not in src_batch)
check("the single import IS prepare + land (the same two functions the batch uses)",
      "_ledger_prepare_file(" in inspect.getsource(R.commission_ledger_import)
      and "_ledger_import_prepared(" in inspect.getsource(R.commission_ledger_import)
      and "_ledger_land_rows(" in inspect.getsource(R._ledger_import_prepared))
check("the plan reads each file through _ledger_prepare_file and measures 'already landed' through _ledger_family_rows (one read)",
      "_ledger_prepare_file(" in src_plan_r and "_ledger_family_rows(" in src_plan_r and "_read_upload_df(" not in src_plan_r)
check("_ledger_landings_present (the lander's measure) reads through the same _ledger_family_rows",
      "_ledger_family_rows(" in inspect.getsource(R._ledger_landings_present))
paths = [getattr(r, "path", "") for r in R.router.routes]
check("both endpoints are registered once each", paths.count("/commcalc/commission-ledger/import-batch") == 1
      and paths.count("/commcalc/commission-ledger/import-batch/preview") == 1, [p for p in paths if "import-batch" in p])
carrier_words = re.compile(r"\b(boost|verizon|cricket|metro|vidapay|total\s+wireless|luxelink|novawave|t-mobile|at&t)\b", re.I)
lb_src = open(os.path.join(HERE, "app/modules/commcalc/ledger_batch.py"), encoding="utf-8").read()
new_router = "\n".join(inspect.getsource(f) for f in (R._ledger_prepare_file, R._ledger_build_rows, R._ledger_import_prepared,
                                                    R.commission_ledger_import, R._ledger_batch_periods, R._ledger_batch_plan,
                                                    R.commission_ledger_import_batch_preview, R.commission_ledger_import_batch,
                                                    R._ledger_family_rows))
check("RULE TWO — no carrier / tenant name in ledger_batch.py or the new router functions",
      not carrier_words.search(lb_src) and not carrier_words.search(new_router))
check("ORG SCOPE — every read in the new router functions is org-scoped (the family read filters org_id)",
      '.eq("org_id", org_id)' in inspect.getsource(R._ledger_query) and "require_org(org_id)" in src_batch
      and "require_org(org_id)" in inspect.getsource(R.commission_ledger_import_batch_preview))
page = open(os.path.join(ROOT, "frontend/src/app/(platform)/commcalc/commission-ledger/page.tsx"), encoding="utf-8").read()
comp = open(os.path.join(ROOT, "frontend/src/components/LedgerBatchUpload.tsx"), encoding="utf-8").read()
check("the Commission Ledger page renders the multi-file upload for the picked template",
      "import LedgerBatchUpload from '@/components/LedgerBatchUpload'" in page and "<LedgerBatchUpload source={src}" in page)
check("…which previews → lets a month be edited → Upload all, through the two endpoints, with the replace confirmation",
      "/api/v1/commcalc/commission-ledger/import-batch/preview" in comp and "'/api/v1/commcalc/commission-ledger/import-batch'" in comp
      and "multiple" in comp and "confirm_replace" in comp and "confirm_other_origin" in comp and "Upload all" in comp
      and "fd.append('periods', JSON.stringify(ms))" in comp)
idx = open(os.path.join(ROOT, "docs/SYSTEM_DATA_FLOW_INDEX.md"), encoding="utf-8").read()
check("registered in the index: §30.17 + the endpoints in §17 + the functions/table rows in §16 + the harnesses",
      "### 30.17" in idx and "/commission-ledger/import-batch" in idx and "ledger_batch.plan_batch" in idx
      and "harness_ledger_batch.py" in idx and "harness_ledger_batch_lock.py" in idx
      and idx.count("/commission-ledger/import-batch") >= 3)
check("registered: §30.17a (one mapping per statement) + the resolver in §16 / §18 + the templates carrier in §17",
      "### 30.17a" in idx and idx.count("column_mapping.statement_rules") >= 3 and "templates[].carrier_id" in idx
      and "_ledger_carrier_for" in idx)
wf = open(os.path.join(ROOT, ".github/workflows/carrier-vocab-guard.yml"), encoding="utf-8").read()
check("CI runs this proof and its lock", "harness_ledger_batch.py" in wf and "harness_ledger_batch_lock.py" in wf)
migs = sorted(f for f in os.listdir(os.path.join(ROOT, "database/migrations")) if "batch" in f.lower() and "ledger" in f.lower())
check("no migration: the batch lands through the existing lander into the existing columns", migs == [], migs)

print(f"\n══ ledger batch import: {_pass} passed, {_fail} failed ══")
if _fail:
    print("FAILED:\n  " + "\n  ".join(_failures))
    sys.exit(1)
