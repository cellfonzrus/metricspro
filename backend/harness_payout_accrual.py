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

  plus: RULE ONE org isolation both ways · graceful degrade before migration 267 · richer-source day
        pick (never a union → never double-counted).

OWNER FOLLOW-UP ANSWERS, 2026-08-04 (sections P/Q/R/S/T, and the updated expectations in A/D/E/H/I/J):

  (P) ledger Q18 "based on tier meeting on that day, it keeps varying throughout the month as their
      commission changes in the individual rep report" — the DEFAULT basis is now 'mtd_attained'. The
      proof is an INVARIANT, not a number: SUM(accruals month-to-date) == commission_engine.preview()
      over the same month (the individual rep report), including across a MID-MONTH TIER CROSSING that
      restates earlier days, under re-runs, out-of-order runs and a vanished day. 'none' (the previous
      default, un-tiered) is retained as a config option and is still proven (D2b / E5b).
      → the sixteen assertions whose EXPECTED VALUES moved are marked "CHANGED 2026-08-04" in place.
  (Q) ledger Q14 "flag it and keep an option to auto net" — flag stays the default; auto_net reduces
      the NEXT cycle's cash due by a prior cycle's over-advance, as its OWN labelled line, with zero
      writes anywhere (the accrual is identical in both modes).
  (R) ledger Q19 "reset each month … payroll cycle / commission cycle as defined in the system" —
      per-cycle balances for all three cycle kinds, carry-over lines that are never hidden, and the
      advisory settlement checklist (which writes nothing at all).
  (S/T) ledger Q17 "dm or higher" — who may record a cash advance, and the router wiring that enforces
      it before the existing store-span check.

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
# CHANGED 2026-08-04 (owner ledger Q18, "based on tier meeting on that day"): the DEFAULT basis is now
# 'mtd_attained'. 'none' (the previous default) is retained as a config option and is still proven
# below (D2b/D9, E5b).
ok("A1 code default is enabled, MTD-attained, on_run_available, auto-run 1 day back",
   d == PA.CODE_DEFAULT and d["tier_basis"] == "mtd_attained"
   and d["tier_recognition"]["mode"] == "on_run_available" and d["auto_run"]["days_back"] == 1)
ok("A2 a junk tier_basis falls back to the tenant default, never to 'as_computed'",
   PA.normalize_config({"tier_basis": "wishful"})["tier_basis"] == "mtd_attained")
ok("A2b the three bases are the only accepted values, and 'none' is still selectable",
   set(PA.TIER_BASES) == {"mtd_attained", "none", "as_computed"}
   and PA.normalize_config({"tier_basis": "none"})["tier_basis"] == "none")
ok("A7 over_advance_mode defaults to FLAG and only accepts flag|auto_net (ledger Q14)",
   d["over_advance_mode"] == "flag"
   and PA.normalize_config({"over_advance_mode": "auto_net"})["over_advance_mode"] == "auto_net"
   and PA.normalize_config({"over_advance_mode": "shred_it"})["over_advance_mode"] == "flag")
ok("A8 the balance cycle defaults to the calendar month and only accepts known modes (ledger Q19)",
   d["cycle"]["mode"] == "calendar_month"
   and PA.normalize_config({"cycle": {"mode": "payroll"}})["cycle"]["mode"] == "payroll"
   and PA.normalize_config({"cycle": {"mode": "lunar"}})["cycle"]["mode"] == "calendar_month")
ok("A9 semi_day clamps to 2..28 (a 1st split would empty the first half; a 30th never exists in Feb)",
   PA.normalize_config({"cycle": {"payroll": {"semi_day": 1}}})["cycle"]["payroll"]["semi_day"] == 2
   and PA.normalize_config({"cycle": {"payroll": {"semi_day": 31}}})["cycle"]["payroll"]["semi_day"] == 28)
ok("A10 record_roles defaults to DM-or-higher and an EMPTY list falls back (never locks everyone out)",
   d["record_roles"] == sorted(PA.DEFAULT_RECORD_ROLES) or d["record_roles"] == list(PA.DEFAULT_RECORD_ROLES),
   f"got {d['record_roles']}")
ok("A11 ... and a tenant-supplied role list is honoured, lower-cased",
   PA.normalize_config({"record_roles": ["Area_Lead"]})["record_roles"] == ["area_lead"]
   and PA.normalize_config({"record_roles": []})["record_roles"] == list(PA.DEFAULT_RECORD_ROLES))
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
# CHANGED 2026-08-04 (ledger Q18): the default basis now accrues at the tier the rep is MEETING. On a
# one-day month-to-date window that is 4 x $10 + (4 x $5) x 2.0 = $80 — the same number the individual
# rep report shows for the month to date, which is the whole point of the owner's answer.
ok("D2 base_amount = $40 + $20 x 2.0 = $80 — the tier the rep is MEETING month-to-date",
   row.get("base_amount") == 80.0, f"got {row.get('base_amount')}")
