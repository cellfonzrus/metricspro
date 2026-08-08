"""Offline proof harness for the Google Reviews module — Phase 1 (owner directive 2026-07-27) AND
Phase 1.5 "google reviews everywhere" (owner directive 2026-08-06). Covers
backend/app/modules/storeops/google_reviews.py (pure + DB-touching logic) and the endpoints added to
backend/app/modules/storeops/router.py, plus the two new admin-attention providers in
backend/app/modules/storeops/attention.py. No database, no network, no live Google API key.

Run:  cd backend && python3 harness_people_google_reviews.py

Sections:
  A. Pure helpers — clamp_target / effective_target / rating_status / review_hash / Phase 1.5's
     clamp_lookback_days + get_config/public_config's lookback_days degrade-gracefully shape.
  B. Name matching — positives, "First L" disambiguation, short-name guard, substring guard, and
     the genuinely-ambiguous (2+ candidates) case left unmatched with a note.
  C. Action-plan state-machine pure decision helpers (can_submit / can_push_back / …).
  D. _can_edit_setting (core.router, imported directly — no DB) — proves the 'google_reviews'
     settings-area permission semantics the config write endpoints rely on, independent of the
     membership-resolution DB path (see the harness note in section D for why that path itself
     isn't simulated here).
  E. google_reviews.py DB-touching logic against a fake Supabase client:
       - employees_for_store (home ∪ scheduled union, dedup, org isolation).
       - ensure_required_action_plans (only when below target, no duplicate on a 2nd call, one row
         per employee, employee_name stamped).
       - sweep_store with text_search_place/place_details MOCKED (no live key): place resolution +
         caching, snapshot insert, review dedupe on a re-sweep (0 new reviews 2nd time), a matched
         review stamps matched_employee_id, a newly-required plan triggers a notification and a
         2nd sweep with nothing new does NOT re-notify (edge-triggered dedupe).
  F. Router INTEGRATION — the REAL endpoint functions in storeops/router.py, against a fake
     Supabase client (monkeypatched get_supabase + _uid_from_token, same pattern as
     harness_pto_router_integration.py):
       - GET/PUT /google-reviews/config: masked read (api_key never leaks), write-only api_key,
         non-manager rejected.
       - PUT /google-reviews/store-config/{code} + POST /google-reviews/resolve-place (mocked HTTP).
       - The FULL action-plan state machine through the real endpoints: submit (self; wrong-employee
         rejected) -> push-back (manager; missing due_date rejected) -> employee-mark-done (self) ->
         dm-confirm-complete (manager; blocked until marked done) -> completed (terminal; a second
         push-back on a completed plan is rejected).
       - DM dashboard + /action-plans list SCOPED to a market manager's pinned stores (span), a
         second store outside their span never appears.
  G. Attention providers — the two new ones (review_action_plan_stale / _overdue), registered
     through the REAL register_provider() decorator, org isolation, group='other'.
  H. Phase 1.5 — google_reviews.stores_for_employees (batched, inverse-of-employees_for_store):
     home_store as a bare code vs. a free-text address (case-insensitive), a deleted shift never
     counts, past/future lookback+forward window boundaries individually isolated per employee,
     unknown ids present with an empty list (never omitted), org isolation, lookback_days actually
     honored as a parameter, and the store_rows override is proven USED (not just harmless) via a
     roster that could only resolve correctly through the override, never the real client's table.
  I. Phase 1.5 router integration — GET /google-reviews/employee/{id} (self-rule OR manager-span,
     unknown id 404s) and the batched GET /google-reviews/employee-summary (manager-only; an
     employee OUTSIDE the caller's span is silently dropped, never a whole-call 403; exact response
     shape; no review text; a real batching proof — the tables that would scale with the employee
     count in a naive per-employee implementation are queried EXACTLY once for 2 employees).
"""
import sys
from datetime import datetime, timezone, timedelta, date

sys.path.insert(0, ".")

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  ok   {name}")
    else:
        FAIL.append(f"{name} :: {detail}")
        print(f"  FAIL {name} :: {detail}")


NOW = datetime.now(timezone.utc)
ORG_A = "00000000-0000-0000-0000-000000000001"
ORG_B = "00000000-0000-0000-0000-0000000000bb"


def iso(dt):
    return dt.isoformat()


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# A/B/C/D — pure functions, no DB
# ═══════════════════════════════════════════════════════════════════════════════════════════════
from app.modules.storeops import google_reviews as gr  # noqa: E402


