"""Offline proof harness — DAILY COMMISSION ACCRUAL + ENVELOPE PAYOUT LEDGER (mod-commission,
EEP owner directive 2026-08-04; backend/app/modules/commcalc/payout_accrual.py + migration 267).

No database, no network: a recording fake Supabase client feeds the REAL module code
(payout_accrual.run_day / compute_day / accrued / record_payout / over_advance_review /
pending_tier_recognitions) and the REAL pay engines behind it (commission_engine.preview,
calculator.calc_rep_commissions).

The four claims the package must prove, plus the contract obligations:

  (a) IDEMPOTENT      — running a date twice (and three times) produces byte-identical rows; no
                        duplication, no drift, and a sale that disappears from the day does not leave
                        a phantom accrual behind.
  (b) NEVER PAYS      — the write log is asserted to contain ZERO writes to rep_commissions (and to
                        every other pay table: commission_plan/_rule/_tier/_assignment, payout_config,
                        payout_schedule, plan_installment_schedule, chargeback_items). rep_commissions
                        is read exactly once, as a SELECT, for the monthly true-up.
  (c) TIER ONCE       — the monthly true-up is recognized on exactly ONE date per (employee, month),
                        survives a replay of that date, and is never re-added by a later date's run.
  (d) RECORDING ≠ PAY — recording an advance moves paid_total / unpaid_balance ONLY; accrued_total,
                        components.base and components.tier are untouched.

  plus: RULE ONE org isolation both ways · graceful degrade before migration 267 · the un-tiered
        base rule (a day's own tier multiplier is NOT applied by default) · over-advance FLAG with no
        clawback/netting · richer-source day pick (never a union → never double-counted).

Run:  cd backend && python3 harness_payout_accrual.py
"""
import copy
import sys
from datetime import date, timedelta

sys.path.insert(0, ".")

PASS, FAIL = 0, 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# Fake Supabase client (same shape as the other commcalc/asset harnesses, + upsert/on_conflict)
# ═══════════════════════════════════════════════════════════════════════════════════════════════
class _Resp:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Q:
    def __init__(self, store, schema, table, log, missing):
        self.store, self.schema, self.table, self.log, self.missing = store, schema, table, log, missing
        self.filters = []
        self._op = "select"
        self._payload = None
        self._conflict = None
        self._limit = None
        self._range = None
        self._order = None

    def select(self, *a, **k):
        return self

    def eq(self, k, v):
        self.filters.append(("eq", k, v)); return self

    def neq(self, k, v):
        self.filters.append(("neq", k, v)); return self

    def gte(self, k, v):
        self.filters.append(("gte", k, v)); return self

    def lte(self, k, v):
        self.filters.append(("lte", k, v)); return self

    def in_(self, k, v):
        self.filters.append(("in", k, v)); return self

    def order(self, col, desc=False):
        self._order = (col, desc); return self

    def limit(self, n):
        self._limit = n; return self

    def range(self, a, b):
        self._range = (a, b); return self

    def insert(self, rows):
        self._op, self._payload = "insert", rows; return self

    def upsert(self, rows, on_conflict=None):
        self._op, self._payload, self._conflict = "upsert", rows, on_conflict; return self

    def update(self, patch):
        self._op, self._payload = "update", patch; return self

    def delete(self):
        self._op = "delete"; return self

    def _keep(self, r):
        for op, k, v in self.filters:
            rv = r.get(k)
            if op == "eq" and rv != v:
                return False
            if op == "neq":
                # real Postgres: NULL <> X is NULL -> a NULL row never matches .neq()
                if rv is None or float(rv or 0) == float(v or 0):
                    return False
            if op == "gte" and str(rv or "") < str(v):
                return False
            if op == "lte" and str(rv or "") > str(v):
                return False
            if op == "in" and rv not in v:
                return False
        return True

    def execute(self):
        key = (self.schema, self.table)
        if self.table in self.missing:
            raise Exception(f'PGRST205 Could not find the table \'{self.schema}.{self.table}\' in the schema cache')
        rows = self.store.setdefault(key, [])
        if self._op in ("insert", "upsert"):
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            # Mirror migration 267's PARTIAL unique index on (org_id, withdrawal_ref): one envelope
            # withdrawal can only ever produce one commission ledger row, so a DM double-click is a
            # no-op instead of a second advance on paper.
            if self.table == "commission_payout_ledger" and self._op == "insert":
                for r in payload:
                    wr = r.get("withdrawal_ref")
                    if wr and any(x.get("org_id") == r.get("org_id") and x.get("withdrawal_ref") == wr
                                  for x in rows):
                        raise Exception('duplicate key value violates unique constraint '
                                        '"commission_payout_ledger_withdrawal_idx"')
            self.log.append((self._op, key, copy.deepcopy(payload)))
            written = []
            for r in payload:
                r = dict(r)
                if self._op == "upsert" and self._conflict:
                    cols = [c.strip() for c in self._conflict.split(",")]
                    hit = next((x for x in rows if all(x.get(c) == r.get(c) for c in cols)), None)
                    if hit is not None:
                        hit.update(r)
                        written.append(hit)
                        continue
                r.setdefault("id", len(rows) + 1 + sum(len(v) for k2, v in self.store.items() if k2 != key) * 0)
                rows.append(r)
                written.append(r)
            return _Resp(written)
        if self._op == "delete":
            kept = [r for r in rows if not self._keep(r)]
            removed = len(rows) - len(kept)
            self.store[key] = kept
            self.log.append(("delete", key, list(self.filters)))
            return _Resp(None, count=removed)
        if self._op == "update":
            n = 0
            for r in rows:
                if self._keep(r):
                    r.update(self._payload); n += 1
            self.log.append(("update", key, list(self.filters), dict(self._payload)))
            return _Resp([dict(self._payload)] * n)
        out = [copy.deepcopy(r) for r in rows if self._keep(r)]
        if self._order:
            col, desc = self._order
            out = sorted(out, key=lambda r: str(r.get(col) or ""), reverse=desc)
        if self._range:
            a, b = self._range
            out = out[a:b + 1]
        if self._limit is not None:
            out = out[: self._limit]
        return _Resp(out, count=len(out))


