"""Offline proof harness for GET /closing/submissions (retail-ops-13, OWNER DIRECTIVE 2026-07-27:
"all columns submitted at time of closing" dashboard detail view + RULE FIVE filters + RULE FOUR
export). No live DB/network — same convention as harness_settings_audit.py / harness_tech_support.py:
runs the REAL `closing_submissions` function against a stateful fake Supabase client.

Run: `cd backend && python3 harness_closing_submissions.py`

Proves:
  A. Org isolation — a second org's daily_closing rows never leak into org A's response.
  B. Date-range semantics — inclusive gte/lte, default = current month when neither is given, a
     lone date_to backfills date_from to that month's 1st, swapped from>to is auto-corrected.
  C. Every submitted column is present on a row's payload (identity/tender/count/expense/status/meta).
  D. The legacy-fallback tender re-derivation (_row_display_tenders) for a pre-mig103 sheet_upload row
     with no t_* columns — reads store_cash/store_cc/epay_cash/epay_cc/other_account instead, EXACTLY
     mirroring create_row's own fallback (no new math).
  E. Market resolution via storeops.stores, with an explicit "(no market)" bucket for an unresolvable
     store (never silently dropped).
  F. DM-verify join (daily_closing_verification) attaches verified/by/at per (store_code, close_date);
     absent → dm_verified False, not an error.
  G. Gate-status re-derivation (ok/flagged/blocked/recon_pending) computed from the SAME
     _money_issues/_rep_b2b helpers the real close gate uses (monkeypatched _b2b_day so this tests the
     STATUS DERIVATION here, not sales-feed parsing — same pattern as harness_settings_audit.py's
     E10-E14 for closing_stale_stores).
  H. The management-review secrecy boundary: gate_reasons/b2b_cash/b2b_card are POPULATED for a
     company-wide caller and EMPTY/None for a DM (market-scope) caller and for a fully-unauthenticated
     caller, even though gate_status itself (the coarse badge) is visible to all three.
  I. status_capped + a capped row's gate_status='not_computed' beyond the distinct-date cap; truncated
     flag when the row cap is hit.
  J. Custom tender / custom count-field flattening (mig 111 / mig 501) with label resolution, and that
     the standard 3 count keys are excluded from the "custom counts" string (no double-reporting).
  K. Envelope-picture reference: the raw storage PATH is passed through as-is (never a signed URL,
     never embedded/rendered as an image) — no per-row Storage network call on a list endpoint that
     can return thousands of rows.
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


# ── stateful fake supabase client (copied convention from harness_settings_audit.py) ──────────────
class Q:
    def __init__(self, store, table):
        self.s, self.t = store, table
        self.op, self.payload = "select", None
        self.filters = []
        self._ilike = []
        self._limit = None

    def select(self, *a, **k): self.op = "select"; return self
    def insert(self, rows, **k): self.op = "insert"; self.payload = rows; return self
    def update(self, patch, **k): self.op = "update"; self.payload = patch; return self
    def delete(self, **k): self.op = "delete"; return self
    def eq(self, c, v): self.filters.append((c, "eq", v)); return self
    def in_(self, c, v): self.filters.append((c, "in", list(v))); return self
    def gte(self, c, v): self.filters.append((c, "gte", v)); return self
    def lte(self, c, v): self.filters.append((c, "lte", v)); return self
    def ilike(self, c, v): self._ilike.append((c, str(v).strip("%").lower())); return self
    def order(self, *a, **k): return self

    def limit(self, n, *a, **k): self._limit = n; return self

    def _match(self, row):
        for c, kind, v in self.filters:
            rv = row.get(c)
            if kind == "eq" and rv != v: return False
            if kind == "in" and rv not in v: return False
            if kind == "gte" and not (rv is not None and str(rv) >= str(v)): return False
            if kind == "lte" and not (rv is not None and str(rv) <= str(v)): return False
        for c, v in self._ilike:
            if v not in str(row.get(c) or "").lower(): return False
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
            "closing_tender_def": [], "closing_count_field_def": [], "app_users": [], "roles": []}


import app.modules.core.router as core            # noqa: E402
import app.modules.closing.router as cr            # noqa: E402

AUTH_NONE = ""
AUTH_GOOD = "Bearer good-token"


def wire(store):
    fake = FakeClient(store)
    cr.sb = lambda: fake
    cr.get_supabase = lambda: fake   # _signed_envelope calls get_supabase() directly, not sb()
    core.get_supabase = lambda: fake
    core._uid_from_token = lambda a: None
    return fake


def role_row(org, name, perms):
    return {"id": nid("role"), "org_id": org, "name": name, "permissions": perms}


def as_dm(store):
    wire(store)
    store["app_users"] = [{"id": nid(), "auth_id": "uid-1", "org_id": HOUSE, "role": "market_manager", "super_admin": False}]
    store["roles"] = [role_row(HOUSE, "market_manager", {"scope": "market"})]
    core._uid_from_token = lambda a: ("uid-1" if a == AUTH_GOOD else None)


def as_company_wide(store):
    wire(store)
    store["app_users"] = [{"id": nid(), "auth_id": "uid-1", "org_id": HOUSE, "role": "admin", "super_admin": False}]
    store["roles"] = [role_row(HOUSE, "admin", {"scope": "all"})]
    core._uid_from_token = lambda a: ("uid-1" if a == AUTH_GOOD else None)


def base_row(**kw):
    r = {"org_id": HOUSE, "close_date": "2026-07-15", "period": "2026-07",
         "submitted_at": "2026-07-15T20:00:00Z", "store_code": "S1", "store_address": "1 Main St",
         "store_name": "1 Main St", "employee_name": "Jane Rep", "source": "manual",
         "t_cash": 100.0, "t_credit": 50.0, "t_ext_cc": 0.0, "t_gift": 0.0, "t_store_acct": 0.0,
         "t_zelle": 0.0, "t_acima": 0.0, "acc_sale": 25.0, "epay_on_cash": 0.0, "epay_on_credit": 0.0,
         "epay_on_acima": 0.0, "upgrade_count": 1, "new_line_count": 2, "postpaid_count": 0,
         "expense_amount": 0.0, "expense_description": None, "expense_approved": False,
         "attempts": 1, "auto_accepted": False, "mgmt_flag": False, "released_at": None,
         "released_by": None, "correction_count": 0, "envelope_picture": "org1/photo1.jpg",
         "remarks": "", "tenders": None, "counts": None}
    r.update(kw)
    return r


ALL_EXPECTED_KEYS = {
    "id", "close_date", "submitted_at", "store_code", "store_address", "market", "employee_name",
    "source", "t_cash", "t_credit", "t_ext_cc", "t_gift", "t_store_acct", "t_zelle", "t_acima",
    "custom_tenders", "total_collected", "acc_sale", "epay_on_cash", "epay_on_credit", "epay_on_acima",
    "upgrade_count", "new_line_count", "postpaid_count", "custom_counts", "expense_amount",
    "expense_description", "expense_approved", "gate_status", "gate_reasons", "b2b_cash", "b2b_card",
    "attempts", "auto_accepted", "mgmt_flag", "released_at", "released_by", "correction_count",
    "dm_verified", "dm_verified_by", "dm_verified_at", "envelope_picture", "remarks",
}


# ═══════════════════════════════════ A. Org isolation ══════════════════════════════════════════════
st = fresh_store(); wire(st)
st["daily_closing"] = [base_row(id="r1"), base_row(id="r2", org_id=OTHER, store_code="S9")]
resp = cr.closing_submissions(date_from="2026-07-01", date_to="2026-07-31", authorization=AUTH_NONE, org_id=HOUSE)
check("A1. org isolation — only HOUSE row returned", len(resp["rows"]) == 1 and resp["rows"][0]["id"] == "r1",
      str([r["id"] for r in resp["rows"]]))

resp_other = cr.closing_submissions(date_from="2026-07-01", date_to="2026-07-31", authorization=AUTH_NONE, org_id=OTHER)
check("A2. other org sees only its own row", len(resp_other["rows"]) == 1 and resp_other["rows"][0]["id"] == "r2")

# ═══════════════════════════════════ B. Date-range semantics ═══════════════════════════════════════
st = fresh_store(); wire(st)
st["daily_closing"] = [base_row(id="in", close_date="2026-07-15"),
                       base_row(id="before", close_date="2026-06-30"),
                       base_row(id="after", close_date="2026-08-01")]
resp = cr.closing_submissions(date_from="2026-07-01", date_to="2026-07-31", authorization=AUTH_NONE, org_id=HOUSE)
check("B1. inclusive gte/lte — only the in-range row returned",
      [r["id"] for r in resp["rows"]] == ["in"], str([r["id"] for r in resp["rows"]]))

resp_swap = cr.closing_submissions(date_from="2026-07-31", date_to="2026-07-01", authorization=AUTH_NONE, org_id=HOUSE)
check("B2. swapped from>to auto-corrected — still finds the in-range row",
      [r["id"] for r in resp_swap["rows"]] == ["in"])

resp_lone_to = cr.closing_submissions(date_from=None, date_to="2026-07-15", authorization=AUTH_NONE, org_id=HOUSE)
check("B3. lone date_to backfills date_from to that month's 1st",
      resp_lone_to["date_from"] == "2026-07-01" and resp_lone_to["date_to"] == "2026-07-15",
      f"{resp_lone_to['date_from']} / {resp_lone_to['date_to']}")

resp_default = cr.closing_submissions(date_from=None, date_to=None, authorization=AUTH_NONE, org_id=HOUSE)
today = cr._biz_today_iso()
check("B4. no params → default range = current month (1st .. today)",
      resp_default["date_from"] == today[:8] + "01" and resp_default["date_to"] == today)

# ═══════════════════════════════════ C. Every submitted column present ═════════════════════════════
st = fresh_store(); wire(st)
st["daily_closing"] = [base_row(id="full")]
st["stores"] = [{"org_id": HOUSE, "store_code": "S1", "market": "Texas"}]
resp = cr.closing_submissions(date_from="2026-07-01", date_to="2026-07-31", authorization=AUTH_NONE, org_id=HOUSE)
row = resp["rows"][0]
missing = ALL_EXPECTED_KEYS - set(row.keys())
check("C1. every expected column key present on the row payload", not missing, f"missing={missing}")
check("C2. market resolved from storeops.stores", row["market"] == "Texas")

# ═══════════════════════════════════ D. Legacy-fallback tender re-derivation ════════════════════════
st = fresh_store(); wire(st)
legacy = base_row(id="legacy", source="sheet_upload", t_cash=None, t_credit=None, t_ext_cc=None,
                  t_gift=None, t_store_acct=None, t_zelle=None, t_acima=None,
                  store_cash=80.0, store_cc=20.0, epay_cash=5.0, epay_cc=2.0, other_account=11.0)
st["daily_closing"] = [legacy]
resp = cr.closing_submissions(date_from="2026-07-01", date_to="2026-07-31", authorization=AUTH_NONE, org_id=HOUSE)
row = resp["rows"][0]
check("D1. legacy row: cash falls back to store_cash+epay_cash", row["t_cash"] == 85.0, str(row["t_cash"]))
check("D2. legacy row: credit falls back to store_cc+epay_cc", row["t_credit"] == 22.0, str(row["t_credit"]))
check("D3. legacy row: zelle falls back to other_account", row["t_zelle"] == 11.0, str(row["t_zelle"]))
check("D4. legacy row: ext_cc/gift/store_acct/acima are 0 (never fabricated)",
      row["t_ext_cc"] == 0.0 and row["t_gift"] == 0.0 and row["t_store_acct"] == 0.0 and row["t_acima"] == 0.0)
check("D5. total_collected sums the derived tenders", row["total_collected"] == 85.0 + 22.0 + 11.0, str(row["total_collected"]))

# A modern (mig103+) row must NOT use the fallback even if store_cash happens to be populated too.
st2 = fresh_store(); wire(st2)
modern = base_row(id="modern", t_cash=100.0, t_credit=50.0, store_cash=999.0, store_cc=999.0)
st2["daily_closing"] = [modern]
resp2 = cr.closing_submissions(date_from="2026-07-01", date_to="2026-07-31", authorization=AUTH_NONE, org_id=HOUSE)
check("D6. modern row (t_cash present) ignores the legacy mirror columns",
      resp2["rows"][0]["t_cash"] == 100.0 and resp2["rows"][0]["t_credit"] == 50.0)

# ═══════════════════════════════════ E. Market resolution — "(no market)" bucket ═══════════════════
st = fresh_store(); wire(st)
st["daily_closing"] = [base_row(id="unmapped", store_code="S_UNKNOWN")]
st["stores"] = [{"org_id": HOUSE, "store_code": "S1", "market": "Texas"}]
resp = cr.closing_submissions(date_from="2026-07-01", date_to="2026-07-31", authorization=AUTH_NONE, org_id=HOUSE)
check("E1. unresolvable store → explicit '(no market)', never dropped",
      len(resp["rows"]) == 1 and resp["rows"][0]["market"] == "(no market)")

# ═══════════════════════════════════ F. DM-verify join ═════════════════════════════════════════════
st = fresh_store(); wire(st)
st["daily_closing"] = [base_row(id="verified_row", store_code="S1", close_date="2026-07-10"),
                       base_row(id="unverified_row", store_code="S2", close_date="2026-07-10")]
st["daily_closing_verification"] = [{"org_id": HOUSE, "store_code": "S1", "close_date": "2026-07-10",
                                     "verified": True, "verified_by": "dm@x.com", "verified_at": "2026-07-10T22:00:00Z"}]
resp = cr.closing_submissions(date_from="2026-07-01", date_to="2026-07-31", authorization=AUTH_NONE, org_id=HOUSE)
by_id = {r["id"]: r for r in resp["rows"]}
check("F1. verified store-day → dm_verified True + by/at populated",
      by_id["verified_row"]["dm_verified"] is True and by_id["verified_row"]["dm_verified_by"] == "dm@x.com")
check("F2. no verification row → dm_verified False, not an error",
      by_id["unverified_row"]["dm_verified"] is False and by_id["unverified_row"]["dm_verified_by"] is None)

# ═══════════════════════════════════ G. Gate-status re-derivation ══════════════════════════════════
def fake_b2b_day(client, org_id, date):
    days = {
        "2026-07-01": {"has_data": True, "by_store": {}, "by_rep": {("S1", "jane rep"): {"cash": 100.0, "card": 50.0, "acc_gross": 0, "total": 150.0, "salesperson": "Jane Rep", "tenders_available": True}}, "counts": {}},
        "2026-07-02": {"has_data": True, "by_store": {}, "by_rep": {("S1", "jane rep"): {"cash": 150.0, "card": 50.0, "acc_gross": 0, "total": 200.0, "salesperson": "Jane Rep", "tenders_available": True}}, "counts": {}},
        "2026-07-03": {"has_data": True, "by_store": {}, "by_rep": {("S1", "jane rep"): {"cash": 100.0, "card": 30.0, "acc_gross": 0, "total": 130.0, "salesperson": "Jane Rep", "tenders_available": True}}, "counts": {}},
        "2026-07-04": {"has_data": False, "by_store": {}, "by_rep": {}, "counts": {}},
    }
    return days.get(date, {"has_data": False, "by_store": {}, "by_rep": {}, "counts": {}})


st = fresh_store(); wire(st)
_orig_b2b_day = cr._b2b_day
cr._b2b_day = fake_b2b_day
st["daily_closing"] = [
    base_row(id="ok_row", close_date="2026-07-01", t_cash=100.0, t_credit=50.0),        # matches exactly -> ok
    base_row(id="short_row", close_date="2026-07-02", t_cash=100.0, t_credit=50.0),     # declared 100 vs b2b 150 -> short -> block
    base_row(id="over_row", close_date="2026-07-03", t_cash=100.0, t_credit=50.0),      # cash ok; credit 50 vs b2b 30 -> over -> block
    base_row(id="pending_row", close_date="2026-07-04", t_cash=100.0, t_credit=50.0),   # no B2B data -> recon_pending
]
try:
    resp = cr.closing_submissions(date_from="2026-07-01", date_to="2026-07-04", authorization=AUTH_NONE, org_id=HOUSE)
finally:
    pass
by_id = {r["id"]: r for r in resp["rows"]}
check("G1. exact match -> gate_status ok", by_id["ok_row"]["gate_status"] == "ok", by_id["ok_row"]["gate_status"])
check("G2. cash short vs B2B -> gate_status blocked", by_id["short_row"]["gate_status"] == "blocked", by_id["short_row"]["gate_status"])
check("G3. credit over vs B2B -> gate_status blocked", by_id["over_row"]["gate_status"] == "blocked", by_id["over_row"]["gate_status"])
check("G4. no B2B data for the day -> recon_pending", by_id["pending_row"]["gate_status"] == "recon_pending", by_id["pending_row"]["gate_status"])

# ═══════════════════════════════════ H. Management-review secrecy boundary ═════════════════════════
check("H1. unauthenticated caller sees the coarse status but NO reasons/b2b amounts",
      by_id["short_row"]["gate_reasons"] == [] and by_id["short_row"]["b2b_cash"] is None
      and by_id["short_row"]["b2b_card"] is None and by_id["short_row"]["gate_status"] == "blocked")

as_dm(st)
resp_dm = cr.closing_submissions(date_from="2026-07-01", date_to="2026-07-04", authorization=AUTH_GOOD, org_id=HOUSE)
dm_row = {r["id"]: r for r in resp_dm["rows"]}["short_row"]
check("H2. DM (market-scope) caller ALSO gets no reasons/b2b amounts",
      dm_row["gate_reasons"] == [] and dm_row["b2b_cash"] is None and resp_dm["can_review"] is False)

as_company_wide(st)
resp_admin = cr.closing_submissions(date_from="2026-07-01", date_to="2026-07-04", authorization=AUTH_GOOD, org_id=HOUSE)
admin_row = {r["id"]: r for r in resp_admin["rows"]}["short_row"]
check("H3. company-wide caller sees reasons + b2b_cash/card",
      len(admin_row["gate_reasons"]) > 0 and admin_row["b2b_cash"] == 150.0 and resp_admin["can_review"] is True,
      str(admin_row))
check("H4. company-wide caller's gate_status unchanged (still 'blocked')", admin_row["gate_status"] == "blocked")

cr._b2b_day = _orig_b2b_day   # restore before the next section reuses real _b2b_day-adjacent code paths

# ═══════════════════════════════════ I. status_capped + truncated ══════════════════════════════════
st = fresh_store(); wire(st)
cr._b2b_day = fake_b2b_day
rows = [base_row(id=f"d{i}", close_date=f"2026-07-{i+1:02d}") for i in range(5)]
st["daily_closing"] = rows
_orig_cap = cr._SUBMISSIONS_MAX_STATUS_DATES
cr._SUBMISSIONS_MAX_STATUS_DATES = 2
try:
    resp = cr.closing_submissions(date_from="2026-07-01", date_to="2026-07-05", authorization=AUTH_NONE, org_id=HOUSE)
finally:
    cr._SUBMISSIONS_MAX_STATUS_DATES = _orig_cap
    cr._b2b_day = _orig_b2b_day
check("I1. status_capped True when distinct dates exceed the cap", resp["status_capped"] is True)
check("I2. status_dates_computed respects the cap", resp["status_dates_computed"] == 2, str(resp["status_dates_computed"]))
by_id = {r["id"]: r for r in resp["rows"]}
# The cap prioritizes the MOST RECENT distinct dates (d3=07-04, d4=07-05) — d4 has no fake_b2b_day
# entry so it degrades to recon_pending (not "not_computed", since its date WAS computed); d3 has an
# entry (has_data False) -> also recon_pending. d0-d2 (older, beyond the cap) show 'not_computed'.
computed_ids = {r["id"] for r in resp["rows"] if r["gate_status"] != "not_computed"}
check("I3. exactly 2 rows (the 2 most recent dates) got a real status, the rest are 'not_computed'",
      computed_ids == {"d3", "d4"} and all(by_id[f"d{i}"]["gate_status"] == "not_computed" for i in range(3)),
      str({k: v["gate_status"] for k, v in by_id.items()}))

st = fresh_store(); wire(st)
st["daily_closing"] = [base_row(id=f"m{i}", close_date="2026-07-10") for i in range(5)]
_orig_max_rows = cr._SUBMISSIONS_MAX_ROWS
cr._SUBMISSIONS_MAX_ROWS = 3
try:
    resp = cr.closing_submissions(date_from="2026-07-01", date_to="2026-07-31", authorization=AUTH_NONE, org_id=HOUSE)
finally:
    cr._SUBMISSIONS_MAX_ROWS = _orig_max_rows
check("I4. truncated True + row count capped when the row limit is hit",
      resp["truncated"] is True and len(resp["rows"]) == 3, str(len(resp["rows"])))

# ═══════════════════════════════════ J. Custom tender / count flattening ═══════════════════════════
st = fresh_store(); wire(st)
st["closing_tender_def"] = [{"org_id": HOUSE, "tender_key": "crypto", "label": "Crypto Payment", "is_active": True, "sort_order": 1}]
st["closing_count_field_def"] = [{"org_id": HOUSE, "field_key": "trade_in", "label": "Trade-Ins", "is_active": True, "sort_order": 1}]
st["daily_closing"] = [base_row(id="custom_row", tenders={"crypto": 15.5}, counts={"trade_in": 3, "upgrade_count": 99})]
resp = cr.closing_submissions(date_from="2026-07-01", date_to="2026-07-31", authorization=AUTH_NONE, org_id=HOUSE)
row = resp["rows"][0]
check("J1. custom tender flattened with its configured label", "Crypto Payment: $15.50" in row["custom_tenders"], row["custom_tenders"])
check("J2. custom count flattened with its configured label", "Trade-Ins: 3" in row["custom_counts"], row["custom_counts"])
check("J3. a standard field_key inside counts jsonb is NOT double-reported in custom_counts",
      "upgrade_count" not in row["custom_counts"] and "99" not in row["custom_counts"], row["custom_counts"])

# ═══════════════════════════════════ K. Envelope reference ═════════════════════════════════════════
st = fresh_store(); wire(st)
st["daily_closing"] = [base_row(id="with_photo", envelope_picture="org1/photo1.jpg"),
                       base_row(id="no_photo", envelope_picture=None)]
resp = cr.closing_submissions(date_from="2026-07-01", date_to="2026-07-31", authorization=AUTH_NONE, org_id=HOUSE)
by_id = {r["id"]: r for r in resp["rows"]}
check("K1. envelope present -> raw storage path passed through unchanged (a reference, not a signed URL)",
      by_id["with_photo"]["envelope_picture"] == "org1/photo1.jpg")
check("K2. no envelope -> field is None, not an error", by_id["no_photo"]["envelope_picture"] is None)

# ── Summary ──────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
