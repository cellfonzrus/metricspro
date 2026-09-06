"""Offline proof harness for the 2026-07-26 OWNER DIRECTIVE settings + imports audit
(mod-retail-ops: closing/** + storevisit/**). No live DB/network — same convention as
harness_tech_support.py / harness_closer_chargebacks.py: runs the REAL functions against a
stateful fake Supabase client.

Run: `cd backend && python3 harness_settings_audit.py`

Proves:
  A. THE SECURITY FIX — `_can_mgmt_review`/`_can_edit_closing_setting` (closing/router.py) used to
     default-ALLOW an unauthenticated/unresolved caller (perms == {}); `__resolved` closes that.
     Same fix mirrored in storevisit's local `_can_edit_visit_setting`.
  B. Every closing settings write endpoint that was UNGATED before this package now enforces the
     'closing' settings area (explicit override wins, else company-wide-scope-only), while the
     intentionally DM-editable /closing/pickup-config is confirmed UNTOUCHED (still open — by
     design, not an oversight).
  C. storevisit's checklist-template + config writes (previously ZERO auth of any kind) now enforce
     the same gate.
  D. closing_stale_alert_days round-trips through GET/PUT /closing/cash-config, defaulting to 3.
  E. The three new closing attention providers (closing_readiness bridge, closing_sweep_credentials,
     closing_stale_stores) and storevisit's storevisit_checklist_template provider.
  F. DM store-visit WRITE-PATH code-path review (owner ask: this path was flagged e2e-untested) found
     FOUR real multi-tenant write-scoping gaps in storevisit/router.py (update_checklist_item /
     delete_checklist_item had NO org_id parameter at all; upload_photo's two store_visits updates
     had no .eq("org_id", ...)) — fixed; proven here with a same-id-different-org collision, the
     sharpest possible test of "did the WHERE clause actually scope it".
"""
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

# Anchor imports AND every source read below to THIS file's own directory, so the
# harness runs identically from backend/ and from the repo root (cf. 564c171f).
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"
_ID = {"n": 0}


def nid(pfx="id"):
    _ID["n"] += 1
    return f"{pfx}-{_ID['n']}"


# ── stateful fake supabase client (mirrors harness_tech_support.py's Q/FakeClient, + count="exact") ──
class Q:
    def __init__(self, store, table):
        self.s, self.t = store, table
        self.op, self.payload, self.on_conflict = "select", None, None
        self.filters = []
        self._count = None
        self._ilike = []

    def select(self, *a, count=None, **k):
        self.op = "select"; self._count = count; return self

    def insert(self, rows, **k): self.op = "insert"; self.payload = rows; return self
    def update(self, patch, **k): self.op = "update"; self.payload = patch; return self
    def upsert(self, rows, on_conflict=None, **k):
        self.op = "upsert"; self.payload = rows; self.on_conflict = on_conflict; return self
    def delete(self, **k): self.op = "delete"; return self
    def eq(self, c, v): self.filters.append((c, "eq", v)); return self
    def in_(self, c, v): self.filters.append((c, "in", list(v))); return self
    def gte(self, c, v): self.filters.append((c, "gte", v)); return self
    def lte(self, c, v): self.filters.append((c, "lte", v)); return self
    def ilike(self, c, v): self._ilike.append((c, str(v).strip("%").lower())); return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

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
            res = SimpleNamespace(data=matched)
            if self._count == "exact":
                res.count = len(matched)
            return res
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
        if self.op == "upsert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            keys = [k.strip() for k in (self.on_conflict or "").split(",") if k.strip()]
            out = []
            for r in payload:
                r = dict(r)
                existing = None
                if keys:
                    for er in rows:
                        if all(er.get(k) == r.get(k) for k in keys):
                            existing = er; break
                if existing:
                    existing.update(r); out.append(dict(existing))
                else:
                    r.setdefault("id", nid(self.t)); rows.append(r); out.append(dict(r))
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
    return {"app_users": [], "roles": [], "tenants": [{"org_id": HOUSE}],
            "tenant_modules": [{"org_id": HOUSE, "module_key": "closing", "is_enabled": True}],
            "store_mapping": [], "stores": [], "raw_sales": [], "daily_sales_feed": [],
            "pos_tender_summary": [], "daily_closing": [], "closing_tender_def": [],
            "closing_count_field_def": [], "closing_sweep_config": [], "closing_deposit_config": [],
            "cash_pickup_config": [], "store_closer": [], "alert_recipient": [],
            "checklist_items": [], "store_visit_config": []}


