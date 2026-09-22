"""DB-FREE PROOF — a ledger statement has ONE identity, ONE period spelling, ONE lander and ONE guard
(owner 2026-09-22; index §30.15).

    python3 harness_ledger_statement_identity.py        (from backend/; no network, no DB)

THE INSTANCE (measured on the live tenant, org f4f1c16e…, 2026-09-22 — `commcalc.commission_ledger`
grouped by (period, source_report), Σ payout_total):

    'Aug 2026'    / <carrier>                          522 rows    7,396.27
    'aug 2026'    / <carrier>__commission_statement    521 rows    7,396.27
    'August 2026' / <carrier>__commission_statement    521 rows   86,970.34
    'July 2026'   / <carrier>                          973 rows   14,411.40
    'July 2026'   / <carrier>__commission_statement    973 rows  165,997.59

The same file landed at 04:00:37 through the onboarding intake (under `<carrier>__commission_statement`)
and at 04:00:57 through the older /commission-ledger/import wizard (under the bare `<carrier>`): the
same statement under two routes was two statements, so the scoped replace never saw the other copy,
and a ledger-sourced P&L (§4b) would have booked July TWICE. The 'Aug 2026' / 'aug 2026' copies are
ORPHANS: stored as typed, found by no reader that looks periods up through `period_keys`.

THE CLASS (three facts, each fixed for every caller, one home each — CLAUDE.md "A fix is a DESIGN fix"):
  1. the ledger identity was ROUTE-DEPENDENT      → ONE derivation, commission_ledger.ledger_source_report
                                                     / ledger_identity / source_report_family, dereferenced
                                                     by the intake (source_report_key), the older wizard and
                                                     the MA refresh — all through the ONE lander,
                                                     router._ledger_land_rows; a bare key READS as that
                                                     base's default statement type (compatibility)
  2. the period was stored AS TYPED               → ONE spelling at landing, account/_period.canonical_period
                                                     (the month-name form, the first period_keys lists),
                                                     every reader filters through period_keys
  3. no reader guarded against N copies           → commission_ledger.landings_for / landing_conflicts: a
                                                     statement × period holding more than one landing is
                                                     REFUSED by every summing reader (the ledger page, by-rep,
                                                     the P&L booking) with the sentence
                                                     "July 2026 holds 2 landings of the commission statement
                                                     (973 + 973 rows) — retire one under Onboarding → Intake"

Runs the REAL router functions over the shared in-memory client (harness_intake_fakes.FakeDB, the
pattern of every intake proof) and the REAL P&L builder over the P&L proof's own client, so the
endpoints' own paths — _ledger_land_rows, _ledger_delete_scoped, /commission-ledger/summary, /by-rep,
/observed-types, /landings, /landings/retire, /onboarding/intake/retire, coa.build_inputs — are
exercised, not re-stated. NEGATIVE CONTROLS (§H): a sibling that keys the landing by the raw key sees
no conflict → RED; the guard bypassed sums the double → RED; the lock scanner goes red on a literal
filter. RULE TWO: no carrier, tenant or product is named in any module this proves (§I reads them
back with `inspect`); the fixture's carrier is a made-up name.
"""
import asyncio
import copy
import inspect
import io
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.modules.commcalc import commission_ledger as CL
from app.modules.commcalc import column_mapping as CM
from app.modules.commcalc import onboarding_intake as OI
from app.modules.commcalc import ledger_ma_sync as LMS
from app.modules.commcalc import router as R
from app.modules.account import _period as PD
from app.modules.account import ledger_pnl as LP
from app.modules.account import coa as COA
from harness_intake_fakes import FakeDB, FakeUpload
import harness_ledger_identity_lock as LOCK
import harness_pl_commission_source as PLH

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ORG = "f4f1c16e-0000-4000-8000-000000000001"          # the reported tenant's SHAPE, not its data
HOUSE = CL.ORG_HOUSE
TENANT2 = "854f6d7b-0000-4000-8000-000000000002"      # the master-agent tenant's shape
CARRIER_ID = "aaaaaaaa-0000-0000-0000-00000000c001"
CODE = "northwind"                                    # a made-up carrier; no real carrier is named anywhere
LONG = f"{CODE}__commission_statement"
SENTENCE_JULY = "July 2026 holds 2 landings of the commission statement (973 + 973 rows) — retire one under Onboarding → Intake"

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
        print(f"  FAIL  {name}{(' — ' + str(extra)) if extra else ''}")
    return bool(cond)


def section(t):
    print(f"\n{t}\n" + "─" * len(t))


def money(x):
    return round(float(x or 0.0), 2)


def run(coro):
    return asyncio.run(coro)


def use(db):
    R.sb = lambda: db
    R._TABLE_COL_PRESENT.clear()
    return db


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE FIXTURE — the live shape, as ledger rows. Σ spread evenly to the cent; the last row takes the
# remainder so every group sums EXACTLY to the measured figure.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def spread(n, total):
    base = round(total / n, 2)
    amts = [base] * (n - 1)
    amts.append(round(total - sum(amts), 2))
    return amts


def landing_rows(org, source_report, period, n, total, origin="file", created_at=None, category="commission",
                 store="10 Main St", rep="alice"):
    out = []
    for i, amt in enumerate(spread(n, total)):
        r = {"org_id": org, "source_report": source_report, "period": period, "origin": origin,
             "store": store, "rep_user": rep, "product_name": "New Activation Commission",
             "order_type": "", "category": category, "payout_total": amt, "raw_amount": amt, "is_payout": True,
             "payment_month": 1, "commission": 0, "spiff": 0, "equipment_rebate": 0, "residual_monthly": 0, "autopay_residual": 0}
        if category in CL.CATEGORIES:
            r[category] = amt
        if created_at:
            r["created_at"] = created_at
        out.append(r)
    return out


LIVE = {
    "aug_bare":   (CODE, "Aug 2026", 522, 7396.27, "2026-09-20T04:01:20Z"),
    "aug_orphan": (LONG, "aug 2026", 521, 7396.27, "2026-09-20T00:15:00Z"),
    "aug_good":   (LONG, "August 2026", 521, 86970.34, "2026-09-20T04:00:40Z"),
    "july_bare":  (CODE, "July 2026", 973, 14411.40, "2026-09-20T04:00:57Z"),
    "july_good":  (LONG, "July 2026", 973, 165997.59, "2026-09-20T04:00:37Z"),
}