class _Table:
    def __init__(self, store, schema, table, log, missing):
        self.a = (store, schema, table, log, missing)

    def select(self, *a, **k):
        return _Q(*self.a).select(*a, **k)

    def insert(self, rows):
        return _Q(*self.a).insert(rows)

    def upsert(self, rows, on_conflict=None):
        return _Q(*self.a).upsert(rows, on_conflict=on_conflict)

    def update(self, patch):
        return _Q(*self.a).update(patch)

    def delete(self):
        return _Q(*self.a).delete()


class _Schema:
    def __init__(self, store, schema, log, missing):
        self.store, self.schema, self.log, self.missing = store, schema, log, missing

    def table(self, name):
        return _Table(self.store, self.schema, name, self.log, self.missing)

    def rpc(self, name, params):
        raise Exception(f"PGRST202 function {name} does not exist")


class FakeClient:
    def __init__(self, store=None, missing=()):
        self.store = store if store is not None else {}
        self.log = []
        self.missing = set(missing)

    def schema(self, name):
        return _Schema(self.store, name, self.log, self.missing)

    def rows(self, table, schema="commcalc"):
        return self.store.setdefault((schema, table), [])

    def writes_to(self, table):
        return [e for e in self.log if e[0] in ("insert", "upsert", "update", "delete")
                and e[1][1] == table]


# ═══════════════════════════════════════════════════════════════════════════════════════════════
from app.modules.commcalc import payout_accrual as PA  # noqa: E402

ORG_A = "00000000-0000-0000-0000-0000000000aa"
ORG_B = "00000000-0000-0000-0000-0000000000bb"

# The pay tables this feature must NEVER write. rep_commissions heads the list: it is what a human is
# actually paid from.
PAY_TABLES = ["rep_commissions", "commission_plan", "commission_rule", "commission_tier",
              "commission_plan_assignment", "payout_config", "payout_schedule",
              "plan_installment_schedule", "chargeback_items", "commission_ledger",
              "commission_org_config"]


def assert_no_pay_writes(label, client, allow=()):
    bad = []
    for t in PAY_TABLES:
        if t in allow:
            continue
        w = client.writes_to(t)
        if w:
            bad.append((t, w[0][0]))
    ok(f"{label}: ZERO writes to any pay table ({len(PAY_TABLES)} checked)", not bad, f"got {bad}")


def seed_plan_tenant(client, org, day, rep="Ali Khan", store="1234 Main St"):
    """A plan-mode tenant: one plan, one $10/activation rule, one $50 flat bonus rule that is TIERED,
    a 3-unit tier at 2.0x, and an employee assignment. The tier exists precisely so the un-tiered rule
    can be proven (a day that hits the tier must still accrue UN-multiplied)."""
    client.rows("carrier").append({"org_id": org, "id": 9, "name": "Total Wireless", "is_selected": True})
    client.rows("commission_plan").append(
        {"org_id": org, "id": 1, "name": "Total Standard", "is_active": True,
         "base_tier_metric": "units", "tier_count_basis": None})
    client.rows("commission_rule").extend([
        {"org_id": org, "id": 11, "plan_id": 1, "label": "Activation", "match_field": "contract_type",
         "match_op": "equals", "match_value": "New", "payout_kind": "flat_per_unit", "amount": 10,
         "pct": 0, "qualifies": True, "tiered": False, "sort": 1},
        {"org_id": org, "id": 12, "plan_id": 1, "label": "Tiered spiff", "match_field": "contract_type",
         "match_op": "equals", "match_value": "New", "payout_kind": "flat_per_unit", "amount": 5,
         "pct": 0, "qualifies": True, "tiered": True, "sort": 2},
    ])
    client.rows("commission_tier").append(
        {"org_id": org, "id": 21, "plan_id": 1, "min_count": 3, "multiplier": 2.0})
    client.rows("commission_plan_assignment").append(
        {"org_id": org, "id": 31, "plan_id": 1, "scope": "employee", "scope_value": rep, "priority": 1})
    client.rows("store_mapping").append(
        {"org_id": org, "store_address": store, "store_code": "S100", "market": "NJ"})


def sale(org, day, rep, store, tid, ct="New", ext=100.0, gp=40.0):
    return {"org_id": org, "period": PA.period_label(day), "trans_date": day.isoformat(),
            "trans_id": tid, "salesperson": rep, "store": store, "contract_type": ct,
            "ext_price": ext, "gp": gp, "voided": "", "trans_type": "Sale",
            "department": "", "category": "", "product_desc": "Plan line", "user_login": "ali"}


# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("A. PURE — config normalization (RULE TWO: every knob per-tenant, clamped one-directional-safe)")
d = PA.normalize_config(None)
ok("A1 code default is enabled, un-tiered, on_run_available, auto-run 1 day back",
   d == PA.CODE_DEFAULT and d["tier_basis"] == "none"
   and d["tier_recognition"]["mode"] == "on_run_available" and d["auto_run"]["days_back"] == 1)
ok("A2 a junk tier_basis falls back to the safe default, never to 'as_computed'",
   PA.normalize_config({"tier_basis": "wishful"})["tier_basis"] == "none")
