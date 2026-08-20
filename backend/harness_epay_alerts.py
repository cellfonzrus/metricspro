"""Offline harness for the hourly ePay discrepancy-alert orchestration (commcalc.router
`_run_epay_discrepancy_alerts`), with the DB layer + email sender mocked. The pure planning
(commcalc.epay_alerts.plan_emails) and the recon math (epay_fee_recon / closing) have their own
tests; this proves the router glue end to end: enable gate → recompute → flag → hierarchy → digest
plan → alert_log dedup → send/mark-run.

Scenarios:
  • a store over tolerance emails the DM + everyone above,
  • within tolerance (no flags) sends nothing,
  • a second run the same day is fully deduped (nothing re-sends),
  • a disabled tenant is skipped on the run-due path,
  • the pure planner names the right store-days + variances.

Run:  python harness_epay_alerts.py    → prints "N passed, M failed" and exits non-zero on any failure.
"""
import asyncio
import datetime as _dt
import sys

import app.modules.commcalc.router as C
import app.modules.storeops.router as S
from app.modules.commcalc import epay_alerts as EA
from app.modules.notify.channels import email_resend

PASS, FAIL = [], []
def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" :: {detail}"))

TODAY = _dt.datetime.now(_dt.timezone.utc).date().isoformat()

# Flagged store-days the (mocked) recompute returns for the enabled tenant: S1 fee + S1 payment + S2 fee.
FLAGS = [
    {"store_code": "S1", "close_date": TODAY, "kind": "fee",
     "system": 145.00, "portal": 132.00, "variance": 13.00},
    {"store_code": "S1", "close_date": TODAY, "kind": "payment",
     "system": 980.00, "portal": 1015.00, "variance": -35.00},
    {"store_code": "S2", "close_date": TODAY, "kind": "fee",
     "system": 60.00, "portal": 48.50, "variance": 11.50},
]
HIER = {
    "S1": {"dm": [{"name": "Dee DM", "email": "dee@x.com"}],
           "above": [{"name": "Rita Regional", "email": "rita@x.com"}, {"name": "Vic VP", "email": "vic@x.com"}]},
    "S2": {"dm": [{"name": "Dee DM", "email": "dee@x.com"}],
           "above": [{"name": "Rita Regional", "email": "rita@x.com"}]},
}


# ── stateful fakes (storeops.tenants + storeops.alert_log) ────────────────────────────────────────
class FakeQ:
    def __init__(self, store, table):
        self.store, self.table = store, table
        self.op = "select"; self.payload = None; self.filters = {}
    def select(self, *a, **k): self.op = "select"; return self
    def insert(self, payload): self.op = "insert"; self.payload = payload; return self
    def update(self, payload): self.op = "update"; self.payload = payload; return self
    def eq(self, col, val): self.filters[col] = val; return self
    def limit(self, *a, **k): return self
    def order(self, *a, **k): return self
    def execute(self):
        class _R: pass
        r = _R(); r.data = self.store._run(self); return r


class StoreopsFake:
    def __init__(self, tenants):
        self.tenants = tenants          # list of tenant dicts (mutated by mark-run)
        self.alert_log = []             # inserted dedup rows
    def schema(self, name): return self
    def table(self, name): return FakeQ(self, name)
    def _run(self, q):
        if q.table == "tenants":
            if q.op == "select":
                return [dict(t) for t in self.tenants]
            if q.op == "update":
                for t in self.tenants:
                    if t.get("org_id") == q.filters.get("org_id"):
                        t.update(q.payload)
                return []
        if q.table == "alert_log":
            if q.op == "select":
                return [r for r in self.alert_log
                        if r.get("org_id") == q.filters.get("org_id")
                        and r.get("scope") == q.filters.get("scope")
                        and r.get("ref_key") == q.filters.get("ref_key")]
            if q.op == "insert":
                rows = q.payload if isinstance(q.payload, list) else [q.payload]
                self.alert_log.extend(rows)
                return rows
        return []


SENT = []
async def _fake_send(to, subject, html, attachments=None):
    SENT.append({"to": to, "subject": subject})
    return "fake-id"


def _install(tenants, *, flags=FLAGS):
    """Wire the fakes/mocks for one scenario and return the StoreopsFake so the test can inspect it."""
    fake = StoreopsFake(tenants)
    C.get_supabase = lambda: fake                       # root client; .schema('storeops') → same fake
    C._epay_recompute_flags = lambda client, org, day, tol: [dict(f) for f in flags]
    S._managers_above_dm = lambda org, store: HIER.get(store, {"dm": [], "above": []})
    S._biz_tz_for = lambda org: _dt.timezone.utc
    email_resend.is_configured = lambda: True
    email_resend.send_email = _fake_send
    return fake


TENANT = {"org_id": "ORG", "epay_alerts_enabled": True, "epay_alert_tolerance": 1.00, "timezone": "UTC"}


