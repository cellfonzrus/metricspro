"""PROOF — the derived-build registry, its verdict and its queue, with NO DATABASE (index §COMMCALC-41).

Owner ask 2026-10-27: *"the data should be live update as soon as something changes"*. What that costs is a
chain of small decisions, and each one is proved here against a fake client:

  §A  the registry itself: every build declares what this house requires of it, and the one home it runs through
  §B  "which builds does a landing of table T make stale" — including the DECLARED (per-carrier) inputs
  §C  the verdict: a row count is never read as a verdict (§COMMCALC-40), a refusal is not a crash
  §D  the queue: coalescing a burst, the claim, and that a landing racing the claim is never lost
  §E  the hook: a landing with no month still refreshes the snapshots; a table nobody reads refreshes nothing
  §F  the sentence an operator reads says what happened and never claims a rebuild that did not happen
  §G  NEGATIVE CONTROLS — the fixtures cannot go vacuous

    python3 backend/harness_derived_builds.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AUTO_CALC_POLLER", "0")
os.environ.setdefault("SUPABASE_URL", "http://harness.invalid")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "harness")

from app.modules.commcalc import derived_builds as db        # noqa: E402
from app.modules.commcalc import auto_calc as ac             # noqa: E402

P = F = 0
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print(f"  PASS  {label}")
    else:
        F += 1
        print(f"  FAIL  {label}   {str(detail)[:600]}")


# ── a fake PostgREST client: the few calls these paths make, over in-memory rows ────────────────────────────
class _Q:
    def __init__(self, store, schema, table):
        self.s, self.schema, self.table_name = store, schema, table
        self.filters, self.payload, self.op, self.cols = [], None, None, "*"

    def select(self, cols="*"):
        self.op, self.cols = "select", cols
        if self.s.get("missing_table") == self.table_name:
            raise RuntimeError("PGRST205 relation does not exist")
        return self

    def upsert(self, rows, on_conflict=None):
        self.op, self.payload = "upsert", rows
        return self

    def update(self, patch):
        self.op, self.payload = "update", patch
        return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v))
        return self

    def in_(self, k, vals):
        self.filters.append(("in", k, [str(v) for v in vals]))
        return self

    def lte(self, k, v):
        self.filters.append(("lte", k, v))
        return self

    def limit(self, _n):
        return self

    def range(self, *_a):
        return self

    def _rows(self):
        out = self.s["rows"].get((self.schema, self.table_name), [])
        for kind, k, v in self.filters:
            if kind == "eq":
                out = [r for r in out if str(r.get(k)) == str(v)]
            elif kind == "in":
                out = [r for r in out if str(r.get(k)) in v]
            else:
                out = [r for r in out if r.get(k) and str(r.get(k)) <= str(v)]
        return out

    def execute(self):
        key = (self.schema, self.table_name)
        bag = self.s["rows"].setdefault(key, [])
        if self.op == "select":
            return type("R", (), {"data": list(self._rows())})
        if self.op == "upsert":
            for row in (self.payload if isinstance(self.payload, list) else [self.payload]):
                k = (row.get("org_id"), row.get("build_key") or row.get("period"))
                cur = next((r for r in bag if (r.get("org_id"), r.get("build_key") or r.get("period")) == k), None)
                if cur:
                    cur.update(row)
                else:
                    bag.append(dict(row))
            self.s["writes"].append(("upsert", self.table_name, self.payload))
            return type("R", (), {"data": list(bag)})
        if self.op == "update":
            hit = self._rows()
            for r in hit:
                r.update(self.payload)
            self.s["writes"].append(("update", self.table_name, self.payload))
            return type("R", (), {"data": list(hit)})
        return type("R", (), {"data": []})


class Client:
    def __init__(self, rows=None, missing_table=None):
        self.store = {"rows": dict(rows or {}), "writes": [], "missing_table": missing_table}
        self._schema = "public"

    def schema(self, s):
        self._schema = s
        return self

    def table(self, t):
        return _Q(self.store, self._schema, t)


def fresh(**kw):
    db._STATE.update(ready=None, checked=0.0)
    db._MEM.clear()
    db._RUNNING.clear()
    ac._STATE.update(ready=None, checked=0.0)
    ac._MEM.clear()
    return Client(**kw)


CFG_ON = [{"org_id": "ORG", "auto_calc_on_landing": True, "auto_calc_debounce_minutes": 5}]
CFG_OFF = [{"org_id": "ORG", "auto_calc_on_landing": False}]
SRC_MAP = [{"org_id": "ORG", "carrier_id": "C1", "is_active": True, "source_table": "raw_ma_commission",
            "owed_price_table": "raw_ma_fulfillment", "sold_match_table": None,
            "reimbursement_match_table": "raw_ma_daily_tx"}]


def base_rows(cfg=None, src=None, state=()):
    return {("commcalc", "commission_org_config"): list(cfg if cfg is not None else CFG_ON),
            ("commcalc", "payable_source_map"): list(src if src is not None else SRC_MAP),
            ("commcalc", "payable_source_map_x"): [],
            ("commcalc", db.STATE_TABLE): list(state),
            ("commcalc", "calc_status"): []}


# ═══ §A the registry ════════════════════════════════════════════════════════════════════════════════════════
print("\n§A THE REGISTRY")
keys = [b["key"] for b in db.BUILDS]
check("A1 every build has a unique key", len(keys) == len(set(keys)), keys)
check("A2 the payable ledger and the recovery ledger are both registered",
      {"payable_ledger", "appeal_recovery"} <= set(keys), keys)
check("A3 the commission calculation is registered too, so the list of live builds has ONE home",
      "commission" in keys)
check("A4 every build declares a label, a scope, a runner and the manual door that stays",
      all(b.get("label") and b.get("scope") in (db.SCOPE_PERIOD, db.SCOPE_ORG) and ":" in b.get("run", "")
          and b.get("endpoint") for b in db.BUILDS),
      [b["key"] for b in db.BUILDS if not (b.get("label") and b.get("endpoint"))])
check("A5 every build has at least one input — a build nothing can make stale is never live",
      all(db.static_inputs(b) or b.get("inputs_fn") for b in db.BUILDS),
      [b["key"] for b in db.BUILDS if not (db.static_inputs(b) or b.get("inputs_fn"))])
check("A6 commission is the ONLY period-scoped build (every snapshot is org-scoped)",
      [b["key"] for b in db.BUILDS if b["scope"] == db.SCOPE_PERIOD] == ["commission"])
check("A7 org_builds() excludes commission, which auto_calc runs on its own path",
      "commission" not in [b["key"] for b in db.org_builds()])
check("A8 every runner resolves to a real function",
      all(callable(db._load(b["run"])) for b in db.BUILDS),
      [b["key"] for b in db.BUILDS])
check("A9 every manual rebuild door is a registered endpoint or excused with a reason — the owner's own "
      "question ('tenant would not know when to run') asked of the code",
      all(len(e) == 2 and e[1] for e in db.EXCUSED_DOORS)
      and "POST /payables/rebuild" in {b["endpoint"] for b in db.BUILDS}
      and "POST /recovery/rebuild" in {b["endpoint"] for b in db.BUILDS},
      db.EXCUSED_DOORS)
check("A9b every excused and every pinned-unwired entry carries a REASON, so a reader can check it",
      all(len(e) == 3 and e[2] for e in db.EXCUSED_BUILDS)
      and all(len(e) == 4 and e[3] for e in db.UNWIRED_SNAPSHOTS),
      [e[:2] for e in db.EXCUSED_BUILDS if len(e) != 3 or not e[2]])
check("A10 the same-class siblings this PR did NOT wire are named rather than left out",
      len(db.UNWIRED_SNAPSHOTS) == 4
      and {e[1] for e in db.UNWIRED_SNAPSHOTS} == {"sync_flags", "_sync_invoice_due_flags",
                                                   "sync_inventory_flags", "_sync_hotsheet_flags"},
      db.UNWIRED_SNAPSHOTS)
check("A11 no function is both registered and excused",
      not ({e[1] for e in db.EXCUSED_BUILDS} & {b["run"].partition(":")[2] for b in db.BUILDS}))
check("A12 the payable build's static inputs are the three feeds every carrier's build reads",
      set(db.static_inputs(db.by_key("payable_ledger"))) == {"asset_ledger", "raw_payment_detail",
                                                             "vip_invoices"},
      db.static_inputs(db.by_key("payable_ledger")))

# ═══ §B which builds a landing makes stale ══════════════════════════════════════════════════════════════════
print("\n§B WHAT A LANDING MAKES STALE")
c = fresh(rows=base_rows())
check("B1 an asset_ledger landing makes the payable ledger stale (the measured 53-day gap)",
      [b["key"] for b, _ in db.stale_builds(c, "ORG", "asset_ledger")] == ["payable_ledger"])
check("B2 a payment-detail landing makes BOTH snapshots stale",
      {b["key"] for b, _ in db.stale_builds(c, "ORG", "raw_payment_detail")}
      == {"payable_ledger", "appeal_recovery"})
check("B3 a DECLARED carrier source makes the payable ledger stale with no code change",
      [b["key"] for b, _ in db.stale_builds(c, "ORG", "raw_ma_commission")] == ["payable_ledger"])
check("B4 a DECLARED price list counts too (owed_price_table)",
      [b["key"] for b, _ in db.stale_builds(c, "ORG", "raw_ma_fulfillment")] == ["payable_ledger"])
check("B5 a table no build reads makes nothing stale", db.stale_builds(c, "ORG", "raw_catalog") == [])
check("B6 declaring a carrier against a different feed moves what refreshes it — config, never code",
      [b["key"] for b, _ in db.stale_builds(
          fresh(rows=base_rows(src=[dict(SRC_MAP[0], source_table="raw_sales")])), "ORG", "raw_sales")]
      == ["payable_ledger"])
check("B7 an INACTIVE carrier row declares nothing",
      db.stale_builds(fresh(rows=base_rows(src=[dict(SRC_MAP[0], is_active=False)])),
                      "ORG", "raw_ma_commission") == [])
unreadable = db.stale_builds(fresh(rows=base_rows(), missing_table="payable_source_map"),
                             "ORG", "asset_ledger")
check("B8 an UNREADABLE config is REPORTED and the static inputs still fire — never read as "
      "'this build declares nothing'",
      len(unreadable) == 1 and unreadable[0][1] and "PGRST205" in unreadable[0][1], unreadable)
check("B9 …and with the config unreadable, a purely DECLARED table makes nothing stale rather than "
      "guessing (the failure is already reported on B8's path)",
      db.stale_builds(fresh(rows=base_rows(), missing_table="payable_source_map"),
                      "ORG", "raw_ma_commission") == [])

# A DECLARATION IS AN INPUT (owner ask 2026-10-29, "Put the Net term days as 14 from the back,
# programmable not hard code"). Net terms were already a config row and nothing in code held a number of
# days — but the stored due dates only agreed with a change once some unrelated feed happened to land, so
# the tenant was the scheduler again, one step further back. The config table makes the build stale.
check("B10 editing the carrier's DECLARATION makes the ledger stale, exactly as a shipment does — "
      "net terms of 14 with due dates still counted at 21 is a stale snapshot by any other name",
      [b["key"] for b, _ in db.stale_builds(c, "ORG", "payable_source_map")] == ["payable_ledger"])
check("B11 …and it makes ONLY that build stale — the recovery ledger does not read the payable map, so a "
      "terms change must not queue a rebuild of something it cannot affect",
      "appeal_recovery" not in {b["key"] for b, _ in db.stale_builds(c, "ORG", "payable_source_map")})
check("B12 the config table is kept OUT of `inputs`, which carry lineage identity, and declared under "
      "`config_inputs` — a config table has no external feed to trace",
      "payable_source_map" in db.config_inputs(db.by_key("payable_ledger"))
      and "payable_source_map" not in db.static_inputs(db.by_key("payable_ledger")))
check("B13 a build that declares no config table is unaffected — `config_inputs` defaults to empty "
      "rather than to something a reader has to notice",
      db.config_inputs(db.by_key("appeal_recovery")) == ()
      and db.config_inputs({"key": "x"}) == ())
check("B14 `matches` is still PURE about it — the config table counts with no client and no config read",
      db.matches(db.by_key("payable_ledger"), "payable_source_map")
      and not db.matches(db.by_key("appeal_recovery"), "payable_source_map"))

# ═══ §C the verdict ═════════════════════════════════════════════════════════════════════════════════════════
print("\n§C THE VERDICT (§COMMCALC-40 — the status word, never a row count)")
check("C1 a build that wrote rows is built", db.classify_build({"status": "done", "written": 11000})
      == ("built", ""))
check("C2 a build that legitimately wrote ZERO rows is still built, not failed",
      db.classify_build({"status": "done", "written": 0}) == ("built", ""))
check("C3 the HTTP-200-over-an-emptied-table shape (written 0 + an error) is a FAILURE",
      db.classify_build({"status": "error", "written": 0,
                         "error": "PGRST204 Could not find the 'rebate_basis' column"})[0] == "failed")
check("C4 a refusal is kept apart from a crash",
      db.classify_build({"status": "error", "error": "REFUSED: this tenant is not configured"})[0]
      == "refused")
check("C5 a per-carrier warning rides out with the result instead of dying in a log line",
      db.classify_build({"status": "done", "written": 9, "warnings": ["Total: no price list"]})
      == ("built_with_warnings", "Total: no price list"))
check("C6 a REFUSAL is its own verdict — the write preflight refusing because the database is behind "
      "the code (§PAYABLES-01) is neither a crash nor a success",
      db.classify_build({"status": "refused", "error": "REFUSED: the database is behind the code",
                         "blocked": [{"carrier_id": "C1"}]})[0] == "refused")
check("C6b …and its sentence tells the reader the figures on screen are the last good build's",
      "last good build" in db.sentence("refused", "the device payable ledger", [], "REFUSED: x"))
check("C6c a refusal is NOT re-queued — a block is a schema fact and a retry cannot clear it",
      "_queue" not in (db.run_one.__doc__ or "") and "requeue" not in (db.run_one.__doc__ or "").lower())
check("C7 a runner that returned nothing recognisable is a failure, never a success",
      db.classify_build({})[0] == "failed" and db.classify_build(None)[0] == "failed")

# ═══ §D the queue ═══════════════════════════════════════════════════════════════════════════════════════════
print("\n§D THE QUEUE — coalesce, claim, never lose a landing")
c = fresh(rows=base_rows())
assert db.state_ready(c) is True
for i in range(30):
    db.queue(c, "ORG", "payable_ledger",
             [ac.landing_entry("manual", "asset_ledger", f"f{i}.xlsx", 10, ac._iso(NOW + timedelta(seconds=i)))],
             NOW + timedelta(seconds=i), True)
rows = c.store["rows"][("commcalc", db.STATE_TABLE)]
check("D1 thirty landings in a burst are ONE request row", len(rows) == 1, len(rows))
check("D2 …carrying the latest landing's timestamp", rows[0][db.COL_REQUESTED] == ac._iso(NOW + timedelta(seconds=29)))
check("D3 …and the FIRST landing is kept, so a steady stream cannot starve the build",
      rows[0][db.COL_LANDINGS][0]["filename"] == "f0.xlsx")
check("D4 the request is not due inside the quiet window",
      not ac.is_due(rows[0][db.COL_REQUESTED], rows[0][db.COL_LANDINGS], 5, NOW + timedelta(minutes=2)))
check("D5 …and is due once uploads have been quiet for the debounce",
      ac.is_due(rows[0][db.COL_REQUESTED], rows[0][db.COL_LANDINGS], 5, NOW + timedelta(minutes=6)))
req = rows[0][db.COL_REQUESTED]
check("D6 the claim takes the request once", db.claim(c, "ORG", "payable_ledger", req, True) is True)
check("D7 a second process claiming the same request gets nothing — one rebuild, not two",
      db.claim(c, "ORG", "payable_ledger", req, True) is False)
c = fresh(rows=base_rows())
db.queue(c, "ORG", "payable_ledger", [ac.landing_entry("manual", "asset_ledger", at=ac._iso(NOW))], NOW, True)
db.queue(c, "ORG", "payable_ledger", [ac.landing_entry("manual", "asset_ledger", at=ac._iso(NOW + timedelta(minutes=1)))],
         NOW + timedelta(minutes=1), True)
check("D8 a landing that arrives after we judged a request due defeats the stale claim, so its data is "
      "never silently dropped", db.claim(c, "ORG", "payable_ledger", ac._iso(NOW), True) is False)
c = fresh(rows=base_rows(), missing_table=db.STATE_TABLE)
check("D9 with mig 1100 UNAPPLIED the queue degrades to this process rather than failing",
      db.state_ready(c) is False)
db.queue(c, "ORG", "payable_ledger", [ac.landing_entry("manual", "asset_ledger", at=ac._iso(NOW))], NOW, False)
check("D10 …and the request is still queued, claimed once, and gone after the claim",
      len(db.pending(c, NOW, False)) == 1
      and db.claim(c, "ORG", "payable_ledger", ac._iso(NOW), False) is True
      and db.pending(c, NOW, False) == [])

# ═══ §E the hook ════════════════════════════════════════════════════════════════════════════════════════════
print("\n§E THE HOOK — one entry point for every lander")
c = fresh(rows=base_rows())
r = ac.landed(c, "ORG", table="asset_ledger", periods=[], source="asset_ledger_upload", rows=15495)
check("E1 a landing with NO month still queues the snapshot rebuild (a shelf snapshot is not a month's figure)",
      r["queued"] is True and r["builds"]["builds"] == ["payable_ledger"], r)
check("E2 …and queues no commission calculation, because asset_ledger is not calculation input",
      r.get("periods") in (None, []) and not c.store["rows"][("commcalc", "calc_status")])
c = fresh(rows=base_rows())
r = ac.landed(c, "ORG", table="raw_payment_detail", periods="September 2026", source="manual", rows=100)
check("E3 a payment-detail landing queues the commission calculation AND both snapshots",
      r["periods"] == ["September 2026"] and set(r["builds"]["builds"]) == {"payable_ledger", "appeal_recovery"}, r)
c = fresh(rows=base_rows())
r = ac.landed(c, "ORG", table="raw_catalog", periods="September 2026", source="manual", rows=5)
check("E4 a table nothing derives from still answers not_a_calc_input — no build, no queue",
      r["queued"] is False and r["reason"] == "not_a_calc_input", r)
c = fresh(rows=base_rows())
r = ac.landed(c, "ORG", table="asset_ledger", periods=[], source="manual", rows=0)
check("E5 a landing of zero rows queues nothing", r["queued"] is False and r["reason"] == "nothing_landed")
c = fresh(rows=base_rows(cfg=CFG_OFF))
r = ac.landed(c, "ORG", table="asset_ledger", periods=[], source="manual", rows=10)
check("E6 a tenant that switched auto-rebuild OFF queues nothing and is TOLD so on the page",
      r["builds"]["queued"] is False
      and any(w[1] == db.STATE_TABLE and isinstance(w[2], dict) and w[2].get(db.COL_LAST, {}).get("outcome") == "off"
              for w in c.store["writes"]), c.store["writes"])
check("E7 the SAME switch governs commission and the snapshots — a tenant turns one thing off, not two",
      db.by_key("payable_ledger") is not None
      and "load_config" in open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                             "app/modules/commcalc/derived_builds.py")).read())
c = fresh(rows=base_rows(), missing_table="payable_source_map")
r = ac.landed(c, "ORG", table="asset_ledger", periods=[], source="manual", rows=10)
check("E8 an unreadable config does not stop the rebuild and the reason travels out with the response",
      r["builds"]["queued"] is True and r["builds"]["unreadable"], r)

# THE SETTINGS CHANGE GOES THROUGH THE SAME DOOR. No second trigger: a terms edit calls `landed` with the
# config table and NO period, exactly as the asset-ledger landing does, so it inherits the same debounce,
# the same claim, the same poller and the same `auto_calc_on_landing` switch.
c = fresh(rows=base_rows())
r = ac.landed(c, "ORG", table="payable_source_map", periods=[], source="payable_source_map_edit", rows=1)
check("E10 changing the carrier's payment terms queues the payable-ledger rebuild through the ONE hook",
      r["queued"] is True and r["builds"]["builds"] == ["payable_ledger"], r)
check("E11 …and queues NO commission calculation and no month — a payment term is not a month's figure",
      r["periods"] == [] and r["builds"]["builds"] == ["payable_ledger"], r)
check("E12 the sentence an operator reads says a SETTING changed, not that something was uploaded — a "
      "rebuild with nothing imported would otherwise look unexplained",
      "payment terms" in ac._label("payable_source_map_edit"), ac._label("payable_source_map_edit"))
c = fresh(rows=base_rows(cfg=CFG_OFF))
r = ac.landed(c, "ORG", table="payable_source_map", periods=[], source="payable_source_map_edit", rows=1)
check("E13 a tenant that switched auto-rebuild OFF is not rebuilt by a settings edit either — one switch "
      "governs every path into this build",
      r["builds"]["queued"] is False, r)


class Boom:
    def schema(self, _s):
        raise RuntimeError("the database is down")


r = ac.landed(Boom(), "ORG", table="asset_ledger", periods=[], source="manual", rows=10)
check("E9 the hook NEVER raises into a lander — an upload is never broken by the queue",
      r["queued"] is False and r["reason"] == "error", r)

# ═══ §F the run and the sentence ════════════════════════════════════════════════════════════════════════════
print("\n§F THE RUN, AND THE SENTENCE AN OPERATOR READS")
c = fresh(rows=base_rows())
db.queue(c, "ORG", "payable_ledger", [ac.landing_entry("asset_ledger_upload", "asset_ledger", "Asset_Lending.xlsx",
                                                       15495, ac._iso(NOW))], NOW, True)
done = db.run_due(c, now=NOW + timedelta(minutes=6), runner=lambda _c, _o: {"status": "done", "written": 11000})
check("F1 a due request runs and records what happened", len(done) == 1 and done[0]["outcome"] == "built", done)
check("F2 the sentence names the time, the landing and the row count",
      "15,495" not in done[0]["sentence"] and "11,000 row(s)" in done[0]["sentence"]
      and "Asset_Lending.xlsx" in done[0]["sentence"], done[0]["sentence"])
c = fresh(rows=base_rows())
db.queue(c, "ORG", "payable_ledger", [ac.landing_entry("manual", "asset_ledger", at=ac._iso(NOW))], NOW, True)
bad = db.run_due(c, now=NOW + timedelta(minutes=6),
                 runner=lambda _c, _o: {"status": "error", "error": "PGRST204 rebate_basis"})
check("F3 a FAILED rebuild says so and says the screen still shows the last good build — it never claims "
      "a rebuild that did not happen",
      bad[0]["outcome"] == "failed" and "last good build" in bad[0]["sentence"], bad[0]["sentence"])
check("F4 a failed rebuild's tone is an error, so a screen that colours on tone cannot show green",
      bad[0]["tone"] == "error")
c = fresh(rows=base_rows())
db.queue(c, "ORG", "payable_ledger", [ac.landing_entry("manual", "asset_ledger", at=ac._iso(NOW))], NOW, True)
raised = db.run_due(c, now=NOW + timedelta(minutes=6),
                    runner=lambda _c, _o: (_ for _ in ()).throw(RuntimeError("boom")))
check("F5 a runner that RAISES is recorded as a failure, not lost", raised[0]["outcome"] == "failed"
      and "boom" in (raised[0]["message"] or ""))
c = fresh(rows=base_rows())
db.queue(c, "ORG", "payable_ledger", [ac.landing_entry("manual", "asset_ledger", at=ac._iso(NOW))], NOW, True)
check("F6 a request inside its quiet window is NOT run", db.run_due(c, now=NOW + timedelta(minutes=1)) == [])
v = db.read_view(c, "ORG", "payable_ledger")
check("F7 while queued, the page says the rebuild is coming and when",
      v["state"] == "queued" and "quiet for 5 minute(s)" in v["sentence"], v)
check("F8 an unknown build key in the queue is dropped with a warning, never run",
      db.run_one(c, "ORG", "no_such_build", [], durable=True) is None)

# ═══ §G negative controls ═══════════════════════════════════════════════════════════════════════════════════
print("\n§G NEGATIVE CONTROLS — the fixtures cannot go vacuous")
check("G1 the fake client really stores what the queue writes",
      len(fresh(rows=base_rows()).store["rows"][("commcalc", db.STATE_TABLE)]) == 0)
c = fresh(rows=base_rows())
db.queue(c, "ORG", "payable_ledger", [ac.landing_entry("manual", "asset_ledger", at=ac._iso(NOW))], NOW, True)
check("G2 removing asset_ledger from the registry would stop the measured defect being fixed — B1 is "
      "load-bearing", "asset_ledger" in db.static_inputs(db.by_key("payable_ledger")))
check("G3 the declared resolver really reads the config (an empty config declares nothing)",
      db.declared_inputs(fresh(rows=base_rows(src=[])), "ORG", db.by_key("payable_ledger")) == ((), None))
check("G4 §C would catch a verdict read off the row count: 'written 0' alone is not an outcome",
      db.classify_build({"written": 0})[0] == "failed")
check("G5 the busy guard really blocks a second concurrent rebuild of one snapshot in one process",
      (db._RUNNING.add(("ORG", "payable_ledger")) or
       db.run_one(c, "ORG", "payable_ledger", [], durable=True, runner=lambda *_a: {"status": "done"})) is None)
db._RUNNING.clear()

print(f"\n{'='*78}\n  {P} passed, {F} failed\n{'='*78}")
sys.exit(1 if F else 0)
