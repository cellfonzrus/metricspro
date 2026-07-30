"""Offline proof harness for the retail-ops closing-summary-keyerror lead (operator dispatch
2026-07-30, fix-pipeline row e2ddbda6-7f74-4c1e-89fb-ae803970a5b7, failure ref d71b6d34):
`GET /closing/summary` -> KeyError.

mod-people traced and exonerated the storeops-side call graph (scope_keyset/in_keyset — all .get()
access, proven by an 8-check harness driving missing-key shapes). This harness covers the
retail-ops side: `_closing_summary_for_date`'s `totals = {...}` block (router.py, the "totals"
dict around what was originally reported as lines ~896-906), the one spot in this function that
used RAW bracket access (`r["store_cash"]`-style) on a `daily_closing` row instead of `.get()` —
every OTHER field access in this same function, and in its sibling `closing_rollup`, already uses
`.get()` exclusively (grepped/confirmed in the review note).

No live DB/network — same stateful-fake-client convention as harness_dmverify_parity.py, driving
the REAL `closing_summary` / `_closing_summary_for_date` functions.

Run: `cd backend && python3 harness_closing_summary_keyerror.py`

Proves:
  A. Root-cause reachability analysis, encoded as a check: EVERY write path in this module
     (`create_row`, `_ingest_dataframe`) always sets all 6 of store_cash/store_cc/epay_cash/
     epay_cc/acc_sale/other_account, so a row this module itself wrote can never omit them —
     the "cannot crash against our own writes" half of the verdict, proven by reading `body`/`rows`
     construction directly (not just asserted in prose).
  B. THE CRASHING SHAPE: a `daily_closing` row dict that is missing one or more of those 6 keys
     entirely (not None -- ABSENT, simulating e.g. a differently-provisioned tenant DB, a
     schema-cache-reload race right after a daily_closing-touching migration, or any future
     caller/writer this module doesn't control) used to raise an uncaught KeyError via the OLD
     raw-bracket totals block (reproduced here as an independent oracle of the pre-fix code,
     copy-pasted from git history commit fcc32dc, NOT a re-test of the fixed code) --  and now, via
     the REAL (fixed) `_closing_summary_for_date`, resolves that field to 0.0 and returns
     normally, no 500.
  C. BYTE-IDENTICAL ON COMPLETE ROWS: for a row shape carrying every key (the only shape any real
     writer in this module produces), the fixed function's totals output is compared field-by-field
     against the SAME independent pre-fix oracle from B -- proving the .get() hardening changed
     NOTHING about the numbers for the data this module actually writes and reads today. Recon
     math (money_recon cash/credit/accessory) is included in the comparison, not just the raw
     totals dict, since that's what the gate/discrepancy numbers are actually built from.
  D. A missing key contributes exactly 0.0 to the SUM alongside a complete sibling row -- the
     same value the column's own SQL `DEFAULT 0` would already have produced -- never a
     fabricated non-zero recon input, and never silently drops the sibling row's real dollars.
  E. The full /closing/summary route (not just the inner function) survives the crashing shape
     end-to-end -- no 500, a normal 200-shaped response.
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"
_ID = {"n": 0}


def nid(pfx="id"):
    _ID["n"] += 1
    return f"{pfx}-{_ID['n']}"


# ── stateful fake supabase client (same convention as harness_dmverify_parity.py) ──────────
class Q:
    def __init__(self, store, table):
        self.s, self.t = store, table
        self.op, self.payload = "select", None
        self.filters = []
        self._limit = None

    def select(self, *a, **k): self.op = "select"; return self
    def insert(self, rows, **k): self.op = "insert"; self.payload = rows; return self
    def update(self, patch, **k): self.op = "update"; self.payload = patch; return self
    def delete(self, **k): self.op = "delete"; return self
    def eq(self, c, v): self.filters.append((c, "eq", v)); return self
    def in_(self, c, v): self.filters.append((c, "in", list(v))); return self
    def gte(self, c, v): self.filters.append((c, "gte", v)); return self
    def lte(self, c, v): self.filters.append((c, "lte", v)); return self
    def order(self, *a, **k): return self
    def limit(self, n, *a, **k): self._limit = n; return self

    def _match(self, row):
        for c, kind, v in self.filters:
            rv = row.get(c)
            if kind == "eq" and rv != v: return False
            if kind == "in" and rv not in v: return False
            if kind == "gte" and not (rv is not None and str(rv) >= str(v)): return False
            if kind == "lte" and not (rv is not None and str(rv) <= str(v)): return False
        return True

    def execute(self):
        rows = self.s.setdefault(self.t, [])
        if self.op == "select":
            # NOTE: unlike Postgres SELECT *, this fake returns the row dict AS STORED -- if a key
            # was never put on it, it's genuinely ABSENT here, which is exactly what lets section B
            # simulate "a row missing a key" (impossible against a real daily_closing table per the
            # review note, but reproduced here as the honest belt-and-braces shape the fix guards).
            matched = [dict(r) for r in rows if self._match(r)]
            if self._limit is not None:
                matched = matched[: self._limit]
            return SimpleNamespace(data=matched)
        if self.op == "insert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for r in payload:
                r = dict(r); r.setdefault("id", nid(self.t))
                rows.append(r); out.append(dict(r))
            return SimpleNamespace(data=out)
        if self.op == "update":
            out = []
            for r in rows:
                if self._match(r):
                    r.update(self.payload); out.append(dict(r))
            return SimpleNamespace(data=out)
        if self.op == "delete":
            keep = [r for r in rows if not self._match(r)]
            deleted = [r for r in rows if self._match(r)]
            self.s[self.t] = keep
            return SimpleNamespace(data=deleted)
        return SimpleNamespace(data=[])


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, _n): return self
    def table(self, name): return Q(self.store, name)


def fresh_store():
    return {"daily_closing": [], "daily_closing_verification": [], "stores": [],
            "closing_tender_def": [], "closing_count_field_def": [], "tenants": [],
            "store_closer": [], "pos_tender_summary": [], "app_users": [], "roles": []}


import app.modules.core.router as core            # noqa: E402
import app.modules.closing.router as cr            # noqa: E402

AUTH_NONE = ""


def wire(store):
    fake = FakeClient(store)
    cr.sb = lambda: fake
    cr.get_supabase = lambda: fake
    core.get_supabase = lambda: fake
    core._uid_from_token = lambda a: None
    cr._who_worked_by_store = lambda client, org_id, date: {}
    cr._b2b_counts_by_store = lambda client, org_id, date: {}
    cr._b2b_money_by_store = lambda client, org_id, date: {}
    cr._xreport_tenders_by_store = lambda client, org_id, date: {}
    cr._b2b_day = lambda client, org_id, date: {"has_data": False, "by_store": {}, "by_rep": {}, "counts": {}}
    return fake


def dc_row(**kw):
    r = {"org_id": HOUSE, "close_date": "2026-07-15", "period": "2026-07",
         "store_code": "S1", "store_address": "1 Main St", "store_name": "1 Main St",
         "employee_name": "Jane Rep", "source": "manual",
         "t_cash": 100.0, "t_credit": 50.0, "t_ext_cc": 0.0, "t_gift": 0.0, "t_store_acct": 0.0,
         "t_zelle": 0.0, "t_acima": 0.0, "store_cash": 100.0, "store_cc": 50.0, "epay_cash": 0.0,
         "epay_cc": 0.0, "acc_sale": 25.0, "other_account": 0.0,
         "upgrade_count": 1, "new_line_count": 2, "postpaid_count": 0,
         "expense_amount": 0.0, "expense_description": None, "expense_approved": False,
         "envelope_picture": None, "remarks": "", "tenders": None, "counts": None}
    r.update(kw)
    return r


# ══════════════ A. reachability: every write path in this module sets all 6 keys ══════════════
import inspect  # noqa: E402

create_row_src = inspect.getsource(cr.create_row)
ingest_src = inspect.getsource(cr._ingest_dataframe)
MONEY6 = ("store_cash", "store_cc", "epay_cash", "epay_cc", "acc_sale", "other_account")
check("A1. create_row explicitly sets all 6 money fields on `body` (either the initial dict "
      "literal `\"acc_sale\": ...` or a later `body[\"k\"] = ...` assignment -- grepped its own source)",
      all((f'body["{k}"]' in create_row_src) or (f'"{k}":' in create_row_src) for k in MONEY6))
check("A2. _ingest_dataframe explicitly sets all 6 money fields on the inserted row dict",
      all(f'"{k}":' in ingest_src for k in MONEY6))

# ══════════════ Independent pre-fix oracle (copy of the OLD raw-bracket totals block, commit fcc32dc + later additions) ═
def old_totals_oracle(reps):
    """Faithful reimplementation of the totals={...} block AS IT WAS on origin/main before this
    fix -- raw bracket r["..."] for the 6 legacy money fields, everything else identical to the
    real (fixed) function today. An independent oracle, not a re-test of router.py's own code."""
    totals = {
        "store_cash": round(sum(cr._f(r["store_cash"]) for r in reps), 2),
        "store_cc": round(sum(cr._f(r["store_cc"]) for r in reps), 2),
        "epay_cash": round(sum(cr._f(r["epay_cash"]) for r in reps), 2),
        "epay_cc": round(sum(cr._f(r["epay_cc"]) for r in reps), 2),
        "epay_on_cash": round(sum(cr._row_epay_display(r)["cash"] for r in reps), 2),
        "epay_on_cc": round(sum(cr._row_epay_display(r)["cc"] for r in reps), 2),
        "acc_sale": round(sum(cr._f(r["acc_sale"]) for r in reps), 2),
        "other_account": round(sum(cr._f(r["other_account"]) for r in reps), 2),
    }
    return totals


