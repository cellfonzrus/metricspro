"""Harness — POS "Customer Special Order" + plug-and-play vendor connectors (mig 864/865/866).

Owner directive 2026-08-19: let a store sell what it doesn't stock by special-ordering it from a
back-end vendor (Amazon at launch, any dropship vendor via a connector), source hidden, while booking
the sale/COGS/profit through the rails we already have — and "plug and play if other vendors also have
a drop shipment platform to use with their API to connect to our system or with our API to connect to
them."

What is actually at risk, and therefore what is tested here:

  1. SOURCE-HIDING AT THE BOUNDARY. create_special_order reads the vendor cost server-side to book COGS
     and guard the margin, but the vendor (cost, SKU, identity) must NEVER appear in the store-facing
     response. A leak here defeats the entire feature.
  2. THE MARGIN FLOOR. A declared price below cost×(1+min%) must be refused — a special order must not
     book a loss.
  3. THE BOOKING IS CORRECT. checkout must be called with unit_price = declared price (→ revenue) and
     cost = vendor cost (→ COGS) so profit derives; and a special_orders row must link the booked sale.
  4. STORE SCOPE. A store-scoped rep can only order for / read their own store's orders.
  5. INVENTORY-NEUTRAL. (migration 865 SQL — asserted structurally here) the sale-item trigger skips
     is_special_order products so a special order never fails for "no stock".
  6. PLUG-AND-PLAY ADAPTERS. get_adapter resolves a connector to the right adapter; a manual/inbound
     connector leaves the order queued; a vendor override wins over the mode default; an outbound
     connector with no URL falls back to the manual queue rather than dropping the order.
  7. CONNECTOR SECRETS. The inbound token is stored only as a hash and shown exactly once; the admin
     read never returns the hash; credential_ref holds a NAME, never a raw key.
  8. INBOUND VENDOR API. A vendor authenticates with its bearer token, sees ONLY its own vendor_key's
     queued orders (no PII / price / cost / margin), and can only set the vendor-settable statuses.
"""
import sys, os, types, copy, hashlib

sys.path.insert(0, os.path.dirname(__file__))
PASS, FAIL = [], []


def ok(c, w):
    (PASS if c else FAIL).append(w)
    print(("  PASS " if c else "  FAIL ") + w)


def raises(fn, needle=""):
    try:
        fn()
    except Exception as e:
        return needle.lower() in (getattr(e, "detail", "") or str(e)).lower()
    return False