def live_rows(*keys):
    rows = []
    for k in (keys or LIVE.keys()):
        sr, per, n, tot, at = LIVE[k]
        rows += landing_rows(ORG, sr, per, n, tot, created_at=at)
    return rows


def fresh_db(rows=None, org=ORG):
    db = FakeDB()
    db.seed("carrier", [{"id": CARRIER_ID, "org_id": org, "name": "Northwind Cellular", "code": CODE}])
    if rows:
        db.seed("commission_ledger", rows)
    return use(db)


def ledger(db):
    return db.tables.get("commission_ledger") or []


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. ONE DERIVATION — the identity, its stored key, its family, its template key, for every route")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("a bare carrier code and the intake's long key name ONE identity (the base's default statement type)",
      CL.ledger_identity(CODE) == CL.ledger_identity(LONG) == (CODE, "commission_statement"))
check("the stored key is the intake's form byte-for-byte: ledger_source_report(bare) == onboarding_intake.source_report_key(code)",
      CL.ledger_source_report(CODE) == OI.source_report_key(CODE) == LONG
      and CL.ledger_source_report(CODE, "Residual statement") == OI.source_report_key(CODE, "residual statement") == f"{CODE}__residual_statement")
check("the intake's derivation DEREFERENCES the ledger's (no second rule): source_report_key / slug / STATEMENT_TYPE_DEFAULT",
      "CL.ledger_source_report(" in inspect.getsource(OI.source_report_key) and "CL.ledger_slug(" in inspect.getsource(OI.slug)
      and OI.STATEMENT_TYPE_DEFAULT is CL.LEDGER_STATEMENT_TYPE_DEFAULT)
check("the family of the default type = [canonical, bare] (+ an un-slugged legacy spelling when given); another type = [its key] only",
      CL.source_report_family(CODE) == [LONG, CODE] and CL.source_report_family(LONG) == [LONG, CODE]
      and CL.source_report_family("Northwind") == [LONG, CODE, "Northwind"]
      and CL.source_report_family(f"{CODE}__residual_statement") == [f"{CODE}__residual_statement"])
check("an explicit statement type on a wizard WINS over the key's suffix; blank keys are refused, never invented",
      CL.ledger_source_report(LONG, "residual statement") == f"{CODE}__residual_statement"
      and CL.source_report_family("") == [] and CL.ledger_identity("") == ("", "commission_statement"))
try:
    CL.ledger_source_report("")
    check("ledger_source_report('') raises", False)
except ValueError:
    check("ledger_source_report('') raises (a landing without a base cannot be keyed)", True)
check("the template key (what the picker lists and every source_report= query names) is the BARE base for the default type and <base>__<slug> otherwise — the §30.10 mapping-key SHAPE",
      CL.template_key(LONG) == CL.template_key(CODE) == CODE and CL.template_key("ma_daily_tx__commission_statement") == "ma_daily_tx"
      and CL.template_key(f"{CODE}__residual_statement") == f"{CODE}__residual_statement")
check("the mapping key (§30.10) is a projection of the SAME (base, type): the default type → today's key from bare and long alike; residual → its own",
      CL.mapping_report_key(CL.statement_type_of_source_report(LONG)) == CL.mapping_report_key(CL.statement_type_of_source_report(CODE))
      == CL.mapping_report_key("") == CL.mapping_report_key(CL.statement_type_of_source_report("ma_daily_tx__commission_statement"))
      and CL.mapping_report_key(CL.statement_type_of_source_report(f"{CODE}__residual_statement")) == CL.mapping_report_key("residual"))
check("identity_key composes the canonical key ONCE (the P&L groups by it, never by the raw key)",
      CL.identity_key(CODE) == CL.identity_key(LONG) == LONG and CL.identity_key("") == "" and CL.is_legacy_source_report(CODE)
      and not CL.is_legacy_source_report(LONG))
check("the built-in templates' defaults follow the identity: 'ma_daily_tx__commission_statement' has the 071 seed's rules, a carrier's key has none",
      CL.default_rules_for("ma_daily_tx__commission_statement") == CL.default_rules_for("ma_daily_tx") != []
      and CL.default_rules_for(CODE) == [] and CL.default_rules_for(LONG) == [])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. ONE PERIOD SPELLING — canonical at landing (the month-name form), every spelling read")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("canonical_period: 'Aug 2026' / 'aug 2026' / 'AUGUST 2026' / '2026-08' / '2026-8' / 'Sept 2026' → the month-name form",
      [PD.canonical_period(p) for p in ("Aug 2026", "aug 2026", "AUGUST 2026", "2026-08", "2026-8", "Sept 2026", " August 2026 ")]
      == ["August 2026"] * 5 + ["September 2026", "August 2026"])
check("the canonical spelling is the FIRST period_keys lists, the numeric form second, the literal kept when it is neither",
      PD.period_keys("Aug 2026") == ["August 2026", "2026-08", "Aug 2026"] and PD.period_keys("August 2026") == ["August 2026", "2026-08"]
      and PD.period_keys("2026-08") == ["August 2026", "2026-08"] and PD.period_keys("bogus") == ["bogus"])
check("period_keys is a SUPERSET of what it returned before (the literal is always kept; nothing a reader matched is lost)",
      all(p in PD.period_keys(p) for p in ("June 2026", "2026-06", "Aug 2026", "bogus", "")))
check("an unparseable period passes through unchanged — never guessed into a month",
      PD.canonical_period("Q3 2026") == "Q3 2026" and PD.canonical_period("") == "" and PD.parse_period("Q3 2026")[0] == 0)
check("the commcalc side DEREFERENCES the period home (canonical_period / ledger_period_keys / is_orphan_period)",
      CL.canonical_period("aug 2026") == "August 2026" and CL.ledger_period_keys("aug 2026") == PD.period_keys("aug 2026")
      and CL.ledger_period_keys("") == [] and CL.is_orphan_period("aug 2026") and not CL.is_orphan_period("August 2026")
      and "_pd.canonical_period(" in inspect.getsource(CL.canonical_period) and "_pd.period_keys(" in inspect.getsource(CL.ledger_period_keys))