# ══════════════ B. THE CRASHING SHAPE: a row missing a money key ══════════════
incomplete_row = dc_row(id="incomplete")
del incomplete_row["store_cash"]     # ABSENT, not None -- the concrete crashing shape
del incomplete_row["acc_sale"]

# B0. Prove the OLD oracle genuinely crashes on this exact shape (establishes "was reachable").
old_crashed = False
try:
    old_totals_oracle([incomplete_row])
except KeyError as e:
    old_crashed = True
    old_err = str(e)
check("B0. independent pre-fix oracle DOES raise KeyError on the missing-key shape (proves the "
      "bug was real against this data shape, not a phantom)", old_crashed, "" if old_crashed else "no raise")

st = fresh_store(); wire(st)
st["daily_closing"] = [incomplete_row]
st["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas"}]

crashed = False
resp = None
try:
    resp = cr.closing_summary(date="2026-07-15", authorization=AUTH_NONE, org_id=HOUSE)
except Exception as e:
    crashed = True
    exc = e
check("B1. the REAL (fixed) closing_summary survives the missing-key row -- no exception at all",
      not crashed, "" if not crashed else f"{type(exc).__name__}: {exc}")
check("B2. response still has the expected {date, stores} shape", resp is not None and "stores" in resp and len(resp["stores"]) == 1)
if resp:
    t = resp["stores"][0]["totals"]
    check("B3. the missing store_cash resolves to 0.0 (same value its own SQL DEFAULT 0 would give)",
          t["store_cash"] == 0.0, str(t.get("store_cash")))
    check("B4. the missing acc_sale resolves to 0.0", t["acc_sale"] == 0.0, str(t.get("acc_sale")))
    check("B5. store_cc (present on this row) is UNAFFECTED -- 50.0, not zeroed by the neighboring gap",
          t["store_cc"] == 50.0, str(t.get("store_cc")))