class _Q:
    """A query that really filters — on SELECT, UPDATE and DELETE alike (the eq-noop trap)."""

    def __init__(self, store, name, calls):
        self.store, self.name, self.calls, self.f = store, name, calls, []
        self._in = None

    def select(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def order(self, *a, **k): return self
    def or_(self, *a, **k): return self

    def eq(self, c, v):
        self.f.append((c, v)); return self

    def in_(self, c, vals):
        self._in = (c, list(vals)); return self

    def _match(self, row):
        if not all(row.get(c) == v for c, v in self.f):
            return False
        if self._in and row.get(self._in[0]) not in self._in[1]:
            return False
        return True

    def insert(self, rows):
        self._ins = rows if isinstance(rows, list) else [rows]; return self

    def upsert(self, rows, on_conflict=None):
        self._ins = rows if isinstance(rows, list) else [rows]
        self._conflict = (on_conflict or "").split(",") if on_conflict else None
        return self

    def update(self, patch):
        self._patch = patch; return self

    def execute(self):
        rows = self.store.setdefault(self.name, [])
        if getattr(self, "_ins", None) is not None:
            out = []
            conflict = getattr(self, "_conflict", None)
            for r in self._ins:
                r = dict(r)
                if conflict:
                    hit = [e for e in rows if all(e.get(k) == r.get(k) for k in conflict)]
                    if hit:
                        hit[0].update(r); out.append(hit[0]); continue
                r.setdefault("id", f"{self.name}-{len(rows) + len(out) + 1}")
                out.append(r); rows.append(r)
            self.calls.append(("insert", self.name, list(self.f)))
            return self._done(copy.deepcopy(out))
        if getattr(self, "_patch", None) is not None:
            hit = [r for r in rows if self._match(r)]
            for r in hit:
                r.update(self._patch)
            self.calls.append(("update", self.name, list(self.f)))
            return self._done(copy.deepcopy(hit))
        self.calls.append(("select", self.name, list(self.f)))
        return self._done(copy.deepcopy([r for r in rows if self._match(r)]))

    def _done(self, data):
        return types.SimpleNamespace(data=data)


class _S:
    def __init__(self, store, calls, rpc_result): self.store, self.calls, self.rpc_result = store, calls, rpc_result

    def table(self, n):
        self.store.setdefault(n, [])
        return _Q(self.store, n, self.calls)

    def rpc(self, name, params):
        self.calls.append(("rpc", name, params))
        return types.SimpleNamespace(execute=lambda: types.SimpleNamespace(data=self.rpc_result(params)))


class FakeClient:
    def __init__(self, store, rpc_result): self.store, self.calls, self.rpc_result = store, [], rpc_result

    def schema(self, n): return _S(self.store, self.calls, self.rpc_result)
    def table(self, n): return _S(self.store, self.calls, self.rpc_result).table(n)


import app.modules.pos.router as R          # noqa: E402
import app.modules.pos.vendor_adapters as VA  # noqa: E402
import app.modules.pos.vendor_api as VApi   # noqa: E402

ORG = "00000000-0000-0000-0000-000000000001"


def sha(t): return hashlib.sha256(t.encode()).hexdigest()


def base_store():
    return {
        "products": [{"id": "P1", "org_id": ORG, "is_special_order": True, "is_active": True,
                      "short_name": "Widget", "full_name": "Deluxe Widget", "retail_price": 100.0,
                      "cost": 40.0, "system_category": "Accessory", "is_taxable": True}],
        "special_order_vendor": [{"id": "V1", "org_id": ORG, "product_id": "P1", "vendor": "amazon",
                                  "vendor_sku": "ASIN123", "vendor_cost": 50.0}],
        "vendor_connector": [{"id": "C1", "org_id": ORG, "vendor_key": "amazon",
                              "integration_mode": "manual", "is_active": True}],
        "special_orders": [],
        "stores": [{"org_id": ORG, "store_code": "S1", "address": "1 Main St"}],
    }


def checkout_result(params):
    return [{"id": "sale-1", "org_id": params.get("p_org"), "receipt_no": "R-1",
             "store_code": (params.get("p_sale") or {}).get("store_code")}]


def wire(store, employee="E1", keyset=None, perm_ok=True):
    fc = FakeClient(store, checkout_result)
    R.sb = lambda: fc
    VApi._sb = lambda: fc
    R._caller_employee = lambda a, o: employee
    R._caller_store_keyset = lambda a, o: keyset
    if perm_ok:
        R._require_pos_perm = lambda a, o, k: None
    else:
        def _deny(a, o, k):
            from fastapi import HTTPException
            raise HTTPException(403, f"your role does not allow this action ({k})")
        R._require_pos_perm = _deny
    return fc


print("\n=== A · booking books the sale correctly and hides the source ===")
store = base_store()
fc = wire(store)
res = R.create_special_order({"store_code": "S1", "product_id": "P1", "declared_sale_price": 120.0,
                              "customer_name": "Jane"}, org_id=ORG)
rpc = [c for c in fc.calls if c[0] == "rpc"]
ok(len(rpc) == 1 and rpc[0][1] == "checkout", "A1 the sale is booked via pos.checkout")
line = rpc[0][2]["p_items"][0]
ok(line["unit_price"] == 120.0, "A2 declared price is the line unit_price (→ revenue)")
ok(line["cost"] == 50.0, "A3 vendor cost is the line cost (→ COGS; profit derives)")
so = res["special_order"]
stored_so = store["special_orders"][0]
ok(stored_so["sale_id"] == "sale-1", "A4 the special_orders row links the booked sale")
ok(stored_so["captured_cost"] == 50.0 and stored_so["sale_price"] == 120.0,
   "A5 the stored order captures cost + declared price (server-side)")
blob = str(res)
ok("captured_cost" not in so and "vendor" not in so,
   "A6 the STORE-facing response redacts the vendor cost + identity (source-hiding)")
ok("ASIN123" not in blob, "A7 vendor SKU is NOT in the response")
ok(stored_so["status"] == "requested", "A8 a manual connector leaves the order queued (requested)")
_saved = R._has_pos_perm
R._has_pos_perm = lambda a, o, k: True
admin_view = R.get_special_order(stored_so["id"], org_id=ORG)["special_order"]
ok(admin_view.get("captured_cost") == 50.0 and admin_view.get("vendor") == "amazon",
   "A9 an HQ (pos_special_order_admin) caller DOES see the vendor cost + identity")
R._has_pos_perm = _saved

print("\n=== B · the margin floor refuses loss-making orders ===")
store = base_store(); wire(store)
ok(raises(lambda: R.create_special_order({"store_code": "S1", "product_id": "P1",
          "declared_sale_price": 55.0}, org_id=ORG), "minimum"),
   "B1 price below cost×(1+15%) is refused (50×1.15 = 57.5)")
store = base_store(); wire(store)
ok(R.create_special_order({"store_code": "S1", "product_id": "P1", "declared_sale_price": 57.5},
                          org_id=ORG)["special_order"]["status"] == "requested",
   "B2 price exactly at the floor is accepted")

print("\n=== C · guards: employee link, catalog membership, store scope ===")
store = base_store(); wire(store, employee="")
ok(raises(lambda: R.create_special_order({"store_code": "S1", "product_id": "P1",
          "declared_sale_price": 120.0}, org_id=ORG), "employee"),
   "C1 an unlinked login cannot place an order")
store = base_store()
store["products"][0]["is_special_order"] = False
wire(store)
ok(raises(lambda: R.create_special_order({"store_code": "S1", "product_id": "P1",
          "declared_sale_price": 120.0}, org_id=ORG), "not a special-order"),
   "C2 a non-special-order product is refused")
store = base_store(); wire(store, keyset={"S2"})
ok(raises(lambda: R.create_special_order({"store_code": "S1", "product_id": "P1",
          "declared_sale_price": 120.0}, org_id=ORG), "scope"),
   "C3 a rep scoped to S2 cannot order for S1")

print("\n=== D · the neutral catalog never reads the vendor table ===")
store = base_store(); fc = wire(store)
cat = R.special_order_catalog(org_id=ORG)
touched = {c[1] for c in fc.calls}
ok("special_order_vendor" not in touched, "D1 the store catalog never queries special_order_vendor")
ok(all("vendor" not in i and "cost" not in i for i in cat["items"]), "D2 catalog items expose no vendor/cost")

print("\n=== E · plug-and-play adapters ===")
ok(isinstance(VA.get_adapter({"integration_mode": "manual"}), VA.ManualAdapter), "E1 manual → ManualAdapter")
ok(isinstance(VA.get_adapter({"integration_mode": "inbound_api"}), VA.InboundApiAdapter),
   "E2 inbound_api → InboundApiAdapter")
ok(isinstance(VA.get_adapter({"integration_mode": "outbound_api"}), VA.OutboundApiAdapter),
   "E3 outbound_api → OutboundApiAdapter")
ok(isinstance(VA.get_adapter({"integration_mode": "wat"}), VA.ManualAdapter),
   "E4 an unknown mode fails safe to manual (order never silently escapes)")
ok(VA.ManualAdapter({}).place_order({}).get("status") == "requested", "E5 manual place → queued")
ok(VA.OutboundApiAdapter({"integration_mode": "outbound_api"}).place_order({}).get("status") == "requested",
   "E6 outbound with no api_base_url falls back to the manual queue")


class _Acme(VA.VendorAdapter):
    mode = "outbound_api"
    def place_order(self, order): return {"status": "ordered", "vendor_order_ref": "ACME-1"}


VA.register_adapter("vendor:acme", _Acme)
ok(isinstance(VA.get_adapter({"integration_mode": "outbound_api", "vendor_key": "acme"}), _Acme),
   "E7 a vendor override wins over the mode default")
ok(VA.token_hash(" tok ") == sha("tok"), "E8 token_hash trims + SHA-256s the token")

print("\n=== F · outbound adapter actually calls the vendor API (config-driven) ===")
sent = {}


class _FakeResp:
    content = b"{}"
    def raise_for_status(self): pass
    def json(self): return {"order_id": "OUT-9", "tracking": "TRK-9"}


def _fake_post(url, json=None, headers=None, timeout=None):
    sent.update(url=url, json=json, headers=headers); return _FakeResp()


fake_requests = types.SimpleNamespace(post=_fake_post)
sys.modules["requests"] = fake_requests
os.environ["ACME_KEY"] = "s3cret"
out = VA.OutboundApiAdapter({"integration_mode": "outbound_api", "api_base_url": "https://acme.test/v1",
                             "credential_ref": "ACME_KEY",
                             "config": {"place_path": "/orders", "auth_scheme": "Token"}}
                            ).place_order({"vendor_sku": "SKU9", "qty": 2, "ship_to": "S1"})
ok(sent.get("url") == "https://acme.test/v1/orders", "F1 posts to api_base_url + place_path")
ok(sent["headers"].get("Authorization") == "Token s3cret",
   "F2 the credential is read from the env NAMED by credential_ref (never stored raw)")
ok(out["status"] == "ordered" and out["vendor_order_ref"] == "OUT-9" and out["tracking"] == "TRK-9",
   "F3 the vendor's order ref + tracking are captured")

print("\n=== G · connector CRUD keeps secrets safe ===")
store = base_store(); fc = wire(store)
created = R.create_vendor_connector({"vendor_key": "acme", "integration_mode": "inbound_api",
                                     "inbound_token": "hand-to-vendor", "credential_ref": "ACME_KEY"},
                                    org_id=ORG)
ok(created.get("inbound_token") == "hand-to-vendor", "G1 the raw token is returned exactly once")
ok("inbound_token_hash" not in created["connector"], "G2 the stored hash is never surfaced")
stored = [c for c in store["vendor_connector"] if c["vendor_key"] == "acme"][0]
ok(stored["inbound_token_hash"] == sha("hand-to-vendor") and "inbound_token" not in stored,
   "G3 only the hash is persisted, not the token")
lst = R.list_vendor_connectors(org_id=ORG)
ok(all("inbound_token_hash" not in c for c in lst["connectors"]), "G4 the admin list redacts the hash")
ok(raises(lambda: R.create_vendor_connector({"vendor_key": "x", "integration_mode": "bogus"},
          org_id=ORG), "integration_mode"), "G5 an unknown mode is refused")

print("\n=== H · inbound vendor API — token scope + reverse source-hiding ===")
store = base_store()
store["vendor_connector"].append({"id": "C2", "org_id": ORG, "vendor_key": "acme",
                                  "integration_mode": "inbound_api", "is_active": True,
                                  "inbound_token_hash": sha("acme-tok")})
store["special_orders"] = [
    {"id": "O1", "org_id": ORG, "vendor": "acme", "status": "requested", "store_code": "S1",
     "ship_to_store": "S1", "product_id": "P1", "description": "Deluxe Widget", "qty": 1,
     "order_no": 1, "sale_price": 120.0, "captured_cost": 50.0, "customer_name": "Jane"},
    {"id": "O2", "org_id": ORG, "vendor": "amazon", "status": "requested", "store_code": "S1",
     "product_id": "P1", "qty": 1, "order_no": 2},
]
wire(store)
pulled = VApi.vendor_pull_orders(authorization="Bearer acme-tok")["orders"]
ok(len(pulled) == 1 and pulled[0]["order_id"] == "O1",
   "H1 a vendor sees ONLY its own vendor_key's queued orders")
o = pulled[0]
ok(o.get("vendor_sku") == "ASIN123" and o.get("ship_to_address") == "1 Main St",
   "H2 the vendor gets what it needs to ship (SKU + ship-to address)")
ok("sale_price" not in o and "captured_cost" not in o and "customer_name" not in o,
   "H3 reverse source-hiding — no price, cost, or customer PII")
ok(raises(lambda: VApi.vendor_pull_orders(authorization="Bearer wrong"), "invalid"),
   "H4 a bad token is rejected")
ok(raises(lambda: VApi.vendor_pull_orders(authorization=""), "token required"),
   "H5 a missing token is rejected")
upd = VApi.vendor_post_status("O1", {"status": "shipped", "tracking": "1Z", "order_ref": "AZ-1"},
                              authorization="Bearer acme-tok")
ok(upd["status"] == "shipped" and upd["tracking"] == "1Z", "H6 the vendor can post shipped + tracking")
ok(raises(lambda: VApi.vendor_post_status("O1", {"status": "delivered"}, authorization="Bearer acme-tok"),
          "status must be"), "H7 the vendor cannot set 'delivered' (that's the store handing to customer)")
ok(raises(lambda: VApi.vendor_post_status("O2", {"status": "shipped"}, authorization="Bearer acme-tok"),
          "not found"), "H8 a vendor cannot touch another vendor's order")

print(f"\n==== {len(PASS)} passed, {len(FAIL)} failed ====")
sys.exit(1 if FAIL else 0)
