"""Harness — an upload must replace ONLY its own slice.

Reproduces the real 2026-08-11 incident before proving the fix. Measured from prod `upload_log` vs
surviving rows:

  • 2026-07-29 `MA Daily Tx SubMA.xls` saved 16,409 July rows — July now holds 4,902, Novawave only.
  • 2026-08-04 `MA Daily Tx SubMA (1).xls` saved 3,417 (Jul+Aug, Luxelink) — only 1,903 survive
    (Aug 1–3); the July half was destroyed 2026-08-11 by `MA Daily Tx SubMA Nova July.xls`.
  • 2026-08-08 22:00 file (2) saved 3,006 August rows — destroyed 16 MINUTES later by file (3),
    SAME company. That one is why the date range is part of the scope and not just the partition.
  • `raw_sales` June holds 6 of 20 stores — same fingerprint on a second table.

The fake Supabase client applies eq/in_/gte/lte/is_ for real, so a filter that does not narrow fails
the test instead of silently passing.
"""
import sys, types, os

sys.path.insert(0, os.path.dirname(__file__))
PASS, FAIL = [], []


def ok(cond, what):
    (PASS if cond else FAIL).append(what)
    print(("  PASS " if cond else "  FAIL ") + what)


# ── fake supabase ──────────────────────────────────────────────────────────────────────────────────
class _Q:
    def __init__(self, store, table, op):
        self.s, self.t, self.op, self.f = store, table, op, []

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def range(self, lo, hi):
        self._range = (lo, hi)
        return self

    def eq(self, c, v): self.f.append(("eq", c, v)); return self
    def in_(self, c, v): self.f.append(("in", c, list(v))); return self
    def gte(self, c, v): self.f.append(("gte", c, v)); return self
    def lte(self, c, v): self.f.append(("lte", c, v)); return self
    def is_(self, c, v): self.f.append(("is", c, v)); return self

    def _match(self, r):
        for kind, c, v in self.f:
            got = r.get(c)
            if kind == "eq" and got != v: return False
            if kind == "in" and str(got) not in [str(x) for x in v]: return False
            if kind == "gte" and (got is None or str(got)[:10] < v): return False
            if kind == "lte" and (got is None or str(got)[:10] > v): return False
            if kind == "is" and v == "null" and got is not None: return False
        return True

    def execute(self):
        rows = self.s[self.t]
        if self.op == "select":
            hit = [dict(r) for r in rows if self._match(r)]
            hit.sort(key=lambda r: r.get("id", 0))
            if hasattr(self, "_range"):
                lo, hi = self._range
                hit = hit[lo:hi + 1]
            return types.SimpleNamespace(data=hit)
        if self.op == "delete":
            keep = [r for r in rows if not self._match(r)]
            self.s["_deleted"] += len(rows) - len(keep)
            self.s[self.t] = keep
            return types.SimpleNamespace(data=[])
        raise AssertionError("unexpected op")


class _Ins:
    def __init__(self, store, table, payload):
        self.s, self.t, self.p = store, table, payload

    def execute(self):
        if self.s.get("_fail_insert"):
            raise RuntimeError("simulated insert failure")
        for r in self.p:
            r = dict(r)
            r.setdefault("id", self.s["_seq"])
            self.s["_seq"] += 1
            self.s[self.t].append(r)
        return types.SimpleNamespace(data=[])


class _Schema:
    def __init__(self, store): self.s = store

    def table(self, name):
        self.s.setdefault(name, [])
        sch, store = self, self.s

        class T:
            def select(_s, *a, **k): return _Q(store, name, "select").select()
            def delete(_s, *a, **k): return _Q(store, name, "delete")
            def insert(_s, payload): return _Ins(store, name, payload)
        return T()


class FakeClient:
    def __init__(self, store): self.s = store
    def schema(self, n): return _Schema(self.s)


import app.modules.commcalc.router as R  # noqa: E402

LUX, NOVA = "170084", "168874"
ORG, OTHER = "854f6d7b", "00000000"


def store_with(rows):
    s = {"_seq": 1000, "_deleted": 0}
    s["raw_ma_daily_tx"] = []
    for r in rows:
        r = dict(r)
        r["id"] = s["_seq"]; s["_seq"] += 1
        s["raw_ma_daily_tx"].append(r)
    return s


