"""Proof for agent/commission/promotion-dedup — the luxelink July 2026 feed→raw_sales
compounding-duplication fix. Pure unit tests over the REAL router functions; NO live DB.

Run:  cd backend && python3 scratchpad/promotion_dedup_proof.py

Covers the four required cases + an end-to-end promotion write:
 (a) content-dedupe collapses duplicated existing rows, counts dupes_dropped, keeps first, stable order
 (b) identical-content rows on DIFFERENT days are both kept; two genuinely-identical line items collapse
 (c) _merge_days_richer + _sales_rows_union swap a degraded primary day at the 50%/50-row threshold and
     report it in meta['richer_days']; boundary + floor cases; filled-days behaviour preserved
 (d) mutex: a second concurrent promotion for the same (org, period) SKIPS (same-thread + threaded)
 (e) end-to-end _promote_feed_to_raw_sales: deduped write happens ONCE, dupes_dropped + heal note surfaced
"""
import sys, os, threading, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.modules.commcalc import router  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


# ── Fake Supabase client (chainable, in-memory) ─────────────────────────────────────────────────
class _Resp:
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, client, table):
        self.c = client
        self.table = table
        self.op = "select"
        self.count_mode = False
        self._range = None
        self._rows = None

    def select(self, *a, **kw):
        self.op = "select"
        if kw.get("count"):
            self.count_mode = True
        return self

    def insert(self, rows):
        self.op = "insert"
        self._rows = rows
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def neq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        if self.op == "select" and not self.count_mode and self.c._hook:
            self.c._hook()
        if self.op == "insert":
            self.c.inserted.setdefault(self.table, []).extend(self._rows)
            return _Resp(data=self._rows)
        if self.op == "delete":
            self.c.deleted.append(self.table)
            return _Resp(data=[])
        rows = self.c.tables.get(self.table, [])
        if self.count_mode:
            return _Resp(count=sum(1 for r in rows if str(r.get("category") or "") != ""))
        if self._range is not None:
            s, e = self._range
            return _Resp(data=rows[s:e + 1])
        return _Resp(data=list(rows))


class FakeClient:
    def __init__(self, tables):
        self.tables = tables
        self.inserted = {}
        self.deleted = []
        self._hook = None

    def schema(self, _s):
        return self

    def table(self, t):
        return _Query(self, t)


def row(tid, day, price, cat="Accessory", rid=None, **extra):
    r = {"trans_id": tid, "trans_date": day, "ext_price": price, "category": cat,
         "org_id": "o", "period": "July 2026"}
    if rid is not None:
        r["id"] = rid
        r["created_at"] = f"2026-07-16T00:00:0{rid % 10}Z"
    r.update(extra)
    return r


# ── (a) content-dedupe collapses duplicated existing rows ───────────────────────────────────────
print("(a) _dedupe_rows — duplicated existing rows collapse, count + first kept, order stable")
dupA1 = row("T4", "2026-07-04", 100, rid=1)
dupA2 = row("T4", "2026-07-04", 100, rid=2)   # same content, diff id/created_at
dupA3 = row("T4", "2026-07-04", 100, rid=3)
distB = row("T4", "2026-07-04", 50, rid=4)    # same trans_id/day, DIFFERENT price → distinct content
distC = row("T5", "2026-07-05", 20, rid=5)
deduped, dropped = router._dedupe_rows([dupA1, dupA2, dupA3, distB, distC])
check("dupes_dropped == 2", dropped == 2)
check("kept 3 distinct rows", len(deduped) == 3)
check("first occurrence kept (id=1)", deduped[0].get("id") == 1)
check("distinct-by-price row survived", any(r.get("id") == 4 for r in deduped))
check("order preserved (T4=100, T4=50, T5=20)",
      [r.get("id") for r in deduped] == [1, 4, 5])
check("re-dedupe is idempotent", router._dedupe_rows(deduped)[1] == 0)

# id/created_at are excluded from the signature (so DB-assigned ids can't defeat the dedupe)
sig1 = router._row_content_sig(dupA1)
sig2 = router._row_content_sig(dupA2)
check("signature ignores id/created_at (dup rows share a signature)", sig1 == sig2)
# None vs '' normalize the same; 5 vs '5' normalize the same
check("None and '' normalize equal in signature",
      router._row_content_sig({"x": None}) == router._row_content_sig({"x": ""}))
check("int and str normalize equal in signature",
      router._row_content_sig({"x": 5}) == router._row_content_sig({"x": "5"}))