check("parse_period is byte-identical on every spelling it parsed before",
      PD.parse_period("June 2026") == (6, 2026) == PD.parse_period("2026-06") == PD.parse_period("june 2026"))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. THE LIVE SHAPE AS A FIXTURE — the landings derived, the refusal sentence, the orphans")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
groups = {(g["key"], g["period"]): g for g in CL.landings_for(live_rows())}
july = groups[(LONG, "July 2026")]
aug = groups[(LONG, "August 2026")]
check("the five stored groups are TWO identity × period groups (one identity, July and August)",
      set(groups) == {(LONG, "July 2026"), (LONG, "August 2026")} and july["base"] == CODE and july["statement_type"] == "commission statement")
check("July holds 2 landings — the bare key's and the long key's — with their rows, nets and when they landed",
      july["conflict"] and [(x["source_report"], x["period"], x["rows"], x["payout_total"], x["landed_at"][:10], x["legacy_key"]) for x in july["landings"]]
      == [(CODE, "July 2026", 973, 14411.40, "2026-09-20", True), (LONG, "July 2026", 973, 165997.59, "2026-09-20", False)],
      july["landings"])
check("THE SENTENCE, verbatim: " + SENTENCE_JULY, july["sentence"] == SENTENCE_JULY, july["sentence"])
check("August holds 3 landings: the good one, and the two orphan spellings flagged as such",
      aug["conflict"] and sorted((x["period"], x["rows"], x["orphan_period"], x["legacy_key"]) for x in aug["landings"])
      == [("Aug 2026", 522, True, True), ("August 2026", 521, False, False), ("aug 2026", 521, True, False)]
      and aug["sentence"] == "August 2026 holds 3 landings of the commission statement (522 + 521 + 521 rows) — retire one under Onboarding → Intake")
check("landing_conflicts narrows to one canonical period; a period filter finds the orphans of THAT month",
      [g["sentence"] for g in CL.landing_conflicts(live_rows(), "July 2026")] == [SENTENCE_JULY]
      and [g["sentence"] for g in CL.landing_conflicts(live_rows(), "aug 2026")] == [aug["sentence"]])
check("the landings are ordered newest first (the sentence's counts follow: 522 landed last)",
      [x["rows"] for x in aug["landings"]] == [522, 521, 521])
check("the good copies alone are ONE landing each — no conflict",
      all(not g["conflict"] for g in CL.landings_for(live_rows("july_good", "aug_good"))))
check("the trace sentence for a replace names the older key, the date, and a differing net / count",
      CL.replaced_sentence([july["landings"][0]], 973, 165997.59)
      == "replaced 973 rows landed on 2026-09-20 under the older key 'northwind' (its net 14,411.40 / 973 rows differs from this landing's 165,997.59 / 973 rows — a different sign convention or line count)"
      and CL.replaced_sentence([], 5, 1.0) == "")
check("a landing's detail names an orphan spelling and a non-file origin",
      CL.landing_detail({"rows": 521, "landed_at": "2026-09-20T00:15:00Z", "source_report": LONG, "period": "aug 2026", "orphan_period": True, "legacy_key": False, "origin": "ma_sync"})
      == "521 rows landed on 2026-09-20 under the period spelling 'aug 2026', origin ma_sync")
s_all = CL.summarize(live_rows("july_bare", "july_good"))
check("summarize carries the guard ADDITIVELY — its totals are unchanged (the double is still what the rows sum to), the sentence rides beside them",
      s_all["landing_conflict"] == SENTENCE_JULY and money(s_all["payout_total"]) == money(14411.40 + 165997.59)
      and s_all["categories"]["commission"]["count"] == 1946)
check("rows without a key / period (a preview) are one landing — no conflict, nothing new refused",
      CL.summarize([{"category": "commission", "payout_total": 1.0}] * 3)["landing_conflict"] is None)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. THE REGRESSION through the REAL endpoints — the live July refused by every summing reader, then retired")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
db = fresh_db(live_rows())
for key in (CODE, LONG):
    s = R.commission_ledger_summary(source_report=key, period="July 2026", org_id=ORG)
    check(f"/commission-ledger/summary?source_report={key}&period=July 2026 REFUSES: 0 summed, 1,946 lines counted, the sentence",
          s.get("refused") is True and s["payout_total"] == 0 and s["earned_total"] == 0 and s["line_count"] == 1946
          and s["landing_conflict"] == SENTENCE_JULY and s["categories"]["commission"]["total"] == 0, (s.get("payout_total"), s.get("landing_conflict")))
s = R.commission_ledger_summary(source_report=CODE, period="2026-07", origin="file", org_id=ORG)
check("…the numeric period spelling and an origin filter read the same family and still refuse (both landings are file imports)",
      s.get("refused") is True and s["landing_conflict"] == SENTENCE_JULY)
br = R.commission_ledger_by_rep(source_report=CODE, period="July 2026", org_id=ORG)
check("/commission-ledger/by-rep refuses: no rep is paid the sum of two copies",
      br.get("refused") is True and br["reps"] == [] and br["landing_conflict"] == SENTENCE_JULY)
ot = R.commission_ledger_observed_types(source_report=LONG, period="July 2026", org_id=ORG)
check("/commission-ledger/observed-types refuses: no label's sum is doubled",
      ot.get("refused") is True and ot["types"] == [] and ot["landing_conflict"] == SENTENCE_JULY)
rw = R.commission_ledger_rows(source_report=CODE, period="July 2026", limit=5000, org_id=ORG)
check("/commission-ledger/rows (a listing, not a sum) returns the family's rows AND says they conflict",
      rw["count"] == 1946 and rw["landing_conflict"] == SENTENCE_JULY)
check("the per-rep statement buckets (the commission statement's ledger leg) read None rather than a doubled figure",
      R._statement_buckets(db, ORG, "July 2026", "alice", source_report=CODE) is None)