def tx(acct, day, org=ORG, period="July 2026"):
    return {"org_id": org, "account_id": acct, "tx_date": day, "period": period, "retail_cost": "10"}


def do_replace(store, table, mapped, period, org=ORG):
    """The production sequence: scope -> snapshot -> scoped delete -> insert."""
    c = FakeClient(store)
    scope = R._replace_scope(table, mapped)
    snap = R._select_replace_slice(c, table, org, period, scope=scope)
    d = c.schema("commcalc").table(table).delete().eq("org_id", org).in_("period", R._pvariants(period))
    R._apply_scope(d, scope).execute()
    for r in mapped:
        pass
    c.schema("commcalc").table(table).insert(mapped).execute()
    return scope, snap


print("\n§1 · THE INCIDENT — Nova's July file must not delete Luxelink's July rows")
st = store_with([tx(LUX, "2026-07-%02d" % d) for d in range(1, 11)] +
                [tx(NOVA, "2026-07-%02d" % d) for d in range(1, 6)])
before_lux = sum(1 for r in st["raw_ma_daily_tx"] if r["account_id"] == LUX)
nova_file = [tx(NOVA, "2026-07-%02d" % d) for d in range(1, 32)]
scope, _ = do_replace(st, "raw_ma_daily_tx", nova_file, "July 2026")
after_lux = sum(1 for r in st["raw_ma_daily_tx"] if r["account_id"] == LUX)
after_nova = sum(1 for r in st["raw_ma_daily_tx"] if r["account_id"] == NOVA)
ok(scope is not None, "a per-slice scope was derived from the file")
ok(scope and scope["partition_col"] == "account_id", "partition column is account_id")
ok(before_lux == 10 and after_lux == 10,
   f"Luxelink's 10 July rows SURVIVE Nova's upload (before {before_lux}, after {after_lux})")
ok(after_nova == 31, f"Nova's own slice was replaced, not appended ({after_nova} rows, expected 31)")

print("\n§2 · LEGACY BEHAVIOUR REPRODUCES THE BUG (negative control)")
st2 = store_with([tx(LUX, "2026-07-05"), tx(NOVA, "2026-07-05")])
c2 = FakeClient(st2)
(c2.schema("commcalc").table("raw_ma_daily_tx").delete()
   .eq("org_id", ORG).in_("period", R._pvariants("July 2026")).execute())   # period-wide, no scope
ok(len(st2["raw_ma_daily_tx"]) == 0,
   "an UNSCOPED period delete wipes BOTH companies — this is the bug the fix removes")

print("\n§3 · THE 08-08 PAIR — same account, later date range, must not eat the earlier upload")
st3 = store_with([tx(LUX, "2026-08-%02d" % d, period="August 2026") for d in (1, 2, 3)])
later = [tx(LUX, "2026-08-%02d" % d, period="August 2026") for d in (4, 5, 6, 7, 8)]
do_replace(st3, "raw_ma_daily_tx", later, "August 2026")
days = sorted(r["tx_date"] for r in st3["raw_ma_daily_tx"])
ok(len(days) == 8 and days[0] == "2026-08-01",
   f"Aug 1–3 survive an Aug 4–8 upload for the SAME account ({len(days)} rows, first {days[0]})")

print("\n§4 · IDEMPOTENT — re-uploading the identical file never duplicates")
st4 = store_with([])
f = [tx(LUX, "2026-07-%02d" % d) for d in range(1, 6)]
do_replace(st4, "raw_ma_daily_tx", [dict(r) for r in f], "July 2026")
n1 = len(st4["raw_ma_daily_tx"])
do_replace(st4, "raw_ma_daily_tx", [dict(r) for r in f], "July 2026")
n2 = len(st4["raw_ma_daily_tx"])
ok(n1 == 5 and n2 == 5, f"same file twice ⇒ still 5 rows, no duplicates (got {n1} then {n2})")

print("\n§5 · TENANT ISOLATION — another org is never touched")
st5 = store_with([tx(NOVA, "2026-07-05", org=OTHER), tx(LUX, "2026-07-05")])
do_replace(st5, "raw_ma_daily_tx", [tx(LUX, "2026-07-05")], "July 2026")
ok(any(r["org_id"] == OTHER for r in st5["raw_ma_daily_tx"]),
   "the other tenant's row survives")

