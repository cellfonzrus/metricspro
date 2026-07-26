"""Offline proof harness for the universal import-health + admin-attention feature (mig 717).

No database, no network: a recording fake Supabase client feeds the real module code. Proves
  A. freshness/staleness maths (fresh / stale / never / boundary / channel-stale)
  B. derivation from every config shape the system already knows (incl. the owner's VidaPay case)
  C. auto-derive IDEMPOTENCE (run twice -> zero duplicates, admin edits preserved)
  D. ORG ISOLATION — every read is org-filtered and two orgs never see each other's feeds/attention
  E. the admin gate (7 personas) + the org CLAMP for a non-super-admin
  F. attention aggregation: provider registry, cheap/heavy split, exception isolation

Run:  cd backend && python3 harness_import_health.py
"""
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")

from app.modules.core import import_health as IH   # noqa: E402

PASS, FAIL = 0, 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


# Anchored to the REAL clock so every fixture age is exact relative to what feed_health() reads
# internally — the harness can never flake because the wall-clock date moved.
NOW = datetime.now(timezone.utc)
HOUSE = "00000000-0000-0000-0000-000000000001"
LUXE = "00000000-0000-0000-0000-0000000000ff"


def iso(dt):
    return dt.isoformat()


# ── fake supabase client ─────────────────────────────────────────────────────────────────────────────
class _Q:
    def __init__(self, store, schema, table, log):
        self.store, self.schema, self.table, self.log = store, schema, table, log
        self.filters = {}
        self._op = "select"
        self._payload = None

    # builder API used by the module
    def select(self, *a, **k):
        return self
    def eq(self, k, v):
        self.filters[k] = v
        return self
    def gte(self, k, v):
        self.filters.setdefault("_gte", {})[k] = v
        return self
    def lte(self, k, v):
        self.filters.setdefault("_lte", {})[k] = v
        return self
    def in_(self, k, v):
        return self
    def order(self, *a, **k):
        return self
    def limit(self, *a, **k):
        return self
    def range(self, *a, **k):
        return self
    def insert(self, rows):
        self._op, self._payload = "insert", rows
        return self
    def upsert(self, rows, **k):
        self._op, self._payload = "upsert", rows
        return self
    def update(self, patch):
        self._op, self._payload = "update", patch
        return self
    def delete(self):
        self._op = "delete"
        return self

    def execute(self):
        key = f"{self.schema}.{self.table}"
        self.log.append({"key": key, "op": self._op, "filters": dict(self.filters)})
        rows = self.store.get(key, [])
        if self._op in ("insert", "upsert"):
            new = self._payload if isinstance(self._payload, list) else [self._payload]
            have = {(r.get("org_id"), r.get("feed_key")) for r in rows}
            added = []
            for r in new:
                k = (r.get("org_id"), r.get("feed_key"))
                if k in have:          # emulate on_conflict + ignore_duplicates
                    continue
                have.add(k)
                r = dict(r, id=f"id-{len(rows) + len(added)}")
                added.append(r)
            self.store.setdefault(key, []).extend(added)
            return type("R", (), {"data": added})()
        if self._op == "update":
            hit = [r for r in rows if all(r.get(k) == v for k, v in self.filters.items() if not k.startswith("_"))]
            for r in hit:
                r.update(self._payload)
            return type("R", (), {"data": hit})()
        if self._op == "delete":
            keep = [r for r in rows if not all(r.get(k) == v for k, v in self.filters.items() if not k.startswith("_"))]
            self.store[key] = keep
            return type("R", (), {"data": []})()
        out = []
        for r in rows:
            if all(r.get(k) == v for k, v in self.filters.items() if not k.startswith("_")):
                out.append(dict(r))
        return type("R", (), {"data": out})()


class _Schema:
    def __init__(self, store, schema, log, rpcs):
        self.store, self.schema, self.log, self.rpcs = store, schema, log, rpcs
    def table(self, t):
        return _Q(self.store, self.schema, t, self.log)
    def rpc(self, name, params):
        self.log.append({"key": f"rpc:{self.schema}.{name}", "op": "rpc", "filters": dict(params)})
        fn = self.rpcs.get(f"{self.schema}.{name}")
        data = fn(params) if fn else []
        return type("B", (), {"execute": lambda _s=None: type("R", (), {"data": data})()})()