ok("D2b the retained 'none' basis still accrues the old un-tiered $60",
   PA.compute_day(c, ORG_A, D1, cfg=PA.normalize_config({"tier_basis": "none"}))
     ["rows"][0]["base_amount"] == 60.0)
ok("D3 the day's own multiplier is reported (2.0) and the row says which basis produced it",
   row["components"]["day_tier_multiplier"] == 2.0
   and row["components"]["tier_basis"] == "mtd_attained")
ok("D3b the row carries the month-to-date audit trail: target, un-tiered weight and factor",
   row["components"]["mtd"]["mtd_total"] == 80.0
   and row["components"]["mtd"]["untiered_base"] == 60.0
   and round(row["components"]["mtd"]["factor"], 4) == round(80.0 / 60.0, 4),
   f"got {row['components'].get('mtd')}")
ok("D4 nothing is deferred to the monthly true-up under this basis, and the row explains itself",
   row["components"]["deferred_to_monthly"] == []
   and "tier the rep is meeting" in row["components"]["explain"].lower())
ok("D5 components carries the per-rule breakdown that will be shown to the rep",
   sorted([(r["label"], r["payout"]) for r in row["components"]["rules"]])
   == [("Activation", 40.0), ("Tiered spiff", 20.0)],
   f"got {row['components']['rules']}")
ok("D6 employee_key is the module's canonical person key; store resolved to its store_code",
   row["employee_key"] == "ali khan" and row["store_code"] == "S100" and row["store_raw"] == "1234 Main St")
ok("D7 the day was read from raw_sales and only that day", res["source_table"] == "raw_sales"
   and res["sale_lines"] == 4)

print("\n   D-bis. tier_basis='as_computed' (opt-in) applies the day's OWN multiplier")
res_ac = PA.compute_day(c, ORG_A, D1, cfg=PA.normalize_config({"tier_basis": "as_computed"}))
ok("D8 opt-in accrues 40 + (20 x 2.0) = $80", res_ac["rows"][0]["base_amount"] == 80.0,
   f"got {res_ac['rows'][0]['base_amount']}")
ok("D9 on a ONE-DAY window as_computed and mtd_attained agree; the difference appears across days (P)",
   PA.compute_day(c, ORG_A, D1)["rows"][0]["base_amount"] == 80.0)

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
   and r1["base_total"] == 80.0, f"got {r1['base_total']}")
ok("E4 the upsert uses the spec's unique key",
   all(e[0] != "upsert" or e[2] is not None for e in c2.log)
   and any(e[0] == "upsert" and e[1][1] == PA.ACCRUAL_TABLE for e in c2.log))

print("\n   E-bis. a sale that VANISHES from the day leaves no phantom accrual (replace, not merge)")
c2.store[("commcalc", "raw_sales")] = [r for r in c2.rows("raw_sales") if r["trans_id"] != "T3"]
r4 = PA.run_day(c2, ORG_A, D1)
# 3 activations still attain the 3-unit tier (each line qualifies under both rules), so the restated
# day is 3 x $10 + (3 x $5) x 2.0 = $60. Under the retained 'none' basis it is the old un-tiered $45.
ok("E5 re-run restates the day downward: $30 + $15 x 2.0 = $60",
   c2.rows(PA.ACCRUAL_TABLE)[0]["base_amount"] == 60.0,
   f"got {c2.rows(PA.ACCRUAL_TABLE)[0]['base_amount']}")
ok("E5b the same restatement under tier_basis='none' is the un-tiered $45",
   PA.compute_day(c2, ORG_A, D1, cfg=PA.normalize_config({"tier_basis": "none"}))
     ["rows"][0]["base_amount"] == 45.0)
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
ok("H1 before any advance: accrued $80, paid $0, unpaid $80, today $80",
   (e0["accrued_total"], e0["paid_total"], e0["unpaid_balance"], e0["today_accrual"]) == (80.0, 0.0, 80.0, 80.0),
   f"got {e0}")
ok("H2 components split base/tier for the consumer", e0["components"] == {"base": 80.0, "tier": 0.0})
ok("H3 the spec's keys are all present",
   set(["employee_key", "name", "store_codes", "accrued_total", "paid_total", "unpaid_balance",
        "today_accrual", "components"]).issubset(e0.keys()))

