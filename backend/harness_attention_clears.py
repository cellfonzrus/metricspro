"""Offline proof harness — "an attention item MUST clear when the fix is done" (owner, 2026-07-26).

No database, no network: a recording fake Supabase client feeds the REAL provider code. It proves

  A. store COVERAGE semantics (the reported bug): a store is covered by store_mapping (normalized with
     commcalc's own _norm_store_match) OR by an explicit store_aliases row targeting its store_code.
  B. the CLEARING flow end-to-end: apply the exact write each deep-linked page performs, re-run the
     provider, and assert the item is GONE — plus that no item points at a page that cannot fix it.
  C. platform-core's own providers (tenant provisioning / seeding, system-error backlog) + degradation.
  D. notify delivery-wiring providers (unconfigured channel, no recipients, idle sweep, last send failed).
  E. helpdesk ticket-alert provider + its read short-circuits.
  F. aggregation: new group counts, the deep-link contract, the ZERO-ITEM state the frontend needs to
     render nothing, permission gates unchanged, org isolation.

Run:  cd backend && python3 harness_attention_clears.py
"""
import re
import sys
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

sys.path.insert(0, ".")

from app.modules.core import import_health as IH                      # noqa: E402
from app.modules.core import platform_attention as PA                  # noqa: E402
from app.modules.notify import attention as NA                        # noqa: E402
from app.modules.helpdesk import attention as HA                      # noqa: E402

PASS, FAIL = 0, 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


NOW = datetime.now(timezone.utc)
HOUSE = "00000000-0000-0000-0000-000000000001"
LUXE = "00000000-0000-0000-0000-0000000000ff"
ISO = lambda d: d.isoformat()                                          # noqa: E731
CTX = {"now": NOW, "feed_health": {}}


# ── minimal recording fake client (purpose-built: no feed_key conflict emulation) ────────────────────
class _Q:
    def __init__(self, store, key, log, fail):
        self.store, self.key, self.log, self.fail = store, key, log, fail
        self.filters, self.cols = {}, ""

    def select(self, cols="*", *a, **k):
        self.cols = cols
        return self
    def eq(self, k, v):
        self.filters[k] = v
        return self
    def gte(self, k, v):
        self.filters.setdefault("_gte", {})[k] = v
        return self
    def order(self, *a, **k):
        return self
    def limit(self, *a, **k):
        return self

    def execute(self):
        self.log.append({"key": self.key, "cols": self.cols, "filters": dict(self.filters)})
        for pat in self.fail:
            if re.search(pat, f"{self.key}|{self.cols}"):
                raise RuntimeError(f"simulated PostgREST error on {self.key} ({pat})")
        rows = self.store.get(self.key, [])
        out = []
        for r in rows:
            if all(r.get(k) == v for k, v in self.filters.items() if not k.startswith("_")):
                out.append(dict(r))
        return SimpleNamespace(data=out)


class _S:
    def __init__(self, store, schema, log, fail):
        self.store, self.schema, self.log, self.fail = store, schema, log, fail
    def table(self, t):
        return _Q(self.store, f"{self.schema}.{t}", self.log, self.fail)


class Fake:
    """`fail` = list of regexes matched against '<schema>.<table>|<selected cols>' → that read raises,
    which is how an un-run migration / missing column behaves through PostgREST."""
    def __init__(self, store, fail=()):
        self.store, self.log, self.fail = store, [], list(fail)
    def schema(self, s):
        return _S(self.store, s, self.log, self.fail)


# ════════════════════════════════════════════════════════════════════════════════════════════════════
print("\nA. store coverage semantics (the reported bug)")
EXACT = lambda s: (s or "").strip().lower()                            # noqa: E731  (pre-fix comparison)

st_missing = [{"store_code": "S9", "address": "9 New Rd", "market": "NY"}]
ok("A1 a store with no mapping and no alias is UNCOVERED (the alert is legitimate)",
   len(IH._uncovered_stores(st_missing, [], [])) == 1)

# THE REPORTED BUG: /commcalc/store-match writes an EXPLICIT store_aliases row (never a store_mapping row)
alias_rows = [{"alias": "STORE 9 - NEW RD", "store_code": "S9"}]
ok("A2 an explicit store_aliases row targeting the store COVERS it (the reported bug is fixed)",
   IH._uncovered_stores(st_missing, [], alias_rows) == [])