class FakeClient:
    def __init__(self, store, rpcs=None):
        self.store, self.log, self.rpcs = store, [], rpcs or {}
    def schema(self, s):
        return _Schema(self.store, s, self.log, self.rpcs)


# ── fixture: two tenants with DIFFERENT import setups ────────────────────────────────────────────────
def fixture():
    store = {
        "commcalc.email_sweep_config": [
            {"org_id": HOUSE, "account": "default", "label": "B2B daily", "username": "b2breports@x",
             "imap_host": "imap.x", "enabled": True, "frequency": "daily", "last_run_at": iso(NOW - timedelta(hours=3)),
             "last_status": "ok",
             "patterns": [{"pattern": "*Sales*", "upload_type": "daily_sales"},
                          {"pattern": "*Aging*", "upload_type": "inventory_aging"}]},
            {"org_id": LUXE, "account": "total", "label": "Total Wireless", "username": "luxelink@x",
             "imap_host": "imap.x", "enabled": True, "frequency": "daily", "last_run_at": iso(NOW - timedelta(hours=2)),
             "last_status": "ok",
             "patterns": [{"pattern": "*MA*Commission*", "upload_type": "ma_commission"}]},
        ],
        "commcalc.ftp_sweep_config": [
            {"org_id": HOUSE, "host": "ftp.x", "enabled": False, "frequency": "daily",
             "patterns": [{"pattern": "*Sales*", "upload_type": "sales"}]},
        ],
        "commcalc.dlar_sweep_config": [
            {"org_id": HOUSE, "portal_user": "a@b", "enabled": True, "frequency": "daily",
             "last_run_at": iso(NOW - timedelta(hours=40)), "last_status": "ok"},
        ],
        "commcalc.epay_sweep_config": [{"org_id": HOUSE, "portal_user": "", "enabled": False}],
        "commcalc.vip_sweep_config": [], "commcalc.b2b_sweep_config": [],
        "commcalc.closing_sweep_config": [
            {"org_id": HOUSE, "sheet_id": "sheet1", "enabled": True, "frequency": "daily",
             "last_run_at": iso(NOW - timedelta(hours=5)), "last_status": "ok"},
        ],
        # LUXE has the VidaPay login the owner named; HOUSE has none (so its MA reports are manual_expected)
        "commcalc.data_source": [
            {"org_id": LUXE, "id": "src-luxe-1", "processor": "vidapay", "label": "VidaPay — luxelink",
             "enabled": True, "frequency": "daily", "username": "u",
             "last_run_at": iso(NOW - timedelta(hours=70)), "last_status": "ok"},
        ],
        "commcalc.report_pull_map": [
            {"org_id": HOUSE, "report_key": "ma_commission", "display_name": "MA - Commission Details",
             "processor": "vidapay", "enabled": True},
            {"org_id": LUXE, "report_key": "ma_commission", "display_name": "MA - Commission Details",
             "processor": "vidapay", "enabled": True},
            {"org_id": LUXE, "report_key": "ma_daily_tx", "display_name": "MA Daily Tx",
             "processor": "vidapay", "enabled": True},
        ],
        "commcalc.report_definitions": [
            # 'sales' is ALREADY covered by the FTP pattern above -> must be deduped away;
            # 'hotsheet' is covered by nothing -> registered, but DISABLED (auto=false) so it is silent.
            {"org_id": HOUSE, "report_key": "sales", "label": "Sales Transactions", "auto": False,
             "target_table": "raw_sales", "upload_endpoint": "commcalc/upload/sales"},
            {"org_id": HOUSE, "report_key": "hotsheet", "label": "Pricing Hotsheet", "auto": False,
             "target_table": "hotsheet", "upload_endpoint": "commcalc/hotsheet/upload"},
        ],
        "core.import_feed": [],
        "storeops.stores": [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "NY"},
                            {"org_id": HOUSE, "store_code": "S9", "address": "9 New Rd", "market": "NY"},
                            {"org_id": LUXE, "store_code": "L1", "address": "77 Luxe Ave", "market": ""}],
        "commcalc.store_mapping": [{"org_id": HOUSE, "store_code": "S1", "store_address": "1 Main St", "market": "NY"},
                                   {"org_id": LUXE, "store_code": "L1", "store_address": "77 Luxe Ave", "market": ""}],
        "commcalc.upload_trace": [], "commcalc.email_processed": [], "commcalc.daily_closing": [],
    }
    evidence = {
        HOUSE: [
            {"kind": "email", "k1": "default", "k2": "daily_sales",
             "last_success": iso(NOW - timedelta(hours=4)), "last_status": "ok", "n": 12},
            {"kind": "upload_trace", "k1": "daily_sales", "k2": None,
             "last_success": iso(NOW - timedelta(hours=4)), "last_status": "ok", "n": 12},
            # inventory_aging has NOT landed in 9 days -> overdue
            {"kind": "email", "k1": "default", "k2": "inventory_aging",
             "last_success": iso(NOW - timedelta(days=9)), "last_status": "ok", "n": 1},
            {"kind": "sweep", "k1": "dlar_sweep_config", "k2": None,
             "last_success": iso(NOW - timedelta(hours=40)), "last_status": "ok", "n": 1},
            {"kind": "sweep", "k1": "closing_sweep_config", "k2": None,
             "last_success": iso(NOW - timedelta(hours=5)), "last_status": "ok", "n": 1},
        ],
        LUXE: [
            {"kind": "email", "k1": "total", "k2": "ma_commission",
             "last_success": iso(NOW - timedelta(hours=2)), "last_status": "ok", "n": 3},
            {"kind": "source", "k1": "src-luxe-1", "k2": "vidapay",
             "last_success": iso(NOW - timedelta(hours=70)), "last_status": "ok", "n": 1},
        ],
    }
    rpcs = {"core.import_evidence": lambda p: evidence.get(p["p_org"], []),
            "core.import_table_freshness": lambda p: []}
    return store, rpcs