lg = R.commission_ledger_landings(source_report=CODE, org_id=ORG)
check("/commission-ledger/landings lists every landing of the family per canonical period, the conflicts, the orphan spellings",
      [(g["period"], len(g["landings"]), g["conflict"]) for g in lg["groups"]] == [("August 2026", 3, True), ("July 2026", 2, True)]
      and lg["orphan_periods"] == ["Aug 2026", "aug 2026"] and lg["stored_as"] == LONG and lg["family"] == [LONG, CODE], lg["groups"])
aug_s = R.commission_ledger_summary(source_report=CODE, period="August 2026", org_id=ORG)
check("August through period_keys sees ONLY the canonical copy (86,970.34) — the orphans count nowhere, exactly as measured live",
      aug_s.get("refused") is not True and money(aug_s["payout_total"]) == 86970.34 and aug_s["line_count"] == 521)
prov = R.commission_ledger_provenance(source_report=CODE, org_id=ORG)
pp = {p["period"]: p for p in prov["periods"]}
check("/commission-ledger/provenance flags the orphan spellings (stored ≠ canonical) and the July conflict",
      pp["Aug 2026"]["orphan"] is True and pp["Aug 2026"]["canonical"] == "August 2026" and pp["aug 2026"]["orphan"] is True
      and pp["August 2026"]["orphan"] is False and pp["July 2026"]["landing_conflict"] == SENTENCE_JULY
      and len(pp["July 2026"]["landings"]) == 2, {k: (v["orphan"], v.get("landing_conflict")) for k, v in pp.items()})
# THE RETIRE — one landing, counted, the number confirmed, removed by id, traced with the reason
body = R.LedgerLandingRetireIn(source_report=CODE, period="July 2026", origin="file", reason="landed twice — the copy under the older key", by="owner")
dry = R.commission_ledger_landing_retire(body, org_id=ORG, authorization="")
check("retire (dry run) counts exactly the older key's 973 rows and removes nothing",
      dry["dry_run"] is True and dry["removal"]["rows"] == 973 and len(ledger(db)) == 973 * 2 + 522 + 521 * 2)
try:
    R.commission_ledger_landing_retire(R.LedgerLandingRetireIn(**dict(body.model_dump(), confirm_rows="972")), org_id=ORG, authorization="")
    check("a confirmed count that is not the dry run's is refused", False)
except R.HTTPException as e:
    check("a confirmed count that is not the dry run's is refused (409), nothing removed",
          e.status_code == 409 and len(ledger(db)) == 973 * 2 + 522 + 521 * 2)
done = R.commission_ledger_landing_retire(R.LedgerLandingRetireIn(**dict(body.model_dump(), confirm_rows="973")), org_id=ORG, authorization="")
check("retire (confirmed) removes exactly the 973 rows under the older key — the good copy and every other period untouched",
      done["dry_run"] is False and done["removal"]["rows"] == 973 and len(ledger(db)) == 973 + 522 + 521 * 2
      and not [r for r in ledger(db) if r["source_report"] == CODE and r["period"] == "July 2026"]
      and len([r for r in ledger(db) if r["source_report"] == LONG and r["period"] == "July 2026"]) == 973)
tr = [t for t in (db.tables.get("upload_trace") or []) if t.get("source") == "ledger-retire"]
check("…and the removal is traced with the reason and the name",
      tr and "removed 973 row(s)" in tr[-1]["guard"]["note"] and "landed twice" in tr[-1]["guard"]["note"] and "owner" in tr[-1]["guard"]["note"], tr)
s2 = R.commission_ledger_summary(source_report=CODE, period="July 2026", org_id=ORG)
check("AFTER the cleanup July books 165,997.59 from the intake's copy — the sum returns, nothing refused",
      s2.get("refused") is not True and money(s2["payout_total"]) == 165997.59 and s2["line_count"] == 973 and s2["landing_conflict"] is None)
for orphan in (LIVE["aug_bare"], LIVE["aug_orphan"]):
    b = R.LedgerLandingRetireIn(source_report=orphan[0], period=orphan[1], origin="file", reason="orphan period spelling", by="owner")
    d = R.commission_ledger_landing_retire(b, org_id=ORG, authorization="")
    R.commission_ledger_landing_retire(R.LedgerLandingRetireIn(**dict(b.model_dump(), confirm_rows=str(d["removal"]["rows"]))), org_id=ORG, authorization="")
check("the two August orphans retired the same way (522 + 521) → August still 86,970.34, the ledger holds exactly the two good copies",
      len(ledger(db)) == 973 + 521 and money(R.commission_ledger_summary(source_report=CODE, period="August 2026", org_id=ORG)["payout_total"]) == 86970.34
      and R.commission_ledger_landings(source_report=CODE, org_id=ORG)["orphan_periods"] == [])
check("the money effect of the cleanup, stated: July 165,997.59 and August 86,970.34 from the intake copies; 14,411.40 + 7,396.27 + 7,396.27 of legacy / orphan copies gone",
      money(sum(r["payout_total"] for r in ledger(db))) == money(165997.59 + 86970.34))

# the intake's OWN retire path covers a commission instance: its statement × period family, counted
db2 = fresh_db(live_rows("july_bare", "july_good"))
db2.seed("onboarding_run", [{"id": "run-1", "org_id": ORG, "run_kind": "initial", "status": "in_progress", "started_at": "2026-09-20T04:00:00Z"}])
IK = OI.instance_key("commission", CARRIER_ID, "commission statement")
db2.seed("onboarding_stage_state", [{"id": "st-1", "run_id": "run-1", "org_id": ORG, "stage": "3", "step": "3.9", "instance_key": IK,
                                     "status": "verified", "payload": {"period": "July 2026", "kind": "commission"},
                                     "verified_numbers": {"source_report": LONG, "period": "July 2026", "rows_landed": 973}}])
rb = R.OnboardingRetireIn(instance_key=IK, reason="filed twice", by="owner", remove_landed="1")
d = R.onboarding_intake_retire(rb, org_id=ORG)
check("POST /onboarding/intake/retire on the commission instance (dry run): its slice is the statement × period FAMILY — both landings, 1,946 rows",
      d["dry_run"] is True and d["would_remove"]["rows"] == 1946 and d["would_remove"]["slice"]["source_report"] == [LONG, CODE]
      and d["would_remove"]["slice"]["period"] == ["July 2026", "2026-07"], d.get("would_remove"))