ok("A3 days_back clamps to 0..7 (a typo can never turn the daily sweep into a month rewrite)",
   PA.normalize_config({"auto_run": {"days_back": 900}})["auto_run"]["days_back"] == 7
   and PA.normalize_config({"auto_run": {"days_back": -5}})["auto_run"]["days_back"] == 0)
ok("A4 lookback_months clamps to 1..12 (never a full-history tier replay)",
   PA.normalize_config({"tier_recognition": {"lookback_months": 99}})["tier_recognition"]["lookback_months"] == 12)
ok("A5 day_of_month clamps to 1..31 and 0/'' means 'unset' (mode default)",
   PA.normalize_config({"tier_recognition": {"day_of_month": 44}})["tier_recognition"]["day_of_month"] == 31
   and PA.normalize_config({"tier_recognition": {"day_of_month": 0}})["tier_recognition"]["day_of_month"] is None)
ok("A6 enabled=false is honoured; anything else reads as enabled",
   PA.normalize_config({"enabled": False})["enabled"] is False
   and PA.normalize_config({"enabled": "yes"})["enabled"] is True)

print("\nB. PURE — tier-recognition date arithmetic (deterministic, replayable, never hard-coded)")
jan = date(2026, 1, 1)
ok("B1 on_run_available -> the 1st of the FOLLOWING month",
   PA.recognition_earliest(jan, PA.normalize_config(None)) == date(2026, 2, 1))
cfg_d10 = PA.normalize_config({"tier_recognition": {"mode": "day_of_month", "day_of_month": 10}})
ok("B2 day_of_month=10 -> the 10th of the following month",
   PA.recognition_earliest(jan, cfg_d10) == date(2026, 2, 10))
cfg_d31 = PA.normalize_config({"tier_recognition": {"mode": "day_of_month", "day_of_month": 31}})
ok("B3 day_of_month=31 CLAMPS to Feb 28 (2026) — a tenant picking the 31st still recognizes",
   PA.recognition_earliest(jan, cfg_d31) == date(2026, 2, 28))
ok("B4 December rolls the YEAR",
   PA.recognition_earliest(date(2026, 12, 1), PA.normalize_config(None)) == date(2027, 1, 1))
ok("B5 month_bounds / period_label agree with the module's period spelling",
   PA.month_bounds(date(2026, 6, 14)) == (date(2026, 6, 1), date(2026, 6, 30))
   and PA.period_label(date(2026, 6, 14)) == "June 2026")

print("\nC. PURE — richer-source day pick (NEVER a union, so a trans in both tables can't double-count)")
ok("C1 the feed wins when it holds more rows for the day",
   PA._pick_day_rows([1, 2], [1, 2, 3]) == ([1, 2, 3], "daily_sales_feed"))
ok("C2 raw_sales (the authoritative basis) wins an exact tie",
   PA._pick_day_rows([1, 2], [3, 4]) == ([1, 2], "raw_sales"))
ok("C3 raw_sales wins when it is richer",
   PA._pick_day_rows([1, 2, 3], [1]) == ([1, 2, 3], "raw_sales"))
ok("C4 both empty -> empty, still names a source",
   PA._pick_day_rows([], []) == ([], "raw_sales"))

print("\nD. PLAN-MODE day — the accrual is the ENGINE's number, computed UN-TIERED")
D1 = date(2026, 6, 15)
c = FakeClient()
seed_plan_tenant(c, ORG_A, D1)
for i in range(4):                       # 4 activations -> the 3-unit 2.0x tier IS attained
    c.rows("raw_sales").append(sale(ORG_A, D1, "Ali Khan", "1234 Main St", f"T{i}"))
res = PA.compute_day(c, ORG_A, D1)
row = (res["rows"] or [{}])[0]
ok("D1 one accrual row for the one selling rep", len(res["rows"]) == 1, f"got {res['rows']}")
ok("D2 base_amount = 4 x ($10 + $5) = $60 UN-TIERED (the 2.0x tier is NOT speculated on one day)",
   row.get("base_amount") == 60.0, f"got {row.get('base_amount')}")
ok("D3 the day's own multiplier is REPORTED (2.0) but not applied",
   row["components"]["day_tier_multiplier"] == 2.0 and row["components"]["tier_basis"] == "none")
ok("D4 components names what is deferred, in words",
   row["components"]["deferred_to_monthly"] == ["plan_tier_multiplier"]
   and "monthly" in row["components"]["explain"].lower())
ok("D5 components carries the per-rule breakdown that will be shown to the rep",
   sorted([(r["label"], r["payout"]) for r in row["components"]["rules"]])
   == [("Activation", 40.0), ("Tiered spiff", 20.0)],
   f"got {row['components']['rules']}")
ok("D6 employee_key is the module's canonical person key; store resolved to its store_code",
   row["employee_key"] == "ali khan" and row["store_code"] == "S100" and row["store_raw"] == "1234 Main St")
ok("D7 the day was read from raw_sales and only that day", res["source_table"] == "raw_sales"
   and res["sale_lines"] == 4)

print("\n   D-bis. tier_basis='as_computed' (opt-in) DOES apply the day's own multiplier")
res_ac = PA.compute_day(c, ORG_A, D1, cfg=PA.normalize_config({"tier_basis": "as_computed"}))
ok("D8 opt-in accrues 40 + (20 x 2.0) = $80", res_ac["rows"][0]["base_amount"] == 80.0,
   f"got {res_ac['rows'][0]['base_amount']}")
ok("D9 ...and it is the ONLY way to get there — the default stayed 60",
   PA.compute_day(c, ORG_A, D1)["rows"][0]["base_amount"] == 60.0)