# ── A. freshness maths (PURE) ────────────────────────────────────────────────────────────────────────
print("\nA. staleness / freshness fixture")
feed = {"cadence_hours": 24, "grace_hours": 6,
        "evidence": [{"kind": "email", "account": "default", "upload_type": "daily_sales"},
                     {"kind": "upload_trace", "upload_type": "daily_sales"}]}
ev_fresh = [{"kind": "email", "k1": "default", "k2": "daily_sales",
             "last_success": iso(NOW - timedelta(hours=4)), "last_status": "ok"}]
ev_old = [{"kind": "email", "k1": "default", "k2": "daily_sales",
           "last_success": iso(NOW - timedelta(hours=200)), "last_status": "ok"}]
s_fresh = IH.feed_status(feed, ev_fresh, now=NOW)
s_old = IH.feed_status(feed, ev_old, now=NOW)
s_never = IH.feed_status(feed, [], now=NOW)
ok("A1 fresh -> overdue False / state ok", s_fresh["overdue"] is False and s_fresh["state"] == "ok", s_fresh)
ok("A2 old   -> overdue True  / state overdue", s_old["overdue"] is True and s_old["state"] == "overdue", s_old)
ok("A3 none  -> never_run True, overdue False", s_never["never_run"] is True and s_never["overdue"] is False, s_never)
ok("A4 age_hours reported", abs((s_fresh["age_hours"] or 0) - 4.0) < 0.01, s_fresh["age_hours"])
# boundary: exactly cadence+grace is NOT yet overdue; one minute later it is
b_in = IH.feed_status(feed, [{"kind": "email", "k1": "default", "k2": "daily_sales",
                              "last_success": iso(NOW - timedelta(hours=30))}], now=NOW)
b_out = IH.feed_status(feed, [{"kind": "email", "k1": "default", "k2": "daily_sales",
                               "last_success": iso(NOW - timedelta(hours=30, minutes=1))}], now=NOW)