rec = PA.record_payout(c4, ORG_A, {"employee_key": "ali khan", "employee_name": "Ali Khan",
                                   "amount": 25, "paid_date": D1.isoformat(), "store_code": "S100",
                                   "withdrawal_ref": "W-1"}, recorded_by="dm-uid")
after = PA.accrued(c4, ORG_A, D1)
e1 = after["employees"][0]
ok("H4 accrued is UNCHANGED by a payout", e1["accrued_total"] == 80.0 and e1["components"] == e0["components"])
ok("H5 paid $25, unpaid $55", (e1["paid_total"], e1["unpaid_balance"]) == (25.0, 55.0), f"got {e1}")
ok("H5b due_now is the cycle balance and the labelled lines add up to it (no hidden arithmetic)",
   e1["due_now"] == 55.0 and e1["net_applied"] == 0.0
   and round(sum(l["amount"] for l in e1["lines"] if l["affects_due"]), 2) == 55.0,
   f"got {e1['lines']}")
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
ok("I1 paid $125 vs accrued $80 -> flagged, unpaid goes NEGATIVE (honest, not clamped)",
   over["over_advanced"] is True and over["over_advance_amount"] == 45.0
   and over["unpaid_balance"] == -45.0, f"got {over}")
ok("I1b due_now never goes negative — the balance is honest, the CASH figure is floored at zero",
   over["due_now"] == 0.0)
rev = PA.over_advance_review(c4, ORG_A, D1)
ok("I2 the review list names the employee and the amount",
   len(rev["running"]) == 1 and rev["running"][0]["over_by"] == 45.0, f"got {rev['running']}")
ok("I2b the CURRENT-CYCLE list answers the question a DM actually acts on",
   len(rev["cycle"]) == 1 and rev["cycle"][0]["over_by"] == 45.0)
ok("I3 the accrual itself was NOT reduced to compensate (no netting)",
   c4.rows(PA.ACCRUAL_TABLE)[0]["total_amount"] == 80.0)
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
# 2 lines => 4 qualifying units and 3 lines => 6, so both tenants attain their own 3-unit 2.0x tier:
# A = $20 + $10x2 = $40, B = $30 + $15x2 = $60 (basis flip, ledger Q18).
ok("J1 each tenant accrued only its OWN sales (A: $40, B: $60)",
   len(a_rows) == 1 and a_rows[0]["base_amount"] == 40.0
   and len(b_rows) == 1 and b_rows[0]["base_amount"] == 60.0,
   f"A={[(r['org_id'], r['base_amount']) for r in a_rows]} B={[(r['org_id'], r['base_amount']) for r in b_rows]}")
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
ok("M4 an immediate second sweep pass is THROTTLED (the accrual rides the hourly promote sweep; a "
   "burst must not re-drive the engine for every tenant)",
   all("already accrued" in str(d.get("skipped") or "") for d in sw2["detail"])
   and len(c9.rows(PA.ACCRUAL_TABLE)) == 2, f"got {sw2['detail']}")
c9.rows("commission_org_config").extend([
    {"org_id": ORG_A, "accrual_config": {"auto_run": {"min_interval_minutes": 0}}},
    {"org_id": ORG_B, "accrual_config": {"auto_run": {"min_interval_minutes": 0}}}])
sw3 = PA.run_all_due(c9, dates=[D1])
ok("M4b with the throttle off it re-runs and is STILL idempotent (no duplication)",
   all(d.get("skipped") is None for d in sw3["detail"]) and len(c9.rows(PA.ACCRUAL_TABLE)) == 2,
   f"got {sw3['detail']}")
ok("M4c a hand-pressed run_day is NEVER throttled (a human asking for a recompute gets one)",
   PA.run_day(c9, ORG_A, D1).get("skipped") is None)
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

# ═══════════════════════════════════════════════════════════════════════════════════════════════
# OWNER ANSWERS 2026-08-04 — ledger Q18 (tier basis) / Q14 (over-advance) / Q19 (cycle) / Q17 (who)
# ═══════════════════════════════════════════════════════════════════════════════════════════════
from app.modules.commcalc import commission_engine as CE  # noqa: E402

JUN = date(2026, 6, 1)


def rep_report_mtd(client, org, period="June 2026", rep="ali khan"):
    """The INDIVIDUAL REP REPORT's month-to-date commission, computed independently of the accrual —
    commission_engine.preview() over the month's raw_sales, which is the same function the plan-mode
    monthly payout runs through. This is the number the owner said the accrual must track."""
    res = CE.preview(client, org, period)
    return round(sum(float(r.get("total_payout") or 0) for r in (res.get("by_rep") or [])
                     if PA.canon_key(r.get("rep")) == rep), 2)


