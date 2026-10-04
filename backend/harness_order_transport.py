#!/usr/bin/env python3
"""THE LOCK — a vendor's order route is declared ONCE, read by every caller, and never PLACES an
order unattended.

Owner 2026-10-03/04: the store-visit accessory list should reach the vendor's own store, which is a
Shopify store the owner runs.

CLAUDE.md, "A fix is a DESIGN fix": the instance is one vendor with an admin API; the CLASS is that
"how does an order reach this vendor" is a fact about the vendor and must have one home. Before
this, exactly one route existed (a scripted portal walk) and it was implied rather than declared,
so a second route could only arrive as a sibling branch at every call site.

WHAT FAILS THE BUILD
  A  the route resolves from CONFIG, with a safe default. No vendor, store or brand name in code
     (RULE TWO); an unreadable or invalid declaration resolves to `none` with a reason, never to a
     guess.
  B  a credential in a transport declaration is REFUSED, not silently stripped.
  C  the DIALECT is pure and addresses the right host: a custom domain is refused, a malformed API
     version is refused, and an unpriced line is carried rather than dropped.
  D  push_enabled's three separate facts — the tenant's switch, whether the route can reach the
     vendor, and whether using it PLACES the order. A route that places an order is refused for an
     unattended sweep however loudly it is switched on.
  E  the real `_push_accessory_pos` over a stub client: idempotent by external_ref, a failure is
     RECORDED on the PO, a dry run speaks to nobody.
  F  LOCKS — no second route resolver, no second dialect selection, no invoice-send call anywhere,
     the credential is never read from config, and migration 1053 is tied to the code. Each with an
     armed control.

Run: python3 backend/harness_order_transport.py     (stdlib only, no DB, no network)
"""
from __future__ import annotations

import asyncio
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(HERE, "app")
MIG = os.path.join(ROOT, "database", "migrations", "1053_vendor_order_transport.sql")
sys.path.insert(0, HERE)

from app.modules.supply import order_transport as OT          # noqa: E402
from app.modules.supply import shopify_draft_order as SH       # noqa: E402
from app.modules.supply import api_credential as CRED           # noqa: E402

passed, failed = 0, 0


def ok(cond, label):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}")
    return bool(cond)


def code_text(path):
    """Source with docstrings and comments removed, spacing preserved, so a rule written ABOUT a
    pattern is never mistaken for the pattern."""
    src = open(path, encoding="utf-8", errors="replace").read()
    src = re.sub(r'(?s)("""|\'\'\')(.*?)\1', lambda m: " " * (m.end() - m.start()), src)
    return re.sub(r"(?m)#[^\n]*", lambda m: " " * (m.end() - m.start()), src)


def py_files():
    out = []
    for d, _, fs in os.walk(APP):
        for f in fs:
            if f.endswith(".py"):
                out.append(os.path.join(d, f))
    return sorted(out)


API = {"dialect": "shopify_draft_order", "host": "example-store.myshopify.com", "version": "2026-07"}


# ── A — the route resolves from config, with a safe default ─────────────────────────────────────
print("\n§A  the route is declared, and the default is safe")
ok(OT.transport_for({})["kind"] == "none", "A1 a vendor with no config has NO route")
ok(OT.transport_for(None)["can_send"] is False, "A2 a missing vendor row cannot send")
ok(OT.transport_for({})["reason"], "A3 the default carries a reason, so a caller can say why")
v_api = {"portal_config": {"order_transport": {"kind": "api", "api": dict(API)}}}
r = OT.transport_for(v_api)
ok(r["kind"] == "api" and r["config"]["host"] == API["host"], "A4 a declared api route resolves")
ok(r["sends_on_its_own"] is False, "A5 an api route DRAFTS by default — it does not place an order")
v_portal = {"portal_config": {"ordering": {"cart_url": "https://x/cart", "line_steps": [{"a": 1}]}}}
ok(OT.transport_for(v_portal)["kind"] == "portal",
   "A6 an existing ordering recipe IS a portal route — derived, never restated")
ok(OT.transport_for({"portal_config": {"order_transport": {"kind": "portal"}}})["kind"] == "none",
   "A7 a portal route declared with no recipe behind it resolves to none, not to a half-route")
