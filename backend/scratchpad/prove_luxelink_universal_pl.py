"""Proof harness for agent/finance/luxelink-universal-pl.

ROOT CAUSE under test: autocompute._PERIOD_SOURCES (the list that decides whether a tenant "has
account data" -> whether recompute_due computes its statements at all, and that drives the P&L/BS
staleness banner) omitted the three universal-tenant sources coa.build_inputs actually reads:
daily_sales_feed, raw_ma_commission, raw_ma_daily_tx. A feed/MA-sourced tenant (luxelink) therefore
read newest_ingest_at()=None -> the sweep skipped it "no account data" -> its P&L snapshot never
computed -> permanently empty.

Proves:
  (A) BEFORE (old source list) newest_ingest_at()=None for a feed/MA-only tenant -> recompute_due
      SKIPS it. AFTER (new list) newest_ingest_at() returns a real ts -> recompute_due COMPUTES it.
  (B) Once computed, engine.compute_and_store produces a POPULATED consolidated P&L for a tenant with
      NO raw_sales, NO raw_mi, NO store_mapping — sales from daily_sales_feed, residual from raw_ma_*,
      opex from store_expenses. Exact expected line values.
  (C) HOUSE BYTE-IDENTITY: engine.compute_and_store output is deep-equal under the OLD vs NEW
      _PERIOD_SOURCES (the engine never reads the list; the change only affects WHEN, never WHAT).
"""
import copy, json
from app.modules.account import coa, engine, autocompute

LUX = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
HOUSE = "00000000-0000-0000-0000-000000000001"
TS_OLD = "2026-07-01T00:00:00+00:00"   # a probed source (store_expenses) ingest
TS_MID = "2026-07-10T00:00:00+00:00"   # a prior snapshot's computed_at
TS_NEW = "2026-07-17T12:00:00+00:00"   # a FRESH feed/MA ingest — newer than the snapshot -> stale


class _Q:
    def __init__(self, store, key):
        self.store, self.key = store, key
        self.mode = "select"
        self.filters = []          # (op, col, val)
        self._range = None
        self._limit = None
        self._order = None
        self._payload = None
        self._onconf = None

    # builders --------------------------------------------------------------
    def select(self, *a, **k): self.mode = "select"; return self
    def insert(self, rows): self.mode = "insert"; self._payload = rows; return self
    def update(self, d): self.mode = "update"; self._payload = d; return self
    def upsert(self, row, on_conflict=None): self.mode = "upsert"; self._payload = row; self._onconf = on_conflict; return self
    def delete(self): self.mode = "delete"; return self
    def eq(self, c, v): self.filters.append(("eq", c, v)); return self
    def in_(self, c, v): self.filters.append(("in", c, list(v))); return self
    def gte(self, c, v): self.filters.append(("gte", c, v)); return self
    def lt(self, c, v): self.filters.append(("lt", c, v)); return self
    def order(self, c, desc=False): self._order = (c, desc); return self
    def limit(self, n): self._limit = n; return self
    def range(self, a, b): self._range = (a, b); return self

    # apply -----------------------------------------------------------------
    def _match(self, row):
        for op, c, v in self.filters:
            rv = row.get(c)
            if op == "eq" and rv != v: return False
            if op == "in" and rv not in v: return False
            if op == "gte" and not (rv is not None and str(rv) >= str(v)): return False
            if op == "lt" and not (rv is not None and str(rv) < str(v)): return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.key, [])
        if self.mode == "select":
            out = [r for r in rows if self._match(r)]
            if self._order:
                col, desc = self._order
                out = sorted(out, key=lambda r: (r.get(col) is None, r.get(col) or ""), reverse=desc)
            if self._range:
                a, b = self._range; out = out[a:b + 1]
            if self._limit is not None:
                out = out[:self._limit]
            return _Res(copy.deepcopy(out))
        if self.mode == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            rows.extend(copy.deepcopy(payload)); return _Res(copy.deepcopy(payload))
        if self.mode == "upsert":
            keys = (self._onconf or "").split(",") if self._onconf else []
            newr = copy.deepcopy(self._payload)
            if keys:
                for i, r in enumerate(rows):
                    if all(r.get(k) == newr.get(k) for k in keys):
                        rows[i] = newr; return _Res([newr])
            rows.append(newr); return _Res([newr])
        if self.mode == "update":
            n = 0
            for r in rows:
                if self._match(r): r.update(self._payload); n += 1
            return _Res([])
        if self.mode == "delete":
            keep = [r for r in rows if not self._match(r)]
            self.store[self.key] = keep; return _Res([])
        return _Res([])