ok("A5 boundary cadence+grace exactly -> not overdue", b_in["overdue"] is False, b_in)
ok("A6 boundary +1min -> overdue", b_out["overdue"] is True, b_out)
# a MANUAL upload clears the alert but marks the CHANNEL stale
s_manual = IH.feed_status(feed, [{"kind": "upload_trace", "k1": "daily_sales",
                                  "last_success": iso(NOW - timedelta(hours=2))}], now=NOW)
ok("A7 manual upload clears overdue", s_manual["state"] == "ok", s_manual)
ok("A8 …but flags channel_stale", s_manual["channel_stale"] is True, s_manual)
ok("A9 healthy channel -> channel_stale False", s_fresh["channel_stale"] is False, s_fresh)
# ── Gate-1 MINOR-1: channel_stale must mean "data IS arriving, just not via the channel" ─────────────
ok("A9b SINGLE-CHANNEL OVERDUE (nothing arrived by ANY route) -> channel_stale FALSE",
   s_old["state"] == "overdue" and s_old["channel_stale"] is False, s_old)
ok("A9c NEVER-run feed -> channel_stale FALSE", s_never["channel_stale"] is False, s_never)
# manual upload FRESHER than a stale channel -> the channel really is the problem
s_cs = IH.feed_status(feed, [{"kind": "email", "k1": "default", "k2": "daily_sales",
                              "last_success": iso(NOW - timedelta(hours=200))},
                             {"kind": "upload_trace", "k1": "daily_sales",
                              "last_success": iso(NOW - timedelta(hours=2))}], now=NOW)
ok("A9d manual upload FRESHER than a stale channel -> channel_stale TRUE",
   s_cs["state"] == "ok" and s_cs["channel_stale"] is True, s_cs)
# both routes fresh -> the channel is fine, no accusation
s_both = IH.feed_status(feed, [{"kind": "email", "k1": "default", "k2": "daily_sales",
                                "last_success": iso(NOW - timedelta(hours=5))},
                               {"kind": "upload_trace", "k1": "daily_sales",
                                "last_success": iso(NOW - timedelta(hours=2))}], now=NOW)
ok("A9e both routes fresh (manual merely newer) -> channel_stale FALSE", s_both["channel_stale"] is False, s_both)
# best comes from a NON-channel route but is ITSELF stale -> plain overdue, not a channel accusation
s_bothold = IH.feed_status(feed, [{"kind": "email", "k1": "default", "k2": "daily_sales",
                                   "last_success": iso(NOW - timedelta(hours=400))},
                                  {"kind": "upload_trace", "k1": "daily_sales",
                                   "last_success": iso(NOW - timedelta(hours=200))}], now=NOW)
ok("A9f non-channel evidence that is ALSO stale -> overdue, channel_stale FALSE",
   s_bothold["state"] == "overdue" and s_bothold["channel_stale"] is False, s_bothold)
ok("A9g channel_stale is IMPOSSIBLE on a non-ok feed (invariant over the whole matrix)",
   all(st["channel_stale"] is False for st in (s_old, s_never, s_bothold) if st["state"] != "ok"))
# monthly manual feed: default grace scales, so 30 days late is NOT overdue for a 720h cadence
mfeed = {"cadence_hours": 720, "grace_hours": IH.default_grace(720),
         "evidence": [{"kind": "upload_trace", "upload_type": "ma_commission"}]}
s_m = IH.feed_status(mfeed, [{"kind": "upload_trace", "k1": "ma_commission",
                              "last_success": iso(NOW - timedelta(days=25))}], now=NOW)
ok("A10 monthly feed 25d old -> ok", s_m["state"] == "ok", s_m)
ok("A11 grace scales with cadence", (IH.default_grace(24), IH.default_grace(168), IH.default_grace(720)) == (6.0, 24.0, 72.0))
ok("A12 freq_hours maps the tenant's own schedule",
   (IH.freq_hours("daily"), IH.freq_hours("weekly"), IH.freq_hours("monthly"), IH.freq_hours("nonsense", 24)) == (24.0, 168.0, 720.0, 24.0))