d2 = R.onboarding_intake_retire(R.OnboardingRetireIn(**dict(rb.model_dump(), confirm_rows="1946")), org_id=ORG)
check("…confirmed: exactly those rows removed, the line retired on the record with the count",
      d2["retired"]["removed"]["rows"] == 1946 and len(ledger(db2)) == 0
      and any(x["instance_key"] == IK for x in d2["rail"]["retired"]))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. THE ONE LANDER — every route lands the derived key and the canonical period; a re-land REPLACES any prior key / spelling")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
db = fresh_db(landing_rows(ORG, CODE, "July 2026", 5, 50.0, created_at="2026-09-20T04:00:57Z"))
rows = landing_rows(ORG, CODE, "july 2026", 5, 500.0)
m = {}
saved = R._ledger_land_rows(db, ORG, rows, CODE, "july 2026", filename="statement.csv", source="ledger-import", meta=m)
check("the older wizard's key (bare) + a lower-case period land as the canonical key and the canonical spelling on EVERY row",
      saved == 5 and all(r["source_report"] == LONG and r["period"] == "July 2026" for r in ledger(db))
      and m["source_report"] == LONG and m["period"] == "July 2026")
check("the 5 legacy rows under the bare key were REPLACED (the family × period wipe), never left as a second copy",
      len(ledger(db)) == 5 and money(sum(r["payout_total"] for r in ledger(db))) == 500.0)
check("the lander SAYS what it replaced — rows, when, under which key, and that the net differed",
      m["replaced_note"] == "replaced 5 rows landed on 2026-09-20 under the older key 'northwind' (its net 50.00 / 5 rows differs from this landing's 500.00 / 5 rows — a different sign convention or line count)"
      and m["replaced"][0]["legacy_key"] is True and m["replaced_measured"] is True, m.get("replaced_note"))
tr = [t for t in (db.tables.get("upload_trace") or []) if t.get("source") == "ledger-import"]
check("…and the upload trace carries the sentence, keyed on the STORED key and the canonical period",
      tr and tr[-1]["upload_type"] == LONG and tr[-1]["periods"] == {"July 2026": 5} and "replaced 5 rows" in tr[-1]["guard"]["note"]
      and tr[-1]["guard"]["note"].startswith("classified into the canonical ledger — replaced"), tr)
# an orphan spelling of the SAME month is replaced too (the wipe measures the family first)
db = fresh_db(landing_rows(ORG, LONG, "aug 2026", 4, 40.0, created_at="2026-09-20T00:15:00Z")
              + landing_rows(ORG, CODE, "Aug 2026", 3, 30.0, created_at="2026-09-20T04:01:20Z"))
m = {}
R._ledger_land_rows(db, ORG, landing_rows(ORG, LONG, "August 2026", 6, 600.0), LONG, "August 2026", meta=m)
check("landing 'August 2026' replaces the 'aug 2026' and 'Aug 2026' copies of the same month (orphans never survive a re-land)",
      len(ledger(db)) == 6 and all(r["period"] == "August 2026" for r in ledger(db))
      and sorted((x["period"], x["rows"]) for x in m["replaced"]) == [("Aug 2026", 3), ("aug 2026", 4)]
      and "under the period spelling 'aug 2026'" in m["replaced_note"] and "under the older key 'northwind'" in m["replaced_note"], m.get("replaced_note"))
# another statement type and another period are NEVER touched
db = fresh_db(landing_rows(ORG, f"{CODE}__residual_statement", "July 2026", 2, 20.0, created_at="2026-09-01T00:00:00Z")
              + landing_rows(ORG, LONG, "June 2026", 2, 22.0, created_at="2026-09-01T00:00:00Z"))
m = {}
R._ledger_land_rows(db, ORG, landing_rows(ORG, CODE, "July 2026", 1, 1.0), CODE, "July 2026", meta=m)
check("the residual statement of the same carrier and the June landing are untouched by a July commission landing",
      len(ledger(db)) == 5 and m["replaced"] == [] and m["replaced_note"] is None)
# a re-land of the same statement under the SAME key and spelling: replaced, said, no 'older key'
m = {}
R._ledger_land_rows(db, ORG, landing_rows(ORG, LONG, "July 2026", 1, 2.0), LONG, "July 2026", meta=m)
check("a plain re-import replaces its own earlier landing and says so without inventing an 'older key'",
      len(ledger(db)) == 5 and m["replaced"][0]["rows"] == 1 and m["replaced_note"].startswith("replaced 1 rows") and "older key" not in m["replaced_note"])
# the MA refresh route lands through the same lander (origin ma_sync): pinned by wiring + the ma_sync proof
src_sync = inspect.getsource(R.commission_ledger_ma_sync)
check("the MA refresh lands THROUGH _ledger_land_rows with origin ma_sync and no insert of its own (harness_ledger_ma_sync proves the rows)",
      "_ledger_land_rows(" in src_sync and "origin=ledger_ma_sync.ORIGIN_SYNC" in src_sync and ".insert(" not in src_sync)
# the origin scope still holds: a file landing never wipes a synced landing of the same statement × period
db = fresh_db(landing_rows(ORG, LONG, "July 2026", 3, 30.0, origin=LMS.ORIGIN_SYNC, created_at="2026-09-01T00:00:00Z"))
m = {}
R._ledger_land_rows(db, ORG, landing_rows(ORG, CODE, "July 2026", 2, 2.0), CODE, "July 2026", meta=m)
check("a file landing wipes only FILE landings of its family × period — the synced rows survive (and the two origins are now two landings the readers refuse)",
      len(ledger(db)) == 5 and m["replaced"] == []
      and R.commission_ledger_summary(source_report=CODE, period="July 2026", org_id=ORG).get("refused") is True
      and money(R.commission_ledger_summary(source_report=CODE, period="July 2026", origin="file", org_id=ORG)["payout_total"]) == 2.0)