def membership(org, role, super_admin=False, auth_id="uid-1"):
    return {"id": nid("mem"), "auth_id": auth_id, "org_id": org, "role": role,
            "super_admin": super_admin}


def role_row(org, name, perms):
    return {"id": nid("role"), "org_id": org, "name": name, "permissions": perms}


import _harness_dbfree  # noqa: E402
import app.modules.core.router as core            # noqa: E402
import app.modules.closing.router as cr            # noqa: E402
import app.modules.closing.attention_providers as cap   # noqa: E402
import app.modules.storevisit.router as sv         # noqa: E402
import app.modules.storevisit.attention_providers as svp  # noqa: E402
from fastapi import HTTPException                  # noqa: E402


def _body(model, payload):
    """Build the endpoint's REAL Pydantic body model, exactly as FastAPI builds it from the JSON
    request. These handlers accepted a plain dict until they were migrated to typed bodies; passing
    a bare dict now dies on `payload.<field>`, so the harness has to call them the way the shipped
    app does or it proves nothing about the real contract. LaxModel ignores unknown keys, so this is
    byte-for-byte what a real request produces."""
    return model(**payload)


def wire(store):
    fake = FakeClient(store)
    core.get_supabase = lambda: fake
    cr.sb = lambda: fake
    sv.sb = lambda: fake       # storevisit's sb() is schema-bound in real code; FakeClient.schema is a no-op anyway
    # DB-FREE GUARD: the bindings above cover only core/cr/sv. Caller RESOLUTION goes through
    # closing.router._caller_perms -> tenant_middleware.caller_app_user, which imports the client
    # factory INSIDE the function body and so never saw them — it was reaching the REAL production
    # database, failing, and denying every caller. That is why each "ALLOWED" check below used to
    # fail while every "DENIED" check passed, and why put_tender_config 403'd mid-harness.
    _harness_dbfree.install(fake)
    return fake


AUTH_NONE = ""
AUTH_BAD = "Bearer bad-token"
AUTH_GOOD = "Bearer good-token"


def as_dm(store):
    """Wire a resolved caller with a market-scope role, no settings override."""
    wire(store)
    store["app_users"] = [membership(HOUSE, "market_manager")]
    store["roles"] = [role_row(HOUSE, "market_manager", {"scope": "market"})]
    core._uid_from_token = lambda a: ("uid-1" if a == AUTH_GOOD else None)


def as_company_wide(store):
    wire(store)
    store["app_users"] = [membership(HOUSE, "admin")]
    store["roles"] = [role_row(HOUSE, "admin", {"scope": "all"})]
    core._uid_from_token = lambda a: ("uid-1" if a == AUTH_GOOD else None)


def as_super_admin(store):
    wire(store)
    store["app_users"] = [membership(HOUSE, "admin", super_admin=True)]
    store["roles"] = [role_row(HOUSE, "admin", {"scope": "store", "settings": {"closing": False}})]
    core._uid_from_token = lambda a: ("uid-1" if a == AUTH_GOOD else None)


def as_dm_with_override(store):
    wire(store)
    store["app_users"] = [membership(HOUSE, "market_manager")]
    store["roles"] = [role_row(HOUSE, "market_manager", {"scope": "market", "settings": {"closing": True}})]
    core._uid_from_token = lambda a: ("uid-1" if a == AUTH_GOOD else None)


def as_company_wide_denied(store):
    """Company-wide scope but an EXPLICIT settings.closing=False deny."""
    wire(store)
    store["app_users"] = [membership(HOUSE, "admin")]
    store["roles"] = [role_row(HOUSE, "admin", {"scope": "all", "settings": {"closing": False}})]
    core._uid_from_token = lambda a: ("uid-1" if a == AUTH_GOOD else None)


# ═══════════════════════════════════ A. THE SECURITY FIX ═══════════════════════════════════════════
st = fresh_store(); wire(st)
core._uid_from_token = lambda a: None   # no/bad token → _caller_perms returns {} (unresolved)
check("A1. unresolved caller → _can_mgmt_review DENIED",
      cr._can_mgmt_review(cr._caller_perms(cr.sb(), AUTH_NONE)) is False)
check("A2. unresolved caller → _can_edit_closing_setting DENIED",
      cr._can_edit_closing_setting(cr._caller_perms(cr.sb(), AUTH_BAD)) is False)