# ── (b) identical-content rows on DIFFERENT days both kept; identical line items collapse ────────
print("(b) different-day rows kept; genuinely-identical line items collapse (documented tradeoff)")
d1 = row("T9", "2026-07-09", 10, rid=10)
d2 = row("T9", "2026-07-10", 10, rid=11)       # identical EXCEPT trans_date → different day → distinct
kept, drop2 = router._dedupe_rows([d1, d2])
check("two rows differing only by day both kept", len(kept) == 2 and drop2 == 0)

line1 = row("TX", "2026-07-09", 10, rid=20)
line2 = row("TX", "2026-07-09", 10, rid=21)     # a real ticket's two identical line items
collapsed, drop3 = router._dedupe_rows([line1, line2])
check("two identical line items collapse to 1 (accepted; re-upload restores truth)",
      len(collapsed) == 1 and drop3 == 1)


# ── (c) day-pick swap at the 50%/50-row threshold ───────────────────────────────────────────────
print("(c) _merge_days_richer — degraded primary day swapped to richer other; boundary + floor")

def mk(day, n, price=10, tid_prefix="p"):
    return [row(f"{tid_prefix}{day}-{i}", day, price) for i in range(n)]

# July 8: primary 20 vs other 60 → other>=50 and 20 < 0.5*60=30 → SWAP
prows = mk("2026-07-07", 60) + mk("2026-07-08", 20, tid_prefix="pf")
orows = mk("2026-07-07", 55, tid_prefix="o") + mk("2026-07-08", 60, tid_prefix="of") + mk("2026-07-09", 15, tid_prefix="og")
merged, swapped, filled = router._merge_days_richer(prows, orows, lambda r: str(r.get("trans_date") or "")[:10])
check("richer/swapped day == ['2026-07-08']", swapped == ["2026-07-08"])
check("filled day (primary lacks) == ['2026-07-09']", filled == ["2026-07-09"])
check("July 7 NOT swapped (primary healthy)", "2026-07-07" not in swapped)
check("merged = keptPrimary(J7 60) + other(J8 60 + J9 15) = 135", len(merged) == 135)
check("swapped day now carries the OTHER source's rows (tids start 'of')",
      all(r.get("trans_id", "").startswith("of") for r in merged if str(r.get("trans_date"))[:10] == "2026-07-08"))
check("filled day carries the other source's rows (tids start 'og')",
      all(r.get("trans_id", "").startswith("og") for r in merged if str(r.get("trans_date"))[:10] == "2026-07-09"))

# boundary: primary exactly at 50% is NOT swapped (needs strictly < ratio*other)
p_eq = mk("2026-07-08", 50)
o_eq = mk("2026-07-08", 100, tid_prefix="o")
_, sw_eq, _ = router._merge_days_richer(p_eq, o_eq, lambda r: str(r.get("trans_date"))[:10])
check("primary at exactly 50% → NOT swapped", sw_eq == [])
p_lt = mk("2026-07-08", 49)
_, sw_lt, _ = router._merge_days_richer(p_lt, o_eq, lambda r: str(r.get("trans_date"))[:10])
check("primary just under 50% → swapped", sw_lt == ["2026-07-08"])

# floor: other under 50 rows never triggers a swap however tiny the primary
p_tiny = mk("2026-07-08", 2)
o_small = mk("2026-07-08", 40, tid_prefix="o")
_, sw_floor, _ = router._merge_days_richer(p_tiny, o_small, lambda r: str(r.get("trans_date"))[:10])
check("other below 50-row floor → NOT swapped (noise-protected)", sw_floor == [])

# blank-day primary rows are always kept
p_blank = [row("B1", "", 5)] + mk("2026-07-08", 60)
o_blank = mk("2026-07-08", 60, tid_prefix="o")
mrg_blank, _, _ = router._merge_days_richer(p_blank, o_blank, lambda r: str(r.get("trans_date") or "")[:10])
check("blank-day primary row retained", any(r.get("trans_id") == "B1" for r in mrg_blank))

# empty primary → whole other source, all days 'filled', none swapped
mrg_e, sw_e, fl_e = router._merge_days_richer([], orows, lambda r: str(r.get("trans_date") or "")[:10])
check("empty primary → all other rows returned", len(mrg_e) == len(orows))
check("empty primary → swapped == []", sw_e == [])