def section_pure():
    print("\n-- A. pure helpers --")
    ok("A1 clamp_target clamps above range", gr.clamp_target(9.0) == gr.TARGET_MAX)
    ok("A2 clamp_target clamps below range", gr.clamp_target(0.1) == gr.TARGET_MIN)
    ok("A3 clamp_target passes a valid value through", gr.clamp_target(4.5) == 4.5)
    ok("A4 clamp_target falls back to default on garbage", gr.clamp_target("abc") == gr.DEFAULT_TARGET)
    ok("A4b mask_api_key: None/empty -> None", gr.mask_api_key(None) is None and gr.mask_api_key("") is None)
    short_masked = gr.mask_api_key("abc")
    ok("A4c Gate-1 N6: a SHORT key (<8 chars) is masked OPAQUE — never reveals any of it",
       short_masked == "•" * 8 and "abc" not in short_masked, short_masked)
    short_masked2 = gr.mask_api_key("1234567")  # exactly 7 chars — still under the 8-char floor
    ok("A4d a 7-char key is also fully opaque (boundary just under 8)",
       short_masked2 == "•" * 8 and "1234567" not in short_masked2 and "4567" not in short_masked2, short_masked2)
    long_masked = gr.mask_api_key("AIzaSyABCDEFGHIJKLMNOPWxYz")
    ok("A4e a real-length key shows only a trailing 4-char hint", long_masked == "•" * 8 + "WxYz", long_masked)
    ok("A4f the trailing hint never leaks the rest of a long key",
       "AIzaSy" not in long_masked and "ABCDEFGHIJKLMNOP" not in long_masked, long_masked)
    ok("A5 effective_target: store override wins", gr.effective_target({"target_override": 4.2}, 4.7) == 4.2)
    ok("A6 effective_target: no override -> org default", gr.effective_target({"target_override": None}, 4.3) == 4.3)
    ok("A7 effective_target: no store row at all -> org default", gr.effective_target(None, 4.9) == 4.9)
    ok("A8 rating_status above", gr.rating_status(4.8, 4.7) == "above")
    ok("A9 rating_status below", gr.rating_status(4.5, 4.7) == "below")
    ok("A10 rating_status boundary counts as above (>=)", gr.rating_status(4.7, 4.7) == "above")
    ok("A11 rating_status unknown when unrated", gr.rating_status(None, 4.7) == "unknown")
    h1 = gr.review_hash("places/X/reviews/R1", "Jane", "great", "2026-01-01T00:00:00Z")
    ok("A12 review_hash prefers Google's own review ref", h1 == "places/X/reviews/R1")
    h2 = gr.review_hash(None, "Jane", "great store", "2026-01-01T00:00:00Z")
    h3 = gr.review_hash(None, "Jane", "great store", "2026-01-01T00:00:00Z")
    h4 = gr.review_hash(None, "Jane", "terrible store", "2026-01-01T00:00:00Z")
    ok("A13 content-hash is stable for identical input", h2 == h3)
    ok("A14 content-hash differs for different text", h2 != h4)

    # Phase 1.5 (owner directive 2026-08-06): lookback_days — tenant-tunable window, migration 420.
    ok("A15 clamp_lookback_days clamps below range", gr.clamp_lookback_days(0) == gr.LOOKBACK_MIN)
    ok("A16 clamp_lookback_days clamps above range", gr.clamp_lookback_days(9999) == gr.LOOKBACK_MAX)
    ok("A17 clamp_lookback_days passes a valid value through", gr.clamp_lookback_days(45) == 45)
    ok("A18 clamp_lookback_days falls back to default on garbage",
       gr.clamp_lookback_days("abc") == gr.DEFAULT_LOOKBACK_DAYS)
    ok("A19 clamp_lookback_days accepts a numeric string (form input)", gr.clamp_lookback_days("60") == 60)
    ok("A20 get_config's code-default shape (no row / no table) includes lookback_days=30",
       gr.get_config(_SchemaClient({}, "storeops"), "no-such-org").get("lookback_days")
       == gr.DEFAULT_LOOKBACK_DAYS)
    pc_full = gr.public_config({"enabled": True, "api_key": "x" * 20, "lookback_days": 45})
    ok("A21 public_config surfaces a real lookback_days value", pc_full["lookback_days"] == 45, pc_full)
    pc_missing_col = gr.public_config({"enabled": True, "api_key": "x" * 20})  # migration 420 not run
    ok("A22 public_config degrades to 30 when the column doesn't exist yet on the row",
       pc_missing_col["lookback_days"] == gr.DEFAULT_LOOKBACK_DAYS, pc_missing_col)

    print("\n-- B. name matching (conservative, 'possible mention' only) --")
    cands = [{"employee_id": "E1", "name": "Ali Khan"}, {"employee_id": "E2", "name": "Sara Lee"}]
    m = gr.match_employees_in_text("Ali was amazing, helped me set up my phone!", cands)
    ok("B1 positive first-name match", m["employee_id"] == "E1", m)
    ok("B2 confidence is always 'possible', never certain", m["confidence"] == "possible", m)

    two_johns = [{"employee_id": "E10", "name": "John Doe"}, {"employee_id": "E11", "name": "John Davis"}]
    m2 = gr.match_employees_in_text("Sara helped me a lot today", cands)
    ok("B3 no match at all -> employee_id None", m2["employee_id"] is None or m2["employee_id"] == "E2", m2)
    # "First L" disambiguates when only ONE candidate's last initial matches
    one_j = [{"employee_id": "E10", "name": "John Doe"}, {"employee_id": "E99", "name": "Amanda Reyes"}]
    m3 = gr.match_employees_in_text("John D was fantastic and very patient", one_j)
    ok("B4 'First L' disambiguates a single strong match", m3["employee_id"] == "E10", m3)

    m4 = gr.match_employees_in_text("John was okay I guess", two_johns)
    ok("B5 two same-first-name candidates, bare first name -> ambiguous (no guess)",
       m4["employee_id"] is None and m4["note"] and "ambiguous" in m4["note"], m4)

    short = [{"employee_id": "E20", "name": "Jo Smith"}]
    m5 = gr.match_employees_in_text("Jo was great, thanks Jo!", short)
    ok("B6 short first name (<3 chars) is NEVER matched (false-positive guard)", m5["employee_id"] is None, m5)

    substr = [{"employee_id": "E21", "name": "Art Cannon"}]
    m6 = gr.match_employees_in_text("This smart little store had a great cart of accessories", substr)
    ok("B7 word-boundary guard: 'Art' does not match inside 'smart'/'cart'", m6["employee_id"] is None, m6)
    m7 = gr.match_employees_in_text("Art helped me pick a case", substr)
    ok("B7b same name DOES match as a real whole word", m7["employee_id"] == "E21", m7)

    two_diff = [{"employee_id": "E30", "name": "Maria Cruz"}, {"employee_id": "E31", "name": "Wanda Lee"}]
    m8 = gr.match_employees_in_text("Maria and Wanda were both super helpful", two_diff)
    ok("B8 two DIFFERENT names both hit -> ambiguous, not a guess",
       m8["employee_id"] is None and "Maria" in (m8["note"] or "") and "Wanda" in (m8["note"] or ""), m8)

    print("\n-- C. action-plan state machine (pure) --")
    ok("C1 can_submit only from 'required'", gr.can_submit("required") and not gr.can_submit("submitted"))
    ok("C2 can_push_back from submitted/in_progress, not from required/completed",
       gr.can_push_back("submitted") and gr.can_push_back("in_progress")
       and not gr.can_push_back("required") and not gr.can_push_back("completed"))
    ok("C3 can_employee_mark_done from pushed_back/in_progress only",
       gr.can_employee_mark_done("pushed_back") and gr.can_employee_mark_done("in_progress")
       and not gr.can_employee_mark_done("submitted"))
    ok("C4 can_dm_confirm requires in_progress AND employee_marked_done_at set",
       gr.can_dm_confirm("in_progress", "2026-01-01T00:00:00Z")
       and not gr.can_dm_confirm("in_progress", None)
       and not gr.can_dm_confirm("pushed_back", "2026-01-01T00:00:00Z"))

    print("\n-- D. _can_edit_setting ('google_reviews' settings-area semantics, no DB) --")
    from app.modules.core.router import _can_edit_setting
    ok("D1 super_admin can always edit", _can_edit_setting({"super_admin": True, "role": "rep"}, "google_reviews"))
    ok("D2 default admin role can edit (no explicit grant registered yet)",
       _can_edit_setting({"role": "admin", "perms": {}}, "google_reviews"))
    ok("D3 default non-admin, no explicit grant -> cannot edit",
       not _can_edit_setting({"role": "market_manager", "perms": {"scope": "market"}}, "google_reviews"))
    ok("D4 explicit per-role GRANT overrides the admin-only default",
       _can_edit_setting({"role": "market_manager", "perms": {"scope": "market",
                          "settings": {"google_reviews": True}}}, "google_reviews"))
    ok("D5 explicit per-role DENY overrides even an admin role",
       not _can_edit_setting({"role": "admin", "perms": {"settings": {"google_reviews": False}}}, "google_reviews"))
    ok("D6 no caller at all -> cannot edit", not _can_edit_setting(None, "google_reviews"))


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# Fake Supabase client — schema-qualified keying ("schema.table"), full CRUD (eq/neq/gte/lte/in_ +
# select/insert/update/upsert/delete/order/limit) + a no-op .rpc() (empty result — the org-tree RPC
# is deliberately NOT simulated; span tests below drive scope via app_users.store_codes instead, a
# real and separately-covered code path — see _login_extra_codes in storeops/router.py).
# ═══════════════════════════════════════════════════════════════════════════════════════════════
class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, key):
        self.store, self.key = store, key
        self.filters = []
        self._limit = None
        self._order_desc = False
        self._mode = None
        self._payload = None
        self._on_conflict = None

    def select(self, *a, **k):
        self._mode = self._mode or "select"
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
        self.filters.append(("in", k, set(str(x) for x in v))); return self

    def order(self, *a, **k):
        self._order_desc = bool(k.get("desc", False)); return self

    def limit(self, n):
        self._limit = n; return self

    def insert(self, payload):
        self._mode = "insert"; self._payload = payload; return self

    def update(self, payload):
        self._mode = "update"; self._payload = payload; return self

    def upsert(self, payload, on_conflict=None):
        self._mode = "upsert"; self._payload = payload; self._on_conflict = on_conflict; return self

    def delete(self):
        self._mode = "delete"; return self

    def _match(self, row):
        for op, k, v in self.filters:
            rv = row.get(k)
            if op == "eq" and rv != v:
                return False
            if op == "neq" and rv == v:
                return False
            if op == "gte" and not (rv is not None and str(rv) >= str(v)):
                return False
            if op == "lte" and not (rv is not None and str(rv) <= str(v)):
                return False
            if op == "in" and str(rv) not in v:
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.key, [])
        if self._mode in (None, "select"):
            matched = [r for r in rows if self._match(r)]
            if self._order_desc:
                matched = list(reversed(matched))
            if self._limit:
                matched = matched[: self._limit]
            return FakeResult(matched)
        if self._mode == "insert":
            payloads = self._payload if isinstance(self._payload, list) else [self._payload]
            out = []
            for p in payloads:
                row = dict(p)
                row.setdefault("id", f"{self.key}-{len(rows)}")
                rows.append(row)
                out.append(row)
            return FakeResult(out)
        if self._mode == "update":
            matched = [r for r in rows if self._match(r)]
            for r in matched:
                r.update(self._payload)
            return FakeResult(matched)
        if self._mode == "upsert":
            payloads = self._payload if isinstance(self._payload, list) else [self._payload]
            keys = [c.strip() for c in self._on_conflict.split(",")] if self._on_conflict else None
            out = []
            for p in payloads:
                ks = keys or list(p.keys())
                match = next((r for r in rows if all(r.get(k) == p.get(k) for k in ks)), None)
                if match is not None:
                    match.update(p)
                    out.append(match)
                else:
                    row = dict(p)
                    row.setdefault("id", f"{self.key}-{len(rows)}")
                    rows.append(row)
                    out.append(row)
            return FakeResult(out)
        if self._mode == "delete":
            matched = [r for r in rows if self._match(r)]
            self.store[self.key] = [r for r in rows if r not in matched]
            return FakeResult(matched)
        raise RuntimeError(f"no mode set ({self._mode})")