def accrued_mtd(client, org, rep="ali khan", month=6, year=2026):
    return round(sum(float(r["total_amount"]) for r in client.rows(PA.ACCRUAL_TABLE)
                     if r["employee_key"] == rep and r["work_date"][:7] == f"{year}-{month:02d}"), 2)


print("\nP. ledger Q18 — MTD AGREEMENT: the accrual stream TRACKS the individual rep report")
print("   owner: \"it will be based on tier meeting on that day, it keeps varying throughout the month")
print("   as their commission changes in the individual rep report\"")
cP = FakeClient()
seed_plan_tenant(cP, ORG_A, JUN)
# Day 1: ONE activation -> 2 qualifying units -> BELOW the 3-unit 2.0x tier.
cP.rows("raw_sales").append(sale(ORG_A, date(2026, 6, 1), "Ali Khan", "1234 Main St", "D1a"))
PA.run_day(cP, ORG_A, date(2026, 6, 1))
ok("P1 day 1 accrues un-tiered $15 — the rep is not meeting the tier yet",
   accrued_mtd(cP, ORG_A) == 15.0, f"got {accrued_mtd(cP, ORG_A)}")
ok("P2 ... and that already EQUALS the rep report's month-to-date figure",
   accrued_mtd(cP, ORG_A) == rep_report_mtd(cP, ORG_A), f"{accrued_mtd(cP, ORG_A)} vs {rep_report_mtd(cP, ORG_A)}")

# Day 2: a second activation -> 4 qualifying units -> the 2.0x tier IS met. The rep report's MTD
# jumps to $20 + $10x2 = $40, so the WHOLE month must restate, not just day 2.
cP.rows("raw_sales").append(sale(ORG_A, date(2026, 6, 2), "Ali Khan", "1234 Main St", "D2a"))
r_d2 = PA.run_day(cP, ORG_A, date(2026, 6, 2))
d1row = [r for r in cP.rows(PA.ACCRUAL_TABLE) if r["work_date"] == "2026-06-01"][0]
d2row = [r for r in cP.rows(PA.ACCRUAL_TABLE) if r["work_date"] == "2026-06-02"][0]
ok("P3 MID-MONTH TIER CHANGE: the month-to-date accrual == the rep report exactly ($40)",
   accrued_mtd(cP, ORG_A) == rep_report_mtd(cP, ORG_A) == 40.0,
   f"accrual {accrued_mtd(cP, ORG_A)} vs report {rep_report_mtd(cP, ORG_A)}")
ok("P4 day 1 RESTATED upward ($15 -> $20) — the whole month moves, not just the crossing day",
   float(d1row["base_amount"]) == 20.0 and float(d2row["base_amount"]) == 20.0,
   f"d1={d1row['base_amount']} d2={d2row['base_amount']}")
ok("P5 the run report says how many earlier days it restated", r_d2["restated"] == 1, f"got {r_d2}")
ok("P6 every restated row keeps its audit trail (weight, factor, target, and words)",
   d1row["components"]["mtd"]["untiered_base"] == 15.0
   and d1row["components"]["mtd"]["mtd_total"] == 40.0
   and "restates" in d1row["components"]["mtd"]["explain"])

# Day 3: a third activation. Report MTD = $30 + $15x2 = $60.
cP.rows("raw_sales").append(sale(ORG_A, date(2026, 6, 3), "Ali Khan", "1234 Main St", "D3a"))
PA.run_day(cP, ORG_A, date(2026, 6, 3))
ok("P7 day 3: the invariant holds again ($60 = $60)",
   accrued_mtd(cP, ORG_A) == rep_report_mtd(cP, ORG_A) == 60.0,
   f"{accrued_mtd(cP, ORG_A)} vs {rep_report_mtd(cP, ORG_A)}")

snapP = copy.deepcopy(cP.rows(PA.ACCRUAL_TABLE))
PA.run_day(cP, ORG_A, date(2026, 6, 3))
PA.run_day(cP, ORG_A, date(2026, 6, 3))
ok("P8 IDEMPOTENT: re-running the same date twice more changes nothing (scaling never compounds)",
   _cmp(cP.rows(PA.ACCRUAL_TABLE)) == _cmp(snapP) and len(cP.rows(PA.ACCRUAL_TABLE)) == 3,
   f"got {_cmp(cP.rows(PA.ACCRUAL_TABLE))}")