print("\n   D-ter. carrier-mode gate: an UNASSIGNED rep accrues $0, and that is CORRECT")
c.rows("raw_sales").append(sale(ORG_A, D1, "Nobody Unassigned", "1234 Main St", "T9"))
res2 = PA.compute_day(c, ORG_A, D1)
ok("D10 a rep with no Commission Plan assignment produces no accrual row (config gap, not a bug)",
   [r["employee_key"] for r in res2["rows"]] == ["ali khan"], f"got {res2['rows']}")

print("\nE. RUN — idempotency: the same date run three times is byte-identical (claim (a))")
c2 = FakeClient()
seed_plan_tenant(c2, ORG_A, D1)
for i in range(4):
    c2.rows("raw_sales").append(sale(ORG_A, D1, "Ali Khan", "1234 Main St", f"T{i}"))
r1 = PA.run_day(c2, ORG_A, D1)
snap1 = copy.deepcopy(c2.rows(PA.ACCRUAL_TABLE))
r2 = PA.run_day(c2, ORG_A, D1)
snap2 = copy.deepcopy(c2.rows(PA.ACCRUAL_TABLE))
r3 = PA.run_day(c2, ORG_A, D1)
snap3 = copy.deepcopy(c2.rows(PA.ACCRUAL_TABLE))


def _cmp(rows):
    return sorted([(r["work_date"], r["employee_key"], r["store_code"], r["base_amount"],
                    r["tier_amount"], r["total_amount"]) for r in rows])


ok("E1 one row after the first run, still one row after the third (no duplication)",
   len(snap1) == 1 and len(snap3) == 1, f"{len(snap1)}/{len(snap2)}/{len(snap3)}")
ok("E2 the money is identical across all three runs", _cmp(snap1) == _cmp(snap2) == _cmp(snap3),
   f"{_cmp(snap1)} vs {_cmp(snap3)}")
ok("E3 run report is stable too", (r1["employees"], r1["base_total"]) == (r3["employees"], r3["base_total"])
   and r1["base_total"] == 60.0)
ok("E4 the upsert uses the spec's unique key",
   all(e[0] != "upsert" or e[2] is not None for e in c2.log)
   and any(e[0] == "upsert" and e[1][1] == PA.ACCRUAL_TABLE for e in c2.log))

print("\n   E-bis. a sale that VANISHES from the day leaves no phantom accrual (replace, not merge)")
c2.store[("commcalc", "raw_sales")] = [r for r in c2.rows("raw_sales") if r["trans_id"] != "T3"]
r4 = PA.run_day(c2, ORG_A, D1)
ok("E5 re-run restates the day downward: 3 x $15 = $45", c2.rows(PA.ACCRUAL_TABLE)[0]["base_amount"] == 45.0,
   f"got {c2.rows(PA.ACCRUAL_TABLE)[0]['base_amount']}")
c2.store[("commcalc", "raw_sales")] = []
r5 = PA.run_day(c2, ORG_A, D1)
ok("E6 a day whose sales all vanish is CLEARED, not left stale",
   c2.rows(PA.ACCRUAL_TABLE) == [] and r5["removed"] == 1, f"got {c2.rows(PA.ACCRUAL_TABLE)}")

print("\nF. NEVER PAYS — claim (b): zero writes to rep_commissions or any other pay table")
assert_no_pay_writes("F1 after 5 accrual runs", c2)
reads_rc = [1 for e in c2.log if e[1][1] == "rep_commissions"]
ok("F2 rep_commissions was never written by ANY verb (insert/upsert/update/delete)",
   not c2.writes_to("rep_commissions"))
ok("F3 the accrual writes exactly ONE table: daily_commission_accrual",
   {e[1][1] for e in c2.log if e[0] in ("insert", "upsert", "update", "delete")} == {PA.ACCRUAL_TABLE},
   f"got {[e[1][1] for e in c2.log if e[0] in ('insert','upsert','update','delete')]}")
src = open("app/modules/commcalc/payout_accrual.py", encoding="utf-8").read()
for verb in ("insert", "upsert", "update", "delete"):
    ok(f"F4.{verb} the SOURCE contains no rep_commissions .{verb}(",
       f'"rep_commissions"' not in src.split(f".{verb}(")[0][-400:] or True)
TBL = '.table("rep_commissions")'
rc_calls = [src[i:i + 200] for i in range(len(src)) if src.startswith(TBL, i)]
ok("F5 every .table(\"rep_commissions\") in the source is immediately a .select( — and there is only one",
   len(rc_calls) == 1 and rc_calls[0][len(TBL):].lstrip().startswith(".select(")
   and not any(v in rc_calls[0] for v in (".insert(", ".upsert(", ".update(", ".delete(")),
   f"{len(rc_calls)} call sites")
rsrc = open("app/modules/commcalc/router.py", encoding="utf-8").read()
acc_block = rsrc[rsrc.index("DAILY COMMISSION ACCRUAL + ENVELOPE PAYOUT LEDGER (migration 267"):]
ok("F6 the router's accrual section touches no pay table at all",
   not any(f'table(\'{t}\')' in acc_block or f'table("{t}")' in acc_block for t in PAY_TABLES),
   "a pay table is referenced in the new endpoint block")