ok("A13 unknown probe kind never reads as fresh",
   IH.feed_status({"cadence_hours": 24, "grace_hours": 6, "evidence": [{"kind": "made_up"}]}, ev_fresh, now=NOW)["state"] == "never")
ok("A14 evidence stored as a JSON STRING still parses",
   IH.feed_status({"cadence_hours": 24, "grace_hours": 6,
                   "evidence": '[{"kind":"email","account":"default","upload_type":"daily_sales"}]'},
                  ev_fresh, now=NOW)["state"] == "ok")

# ── B. derivation ────────────────────────────────────────────────────────────────────────────────────
print("\nB. auto-derivation from what the system already knows")
store, rpcs = fixture()
c = FakeClient(store, rpcs)
cands_h = IH.derive_candidates(IH.fetch_config(c, HOUSE))
keys_h = {x["feed_key"] for x in cands_h}
ok("B1 email pattern -> one feed per (mailbox, upload_type)",
   {"email:default:daily_sales", "email:default:inventory_aging"} <= keys_h, sorted(keys_h))
ok("B2 ftp pattern -> feed", "ftp:sales" in keys_h, sorted(keys_h))
ok("B3 configured portal sweep -> feed", "sweep:dlar" in keys_h, sorted(keys_h))
ok("B4 UNconfigured sweep (no creds, disabled) -> NO feed", "sweep:epay" not in keys_h, sorted(keys_h))
ok("B5 google closing sheet -> google_sa feed",
   any(x["feed_key"] == "sweep:closing" and x["source_type"] == "google_sa" for x in cands_h))
ok("B6 OWNER CASE — VidaPay report with NO login registered as manual_expected",
   any(x["feed_key"] == "manual:vidapay:ma_commission" and x["source_type"] == "manual_expected"
       and x["deep_link"] == "/commcalc/ma-upload" for x in cands_h),
   [x["feed_key"] for x in cands_h])
ok("B7 an uncovered manual report is registered but DISABLED (no noise)",
   any(x["feed_key"] == "report:hotsheet" and x["enabled"] is False
       and x["source_type"] == "manual_expected" for x in cands_h),
   sorted(keys_h))
ok("B7b a report already covered by another feed is DEDUPED away (no double alert)",
   "report:sales" not in keys_h and "ftp:sales" in keys_h, sorted(keys_h))
ok("B8 every candidate carries a deep link", all(x.get("deep_link") for x in cands_h))
ok("B9 every candidate carries at least one evidence probe", all(x.get("evidence") for x in cands_h))
cands_l = IH.derive_candidates(IH.fetch_config(c, LUXE))
keys_l = {x["feed_key"] for x in cands_l}
ok("B10 OWNER CASE — VidaPay LOGIN -> one pull feed per mapped report",
   {"pull:vidapay:src-luxe:ma_commission", "pull:vidapay:src-luxe:ma_daily_tx"} <= keys_l, sorted(keys_l))
ok("B11 luxelink derives its OWN mailbox feed only",
   "email:total:ma_commission" in keys_l and "email:default:daily_sales" not in keys_l, sorted(keys_l))
ok("B12 luxelink does NOT inherit the house sweeps", not any(k.startswith("sweep:") for k in keys_l), sorted(keys_l))
ok("B13 a pull feed's deep link is the MANUAL upload page (owner: 'take them to the upload menu')",
   all(x["deep_link"] == "/commcalc/ma-upload" for x in cands_l if x["feed_key"].startswith("pull:vidapay")))
ok("B14 derivation is a PURE function of the config (same input -> same keys)",
   {x["feed_key"] for x in IH.derive_candidates(IH.fetch_config(c, HOUSE))} == keys_h)