# (c-integration) _sales_rows_union surfaces richer_days in meta (open month July 2026)
# UPDATED 2026-07-18 (agent/commission/sales-capture-fix): `_sales_rows_union` now dedups a completeness
# backfill by (store-cell, trans_id). On the SHARED day 07-07 the feed and raw hold copies of the SAME
# transactions, so raw's 07-07 uses the SAME "p" trans_id prefix as the feed (realistic — a POS
# transaction carries one B2B Soft id in both tables). The feed leads that cell and its 55 shared raw
# copies are NOT re-added → NO double-show (the honest guarantee). 07-08 still swaps to the richer raw
# copy; 07-09 is still filled from raw. Nothing is a genuine raw-ONLY transaction here, so
# completeness_rows == 0. (The backfill of a genuine raw-only txn is proven in
# sales_capture_completeness_proof.py.)
uni_tables = {
    "daily_sales_feed": mk("2026-07-07", 60) + mk("2026-07-08", 20, tid_prefix="pf"),
    "raw_sales": mk("2026-07-07", 55) + mk("2026-07-08", 60, tid_prefix="of") + mk("2026-07-09", 15, tid_prefix="og"),
}
uni_rows, uni_meta = router._sales_rows_union(FakeClient(uni_tables), "o", "July 2026")
check("union meta.primary == daily_sales_feed (open month)", uni_meta["primary"] == "daily_sales_feed")
check("union meta.richer_days == ['2026-07-08']", uni_meta["richer_days"] == ["2026-07-08"])
check("union meta.filled_days == ['2026-07-09']", uni_meta["filled_days"] == ["2026-07-09"])
check("union shown_rows == 135 (feed 07-07 60 + swapped raw 07-08 60 + filled raw 07-09 15)",
      uni_meta["shown_rows"] == 135 and len(uni_rows) == 135)
check("union completeness_rows == 0 (07-07 tids shared → no double-show)", uni_meta["completeness_rows"] == 0)
check("union never-raises on a read error (patched to throw) → degrades",
      True)  # exercised below

# never-raises: a read that throws degrades to the other source
class _ThrowClient(FakeClient):
    def table(self, t):
        q = _Query(self, t)
        if t == "raw_sales":
            def _boom():
                raise RuntimeError("column drift")
            self._hook = _boom
        return q
_tc = _ThrowClient({"daily_sales_feed": mk("2026-07-08", 3), "raw_sales": mk("2026-07-08", 60, tid_prefix="o")})
# note: hook fires on daily_sales_feed too; use a table-specific throw instead
class _ThrowRaw(FakeClient):
    def table(self, t):
        q = _Query(self, t)
        orig = q.execute
        if t == "raw_sales":
            def _ex():
                raise RuntimeError("column drift")
            q.execute = _ex
        return q
tr_rows, tr_meta = router._sales_rows_union(_ThrowRaw({"daily_sales_feed": mk("2026-07-08", 3), "raw_sales": mk("2026-07-08", 60, tid_prefix="o")}), "o", "July 2026")
check("union degrades (no raise) when one source read throws", tr_meta["other_rows"] == 0 and len(tr_rows) == 3)


# ── stub the trace so no network + capture heal note ────────────────────────────────────────────
_traces = []
router._write_upload_trace = lambda org_id, **kw: _traces.append((org_id, kw))


# ── (d) mutex — second concurrent promotion for same (org, period) skips ─────────────────────────
print("(d) promotion mutex — concurrent run for same (org, period) SKIPS, does not double-write")

# same-thread deterministic: hold the lock, then a real promotion must skip
lk = router._promo_lock_for("org-mtx", "July 2026")
got = lk.acquire(blocking=False)
check("acquired the (org, period) lock", got)
skip_client = FakeClient({"daily_sales_feed": [row("T1", "2026-07-01", 10)], "raw_sales": [row("T9", "2026-07-09", 5, rid=1)]})
res_skip = router._promote_feed_to_raw_sales(skip_client, "org-mtx", "July 2026", dry_run=False)
check("concurrent promotion returns skipped 'already running'",
      "already running" in (res_skip.get("skipped") or ""))
check("skipped run performed NO delete/insert", skip_client.deleted == [] and skip_client.inserted == {})
check("skipped run did NOT write (no 'written')", res_skip.get("written") is None)
lk.release()
# after release, a real run proceeds (no longer skipped)
res_after = router._promote_feed_to_raw_sales(skip_client, "org-mtx", "July 2026", dry_run=False, force=True)
check("after release the promotion runs (not skipped)", "already running" not in (res_after.get("skipped") or ""))

# dry_run is NEVER mutex-gated (read-only preview must not block a real run)
lk2 = router._promo_lock_for("org-dry", "July 2026")
lk2.acquire(blocking=False)
res_dry = router._promote_feed_to_raw_sales(skip_client, "org-dry", "July 2026", dry_run=True)
check("dry_run bypasses the mutex (not skipped for concurrency)",
      "already running" not in (res_dry.get("skipped") or ""))