# THE OLDER WIZARD'S ROUTE end to end: /commission-ledger/import with the bare code and 'Aug 2026'
db = fresh_db()
hdrs = {d["target_field"]: d["source_header"] for d in CM.default_mapping(CL.mapping_report_key(""))}
cols = ["account_id", "account_name", "store", "rep_user", "order_number", "order_type", "product_name", "trans_date", "due_date", "raw_amount"]
lines = [",".join(hdrs[c] for c in cols)]
for i, amt in enumerate((-25.0, -25.0, 10.0)):
    lines.append(",".join(["A%d" % i, "Acct", "10 Main St", "alice", "O%d" % i, "Postpaid Order", "TBV MONTH 1 New Activation Commission",
                           "2026-08-0%d" % (i + 1), "2026-08-0%d" % (i + 1), f"{amt:.2f}"]))
csv = ("\n".join(lines) + "\n").encode("utf-8")
imp = run(R.commission_ledger_import(file=FakeUpload(csv, filename="statement.csv"), source_report=CODE, period="Aug 2026",
                                     carrier_id="", statement_type="", org_id=ORG))
check("/commission-ledger/import with source_report=<bare code>, period='Aug 2026' → stored as <code>__commission_statement / 'August 2026'; the payload says what was requested and what was stored",
      imp["saved"] == 3 and imp["source_report"] == LONG and imp["period"] == "August 2026"
      and imp["requested"] == {"source_report": CODE, "period": "Aug 2026"} and imp["replaced"] == []
      and all(r["source_report"] == LONG and r["period"] == "August 2026" for r in ledger(db)), (imp.get("source_report"), imp.get("period")))
imp2 = run(R.commission_ledger_import(file=FakeUpload(csv, filename="statement.csv"), source_report=CODE, period="2026-08",
                                      carrier_id="", statement_type="", org_id=ORG))
check("…and importing the same statement again under the numeric spelling REPLACES it (3 rows, one landing, the note says so)",
      imp2["saved"] == 3 and len(ledger(db)) == 3 and imp2["replaced"][0]["rows"] == 3 and imp2["replaced_note"].startswith("replaced 3 rows")
      and not R.commission_ledger_summary(source_report=CODE, period="August 2026", org_id=ORG).get("refused"))
check("the intake's 3.9 commit lands through the same lander and records what it replaced (wiring; the intake proofs land the rows)",
      "_ledger_land_rows(" in inspect.getsource(R._intake_commit_commission) and "meta=land" in inspect.getsource(R._intake_commit_commission)
      and '"replaced_note": land.get("replaced_note")' in inspect.getsource(R._intake_commit_commission))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. THE P&L — a conflicted statement × period books NOTHING and says why; a clean one books")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
buckets = CL.builtin_buckets()
sections = COA.PL_SECTION
clean = live_rows("july_good")
lb = LP.ledger_bookings(clean, buckets, sections)
check("one landing → the statement books to its registry line (carrier_comm), nothing held",
      lb["bookings"] and lb["by_line"].get("carrier_comm") == 165997.59 and lb["conflicts"] == [] and lb["held_lines"] == 0, lb["by_line"])
lb2 = LP.ledger_bookings(live_rows("july_bare", "july_good"), buckets, sections)
check("two landings → NOTHING booked from that statement; the money is reported under `unbooked` with the ledger's sentence; the evidence rides along",
      lb2["bookings"] == [] and lb2["by_line"] == {} and lb2["held_lines"] == 1946
      and lb2["unbooked"][0]["reason"].endswith(SENTENCE_JULY) and lb2["unbooked"][0]["amount"] == money(14411.40 + 165997.59)
      and lb2["conflicts"][0]["sentence"] == SENTENCE_JULY, lb2["unbooked"])
other = landing_rows(ORG, "othercarrier__commission_statement", "July 2026", 2, 200.0, created_at="2026-09-01T00:00:00Z")
lb3 = LP.ledger_bookings(live_rows("july_bare", "july_good") + other, buckets, sections)
check("a SECOND statement in the same period still books while the conflicted one is held (per statement, not per period)",
      lb3["by_line"].get("carrier_comm") == 200.0 and lb3["held_lines"] == 1946 and len(lb3["conflicts"]) == 1)
dv = LP.divergence({"carrier_comm": 620.0}, {}, LP.SOURCE_LEDGER, {"carrier_comm"}, configured="ledger",
                   unbooked=lb2["unbooked"], conflicts=lb2["conflicts"], ledger_line_count=1946)
check("the P&L line's words say nothing was booked from the ledger, name the sentence, and keep the feed booking switched off",
      dv["carrier_comm"]["ledger"] == 0.0 and dv["carrier_comm"]["suppressed"] == 620.0 and SENTENCE_JULY in dv["carrier_comm"]["words"]
      and "stays switched off" in dv["carrier_comm"]["words"] and dv["carrier_comm"]["landing_conflicts"] == [SENTENCE_JULY], dv["carrier_comm"]["words"])
dv0 = LP.divergence({"carrier_comm": 620.0}, {"carrier_comm": 165997.59}, LP.SOURCE_LEDGER, {"carrier_comm"}, configured="ledger", conflicts=[])
check("with no conflict the words are exactly the #273 words (byte-identical divergence payload; `landing_conflicts` is [])",
      dv0["carrier_comm"]["words"].startswith("Booked from the Commission Ledger ($165,997.59)") and dv0["carrier_comm"]["landing_conflicts"] == [])