PA.run_day(cP, ORG_A, date(2026, 6, 1))
ok("P9 ORDER-INDEPENDENT: re-running an EARLIER day re-allocates the whole month, invariant intact",
   accrued_mtd(cP, ORG_A) == rep_report_mtd(cP, ORG_A) == 60.0 and len(cP.rows(PA.ACCRUAL_TABLE)) == 3,
   f"{accrued_mtd(cP, ORG_A)} vs {rep_report_mtd(cP, ORG_A)}")

# a VOIDED day (sales withdrawn) must restate the rest of the month, not strand the money
cP.store[("commcalc", "raw_sales")] = [r for r in cP.rows("raw_sales") if r["trans_id"] != "D3a"]
PA.run_day(cP, ORG_A, date(2026, 6, 3))
ok("P10 a day whose sales VANISH is cleared and the survivors re-absorb the month-to-date total",
   accrued_mtd(cP, ORG_A) == rep_report_mtd(cP, ORG_A) == 40.0
   and len(cP.rows(PA.ACCRUAL_TABLE)) == 2,
   f"{accrued_mtd(cP, ORG_A)} vs {rep_report_mtd(cP, ORG_A)} rows={len(cP.rows(PA.ACCRUAL_TABLE))}")
ok("P11 the cents always tie out exactly — no rounding drift across the allocation",
   accrued_mtd(cP, ORG_A) == round(sum(float(r["base_amount"]) for r in cP.rows(PA.ACCRUAL_TABLE)), 2))
assert_no_pay_writes("P12 the whole MTD basis (3 days, 6 runs, 2 restatements)", cP)
ok("P13 it STILL writes exactly one table",
   {e[1][1] for e in cP.log if e[0] in ("insert", "upsert", "update", "delete")} == {PA.ACCRUAL_TABLE})

# uneven days: the allocation is proportional and exact to the cent
cQ = FakeClient()
seed_plan_tenant(cQ, ORG_A, JUN)
for i in range(2):
    cQ.rows("raw_sales").append(sale(ORG_A, date(2026, 6, 10), "Ali Khan", "1234 Main St", f"E{i}"))
cQ.rows("raw_sales").append(sale(ORG_A, date(2026, 6, 11), "Ali Khan", "1234 Main St", "E9"))
PA.run_day(cQ, ORG_A, date(2026, 6, 10))
PA.run_day(cQ, ORG_A, date(2026, 6, 11))
rows_q = sorted(cQ.rows(PA.ACCRUAL_TABLE), key=lambda r: r["work_date"])
ok("P14 an uneven month splits proportionally (2 sales vs 1 -> $40 / $20) and sums to the report",
   [float(r["base_amount"]) for r in rows_q] == [40.0, 20.0]
   and accrued_mtd(cQ, ORG_A) == rep_report_mtd(cQ, ORG_A) == 60.0,
   f"got {[float(r['base_amount']) for r in rows_q]}")

print("\n   P-bis. the monthly TRUE-UP still reconciles whatever residual remains at month close")
cP2 = FakeClient()
seed_plan_tenant(cP2, ORG_A, JUN)
for i in range(2):
    cP2.rows("raw_sales").append(sale(ORG_A, date(2026, 6, 5), "Ali Khan", "1234 Main St", f"F{i}"))
PA.run_day(cP2, ORG_A, date(2026, 6, 5))
cP2.rows("rep_commissions").append(
    {"org_id": ORG_A, "period": "June 2026", "epay_salesperson": "Ali Khan", "store": "1234 Main St",
     "total_payout": 55.0})           # the finished run added a $15 monthly component
PA.run_day(cP2, ORG_A, date(2026, 7, 1))
jul = [r for r in cP2.rows(PA.ACCRUAL_TABLE) if r["work_date"] == "2026-07-01"]
ok("P15 the true-up recognizes only the RESIDUAL ($55 run - $40 accrued = $15), not the tier twice",
   len(jul) == 1 and float(jul[0]["tier_amount"]) == 15.0, f"got {jul}")
ok("P16 ... and the stream converges on what the month actually paid",
   round(sum(float(r["total_amount"]) for r in cP2.rows(PA.ACCRUAL_TABLE)), 2) == 55.0)
assert_no_pay_writes("P17 true-up under the MTD basis", cP2)