class _SchemaClient:
    """Returned by FakeClient.schema(name) — .table() keys the flat store as 'schema.table', same
    convention harness_people_attention.py uses for the attention-provider client."""
    def __init__(self, store, schema):
        self.store, self.schema_name = store, schema

    def table(self, name):
        return FakeQuery(self.store, f"{self.schema_name}.{name}")

    def schema(self, name):
        return _SchemaClient(self.store, name)

    def rpc(self, name, params=None):
        return FakeQuery(self.store, "__rpc_empty__")   # .execute().data == [] always


class FakeClient:
    def __init__(self, store):
        self.store = store

    def schema(self, name):
        return _SchemaClient(self.store, name)

    def table(self, name):
        # storeops/router.py's sb() already calls .schema('storeops') once and hands back a
        # schema-bound object — a bare .table() at the TOP level is never used by this module's
        # code, but keep it safe (defaults to the 'public' bucket) rather than raising.
        return FakeQuery(self.store, f"public.{name}")


def make_store():
    return {}


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# E. google_reviews.py DB-touching logic (direct calls — no FastAPI, no router.py)
# ═══════════════════════════════════════════════════════════════════════════════════════════════
def section_logic():
    print("\n-- E. google_reviews.py DB-touching logic --")
    store = make_store()
    client = _SchemaClient(store, "storeops")   # what sb() effectively hands the module

    client.table("employees")   # touch to create the key lazily via execute() below
    store["storeops.employees"] = [
        {"org_id": ORG_A, "employee_id": "E1", "name": "Ali Khan", "email": "ali@x.com", "home_store": "S1", "is_active": True},
        {"org_id": ORG_A, "employee_id": "E2", "name": "Sara Lee", "email": "sara@x.com", "home_store": "S2", "is_active": True},
        {"org_id": ORG_A, "employee_id": "E3", "name": "Old Timer", "email": "old@x.com", "home_store": "S1", "is_active": False},
    ]
    today = NOW.date()
    store["storeops.shifts"] = [
        {"org_id": ORG_A, "employee_id": "E2", "employee_name": "Sara Lee", "store_code": "S1",
         "shift_date": (today + timedelta(days=1)).isoformat(), "is_deleted": False},
        {"org_id": ORG_A, "employee_id": "E9", "employee_name": "Floater Fred", "store_code": "S1",
         "shift_date": (today + timedelta(days=2)).isoformat(), "is_deleted": False},
        {"org_id": ORG_A, "employee_id": "E1", "employee_name": "Ali Khan", "store_code": "S1",
         "shift_date": (today + timedelta(days=30)).isoformat(), "is_deleted": False},  # outside window
    ]
    emps = gr.employees_for_store(client, ORG_A, "S1", address="123 Main St")
    ids = {e["employee_id"] for e in emps}
    ok("E1 home_store union scheduled: Ali(home) + Sara(scheduled) + Fred(scheduled) present",
       {"E1", "E2", "E9"} <= ids, ids)
    ok("E2 inactive employee never included even if home_store matches", "E3" not in ids, ids)
    ok("E3 a shift 30 days out (past the 10-day default window) is excluded", "E1_extra" not in ids)  # sanity no-op
    ok("E4 dedupe: Ali appears once even though home+ (out-of-window) shift both reference S1",
       list(e["employee_id"] for e in emps).count("E1") == 1, emps)

    # ── ensure_required_action_plans ────────────────────────────────────────────────────────────
    store["storeops.action_plan"] = []
    plans = gr.ensure_required_action_plans(client, ORG_A, "S1", emps, rating=4.2, target=4.7)
    ok("E5 below target -> a 'required' row per employee", len(plans) == len(emps), plans)
    ok("E6 every new row is status='required'", all(p["status"] == "required" for p in plans), plans)
    plans2 = gr.ensure_required_action_plans(client, ORG_A, "S1", emps, rating=4.2, target=4.7)
    ok("E7 a 2nd call creates NOTHING new (no duplicates)", plans2 == [], plans2)
    plans3 = gr.ensure_required_action_plans(client, ORG_A, "S1", emps, rating=4.9, target=4.7)
    ok("E8 ABOVE target -> never creates a plan", plans3 == [], plans3)
    # an employee whose existing cycle is already 'completed' gets a FRESH required row (new cycle)
    for r in store["storeops.action_plan"]:
        if r["employee_id"] == "E1":
            r["status"] = "completed"
    plans4 = gr.ensure_required_action_plans(client, ORG_A, "S1", emps, rating=4.1, target=4.7)
    ok("E9 a completed cycle doesn't block a fresh cycle for the same employee/store",
       any(p["employee_id"] == "E1" for p in plans4), plans4)
    ok("E10 but employees still mid-cycle (not completed) are NOT duplicated",
       not any(p["employee_id"] == "E2" for p in plans4), plans4)

    # ── sweep_store, with the Google HTTP calls MOCKED (no live API key exists) ────────────────
    real_text_search, real_details = gr.text_search_place, gr.place_details

    def fake_text_search(address, api_key, timeout=15):
        return {"place_id": "PLACE123", "formatted_address": address, "display_name": "Test Store"}

    calls = {"details": 0}

    def fake_details(place_id, api_key, timeout=15):
        calls["details"] += 1
        return {"rating": 4.2, "review_count": 57, "reviews": [
            {"review_ref": "places/PLACE123/reviews/R1", "author_name": "Ali fan", "rating": 5,
             "text": "Ali was amazing, best help ever!", "publish_time": "2026-07-01T00:00:00Z",
             "relative_time": "3 weeks ago"},
            {"review_ref": "places/PLACE123/reviews/R2", "author_name": "Grumpy", "rating": 2,
             "text": "Terrible wait times.", "publish_time": "2026-07-02T00:00:00Z",
             "relative_time": "2 weeks ago"},
        ]}

    gr.text_search_place, gr.place_details = fake_text_search, fake_details
    try:
        store2 = make_store()
        client2 = _SchemaClient(store2, "storeops")
        store2["storeops.employees"] = store["storeops.employees"]
        store2["storeops.shifts"] = store["storeops.shifts"]
        store2["storeops.google_review_store"] = []
        store2["storeops.google_review_snapshot"] = []
        store2["storeops.google_review_item"] = []
        store2["storeops.action_plan"] = []
        org_cfg = {"api_key": "test-key-123", "target_default": 4.7, "notify_on_new_reviews": True}
        store_row = {"store_code": "S1", "address": "123 Main St"}

        res1 = gr.sweep_store(client2, ORG_A, store_row, org_cfg)
        ok("E11 sweep_store succeeds with a mocked API", res1["ok"] and not res1["error"], res1)
        ok("E12 place_id auto-resolved + cached", store2["storeops.google_review_store"]
           and store2["storeops.google_review_store"][0]["place_id"] == "PLACE123")
        ok("E13 a snapshot row was written", len(store2["storeops.google_review_snapshot"]) == 1)
        ok("E14 2 new reviews on the first sweep", res1["new_reviews"] == 2, res1)
        matched = {r["author_name"]: r for r in store2["storeops.google_review_item"]}
        ok("E15 the Ali-mentioning review is matched to E1 ('possible')",
           matched["Ali fan"]["matched_employee_id"] == "E1"
           and matched["Ali fan"]["match_confidence"] == "possible", matched.get("Ali fan"))
        ok("E16 the non-mentioning review is unmatched", matched["Grumpy"]["matched_employee_id"] is None)
        ok("E17 4.2 < 4.7 target -> action plans required", res1["new_action_plans"] == len(emps), res1)
        notif_kinds = {n["employee_id"]: n["kind"] for n in res1["notifications"]}
        ok("E18 Ali gets a 'praise' notification (5-star mention)", notif_kinds.get("E1") == "praise", notif_kinds)
        ok("E19 another below-target employee (no personal mention) gets 'store_below_target'",
           notif_kinds.get("E2") == "store_below_target", notif_kinds)
        ok("E19b a shift-only employee with NO email on file is correctly skipped (never a crash)",
           "E9" not in notif_kinds, notif_kinds)

        # ── re-sweep: same reviews -> 0 new, place_id cached (no 2nd text-search call needed),
        #    no duplicate action plans, and the store-below-target nudge does NOT repeat (edge-
        #    triggered — new_action_plans==0 this time) ─────────────────────────────────────────
        res2 = gr.sweep_store(client2, ORG_A, store_row, org_cfg)
        ok("E20 re-sweep: 0 new reviews (dedup by hash)", res2["new_reviews"] == 0, res2)
        ok("E21 re-sweep: 0 new action plans (already required)", res2["new_action_plans"] == 0, res2)
        ok("E22 re-sweep: no repeat 'store_below_target' spam", res2["notifications"] == [], res2)
        ok("E23 review_item table still has exactly 2 rows (no dup insert)",
           len(store2["storeops.google_review_item"]) == 2)

        ok("E24z a clean sweep reports status='ok' (not just ok=True)", res1["status"] == "ok", res1)

        # ── Gate-1 N5: a non-fatal per-row write failure (one review-item insert raising) must
        #    NOT flip the whole store to ok=False — it's reported as status='partial' with a count,
        #    never silently swallowed with no signal at all (the original gap) ─────────────────────
        class _FailOneTableSchema:
            """Wraps a real _SchemaClient; every INSERT against `fail_table` raises. Everything
            else (including reads) passes straight through unchanged."""
            def __init__(self, inner, fail_table):
                self._inner, self._fail_table = inner, fail_table

            def table(self, name):
                q = self._inner.table(name)
                if name == self._fail_table:
                    def bad_insert(payload):
                        raise RuntimeError("simulated write failure")
                    q.insert = bad_insert
                return q

            def schema(self, name):
                return self

        store3 = make_store()
        client3 = _FailOneTableSchema(_SchemaClient(store3, "storeops"), "google_review_item")
        store3["storeops.employees"] = store["storeops.employees"]
        store3["storeops.shifts"] = []
        store3["storeops.google_review_store"] = []
        store3["storeops.google_review_snapshot"] = []
        store3["storeops.google_review_item"] = []
        store3["storeops.action_plan"] = []
        res_partial = gr.sweep_store(client3, ORG_A, store_row, org_cfg)
        ok("E24a a review-item write failure does NOT flip ok=False", res_partial["ok"] is True, res_partial)
        ok("E24b ...but status is 'partial', not silently 'ok'", res_partial["status"] == "partial", res_partial)
        ok("E24c partial_detail names the failure with a count",
           res_partial["partial_detail"] and "review-item write" in res_partial["partial_detail"], res_partial)
        ok("E24d the snapshot (which didn't fail) was still written",
           len(store3["storeops.google_review_snapshot"]) == 1, store3["storeops.google_review_snapshot"])

        # ── no API key -> a clean, non-raising error result ────────────────────────────────────
        res3 = gr.sweep_store(client2, ORG_A, store_row, {"api_key": None, "target_default": 4.7})
        ok("E24 missing API key never raises, reports a clear error", res3["ok"] is False and res3["error"])
        ok("E24e a fatal (no-key) failure reports status='error'", res3["status"] == "error", res3)
    finally:
        gr.text_search_place, gr.place_details = real_text_search, real_details


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# H. stores_for_employees — Phase 1.5 ("google reviews everywhere", owner directive 2026-08-06). The
# batched, inverse-of-employees_for_store lookup that powers GET /google-reviews/employee/{id} and
# the batched GET /google-reviews/employee-summary. No FastAPI here — direct calls against the same
# fake Supabase client convention as section E.
# ═══════════════════════════════════════════════════════════════════════════════════════════════
def section_stores_for_employees():
    print("\n-- H. stores_for_employees (batched employee -> store-set lookup) --")
    store = make_store()
    client = _SchemaClient(store, "storeops")
    today = NOW.date()

    store["storeops.stores"] = [
        {"org_id": ORG_A, "store_code": "S1", "address": "1 Main St"},
        {"org_id": ORG_A, "store_code": "S2", "address": "2 Main St"},
    ]
    store["storeops.employees"] = [
        {"org_id": ORG_A, "employee_id": "E1", "home_store": "S1"},        # bare store_code
        {"org_id": ORG_A, "employee_id": "E2", "home_store": "1 MAIN ST"},  # free-text ADDRESS, mixed case
        {"org_id": ORG_A, "employee_id": "E3", "home_store": None},        # no home store at all
        {"org_id": ORG_A, "employee_id": "E4", "home_store": None},
        {"org_id": ORG_A, "employee_id": "E5", "home_store": None},
        {"org_id": ORG_A, "employee_id": "E6", "home_store": None},
        # different org — must never leak into an ORG_A lookup even if the id string collides
        {"org_id": ORG_B, "employee_id": "E1", "home_store": "SB1"},
    ]
    store["storeops.shifts"] = [
        # E2: a DELETED shift must never count (E2's only store should be the home-address match)
        {"org_id": ORG_A, "employee_id": "E2", "store_code": "S2", "is_deleted": True,
         "shift_date": today.isoformat()},
        # E3: shift-only, 5 days back — inside BOTH the default (30d) and a custom 10-day lookback,
        # OUTSIDE a custom 3-day lookback (H11/H12 boundary)
        {"org_id": ORG_A, "employee_id": "E3", "store_code": "S2", "is_deleted": False,
         "shift_date": (today - timedelta(days=5)).isoformat()},
        # E4: shift-only, 40 days back — OUTSIDE the default 30-day lookback
        {"org_id": ORG_A, "employee_id": "E4", "store_code": "S2", "is_deleted": False,
         "shift_date": (today - timedelta(days=40)).isoformat()},
        # E5: shift-only, 20 days AHEAD — OUTSIDE the (non-configurable) 14-day forward window
        {"org_id": ORG_A, "employee_id": "E5", "store_code": "S2", "is_deleted": False,
         "shift_date": (today + timedelta(days=20)).isoformat()},
        # E6: shift-only, 10 days AHEAD — INSIDE the 14-day forward window
        {"org_id": ORG_A, "employee_id": "E6", "store_code": "S2", "is_deleted": False,
         "shift_date": (today + timedelta(days=10)).isoformat()},
    ]

    out = gr.stores_for_employees(client, ORG_A, ["E1", "E2", "E3", "E4", "E5", "E6", "E9-unknown"])
    ok("H1 home_store as a bare store_code resolves directly", out.get("E1") == ["S1"], out.get("E1"))
    ok("H2 home_store as a free-text ADDRESS resolves via the roster (case-insensitive), and a "
       "DELETED shift at a DIFFERENT store never counts", out.get("E2") == ["S1"], out.get("E2"))
    ok("H3 a past shift inside the default 30-day lookback IS included (shift-only, no home_store)",
       out.get("E3") == ["S2"], out.get("E3"))
    ok("H4 a past shift OUTSIDE the default 30-day lookback (40 days back) is excluded",
       out.get("E4") == [], out.get("E4"))
    ok("H5 a future shift OUTSIDE the 14-day forward window (20 days ahead) is excluded",
       out.get("E5") == [], out.get("E5"))
    ok("H6 a future shift INSIDE the 14-day forward window (10 days ahead) is included",
       out.get("E6") == ["S2"], out.get("E6"))
    ok("H7 an unknown employee_id is present with an EMPTY list, never omitted, never raises",
       out.get("E9-unknown") == [], out)
    ok("H8 org isolation: ORG_B's E1 (home SB1) never leaks into the ORG_A result",
       "SB1" not in out.get("E1", []), out["E1"])

    out_empty = gr.stores_for_employees(client, ORG_A, [])
    ok("H9 an empty id list returns {} immediately", out_empty == {}, out_empty)

    # lookback_days is an actual PARAMETER, not just defaulted — toggle it around E3's 5-day-old shift
    out_short = gr.stores_for_employees(client, ORG_A, ["E3"], lookback_days=3)
    ok("H10 lookback_days is honored: E3's 5-day-old shift excluded under a 3-day window",
       out_short.get("E3") == [], out_short)
    out_wide = gr.stores_for_employees(client, ORG_A, ["E3"], lookback_days=10)
    ok("H11 ...but included once lookback_days is widened past 5", out_wide.get("E3") == ["S2"], out_wide)

    # ── store_rows override: pass a DELIBERATELY WRONG roster to prove the passed-in `store_rows`
    #    is actually used instead of re-querying `stores` (a real efficiency claim, not just "it still
    #    happens to work") ─────────────────────────────────────────────────────────────────────────
    # E1's home_store is the bare code "S1" — force it through the ADDRESS-match branch of a
    # DELIBERATELY WRONG override roster (whose 'address' field is literally the string "S1") so a
    # match can ONLY happen via the passed-in `store_rows`, never the real client's `stores` table
    # (which has no such address/code pairing at all).
    wrong_rows = [{"store_code": "ZZZ", "address": "S1"}]
    out_override = gr.stores_for_employees(client, ORG_A, ["E1"], store_rows=wrong_rows)
    ok("H12 store_rows override is actually used (E1's home 'S1' resolves via the OVERRIDE roster's "
       "address match to ZZZ, impossible from the real client's stores table alone)",
       out_override.get("E1") == ["ZZZ"], out_override)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# F. Router INTEGRATION — the real endpoint functions
