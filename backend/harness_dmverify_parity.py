"""Offline proof harness for retail-ops-14 (OWNER DIRECTIVE 2026-07-28 x2): DM Verify data-parity +
RULE FIVE filters + RULE FOUR exports, plus the same-day follow-up extending the Daily Closing
DASHBOARD's By-store/By-rep tabs with the identical filter set. No live DB/network — same convention
as harness_closing_submissions.py: runs the REAL `closing_summary` / `closing_rollup` /
`get_missed_dm_verifies` functions against a stateful fake Supabase client, monkeypatching only the
heavy B2B/who-worked data-source functions (already independently proven elsewhere) so this harness
stays focused on THIS package's actual change surface.

Run: `cd backend && python3 harness_dmverify_parity.py`

Proves:
  A. Root cause 1a — bucket-aware market filtering. An unresolved/blank-market store is NEVER
     silently dropped by a market filter (neither in /closing/summary NOR /closing/rollup); it can
     only be excluded by an EXPLICIT "(no market)" deselection. The legacy singular `market=` param
     still works exactly like before.
  B. /closing/summary date-range mode: date_from/date_to loops the SAME per-day computation once per
     calendar date (one store-card per (store, date)), sorted close_date-desc/store-address-asc;
     single-`date=` mode is BYTE-IDENTICAL in shape to the historical response (still `{date, stores}`
     with `stores` unchanged in count/order for a single day). Range is bounded + capped-most-recent.
  C. Store(s)/rep(s) filters on /closing/summary: store filter never drops an unresolved-store row;
     rep filter narrows which STORE CARDS show (submitted/worked/missing) WITHOUT changing a store's
     totals (never re-aggregates over a subset of reps — money math untouched).
  D. Additive per-tender detail on /closing/summary (ACIMA + the individual buckets + custom tenders,
     previously entirely invisible on this page) — reuses `_row_display_tenders` verbatim (both the
     modern t_* path and the legacy pre-mig103 fallback), and the pre-existing legacy totals fields
     stay byte-identical.
  E. Re-derived close-gate status per rep + a store-level rollup — reuses `_money_issues`/`_rep_b2b`
     verbatim (monkeypatched `_b2b_day`, so this tests the STATUS DERIVATION, same convention as
     harness_closing_submissions.py's section G) — and the SAME money-secrecy boundary
     (`_can_mgmt_review`) as /closing/submissions: reasons/b2b amounts populate ONLY for a
     company-wide caller.
  F. /closing/rollup gains date_from/date_to (period= still works, unchanged), stores=/reps=, and the
     same bucket-aware market fix; verified_keys/submitted_keys stay correct in both modes.
  G. /closing/ops-chargebacks/dm-verify gains date_from/date_to/stores/reps/markets post-filtering
     over whatever `detect_missed_dm_verifies` returns (mocked — that function has its own harness).
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000099"
_ID = {"n": 0}


def nid(pfx="id"):
    _ID["n"] += 1
    return f"{pfx}-{_ID['n']}"


# ── stateful fake supabase client (copied convention from harness_closing_submissions.py) ──────────
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
import app.modules.closing.ops_chargebacks as oc   # noqa: E402

AUTH_NONE = ""
AUTH_GOOD = "Bearer good-token"


def wire(store):
    fake = FakeClient(store)
    cr.sb = lambda: fake
    cr.get_supabase = lambda: fake
    core.get_supabase = lambda: fake
    core._uid_from_token = lambda a: None
    # Neutralize the heavy B2B/who-worked helpers by default — each section overrides as needed.
    cr._who_worked_by_store = lambda client, org_id, date: {}
    cr._b2b_counts_by_store = lambda client, org_id, date: {}
    cr._b2b_money_by_store = lambda client, org_id, date: {}
    cr._xreport_tenders_by_store = lambda client, org_id, date: {}
    cr._b2b_day = lambda client, org_id, date: {"has_data": False, "by_store": {}, "by_rep": {}, "counts": {}}
    return fake


def role_row(org, name, perms):
    return {"id": nid("role"), "org_id": org, "name": name, "permissions": perms}


def as_dm(store):
    store["app_users"] = [{"id": nid(), "auth_id": "uid-1", "org_id": HOUSE, "role": "market_manager", "super_admin": False}]
    store["roles"] = [role_row(HOUSE, "market_manager", {"scope": "market"})]
    core._uid_from_token = lambda a: ("uid-1" if a == AUTH_GOOD else None)


def as_company_wide(store):
    store["app_users"] = [{"id": nid(), "auth_id": "uid-1", "org_id": HOUSE, "role": "admin", "super_admin": False}]
    store["roles"] = [role_row(HOUSE, "admin", {"scope": "all"})]
    core._uid_from_token = lambda a: ("uid-1" if a == AUTH_GOOD else None)


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


# ═══════════════════════════ A. Bucket-aware market filtering (root cause 1a) ═══════════════════════
st = fresh_store(); wire(st)
st["daily_closing"] = [dc_row(id="r1", store_code="S1", close_date="2026-07-15"),
                       dc_row(id="r2", store_code=None, store_name="Unmapped SFID Store",
                             close_date="2026-07-15", employee_name="John Rep")]
st["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas"}]

resp = cr.closing_summary(date="2026-07-15", authorization=AUTH_NONE, org_id=HOUSE)
check("A1. no market filter -> BOTH stores present (unresolved one bucketed, not dropped)",
      len(resp["stores"]) == 2, str([s["store_code"] for s in resp["stores"]]))
unresolved = [s for s in resp["stores"] if s["store_code"] is None][0]
check("A2. unresolved store's market is the explicit '(no market)' bucket", unresolved["market"] == "(no market)")

resp_tx = cr.closing_summary(date="2026-07-15", markets="Texas", authorization=AUTH_NONE, org_id=HOUSE)
check("A3. markets=Texas -> only the resolved Texas store, unresolved DROPPED (explicit exclude, not silent)",
      [s["store_code"] for s in resp_tx["stores"]] == ["S1"], str([s["store_code"] for s in resp_tx["stores"]]))

resp_nomkt = cr.closing_summary(date="2026-07-15", markets="(no market)", authorization=AUTH_NONE, org_id=HOUSE)
check("A4. markets=(no market) -> ONLY the unresolved store (explicit inclusion works both ways)",
      resp_nomkt["stores"] and resp_nomkt["stores"][0]["store_code"] is None)

resp_legacy = cr.closing_summary(date="2026-07-15", market="Texas", authorization=AUTH_NONE, org_id=HOUSE)
check("A5. legacy singular market= param still works",
      [s["store_code"] for s in resp_legacy["stores"]] == ["S1"])

# Same fix applied to /closing/rollup.
st2 = fresh_store(); wire(st2)
st2["daily_closing"] = [dc_row(id="p1", store_code="S1", period="2026-07"),
                        dc_row(id="p2", store_code=None, store_name="Unmapped SFID Store",
                              period="2026-07", employee_name="John Rep")]
st2["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas"}]
roll = cr.closing_rollup(period="2026-07", authorization=AUTH_NONE, org_id=HOUSE)
check("A6. rollup, no filter -> both stores present", len(roll["by_store"]) == 2)
roll_tx = cr.closing_rollup(period="2026-07", markets="Texas", authorization=AUTH_NONE, org_id=HOUSE)
check("A7. rollup markets=Texas -> unresolved store excluded EXPLICITLY, resolved one kept",
      len(roll_tx["by_store"]) == 1 and roll_tx["by_store"][0]["store_code"] == "S1")
roll_nomkt = cr.closing_rollup(period="2026-07", markets="(no market)", authorization=AUTH_NONE, org_id=HOUSE)
check("A8. rollup markets=(no market) -> only the unresolved store",
      len(roll_nomkt["by_store"]) == 1 and roll_nomkt["by_store"][0]["store_code"] is None)

st = fresh_store(); wire(st)
today = cr._biz_today_iso()
st["daily_closing"] = [dc_row(id="today_row", close_date=today)]
resp_noargs = cr.closing_summary(authorization=AUTH_NONE, org_id=HOUSE)
check("A9. no date/date_from/date_to at all -> degrades to TODAY (never a 400, matches the standard "
      "'Clear filters' -> sane-default doctrine)",
      resp_noargs["date"] == today and len(resp_noargs["stores"]) == 1, str(resp_noargs.get("date")))

# ═══════════════════════════════════ B. Date-range mode ═════════════════════════════════════════════
st = fresh_store(); wire(st)
st["daily_closing"] = [dc_row(id="d1", close_date="2026-07-01"),
                       dc_row(id="d2", close_date="2026-07-02"),
                       dc_row(id="d3", close_date="2026-07-03")]
resp_single = cr.closing_summary(date="2026-07-02", authorization=AUTH_NONE, org_id=HOUSE)
check("B1. single-date call: response shape byte-compatible ({date, stores}), 1 store card",
      resp_single["date"] == "2026-07-02" and len(resp_single["stores"]) == 1
      and resp_single["range"] is False and resp_single["stores"][0]["close_date"] == "2026-07-02")

resp_range = cr.closing_summary(date_from="2026-07-01", date_to="2026-07-03", authorization=AUTH_NONE, org_id=HOUSE)
check("B2. range mode: one store-card PER DATE (3 dates x 1 store = 3 cards)",
      resp_range["range"] is True and len(resp_range["stores"]) == 3, str(len(resp_range["stores"])))
check("B3. range mode: dates_computed/dates_requested correct, not capped",
      resp_range["dates_computed"] == 3 and resp_range["dates_requested"] == 3 and resp_range["range_capped"] is False)
dates_seen = [s["close_date"] for s in resp_range["stores"]]
check("B4. sorted close_date DESCENDING", dates_seen == sorted(dates_seen, reverse=True), str(dates_seen))

_orig_cap = cr._SUMMARY_MAX_RANGE_DATES
cr._SUMMARY_MAX_RANGE_DATES = 2
try:
    resp_capped = cr.closing_summary(date_from="2026-07-01", date_to="2026-07-05", authorization=AUTH_NONE, org_id=HOUSE)
finally:
    cr._SUMMARY_MAX_RANGE_DATES = _orig_cap
check("B5. wide range capped to the MOST RECENT N dates",
      resp_capped["range_capped"] is True and resp_capped["dates_computed"] == 2
      and resp_capped["dates"] == ["2026-07-04", "2026-07-05"], str(resp_capped["dates"]))

# ═══════════════════════════════════ C. Store(s)/rep(s) filters ═════════════════════════════════════
st = fresh_store(); wire(st)
st["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas"},
                {"org_id": HOUSE, "store_code": "S2", "address": "2 Oak Ave", "market": "Ohio"}]
st["daily_closing"] = [
    dc_row(id="jane", store_code="S1", employee_name="Jane Rep", t_cash=100.0, t_credit=50.0),
    dc_row(id="john", store_code="S1", employee_name="John Rep", t_cash=40.0, t_credit=10.0),
    dc_row(id="s2row", store_code="S2", employee_name="Mo Rep", t_cash=1.0, t_credit=1.0),
    dc_row(id="unresolved", store_code="S_X", employee_name="Unmapped Rep", t_cash=1.0, t_credit=1.0),
]
resp = cr.closing_summary(date="2026-07-15", stores="S1", authorization=AUTH_NONE, org_id=HOUSE)
codes = sorted({s["store_code"] for s in resp["stores"]}, key=lambda x: (x is None, x))
check("C1. stores=S1 -> S1's card + the UNRESOLVED store (S_X, no roster match) — S2 (a real, "
      "just-not-picked store) IS dropped, an unresolved one never is",
      codes == ["S1", "S_X"], str(codes))

s1_card = [s for s in resp["stores"] if s["store_code"] == "S1"][0]
check("C2. store filter does NOT re-aggregate — S1's total still sums BOTH Jane and John",
      s1_card["totals"]["t_cash"] == 140.0, str(s1_card["totals"]["t_cash"]))

resp_rep = cr.closing_summary(date="2026-07-15", reps="Jane Rep", authorization=AUTH_NONE, org_id=HOUSE)
check("C3. reps=Jane Rep -> S1's card still shows (Jane submitted there); other stores dropped",
      [s["store_code"] for s in resp_rep["stores"]] == ["S1"], str([s["store_code"] for s in resp_rep["stores"]]))
s1_rep_card = resp_rep["stores"][0]
check("C4. rep filter does NOT narrow the totals — still both Jane+John's $140 cash",
      s1_rep_card["totals"]["t_cash"] == 140.0, str(s1_rep_card["totals"]["t_cash"]))

resp_norep = cr.closing_summary(date="2026-07-15", reps="Nobody Here", authorization=AUTH_NONE, org_id=HOUSE)
check("C5. reps= a name matching nobody -> zero cards", len(resp_norep["stores"]) == 0)

# missing-rep interaction: a store that WORKED but never submitted, filtered by the missing rep's name.
st2 = fresh_store(); wire(st2)
st2["daily_closing"] = []
cr._who_worked_by_store = lambda client, org_id, date: {"S3": {"clocked_in": {"Ghost Rep"}, "sold": set(), "logins": {}}}
resp_missing = cr.closing_summary(date="2026-07-15", reps="Ghost Rep", authorization=AUTH_NONE, org_id=HOUSE)
check("C6. reps=<a worked-but-never-submitted rep> -> the no_closing_submitted card still surfaces",
      len(resp_missing["stores"]) == 1 and resp_missing["stores"][0].get("no_closing_submitted") is True)
cr._who_worked_by_store = lambda client, org_id, date: {}

# ═══════════════════════════ D. Additive per-tender detail (ACIMA etc.) ═════════════════════════════
st = fresh_store(); wire(st)
st["closing_tender_def"] = [{"org_id": HOUSE, "tender_key": "venmo", "label": "Venmo", "is_active": True, "sort_order": 1}]
modern = dc_row(id="modern", t_cash=100.0, t_credit=50.0, t_gift=10.0, t_store_acct=5.0, t_zelle=7.0,
               t_acima=200.0, tenders={"venmo": 33.0})
st["daily_closing"] = [modern]
resp = cr.closing_summary(date="2026-07-15", authorization=AUTH_NONE, org_id=HOUSE)
t = resp["stores"][0]["totals"]
check("D1. ACIMA now visible in totals (previously entirely absent from this page)", t["t_acima"] == 200.0, str(t["t_acima"]))
check("D2. individual tender buckets present", t["t_gift"] == 10.0 and t["t_store_acct"] == 5.0 and t["t_zelle"] == 7.0)
check("D3. custom tender (Venmo) surfaced with its configured label",
      any(c["label"] == "Venmo" and c["value"] == 33.0 for c in t["custom_tenders"]), str(t["custom_tenders"]))
check("D4. total_collected sums the 7 standard tender buckets (custom tenders reported separately, "
      "matching /closing/submissions' identical convention — never double-counted into one figure)",
      t["total_collected"] == round(100 + 50 + 10 + 5 + 7 + 200, 2), str(t["total_collected"]))
check("D5. legacy totals fields UNCHANGED (byte-identical to before this package)",
      t["store_cash"] == 100.0 and t["store_cc"] == 50.0 and t["acc_sale"] == 25.0)
rep_out = resp["stores"][0]["reps"][0]
check("D6. per-rep row carries _tenders (acima visible at rep grain too)", rep_out["_tenders"]["acima"] == 200.0)
check("D7. per-rep custom tender display string populated", "Venmo" in rep_out["_custom_tenders_display"])

# Legacy pre-mig103 row (no t_* at all) -> same fallback closing_submissions already uses.
st2 = fresh_store(); wire(st2)
legacy = dc_row(id="legacy", t_cash=None, t_credit=None, t_ext_cc=None, t_gift=None, t_store_acct=None,
               t_zelle=None, t_acima=None, store_cash=80.0, store_cc=20.0, epay_cash=5.0, epay_cc=2.0,
               other_account=11.0)
st2["daily_closing"] = [legacy]
resp2 = cr.closing_summary(date="2026-07-15", authorization=AUTH_NONE, org_id=HOUSE)
t2 = resp2["stores"][0]["totals"]
check("D8. legacy row: t_cash falls back to store_cash+epay_cash (same as create_row's own fallback)",
      t2["t_cash"] == 85.0, str(t2["t_cash"]))
check("D9. legacy row: t_zelle falls back to other_account", t2["t_zelle"] == 11.0, str(t2["t_zelle"]))

# ═══════════════════════════ E. Gate-status re-derivation + secrecy boundary ═════════════════════════
def fake_b2b_day(client, org_id, date):
    return {"has_data": True, "by_store": {},
            "by_rep": {("S1", "jane rep"): {"cash": 100.0, "card": 50.0, "acc_gross": 0, "total": 150.0,
                                            "salesperson": "Jane Rep", "tenders_available": True},
                      ("S1", "john rep"): {"cash": 40.0, "card": 10.0, "acc_gross": 0, "total": 50.0,
                                           "salesperson": "John Rep", "tenders_available": True}},
            "counts": {}}


st = fresh_store(); wire(st)
cr._b2b_day = fake_b2b_day
st["daily_closing"] = [
    dc_row(id="jane_ok", store_code="S1", employee_name="Jane Rep", t_cash=100.0, t_credit=50.0),
    dc_row(id="john_short", store_code="S1", employee_name="John Rep", t_cash=10.0, t_credit=10.0),  # cash short vs b2b 40 -> block
]
resp = cr.closing_summary(date="2026-07-15", authorization=AUTH_NONE, org_id=HOUSE)
card = resp["stores"][0]
by_id = {r["id"]: r for r in card["reps"]}
check("E1. matching rep -> per-rep gate ok", by_id["jane_ok"]["_gate"]["status"] == "ok", by_id["jane_ok"]["_gate"]["status"])
check("E2. cash-short rep -> per-rep gate blocked", by_id["john_short"]["_gate"]["status"] == "blocked", by_id["john_short"]["_gate"]["status"])
check("E3. store-level gate_status = worst-of across reps (blocked wins over ok)", card["gate_status"] == "blocked", card["gate_status"])
check("E4. unauthenticated caller: no reasons/b2b amounts even though status is visible",
      by_id["john_short"]["_gate"]["reasons"] == [] and by_id["john_short"]["_gate"]["b2b_cash"] is None)

as_dm(st)
resp_dm = cr.closing_summary(date="2026-07-15", authorization=AUTH_GOOD, org_id=HOUSE)
dm_row = {r["id"]: r for r in resp_dm["stores"][0]["reps"]}["john_short"]
check("E5. DM (market-scope) caller ALSO gets no reasons/b2b amounts", dm_row["_gate"]["reasons"] == [] and resp_dm["can_review"] is False)

as_company_wide(st)
resp_admin = cr.closing_summary(date="2026-07-15", authorization=AUTH_GOOD, org_id=HOUSE)
admin_row = {r["id"]: r for r in resp_admin["stores"][0]["reps"]}["john_short"]
check("E6. company-wide caller sees reasons + b2b_cash/card",
      len(admin_row["_gate"]["reasons"]) > 0 and admin_row["_gate"]["b2b_cash"] == 40.0 and resp_admin["can_review"] is True,
      str(admin_row["_gate"]))

# No B2B data for the day -> recon_pending, never a guess.
cr._b2b_day = lambda client, org_id, date: {"has_data": False, "by_store": {}, "by_rep": {}, "counts": {}}
st2 = fresh_store(); wire(st2)
st2["daily_closing"] = [dc_row(id="pending", store_code="S1", employee_name="Jane Rep")]
resp_pending = cr.closing_summary(date="2026-07-15", authorization=AUTH_NONE, org_id=HOUSE)
check("E7. no B2B data -> gate recon_pending, not a false ok/block",
      resp_pending["stores"][0]["reps"][0]["_gate"]["status"] == "recon_pending")

# ═══════════════════════════════════ F. /closing/rollup extension ═══════════════════════════════════
st = fresh_store(); wire(st)
st["daily_closing"] = [
    dc_row(id="r1", store_code="S1", close_date="2026-07-01", period="2026-07", employee_name="Jane Rep"),
    dc_row(id="r2", store_code="S1", close_date="2026-07-15", period="2026-07", employee_name="John Rep"),
    dc_row(id="r3", store_code="S2", close_date="2026-07-15", period="2026-07", employee_name="Mo Rep"),
]
st["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas"},
               {"org_id": HOUSE, "store_code": "S2", "address": "2 Oak Ave", "market": "Ohio"}]
st["daily_closing_verification"] = [{"org_id": HOUSE, "store_code": "S1", "close_date": "2026-07-15", "verified": True}]

roll_period = cr.closing_rollup(period="2026-07", authorization=AUTH_NONE, org_id=HOUSE)
check("F1. period mode unchanged: 2 stores, 3 rows total",
      len(roll_period["by_store"]) == 2 and roll_period["totals"]["rows"] == 3)

roll_range = cr.closing_rollup(date_from="2026-07-10", date_to="2026-07-20", authorization=AUTH_NONE, org_id=HOUSE)
check("F2. date_from/date_to mode: only the 2 rows in [07-10,07-20]",
      roll_range["totals"]["rows"] == 2, str(roll_range["totals"]["rows"]))

roll_stores = cr.closing_rollup(period="2026-07", stores="S1", authorization=AUTH_NONE, org_id=HOUSE)
check("F3. stores=S1 -> only S1 rows aggregated", len(roll_stores["by_store"]) == 1 and roll_stores["by_store"][0]["store_code"] == "S1")

roll_reps = cr.closing_rollup(period="2026-07", reps="Jane Rep", authorization=AUTH_NONE, org_id=HOUSE)
check("F4. reps=Jane Rep -> only Jane's row aggregated", roll_reps["totals"]["rows"] == 1)

check("F5. verified_keys/submitted_keys correct in period mode",
      roll_period["submitted_keys"] == 3 and roll_period["verified_keys"] == 1,
      f"{roll_period['submitted_keys']}/{roll_period['verified_keys']}")
check("F6. verified_keys/submitted_keys correct in range mode (only 1 of 2 rows in range verified)",
      roll_range["submitted_keys"] == 2 and roll_range["verified_keys"] == 1)

# ═══════════════════════════════ G. Chargebacks endpoint filters ════════════════════════════════════
_orig_detect = oc.detect_missed_dm_verifies
CB_ROWS = [
    {"id": "cb1", "org_id": HOUSE, "store_code": "S1", "incident_date": "2026-07-10", "employee_name": "DM One",
     "status": "pending", "amount": 25.0, "parent_id": None},
    {"id": "cb2", "org_id": HOUSE, "store_code": "S2", "incident_date": "2026-07-20", "employee_name": "DM Two",
     "status": "pending", "amount": 25.0, "parent_id": None},
]
oc.detect_missed_dm_verifies = lambda org_id, lookback_days=14: list(CB_ROWS)
st = fresh_store(); wire(st)
st["stores"] = [{"org_id": HOUSE, "store_code": "S1", "market": "Texas"},
               {"org_id": HOUSE, "store_code": "S2", "market": "Ohio"}]
try:
    resp = cr.get_missed_dm_verifies(authorization=AUTH_NONE, org_id=HOUSE)
    check("G1. no filter -> both chargebacks", len(resp["rows"]) == 2)

    resp_d = cr.get_missed_dm_verifies(date_from="2026-07-15", authorization=AUTH_NONE, org_id=HOUSE)
    check("G2. date_from narrows to the later incident only", [r["id"] for r in resp_d["rows"]] == ["cb2"])

    resp_s = cr.get_missed_dm_verifies(stores="S1", authorization=AUTH_NONE, org_id=HOUSE)
    check("G3. stores=S1 narrows to cb1 only", [r["id"] for r in resp_s["rows"]] == ["cb1"])

    resp_r = cr.get_missed_dm_verifies(reps="DM Two", authorization=AUTH_NONE, org_id=HOUSE)
    check("G4. reps=DM Two narrows to cb2 only", [r["id"] for r in resp_r["rows"]] == ["cb2"])

    resp_m = cr.get_missed_dm_verifies(markets="Ohio", authorization=AUTH_NONE, org_id=HOUSE)
    check("G5. markets=Ohio (resolved via storeops.stores) narrows to cb2 only", [r["id"] for r in resp_m["rows"]] == ["cb2"])
finally:
    oc.detect_missed_dm_verifies = _orig_detect

# ═══════════════════════════ H. Multi-tenant org isolation (Gate-1 NIT-2) ═══════════════════════════
# OTHER (org 0099) is defined at the top of this file but was never actually exercised end-to-end —
# add explicit isolation checks on all three endpoints this package extended.
st = fresh_store(); wire(st)
st["daily_closing"] = [
    dc_row(id="house_row", org_id=HOUSE, store_code="S1", close_date="2026-07-15"),
    dc_row(id="other_row", org_id=OTHER, store_code="S1", close_date="2026-07-15", employee_name="Intruder Rep"),
]
st["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas"},
               {"org_id": OTHER, "store_code": "S1", "address": "Other Tenant's S1", "market": "Nowhere"}]

resp_summary = cr.closing_summary(date="2026-07-15", authorization=AUTH_NONE, org_id=HOUSE)
all_rep_ids = [r["id"] for s in resp_summary["stores"] for r in (s.get("reps") or [])]
check("H1. /closing/summary — OTHER org's row never surfaces in a HOUSE call",
      "other_row" not in all_rep_ids and "house_row" in all_rep_ids, str(all_rep_ids))
resp_summary_other = cr.closing_summary(date="2026-07-15", authorization=AUTH_NONE, org_id=OTHER)
other_rep_ids = [r["id"] for s in resp_summary_other["stores"] for r in (s.get("reps") or [])]
check("H2. /closing/summary — OTHER org's own call sees only its own row",
      other_rep_ids == ["other_row"], str(other_rep_ids))

st2 = fresh_store(); wire(st2)
st2["daily_closing"] = [
    dc_row(id="house_p", org_id=HOUSE, store_code="S1", period="2026-07"),
    dc_row(id="other_p", org_id=OTHER, store_code="S1", period="2026-07", employee_name="Intruder Rep"),
]
roll_house = cr.closing_rollup(period="2026-07", authorization=AUTH_NONE, org_id=HOUSE)
check("H3. /closing/rollup — OTHER org's row never surfaces in a HOUSE call",
      roll_house["totals"]["rows"] == 1, str(roll_house["totals"]["rows"]))
roll_other = cr.closing_rollup(period="2026-07", authorization=AUTH_NONE, org_id=OTHER)
check("H4. /closing/rollup — OTHER org's own call sees only its own row",
      roll_other["totals"]["rows"] == 1)

CB_ROWS_ISO = [
    {"id": "cb_house", "org_id": HOUSE, "store_code": "S1", "incident_date": "2026-07-10",
     "employee_name": "DM House", "status": "pending", "amount": 25.0, "parent_id": None},
]
_orig_detect2 = oc.detect_missed_dm_verifies
# detect_missed_dm_verifies is itself org-scoped by contract (called with org_id) — a fake that only
# ever returns HOUSE-org rows for a HOUSE call and [] for anyone else proves the ENDPOINT (not just
# the detector) never asks for/returns another tenant's rows.
oc.detect_missed_dm_verifies = lambda org_id, lookback_days=14: (list(CB_ROWS_ISO) if org_id == HOUSE else [])
st3 = fresh_store(); wire(st3)
try:
    resp_cb_house = cr.get_missed_dm_verifies(authorization=AUTH_NONE, org_id=HOUSE)
    check("H5. /closing/ops-chargebacks/dm-verify — HOUSE call gets its own row",
          [r["id"] for r in resp_cb_house["rows"]] == ["cb_house"])
    resp_cb_other = cr.get_missed_dm_verifies(authorization=AUTH_NONE, org_id=OTHER)
    check("H6. /closing/ops-chargebacks/dm-verify — OTHER org call never sees HOUSE's row",
          resp_cb_other["rows"] == [], str(resp_cb_other["rows"]))
finally:
    oc.detect_missed_dm_verifies = _orig_detect2

# ═══════════════════════ I. Gate-1 rework regression checks (B1-adjacent NITs) ═══════════════════════
# NIT-3: closing_rollup range mode must 400 on a garbage date instead of an uncaught 500.
st = fresh_store(); wire(st)
try:
    cr.closing_rollup(date_from="not-a-date", date_to="also-not-a-date", authorization=AUTH_NONE, org_id=HOUSE)
    check("I1. rollup range mode rejects a garbage date with a clean HTTPException(400)", False, "did not raise")
except Exception as e:
    from fastapi import HTTPException as _HTTPException
    check("I1. rollup range mode rejects a garbage date with a clean HTTPException(400)",
          isinstance(e, _HTTPException) and e.status_code == 400, f"{type(e).__name__}: {e}")

# NIT-4a: chargebacks store filter never drops a row with no store_code.
CB_NO_CODE = [{"id": "cb_nocode", "org_id": HOUSE, "store_code": None, "incident_date": "2026-07-10",
              "employee_name": "DM Unresolved", "status": "pending", "amount": 25.0, "parent_id": None}]
_orig_detect3 = oc.detect_missed_dm_verifies
oc.detect_missed_dm_verifies = lambda org_id, lookback_days=14: list(CB_NO_CODE)
st = fresh_store(); wire(st)
try:
    resp = cr.get_missed_dm_verifies(stores="S1", authorization=AUTH_NONE, org_id=HOUSE)
    check("I2. chargebacks store filter never drops a row with no store_code",
          [r["id"] for r in resp["rows"]] == ["cb_nocode"], str(resp["rows"]))
finally:
    oc.detect_missed_dm_verifies = _orig_detect3

# NIT-4b: a failed storeops.stores fetch degrades to NOT applying the market filter (never silently
# empties the panel by defaulting every row into "(no market)").
CB_REAL_MARKET = [{"id": "cb_tx", "org_id": HOUSE, "store_code": "S1", "incident_date": "2026-07-10",
                   "employee_name": "DM One", "status": "pending", "amount": 25.0, "parent_id": None}]
_orig_detect4 = oc.detect_missed_dm_verifies
oc.detect_missed_dm_verifies = lambda org_id, lookback_days=14: list(CB_REAL_MARKET)
st = fresh_store(); wire(st)
fake_client = cr.sb()   # the just-wired FakeClient instance (cr.sb is a lambda closing over it)
_orig_table = fake_client.table
def _exploding_table(name):
    if name == "stores":
        raise RuntimeError("storeops.stores unreachable (simulated)")
    return _orig_table(name)
fake_client.table = _exploding_table
try:
    resp = cr.get_missed_dm_verifies(markets="Texas", authorization=AUTH_NONE, org_id=HOUSE)
    check("I3. a failed storeops.stores fetch degrades to NOT applying the market filter (row still shown)",
          [r["id"] for r in resp["rows"]] == ["cb_tx"], str(resp["rows"]))
finally:
    oc.detect_missed_dm_verifies = _orig_detect4
    fake_client.table = _orig_table

# ── Summary ──────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