st = fresh_store(); as_company_wide(st)
check("A3. resolved + scope 'all', no override → ALLOWED",
      cr._can_edit_closing_setting(cr._caller_perms(cr.sb(), AUTH_GOOD)) is True)

st = fresh_store(); as_dm(st)
check("A4. resolved + scope 'market' (DM), no override → DENIED",
      cr._can_edit_closing_setting(cr._caller_perms(cr.sb(), AUTH_GOOD)) is False)

st = fresh_store(); as_dm_with_override(st)
check("A5. DM + explicit settings.closing=True → ALLOWED (override wins over scope)",
      cr._can_edit_closing_setting(cr._caller_perms(cr.sb(), AUTH_GOOD)) is True)

st = fresh_store(); as_company_wide_denied(st)
check("A6. company-wide + explicit settings.closing=False → DENIED (explicit deny wins)",
      cr._can_edit_closing_setting(cr._caller_perms(cr.sb(), AUTH_GOOD)) is False)

st = fresh_store(); as_super_admin(st)
check("A7. super_admin → ALLOWED even with an explicit settings.closing=False on the role",
      cr._can_edit_closing_setting(cr._caller_perms(cr.sb(), AUTH_GOOD)) is True)

# storevisit's local, self-contained mirror of the same fix
st = fresh_store(); wire(st)
core._uid_from_token = lambda a: None
check("A8. storevisit: unresolved caller → _can_edit_visit_setting DENIED",
      sv._can_edit_visit_setting(sv._caller_perms(AUTH_NONE)) is False)
st = fresh_store(); as_dm(st)
check("A9. storevisit: DM (market scope) → DENIED",
      sv._can_edit_visit_setting(sv._caller_perms(AUTH_GOOD)) is False)
st = fresh_store(); as_company_wide(st)
check("A10. storevisit: company-wide → ALLOWED",
      sv._can_edit_visit_setting(sv._caller_perms(AUTH_GOOD)) is True)


# ═══════════════════ B. CLOSING SETTINGS WRITE ENDPOINTS NOW ENFORCE THE GATE ═══════════════════════
def expect_403(fn, *a, **k):
    try:
        fn(*a, **k)
        return False
    except HTTPException as e:
        return e.status_code == 403


st = fresh_store(); as_dm(st)
check("B1. put_tender_config: DM → 403",
      expect_403(cr.put_tender_config, {"defs": []}, org_id=HOUSE, authorization=AUTH_GOOD))
st = fresh_store(); as_company_wide(st)
r = cr.put_tender_config(_body(cr.PutTenderConfigIn, {"defs": []}), org_id=HOUSE, authorization=AUTH_GOOD)
check("B2. put_tender_config: company-wide → succeeds", r.get("ok") is True)

st = fresh_store(); as_dm(st)
check("B3. seed_standard_tenders: DM → 403",
      expect_403(cr.seed_standard_tenders, org_id=HOUSE, authorization=AUTH_GOOD))
st = fresh_store(); as_dm(st)
check("B4. put_count_config: DM → 403",
      expect_403(cr.put_count_config, {"defs": []}, org_id=HOUSE, authorization=AUTH_GOOD))
st = fresh_store(); as_dm(st)
check("B5. seed_standard_counts: DM → 403",
      expect_403(cr.seed_standard_counts, org_id=HOUSE, authorization=AUTH_GOOD))

st = fresh_store(); as_dm(st)
check("B6. put_deposit_config: DM → 403",
      expect_403(cr.put_deposit_config, {"match_target": "total_cash"}, org_id=HOUSE, authorization=AUTH_GOOD))
st = fresh_store(); wire(st)  # completely unauthenticated — the exact ORIGINAL gap this was flagged for
check("B7. put_deposit_config: NO auth header at all → 403 (was fully open before this package)",
      expect_403(cr.put_deposit_config, {"match_target": "total_cash"}, org_id=HOUSE, authorization=AUTH_NONE))
st = fresh_store(); as_company_wide(st)
r = cr.put_deposit_config(_body(cr.PutDepositConfigIn, {"match_target": "store_cash"}), org_id=HOUSE, authorization=AUTH_GOOD)
check("B8. put_deposit_config: company-wide → succeeds", r.get("match_target") == "store_cash")

st = fresh_store(); as_dm(st)
check("B9. put_cash_config: DM → 403",
      expect_403(cr.put_cash_config, {"closing_gate_enabled": True}, org_id=HOUSE, authorization=AUTH_GOOD))