print("\n§6 · UNKNOWN TABLE ⇒ byte-identical legacy behaviour")
ok(R._replace_scope("raw_payment_detail", [{"a": 1}]) is None,
   "a table with no partition spec returns None (period-wide replace, unchanged)")
ok(R._replace_scope("raw_ma_daily_tx", []) is None, "an empty file returns None")

print("\n§7 · UNPROVABLE SLICE ⇒ refuse to narrow, and SAY so")
blank = [tx(LUX, "2026-07-01"), {"org_id": ORG, "account_id": "", "tx_date": "2026-07-02"}]
ok(R._replace_scope("raw_ma_daily_tx", blank) is None,
   "one blank partition value ⇒ None — narrowing on a guess would strand rows")
nodate = [{"org_id": ORG, "account_id": LUX, "tx_date": ""}]
ok(R._replace_scope("raw_ma_daily_tx", nodate) is None, "no usable dates ⇒ None")

print("\n§8 · raw_sales partitions by STORE (June's 6-of-20 fingerprint)")
sc = R._replace_scope("raw_sales", [{"store": "957 Pennsylvania Avenue", "trans_date": "2026-06-03"},
                                    {"store": "531 Utica Ave", "trans_date": "2026-06-09"}])
ok(sc and sc["partition_col"] == "store" and sc["date_col"] == "trans_date",
   "raw_sales scopes on store × trans_date")
ok(sc and sc["lo"] == "2026-06-03" and sc["hi"] == "2026-06-09",
   f"date range is the file's own min/max ({sc['lo']}..{sc['hi']})")
ok(sc and len(sc["values"]) == 2, "both stores are in the slice")

print("\n§9 · SNAPSHOT COVERS EXACTLY WHAT THE DELETE REMOVES")
st9 = store_with([tx(LUX, "2026-07-05"), tx(NOVA, "2026-07-05"), tx(LUX, "2026-07-20")])
mapped9 = [tx(LUX, "2026-07-05")]
scope9, snap9 = do_replace(st9, "raw_ma_daily_tx", mapped9, "July 2026")
ok(len(snap9) == 1 and snap9[0]["account_id"] == LUX,
   f"snapshot holds ONLY the file's own slice ({len(snap9)} row) — a restore can never "
   "resurrect another company's rows")
ok(any(r["account_id"] == NOVA for r in st9["raw_ma_daily_tx"]), "Nova untouched")
ok(any(r["tx_date"] == "2026-07-20" for r in st9["raw_ma_daily_tx"]),
   "the same account's row OUTSIDE the file's date range also survives")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# §5 · A FILE THAT DERIVES ITS PERIOD PER ROW NAMES NO PERIOD (mig 1004 follow-up)
#
# A POS history file spanning many months must NOT be given one period label — each row is booked to
# the month of its own date. But the replace was gated on `if mapped and period:`, so such a file fell
# to a PURE APPEND and a re-upload DOUBLED the data. When the file proves its own slice, the replace
# now fires on the SCOPE ALONE. The period filter is applied conditionally in BOTH the snapshot and
# the delete — if they ever disagreed, the restore would cover a different slice than the delete.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§5 · no period + a provable slice ⇒ replace that slice (idempotent), not a blind append")

STORE_A, STORE_B = "Wireless Zone Brooklyn WZ1321", "Some Other Store"


def sale(store, day, org=ORG, period=None):
    r = {"org_id": org, "store": store, "trans_date": day, "ext_price": "10", "trans_id": f"{store}-{day}"}
    if period:
        r["period"] = period
    return r


def store_with_sales(rows):
    s = {"_seq": 2000, "_deleted": 0, "raw_sales": []}
    for r in rows:
        r = dict(r); r["id"] = s["_seq"]; s["_seq"] += 1
        s["raw_sales"].append(r)
    return s


def do_replace_np(store, table, mapped, period, org=ORG):
    """PRODUCTION SEQUENCE with the period filter applied CONDITIONALLY — mirrors _ingest_mapped_df
    after the fix. Gate: replace when (period or scope); otherwise pure append."""
    c = FakeClient(store)
    scope = R._replace_scope(table, mapped)
    if not (period or scope):
        c.schema("commcalc").table(table).insert(mapped).execute()   # pure append, no delete
        return scope, None, False
    snap = R._select_replace_slice(c, table, org, period, scope=scope)
    d = c.schema("commcalc").table(table).delete().eq("org_id", org)
    if period:
        d = d.in_("period", R._pvariants(period))
    R._apply_scope(d, scope).execute()
    c.schema("commcalc").table(table).insert(mapped).execute()
    return scope, snap, True