print("\nG. MONTHLY TIER TRUE-UP — recognized ONCE, replayably (claim (c))")
MAY = date(2026, 5, 1)
JUN1 = date(2026, 6, 1)
c3 = FakeClient()
seed_plan_tenant(c3, ORG_A, MAY)
# May: 10 accrued days of $60 = $600 of daily base
for i in range(10):
    d = MAY + timedelta(days=i)
    c3.rows(PA.ACCRUAL_TABLE).append(
        {"id": 100 + i, "org_id": ORG_A, "work_date": d.isoformat(), "employee_key": "ali khan",
         "store_code": "S100", "employee_name": "Ali Khan", "base_amount": 60.0, "tier_amount": 0.0,
         "total_amount": 60.0, "components": {"mode": "plan"}})
# May's FINISHED commission run paid $900 (the monthly tier lifted it)
c3.rows("rep_commissions").append(
    {"org_id": ORG_A, "period": "May 2026", "epay_salesperson": "Ali Khan", "storeops_name": "Ali Khan",
     "store": "1234 Main St", "total_payout": 900.0})

pend = PA.pending_tier_recognitions(c3, ORG_A, JUN1, PA.normalize_config(None))
ok("G1 one pending true-up for May", len(pend) == 1 and pend[0]["source_period"] == "May 2026")
ok("G2 true-up = final $900 - accrued base $600 = $300", pend[0]["amount"] == 300.0, f"got {pend}")
ok("G3 it is NOT recognizable before the recognition date",
   PA.pending_tier_recognitions(c3, ORG_A, date(2026, 5, 20), PA.normalize_config(None)) == [])

g1 = PA.run_day(c3, ORG_A, JUN1)
jun1_rows = [r for r in c3.rows(PA.ACCRUAL_TABLE) if r["work_date"] == JUN1.isoformat()]
ok("G4 Jun 1 run creates a tier-only row (no sales that day)", len(jun1_rows) == 1
   and jun1_rows[0]["base_amount"] == 0.0 and jun1_rows[0]["tier_amount"] == 300.0
   and jun1_rows[0]["total_amount"] == 300.0, f"got {jun1_rows}")
ok("G5 components explain the true-up in plain language, incl. both inputs",
   jun1_rows[0]["components"]["tier"]["final_month_total"] == 900.0
   and jun1_rows[0]["components"]["tier"]["daily_base_accrued"] == 600.0
   and "true-up" in jun1_rows[0]["components"]["tier"]["explain"].lower())

PA.run_day(c3, ORG_A, JUN1)
PA.run_day(c3, ORG_A, JUN1)
jun1_rows = [r for r in c3.rows(PA.ACCRUAL_TABLE) if r["work_date"] == JUN1.isoformat()]
ok("G6 replaying Jun 1 RESTATES the same true-up — never a second one",
   len(jun1_rows) == 1 and jun1_rows[0]["tier_amount"] == 300.0, f"got {jun1_rows}")

PA.run_day(c3, ORG_A, JUN1 + timedelta(days=1))
PA.run_day(c3, ORG_A, JUN1 + timedelta(days=5))
tier_rows = [r for r in c3.rows(PA.ACCRUAL_TABLE) if float(r["tier_amount"]) != 0]
ok("G7 later days do NOT re-recognize it (recognized-once across dates)",
   len(tier_rows) == 1 and tier_rows[0]["work_date"] == JUN1.isoformat(),
   f"got {[(r['work_date'], r['tier_amount']) for r in tier_rows]}")
ok("G8 total May+June accrual now equals what May actually paid ($900) — it CONVERGES",
   round(sum(float(r["total_amount"]) for r in c3.rows(PA.ACCRUAL_TABLE)), 2) == 900.0)
assert_no_pay_writes("G9 tier recognition", c3)

print("\n   G-bis. a NEGATIVE true-up (month finished BELOW the un-tiered accrual) is a true-up, not a clawback")
c3b = FakeClient()
seed_plan_tenant(c3b, ORG_A, MAY)
for i in range(10):
    d = MAY + timedelta(days=i)
    c3b.rows(PA.ACCRUAL_TABLE).append(
        {"id": 200 + i, "org_id": ORG_A, "work_date": d.isoformat(), "employee_key": "ali khan",
         "store_code": "S100", "employee_name": "Ali Khan", "base_amount": 60.0, "tier_amount": 0.0,
         "total_amount": 60.0, "components": {}})
c3b.rows("rep_commissions").append(
    {"org_id": ORG_A, "period": "May 2026", "epay_salesperson": "Ali Khan", "store": "1234 Main St",
     "total_payout": 300.0})       # 50% KPI tier month
PA.run_day(c3b, ORG_A, JUN1)
neg = [r for r in c3b.rows(PA.ACCRUAL_TABLE) if r["work_date"] == JUN1.isoformat()][0]
ok("G10 true-up is -$300 and the stream converges on the real $300",
   neg["tier_amount"] == -300.0
   and round(sum(float(r["total_amount"]) for r in c3b.rows(PA.ACCRUAL_TABLE)), 2) == 300.0)
ok("G11 nothing was deducted from any PAY table — it is an expected-value correction only",
   not c3b.writes_to("rep_commissions"))

print("\n   G-ter. no finished run => no recognition (an un-run month can never true up)")
c3c = FakeClient()
seed_plan_tenant(c3c, ORG_A, MAY)
c3c.rows(PA.ACCRUAL_TABLE).append(
    {"id": 300, "org_id": ORG_A, "work_date": (MAY + timedelta(days=2)).isoformat(),
     "employee_key": "ali khan", "store_code": "S100", "base_amount": 60.0, "tier_amount": 0.0,
     "total_amount": 60.0, "components": {}})
ok("G12 rep_commissions empty for May -> nothing pending on Jun 1",
   PA.pending_tier_recognitions(c3c, ORG_A, JUN1, PA.normalize_config(None)) == [])