st = fresh_store(); as_dm(st)
check("B10. set_store_closer: DM → 403",
      expect_403(cr.set_store_closer, {"store_code": "S1", "employee_id": "e1"}, org_id=HOUSE, authorization=AUTH_GOOD))
st = fresh_store(); as_dm(st)
check("B11. upsert_alert_recipient: DM → 403",
      expect_403(cr.upsert_alert_recipient, {"scope": "all", "email": "x@y.com"}, org_id=HOUSE, authorization=AUTH_GOOD))
st = fresh_store(); as_company_wide(st)
rec = cr.upsert_alert_recipient(_body(cr.UpsertAlertRecipientIn, {"scope": "all", "email": "x@y.com"}), org_id=HOUSE, authorization=AUTH_GOOD)
rid = rec.get("id")
st_recipients_before = len(st["alert_recipient"])
check("B12a. upsert_alert_recipient: company-wide → succeeds", rec.get("ok") is True and rid)
as_dm(st)
check("B12b. delete_alert_recipient: DM → 403",
      expect_403(cr.delete_alert_recipient, rid, org_id=HOUSE, authorization=AUTH_GOOD))
as_company_wide(st)
d = cr.delete_alert_recipient(rid, org_id=HOUSE, authorization=AUTH_GOOD)
check("B12c. delete_alert_recipient: company-wide → succeeds", d.get("ok") is True)

st = fresh_store(); as_dm(st)
check("B13. closing_sweep_put_config: DM → 403",
      expect_403(cr.closing_sweep_put_config, {"sheet_id": "abc"}, org_id=HOUSE, authorization=AUTH_GOOD))
st = fresh_store(); as_company_wide(st)
r = cr.closing_sweep_put_config(_body(cr.ClosingSweepPutConfigIn, {"sheet_id": "abc123"}), org_id=HOUSE, authorization=AUTH_GOOD)
check("B14. closing_sweep_put_config: company-wide → succeeds", r.get("sheet_id") == "abc123")

# Confirm pickup-config was INTENTIONALLY left ungated (nav scope ['all','market'] — DM-editable by
# design; see rbac.ts). Calling it with a DM identity and no `authorization` param at all must still
# work exactly as before this package (byte-identical signature/behaviour).
st = fresh_store(); as_dm(st)
r = cr.put_pickup_config(_body(cr.PutPickupConfigIn, {"recipient_email": "dm@x.com"}), org_id=HOUSE)
check("B15. put_pickup_config UNCHANGED — still callable with no auth gate (by design)",
      r.get("recipient_email") == "dm@x.com")


# ═══════════════════════ C. STOREVISIT: PREVIOUSLY ZERO-AUTH ENDPOINTS NOW GATED ═══════════════════
st = fresh_store(); wire(st)   # totally unauthenticated
check("C1. put_storevisit_config: no auth at all → 403 (was fully open before)",
      expect_403(sv.put_storevisit_config, {"accessory_order_url": "https://evil.example"}, org_id=HOUSE, authorization=AUTH_NONE))
st = fresh_store(); as_dm(st)
check("C2. put_storevisit_config: DM → 403",
      expect_403(sv.put_storevisit_config, {"accessory_order_url": "https://x.example"}, org_id=HOUSE, authorization=AUTH_GOOD))
st = fresh_store(); as_company_wide(st)
r = sv.put_storevisit_config(_body(sv.PutStorevisitConfigIn, {"accessory_order_url": "https://x.example", "accessory_order_label": "Order"}), org_id=HOUSE, authorization=AUTH_GOOD)
check("C3. put_storevisit_config: company-wide → succeeds", r.get("accessory_order_url") == "https://x.example")

st = fresh_store(); wire(st)
check("C4. create_checklist_item: no auth at all → 403 (was fully open before)",
      expect_403(sv.create_checklist_item, {"label": "Broom"}, org_id=HOUSE, authorization=AUTH_NONE))
st = fresh_store(); as_dm(st)
check("C5. create_checklist_item: DM → 403",
      expect_403(sv.create_checklist_item, {"label": "Broom"}, org_id=HOUSE, authorization=AUTH_GOOD))