async def main():
    # ── Scenario 1: a store over tolerance emails the DM + everyone above ─────────────────────────
    SENT.clear()
    fake = _install([dict(TENANT)])
    res = await C._run_epay_discrepancy_alerts(respect_enabled=True, dry_run=False)
    r0 = res["results"][0]
    tos = {e["to"] for e in SENT}
    ok("S1 emails the DM + every manager above (dee + rita + vic)",
       tos == {"dee@x.com", "rita@x.com", "vic@x.com"}, tos)
    ok("S1 run reports 3 recipients sent", r0["sent"] == 3, r0)
    ok("S1 flagged store-days counted (S1 fee + S1 payment + S2 fee = 3)", r0["flagged"] == 3, r0)
    dee = next((e for e in SENT if e["to"] == "dee@x.com"), None)
    ok("S1 DM digest subject counts 3 discrepancies across 2 stores",
       dee and "3 discrepancies across 2 stores" in dee["subject"], dee)
    vic = next((e for e in SENT if e["to"] == "vic@x.com"), None)
    ok("S1 above-manager over ONE store gets 2 discrepancies across 1 store",
       vic and "2 discrepancies across 1 store" in vic["subject"], vic)
    ok("S1 mark-run stamped on the tenant row",
       "sent 3" in (fake.tenants[0].get("epay_alert_last_detail") or ""), fake.tenants[0])

    # ── Scenario 2: a SECOND run the same day is fully deduped (alert_log already has the rows) ────
    SENT.clear()
    res2 = await C._run_epay_discrepancy_alerts(respect_enabled=True, dry_run=False)  # same `fake`, same day
    r2 = res2["results"][0]
    ok("S2 dedup — a second same-day run sends nothing", len(SENT) == 0 and r2["sent"] == 0, (SENT, r2))
    ok("S2 dedup — all recipients marked skipped", r2["skipped"] == 3, r2)

    # ── Scenario 3: within tolerance (no flags) sends nothing ─────────────────────────────────────
    SENT.clear()
    fake3 = _install([dict(TENANT)], flags=[])
    res3 = await C._run_epay_discrepancy_alerts(respect_enabled=True, dry_run=False)
    r3 = res3["results"][0]
    ok("S3 within tolerance (no flags) sends nothing", len(SENT) == 0 and r3["sent"] == 0, (SENT, r3))
    ok("S3 no-discrepancy run still marks the tenant row",
       "no discrepancies" in (fake3.tenants[0].get("epay_alert_last_detail") or ""), fake3.tenants[0])

    # ── Scenario 4: a disabled tenant is skipped on the run-due path ──────────────────────────────
    SENT.clear()
    _install([{**TENANT, "epay_alerts_enabled": False}])
    res4 = await C._run_epay_discrepancy_alerts(respect_enabled=True, dry_run=False)
    ok("S4 disabled tenant skipped on the run-due path", res4["ran"] == 0 and len(SENT) == 0, res4)

    # ── Scenario 5: run-now (respect_enabled=False) previews a disabled tenant in dry-run ─────────
    SENT.clear()
    _install([{**TENANT, "epay_alerts_enabled": False}])
    res5 = await C._run_epay_discrepancy_alerts(respect_enabled=False, dry_run=True)
    r5 = res5["results"][0]
    planned = r5.get("planned") or []
    ok("S5 run-now previews even when disabled, sending nothing",
       res5["ran"] == 1 and len(SENT) == 0 and len(planned) == 3, (res5, SENT))


asyncio.run(main())

# ── Scenario 6: the pure planner names the right store-days + variances ───────────────────────────
plan = EA.plan_emails(FLAGS, HIER, TODAY)
dee = next(d for d in plan["digests"] if d["to"] == "dee@x.com")
ok("P1 planner: DM digest lists Store S1 and Store S2",
   "Store S1" in dee["html"] and "Store S2" in dee["html"], dee["subject"])
ok("P2 planner: DM digest shows the fee ($13.00), payment ($-35.00) and S2 fee ($11.50) variances",
   "$13.00" in dee["html"] and "$-35.00" in dee["html"] and "$11.50" in dee["html"], "")
ok("P3 planner: ref_key incorporates tenant-scope+store+date+kind (once/day)",
   any(i["ref_key"] == f"epay_discrepancy|{TODAY}|dee@x.com|S1|{TODAY}|payment" for i in dee["items"]),
   [i["ref_key"] for i in dee["items"]])
ok("P4 planner: a manager with no email is skipped",
   all(d["to"] for d in plan["digests"]),
   [d["to"] for d in EA.plan_emails(FLAGS, {"S1": {"dm": [{"name": "No Email", "email": ""}], "above": []},
                                            "S2": HIER["S2"]}, TODAY)["digests"]])

print(f"\n{'=' * 60}\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:", FAIL)
sys.exit(1 if FAIL else 0)