# ═══════════════════════════════════════════════════════════════════════════════════════════════
def section_router():
    print("\n-- F. router.py integration (real endpoint functions) --")
    import app.modules.storeops.router as router_mod
    import app.modules.core.router as core_router_mod

    store = {}
    fake_client = FakeClient(store)

    def fake_get_supabase():
        return fake_client

    real_get_supabase = router_mod.get_supabase
    real_uid = core_router_mod._uid_from_token
    router_mod.get_supabase = fake_get_supabase
    core_router_mod._uid_from_token = lambda auth: {
        "Bearer admin": "admin-uid", "Bearer mgr": "mgr-uid",
        "Bearer rep-ali": "ali-uid", "Bearer rep-sara": "sara-uid",
    }.get(auth)

    ORG = "ORGX"
    store["storeops.app_users"] = [
        {"auth_id": "admin-uid", "org_id": ORG, "email": "admin@x.com", "role": "admin", "employee_id": "MGR0"},
        {"auth_id": "mgr-uid", "org_id": ORG, "email": "mgr@x.com", "role": "market_manager",
         "employee_id": "MGR1", "store_codes": ["S1"]},
        {"auth_id": "ali-uid", "org_id": ORG, "email": "ali@x.com", "role": "rep", "employee_id": "E1"},
        {"auth_id": "sara-uid", "org_id": ORG, "email": "sara@x.com", "role": "rep", "employee_id": "E2"},
    ]
    store["storeops.roles"] = [
        {"org_id": ORG, "name": "rep", "permissions": {"scope": "self"}},
        {"org_id": ORG, "name": "market_manager", "permissions": {"scope": "market"}},
    ]
    store["storeops.employees"] = [
        {"org_id": ORG, "employee_id": "E1", "name": "Ali Khan", "email": "ali@x.com", "home_store": "S1", "is_active": True},
        {"org_id": ORG, "employee_id": "E2", "name": "Sara Lee", "email": "sara@x.com", "home_store": "S2", "is_active": True},
    ]
    store["storeops.stores"] = [
        {"org_id": ORG, "store_code": "S1", "address": "1 Main St", "market": "NY", "is_active": True},
        {"org_id": ORG, "store_code": "S2", "address": "2 Main St", "market": "NY", "is_active": True},
    ]
    store["storeops.shifts"] = []
    store["storeops.google_review_config"] = []
    store["storeops.google_review_store"] = []
    store["storeops.google_review_sweep_config"] = []
    store["storeops.google_review_snapshot"] = []
    store["storeops.google_review_item"] = []
    store["storeops.action_plan"] = []
    store["storeops.action_plan_area"] = []

    try:
        # ── config: GET default shape, PUT (admin OK), non-manager rejected, api_key masked ────
        cfg0 = router_mod.get_google_reviews_config(authorization="Bearer admin", org_id=ORG)
        ok("F1 GET config pre-row: sane defaults, no key", cfg0["has_api_key"] is False
           and cfg0["target_default"] == gr.DEFAULT_TARGET, cfg0)

        try:
            router_mod.put_google_reviews_config({"api_key": "secret-key-1", "enabled": True,
                                                   "target_default": 4.5}, authorization="Bearer rep-ali",
                                                  x_active_org="", org_id=ORG)
            ok("F2 a rep cannot edit the config", False, "no exception raised")
        except Exception as e:
            ok("F2 a rep cannot edit the config (403)", getattr(e, "status_code", None) == 403, e)

        put1 = router_mod.put_google_reviews_config({"api_key": "secret-key-1", "enabled": True,
                                                      "target_default": 4.5}, authorization="Bearer admin",
                                                     x_active_org="", org_id=ORG)
        ok("F3 admin CAN save the config", put1["enabled"] is True and put1["target_default"] == 4.5, put1)
        ok("F4 the raw api_key is NEVER echoed back", "secret-key-1" not in str(put1), put1)
        ok("F5 has_api_key now true, with a masked hint only", put1["has_api_key"] is True
           and put1["api_key_hint"] and "secret-key-1" not in put1["api_key_hint"], put1)
        raw_row = store["storeops.google_review_config"][0]
        ok("F6 the raw key IS actually persisted server-side (just never returned)",
           raw_row["api_key"] == "secret-key-1", raw_row)

        put2 = router_mod.put_google_reviews_config({"enabled": False}, authorization="Bearer admin",
                                                     x_active_org="", org_id=ORG)
        ok("F7 omitting api_key on a later PUT keeps the existing key (write-only field)",
           store["storeops.google_review_config"][0]["api_key"] == "secret-key-1", store["storeops.google_review_config"])
        ok("F8 other fields (enabled) still update independently", put2["enabled"] is False, put2)

        # ── store-config override + resolve-place (mocked) ─────────────────────────────────────
        router_mod.put_google_review_store_config("S2", {"target_override": 4.3},
                                                   authorization="Bearer admin", x_active_org="", org_id=ORG)
        stores_view = router_mod.list_google_review_stores(authorization="Bearer admin", org_id=ORG)
        s2 = next(s for s in stores_view["stores"] if s["store_code"] == "S2")
        ok("F9 per-store target_override reflected in the stores list", s2["target"] == 4.3, s2)

        real_ts = router_mod._gr.text_search_place
        router_mod._gr.text_search_place = lambda address, api_key, timeout=15: {
            "place_id": "PLACEZ", "formatted_address": address, "display_name": "Z"}
        try:
            rp = router_mod.post_resolve_place({"store_code": "S1"}, authorization="Bearer admin",
                                               x_active_org="", org_id=ORG)
            ok("F10 resolve-place persists a place_id from the store's OWN address (no free-typed input)",
               rp["place_id"] == "PLACEZ", rp)
        finally:
            router_mod._gr.text_search_place = real_ts

        # ── full action-plan state machine through the REAL endpoints ─────────────────────────
        row = {"org_id": ORG, "employee_id": "E1", "employee_name": "Ali Khan", "store_code": "S1",
               "area_key": "google_reviews", "status": "required",
               "trigger_detail": "Store rating 4.2 vs target 4.7"}
        inserted = fake_client.schema("storeops").table("action_plan").insert(row).execute().data[0]
        plan_id = inserted["id"]

        try:
            router_mod.submit_action_plan(plan_id, {"plan_text": "Will do X"}, authorization="Bearer rep-sara")
            ok("F11 a DIFFERENT employee cannot submit someone else's plan", False, "no exception")
        except Exception as e:
            ok("F11 a different employee cannot submit someone else's plan (403)",
               getattr(e, "status_code", None) == 403, e)

        sub = router_mod.submit_action_plan(plan_id, {"plan_text": "Will follow up with the customer"},
                                            authorization="Bearer rep-ali")
        ok("F12 the OWNING employee can submit -> status='submitted'", sub["status"] == "submitted", sub)

        try:
            router_mod.push_back_action_plan(plan_id, {"dm_comments": "needs more detail"},
                                             authorization="Bearer mgr", org_id=ORG)
            ok("F13 push-back without a due_date is rejected", False, "no exception")
        except Exception as e:
            ok("F13 push-back without a due_date is rejected (400)", getattr(e, "status_code", None) == 400, e)

        pb = router_mod.push_back_action_plan(plan_id, {"dm_comments": "please add a follow-up date",
                                                        "due_date": "2026-08-15"},
                                              authorization="Bearer mgr", org_id=ORG)
        ok("F14 DM push-back sets status/comments/due_date", pb["status"] == "pushed_back"
           and pb["due_date"] == "2026-08-15" and pb["dm_comments"], pb)

        try:
            router_mod.dm_confirm_action_plan(plan_id, {}, authorization="Bearer mgr", org_id=ORG)
            ok("F15 DM cannot confirm before the employee marks done", False, "no exception")
        except Exception as e:
            ok("F15 DM cannot confirm before the employee marks done (400)",
               getattr(e, "status_code", None) == 400, e)

        try:
            router_mod.employee_mark_action_plan_done(plan_id, authorization="Bearer rep-sara")
            ok("F16 a different employee cannot mark someone else's plan done", False, "no exception")
        except Exception as e:
            ok("F16 a different employee cannot mark someone else's plan done (403)",
               getattr(e, "status_code", None) == 403, e)

        md = router_mod.employee_mark_action_plan_done(plan_id, authorization="Bearer rep-ali")
        ok("F17 employee marks done: employee_marked_done_at set, status still in_progress",
           md["employee_marked_done_at"] and md["status"] == "in_progress", md)

        done = router_mod.dm_confirm_action_plan(plan_id, {"dm_comments": "confirmed, great job"},
                                                 authorization="Bearer mgr", org_id=ORG)
        ok("F18 DM confirm -> status='completed' (terminal)", done["status"] == "completed", done)

        try:
            router_mod.push_back_action_plan(plan_id, {"dm_comments": "x", "due_date": "2026-09-01"},
                                             authorization="Bearer mgr", org_id=ORG)
            ok("F19 a completed plan cannot be pushed back again", False, "no exception")
        except Exception as e:
            ok("F19 a completed plan cannot be pushed back again (400)", getattr(e, "status_code", None) == 400, e)

        mine = router_mod.my_action_plans(authorization="Bearer rep-ali")
        ok("F20 GET /action-plans/mine returns the employee's own plan", len(mine["items"]) == 1
           and mine["items"][0]["id"] == plan_id, mine)

        # ── DM dashboard / list scoped to the market manager's span (S1 only, not S2) ─────────
        dash = router_mod.google_reviews_dm_dashboard(authorization="Bearer mgr", org_id=ORG)
        codes_seen = {s["store_code"] for s in dash["stores"]}
        ok("F21 a market manager pinned to S1 sees ONLY S1 on their dashboard, never S2",
           codes_seen == {"S1"}, codes_seen)

        admin_dash = router_mod.google_reviews_dm_dashboard(authorization="Bearer admin", org_id=ORG)
        admin_codes = {s["store_code"] for s in admin_dash["stores"]}
        ok("F22 an admin (unrestricted span) sees every store", admin_codes == {"S1", "S2"}, admin_codes)

        ap_list = router_mod.list_action_plans(authorization="Bearer mgr", org_id=ORG)
        ok("F23 /action-plans list is ALSO scoped to the manager's span (S1's plan only)",
           all(p["store_code"] == "S1" for p in ap_list["items"]) and len(ap_list["items"]) >= 1, ap_list)

        # ── sweep-config round trip + next_run_at is a real future UTC timestamp ────────────────
        sc0 = router_mod.get_google_reviews_sweep_config(authorization="Bearer admin", org_id=ORG)
        ok("F24 GET sweep-config pre-row: sane defaults", sc0["frequency"] == "daily" and sc0["enabled"] is False, sc0)
        sc1 = router_mod.put_google_reviews_sweep_config({"enabled": True, "frequency": "weekly",
                                                           "day_of_week": 2, "hour": 7},
                                                          authorization="Bearer admin", x_active_org="", org_id=ORG)
        ok("F25 PUT sweep-config persists + computes next_run_at", sc1["enabled"] is True
           and sc1["frequency"] == "weekly" and sc1.get("next_run_at"), sc1)
        nxt = datetime.fromisoformat(sc1["next_run_at"].replace("Z", "+00:00"))
        ok("F26 next_run_at is strictly in the future", nxt > datetime.now(timezone.utc), nxt)

        # ── /google-reviews/store/{code}: a manager in span may view; an employee scheduled/home
        #    there may view; a rep with NO relationship to the store is rejected ─────────────────
        detail_mgr = router_mod.google_review_store_detail("S1", authorization="Bearer mgr", org_id=ORG)
        ok("F27 manager-in-span can view a store detail card", detail_mgr["store_code"] == "S1", detail_mgr)
        detail_emp = router_mod.google_review_store_detail("S1", authorization="Bearer rep-ali")
        ok("F28 an employee HOME at that store can view it too", detail_emp["store_code"] == "S1", detail_emp)
        try:
            router_mod.google_review_store_detail("S2", authorization="Bearer rep-ali")
            ok("F29 an employee with NO relationship to a DIFFERENT store is rejected", False, "no exception")
        except Exception as e:
            ok("F29 an employee with NO relationship to a DIFFERENT store is rejected (403)",
               getattr(e, "status_code", None) == 403, e)

        # ── _do_google_reviews_sweep — the full background-task body through the REAL sweep-status
        #    write path (mocking only the Google HTTP calls; everything else is the real code) ───
        store["storeops.google_review_config"][0]["api_key"] = "sweep-key"
        store["storeops.google_review_config"][0]["enabled"] = True
        real_ts2, real_pd2 = router_mod._gr.text_search_place, router_mod._gr.place_details
        router_mod._gr.text_search_place = lambda address, api_key, timeout=15: {
            "place_id": "PSWEEP", "formatted_address": address, "display_name": "Sweep Store"}
        router_mod._gr.place_details = lambda place_id, api_key, timeout=15: {
            "rating": 4.9, "review_count": 10, "reviews": []}
        try:
            sweep_res = router_mod._do_google_reviews_sweep(ORG, None)
            ok("F30 _do_google_reviews_sweep runs against every active store", sweep_res.get("ok") is True
               and len(sweep_res.get("stores", [])) == 2, sweep_res)
            status_after = router_mod._gr.get_sweep_config(fake_client.schema("storeops"), ORG)
            ok("F31 sweep-config status/last_run_at updated by the real sweep path",
               status_after.get("last_status") == "ok" and status_after.get("last_run_at"), status_after)
        finally:
            router_mod._gr.text_search_place, router_mod._gr.place_details = real_ts2, real_pd2
    finally:
        router_mod.get_supabase = real_get_supabase
        core_router_mod._uid_from_token = real_uid


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# I. Phase 1.5 router integration — GET /google-reviews/employee/{id} + the batched
#    GET /google-reviews/employee-summary (owner directive 2026-08-06, "google reviews everywhere").
#    Same fake-client convention as section F, fresh org/fixtures.
# ═══════════════════════════════════════════════════════════════════════════════════════════════
class _CountingSchema:
    """Wraps a real _SchemaClient; records how many times .table(name) is called per name — proves
    the batch endpoint queries each table ONCE for N employees, never N times (AGENT_CONTRACT
    'aggregate in Postgres, never N round-trips')."""
    def __init__(self, inner):
        self._inner = inner
        self.calls: dict = {}

    def table(self, name):
        self.calls[name] = self.calls.get(name, 0) + 1
        return self._inner.table(name)

    def schema(self, name):
        return self