st = fresh_store(); as_company_wide(st)
item = sv.create_checklist_item(_body(sv.ChecklistItemIn, {"label": "Broom", "item_key": "broom"}), org_id=HOUSE, authorization=AUTH_GOOD)
check("C6. create_checklist_item: company-wide → succeeds", item.get("label") == "Broom")
as_dm(st)
check("C7. update_checklist_item: DM → 403",
      expect_403(sv.update_checklist_item, item["id"], {"label": "Mop"}, authorization=AUTH_GOOD))
as_dm(st)
check("C8. delete_checklist_item: DM → 403",
      expect_403(sv.delete_checklist_item, item["id"], authorization=AUTH_GOOD))
as_company_wide(st)
dl = sv.delete_checklist_item(item["id"], authorization=AUTH_GOOD)
check("C9. delete_checklist_item: company-wide → succeeds (soft-delete)", dl.get("deactivated") == item["id"])


# ═════════════════════════ D. closing_stale_alert_days round-trips (mig 505) ═════════════════════════
st = fresh_store(); as_company_wide(st)
g = cr.get_cash_config(HOUSE)
check("D1. get_cash_config defaults closing_stale_alert_days to 3 (no column/row yet)",
      g.get("closing_stale_alert_days") == 3)
r = cr.put_cash_config(_body(cr.PutCashConfigIn, {"closing_stale_alert_days": 5}), org_id=HOUSE, authorization=AUTH_GOOD)
check("D2. put_cash_config persists closing_stale_alert_days", r.get("closing_stale_alert_days") == 5)
g2 = cr.get_cash_config(HOUSE)
check("D3. get_cash_config re-read reflects the saved value", g2.get("closing_stale_alert_days") == 5)
# migration-not-run degrade: the PUT for this one field must never break saving the OTHER (already-
# working) cash-config fields even if persisting the new column itself throws.
class _BoomOnStale(Q):
    def execute(self):
        if self.t == "tenants" and self.op == "update" and self.payload and "closing_stale_alert_days" in self.payload:
            raise RuntimeError("column closing_stale_alert_days does not exist")
        return super().execute()
st2 = fresh_store()
fake2 = FakeClient(st2)
fake2.table = lambda name: _BoomOnStale(st2, name)
as_company_wide(st2)
cr.sb = lambda: fake2
r3 = cr.put_cash_config(_body(cr.PutCashConfigIn, {"closing_deadline": "21:00", "closing_stale_alert_days": 9}), org_id=HOUSE, authorization=AUTH_GOOD)
check("D4. put_cash_config degrades: closing_deadline still saved even if the new column throws",
      r3.get("closing_deadline") == "21:00")


# ═══════════════════════════ E. ATTENTION PROVIDERS ═════════════════════════════════════════════════
NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


def ctx():
    return {"now": NOW}


# E1-E5: closing_readiness bridge — real closing_readiness() against a controlled fake client
st = fresh_store(); wire(st)
items = cap._p_closing_readiness(cr.sb(), HOUSE, ctx())
check("E1. readiness bridge: empty tenant → no_stores CRITICAL surfaces as severity=error",
      any(i["key"] == "closing_readiness:no_stores" and i["severity"] == "error" for i in items))
check("E2. readiness bridge: no_sales_source CRITICAL surfaces as error",
      any(i["key"] == "closing_readiness:no_sales_source" and i["severity"] == "error" for i in items))
check("E3. readiness bridge: tender_config_default INFO surfaces as severity=info",
      any(i["key"] == "closing_readiness:tender_config_default" and i["severity"] == "info" for i in items))
check("E4. readiness bridge: every item deep-links to /closing/readiness",
      items and all(i["deep_link"] == "/closing/readiness" for i in items))
check("E4b. GATE-1 REWORK: readiness-bridge items carry group='other' (rendered by AdminAttention.tsx), never 'ops'",
      items and all(i["group"] == "other" for i in items))

st["store_mapping"] = [{"org_id": HOUSE, "store_code": "S1", "store_address": "1 Main"}]
st["raw_sales"] = [{"org_id": HOUSE, "store": "1 Main"}]
st["pos_tender_summary"] = [{"org_id": HOUSE}]
items2 = cap._p_closing_readiness(cr.sb(), HOUSE, ctx())
check("E5. readiness bridge: a fully-configured tenant surfaces no no_stores/no_sales_source/no_xreport",
      not any(i["key"] in ("closing_readiness:no_stores", "closing_readiness:no_sales_source",
                          "closing_readiness:no_xreport_ever") for i in items2))

