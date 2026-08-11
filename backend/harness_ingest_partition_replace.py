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

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f_ in FAIL:
    print("  ✗ " + f_)
sys.exit(1 if FAIL else 0)