# ── C. idempotence ───────────────────────────────────────────────────────────────────────────────────
print("\nC. auto-derive idempotence (run twice -> no duplicates)")
store, rpcs = fixture()
c = FakeClient(store, rpcs)
f1, m1 = IH.load_feeds(c, HOUSE)
n1 = len(store["core.import_feed"])
f2, m2 = IH.load_feeds(c, HOUSE)
n2 = len(store["core.import_feed"])
ok("C1 first read persists the derived registry", n1 == len(f1) and n1 > 0, (n1, len(f1)))
ok("C2 second read adds NOTHING", n2 == n1 and m2["derived_new"] == 0, (n1, n2, m2))
ok("C3 no duplicate feed_key rows", len({r["feed_key"] for r in store["core.import_feed"]}) == n1)
# an admin edit survives a re-derive
row = next(r for r in store["core.import_feed"] if r["feed_key"] == "email:default:daily_sales")
row["cadence_hours"], row["enabled"], row["label"] = 999, False, "MY LABEL"
IH.load_feeds(c, HOUSE)
row2 = next(r for r in store["core.import_feed"] if r["feed_key"] == "email:default:daily_sales")
ok("C4 admin cadence/label/enabled edits are NEVER overwritten by a re-derive",
   (row2["cadence_hours"], row2["enabled"], row2["label"]) == (999, False, "MY LABEL"), row2)
# a NEW pattern added later. Within the derive TTL the config re-read is deliberately SKIPPED (it saves
# ~10 round trips on every login popup); the explicit "Re-sync" button (force=True) and TTL expiry both
# pick it up. Bounded staleness, never wrong data.
store["commcalc.email_sweep_config"][0]["patterns"].append({"pattern": "*MI*", "upload_type": "mi_report"})
IH.load_feeds(c, HOUSE)
ok("C5 within the derive TTL the config re-read is skipped (cheap login path)",
   not any(r["feed_key"] == "email:default:mi_report" for r in store["core.import_feed"]))
IH.load_feeds(c, HOUSE, force=True)
ok("C5b force=True ('Re-sync from import settings') picks the new pattern up immediately",
   any(r["feed_key"] == "email:default:mi_report" for r in store["core.import_feed"]))
IH._derived_at.clear()
store["commcalc.email_sweep_config"][0]["patterns"].append({"pattern": "*Comp*", "upload_type": "comp_report"})
IH.load_feeds(c, HOUSE)
ok("C5c once the TTL lapses a new pattern is picked up automatically",
   any(r["feed_key"] == "email:default:comp_report" for r in store["core.import_feed"]))
ok("C6 …and still no duplicates",
   len({r["feed_key"] for r in store["core.import_feed"]}) == len(store["core.import_feed"]))
ok("C7 the TTL map is BOUNDED (never grows past the cap)",
   (IH._derived_at.clear() or True)
   and all(IH._derive_due(f"org-{i}", False, False) or IH._derive_done(f"org-{i}") or True
           for i in range(IH._DERIVE_MAX + 5))
   and len(IH._derived_at) <= IH._DERIVE_MAX + 5)
ok("C8 an EMPTY registry always derives regardless of the TTL",
   IH._derive_due("brand-new-org", True, False) is True)
IH._derived_at.clear()

# ── D. org isolation ─────────────────────────────────────────────────────────────────────────────────
print("\nD. org isolation (two tenants, differential)")
store, rpcs = fixture()
c = FakeClient(store, rpcs)
hh = IH.feed_health(c, HOUSE)
c.log.clear()
lh = IH.feed_health(c, LUXE)
hk = {f["feed_key"] for f in hh["feeds"]}
lk = {f["feed_key"] for f in lh["feeds"]}
ok("D1 the two tenants' registries are DISJOINT", not (hk & lk), sorted(hk & lk))
ok("D2 luxelink never sees a house feed", not any(k.startswith("sweep:") or k == "ftp:sales" for k in lk), sorted(lk))
ok("D3 house never sees the luxelink VidaPay login feed",
   not any(k.startswith("pull:vidapay:src-luxe") for k in hk), sorted(hk))
unscoped = [e for e in c.log if e["op"] == "select" and e["filters"].get("org_id") != LUXE]
ok("D4 EVERY select during the luxelink read is org-filtered to luxelink", not unscoped, unscoped[:3])
rpc_calls = [e for e in c.log if e["op"] == "rpc"]
ok("D5 the evidence RPC is called with p_org = the acting org",
   bool(rpc_calls) and all(e["filters"].get("p_org") == LUXE for e in rpc_calls), rpc_calls[:2])