# E6-E9: closing_sweep_credentials
st = fresh_store(); wire(st)
check("E6. sweep never configured (no sheet_id, disabled) → no item", cap._p_closing_sweep_credentials(cr.sb(), HOUSE, ctx()) == [])

st["closing_sweep_config"] = [{"org_id": HOUSE, "sheet_id": "abc", "enabled": True, "last_status": "ok"}]
import app.modules.closing.gsheet as gsheet
gsheet.sa_info = lambda: {"client_email": "svc@x.iam.gserviceaccount.com"}
items = cap._p_closing_sweep_credentials(cr.sb(), HOUSE, ctx())
check("E7. sweep configured, SA present, last_status=ok → no item", items == [])

st["closing_sweep_config"] = [{"org_id": HOUSE, "sheet_id": "abc", "enabled": True,
                               "last_status": "error", "last_detail": "sheet not shared"}]
items = cap._p_closing_sweep_credentials(cr.sb(), HOUSE, ctx())
check("E8. sweep configured, SA present, last_status=error → ERROR item mentioning the detail",
      len(items) == 1 and items[0]["severity"] == "error" and "sheet not shared" in items[0]["detail"])
check("E8b. GATE-1 REWORK: sweep-credentials items carry group='import' (rendered), unchanged by the rework",
      items and all(i["group"] == "import" for i in items))

gsheet.sa_info = lambda: None
items = cap._p_closing_sweep_credentials(cr.sb(), HOUSE, ctx())
check("E9. sheet configured but NO Google credentials on the server → ERROR item",
      any(i["key"] == "closing_sweep_no_sa" for i in items))
gsheet.sa_info = lambda: {"client_email": "svc@x.iam.gserviceaccount.com"}   # restore for later checks


# E10-E14: closing_stale_stores (monkeypatch _b2b_day so this tests the PROVIDER, not sales parsing)
_b2b_calls = []


def fake_b2b_day_selling(client, org_id, date):
    _b2b_calls.append(date)
    return {"has_data": True, "by_store": {"S1": {"total": 500.0}}, "by_rep": {}, "counts": {}}


def fake_b2b_day_none(client, org_id, date):
    _b2b_calls.append(date)
    return {"has_data": False, "by_store": {}, "by_rep": {}, "counts": {}}


st = fresh_store(); wire(st); as_company_wide(st)
cr._b2b_day = fake_b2b_day_selling
items = cap._p_closing_stale_stores(cr.sb(), HOUSE, ctx())
check("E10. store sold in-window, no daily_closing row at all → flagged",
      len(items) == 1 and items[0]["count"] == 1 and "S1" in items[0]["detail"])
check("E10b. GATE-1 REWORK: stale-stores item carries group='other', never 'ops'",
      items and all(i["group"] == "other" for i in items))

st["daily_closing"] = [{"org_id": HOUSE, "store_code": "S1",
                        "close_date": (NOW - timedelta(days=1)).date().isoformat()}]
items = cap._p_closing_stale_stores(cr.sb(), HOUSE, ctx())
check("E11. same store WITH a recent daily_closing row → not flagged", items == [])

st["daily_closing"] = []
st["tenants"] = [{"org_id": HOUSE, "closing_stale_alert_days": 0}]
items = cap._p_closing_stale_stores(cr.sb(), HOUSE, ctx())
check("E12. closing_stale_alert_days=0 → check disabled entirely", items == [])

st["tenants"] = [{"org_id": HOUSE, "closing_stale_alert_days": 999}]
_b2b_calls.clear()
cap._p_closing_stale_stores(cr.sb(), HOUSE, ctx())
check("E13. an absurd N is bounded to 14 calls (never an unbounded scan)", len(_b2b_calls) == 14)

st["tenants"] = [{"org_id": HOUSE}]     # migration 505 not run / no row → default 3
cr._b2b_day = fake_b2b_day_none
items = cap._p_closing_stale_stores(cr.sb(), HOUSE, ctx())
check("E14. no B2B data at all (has_data False everywhere) → nothing to compare, no false flag",
      items == [])


# E15-E18: storevisit_checklist_template
st = fresh_store(); wire(st)
items = svp._p_checklist_template(sv.sb(), HOUSE, ctx())
check("E15. zero checklist items + NO market-scope role at all → not flagged (no DMs, nothing to warn about)",
      items == [])