ok("A2b …and the OLD store_mapping-only rule would still call it unmapped (regression captured)",
   len([s for s in st_missing
        if EXACT(s["store_code"]) not in set() and EXACT(s["address"]) not in set()]) == 1)
ok("A3 store_mapping by store_code (case-insensitive) covers it",
   IH._uncovered_stores(st_missing, [{"store_code": "s9", "store_address": "somewhere else"}], []) == [])

# normalization parity with commcalc's own matcher (punctuation + suite tokens)
so = [{"store_code": "B-3PL", "address": "3 Palisade Ave., Suite 200"}]
sm = [{"store_code": "B-3PL-OTHER", "store_address": "3 Palisade Ave Ste 200"}]
ok("A4 store_mapping address differing only by punctuation/suite tokens covers it (normalized)",
   IH._uncovered_stores(so, sm, []) == [])
ok("A4b …and the pre-fix EXACT compare would have flagged it (why normalization was added)",
   len(IH._uncovered_stores(so, sm, [], norm=EXACT)) == 1)
ok("A5 an alias targeting a DIFFERENT store does not cover this one",
   len(IH._uncovered_stores(st_missing, [], [{"alias": "x", "store_code": "S1"}])) == 1)
ok("A6 an INACTIVE storeops store is never reported (a closed store is not a mapping gap)",
   IH._uncovered_stores([{"store_code": "S9", "address": "9 New Rd", "is_active": False}], [], []) == [])
ok("A7 a row with neither code nor address is skipped (nothing to map)",
   IH._uncovered_stores([{"store_code": "", "address": "  "}], [], []) == [])
ok("A8 alias store_code compare is case-insensitive",
   IH._uncovered_stores(st_missing, [], [{"alias": "x", "store_code": "s9"}]) == [])
ok("A9 blank mapping keys never act as a wildcard",
   len(IH._uncovered_stores(st_missing, [{"store_code": "", "store_address": ""}], [])) == 1)

# the normalizer really is commcalc's (lazy guarded import, no second implementation)
from app.modules.commcalc.router import _norm_store_match as CC_NORM    # noqa: E402
norm = IH._store_norm()
probe = "3 Palisade Ave., Suite 200"
ok("A10 _store_norm() resolves to commcalc's OWN _norm_store_match (no duplicated logic)",
   norm(probe) == CC_NORM(probe) and norm(probe) != EXACT(probe), (norm(probe), CC_NORM(probe)))
ok("A11 …and the fallback is pure/deterministic if that import ever fails",
   EXACT(probe) == EXACT(probe) and isinstance(IH._uncovered_stores(so, sm, [], norm=EXACT), list))


# ════════════════════════════════════════════════════════════════════════════════════════════════════
print("\nB. the CLEARING flow (apply the page's real write → the item disappears)")