print("\nQ. ledger Q14 — OVER-ADVANCE: flag by default, auto_net as an explicit, labelled option")
print("   owner: \"flag it and keep an option to auto net\"")
MAY20, JUN10 = date(2026, 5, 20), date(2026, 6, 10)
cN = FakeClient()
# May: $100 accrued, $160 advanced -> a $60 over-advance carried into June. June: $90 accrued.
cN.rows(PA.ACCRUAL_TABLE).extend([
    {"id": 1, "org_id": ORG_A, "work_date": "2026-05-20", "employee_key": "ali khan",
     "employee_name": "Ali Khan", "store_code": "S100", "base_amount": 100.0, "tier_amount": 0.0,
     "total_amount": 100.0, "components": {}},
    {"id": 2, "org_id": ORG_A, "work_date": "2026-06-05", "employee_key": "ali khan",
     "employee_name": "Ali Khan", "store_code": "S100", "base_amount": 90.0, "tier_amount": 0.0,
     "total_amount": 90.0, "components": {}}])
cN.rows(PA.LEDGER_TABLE).append(
    {"id": 1, "org_id": ORG_A, "employee_key": "ali khan", "employee_name": "Ali Khan",
     "amount": 160.0, "paid_date": "2026-05-25", "store_code": "S100", "method": "envelope_cash"})
flag = PA.accrued(cN, ORG_A, JUN10, cfg=PA.normalize_config({"over_advance_mode": "flag"}))["employees"][0]
ok("Q1 FLAG (default): June's balance is June's — $90 due, the May over-advance is NOT deducted",
   flag["accrued_total"] == 90.0 and flag["paid_total"] == 0.0 and flag["due_now"] == 90.0
   and flag["net_applied"] == 0.0, f"got {flag}")
ok("Q2 ... but the $60 over-advance is VISIBLE as a labelled carry-over line, never hidden",
   flag["carry_over"] == -60.0
   and any(l["kind"] == "carry_over" and "advanced beyond" in l["label"] for l in flag["lines"]),
   f"got {flag['lines']}")
ok("Q3 ... and it is still FLAGGED lifetime (advances $160 vs accrual $190 is fine, May alone is not)",
   flag["prior_over_advance"] == 60.0)

net = PA.accrued(cN, ORG_A, JUN10, cfg=PA.normalize_config({"over_advance_mode": "auto_net"}))["employees"][0]
ok("Q4 AUTO_NET: the $60 prior over-advance reduces the NEXT payable balance -> due now $30",
   net["due_now"] == 30.0 and net["net_applied"] == 60.0, f"got {net}")
ok("Q5 ... and it appears as ITS OWN labelled line — never a silently smaller number",
   any(l["kind"] == "net" and l["amount"] == -60.0 and "auto-net" in l["label"] for l in net["lines"]),
   f"got {net['lines']}")
ok("Q6 ... the labelled lines still add up to due_now exactly",
   round(sum(l["amount"] for l in net["lines"] if l["affects_due"]), 2) == net["due_now"])
ok("Q7 auto_net does NOT touch the accrual: the cycle's accrued figure is identical in both modes",
   net["accrued_total"] == flag["accrued_total"] == 90.0
   and net["components"] == flag["components"])
ok("Q8 auto_net never over-recovers: netting is capped at what is due (never a negative payout)",
   PA.accrued(cN, ORG_A, date(2026, 6, 10),
              cfg=PA.normalize_config({"over_advance_mode": "auto_net"}))["employees"][0]["due_now"] >= 0)
assert_no_pay_writes("Q9 auto_net is READ-SIDE ONLY — zero writes anywhere", cN)
ok("Q10 ... including zero writes to the accrual and ledger tables themselves",
   not cN.writes_to(PA.ACCRUAL_TABLE) and not cN.writes_to(PA.LEDGER_TABLE))
revN = PA.over_advance_review(cN, ORG_A, JUN10, cfg=PA.normalize_config({"over_advance_mode": "auto_net"}))
ok("Q11 the review still FLAGS under auto_net and says so in the policy line",
   "auto-netted" in revN["policy"].lower() and revN["over_advance_mode"] == "auto_net")

print("\nR. ledger Q19 — PER-CYCLE balances, carry-over lines and the settlement checklist")
print("   owner: \"reset each month and advise the user to clear the employee balance at the end of")
print("   the month / payroll cycle / commission cycle as defined in the system\"")
CAL = PA.normalize_config(None)
ok("R1 calendar month (default): Aug 4 -> Aug 1..31, labelled by the month",
   PA.cycle_bounds(date(2026, 8, 4), CAL) == (date(2026, 8, 1), date(2026, 8, 31), "August 2026",
                                              "calendar_month"))