# END TO END through the REAL coa.build_inputs over the P&L proof's client, its statement landed TWICE (two period spellings)
t = PLH.tables({"pl_commission_source": "ledger"})
dup = [dict(r, period="2026-07") for r in t["commission_ledger"] if r["org_id"] == PLH.ORG and r["period"] == PLH.P]
t2 = copy.deepcopy(t)
t2["commission_ledger"] = t["commission_ledger"] + dup
L_clean = COA.build_inputs(PLH.Client(t), PLH.ORG, PLH.P)
L_dup = COA.build_inputs(PLH.Client(t2), PLH.ORG, PLH.P)
cc_clean, cc_dup = PLH.total(L_clean, "carrier_comm"), PLH.total(L_dup, "carrier_comm")
check("coa.build_inputs under 'ledger': the clean statement books carrier_comm; the same statement under TWO period spellings books 0 there (feeds suppressed, ledger refused) and says why",
      cc_clean > 0 and cc_dup == 0.0 and SENTENCE_JULY.split(" holds")[0] in (L_dup["carrier_comm"].get("commission_source") or {}).get("words", "")
      and "holds 2 landings" in (L_dup["carrier_comm"].get("commission_source") or {}).get("words", "")
      and (L_dup["carrier_comm"]["commission_source"]["landing_conflicts"]), (cc_clean, cc_dup, (L_dup["carrier_comm"].get("commission_source") or {}).get("words")))
check("…and every line the ledger does not cover is byte-identical between the two runs (nothing else on the P&L moves)",
      all(PLH.lines_of(L_clean)[k] == PLH.lines_of(L_dup)[k] for k in PLH.lines_of(L_clean) if k not in LP.covered_lines(PLH.BUCKETS, sections)))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. COMPATIBILITY PINS — the house org and the master-agent tenant read byte-identically")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
house_rows = (landing_rows(HOUSE, "ma_daily_tx", "June 2026", 4, 444.0, rep="amir", created_at="2026-07-12T14:03:00Z")
              + landing_rows(HOUSE, "ma_daily_tx", "June 2026", 2, 20.0, category="spiff", rep="bea", created_at="2026-07-12T14:03:00Z"))
db = fresh_db(house_rows, org=HOUSE)
direct = CL.summarize(house_rows)
s = R.commission_ledger_summary(source_report="ma_daily_tx", period="June 2026", org_id=HOUSE)
check("the house org's bare 'ma_daily_tx' rows read as before: one landing, the same totals as summarize over the rows, nothing refused",
      s.get("refused") is not True and s["payout_total"] == direct["payout_total"] == 464.0 and s["categories"]["spiff"]["total"] == 20.0
      and s["line_count"] == 6 and s["landing_conflict"] is None)
br = R.commission_ledger_by_rep(source_report="ma_daily_tx", period="2026-06", org_id=HOUSE)
check("by-rep under the numeric spelling: the same reps and figures the old _pvariants read produced",
      {r["rep"]: r["ledger_payout"] for r in br["reps"]} == {"amir": 444.0, "bea": 20.0} and br["totals"]["ledger_payout"] == 464.0)
tm = {t["key"]: t for t in CL.list_templates(db, HOUSE)}
check("the picker lists 'ma_daily_tx' ONCE with its 6 lines — never a second 'ma_daily_tx__commission_statement' entry",
      tm["ma_daily_tx"]["ledger_lines"] == 6 and "ma_daily_tx__commission_statement" not in tm and tm["ma_daily_tx"]["spellings"] == ["ma_daily_tx"])
check("the house template's rules still resolve to the 071 seed's built-ins through the family (rules_source builtin_default)",
      CL.load_rules_meta(db, HOUSE, "ma_daily_tx")[1] == CL.RULES_BUILTIN == CL.load_rules_meta(db, HOUSE, "ma_daily_tx__commission_statement")[1])
m = {}
R._ledger_land_rows(db, HOUSE, landing_rows(HOUSE, "ma_daily_tx", "June 2026", 5, 555.0, rep="amir"), "ma_daily_tx", "June 2026", meta=m)
s2 = R.commission_ledger_summary(source_report="ma_daily_tx", period="June 2026", org_id=HOUSE)
tm2 = {t["key"]: t for t in CL.list_templates(db, HOUSE)}
check("the NEXT house landing stores the canonical key, REPLACES the bare-key rows (6 → 5), reads through the same picker key with one landing",
      m["source_report"] == "ma_daily_tx__commission_statement" and m["replaced"][0]["rows"] == 6 and s2["payout_total"] == 555.0
      and s2["line_count"] == 5 and s2["landing_conflict"] is None and tm2["ma_daily_tx"]["ledger_lines"] == 5
      and tm2["ma_daily_tx"]["spellings"] == ["ma_daily_tx__commission_statement"] and "ma_daily_tx__commission_statement" not in tm2)
# the master-agent tenant's shape: two TEMPLATES (ma_commission + ma_daily_tx) in one period are two identities
ma_rows = (landing_rows(TENANT2, "ma_commission", "June 2026", 4, 40.0, created_at="2026-07-01T00:00:00Z")
           + landing_rows(TENANT2, "ma_daily_tx", "June 2026", 4, 400.0, created_at="2026-07-01T00:00:00Z"))
db = fresh_db(ma_rows, org=TENANT2)
a, b = (R.commission_ledger_summary(source_report=k, period="June 2026", org_id=TENANT2) for k in ("ma_commission", "ma_daily_tx"))
check("two templates in one period are two statements — each sums its own, neither refused, and the other's rows never bleed in",
      a["payout_total"] == 40.0 and b["payout_total"] == 400.0 and not a.get("refused") and not b.get("refused") and a["line_count"] == b["line_count"] == 4)
check("the per-rep statement buckets for the master-agent tenant read as before",
      R._statement_buckets(db, TENANT2, "June 2026", "alice", source_report="ma_daily_tx") is not None)
# a carrier onboarded ONLY through the intake (the good state): rules under the long key are found from either spelling
db = fresh_db(live_rows("july_good", "aug_good"))
db.seed("commission_category_map", [{"org_id": ORG, "source_report": LONG, "match_field": "product_name", "match_op": "contains",
                                     "pattern": "New Activation", "category": "commission", "sign_rule": "negative_only", "priority": 10}])
check("the intake-written rules (under the long key) are found by the older wizard's bare key and by the long key alike (rules_source tenant)",
      CL.load_rules_meta(db, ORG, CODE)[1] == CL.RULES_TENANT == CL.load_rules_meta(db, ORG, LONG)[1]
      and R.get_commission_category_map(source_report=CODE, org_id=ORG)["rules"][0]["source_report"] == LONG)