def store_fixture():
    return {
        "storeops.stores": [
            {"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "NY", "is_active": True},
            {"org_id": HOUSE, "store_code": "S9", "address": "9 New Rd", "market": "NY", "is_active": True},
            {"org_id": LUXE, "store_code": "L1", "address": "77 Luxe Ave", "market": "", "is_active": True},
        ],
        "commcalc.store_mapping": [
            {"id": "m1", "org_id": HOUSE, "store_code": "S1", "store_address": "1 Main St", "market": "NY"},
            {"id": "m2", "org_id": HOUSE, "store_code": "S2", "store_address": "2 Old Rd", "market": ""},
            {"id": "m3", "org_id": LUXE, "store_code": "L1", "store_address": "77 Luxe Ave", "market": ""},
        ],
        "commcalc.store_aliases": [],
    }


store = store_fixture()
c = Fake(store)
items = IH._p_unmapped_stores(c, HOUSE, CTX)
by = {i["key"]: i for i in items}
ok("B1 the unresolvable store raises `stores_unmapped`", "stores_unmapped" in by, list(by))
ok("B1b …deep-linked to the page that actually writes the mapping",
   by["stores_unmapped"]["deep_link"] == "/commcalc/store-match", by.get("stores_unmapped"))
ok("B1c …and the blank-market store raises `stores_no_market`", "stores_no_market" in by, list(by))

# ── THE ACCEPTANCE CRITERION (item A): perform EXACTLY what POST /commcalc/store-aliases writes ──────
alias_row = {"org_id": HOUSE, "alias": "STORE 9 NEW RD", "store_code": "S9", "note": None,
             "source": "suggested", "confidence": "high"}
store["commcalc.store_aliases"].append(alias_row)
items2 = IH._p_unmapped_stores(c, HOUSE, CTX)
ok("B2 after the Store-Matching confirm, `stores_unmapped` is GONE (the user-reported bug)",
   "stores_unmapped" not in {i["key"] for i in items2}, [i["key"] for i in items2])
ok("B2b …and the unrelated market item is untouched",
   "stores_no_market" in {i["key"] for i in items2})

# source parity: the row we simulated is the row the real endpoint inserts
RSRC = open("app/modules/commcalc/router.py", encoding="utf-8").read()
ins = RSRC.split("async def add_store_alias", 1)[1].split("@router.delete", 1)[0]
ok("B2c source parity — the real POST /store-aliases inserts exactly {org_id, alias, store_code, note}"
   " (+ optional source/confidence)",
   "row = {'org_id': org_id, 'alias': alias, 'store_code': code," in ins
   and "'note':" in ins and "'source':" in ins and "'confidence':" in ins)
ok("B2d source parity — that endpoint does NOT write commcalc.store_mapping (why the old check never cleared)",
   "table('store_mapping')" not in ins and 'table("store_mapping")' not in ins
   and "table('store_aliases').insert" in ins)

# ── item B: perform EXACTLY what PUT /commcalc/stores/{id} writes (Settings → Stores & Markets) ──────
ok("B3 the market item deep-links to a page that HAS a market editor",
   by["stores_no_market"]["deep_link"] == "/commcalc/settings", by["stores_no_market"])
SSRC = open("../frontend/src/app/(platform)/commcalc/settings/page.tsx", encoding="utf-8").read()
ok("B3b source parity — /commcalc/settings really saves a store market (PUT /commcalc/stores/{id})",
   "async function saveStoreMarket" in SSRC and "/api/v1/commcalc/stores/${storeId}" in SSRC
   and "'🏪 Stores & Markets'" in SSRC.replace('"', "'"))
MSRC = open("../frontend/src/app/(platform)/commcalc/mapping/page.tsx", encoding="utf-8").read()
ok("B3c the OLD target /commcalc/mapping is a link hub with no market editor (dead end — why it moved)",
   "saveStoreMarket" not in MSRC and "/api/v1/commcalc/stores/" not in MSRC)
next(m for m in store["commcalc.store_mapping"] if m["id"] == "m2")["market"] = "NJ"
items3 = IH._p_unmapped_stores(c, HOUSE, CTX)
ok("B4 after setting the market, `stores_no_market` is GONE",
   "stores_no_market" not in {i["key"] for i in items3}, [i["key"] for i in items3])
ok("B5 with both fixed the provider returns NOTHING at all", items3 == [], items3)

# org isolation
c2 = Fake(store_fixture())
c2.store["commcalc.store_aliases"].append({"org_id": LUXE, "alias": "9 New Rd", "store_code": "S9"})
ok("B6 another tenant's alias never covers this tenant's store",
   "stores_unmapped" in {i["key"] for i in IH._p_unmapped_stores(c2, HOUSE, CTX)})
c3 = Fake(store_fixture())
IH._p_unmapped_stores(c3, LUXE, CTX)
ok("B6b every read in the provider is org-filtered to the acting org",
   all(e["filters"].get("org_id") == LUXE for e in c3.log), c3.log)
ok("B6c luxelink's own gap is reported from luxelink's own rows",
   {i["key"] for i in IH._p_unmapped_stores(Fake(store_fixture()), LUXE, CTX)} == {"stores_no_market"})
ok("B7 mig-023 absent (store_aliases unreadable) degrades to the store_mapping-only answer, no crash",
   {i["key"] for i in IH._p_unmapped_stores(Fake(store_fixture(), fail=[r"store_aliases"]), HOUSE, CTX)}
   == {"stores_unmapped", "stores_no_market"})


# ════════════════════════════════════════════════════════════════════════════════════════════════════
print("\nC. platform-core providers (provisioning / seeding · system-error backlog)")
from app.modules.core.entitlements import SEED_VERSION                 # noqa: E402

ok("C1 no tenants row for the acting org → 'not registered' ERROR item",
   [(i["key"], i["severity"], i["deep_link"]) for i in
    PA._p_tenant_provisioning(Fake({"storeops.tenants": []}), HOUSE, CTX)]
   == [("tenant_unregistered", "error", "/admin/tenants")])
beh = Fake({"storeops.tenants": [{"org_id": HOUSE, "name": "House", "seed_version": SEED_VERSION - 1}]})
ok("C2 seed_version behind → 'default setup content is missing' WARNING",
   [i["key"] for i in PA._p_tenant_provisioning(beh, HOUSE, CTX)] == ["tenant_seed_behind"])
ok("C2b …and it names both versions in plain language",
   str(SEED_VERSION) in PA._p_tenant_provisioning(beh, HOUSE, CTX)[0]["detail"])
cur = Fake({"storeops.tenants": [{"org_id": HOUSE, "seed_version": SEED_VERSION}]})
ok("C3 once provisioning catches up the item CLEARS", PA._p_tenant_provisioning(cur, HOUSE, CTX) == [])
ok("C3b a FUTURE seed_version (rollback) does not nag",
   PA._p_tenant_provisioning(Fake({"storeops.tenants": [{"seed_version": SEED_VERSION + 3,
                                                         "org_id": HOUSE}]}), HOUSE, CTX) == [])
ok("C4 pre-mig-076 row (no seed_version column) → silent, no false alarm",
   PA._p_tenant_provisioning(Fake({"storeops.tenants": [{"org_id": HOUSE, "name": "House"}]}), HOUSE, CTX) == [])
ok("C4b tenants table unreachable → silent (never breaks the popup)",
   PA._p_tenant_provisioning(Fake({}, fail=[r"storeops.tenants"]), HOUSE, CTX) == [])
ok("C4c the provisioning read is org-scoped",
   all(e["filters"].get("org_id") == LUXE
       for e in (lambda f: (PA._p_tenant_provisioning(f, LUXE, CTX), f)[1])(
           Fake({"storeops.tenants": []})).log))

FL = "core.failure_log"
fl_rows = [
    {"id": "f1", "org_id": HOUSE, "category": "sweep_error", "severity": "error", "status": "open",
     "reviewed": False, "created_at": ISO(NOW - timedelta(days=1))},
    {"id": "f2", "org_id": HOUSE, "category": "upload_rejected", "severity": "critical", "status": "open",
     "reviewed": False, "created_at": ISO(NOW - timedelta(days=2))},
    {"id": "f3", "org_id": HOUSE, "category": "face_mismatch", "severity": "warning", "status": "open",
     "reviewed": False, "created_at": ISO(NOW - timedelta(hours=2))},
    {"id": "f4", "org_id": LUXE, "category": "sweep_error", "severity": "error", "status": "open",
     "reviewed": False, "created_at": ISO(NOW - timedelta(hours=2))},
]
fc = Fake({FL: [dict(r) for r in fl_rows]})
fi = PA._p_failure_backlog(fc, HOUSE, CTX)
ok("C5 unreviewed error/critical rows raise ONE grouped item", [i["key"] for i in fi] == ["failures_unreviewed"])
ok("C5b …counting only this tenant's error+critical rows (warnings excluded)", fi[0]["count"] == 2, fi[0])
ok("C5c …deep-linked to /failures with plain-language instructions",
   fi[0]["deep_link"] == "/failures" and "plain English" in fi[0]["detail"])
ok("C5d …in the new `system` group", fi[0]["group"] == "system")
for r in fc.store[FL]:
    if r["org_id"] == HOUSE:
        r["reviewed"] = True                    # exactly what POST /core/failures/bulk-review writes
ok("C6 after the admin CLEARS the group the item is GONE", PA._p_failure_backlog(fc, HOUSE, CTX) == [])
ok("C7 warnings alone never raise the item",
   PA._p_failure_backlog(Fake({FL: [dict(fl_rows[2])]}), HOUSE, CTX) == [])
ok("C8 pre-mig-716 (no `reviewed` column) falls back to status='open' instead of going silent",
   [i["count"] for i in PA._p_failure_backlog(
       Fake({FL: [dict(r) for r in fl_rows]}, fail=[r"reviewed"]), HOUSE, CTX)] == [2])
ok("C8b …and a fully-resolved pre-716 log stays quiet",
   PA._p_failure_backlog(Fake({FL: [dict(r, status="resolved") for r in fl_rows]},
                              fail=[r"reviewed"]), HOUSE, CTX) == [])
ok("C9 failure_log unreadable (mig 112 un-run) → silent",
   PA._p_failure_backlog(Fake({}, fail=[r"failure_log"]), HOUSE, CTX) == [])
lc = Fake({FL: [dict(r) for r in fl_rows]})
PA._p_failure_backlog(lc, LUXE, CTX)
ok("C9b the failure read is org-scoped", all(e["filters"].get("org_id") == LUXE for e in lc.log), lc.log)


# ════════════════════════════════════════════════════════════════════════════════════════════════════
print("\nD. notify delivery wiring")
_real_email, _real_wa = NA.email_resend, NA.whatsapp_meta


def channels(email=True, whatsapp=True):
    NA.email_resend = SimpleNamespace(is_configured=lambda: email)
    NA.whatsapp_meta = SimpleNamespace(is_configured=lambda: whatsapp)


def sub(**kw):
    base = {"id": "s1", "org_id": HOUSE, "name": "Weekly sales", "report_key": "sales",
            "channels": ["email"], "recipient_ids": ["r1"], "ad_hoc_emails": [], "ad_hoc_phones": [],
            "is_active": True, "next_run_at": ISO(NOW + timedelta(hours=5)), "last_run_at": None}
    base.update(kw)
    return base


channels(email=True, whatsapp=False)
d1 = NA._p_notify_delivery(Fake({"notify.subscriptions": [sub(channels=["whatsapp"])]}), HOUSE, CTX)
ok("D1 a schedule on an UNCONFIGURED channel raises an error item",
   [(i["key"], i["severity"]) for i in d1] == [("notify_channel_missing:whatsapp", "error")], d1)
ok("D1b …deep-linked to the schedules tab", d1[0]["deep_link"] == "/notify?tab=subs")
ok("D2 switching that schedule to a configured channel CLEARS it",
   NA._p_notify_delivery(Fake({"notify.subscriptions": [sub(channels=["email"])]}), HOUSE, CTX) == [])
channels(True, True)
ok("D2b configuring the channel ALSO clears it (same item, other fix)",
   NA._p_notify_delivery(Fake({"notify.subscriptions": [sub(channels=["whatsapp"],
                                                            ad_hoc_phones=["+15551234567"])]}),
                         HOUSE, CTX) == [])
d3 = NA._p_notify_delivery(Fake({"notify.subscriptions": [sub(recipient_ids=[])]}), HOUSE, CTX)
ok("D3 an active schedule with NO recipient at all is reported",
   [i["key"] for i in d3] == ["notify_no_recipients"], d3)
ok("D3b attaching a recipient clears it",
   NA._p_notify_delivery(Fake({"notify.subscriptions": [sub(recipient_ids=["r7"])]}), HOUSE, CTX) == [])
ok("D3c an ad-hoc email counts as a recipient",
   NA._p_notify_delivery(Fake({"notify.subscriptions": [sub(recipient_ids=[],
                                                            ad_hoc_emails=["a@b.c"])]}), HOUSE, CTX) == [])
idle = Fake({"notify.subscriptions": [sub(next_run_at=ISO(NOW - timedelta(hours=30)))], "storeops.tenants": []})
d4 = NA._p_notify_delivery(idle, HOUSE, CTX)
ok("D4 a schedule 30h past its send time reports the sweep as not running",
   [(i["key"], i["severity"]) for i in d4] == [("notify_scheduler_idle", "error")], d4)
ok("D4b …and says how far behind it is", "30h" in d4[0]["detail"], d4[0]["detail"])
ok("D5 once the sweep stamps next_run_at forward the item CLEARS",
   NA._p_notify_delivery(Fake({"notify.subscriptions": [sub()]}), HOUSE, CTX) == [])
ok("D5b a tenant grace override (notify_policy.scheduler_grace_hours) is honored — RULE TWO",
   NA._p_notify_delivery(
       Fake({"notify.subscriptions": [sub(next_run_at=ISO(NOW - timedelta(hours=30)))],
             "storeops.tenants": [{"org_id": HOUSE, "notify_policy": {"scheduler_grace_hours": 48}}]}),
       HOUSE, CTX) == [])
ok("D5c the grace lookup happens ONLY when a schedule already looks idle (cheap healthy path)",
   not any(e["key"] == "storeops.tenants"
           for e in (lambda f: (NA._p_notify_delivery(f, HOUSE, CTX), f)[1])(
               Fake({"notify.subscriptions": [sub()]})).log))
ok("D6 an INACTIVE schedule is ignored entirely",
   NA._p_notify_delivery(Fake({"notify.subscriptions": [sub(is_active=False, channels=["whatsapp"],
                                                            recipient_ids=[],
                                                            next_run_at=ISO(NOW - timedelta(days=9)))]}),
                         HOUSE, CTX) == [])
log_fail = [{"org_id": HOUSE, "channel": "email", "status": "failed", "error": "domain not verified",
             "created_at": ISO(NOW - timedelta(hours=1))},
            {"org_id": HOUSE, "channel": "email", "status": "sent", "error": None,
             "created_at": ISO(NOW - timedelta(hours=9))}]
d7 = NA._p_notify_delivery(Fake({"notify.send_log": log_fail}), HOUSE, CTX)
ok("D7 the LAST attempt on a channel failing is reported",
   [i["key"] for i in d7] == ["notify_channel_failing:email"], d7)
ok("D7b …with the provider's own reason quoted", "domain not verified" in d7[0]["detail"])
ok("D7c …and deep-linked to the delivery log", d7[0]["deep_link"] == "/notify?tab=log")
ok("D8 a later SUCCESSFUL send clears it immediately (state, not a rolling error count)",
   NA._p_notify_delivery(Fake({"notify.send_log": [{"org_id": HOUSE, "channel": "email", "status": "sent",
                                                    "created_at": ISO(NOW)}] + log_fail}), HOUSE, CTX) == [])
ok("D8b an ANCIENT failure (>30d, nothing since) is not treated as current",
   NA._p_notify_delivery(Fake({"notify.send_log": [{"org_id": HOUSE, "channel": "email", "status": "failed",
                                                    "created_at": ISO(NOW - timedelta(days=45))}]}),
                         HOUSE, CTX) == [])
ok("D9 a tenant with no notify config at all is silent", NA._p_notify_delivery(Fake({}), HOUSE, CTX) == [])
ok("D9b notify tables unreachable (mig 010 un-run) → silent",
   NA._p_notify_delivery(Fake({}, fail=[r"notify\."]), HOUSE, CTX) == [])
nl = Fake({"notify.subscriptions": [sub(org_id=LUXE)], "notify.send_log": log_fail})
NA._p_notify_delivery(nl, LUXE, CTX)
ok("D10 every notify read is org-scoped", all(e["filters"].get("org_id") == LUXE for e in nl.log), nl.log)
NA.email_resend, NA.whatsapp_meta = _real_email, _real_wa


# ════════════════════════════════════════════════════════════════════════════════════════════════════
print("\nE. helpdesk ticket alerts")
TCK, TS, TC = "storeops.tickets", "storeops.ticket_settings", "storeops.ticket_categories"
e0 = Fake({TCK: [], TS: [], TC: []})
ok("E1 a tenant with NO tickets is silent (helpdesk unused → no nagging)",
   HA._p_helpdesk_alerts(e0, HOUSE, CTX) == [])
ok("E1b …costing exactly ONE read", len(e0.log) == 1, e0.log)
gap = {TCK: [{"id": "t1", "org_id": HOUSE}], TS: [{"org_id": HOUSE, "notify_emails": []}],
       TC: [{"org_id": HOUSE, "name": "IT / Systems", "is_active": True, "notify_emails": []},
            {"org_id": HOUSE, "name": "HR / Payroll", "is_active": True, "notify_emails": ["hr@x.com"]}]}
e2 = HA._p_helpdesk_alerts(Fake(gap), HOUSE, CTX)
ok("E2 tickets + no alert email anywhere → item", [i["key"] for i in e2] == ["helpdesk_no_alert_email"], e2)
ok("E2b …deep-linked to the tab that fixes it",
   e2[0]["deep_link"] == "/helpdesk/settings?tab=settings")
ok("E2c …naming the unrouted categories", "IT / Systems" in e2[0]["detail"], e2[0]["detail"])
ok("E3 saving a company-wide alert list CLEARS it",
   HA._p_helpdesk_alerts(Fake({**gap, TS: [{"org_id": HOUSE, "notify_emails": ["ops@x.com"]}]}),
                         HOUSE, CTX) == [])
ok("E4 …or routing EVERY active category clears it",
   HA._p_helpdesk_alerts(Fake({**gap, TC: [{"org_id": HOUSE, "name": "IT", "is_active": True,
                                            "notify_emails": ["it@x.com"]},
                                           {"org_id": HOUSE, "name": "Old", "is_active": False,
                                            "notify_emails": []}]}), HOUSE, CTX) == [])
e5 = Fake({**gap, TS: [{"org_id": HOUSE, "notify_emails": ["ops@x.com"]}]})
HA._p_helpdesk_alerts(e5, HOUSE, CTX)
ok("E5 the configured path costs 2 reads (no category scan)", len(e5.log) == 2, e5.log)
ok("E6 helpdesk tables unreachable (mig 053 un-run) → silent",
   HA._p_helpdesk_alerts(Fake({}, fail=[r"storeops\.ticket"]), HOUSE, CTX) == [])
e7 = Fake({TCK: [{"id": "t1", "org_id": LUXE}], TS: [], TC: []})
HA._p_helpdesk_alerts(e7, LUXE, CTX)
ok("E7 every helpdesk read is org-scoped", all(x["filters"].get("org_id") == LUXE for x in e7.log), e7.log)


# ════════════════════════════════════════════════════════════════════════════════════════════════════
print("\nF. aggregation · deep-link contract · ZERO-state · gates")
channels(True, True)
messy = Fake({**store_fixture(),
              "storeops.tenants": [{"org_id": HOUSE, "seed_version": SEED_VERSION - 1}],
              FL: [dict(r) for r in fl_rows],
              "notify.subscriptions": [sub(recipient_ids=[])],
              TCK: [{"id": "t1", "org_id": HOUSE}], TS: [{"org_id": HOUSE, "notify_emails": []}],
              TC: [{"org_id": HOUSE, "name": "IT", "is_active": True, "notify_emails": []}]})
agg = IH.collect_attention(messy, HOUSE, deep=False, feed_h={})
keys = {i["key"] for i in agg["items"]}
ok("F1 one call aggregates every domain's items",
   {"stores_unmapped", "stores_no_market", "tenant_seed_behind", "failures_unreviewed",
    "notify_no_recipients", "helpdesk_no_alert_email"} <= keys, sorted(keys))
ok("F2 counts expose the NEW groups the UI summarises",
   agg["counts"]["config"] >= 2 and agg["counts"]["system"] == 1
   and agg["counts"]["total"] == len(agg["items"]), agg["counts"])
ok("F3 no provider raised", agg["provider_errors"] == [], agg["provider_errors"])
ok("F4 EVERY item carries a fix deep link + a button label (the provider contract)",
   all(i.get("deep_link") and i.get("deep_link_label") for i in agg["items"]),
   [(i["key"], i.get("deep_link")) for i in agg["items"]])

# every deep link must be a REAL page, and must be a page where the fix is possible
import os                                                              # noqa: E402
FRONT = "../frontend/src/app/(platform)"


def page_exists(link):
    p = link.split("?")[0].split("#")[0].strip("/")
    return os.path.isfile(os.path.join(FRONT, p, "page.tsx"))


bad = [i["deep_link"] for i in agg["items"] if not page_exists(i["deep_link"])]
ok("F5 every deep link resolves to an existing page (no 404 'Fix' button)", not bad, bad)
ok("F5b no item points at /commcalc/mapping (the hub that cannot fix either mapping item)",
   not any((i["deep_link"] or "").startswith("/commcalc/mapping") for i in agg["items"]))
PROV_SRC = "".join(open(f, encoding="utf-8").read() for f in
                   ("app/modules/core/import_health.py", "app/modules/core/platform_attention.py",
                    "app/modules/notify/attention.py", "app/modules/helpdesk/attention.py"))
groups = set(re.findall(r'_item\(\s*"([a-z_]+)"', PROV_SRC))
AD = open("../frontend/src/components/AdminAttention.tsx", encoding="utf-8").read()
ui_groups = set(re.findall(r"'([a-z_]+)'", AD.split("GROUP_ORDER = [", 1)[1].split("]", 1)[0]))
ok("F6 every group the backend can emit is renderable by the popup (no silently-dropped item)",
   groups <= ui_groups, sorted(groups - ui_groups))


# operator addition 2026-07-26: another module may register a provider with a group this build of the UI has
# never heard of. The aggregator must still COUNT it in `total` (the pill number); the UI buckets it into
# "Other" (proven in prove_attention_clears.mjs D9-D12) so pill and rows can never disagree.
@IH.register_provider("harness_unknown_group", label="fabricated", group="zzz", cost="cheap")
def _fab(client, org_id, ctx):
    return [IH._item("zzz", "fabricated_item", "warning", "Fabricated", "d", 1, "/failures", "Fix")]


fab = IH.collect_attention(Fake({}), HOUSE, deep=False, feed_h={})
ok("F6b an unknown group still reaches `items` and `counts.total`",
   any(i["key"] == "fabricated_item" for i in fab["items"])
   and fab["counts"]["total"] == len(fab["items"]), fab["counts"])
ok("F6c …and is NOT double-counted under any named group",
   sum(fab["counts"][k] for k in ("import", "mapping", "duplicate", "config", "system"))
   < fab["counts"]["total"], fab["counts"])
IH.PROVIDERS[:] = [p for p in IH.PROVIDERS if p["key"] != "harness_unknown_group"]

# ── the ZERO state: everything configured ⇒ the payload the frontend renders NOTHING for ─────────────
clean = Fake({
    "storeops.stores": [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "is_active": True}],
    "commcalc.store_mapping": [{"id": "m1", "org_id": HOUSE, "store_code": "S1",
                                "store_address": "1 Main St", "market": "NY"}],
    "commcalc.store_aliases": [], "storeops.tenants": [{"org_id": HOUSE, "seed_version": SEED_VERSION}],
    FL: [], "notify.subscriptions": [sub()], "notify.send_log": [], TCK: [], TS: [], TC: [],
    "commcalc.upload_trace": [], "commcalc.email_processed": [], "commcalc.daily_closing": []})