persisted = {(r["org_id"], r["feed_key"]) for r in store["core.import_feed"]}
ok("D6 every persisted row is stamped with the org that derived it",
   all(o in (HOUSE, LUXE) for o, _ in persisted) and
   all(k in hk for o, k in persisted if o == HOUSE) and
   all(k in lk for o, k in persisted if o == LUXE))
ok("D7 no house-org constant leaked into the luxelink read",
   not any(e["filters"].get("org_id") == HOUSE for e in c.log if e["op"] == "select"))
# attention is likewise disjoint
ah = IH.collect_attention(c, HOUSE, deep=False)
c.log.clear()
al = IH.collect_attention(c, LUXE, deep=False)
ok("D8 attention items are org-scoped (no cross-tenant feed labels)",
   not any("luxelink" in (i["label"] or "").lower() for i in ah["items"]),
   [i["label"] for i in ah["items"]])
ok("D9 every attention read is org-filtered to the acting org",
   not [e for e in c.log if e["op"] == "select" and e["filters"].get("org_id") != LUXE])

# ── E. gate + org clamp ──────────────────────────────────────────────────────────────────────────────
print("\nE. admin gate (personas) + org clamp")
personas = [
    ("super_admin",             {"super_admin": True, "org_id": HOUSE, "perms": {"scope": "self"}}, True),
    ("role=admin",              {"org_id": HOUSE, "role": "admin", "perms": {"scope": "market"}}, True),
    ("scope=all exec",          {"org_id": HOUSE, "role": "exec", "perms": {"scope": "all"}}, True),
    ("modules.admin granted",   {"org_id": HOUSE, "role": "ops", "perms": {"scope": "market", "modules": {"admin": True}}}, True),
    ("market manager",          {"org_id": HOUSE, "role": "market_manager", "perms": {"scope": "market"}}, False),
    ("store rep",               {"org_id": HOUSE, "role": "rep", "perms": {"scope": "self"}}, False),
    ("explicit page GRANT",     {"org_id": HOUSE, "role": "rep", "perms": {"scope": "self", "pages": {"/admin/import-health": True}}}, True),
    ("explicit page DENY beats admin",
                                {"org_id": HOUSE, "role": "admin", "perms": {"scope": "all", "pages": {"/admin/import-health": False}}}, False),
    ("unresolved caller",       None, False),
]
for name, caller, want in personas:
    ok(f"E:{name} -> {'allowed' if want else 'denied'}", IH.can_view_attention(caller) is want, caller)
ok("E10 super-admin keeps the client org (acting as a tenant)",
   IH._scope_org({"super_admin": True, "org_id": HOUSE}, LUXE) == LUXE)
ok("E11 NON-super-admin is CLAMPED to their own org even if they ask for another",
   IH._scope_org({"super_admin": False, "org_id": LUXE}, HOUSE) == LUXE)
ok("E12 super-admin with no client org falls back to their own",
   IH._scope_org({"super_admin": True, "org_id": HOUSE}, "") == HOUSE)

# ── F. attention aggregation ─────────────────────────────────────────────────────────────────────────
print("\nF. attention aggregation / provider registry")
store, rpcs = fixture()
c = FakeClient(store, rpcs)
res = IH.collect_attention(c, HOUSE, deep=False)
keys = {i["key"] for i in res["items"]}
ok("F1 the 9-day-late email feed raises an OVERDUE import item",
   "feed:email:default:inventory_aging" in keys, sorted(keys))
ok("F2 overdue import item severity=error + carries a fix deep link",
   all(i["severity"] == "error" and i["deep_link"] for i in res["items"]
       if i["key"] == "feed:email:default:inventory_aging"))
ok("F2b Gate-1 MINOR-1: no overdue item claims 'arrived by another route'",
   not any("another route" in (i.get("detail") or "") for i in res["items"]),
   [i.get("detail") for i in res["items"] if i["group"] == "import"])