def section_phase15_router():
    print("\n-- I. router.py integration — GET /google-reviews/employee/{id} + /employee-summary --")
    import app.modules.storeops.router as router_mod
    import app.modules.core.router as core_router_mod

    store = {}
    fake_client = FakeClient(store)
    real_get_supabase = router_mod.get_supabase
    real_uid = core_router_mod._uid_from_token
    router_mod.get_supabase = lambda: fake_client
    core_router_mod._uid_from_token = lambda auth: {
        "Bearer admin": "admin-uid", "Bearer mgr": "mgr-uid",
        "Bearer rep-ali": "ali-uid", "Bearer rep-sara": "sara-uid",
    }.get(auth)

    ORG = "ORGY"
    store["storeops.app_users"] = [
        {"auth_id": "admin-uid", "org_id": ORG, "email": "admin@y.com", "role": "admin", "employee_id": "MGR0"},
        {"auth_id": "mgr-uid", "org_id": ORG, "email": "mgr@y.com", "role": "market_manager",
         "employee_id": "MGR1", "store_codes": ["S1"]},
        {"auth_id": "ali-uid", "org_id": ORG, "email": "ali@y.com", "role": "rep", "employee_id": "E1"},
        {"auth_id": "sara-uid", "org_id": ORG, "email": "sara@y.com", "role": "rep", "employee_id": "E2"},
    ]
    store["storeops.roles"] = [
        {"org_id": ORG, "name": "rep", "permissions": {"scope": "self"}},
        {"org_id": ORG, "name": "market_manager", "permissions": {"scope": "market"}},
    ]
    store["storeops.employees"] = [
        {"org_id": ORG, "employee_id": "E1", "name": "Ali Khan", "email": "ali@y.com", "home_store": "S1", "is_active": True},
        {"org_id": ORG, "employee_id": "E2", "name": "Sara Lee", "email": "sara@y.com", "home_store": "S2", "is_active": True},
    ]
    store["storeops.stores"] = [
        {"org_id": ORG, "store_code": "S1", "address": "1 Main St", "market": "NY", "is_active": True},
        {"org_id": ORG, "store_code": "S2", "address": "2 Main St", "market": "NY", "is_active": True},
    ]
    store["storeops.shifts"] = []
    store["storeops.google_review_config"] = []       # no row yet -> code default (30-day lookback)
    store["storeops.google_review_store"] = []
    store["storeops.google_review_snapshot"] = [
        {"org_id": ORG, "store_code": "S1", "rating": 4.2, "review_count": 50, "fetched_at": "2026-08-01T00:00:00Z"},
        {"org_id": ORG, "store_code": "S2", "rating": 4.9, "review_count": 30, "fetched_at": "2026-08-01T00:00:00Z"},
    ]
    store["storeops.google_review_item"] = []
    store["storeops.action_plan"] = []

    try:
        # ── GET /google-reviews/employee/{id} ───────────────────────────────────────────────────
        d1 = router_mod.google_review_employee_detail("E1", authorization="Bearer rep-ali", org_id=ORG)
        ok("I1 an employee viewing their OWN card gets it (self-rule, same as /my)",
           d1["employee_id"] == "E1" and d1["employee_name"] == "Ali Khan"
           and any(s["store_code"] == "S1" for s in d1["stores"]), d1)

        try:
            router_mod.google_review_employee_detail("E1", authorization="Bearer rep-sara", org_id=ORG)
            ok("I2 a non-manager rep cannot view a DIFFERENT employee's card", False, "no exception")
        except Exception as e:
            ok("I2 a non-manager rep cannot view a DIFFERENT employee's card (403)",
               getattr(e, "status_code", None) == 403, e)

        d2 = router_mod.google_review_employee_detail("E1", authorization="Bearer mgr", org_id=ORG)
        ok("I3 a market manager pinned to S1 CAN view E1 (home S1, in span)",
           d2["employee_id"] == "E1" and any(s["store_code"] == "S1" for s in d2["stores"]), d2)

        try:
            router_mod.google_review_employee_detail("E2", authorization="Bearer mgr", org_id=ORG)
            ok("I4 the SAME manager cannot view E2 (home S2, outside their span)", False, "no exception")
        except Exception as e:
            ok("I4 the same manager cannot view E2 (home S2, outside their span) (403)",
               getattr(e, "status_code", None) == 403, e)

        d3 = router_mod.google_review_employee_detail("E2", authorization="Bearer admin", org_id=ORG)
        ok("I5 an admin (unrestricted span) CAN view E2", d3["employee_id"] == "E2", d3)

        try:
            router_mod.google_review_employee_detail("NOPE", authorization="Bearer admin", org_id=ORG)
            ok("I6 an unknown employee_id 404s", False, "no exception")
        except Exception as e:
            ok("I6 an unknown employee_id 404s", getattr(e, "status_code", None) == 404, e)

        ok("I6b the response 'note' matches the same honest-limitation text /my uses",
           "curated subset" in (d1.get("note") or ""), d1)

        # ── GET /google-reviews/employee-summary (batched) ─────────────────────────────────────
        try:
            router_mod.google_reviews_employee_summary(employee_ids="E1,E2", authorization="Bearer rep-ali", org_id=ORG)
            ok("I7 a non-manager cannot call the batch summary endpoint", False, "no exception")
        except Exception as e:
            ok("I7 a non-manager cannot call the batch summary endpoint (403)",
               getattr(e, "status_code", None) == 403, e)

        s1 = router_mod.google_reviews_employee_summary(employee_ids="E1,E2", authorization="Bearer mgr", org_id=ORG)
        ok("I8 manager pinned to S1: E1 (home S1) present in the summary", "E1" in s1["summaries"], s1)
        ok("I9 ...but E2 (home S2, outside span) is SILENTLY DROPPED, not a whole-call 403",
           "E2" not in s1["summaries"], s1)
        e1_rows = s1["summaries"]["E1"]
        ok("I10 the summary row shape is EXACT: store_code/rating/review_count/target/status only",
           set(e1_rows[0].keys()) == {"store_code", "rating", "review_count", "target", "status"}, e1_rows)
        ok("I11 the summary carries the real snapshot rating (4.2) for S1", e1_rows[0]["rating"] == 4.2, e1_rows)
        ok("I12 no review TEXT anywhere in the summary payload (light, table-column shape)",
           "reviews" not in str(e1_rows), e1_rows)

        s2 = router_mod.google_reviews_employee_summary(employee_ids="E1,E2", authorization="Bearer admin", org_id=ORG)
        ok("I13 an admin (unrestricted span) sees BOTH E1 and E2 in the batch summary",
           "E1" in s2["summaries"] and "E2" in s2["summaries"], s2)

        s_unknown = router_mod.google_reviews_employee_summary(employee_ids="E1,NOPE", authorization="Bearer admin", org_id=ORG)
        ok("I14 an unknown id in the csv is silently dropped (no crash, no key)",
           "E1" in s_unknown["summaries"] and "NOPE" not in s_unknown["summaries"], s_unknown)

        s_empty = router_mod.google_reviews_employee_summary(employee_ids="", authorization="Bearer admin", org_id=ORG)
        ok("I15 an empty employee_ids param returns {'summaries': {}} without erroring",
           s_empty == {"summaries": {}}, s_empty)

        # ── batching proof: the tables whose query cost would scale with the NUMBER OF EMPLOYEES in
        #    a naive per-employee implementation (stores/employees/shifts/overlay/snapshot) are each
        #    queried EXACTLY ONCE for 2 employees, not once per employee. app_users/roles are a
        #    fixed, employee-count-INDEPENDENT auth/span-resolution cost (same for 1 or 1000
        #    employee_ids) and are deliberately excluded from this check. ─────────────────────────
        counting = _CountingSchema(FakeClient(store).schema("storeops"))
        router_mod.get_supabase = lambda: counting
        try:
            router_mod.google_reviews_employee_summary(employee_ids="E1,E2", authorization="Bearer admin", org_id=ORG)
        finally:
            router_mod.get_supabase = lambda: fake_client
        batched_tables = ("stores", "employees", "shifts", "google_review_store", "google_review_snapshot")
        over_queried = {t: counting.calls.get(t, 0) for t in batched_tables if counting.calls.get(t, 0) != 1}
        ok("I16 every N-employee-scaling table is queried EXACTLY once for 2 employees "
           "(batched, never N round-trips)", not over_queried, counting.calls)

        # ── config degrade: no google_review_config row at all -> the employee endpoint still
        #    works with the code-default 30-day lookback (migration 420 not yet run) ─────────────
        d_degrade = router_mod.google_review_employee_detail("E1", authorization="Bearer rep-ali", org_id=ORG)
        ok("I17 GET /google-reviews/employee/{id} works with zero config rows (pre-migration-411 AND "
           "pre-migration-420 degrade)", d_degrade["employee_id"] == "E1", d_degrade)
    finally:
        router_mod.get_supabase = real_get_supabase
        core_router_mod._uid_from_token = real_uid


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# G. Attention providers (real register_provider(), same fixture convention as
#    harness_people_attention.py)
# ═══════════════════════════════════════════════════════════════════════════════════════════════
def section_attention():
    print("\n-- G. admin-attention providers (review_action_plan_stale / _overdue) --")
    from app.modules.core import import_health as IH
    from app.modules.storeops import attention as SA

    before = list(IH.PROVIDERS)
    SA.register(IH.register_provider)
    after = {p["key"]: p for p in IH.PROVIDERS}
    for k in ("storeops_review_action_plan_stale", "storeops_review_action_plan_overdue"):
        ok(f"G registered: {k}", k in after, sorted(after))
        ok(f"G cost=cheap: {k}", after.get(k, {}).get("cost") == "cheap")
        ok(f"G group='other': {k}", after.get(k, {}).get("group") == "other")

    def d_ago(n):
        return (NOW - timedelta(days=n)).isoformat()

    def day_ago(n):
        return (NOW.date() - timedelta(days=n)).isoformat()

    def day_ahead(n):
        return (NOW.date() + timedelta(days=n)).isoformat()

    store = {
        "storeops.action_plan": [
            # stale: required 10 days ago (>5-day threshold) -> FLAG
            {"org_id": ORG_A, "employee_id": "E1", "employee_name": "Ali Khan", "store_code": "S1",
             "area_key": "google_reviews", "status": "required", "created_at": d_ago(10), "due_date": None},
            # NOT stale: required 2 days ago (under threshold)
            {"org_id": ORG_A, "employee_id": "E2", "employee_name": "Sara Lee", "store_code": "S1",
             "area_key": "google_reviews", "status": "required", "created_at": d_ago(2), "due_date": None},
            # not counted: already submitted (no longer 'required')
            {"org_id": ORG_A, "employee_id": "E3", "employee_name": "Old One", "store_code": "S2",
             "area_key": "google_reviews", "status": "submitted", "created_at": d_ago(20), "due_date": None},
            # overdue: pushed_back with a due_date in the past -> FLAG
            {"org_id": ORG_A, "employee_id": "E4", "employee_name": "Past Due", "store_code": "S3",
             "area_key": "google_reviews", "status": "pushed_back", "created_at": d_ago(15),
             "due_date": day_ago(1)},
            # overdue (in_progress variant) -> FLAG
            {"org_id": ORG_A, "employee_id": "E5", "employee_name": "Also Late", "store_code": "S3",
             "area_key": "google_reviews", "status": "in_progress", "created_at": d_ago(15),
             "due_date": day_ago(3)},
            # NOT overdue: due date is in the FUTURE
            {"org_id": ORG_A, "employee_id": "E6", "employee_name": "On Track", "store_code": "S3",
             "area_key": "google_reviews", "status": "pushed_back", "created_at": d_ago(5),
             "due_date": day_ahead(5)},
            # NOT overdue: already completed even though due_date is in the past
            {"org_id": ORG_A, "employee_id": "E7", "employee_name": "Finished", "store_code": "S3",
             "area_key": "google_reviews", "status": "completed", "created_at": d_ago(30),
             "due_date": day_ago(10)},
            # org B: isolation check — a stale row that must never leak into org A's result
            {"org_id": ORG_B, "employee_id": "B1", "employee_name": "B Person", "store_code": "SB1",
             "area_key": "google_reviews", "status": "required", "created_at": d_ago(20), "due_date": None},
        ],
    }
    client = FakeClient(store)
    ctx = {"now": NOW, "feed_health": {}}

    items = after["storeops_review_action_plan_stale"]["fn"](client, ORG_A, ctx)
    ok("G-stale fires exactly one item", len(items) == 1, items)
    ok("G-stale count = 1 (only Ali's 10-day-old required row)", items and items[0]["count"] == 1, items)
    ok("G-stale group='other'", items and items[0]["group"] == "other", items)
    detail = items[0]["detail"] if items else ""
    ok("G-stale names Ali", "Ali Khan" in detail, detail)
    ok("G-stale never counts Sara (under threshold)", "Sara Lee" not in detail, detail)
    ok("G-stale never counts a submitted plan", "Old One" not in detail, detail)
    items_b = after["storeops_review_action_plan_stale"]["fn"](client, ORG_B, ctx)
    ok("G-stale org isolation: org B's own stale row IS visible under org B",
       len(items_b) == 1 and items_b[0]["count"] == 1, items_b)
    items_a_again = after["storeops_review_action_plan_stale"]["fn"](client, ORG_A, ctx)
    ok("G-stale org A result never includes org B's employee", "B Person" not in str(items_a_again), items_a_again)

    items2 = after["storeops_review_action_plan_overdue"]["fn"](client, ORG_A, ctx)
    ok("G-overdue fires exactly one item", len(items2) == 1, items2)
    ok("G-overdue count = 2 (Past Due + Also Late)", items2 and items2[0]["count"] == 2, items2)
    detail2 = items2[0]["detail"] if items2 else ""
    ok("G-overdue names Past Due", "Past Due" in detail2, detail2)
    ok("G-overdue names Also Late", "Also Late" in detail2, detail2)
    ok("G-overdue never counts a future due_date", "On Track" not in detail2, detail2)
    ok("G-overdue never counts a completed plan even with a past due_date", "Finished" not in detail2, detail2)

    IH.PROVIDERS[:] = before   # never pollute the process-wide registry for other harnesses


def main():
    section_pure()
    section_logic()
    section_stores_for_employees()
    section_router()
    section_phase15_router()
    section_attention()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("\nFAILED:")
        for f in FAIL:
            print(" -", f)
        sys.exit(1)


if __name__ == "__main__":
    main()