class _Res:
    def __init__(self, data): self.data = data


class _Schema:
    def __init__(self, store, schema): self.store, self.schema = store, schema
    def table(self, name): return _Q(self.store, (self.schema, name))


class FakeClient:
    def __init__(self, tables): self.store = {(s, t): list(rows) for (s, t), rows in tables.items()}
    def schema(self, name): return _Schema(self.store, name)


# ── datasets ────────────────────────────────────────────────────────────────────────────────────
def _feed_rows(ts):
    return [
        {"org_id": LUX, "period": "July 2026", "trans_id": "T1", "department": "IPHONE - XP",
         "category": "Phone", "product_desc": "iPhone 15", "ext_price": 1000, "gp": 200,
         "voided": "", "store": "Luxelink Main", "uploaded_at": ts},
        {"org_id": LUX, "period": "July 2026", "trans_id": "T2", "department": "Ondigo",
         "category": "Case", "product_desc": "OtterBox", "ext_price": 100, "gp": 40,
         "voided": "", "store": "Luxelink Main", "uploaded_at": ts},
    ]


def _ma_rows(ts):
    return (
        [{"org_id": LUX, "period": "July 2026", "merchant_account_id": "M1",
          "device_margin": -30, "consumer_margin": -20, "consumer_financing": 0,
          "rebate": 0, "spiff": 0, "residual": 0, "created_at": ts}],
        [{"org_id": LUX, "period": "July 2026", "account_id": "A1", "account_name": "Lux",
          "merchant_discount": 15, "created_at": ts}],
    )


def lux_tables(feed_ts=TS_NEW, with_expenses=True, expense_ts=TS_NEW):
    ma_c, ma_tx = _ma_rows(feed_ts)
    t = {
        ("storeops", "tenants"): [{"org_id": LUX, "name": "Luxelink", "is_active": True}],
        ("commcalc", "daily_sales_feed"): _feed_rows(feed_ts),
        ("commcalc", "raw_ma_commission"): ma_c,
        ("commcalc", "raw_ma_daily_tx"): ma_tx,
        # deliberately EMPTY: raw_sales, raw_mi, store_mapping, companies, gp_category_map,
        # accessory_config, journal_entries, account_config, account_statements ...
    }
    if with_expenses:
        t[("commcalc", "store_expenses")] = [
            {"org_id": LUX, "period": "July 2026", "store_code": "Luxelink Main",
             "expense_name": "Rent", "expense_type": "rent", "amount": 500, "source_key": None,
             "created_at": expense_ts}]
    return t


# resolve _MA_COMPONENTS actually used so the harness matches the code exactly
from app.modules.account.residual_subs import _MA_COMPONENTS


def _consolidated(client, org, period):
    engine.compute_and_store(client, org, period)
    rows = client.schema("commcalc").table("account_statements").select("*") \
        .eq("org_id", org).eq("period", period).execute().data
    pl = next(r["payload"] for r in rows if r["statement_type"] == "pl" and r["scope_key"] == "consolidated")
    return pl


def _line(pl, key):
    for sec in pl["sections"]:
        for ln in sec["lines"]:
            if ln["key"] == key:
                return ln["amount"]
    return 0.0