ok(OT.transport_for({"portal_config": {"order_transport": {"kind": "telepathy"}}})["kind"] == "none",
   "A8 an unknown kind resolves to none")
bad_dialect = {"portal_config": {"order_transport": {"kind": "api", "api": dict(API, dialect="smoke")}}}
ok(OT.transport_for(bad_dialect)["kind"] == "none",
   "A9 a dialect this platform does not speak is NO route, never a half-send")
ok(OT.transport_for({"portal_config": {"order_transport": {"kind": "api",
                                                           "api": {"dialect": API["dialect"],
                                                                   "host": API["host"]}}}})["kind"]
   == "none", "A10 an api route with no declared version is refused — a default version moves")
explicit = {"portal_config": {"order_transport": {"kind": "api", "api": dict(API, places_order=True)}}}
ok(OT.transport_for(explicit)["sends_on_its_own"] is True,
   "A11 a route that PLACES the order must say so explicitly, and then says so")


# ── B — a credential is refused, not stripped ───────────────────────────────────────────────────
print("\n§B  a credential never lives in config")
for key in ("access_token", "api_token", "password", "secret", "authorization"):
    errs = OT.validate_transport({"kind": "api", "api": dict(API, **{key: "x"})})
    ok(any("credential" in e for e in errs), f"B1 {key} in a declaration is an ERROR")
nested = {"kind": "api", "api": dict(API, headers={"X-Shopify-Access-Token": "shpat_x"})}
ok(any("credential" in e for e in OT.validate_transport(nested)),
   "B2 a credential nested in a headers block is found too")
ok(OT.transport_for({"portal_config": {"order_transport": nested}})["kind"] == "none",
   "B3 a declaration carrying a credential yields NO route")
ok(OT.validate_transport({"kind": "api", "api": dict(API)}) == [],
   "B4 a clean declaration validates")
ok(OT.validate_transport(None) == [] and OT.validate_transport({}) == [],
   "B5 absent is legal and means 'none'")


# ── C — the dialect is pure and addresses the right host ────────────────────────────────────────
print("\n§C  the dialect")
ok(SH.endpoint(API).startswith(f"https://{API['host']}/admin/api/{API['version']}/"),
   "C1 the endpoint is built from the declared host and version")
ok(any("permanent" in e for e in SH.validate_target(dict(API, host="shop.example.com"))),
   "C2 a pretty custom domain is refused — the token is issued against the permanent one")
ok(any("permanent" in e for e in SH.validate_target(dict(API, host="evil.com/x.myshopify.com"))),
   "C3 a host that merely CONTAINS the permanent suffix is refused")
for bad in ("2026-7", "2026-05", "v1", "latest"):
    ok(SH.validate_target(dict(API, version=bad)) != [], f"C4 version {bad!r} is refused")
try:
    SH.endpoint(dict(API, host="shop.example.com"))
    ok(False, "C5 endpoint() raises on a target that did not validate")
except ValueError:
    ok(True, "C5 endpoint() raises on a target that did not validate")
body = SH.draft_order_payload({"po_number": "PO-1042"},
                              [{"name": "Case", "qty": 10, "unit_cost": 3.5},
                               {"name": "Glass", "qty": 5, "unit_cost": None},
                               {"name": "Nothing", "qty": 0, "unit_cost": 1}])["draft_order"]
ok(len(body["line_items"]) == 2, "C6 a zero-quantity line is not ordered")
ok(body["line_items"][0]["price"] == "3.50", "C7 money is sent as a fixed 2-decimal string")
ok("price to confirm" in body["line_items"][1]["title"] and body["line_items"][1]["price"] == "0.00",
   "C8 an UNPRICED line is carried and SAID so — never dropped, which would under-order")
ok(body["tags"] == "metricspro-po-po-1042", "C9 the basket is tagged with our PO number")
ok(body["use_customer_default_address"] is False, "C10 no customer address is assumed")
res = SH.read_result({"draft_order": {"id": 123, "invoice_url": "https://x/i", "name": "#D1",
                                      "line_items": [{"title": "secret"}]}})
ok(res["ok"] and res["external_ref"] == "123" and res["external_url"] == "https://x/i",
   "C11 the result keeps the reference and the URL")
ok("line_items" not in res, "C12 and keeps nothing else — the vendor's basket is not our data")
ok(SH.read_result({})["ok"] is False, "C13 an empty response is not a success")