lk2.release()

# threaded: thread-1 holds the lock while blocked inside its read; thread-2 must skip
blocked = {"done": False}
started, release = threading.Event(), threading.Event()

def _blocking_hook():
    if not blocked["done"]:
        blocked["done"] = True
        started.set()
        release.wait(timeout=5)

t1_client = FakeClient({"daily_sales_feed": [], "raw_sales": []})
t1_client._hook = _blocking_hook
t1_out = []
t1 = threading.Thread(target=lambda: t1_out.append(
    router._promote_feed_to_raw_sales(t1_client, "org-thr", "July 2026", dry_run=False)))
t1.start()
check("thread-1 entered the read holding the lock", started.wait(timeout=5))
t2_client = FakeClient({"daily_sales_feed": [row("T1", "2026-07-01", 1)], "raw_sales": [row("T9", "2026-07-09", 1, rid=1)]})
res_t2 = router._promote_feed_to_raw_sales(t2_client, "org-thr", "July 2026", dry_run=False)
check("thread-2 concurrent run SKIPS", "already running" in (res_t2.get("skipped") or ""))
check("thread-2 wrote nothing", t2_client.deleted == [] and t2_client.inserted == {})
release.set()
t1.join(timeout=5)
check("thread-1 completed and released", not t1.is_alive())


# ── (e) end-to-end promotion write — deduped, once, note surfaced ────────────────────────────────
print("(e) _promote_feed_to_raw_sales end-to-end — deduped write happens once, note surfaced")
e2e_feed = [row("T1", "2026-07-01", 30), row("T2", "2026-07-02", 40), row("T3", "2026-07-03", 50)]
# existing raw_sales rows are read scoped to the org, so in production they already carry the right
# org_id (only feed rows get re-stamped). Mirror that here.
e2e_existing = [
    row("T4", "2026-07-04", 100, rid=1, org_id="org-e2e"),   # bloat: 3 identical copies (feed-less day 4)
    row("T4", "2026-07-04", 100, rid=2, org_id="org-e2e"),
    row("T4", "2026-07-04", 100, rid=3, org_id="org-e2e"),
    row("T4", "2026-07-04", 50, rid=4, org_id="org-e2e"),    # distinct real line on same ticket (price differs)
    row("T5", "2026-07-05", 20, rid=5, org_id="org-e2e"),    # feed-less day 5
]
e2e = FakeClient({"daily_sales_feed": e2e_feed, "raw_sales": e2e_existing})
_traces.clear()
summ = router._promote_feed_to_raw_sales(e2e, "org-e2e", "July 2026", dry_run=False, force=True)
check("summary.dupes_dropped == 2", summ.get("dupes_dropped") == 2)
# new_rows = 3 feed + (T4a, T4b, T5) deduped monthly = 6
check("summary.written == 6 (3 feed + 3 deduped monthly)", summ.get("written") == 6)
check("raw_sales deleted exactly once before insert", e2e.deleted.count("raw_sales") == 1)
inserted = e2e.inserted.get("raw_sales", [])
check("inserted exactly 6 rows", len(inserted) == 6)
t4_100 = [r for r in inserted if r.get("trans_id") == "T4" and float(r.get("ext_price")) == 100.0]
check("the tripled T4/$100 line collapsed to ONE in the write", len(t4_100) == 1)
check("the distinct T4/$50 line survived", any(r.get("trans_id") == "T4" and float(r.get("ext_price")) == 50.0 for r in inserted))
check("inserted rows all re-stamped org_id", all(r.get("org_id") == "org-e2e" for r in inserted))
heal_notes = [kw.get("result", {}).get("note") for _o, kw in _traces if kw.get("result")]
check("upload_trace note surfaces the heal ('healed 2 duplicate ...')",
      any(n and "healed 2 duplicate" in n for n in heal_notes))

# a clean promotion (no dupes) reports dupes_dropped == 0 and no heal note
clean = FakeClient({"daily_sales_feed": e2e_feed, "raw_sales": [row("T5", "2026-07-05", 20, rid=9)]})
_traces.clear()
summ_clean = router._promote_feed_to_raw_sales(clean, "org-clean", "July 2026", dry_run=False, force=True)
check("clean promotion → dupes_dropped == 0", summ_clean.get("dupes_dropped") == 0)
check("clean promotion → no heal note", all(not (kw.get("result", {}).get("note") or "").startswith("healed")
                                            for _o, kw in _traces if kw.get("result")))


print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