def main():
    period = "2026-07"
    results = []

    # ── (A) BEFORE/AFTER newest_ingest_at + recompute_due ─────────────────────────────────────
    OLD = [s for s in autocompute._PERIOD_SOURCES
           if s[0] not in ("daily_sales_feed", "raw_ma_commission", "raw_ma_daily_tx")]
    NEW = list(autocompute._PERIOD_SOURCES)
    assert len(NEW) - len(OLD) == 3, "expected exactly 3 new sources"

    # A1 — feed/MA-ONLY tenant (NO store_expenses / raw_sales / raw_mi): the "permanently empty" case.
    #   BEFORE: newest_ingest=None -> recompute_due SKIPS "no account data" -> snapshot never written.
    #   AFTER : newest_ingest=fresh ts -> recompute_due COMPUTES it.
    autocompute._PERIOD_SOURCES = OLD
    c_b = FakeClient(lux_tables(with_expenses=False))
    ni_b = autocompute.newest_ingest_at(c_b, LUX, "July 2026")
    r_b = autocompute.recompute_due(c_b, only_org=LUX, force=False)["results"][0]
    assert ni_b is None, f"A1 BEFORE newest_ingest should be None, got {ni_b}"
    assert r_b.get("reason") == "no account data", f"A1 BEFORE should skip: {r_b}"
    assert len(c_b.schema("commcalc").table("account_statements").select("*").eq("org_id", LUX).execute().data) == 0
    autocompute._PERIOD_SOURCES = NEW
    c_a = FakeClient(lux_tables(with_expenses=False))
    ni_a = autocompute.newest_ingest_at(c_a, LUX, "July 2026")
    r_a = autocompute.recompute_due(c_a, only_org=LUX, force=False)["results"][0]
    assert ni_a == TS_NEW, f"A1 AFTER newest_ingest should be {TS_NEW}, got {ni_a}"
    assert r_a.get("status") == "ok" and r_a.get("recomputed"), f"A1 AFTER should compute: {r_a}"
    assert len(c_a.schema("commcalc").table("account_statements").select("*").eq("org_id", LUX).execute().data) > 0
    results.append(("A1 feed/MA-only", f"before ni={ni_b} -> {r_b.get('reason')}",
                    f"after ni={ni_a} -> recomputed {r_a['recomputed']}"))

    # A2 — staleness UNDERCOUNT even WITH a probed source. Tenant has store_expenses at TS_OLD, a prior
    #   snapshot computed at TS_MID, and a FRESH feed/MA ingest at TS_NEW (> snapshot).
    #   BEFORE: newest_ingest=TS_OLD (misses the feed) < snapshot -> NOT stale -> P&L shows stale GP.
    #   AFTER : newest_ingest=TS_NEW > snapshot -> stale=True -> recompute prompted.
    def _a2_tables():
        t = lux_tables(feed_ts=TS_NEW, with_expenses=True, expense_ts=TS_OLD)
        t[("commcalc", "account_statements")] = [
            {"org_id": LUX, "period": "July 2026", "statement_type": "pl", "scope_key": "consolidated",
             "computed_at": TS_MID, "payload": {}}]
        return t
    autocompute._PERIOD_SOURCES = OLD
    st_b = autocompute.staleness(FakeClient(_a2_tables()), LUX, "July 2026")
    autocompute._PERIOD_SOURCES = NEW
    st_a = autocompute.staleness(FakeClient(_a2_tables()), LUX, "July 2026")
    assert st_b["stale"] is False and st_b["newest_ingest_at"] == TS_OLD, f"A2 BEFORE: {st_b}"
    assert st_a["stale"] is True and st_a["newest_ingest_at"] == TS_NEW, f"A2 AFTER: {st_a}"
    results.append(("A2 staleness undercount",
                    f"before newest={st_b['newest_ingest_at']} stale={st_b['stale']}",
                    f"after newest={st_a['newest_ingest_at']} stale={st_a['stale']}"))

    # ── (B) POPULATED P&L from feed + MA + expenses (no raw_sales/raw_mi/store_mapping) ───────────
    autocompute._PERIOD_SOURCES = NEW
    c = FakeClient(lux_tables())
    pl = _consolidated(c, LUX, "July 2026")
    got = {
        "device_rev": _line(pl, "device_rev"),
        "device_cost": _line(pl, "device_cost"),
        "accessory_rev": _line(pl, "accessory_rev"),
        "accessory_cost": _line(pl, "accessory_cost"),
        "mi_income": _line(pl, "mi_income"),
        "atu_income": _line(pl, "atu_income"),
        "store_opex": _line(pl, "store_opex"),
        "gross_profit": pl["gross_profit"],
        "net_income": pl["net_income"],
    }
    expect = {
        "device_rev": 1000.0, "device_cost": 800.0,
        "accessory_rev": 100.0, "accessory_cost": 20.0,
        "mi_income": 50.0, "atu_income": 15.0, "store_opex": 500.0,
        "gross_profit": 1165.0 - 820.0, "net_income": (1165.0 - 820.0) - 500.0,
    }
    for k, v in expect.items():
        assert abs(got[k] - v) < 0.01, f"B: line {k} expected {v}, got {got[k]}"
    results.append(("B populated P&L", json.dumps(got), "all lines match expected"))

    # ── (C) HOUSE BYTE-IDENTITY: engine output invariant to the source-list change ────────────────
    house_tables = {
        ("storeops", "tenants"): [{"org_id": HOUSE, "name": "House", "is_active": True}],
        ("commcalc", "raw_sales"): [
            {"org_id": HOUSE, "period": "July 2026", "trans_id": "H1", "department": "IPHONE - XP",
             "category": "Phone", "product_desc": "iPhone", "ext_price": 900, "gp": 150,
             "voided": "", "store": "3 Palisade Ave", "created_at": TS_OLD},
            {"org_id": HOUSE, "period": "July 2026", "trans_id": "H2", "department": "Ondigo",
             "category": "Case", "product_desc": "Case", "ext_price": 60, "gp": 25,
             "voided": "", "store": "3 Palisade Ave", "created_at": TS_OLD},
        ],
        ("commcalc", "raw_mi"): [
            {"org_id": HOUSE, "period": "July 2026", "actual_mi_payout": 300, "actual_atu_payout": 40,
             "created_at": TS_OLD},
        ],
        ("commcalc", "store_expenses"): [
            {"org_id": HOUSE, "period": "July 2026", "store_code": "3 Palisade Ave",
             "expense_name": "Rent", "expense_type": "rent", "amount": 700, "source_key": None,
             "created_at": TS_OLD},
        ],
        ("commcalc", "store_mapping"): [
            {"org_id": HOUSE, "store_code": "3PAL", "store_address": "3 Palisade Ave", "market": "NJ"},
        ],
    }
    autocompute._PERIOD_SOURCES = OLD
    pl_old = _consolidated(FakeClient(copy.deepcopy(house_tables)), HOUSE, "July 2026")
    autocompute._PERIOD_SOURCES = NEW
    pl_new = _consolidated(FakeClient(copy.deepcopy(house_tables)), HOUSE, "July 2026")
    assert json.dumps(pl_old, sort_keys=True) == json.dumps(pl_new, sort_keys=True), \
        "C: house P&L payload changed under the source-list edit"
    results.append(("C house byte-identity", f"gp={pl_new['gross_profit']} ni={pl_new['net_income']}",
                    "OLD==NEW deep-equal"))

    # restore
    autocompute._PERIOD_SOURCES = NEW
    print("ALL PROOFS PASS\n")
    for name, val, note in results:
        print(f"  [{name}]  {val}   -> {note}")


if __name__ == "__main__":
    main()