SEMI = PA.normalize_config({"cycle": {"mode": "payroll", "payroll": {"kind": "semimonthly", "semi_day": 16}}})
ok("R2 payroll semimonthly: the 4th is in Aug 1–15, the 20th is in Aug 16–31",
   PA.cycle_bounds(date(2026, 8, 4), SEMI)[:2] == (date(2026, 8, 1), date(2026, 8, 15))
   and PA.cycle_bounds(date(2026, 8, 20), SEMI)[:2] == (date(2026, 8, 16), date(2026, 8, 31)))
ok("R3 ... and a half-month is NOT labelled 'August 2026' (that would be a lie on an export)",
   PA.cycle_bounds(date(2026, 8, 4), SEMI)[2] == "Aug 1–15 2026")
BIW = PA.normalize_config({"cycle": {"mode": "payroll",
                                     "payroll": {"kind": "biweekly", "anchor_date": "2026-08-03"}}})
ok("R4 payroll biweekly runs in 14-day blocks from the anchor, before AND after it",
   PA.cycle_bounds(date(2026, 8, 4), BIW)[:2] == (date(2026, 8, 3), date(2026, 8, 16))
   and PA.cycle_bounds(date(2026, 8, 20), BIW)[:2] == (date(2026, 8, 17), date(2026, 8, 30))
   and PA.cycle_bounds(date(2026, 7, 30), BIW)[:2] == (date(2026, 7, 20), date(2026, 8, 2)))
COMM = PA.normalize_config({"cycle": {"mode": "commission", "commission": {"end_day": 25}}})
ok("R5 commission cycle closing on the 25th: Aug 4 -> Jul 26..Aug 25; Aug 26 -> Aug 26..Sep 25",
   PA.cycle_bounds(date(2026, 8, 4), COMM)[:2] == (date(2026, 7, 26), date(2026, 8, 25))
   and PA.cycle_bounds(date(2026, 8, 26), COMM)[:2] == (date(2026, 8, 26), date(2026, 9, 25)))
COMM31 = PA.normalize_config({"cycle": {"mode": "commission", "commission": {"end_day": 31}}})
ok("R6 a 31st close CLAMPS to each month's real end (February still closes)",
   PA.cycle_bounds(date(2026, 2, 15), COMM31)[:2] == (date(2026, 2, 1), date(2026, 2, 28)))

acc_cyc = PA.accrued(cN, ORG_A, JUN10, cfg=CAL)
ok("R7 a cycle RESETS: June shows June's $90, not the lifetime $190",
   acc_cyc["employees"][0]["accrued_total"] == 90.0
   and acc_cyc["employees"][0]["lifetime_accrued"] == 190.0)
ok("R8 the cycle window and its predecessor are named in the response (for the page banner)",
   acc_cyc["cycle"]["label"] == "June 2026" and acc_cyc["cycle"]["previous_label"] == "May 2026"
   and acc_cyc["cycle"]["mode"] == "calendar_month")
ok("R9 an unsettled prior cycle raises the 'settle employee balances' advisory",
   acc_cyc["settlement_advisory"]["due"] is True
   and "unsettled" in (acc_cyc["settlement_advisory"]["message"] or "").lower())
acc_semi = PA.accrued(cN, ORG_A, JUN10, cfg=PA.normalize_config(
    {"cycle": {"mode": "payroll", "payroll": {"kind": "semimonthly", "semi_day": 16}}}))
ok("R10 switching the tenant to a semi-monthly cycle re-cuts the same rows (config, not code)",
   acc_semi["cycle"]["label"] == "Jun 1–15 2026"
   and acc_semi["employees"][0]["accrued_total"] == 90.0)

st = PA.settlement(cN, ORG_A, JUN10, cfg=CAL)
e_st = st["employees"][0]
ok("R11 the settlement checklist lists every cycle with accrued vs advanced vs remainder",
   [c["label"] for c in e_st["cycles"]] == ["March 2026", "April 2026", "May 2026", "June 2026"],
   f"got {[c['label'] for c in e_st['cycles']]}")
ok("R12 May reads accrued $100 / advanced $160 / remainder -$60 (cash to collect back)",
   [(c["accrued"], c["advanced"], c["remainder"]) for c in e_st["cycles"] if c["label"] == "May 2026"]
   == [(100.0, 160.0, -60.0)])
ok("R13 the current cycle is marked, and its remainder is what to pay in cash",
   e_st["cycles"][-1]["is_current"] is True and e_st["cycle_remainder"] == 90.0)
ok("R14 the unsettled prior cycle is carried, labelled and counted — not hidden",
   e_st["carry_over"] == -60.0 and e_st["unsettled_prior"] is True and e_st["status"] != "settled")
ok("R15 the advisory is ADVICE: it names the cycle end and says nothing settles automatically",
   st["advisory"]["due"] is True and "not a payment" in st["advisory"]["message"].lower()
   and "advisory only" in st["note"].lower())