# the real shape: one store, 20 months, every row dated, NO period named
hist = [sale(STORE_A, f"2025-{m:02d}-15") for m in range(1, 13)] + \
       [sale(STORE_A, f"2026-{m:02d}-15") for m in range(1, 9)]
ok(len(hist) == 20, "a 20-month history file, every row dated, no period named")
scope = R._replace_scope("raw_sales", hist)
ok(scope is not None, "the file proves its own slice even with NO period")
ok(scope and scope["partition_col"] == "store", "partition column is store")
ok(scope and scope["lo"] == "2025-01-15" and scope["hi"] == "2026-08-15",
   f"date range is the file's own: {scope['lo']} .. {scope['hi']}")

# FIRST load into an empty table
st = store_with_sales([])
_, _, replaced = do_replace_np(st, "raw_sales", hist, "")
ok(len(st["raw_sales"]) == 20, f"first load lands 20 rows (got {len(st['raw_sales'])})")

# RE-UPLOAD the identical file — the regression: this used to DOUBLE the data
_, _, replaced2 = do_replace_np(st, "raw_sales", hist, "")
ok(replaced2, "the re-upload took the REPLACE path, not the append path")
ok(len(st["raw_sales"]) == 20,
   f"POST-FIX re-upload is IDEMPOTENT — still 20 rows, not 40 (got {len(st['raw_sales'])})")

# another store's rows, and another org's, are untouched by that replace
st2 = store_with_sales([sale(STORE_A, "2025-06-15"), sale(STORE_B, "2025-06-15"),
                        sale(STORE_A, "2025-06-15", org=OTHER)])
do_replace_np(st2, "raw_sales", [sale(STORE_A, f"2025-{m:02d}-15") for m in range(1, 13)], "")
ok(sum(1 for r in st2["raw_sales"] if r["store"] == STORE_B) == 1,
   "another STORE's row in the same date range survives")
ok(sum(1 for r in st2["raw_sales"] if r["org_id"] == OTHER) == 1,
   "another ORG's row survives (org scoping still applies)")

# a row of the SAME store OUTSIDE the file's date range survives
st3 = store_with_sales([sale(STORE_A, "2024-01-15"), sale(STORE_A, "2025-06-15")])
do_replace_np(st3, "raw_sales", [sale(STORE_A, "2025-06-15")], "")
ok(any(r["trans_date"] == "2024-01-15" for r in st3["raw_sales"]),
   "the same store's row OUTSIDE the file's date range survives")

# NO period AND no provable slice ⇒ still a pure append (byte-identical to before the fix):
# a delete that cannot be proven is never run.
blind = [{"org_id": ORG, "store": "", "trans_date": "2025-06-15", "ext_price": "1", "trans_id": "x"}]
ok(R._replace_scope("raw_sales", blind) is None,
   "a file with a BLANK partition value proves no slice")
st4 = store_with_sales([sale(STORE_A, "2025-06-15")])
_, _, replaced4 = do_replace_np(st4, "raw_sales", blind, "")
ok(not replaced4, "no period + no provable slice ⇒ PURE APPEND, nothing deleted")
ok(len(st4["raw_sales"]) == 2, "the pre-existing row survives an unprovable append")

# a table outside INGEST_PARTITION proves no slice either — snapshot feeds keep appending
ok(R._replace_scope("inventory_aging_device", [{"org_id": ORG, "sku": "A"}]) is None,
   "a table outside INGEST_PARTITION proves no slice (snapshot feeds stay append-only)")

# and the PERIOD path is unchanged: naming a period still filters on it
st5 = store_with_sales([sale(STORE_A, "2025-06-15", period="June 2025"),
                        sale(STORE_A, "2025-06-16", period="July 2025")])
do_replace_np(st5, "raw_sales", [sale(STORE_A, "2025-06-15", period="June 2025")], "June 2025")
ok(any(r.get("period") == "July 2025" for r in st5["raw_sales"]),
   "naming a period still scopes the delete to THAT period — other periods untouched")


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f_ in FAIL:
    print("  ✗ " + f_)
sys.exit(1 if FAIL else 0)