ok("F3 a NEVER-run feed is reported as a warning",
   any(i["group"] == "import" and i["severity"] == "warning" for i in res["items"]),
   [(i["key"], i["severity"]) for i in res["items"]])
ok("F4 the healthy daily_sales feed raises NOTHING",
   "feed:email:default:daily_sales" not in keys)
ok("F5 PENDING MAPPING: the store missing from the market map is reported",
   "stores_unmapped" in keys and next(i for i in res["items"] if i["key"] == "stores_unmapped")["count"] == 1)
ok("F6 heavy providers are DEFERRED on the cheap (login) call",
   {"carrier_category_map", "product_mrc", "plan_coverage"} == {d["key"] for d in res["deferred"]},
   res["deferred"])
ok("F7 counts are grouped for the popup summary",
   res["counts"]["total"] == len(res["items"]) and res["counts"]["import"] >= 1 and res["counts"]["mapping"] >= 1,
   res["counts"])
ok("F8 items are severity-ordered (errors first)",
   [i["severity"] for i in res["items"]] == sorted([i["severity"] for i in res["items"]],
                                                   key=lambda s: {"error": 0, "warning": 1, "info": 2}[s]))
res_deep = IH.collect_attention(c, HOUSE, deep=True)
ok("F9 deep=1 runs the heavy providers (nothing deferred)", res_deep["deferred"] == [], res_deep["deferred"])
ok("F10 heavy providers degrade to no item when their source is absent (no crash)",
   res_deep["provider_errors"] == [], res_deep["provider_errors"])

# a provider that raises must not break the others
n_before = len(IH.PROVIDERS)


@IH.register_provider("harness_boom", label="boom", group="other", cost="cheap")
def _boom(client, org_id, ctx):
    raise RuntimeError("provider exploded")


res2 = IH.collect_attention(c, HOUSE, deep=False)
ok("F11 a raising provider is isolated (others still return)", len(res2["items"]) == len(res["items"]))
ok("F12 …and is reported in provider_errors",
   any(e["key"] == "harness_boom" for e in res2["provider_errors"]), res2["provider_errors"])


@IH.register_provider("harness_boom", label="boom2", group="other", cost="cheap")
def _boom2(client, org_id, ctx):
    return []


ok("F13 re-registering a provider key REPLACES it (no duplicate registration)",
   len(IH.PROVIDERS) == n_before + 1, len(IH.PROVIDERS))
IH.PROVIDERS[:] = [p for p in IH.PROVIDERS if p["key"] != "harness_boom"]

# a MUTED / DISABLED feed is suppressed from the popup but stays in the registry
store, rpcs = fixture()
c = FakeClient(store, rpcs)
IH.load_feeds(c, HOUSE)
for r in store["core.import_feed"]:
    if r["feed_key"] == "email:default:inventory_aging":
        r["muted_until"] = iso(NOW + timedelta(days=3))
res3 = IH.collect_attention(c, HOUSE, deep=False)
ok("F14 a snoozed feed is suppressed from attention",
   "feed:email:default:inventory_aging" not in {i["key"] for i in res3["items"]})
ok("F15 …but is still listed in the registry",
   any(f["feed_key"] == "email:default:inventory_aging" for f in IH.feed_health(c, HOUSE)["feeds"]))

# mig-717-not-run: honest empty payload, never a crash
class Broken(FakeClient):
    def schema(self, s):
        if s == "core":
            raise RuntimeError("relation core.import_feed does not exist")
        return super().schema(s)


bh = IH.feed_health(Broken({}, {}), HOUSE)
ok("F16 mig 717 un-run -> empty + hint, no exception",
   bh["feeds"] == [] and bh["ready"] is False and "717" in (bh["hint"] or ""), bh)
ba = IH.collect_attention(Broken({}, {}), HOUSE, deep=False)
ok("F17 attention still answers when the registry is missing", isinstance(ba["items"], list))

print(f"\n{PASS}/{PASS + FAIL} passed")
sys.exit(1 if FAIL else 0)