tm = {t["key"]: t for t in CL.list_templates(db, ORG)}
check("a tenant carrier's picker entry is its bare code with the long key's lines and rules folded in — one entry, not two",
      tm[CODE]["ledger_lines"] == 973 + 521 and tm[CODE]["rule_count"] == 1 and LONG not in tm and tm[CODE]["spellings"] == [LONG])
check("the older wizard's `source_report` query parameter accepts either spelling and reads the same family (the page never re-spells)",
      R.commission_ledger_summary(source_report=LONG, period="July 2026", org_id=ORG)["payout_total"]
      == R.commission_ledger_summary(source_report=CODE, period="July 2026", org_id=ORG)["payout_total"] == 165997.59)
check("the intake's second-carrier / residual keys are unchanged by-name (statement-type mapping proof pins them end to end)",
      OI.source_report_key("northwind", "residual statement") == "northwind__residual_statement" and OI.instance_key("commission", "c1", "residual statement") == "commission:c1:residual_statement")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("H. NEGATIVE CONTROLS — a sibling, the guard bypassed, the lock scanner: each goes RED")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def sibling_landings(rows):
    """A derivation that keys a landing by the RAW source_report — what every reader did before."""
    g = {}
    for r in rows:
        g.setdefault((r["source_report"], CL.canonical_period(r["period"])), set()).add((r.get("origin"), r["period"]))
    return {k: len(v) for k, v in g.items()}
sib = sibling_landings(live_rows("july_bare", "july_good"))
check("a sibling keyed by the raw key sees July as TWO statements with ONE landing each — no conflict, the double summed silently → RED",
      all(n == 1 for n in sib.values()) and len(sib) == 2 and CL.landing_conflicts(live_rows("july_bare", "july_good")))
check("the guard bypassed (plain summarize instead of the guarded one) reports the DOUBLE: 180,408.99 for a 165,997.59 statement → RED",
      money(CL.summarize(live_rows("july_bare", "july_good"))["payout_total"]) == money(14411.40 + 165997.59)
      and R._ledger_guarded_summary(live_rows("july_bare", "july_good"))["payout_total"] == 0)
check("a reader that filters the period by its literal misses the '2026-07' spelling the other route stored → RED (the class of the orphans)",
      len([r for r in live_rows("july_good") + [dict(x, period="2026-07") for x in live_rows("july_bare")] if r["period"] == "July 2026"]) == 973
      and len([r for r in live_rows("july_good") + [dict(x, period="2026-07") for x in live_rows("july_bare")] if r["period"] in CL.ledger_period_keys("July 2026")]) == 1946)
syn = 'def f(client, org_id, period):\n    q = client.schema("commcalc").table("commission_ledger").select("*").eq("org_id", org_id).eq("period", period)\n    return q.execute().data\n'
check("the lock scanner goes RED on a ledger chain filtered by a literal period, GREEN through _ledger_query",
      bool(LOCK.literal_ledger_filters("modules/x.py", syn))
      and not LOCK.literal_ledger_filters("modules/x.py", 'def f(c, o):\n    return _ledger_query(c, o, "k", "p").eq("category", "x").execute().data\n'))
b2 = {"modules/commcalc/other.py": 'def land(c, rows):\n    c.schema("commcalc").table("commission_ledger").insert(rows).execute()\n'}
check("the lock goes RED on a second lander and on a second key composition",
      bool(LOCK.inserts_outside_lander(b2)) and bool(LOCK.second_compositions({"modules/commcalc/other.py": 'k = f"{b}__{t}"\n'}, LOCK.ALLOW)))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("I. WIRED, REGISTERED, RULE TWO, CI")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
BANNED = ("verizon", "vzw", "fios", "agentssoid", "luxelink", "boost mobile")
for mod in (CL, PD, LP, OI):
    src = io.open(mod.__file__, encoding="utf-8").read().lower()
    check(os.path.basename(mod.__file__) + " names no carrier (RULE TWO)", not [b for b in BANNED if b in src])
rsrc = "\n".join(inspect.getsource(getattr(R, f)) for f in ("_ledger_query", "_ledger_landings_present", "_ledger_delete_scoped", "_ledger_land_rows",
                                                            "_ledger_guarded_summary", "_ledger_landing_slice", "commission_ledger_landings",
                                                            "commission_ledger_landing_retire", "commission_ledger_summary", "commission_ledger_by_rep")).lower()
check("the router's identity / landing functions name no carrier (RULE TWO)", not [b for b in BANNED if b in rsrc])
check("every summing reader is wired to the guard and every reader to the one query (the lock's own checks, run here)",
      LOCK.main.__code__ is not None and "_ledger_guarded_summary(" in inspect.getsource(R.commission_ledger_summary)
      and "landing_conflicts(" in inspect.getsource(R.commission_ledger_by_rep) and "_cl.landing_conflicts(" in inspect.getsource(LP.ledger_bookings))
check("the endpoints are mounted", all(p in [rt.path for rt in R.router.routes] for p in ("/commcalc/commission-ledger/landings", "/commcalc/commission-ledger/landings/retire")))
idx = io.open(os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md"), encoding="utf-8").read()
check("registered in the index: §30.15, the endpoints in §17, the function in §16, the guard in §18",
      "### 30.15" in idx and "/commission-ledger/landings/retire" in idx and "ledger_source_report" in idx and "landing_conflicts" in idx)
design = io.open(os.path.join(ROOT, "docs", "ONBOARDING_FLOW_DESIGN.md"), encoding="utf-8").read()
check("registered in the design doc", "ledger_source_report" in design and "landings_for" in design)
wf = io.open(os.path.join(ROOT, ".github", "workflows", "carrier-vocab-guard.yml"), encoding="utf-8").read()
check("the lock runs in CI", "python3 harness_ledger_identity_lock.py" in wf)
check("no migration was needed: the landing is derived from columns every row already carries (source_report, period, origin, created_at)",
      not [f for f in os.listdir(os.path.join(ROOT, "database", "migrations")) if "landing" in f.lower() and "ledger" in f.lower()])

print("\n══ ledger statement identity: %d passed, %d failed ══" % (_pass, _fail))
if _failures:
    print("   failed: " + "; ".join(_failures))
sys.exit(1 if _fail else 0)