# ── D — may a sweep use this route? ─────────────────────────────────────────────────────────────
print("\n§D  the three separate facts behind 'may a sweep send this'")
route_api = OT.transport_for(v_api)
ok(OT.push_enabled(route_api, False)[0] is False, "D1 the tenant's switch OFF refuses")
ok(OT.push_enabled(route_api, True)[0] is True, "D2 switched on, a draft route is allowed")
ok(OT.push_enabled(OT.transport_for({}), True)[0] is False, "D3 no route refuses")
places = OT.transport_for(explicit)
allowed, why = OT.push_enabled(places, True)
ok(allowed is False and "PLACES" in why,
   "D4 a route that PLACES an order is refused unattended HOWEVER loudly it is switched on")
ok(OT.push_enabled(None, True)[0] is False, "D5 a missing route refuses")
ok(all(isinstance(OT.push_enabled(r_, True)[1], str) and OT.push_enabled(r_, True)[1]
       for r_ in (route_api, places, OT.transport_for({}))),
   "D6 every answer carries a reason a human can read")


# ── E — the real _push_accessory_pos over a stub client ─────────────────────────────────────────
print("\n§E  the real push step")


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.f, self.patch = db, table, {}, None

    def select(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    def eq(self, k, v):
        self.f[k] = v
        return self

    def update(self, patch):
        self.patch = patch
        return self

    def execute(self):
        if self.patch is not None:
            for row in self.db.rows(self.table):
                if all(str(row.get(k)) == str(v) for k, v in self.f.items()):
                    row.update(self.patch)
                    self.db.updates.append((self.table, dict(self.patch)))
            return type("R", (), {"data": []})()
        out = [r for r in self.db.rows(self.table)
               if all(str(r.get(k)) == str(v) for k, v in self.f.items())]
        return type("R", (), {"data": [dict(r) for r in out]})()


class _Schema:
    def __init__(self, db):
        self.db = db

    def table(self, name):
        return _Q(self.db, name)


class FakeClient:
    def __init__(self, po_rows, line_rows):
        self.data = {"purchase_order": po_rows, "purchase_order_line": line_rows}
        self.updates = []

    def rows(self, t):
        return self.data.setdefault(t, [])

    def schema(self, _n):
        return _Schema(self)

    def table(self, name):
        return _Q(self, name)


ORG = "00000000-0000-0000-0000-000000000001"
VENDOR = {"id": "v1", "name": "A Vendor", "data_source_id": "src1",
          "portal_config": {"order_transport": {"kind": "api", "api": dict(API)}}}


def _run(cfg, po_rows, created, *, vendor=VENDOR, token="shpat_test", push_result=None):
    """Drive the REAL _push_accessory_pos with the client, vendor, credential and network stubbed."""
    import app.modules.storevisit.router as R
    from app.modules.supply import store as SS
    client = FakeClient(po_rows, [{"org_id": ORG, "po_id": "po1", "line_no": 1,
                                   "device_model": "Case", "qty_ordered": 4, "unit_cost": 2.5}])
    calls = []

    async def fake_push(cfg_, token_, po_, lines_, **kw):
        calls.append({"cfg": cfg_, "token": token_, "po": po_, "lines": lines_})
        return push_result or {"ok": True, "external_ref": "D1", "external_url": "https://x/i",
                               "external_name": "#D1", "error": ""}

    import app.modules.supply.api_credential as CR

    async def fake_token(_c, _o, login, host, **kw):
        calls.append({"minted_for": host})
        return ({"token": token, "error": "", "minted": True} if token
                else {"token": "", "error": "no API credential is stored for this vendor",
                      "minted": False})

    keep = (R.get_supabase, SS.vendor_by_id, SS.login_row_full,
            R._shopify.push_draft_order, CR.access_token)
    R.get_supabase = lambda: client
    SS.vendor_by_id = lambda *a, **k: vendor
    SS.login_row_full = lambda *a, **k: ({"id": "src1", "username": "cid", "password": "csec"}
                                         if token else {})
    R._shopify.push_draft_order = fake_push
    CR.access_token = fake_token
    try:
        out = asyncio.get_event_loop().run_until_complete(
            R._push_accessory_pos(ORG, cfg, created))
    finally:
        (R.get_supabase, SS.vendor_by_id, SS.login_row_full,
         R._shopify.push_draft_order, CR.access_token) = keep
    pushes = [c for c in calls if "minted_for" not in c]
    return out, client, pushes


CFG_PUSH = {"po_mode": "draft_push", "accessory_vendor_id": "v1"}
PO = [{"org_id": ORG, "id": "po1", "po_number": "PO-1", "notes": "", "external_ref": None}]

out, client, calls = _run(CFG_PUSH, [dict(PO[0])], [{"id": "po1"}])
ok(len(out["pushed"]) == 1 and len(calls) == 1, "E1 a fresh draft is pushed once")
ok(client.rows("purchase_order")[0]["external_ref"] == "D1",
   "E2 the vendor's reference is recorded on the PO")
ok(client.rows("purchase_order")[0].get("external_pushed_at"), "E3 and when it was pushed")
ok(calls[0]["lines"] and calls[0]["lines"][0]["name"] == "Case" and calls[0]["lines"][0]["qty"] == 4,
   "E4 the lines come from the PO we WROTE, not from the draft that produced it")
ok(calls[0]["token"] == "shpat_test", "E5 the credential comes from the login row")

out2, client2, calls2 = _run(CFG_PUSH, [dict(PO[0], external_ref="ALREADY")], [{"id": "po1"}])
ok(not calls2, "E6 IDEMPOTENT — a PO that already carries a reference is never pushed again")

out3, client3, calls3 = _run(CFG_PUSH, [dict(PO[0])], [{"id": "po1"}],
                             push_result={"ok": False, "external_ref": "", "external_url": "",
                                          "external_name": "", "error": "vendor refused (401)"})
ok(len(out3["failed"]) == 1, "E7 a refusal is reported as a failure")
ok("401" in str(client3.rows("purchase_order")[0].get("external_error")),
   "E8 and is RECORDED on the PO — a draft that never arrived must be visible, not absent")
ok(not client3.rows("purchase_order")[0].get("external_ref"),
   "E9 a failed push records no reference, so the next run retries it")

out4, _, calls4 = _run({"po_mode": "draft", "accessory_vendor_id": "v1"}, [dict(PO[0])], [{"id": "po1"}])
ok(not calls4 and out4["skipped"], "E10 po_mode 'draft' pushes nothing")
out5, _, calls5 = _run(CFG_PUSH, [dict(PO[0])], [{"id": "po1"}], token="")
ok(not calls5 and "credential" in (out5["skipped"] or ""), "E11 no credential, no push")
out6, _, calls6 = _run(CFG_PUSH, [dict(PO[0])], [{"id": "po1"}],
                       vendor=dict(VENDOR, portal_config={}))
ok(not calls6 and out6["skipped"], "E12 a vendor with no declared route is not pushed to")
out7, _, calls7 = _run(CFG_PUSH, [dict(PO[0])], [{"id": "po1"}],
                       vendor=dict(VENDOR, portal_config={"order_transport":
                                   {"kind": "api", "api": dict(API, places_order=True)}}))
ok(not calls7 and "PLACES" in (out7["skipped"] or ""),
   "E13 a route that would PLACE the order is refused by the sweep")
out8, _, calls8 = _run(CFG_PUSH, [dict(PO[0])], [])
ok(not calls8, "E14 nothing created, nothing pushed")


# ── F — the locks ───────────────────────────────────────────────────────────────────────────────
print("\n§F  locks")
HOME_OT = os.path.join(APP, "modules", "supply", "order_transport.py")
HOME_SH = os.path.join(APP, "modules", "supply", "shopify_draft_order.py")

second_resolver = [f for f in py_files() if f != HOME_OT
                   and re.search(r"portal_config.{0,40}order_transport", code_text(f))]
ok(not second_resolver,
   f"F1 ONE resolver — nobody else reads the order_transport config block {second_resolver}")

speakers = [f for f in py_files() if f != HOME_SH
            and re.search(r"draft_orders\.json|X-Shopify-Access-Token", code_text(f))]
ok(not speakers, f"F2 ONE dialect — nobody else speaks this API {speakers}")

invoice = [f for f in py_files() if re.search(r"draft_order_invoice|send_invoice", code_text(f))]
ok(not invoice, f"F3 NOTHING anywhere calls the vendor's invoice-send endpoint {invoice}")

sh = code_text(HOME_SH)
ok(not re.search(r"portal_config|data_source|\.password", sh),
   "F4 the dialect never reads a credential itself — the caller passes it in")
ok("places_order" in code_text(HOME_OT), "F5 the home knows the difference between draft and place")

brand = re.compile(r"accessoriz|cellfonz|luxelink|boost|vzone", re.I)
named = [f for f in (HOME_OT, HOME_SH) if brand.search(open(f, encoding="utf-8").read())]
ok(not named, f"F6 RULE TWO — no tenant, vendor or carrier name in either home {named}")

mig = open(MIG, encoding="utf-8").read() if os.path.exists(MIG) else ""
ok(bool(mig), "F7 migration 1053 exists")
ok("external_ref" in mig and "ux_po_org_external_ref" in mig,
   "F8 it adds the reference the idempotency depends on, and its unique index")
ok("draft_push" in mig and "draft_push" in code_text(
    os.path.join(APP, "modules", "storevisit", "visit_alerts.py")),
   "F9 the mode the migration allows is the mode the code reads")
ok("REVERT:" in mig, "F10 it carries a REVERT note")
ok("NOTIFY pgrst" in mig, "F11 and tells PostgREST the shape changed")

router_src = code_text(os.path.join(APP, "modules", "storevisit", "router.py"))
ok("_push_accessory_pos" in router_src and "push_enabled" in router_src,
   "F12 the sweep asks the home whether it may send; it does not decide for itself")
ok(re.search(r"if not dry_run:\s*\n\s*po\[.vendor_push.\]", router_src),
   "F13 a DRY RUN speaks to nobody")

# ── armed controls — each lock must be able to fail ─────────────────────────────────────────────
print("\n§F  armed controls")
ok(bool(re.search(r"portal_config.{0,40}order_transport",
                  "x = vendor['portal_config']['order_transport']")),
   "F1-armed the second-resolver scan matches a real second read")
ok(bool(re.search(r"draft_orders\.json|X-Shopify-Access-Token",
                  'cx.post(f"{h}/draft_orders.json")')),
   "F2-armed the second-dialect scan matches a real second speaker")
ok(bool(re.search(r"draft_order_invoice|send_invoice", "await cx.post(draft_order_invoice_url)")),
   "F3-armed the invoice-send scan matches a real send")
ok(bool(brand.search("vendor = 'V Accessorize'")), "F6-armed the brand scan matches a real name")
import tempfile as _tf
with _tf.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as _fh:
    _fh.write('x = 1  # order_transport\n"""order_transport in a docstring"""\n')
    _probe = _fh.name
ok("order_transport" not in code_text(_probe),
   "F-armed code_text strips comments AND docstrings, so a rule ABOUT a pattern is not the pattern")
os.unlink(_probe)

# ── G — the credential home: a token is ASKED FOR, never read from a column ─────────────────────
print("\n§G  the credential (client id + secret -> a short-lived token)")
import json as _json
import time as _time

NOW = 1_000_000.0
FRESH = {"id": "s1", "username": "cid", "password": "csec",
         "session_state": _json.dumps({"access_token": "tok-A", "expires_at": NOW + 86400})}
STALE = dict(FRESH, session_state=_json.dumps({"access_token": "tok-A",
                                               "expires_at": NOW + 60}))
NONE_ = {"id": "s1", "username": "cid", "password": "csec"}

ok(CRED.usable(FRESH, now=NOW) == "tok-A", "G1 a token with hours left is used as it is")
ok(CRED.usable(STALE, now=NOW) == "",
   "G2 a token expiring within the refresh margin is NOT used — one that dies mid-request is a "
   "failure a human has to read about")
ok(CRED.usable(NONE_, now=NOW) == "", "G3 no cached token, nothing to use")
ok(CRED.usable(dict(FRESH, session_state="{not json"), now=NOW) == "",
   "G4 an unreadable cache is the same as none — it must cause a mint, never an exception on a "
   "path whose job is to send an order")
ok(CRED.cached(dict(FRESH, session_state={"access_token": "t", "expires_at": NOW + 99999}))
   ["access_token"] == "t", "G5 the cache is read whether it arrives as JSON text or a dict")
ok(CRED.has_credential(NONE_) and not CRED.has_credential({"username": "cid"}),
   "G6 'can we mint' needs BOTH halves — a cached token alone does not count, it expires")
ok(CRED.mint_body(NONE_) == {"client_id": "cid", "client_secret": "csec",
                             "grant_type": "client_credentials"},
   "G7 the exchange asks for exactly the client-credentials grant")
ok(CRED.token_endpoint("x.myshopify.com") == "https://x.myshopify.com/admin/oauth/access_token",
   "G8 against the store's own token endpoint")

patch = CRED.store_patch({"access_token": "tok-B", "expires_in": 86399, "scope": "write_draft_orders"},
                         now=NOW)
blob = _json.loads(patch["session_state"])
ok(list(patch) == ["session_state"],
   "G9 an exchange writes back ONLY the cache — rewriting the credential on a read path is how "
   "credentials get lost")
ok(abs(blob["expires_at"] - (NOW + 86399)) < 1, "G10 the expiry is absolute, not a duration")
ok(CRED.store_patch({"access_token": "t", "expires_in": 999_999_999}, now=NOW)
   and _json.loads(CRED.store_patch({"access_token": "t", "expires_in": 999_999_999},
                                    now=NOW)["session_state"])["expires_at"]
   <= NOW + CRED.MAX_LIFETIME_SECONDS,
   "G11 a nonsense lifetime is capped — a vendor must not pin a dead token in place for a year")
ok(CRED.store_patch({"expires_in": 99}) == {}, "G12 no token, nothing written")
ok(CRED.store_patch({"access_token": "t", "expires_in": "oops"}, now=NOW)
   and _json.loads(CRED.store_patch({"access_token": "t", "expires_in": "oops"},
                                    now=NOW)["session_state"])["expires_at"] == NOW,
   "G13 an unparseable lifetime expires immediately rather than never")

d = CRED.describe(FRESH, now=NOW)
ok(d["has_credential"] and d["has_token"] and d["usable"] and d["expires_in"] > 0,
   "G14 a screen can learn that a credential exists and when it expires")
ok("access_token" not in _json.dumps(d) and "tok-A" not in _json.dumps(d),
   "G15 and NEVER the token itself")

cred_src = code_text(os.path.join(APP, "modules", "supply", "api_credential.py"))
ok(cred_src.count("client_secret") == 1 and "password" in cred_src,
   "G16 the secret is named in ONE place in the home — the exchange body")
ok("res.text" not in cred_src and "res.json()" in cred_src,
   "G17 a refused exchange never echoes the response body — it can contain what was sent")

_rt = code_text(os.path.join(APP, "modules", "storevisit", "router.py"))
ok("_cred.access_token(" in _rt and '.get("password")' not in _rt,
   "G18 LOCK: the sweep ASKS for a token — it never reads a credential column itself, which was "
   "correct only while vendors issued permanent tokens")
# Excused by name, with the reason written down: vision/google_sdm.py performs its own OAuth
# exchange for a camera feed. It is not a vendor ORDER credential, shares no caller with one, and
# predates this home. Any OTHER file that mints a token is the duplicate this lock exists to catch.
CRED_EXCHANGE_EXCUSED = (
    os.path.join(APP, "modules", "supply", "api_credential.py"),   # the one home
    os.path.join(APP, "modules", "vision", "google_sdm.py"),       # camera OAuth, not an order route
)
_others = [f for f in py_files() if f not in CRED_EXCHANGE_EXCUSED
           and re.search(r"grant_type|admin/oauth/access_token", code_text(f))]
ok(not _others, f"G19 LOCK: ONE place performs a vendor-order credential exchange {_others}")
ok(all(os.path.exists(f) for f in CRED_EXCHANGE_EXCUSED),
   "G19b every excused file still exists — a stale excuse is a hole in the lock")
ok(bool(re.search(r"grant_type|admin/oauth/access_token",
                  'body = {"grant_type": "client_credentials"}')),
   "G19-armed the exchange scan matches a real second exchange")

print("\n" + "=" * 78)
print(f"{passed} passed, {failed} failed")
if failed:
    print("FAIL — a vendor's order route is not locked.")
    raise SystemExit(1)
print("OK — one declared route, one dialect, nothing placed unattended, nothing invoiced.")