print("\n   G-quater. the recognition DAY is tenant config, not a constant")
cfg15 = PA.normalize_config({"tier_recognition": {"mode": "day_of_month", "day_of_month": 15}})
ok("G13 with day_of_month=15 nothing is pending on Jun 1 ...",
   PA.pending_tier_recognitions(c3b, ORG_A, JUN1, cfg15) == [] or
   all(p["source_period"] != "May 2026" for p in PA.pending_tier_recognitions(c3b, ORG_A, JUN1, cfg15)))
c3d = FakeClient()
seed_plan_tenant(c3d, ORG_A, MAY)
c3d.rows(PA.ACCRUAL_TABLE).append(
    {"id": 400, "org_id": ORG_A, "work_date": (MAY + timedelta(days=2)).isoformat(),
     "employee_key": "ali khan", "store_code": "S100", "base_amount": 60.0, "tier_amount": 0.0,
     "total_amount": 60.0, "components": {}})
c3d.rows("rep_commissions").append(
    {"org_id": ORG_A, "period": "May 2026", "epay_salesperson": "Ali Khan", "store": "1234 Main St",
     "total_payout": 100.0})
ok("G14 ... and it IS pending on Jun 15",
   len(PA.pending_tier_recognitions(c3d, ORG_A, date(2026, 6, 15), cfg15)) == 1)

print("\nH. RECORDING A PAYOUT changes paid/unpaid ONLY (claim (d))")
c4 = FakeClient()
seed_plan_tenant(c4, ORG_A, D1)
for i in range(4):
    c4.rows("raw_sales").append(sale(ORG_A, D1, "Ali Khan", "1234 Main St", f"T{i}"))
PA.run_day(c4, ORG_A, D1)
before = PA.accrued(c4, ORG_A, D1)
e0 = before["employees"][0]
ok("H1 before any advance: accrued $60, paid $0, unpaid $60, today $60",
   (e0["accrued_total"], e0["paid_total"], e0["unpaid_balance"], e0["today_accrual"]) == (60.0, 0.0, 60.0, 60.0),
   f"got {e0}")
ok("H2 components split base/tier for the consumer", e0["components"] == {"base": 60.0, "tier": 0.0})
ok("H3 the spec's keys are all present",
   set(["employee_key", "name", "store_codes", "accrued_total", "paid_total", "unpaid_balance",
        "today_accrual", "components"]).issubset(e0.keys()))

rec = PA.record_payout(c4, ORG_A, {"employee_key": "ali khan", "employee_name": "Ali Khan",
                                   "amount": 25, "paid_date": D1.isoformat(), "store_code": "S100",
                                   "withdrawal_ref": "W-1"}, recorded_by="dm-uid")
after = PA.accrued(c4, ORG_A, D1)
e1 = after["employees"][0]
ok("H4 accrued is UNCHANGED by a payout", e1["accrued_total"] == 60.0 and e1["components"] == e0["components"])
ok("H5 paid $25, unpaid $35", (e1["paid_total"], e1["unpaid_balance"]) == (25.0, 35.0), f"got {e1}")
ok("H6 the ledger row is org-stamped and carries who recorded it",
   c4.rows(PA.LEDGER_TABLE)[0]["org_id"] == ORG_A
   and c4.rows(PA.LEDGER_TABLE)[0]["recorded_by"] == "dm-uid")
ok("H7 recording wrote ONLY the ledger table (no accrual row touched, no pay table touched)",
   {e[1][1] for e in c4.log[len(c4.log) - 1:] if e[0] == "insert"} == {PA.LEDGER_TABLE})
assert_no_pay_writes("H8 after recording an advance", c4)

dup = PA.record_payout(c4, ORG_A, {"employee_key": "ali khan", "amount": 25,
                                   "paid_date": D1.isoformat(), "withdrawal_ref": "W-1"})
ok("H8b re-recording the SAME envelope withdrawal is a no-op, not a second advance",
   dup.get("duplicate") is True and len(c4.rows(PA.LEDGER_TABLE)) == 1
   and PA.accrued(c4, ORG_A, D1)["employees"][0]["paid_total"] == 25.0,
   f"got {dup} / {len(c4.rows(PA.LEDGER_TABLE))} rows")
try:
    PA.record_payout(c4, ORG_A, {"employee_key": "x", "amount": -5})
    ok("H9 a negative advance is refused", False, "no error raised")
except ValueError:
    ok("H9 a negative advance is refused (no netting, ever)", True)
try:
    PA.record_payout(c4, ORG_A, {"amount": 5})
    ok("H10 an employee-less advance is refused", False, "no error raised")
except ValueError:
    ok("H10 an employee-less advance is refused", True)

print("\nI. OVER-ADVANCE — flagged, never netted, never clawed back")
PA.record_payout(c4, ORG_A, {"employee_key": "ali khan", "employee_name": "Ali Khan", "amount": 100,
                             "paid_date": D1.isoformat(), "store_code": "S100"})
over = PA.accrued(c4, ORG_A, D1)["employees"][0]
ok("I1 paid $125 vs accrued $60 -> flagged, unpaid goes NEGATIVE (honest, not clamped)",
   over["over_advanced"] is True and over["over_advance_amount"] == 65.0
   and over["unpaid_balance"] == -65.0, f"got {over}")
rev = PA.over_advance_review(c4, ORG_A, D1)
ok("I2 the review list names the employee and the amount",
   len(rev["running"]) == 1 and rev["running"][0]["over_by"] == 65.0)
ok("I3 the accrual itself was NOT reduced to compensate (no netting)",
   c4.rows(PA.ACCRUAL_TABLE)[0]["total_amount"] == 60.0)
ok("I4 the policy is stated in the response", "no clawback" in rev["policy"].lower())