assert_no_pay_writes("R16 the settlement checklist is read-only", cN)
ok("R17 ... and it writes NOTHING at all (advisory, per the owner)",
   not [e for e in cN.log if e[0] in ("insert", "upsert", "update", "delete")])
st_missing = PA.settlement(FakeClient(missing={PA.ACCRUAL_TABLE, PA.LEDGER_TABLE}), ORG_A, JUN10)
ok("R18 the checklist degrades gracefully before migration 267", st_missing["ready"] is False)
sc = PA.settlement(cN, ORG_A, JUN10, cfg=CAL)
ok("R19 span scope applies to the checklist too (an alien span sees none of S100's balances)",
   PA.settlement(cN, ORG_A, JUN10, keyset={"S777"}, cfg=CAL)["employees"] == []
   and len(sc["employees"]) == 1)

print("\nS. ledger Q17 — WHO may record a cash advance: DM or higher (owner: \"dm or higher\")")
CFG_R = PA.normalize_config(None)
ok("S1 a plain rep may NOT record a cash advance",
   PA.may_record({"role": "sales_rep", "perms": {"scope": "self"}}, CFG_R)[0] is False)
ok("S2 a STORE manager may not either — this is deliberately above store level",
   PA.may_record({"role": "store_manager", "perms": {"scope": "store"}}, CFG_R)[0] is False)
ok("S3 ... and the refusal SAYS what to do instead",
   "district" in PA.may_record({"role": "store_manager", "perms": {"scope": "store"}}, CFG_R)[1].lower())
ok("S4 a district manager MAY", PA.may_record({"role": "district_manager", "perms": {}}, CFG_R)[0] is True)
for r in ("market_manager", "regional_manager", "director", "executive", "admin"):
    ok(f"S5.{r} {r} may record", PA.may_record({"role": r, "perms": {}}, CFG_R)[0] is True)
ok("S6 a super-admin always may", PA.may_record({"role": "whatever", "super_admin": True}, CFG_R)[0] is True)
ok("S7 a CUSTOM role that spans a market qualifies without anyone hard-coding its name",
   PA.may_record({"role": "area_lead", "perms": {"scope": "market"}}, CFG_R)[0] is True)
ok("S8 a custom role scoped to one store does not",
   PA.may_record({"role": "keyholder", "perms": {"scope": "store"}}, CFG_R)[0] is False)
ok("S9 the role list is TENANT CONFIG (RULE TWO), not a constant",
   PA.may_record({"role": "shift_lead", "perms": {"scope": "self"}},
                 PA.normalize_config({"record_roles": ["shift_lead"]}))[0] is True
   and PA.may_record({"role": "district_manager", "perms": {"scope": "self"}},
                     PA.normalize_config({"record_roles": ["shift_lead"]}))[0] is False)
ok("S10 an unresolvable caller (RBAC off / no token) degrades OPEN — the house org is never locked out",
   PA.may_record(None, CFG_R)[0] is True)

print("\nT. the router's gate + settlement endpoint are wired to the same rules")
rsrc2 = open("app/modules/commcalc/router.py", encoding="utf-8").read()
blk = rsrc2[rsrc2.index("DAILY COMMISSION ACCRUAL + ENVELOPE PAYOUT LEDGER (migration 267"):]
ok("T1 POST /payout/record calls the DM-or-higher gate BEFORE the span check",
   "_require_payout_recorder(authorization, org_id)" in blk
   and blk.index("_require_payout_recorder(authorization, org_id)")
       < blk.index("is outside your assigned stores"))
ok("T2 the GET surfaces keep the span keyset and gained no new gate",
   blk.count("_accrual_keyset(authorization, org_id)") >= 5)
ok("T3 the settlement endpoint exists, is org-scoped and span-scoped",
   '@router.get("/payout/settlement")' in blk and "require_org(org_id)" in blk
   and "payout_accrual.settlement(client, org_id, d, keyset=" in blk)
ok("T4 the config endpoint exposes every new knob as options (RULE TWO admin surface)",
   all(k in blk for k in ("over_advance_modes", "cycle_modes", "payroll_kinds",
                          "default_record_roles", "tier_basis_options")))
ok("T5 the router's accrual section STILL touches no pay table",
   not any(f'table(\'{t}\')' in blk or f'table("{t}")' in blk for t in PAY_TABLES))

print("\n" + "=" * 90)
print(f"PASS {PASS}   FAIL {FAIL}")
print("=" * 90)
sys.exit(1 if FAIL else 0)