st["roles"] = [{"org_id": HOUSE, "name": "market_manager", "permissions": {"scope": "market"}}]
st["app_users"] = [{"org_id": HOUSE, "role": "market_manager", "id": "u1"}]
items = svp._p_checklist_template(sv.sb(), HOUSE, ctx())
check("E16. zero checklist items + a DM role WITH an assigned user → FLAGGED",
      len(items) == 1 and items[0]["deep_link"] == "/storeops/visits/settings")
check("E16b. GATE-1 REWORK: checklist-template item carries group='other', never 'ops'",
      items and all(i["group"] == "other" for i in items))

st["checklist_items"] = [{"org_id": HOUSE, "item_key": "uniform", "is_active": True}]
items = svp._p_checklist_template(sv.sb(), HOUSE, ctx())
check("E17. at least one active checklist item → not flagged", items == [])

st["checklist_items"] = []
st["app_users"] = []   # DM role exists but nobody is assigned it
items = svp._p_checklist_template(sv.sb(), HOUSE, ctx())
check("E18. DM role defined but zero app_users hold it → not flagged", items == [])


# ═════ E19-E22. GATE-1 REWORK GUARD — registry-level: frontend/src/components/AdminAttention.tsx ═════
# only renders GROUP_ORDER = ['import','mapping','duplicate','other'] in the modal body; any OTHER
# group value is counted in the "N needs attention" pill (counts.total) but never shown in a row —
# the pill and the visible list would disagree. Checking every item dict above (E4b/E8b/E10b/E16b)
# proves the RUNTIME output; this checks the REGISTRY itself so a future edit that reintroduces
# group="ops" on the decorator (even if some code path never emits an item to catch it live) fails
# loudly here instead of silently shipping.
RENDERED_GROUPS = {"import", "mapping", "duplicate", "other"}
from app.modules.core.import_health import PROVIDERS   # noqa: E402
_our_provider_keys = {"closing_readiness", "closing_sweep_credentials", "closing_stale_stores",
                      "storevisit_checklist_template"}
_ours = {p["key"]: p for p in PROVIDERS if p["key"] in _our_provider_keys}
check("E19. all 4 retail-ops providers are actually registered", _ours.keys() == _our_provider_keys)
check("E20. none of our registered providers use group='ops' (the Gate-1 defect)",
      all(p["group"] != "ops" for p in _ours.values()))
check("E21. every one of our registered providers uses a group AdminAttention.tsx renders",
      all(p["group"] in RENDERED_GROUPS for p in _ours.values()))
check("E22. the 3 'ops'→'other' reworked providers now say 'other'; sweep-credentials stays 'import'",
      _ours["closing_readiness"]["group"] == "other"
      and _ours["closing_stale_stores"]["group"] == "other"
      and _ours["storevisit_checklist_template"]["group"] == "other"
      and _ours["closing_sweep_credentials"]["group"] == "import")


# ═══════════ F. DM store-visit WRITE PATH — multi-tenant org-scoping fixes (code-path review) ═══════
TENANT_B = "bbbbbbbb-0000-0000-0000-000000000002"

st = fresh_store(); as_company_wide(st)
st["checklist_items"] = [
    {"id": "shared-1", "org_id": HOUSE, "label": "House label", "is_active": True},
    {"id": "shared-1", "org_id": TENANT_B, "label": "Tenant B label", "is_active": True},
]
sv.update_checklist_item("shared-1", _body(sv.UpdateChecklistItemIn, {"label": "HACKED"}), org_id=HOUSE, authorization=AUTH_GOOD)
house_row = next(r for r in st["checklist_items"] if r["org_id"] == HOUSE)
b_row = next(r for r in st["checklist_items"] if r["org_id"] == TENANT_B)
check("F1. update_checklist_item updates the caller's own-org row", house_row["label"] == "HACKED")
check("F2. update_checklist_item is org-scoped — same-id row in ANOTHER org is untouched",
      b_row["label"] == "Tenant B label")

st = fresh_store(); as_company_wide(st)
st["checklist_items"] = [
    {"id": "shared-2", "org_id": HOUSE, "label": "House", "is_active": True},
    {"id": "shared-2", "org_id": TENANT_B, "label": "TenantB", "is_active": True},
]
sv.delete_checklist_item("shared-2", org_id=HOUSE, authorization=AUTH_GOOD)
house_row = next(r for r in st["checklist_items"] if r["org_id"] == HOUSE)
b_row = next(r for r in st["checklist_items"] if r["org_id"] == TENANT_B)
check("F3. delete_checklist_item soft-deletes the caller's own-org row", house_row["is_active"] is False)
check("F4. delete_checklist_item is org-scoped — same-id row in ANOTHER org stays active",
      b_row["is_active"] is True)