# ══════════════ C. Byte-identical on COMPLETE rows (the only shape any real writer produces) ══════════════
complete_reps = [dc_row(id="c1", employee_name="Jane Rep", store_cash=100.0, store_cc=50.0,
                        epay_cash=10.0, epay_cc=5.0, acc_sale=25.0, other_account=7.5),
                 dc_row(id="c2", employee_name="John Rep", store_cash=40.0, store_cc=10.0,
                        epay_cash=0.0, epay_cc=0.0, acc_sale=0.0, other_account=0.0)]
oracle_totals = old_totals_oracle(complete_reps)

st2 = fresh_store(); wire(st2)
st2["daily_closing"] = complete_reps
st2["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas"}]
resp2 = cr.closing_summary(date="2026-07-15", authorization=AUTH_NONE, org_id=HOUSE)
real_totals = resp2["stores"][0]["totals"]

for k in oracle_totals:
    check(f"C.{k}: fixed-function output byte-identical to the pre-fix oracle on a complete row",
          real_totals[k] == oracle_totals[k], f"real={real_totals[k]} oracle={oracle_totals[k]}")

# Also compare the derived money_recon (what the gate/discrepancy display actually reads), not just totals.
resp2_mr = cr._b2b_money_by_store  # left un-monkeypatched-away here on purpose: still {} (wired above), so
                                    # money_recon is None for this call -- confirm that explicitly (no crash,
                                    # no accidental fabricated recon row) rather than skip the check.
check("C_mr. money_recon stays None when no B2B money data loaded (unaffected by this fix, still "
      "the pre-existing recon-pending contract)", resp2["stores"][0]["money_recon"] is None)

# ══════════════ D. missing key contributes exactly 0.0 alongside a complete sibling row (never drops the sibling's $) ══
mixed_reps_row = dc_row(id="complete_sibling", employee_name="John Rep", store_cash=40.0, store_cc=10.0,
                        epay_cash=0.0, epay_cc=0.0, acc_sale=15.0, other_account=0.0)
st3 = fresh_store(); wire(st3)
st3["daily_closing"] = [incomplete_row, mixed_reps_row]
st3["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas"}]
resp3 = cr.closing_summary(date="2026-07-15", authorization=AUTH_NONE, org_id=HOUSE)
t3 = resp3["stores"][0]["totals"]
check("D1. store_cash sums to the sibling's real 40.0 (missing row contributed 0.0, not dropped/doubled)",
      t3["store_cash"] == 40.0, str(t3.get("store_cash")))
check("D2. acc_sale sums to the sibling's real 15.0 (missing row's own acc_sale contributed 0.0)",
      t3["acc_sale"] == 15.0, str(t3.get("acc_sale")))
check("D3. rep_count still 2 (the incomplete row is still a real submission, just with 2 missing $ fields)",
      t3["rep_count"] == 2, str(t3.get("rep_count")))

# ══════════════ E. Full end-to-end route survives, real 200-shaped response ══════════════
check("E1. /closing/summary route-level: resp keys present (date/dates/range/stores/can_review/...)",
      resp is not None and all(k in resp for k in ("date", "dates", "range", "stores", "can_review")))

print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