zero = IH.collect_attention(clean, HOUSE, deep=False, feed_h={})
ok("F7 a fully-healthy tenant produces ZERO items", zero["items"] == [], zero["items"])
ok("F7b …zero counts in every group",
   all(zero["counts"][k] == 0 for k in ("total", "error", "warning", "import", "mapping",
                                        "duplicate", "config", "system")), zero["counts"])
ok("F7c …and no provider errored on the way", zero["provider_errors"] == [], zero["provider_errors"])
ok("F8 the frontend renders NOTHING for that payload (source parity: the single null-guard)",
   "if (!allowed || !data || !(data.items || []).length) return null" in AD)
ok("F8b …and the popup can only be OPENED when items exist",
   "if (!alive || !d || !(d.items || []).length) return" in AD)
ok("F8c the pill is inside the same guarded return (no separate render path)",
   AD.index("if (!allowed || !data || !(data.items || []).length) return null") < AD.index("needs attention"))

# permissions: unchanged gate, still admin-only, still clamped
personas = [
    ("super_admin", {"super_admin": True, "org_id": HOUSE, "perms": {"scope": "self"}}, True),
    ("role=admin", {"org_id": HOUSE, "role": "admin", "perms": {"scope": "market"}}, True),
    ("scope=all", {"org_id": HOUSE, "role": "exec", "perms": {"scope": "all"}}, True),
    ("modules.admin", {"org_id": HOUSE, "role": "ops", "perms": {"modules": {"admin": True}}}, True),
    ("market manager", {"org_id": HOUSE, "role": "market_manager", "perms": {"scope": "market"}}, False),
    ("rep", {"org_id": HOUSE, "role": "rep", "perms": {"scope": "self"}}, False),
    ("page GRANT", {"org_id": HOUSE, "role": "rep",
                    "perms": {"scope": "self", "pages": {"/admin/import-health": True}}}, True),
    ("page DENY beats admin", {"org_id": HOUSE, "role": "admin",
                               "perms": {"scope": "all", "pages": {"/admin/import-health": False}}}, False),
    ("unauthenticated", None, False),
]
for nm, caller, want in personas:
    ok(f"F9:{nm} -> {'sees' if want else 'never sees'} attention",
       IH.can_view_attention(caller) is want, caller)
ok("F10 a super-admin acting as a tenant scopes to the ACTING tenant",
   IH._scope_org({"super_admin": True, "org_id": HOUSE}, LUXE) == LUXE)
ok("F10b a normal admin is CLAMPED to their own tenant",
   IH._scope_org({"super_admin": False, "org_id": LUXE}, HOUSE) == LUXE)
NA.email_resend, NA.whatsapp_meta = _real_email, _real_wa

print(f"\n{PASS}/{PASS + FAIL} passed")
sys.exit(1 if FAIL else 0)