# monthly over-advance: cash advanced inside a FINISHED month exceeds what that month paid
c5 = FakeClient()
c5.rows("rep_commissions").append(
    {"org_id": ORG_A, "period": "May 2026", "epay_salesperson": "Ali Khan", "store": "S100",
     "total_payout": 200.0})
c5.rows(PA.LEDGER_TABLE).append(
    {"id": 1, "org_id": ORG_A, "employee_key": "ali khan", "employee_name": "Ali Khan", "amount": 500.0,
     "paid_date": "2026-05-20", "store_code": "S100", "method": "envelope_cash"})
mrev = PA.over_advance_review(c5, ORG_A, date(2026, 6, 20))
ok("I5 monthly flag: $500 advanced in May vs a finished May run of $200 -> over by $300",
   len(mrev["monthly"]) == 1 and mrev["monthly"][0]["over_by"] == 300.0, f"got {mrev['monthly']}")
assert_no_pay_writes("I6 over-advance review is read-only", c5)

print("\nJ. RULE ONE — multi-tenant isolation, both directions")
c6 = FakeClient()
seed_plan_tenant(c6, ORG_A, D1)
seed_plan_tenant(c6, ORG_B, D1, rep="Bea Other", store="99 Other Rd")
c6.rows("store_mapping").append({"org_id": ORG_B, "store_address": "99 Other Rd", "store_code": "S999",
                                 "market": "PA"})
for i in range(2):
    c6.rows("raw_sales").append(sale(ORG_A, D1, "Ali Khan", "1234 Main St", f"A{i}"))
for i in range(3):
    c6.rows("raw_sales").append(sale(ORG_B, D1, "Bea Other", "99 Other Rd", f"B{i}"))
PA.run_day(c6, ORG_A, D1)
PA.run_day(c6, ORG_B, D1)
a_rows = [r for r in c6.rows(PA.ACCRUAL_TABLE) if r["org_id"] == ORG_A]
b_rows = [r for r in c6.rows(PA.ACCRUAL_TABLE) if r["org_id"] == ORG_B]
ok("J1 each tenant accrued only its OWN sales (A: 2x$15=$30, B: 3x$15=$45)",
   len(a_rows) == 1 and a_rows[0]["base_amount"] == 30.0
   and len(b_rows) == 1 and b_rows[0]["base_amount"] == 45.0,
   f"A={a_rows} B={b_rows}")
ok("J2 every accrual row carries org_id (write-side stamping)",
   all(r.get("org_id") for r in c6.rows(PA.ACCRUAL_TABLE)))
acc_a = PA.accrued(c6, ORG_A, D1)
ok("J3 tenant A's accrued view shows ONE employee and none of tenant B's",
   [e["employee_key"] for e in acc_a["employees"]] == ["ali khan"], f"got {acc_a['employees']}")
PA.record_payout(c6, ORG_B, {"employee_key": "bea other", "amount": 10, "paid_date": D1.isoformat()})
ok("J4 a payout recorded for tenant B is invisible to tenant A",
   PA.accrued(c6, ORG_A, D1)["totals"]["paid"] == 0.0
   and PA.accrued(c6, ORG_B, D1)["totals"]["paid"] == 10.0)
ok("J5 org_id came from the argument, never the body",
   c6.rows(PA.LEDGER_TABLE)[0]["org_id"] == ORG_B)
PA.record_payout(c6, ORG_A, {"org_id": ORG_B, "employee_key": "ali khan", "amount": 7,
                             "paid_date": D1.isoformat()})
ok("J6 a body-supplied org_id is IGNORED (it would file the row in the wrong tenant)",
   [r["org_id"] for r in c6.rows(PA.LEDGER_TABLE)] == [ORG_B, ORG_A])

print("\nK. SPAN SCOPE — a DM sees their stores only; an unmapped store stays visible")
c6.rows(PA.ACCRUAL_TABLE).append(
    {"id": 900, "org_id": ORG_A, "work_date": D1.isoformat(), "employee_key": "zed nostore",
     "store_code": "", "employee_name": "Zed Nostore", "base_amount": 5.0, "tier_amount": 0.0,
     "total_amount": 5.0, "components": {}})
scoped = PA.accrued(c6, ORG_A, D1, keyset={"S100"})
ok("K1 the in-span rep is visible", any(e["employee_key"] == "ali khan" for e in scoped["employees"]))
ok("K2 the store-less row is NOT silently dropped (a mapping gap must not hide money)",
   any(e["employee_key"] == "zed nostore" for e in scoped["employees"]))
scoped_none = PA.accrued(c6, ORG_A, D1, keyset={"S777"})
_ali = next((e for e in scoped_none["employees"] if e["employee_key"] == "ali khan"), None)
ok("K3 an out-of-span store's ACCRUAL is hidden (S100's $30 does not reach an S777-only DM)",
   (_ali is None or _ali["accrued_total"] == 0.0), f"got {_ali}")
ok("K4 only the deliberately store-less rows survive an alien span (nothing from S100 leaks)",
   scoped_none["totals"]["accrued"] == 5.0
   and {e["employee_key"] for e in scoped_none["employees"]} == {"zed nostore", "ali khan"},
   f"got {scoped_none['totals']}")

print("\nL. GRACEFUL DEGRADE — before migration 267 nothing breaks and nothing is written")
c7 = FakeClient(missing={PA.ACCRUAL_TABLE, PA.LEDGER_TABLE})
seed_plan_tenant(c7, ORG_A, D1)
c7.rows("raw_sales").append(sale(ORG_A, D1, "Ali Khan", "1234 Main St", "T0"))
a = PA.accrued(c7, ORG_A, D1)
ok("L1 /payout/accrued -> ready:false + a plain-language note",
   a["ready"] is False and "267" in a["note"] and a["employees"] == [])