class _FakeUploadFile:
    def __init__(self, filename, content):
        self.filename = filename
        self.content_type = "image/jpeg"
        self._content = content

    async def read(self):
        return self._content


class _FakeStorageBucket:
    def __init__(self, files): self.files = files
    def upload(self, path, contents, opts=None): self.files[path] = contents
    def create_signed_url(self, path, ttl): return {"signedURL": f"https://signed/{path}"}


class _FakeStorage:
    def __init__(self): self.files = {}
    def get_bucket(self, name): raise RuntimeError("no bucket yet")   # forces the create_bucket no-op path
    def create_bucket(self, name): return None
    def from_(self, name): return _FakeStorageBucket(self.files)


class _FakeSupabaseWithStorage:
    def __init__(self, client, storage): self._client, self.storage = client, storage
    def schema(self, n): return self._client.schema(n)
    def table(self, n): return self._client.table(n)


st = fresh_store(); as_company_wide(st)
st["store_visits"] = [
    {"id": "visit-1", "org_id": HOUSE, "clean_store_photo_path": None},
    {"id": "visit-1", "org_id": TENANT_B, "clean_store_photo_path": None},
]
fake = wire(st)
sv.get_supabase = lambda: _FakeSupabaseWithStorage(fake, _FakeStorage())
asyncio.run(sv.upload_photo("visit-1", kind="clean_store",
                            file=_FakeUploadFile("a.jpg", b"binary-jpeg-bytes"), org_id=HOUSE))
house_row = next(r for r in st["store_visits"] if r["org_id"] == HOUSE)
b_row = next(r for r in st["store_visits"] if r["org_id"] == TENANT_B)
check("F5. upload_photo stamps the photo path on the caller's own-org visit",
      house_row["clean_store_photo_path"] is not None)
check("F6. upload_photo is org-scoped — same-id visit row in ANOTHER org is untouched",
      b_row["clean_store_photo_path"] is None)

# Sanity: the REST of the write path (create_visit / update_visit / submit_visit / action-items /
# action-plan / signoff) was ALREADY correctly org-scoped on every read/write — code-path review found
# no further gap. Exercise it once end-to-end so this isn't just a grep-based claim.
v = sv.create_visit(_body(sv.CreateVisitIn, {"store_code": "S1", "market": "NY", "dm_email": "dm@x.com"}), org_id=HOUSE)
vid = v["id"]
sv.update_visit(vid, _body(sv.UpdateVisitIn, {"responses": [{"item_key": "uniform", "checked": True}],
                     "accessories": [{"accessory_name": "case", "qty": 2}]}), org_id=HOUSE)
got = sv.get_visit(vid, org_id=HOUSE)
check("F7. create_visit → update_visit (responses+accessories) → get_visit round-trips",
      len(got["responses"]) == 1 and got["responses"][0]["item_key"] == "uniform"
      and len(got["accessories"]) == 1 and got["accessories"][0]["qty"] == 2)
sv.submit_visit(vid, org_id=HOUSE)
check("F8. submit_visit marks the visit submitted",
      sv.get_visit(vid, org_id=HOUSE)["visit"]["status"] == "submitted")
sv.save_action_items(vid, _body(sv.SaveActionItemsIn, {"items": [{"item_key": "uniform", "severity": "warning", "discussed": True}]}), org_id=HOUSE)
sv.save_action_plan(vid, _body(sv.SaveActionPlanIn, {"plan": [{"description": "Fix uniform", "store_code": "S1"}]}), org_id=HOUSE)
sv.signoff(vid, _body(sv.SignoffIn, {"who": "dm", "name": "DM One", "signed": True}), org_id=HOUSE)
act = sv.get_visit_action(vid, org_id=HOUSE)
check("F9. action-items/action-plan/signoff round-trip through get_visit_action",
      len(act["items"]) == 1 and len(act["plan"]) == 1 and act["signoff"]["plan_dm_signed"] is True)
# A visit_id that belongs to a DIFFERENT org must never be reachable through get_visit.
try:
    sv.get_visit(vid, org_id=TENANT_B)
    check("F10. get_visit refuses a cross-org visit_id", False)
except HTTPException as e:
    check("F10. get_visit refuses a cross-org visit_id", e.status_code == 404)


# ── summary ──────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