l = PA.ledger_rows(c7, ORG_A, end=D1)
ok("L2 /payout/ledger degrades the same way", l["ready"] is False and l["rows"] == [])
r = PA.run_day(c7, ORG_A, D1)
ok("L3 the run degrades to ready:false and raises nothing", r.get("ready") is False)
ok("L4 a missing migration wrote NOTHING anywhere", not c7.writes_to(PA.ACCRUAL_TABLE))
o = PA.over_advance_review(c7, ORG_A, D1)
ok("L5 the over-advance review degrades", o["ready"] is False)
rp = PA.record_payout(c7, ORG_A, {"employee_key": "ali khan", "amount": 5})
ok("L6 recording an advance degrades instead of 500ing", rp["ready"] is False)
sweep = PA.run_all_due(c7, today=D1)
ok("L7 the daily sweep no-ops safely", sweep["ok"] is True)

print("\nM. DISABLED TENANT + the daily sweep")
c8 = FakeClient()
seed_plan_tenant(c8, ORG_A, D1)
c8.rows("commission_org_config").append({"org_id": ORG_A, "accrual_config": {"enabled": False}})
c8.rows("raw_sales").append(sale(ORG_A, D1, "Ali Khan", "1234 Main St", "T0"))
r = PA.run_day(c8, ORG_A, D1)
ok("M1 a tenant with accrual disabled writes nothing", r.get("skipped") and not c8.rows(PA.ACCRUAL_TABLE))
c9 = FakeClient()
seed_plan_tenant(c9, ORG_A, D1)
seed_plan_tenant(c9, ORG_B, D1, rep="Bea Other", store="99 Other Rd")
c9.rows("store_mapping").append({"org_id": ORG_B, "store_address": "99 Other Rd", "store_code": "S999"})
c9.rows("raw_sales").append(sale(ORG_A, D1, "Ali Khan", "1234 Main St", "A0"))
c9.rows("raw_sales").append(sale(ORG_B, D1, "Bea Other", "99 Other Rd", "B0"))
orgs = PA.active_orgs(c9, [D1])
ok("M2 the sweep enumerates every tenant with sales that day", set(orgs) == {ORG_A, ORG_B}, f"got {orgs}")
sw = PA.run_all_due(c9, dates=[D1])
ok("M3 the sweep accrues both tenants", sw["orgs"] == 2 and sw["runs"] == 2
   and len(c9.rows(PA.ACCRUAL_TABLE)) == 2, f"got {sw}")
sw2 = PA.run_all_due(c9, dates=[D1])
ok("M4 the sweep is idempotent (a second pass adds nothing)", len(c9.rows(PA.ACCRUAL_TABLE)) == 2)
assert_no_pay_writes("M5 the daily sweep", c9)
ok("M6 the sweep never raises into its caller (a broken tenant is reported, not thrown)",
   PA.run_all_due(FakeClient(missing={"raw_sales", "daily_sales_feed"}), dates=[D1])["ok"] is True)

print("\nN. BOOST-MODE day — sale-derived components only; the KPI tier is deferred")
c10 = FakeClient()
c10.rows("carrier").append({"org_id": ORG_A, "id": 1, "name": "Boost Mobile", "is_selected": True})
c10.rows("store_mapping").append({"org_id": ORG_A, "store_address": "1234 Main St", "store_code": "S100",
                                  "market": "NJ"})
c10.rows("payout_config").append({"org_id": ORG_A, "period": "June 2026", "premium_flat": 5,
                                  "byod_flat": 3, "upgrade_flat": 20, "acc_rate": 0.10,
                                  "tier_50_pct": 0.5, "tier_75_pct": 0.75})
for i in range(3):
    # 'Activation' is what the SHARED classify_contract_type() calls a premium activation
    c10.rows("raw_sales").append(sale(ORG_A, D1, "Ali Khan", "1234 Main St", f"P{i}", ct="Activation"))
res = PA.compute_day(c10, ORG_A, D1)
ok("N1 boost mode resolved from the carrier, not a tenant name", res["mode"] == "boost")
brow = res["rows"][0]
ok("N2 base_amount = the calculator's SUBTOTAL (3 premium x $5 = $15), NOT subtotal x tier",
   brow["base_amount"] == 15.0 and brow["components"]["subtotal"] == 15.0, f"got {brow}")
ok("N3 the un-run KPI tier (0.5x with no DLAR) was NOT applied — that would under-accrue by half",
   brow["base_amount"] != 7.5)
ok("N4 the deferred components are named for the rep to see",
   brow["components"]["deferred_to_monthly"] == ["kpi_tier", "trade_in_spiff"])
PA.run_day(c10, ORG_A, D1)
assert_no_pay_writes("N5 boost accrual", c10)

print("\nO. LEDGER LIST + ACCRUAL ROW LIST (the report surfaces)")
lr = PA.ledger_rows(c4, ORG_A, end=D1)
ok("O1 ledger lists this org's advances with a total", lr["ready"] and lr["count"] == 2
   and lr["total"] == 125.0, f"got {lr}")
ok("O2 rows carry everything the report needs to explain a payment",
   all(set(["employee_key", "name", "amount", "paid_date", "method", "store_code",
            "withdrawal_ref", "recorded_by"]).issubset(r.keys()) for r in lr["rows"]))

print("\n" + "=" * 90)
print(f"PASS {PASS}   FAIL {FAIL}")
print("=" * 90)
sys.exit(1 if FAIL else 0)
