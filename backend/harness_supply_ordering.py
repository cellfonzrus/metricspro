"""PROOF — SUPPLY ORDERING (index §36, mig 1021): vendor setup, catalog ingest, the cart optimizer (free-shipping
thresholds, lead time, stock, pack basis), the assisted-order recipe, confirmation capture, the dashboard tiles —
and the locks that keep it on ONE home per fact. Stdlib only, DB-free, browser-free, network-free:

    cd backend && python3 harness_supply_ordering.py

Owner request 2026-09-25 (abridged): add items to the cheapest vendor's cart from the platform and place the order;
capture the order confirmation back into the system; each vendor's free-shipping threshold and approximate delivery
time are defined by the tenant when the vendor is set up. Vendors are config, never code.
"""
import json
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
SUP = os.path.join(HERE, "app", "modules", "supply")
sys.path.insert(0, HERE)

from app.modules.supply import ordering_logic as L  # noqa: E402
from app.modules.supply import pricing_core as core  # noqa: E402
from app.modules.supply import portal as P  # noqa: E402
from app.modules.supply import store as S  # noqa: E402

FAILS, N = [], 0


def check(name, cond, detail=""):
    global N
    N += 1
    if not cond:
        FAILS.append(f"{name} {detail}")
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  → {detail}"))


def src(*parts):
    return open(os.path.join(*parts), encoding="utf-8").read()


# ══ A. VENDOR SETUP — the tenant defines the free-shipping threshold and the delivery time ═══════════════
print("§A vendor setup")
_, e = L.validate_vendor({"name": "Vendor One", "is_price_source": True, "portal_url": "https://shop.example/login"})
check("A1 a portal vendor without a threshold is refused", any("free-shipping threshold" in x for x in e), e)
check("A2 ...and without a delivery time", any("delivery time" in x for x in e), e)
ok_body = {"name": "Vendor One", "is_price_source": True, "portal_url": "https://shop.example/login",
           "free_shipping_threshold": "150", "shipping_fee_below_threshold": "$12.50",
           "delivery_days_min": "2", "delivery_days_max": 4, "catalog_urls": "https://shop.example/a, https://shop.example/b"}
clean, e = L.validate_vendor(ok_body)
check("A3 a complete portal vendor passes", not e, e)
check("A4 money + days are normalised", clean["free_shipping_threshold"] == 150.0 and clean["shipping_fee_below_threshold"] == 12.5
      and clean["delivery_days_min"] == 2 and clean["delivery_days_max"] == 4, clean)
check("A5 catalog links split", clean["catalog_urls"] == ["https://shop.example/a", "https://shop.example/b"], clean["catalog_urls"])
_, e = L.validate_vendor({**ok_body, "delivery_days_min": 6})
check("A6 min days > max days refused", any("minimum days" in x for x in e), e)
_, e = L.validate_vendor({**ok_body, "free_shipping_threshold": -1})
check("A7 negative threshold refused", any("cannot be negative" in x for x in e), e)
_, e = L.validate_vendor({**ok_body, "portal_url": "shop.example"})
check("A8 a bare host is not a portal URL", any("full http" in x for x in e), e)
_, e = L.validate_vendor({"name": "Paper Co"})
check("A9 a plain (non-portal) roster vendor needs only a name", not e, e)
_, e = L.validate_vendor({"free_shipping_threshold": 0}, existing={**clean, "name": "Vendor One"})
check("A10 threshold 0 (always free) is allowed on update", not e, e)
_, e = L.validate_vendor({"delivery_days_max": ""}, existing={**clean, "name": "Vendor One"})
check("A11 clearing the delivery time of a portal vendor is refused", any("delivery time" in x for x in e), e)

# ══ B. PORTAL CONFIG — a vendors.json block; never a credential ═══════════════════════════════════════════
print("§B portal config")
kit_cfg = json.load(open(os.path.join(REPO, "tools", "vendor_price_compare", "vendors.json")))
for blk in kit_cfg["vendors"]:
    c, e = L.normalize_portal_config(blk)
    check(f"B1 the kit's vendors.json block '{blk['key'][:3]}…' is a valid portal_config", not e, e)
_, e = L.normalize_portal_config({"login": {"url": "https://x.example", "password": "hunter2"}})
check("B2 a config carrying a password is refused", any("must not hold a login" in x for x in e), e)
_, e = L.normalize_portal_config({"login": {"username": "me@x.example"}})
check("B3 ...and one carrying a username", any("must not hold a login" in x for x in e), e)
_, e = L.normalize_portal_config({"login": {"username_selector": "input[name=email]", "password_selector": "input[type=password]"}})
check("B4 selectors naming the login boxes are fine (negative control)", not e, e)
_, e = L.normalize_portal_config({"follow_patterns": ["main_page=(index"]})
check("B5 a broken pattern is refused", any("not a valid pattern" in x for x in e), e)
_, e = L.normalize_portal_config('{"max_pages": 20}')
check("B6 JSON text is accepted", not e, e)
_, e = L.normalize_portal_config("{nope")
check("B7 invalid JSON said", any("not valid JSON" in x for x in e), e)
_, e = L.normalize_portal_config({"ordering": {"line_steps": [{"action": "hack"}]}})
check("B8 recipe errors surface through the config check", any("unknown action" in x for x in e), e)

# ══ C. THE READER'S CONFIG — the vendor key is its id, never a name ═════════════════════════════════════
print("§C scrape config + hosts")
V1 = {"id": "v-1", "name": "Vendor One", "portal_url": "https://shop.example/login", "catalog_urls": ["https://shop.example/cat"],
      "portal_config": {"strip_params": ["sid"], "ordering": {"cart_url": "https://cart.shop.example/cart"},
                        "extra_hosts": ["img.shop.example"]}}
sc = L.vendor_scrape_config(V1)
check("C1 key = vendor id", sc["key"] == "v-1")
check("C2 start_urls = the tenant's catalog links", sc["start_urls"] == ["https://shop.example/cat"])
check("C3 login url from portal_url", sc["login"]["url"] == "https://shop.example/login")
check("C4 platform keeps no page snapshots", sc["snapshot_pages"] == 0)
check("C5 the ordering recipe never reaches the read-only reader", "ordering" not in sc)
check("C6 hosts = portal + catalog + cart + extra", L.vendor_hosts(V1) == {"shop.example", "cart.shop.example", "img.shop.example"},
      L.vendor_hosts(V1))
check("C7 no catalog links → the portal page is the start", L.vendor_scrape_config({"id": "x", "portal_url": "https://a.example/"})["start_urls"]
      == ["https://a.example/"])

# ══ D. CATALOG INGEST — one normaliser for both routes ═══════════════════════════════════════════════════
print("§D catalog rows")
kit_json = json.dumps({"run_info": {}, "products": [
    {"vendor": "alpha", "name": "12 x 12 x 12 Box - 25/bundle", "sku": "BX-121212", "price": 18.75, "list_price": 21.0,
     "pack_qty": 25, "availability": "in_stock", "stock_qty": 340, "url": "https://a.example/p/1"},
    {"vendor": "alpha", "name": "Tape", "sku": "", "price": None, "availability": "unknown"},
    {"vendor": "beta", "name": "Poly mailer 10 x 13 - 100 ct", "sku": "", "price": "$9.50", "availability": "",
     "stock_text": "Out of Stock", "url": "https://b.example/x#top"}]})
rows = L.parse_products_upload("products.json", kit_json.encode())
check("D1 products.json read", len(rows) == 3)
clean, rej = L.normalize_catalog_rows(rows)
check("D2 a row with no price is rejected with its reason (never $0)", len(clean) == 2 and rej[0]["reason"] == "no price", rej)
box = clean[0]
check("D3 item_key from the item number", box["item_key"] == "s:BX121212", box["item_key"])
check("D4 list price kept only when above price", box["list_price"] == 21.0)
mailer = clean[1]
check("D5 availability read from the stock text when the column is blank", mailer["availability"] == "out_of_stock", mailer)
check("D6 pack read from the name", mailer["pack_qty"] == 100)
check("D7 no item number → hash of link + name (fragment ignored)", mailer["item_key"].startswith("u:")
      and mailer["item_key"] == L.item_key({"url": "https://b.example/x", "name": "poly mailer 10 x 13 - 100 ct"}))
csv_text = "vendor,name,sku,price,list_price,pack_qty,availability,stock_qty,stock_text,url\nalpha,Widget 5,W-55,$4.00,,,,,Units in Stock: 7,u\n"
crow, _ = L.normalize_catalog_rows(L.parse_products_upload("products.csv", csv_text))
check("D8 products.csv read; stock qty from the text", crow[0]["availability"] == "in_stock" and crow[0]["stock_qty"] == 7, crow)
dup, _ = L.normalize_catalog_rows([{"name": "Widget 5", "sku": "W55", "price": 4, "availability": "unknown"},
                                   {"name": "Widget 5", "sku": "w-55", "price": 4, "availability": "in_stock", "stock_qty": 3}])
check("D9 listing + detail rows of one product merge (detail knows the stock)", len(dup) == 1 and dup[0]["availability"] == "in_stock", dup)
try:
    L.parse_products_upload("prices.xlsx", b"\x00\x01")
    check("D10 an unknown file is refused", False)
except ValueError:
    check("D10 an unknown file is refused", True)
vendors = [{"id": "v-a", "name": "Alpha Supply", "portal_config": {"key": "alpha"}},
           {"id": "v-b", "name": "Beta", "portal_config": {}}, {"id": "v-c", "name": "beta", "portal_config": {}}]
check("D11 kit key → vendor by portal_config.key", L.map_kit_vendor("alpha", vendors) == "v-a")
check("D12 an ambiguous name maps to nobody (never guessed)", L.map_kit_vendor("beta", vendors) is None)
check("D13 an unknown key maps to nobody", L.map_kit_vendor("gamma", vendors) is None)

# ══ E. THE CART OPTIMIZER ═══════════════════════════════════════════════════════════════════════════════
print("§E cart optimizer")


def O(vendor, price, pack=None, avail="in_stock", stock=None, rid=None):
    return {"vendor": vendor, "price": price, "pack_qty": pack, "availability": avail, "stock_qty": stock,
            "row_id": rid or f"{vendor}-{price}", "name": f"item@{vendor}", "sku": "", "url": f"https://{vendor}.example/p"}


def VT(name, thr=0, fee=None, dmin=None, dmax=None):
    return {"name": name, "free_shipping_threshold": thr, "shipping_fee_below_threshold": fee,
            "delivery_days_min": dmin, "delivery_days_max": dmax}


# E1–E3: the free-shipping threshold FLIPS the answer
VEN = {"A": VT("A", 50, 10, 2, 3), "B": VT("B", 50, 10, 1, 2)}
items = [{"key": "x", "label": "X", "qty": 1, "offers": [O("A", 40), O("B", 42)]},
         {"key": "y", "label": "Y", "qty": 1, "offers": [O("A", 31), O("B", 30)]}]
plan = L.optimize_cart(items, VEN)
check("E1 exact search used for a small cart", plan["method"] == "exact")
check("E2 threshold flip: both items at A ($71, free shipping) beats the per-item split ($90)",
      plan["total"] == 71.0 and {ln["vendor"] for ln in plan["lines"]} == {"A"}, plan["total"])
check("E3 the plan says what it saved vs buying each item at its cheapest vendor",
      plan["baselines"]["cheapest_each_item"] == 90.0 and plan["savings_vs_cheapest_each_item"] == 19.0, plan["baselines"])
check("E4 shipping 0 at/above the threshold", plan["vendors"][0]["shipping"] == 0 and plan["shipping_total"] == 0)
# threshold not reached → hint; and a free vendor wins when the fee makes the other dearer
V2 = {"A": VT("A", 50, 8), "B": VT("B", 0)}
p2 = L.optimize_cart([{"key": "x", "qty": 1, "offers": [O("A", 40), O("B", 49)]}], V2)
check("E5 under-threshold vendor still wins when price + fee is lower (48 < 49)", p2["lines"][0]["vendor"] == "A" and p2["total"] == 48.0, p2["total"])
check("E6 'add $X more to reach free shipping' hint", any("Add $10.00 more at A" in h and "$8.00" in h for h in p2["hints"]), p2["hints"])
check("E7 to_free_shipping on the vendor block", p2["vendors"][0]["to_free_shipping"] == 10.0)
p3 = L.optimize_cart([{"key": "x", "qty": 1, "offers": [O("A", 40), O("B", 47)]}], V2)
check("E8 ...and loses when it does not (47 free < 40 + 8)", p3["lines"][0]["vendor"] == "B" and p3["total"] == 47.0)
# lead time
V3 = {"A": VT("A", 0, None, 4, 5), "B": VT("B", 0, None, 1, 2), "C": VT("C", 0)}
it3 = [{"key": "x", "qty": 1, "offers": [O("A", 10), O("B", 12), O("C", 9)]}]
check("E9 no limit → the cheapest (C)", L.optimize_cart(it3, V3)["lines"][0]["vendor"] == "C")
p4 = L.optimize_cart(it3, V3, max_delivery_days=3)
check("E10 limit 3 days → B (A delivers in up to 5)", p4["lines"][0]["vendor"] == "B")
check("E11 a vendor with no delivery time set is dropped under a limit, and says so",
      any(x["vendor"] == "C" and "delivery time not set" in x["reason"] for x in p4["lines"][0]["excluded"]), p4["lines"][0]["excluded"])
check("E12 plan delivery window = slowest vendor used", p4["delivery_days"] == {"min": 1, "max": 2}, p4["delivery_days"])
# stock
p5 = L.optimize_cart([{"key": "x", "qty": 1, "offers": [O("A", 5, avail="out_of_stock"), O("B", 9)]}], {"A": VT("A"), "B": VT("B")})
check("E13 out of stock is never chosen, even when cheapest", p5["lines"][0]["vendor"] == "B")
p6 = L.optimize_cart([{"key": "x", "label": "X", "qty": 1, "offers": [O("A", 5, avail="out_of_stock")]}], {"A": VT("A")})
check("E14 all out of stock → unfillable, with the reason", not p6["lines"] and "out of stock" in p6["unfillable"][0]["reason"], p6["unfillable"])
p7 = L.optimize_cart([{"key": "x", "qty": 3, "offers": [O("A", 5, stock=2), O("B", 6)]}], {"A": VT("A"), "B": VT("B")})
check("E15 a known stock shortfall is not chosen (2 in stock, 3 wanted)", p7["lines"][0]["vendor"] == "B")
p8 = L.optimize_cart([{"key": "x", "qty": 1, "offers": [O("A", 5, avail="unknown"), O("B", 6)]}], {"A": VT("A"), "B": VT("B")})
check("E16 unknown availability may be chosen but is FLAGGED", p8["lines"][0]["vendor"] == "A"
      and any("unknown" in f for f in p8["lines"][0]["flags"]), p8["lines"][0]["flags"])
p9 = L.optimize_cart([{"key": "x", "qty": 1, "offers": [O("A", 5, avail="unknown"), O("B", 6)]}], {"A": VT("A"), "B": VT("B")},
                     allow_unknown=False)
check("E17 ...and refused when the user asks for confirmed stock only", p9["lines"][0]["vendor"] == "B")
p10 = L.optimize_cart([{"key": "x", "qty": 1, "offers": [O("A", 5, avail="backorder"), O("B", 6)]}], {"A": VT("A"), "B": VT("B")})
check("E18 backorder refused by default", p10["lines"][0]["vendor"] == "B")
p11 = L.optimize_cart([{"key": "x", "qty": 1, "offers": [O("A", 5, avail="backorder"), O("B", 6)]}], {"A": VT("A"), "B": VT("B")},
                      allow_backorder=True)
check("E19 ...allowed (and flagged) when asked", p11["lines"][0]["vendor"] == "A" and "backorder" in p11["lines"][0]["flags"])
# pack basis
PK = {"A": VT("A"), "B": VT("B")}
p12 = L.optimize_cart([{"key": "box", "qty": 100, "offers": [O("A", 18.75, 25), O("B", 8.00, 10)]}], PK)
check("E20 100 units: A 4 packs $75 beats B 10 packs $80", p12["lines"][0]["vendor"] == "A" and p12["lines"][0]["packs"] == 4
      and p12["total"] == 75.0, p12["lines"][0])
p13 = L.optimize_cart([{"key": "box", "qty": 30, "offers": [O("A", 18.75, 25), O("B", 8.00, 10)]}], PK)
check("E21 30 units: B 3 packs $24 beats A 2 packs $37.50 (whole packs only)", p13["lines"][0]["vendor"] == "B"
      and p13["lines"][0]["packs"] == 3 and p13["total"] == 24.0, p13["lines"][0])
p14 = L.optimize_cart([{"key": "box", "qty": 30, "offers": [O("A", 18.75, 25)]}], PK)
check("E22 rounding up to whole packs is SAID", any("rounded up" in f for f in p14["lines"][0]["flags"]), p14["lines"][0]["flags"])
p15 = L.optimize_cart([{"key": "box", "qty": 3, "offers": [O("A", 18.75, 25), O("B", 8.00)]}], PK)
check("E23 a pack size one vendor does not state → quantity in each vendor's packs, flagged",
      p15["lines"][0]["basis"] == "packs" and p15["lines"][0]["packs"] == 3
      and any("pack size not stated" in f for f in p15["lines"][0]["flags"]), p15["lines"][0])
# unknown fee
p16 = L.optimize_cart([{"key": "x", "qty": 1, "offers": [O("A", 20)]}], {"A": VT("A", 50, None)})
check("E24 under threshold with no fee set → shipping NOT included, and said", p16["shipping_total"] == 0
      and not p16["vendors"][0]["shipping_known"] and p16["flags"], p16["flags"])
# ties → fewer vendors
p17 = L.optimize_cart([{"key": "x", "qty": 1, "offers": [O("A", 10), O("B", 10)]},
                       {"key": "y", "qty": 1, "offers": [O("A", 10), O("B", 10)]}], {"A": VT("A"), "B": VT("B")})
check("E25 a tie is broken toward fewer vendors", len(p17["vendors"]) == 1)
# single-vendor baseline
check("E26 best single vendor baseline + saving", plan["baselines"]["best_single_vendor"] == 71.0 and plan["savings_vs_single_vendor"] == 0.0)
p18 = L.optimize_cart([{"key": "x", "qty": 0, "offers": [O("A", 1)]}], {"A": VT("A")})
check("E27 zero quantity is unfillable, not a $0 line", not p18["lines"] and p18["unfillable"][0]["reason"] == "quantity is zero")
p19 = L.optimize_cart([{"key": "x", "qty": 1, "offers": [O("Z", 1)]}], {"A": VT("A")})
check("E28 an offer from a vendor that is not set up is never ordered", not p19["lines"]
      and "not set up" in p19["unfillable"][0]["reason"])
# the subset search (large carts) never beats the true optimum and matches it on these instances
rng = random.Random(1021)
worse = better = same = 0
for _ in range(150):
    nv, ni = rng.randint(2, 4), rng.randint(1, 6)
    vv = {f"V{j}": VT(f"V{j}", rng.choice([0, 25, 50, 75, 100]), rng.choice([None, 5, 9.99, 15]), 1, rng.randint(1, 7))
          for j in range(nv)}
    its = []
    for i in range(ni):
        offs = [O(v, round(rng.uniform(3, 60), 2), rng.choice([None, 10, 25]), rng.choice(["in_stock"] * 4 + ["out_of_stock"]))
                for v in vv if rng.random() < 0.85]
        its.append({"key": str(i), "qty": rng.randint(1, 30), "offers": offs})
    ex = L.optimize_cart(its, vv)
    ss = L.optimize_cart(its, vv, exact_limit=0)
    if ex["lines"] or ss["lines"]:
        if ss["total"] < ex["total"] - 1e-9:
            better += 1
        elif abs(ss["total"] - ex["total"]) < 1e-9:
            same += 1
        else:
            worse += 1
check("E29 the subset search is never cheaper than the exact optimum (consistency)", better == 0, (better, same, worse))
check("E30 ...and finds the optimum on ≥95% of 150 random carts", same >= 0.95 * (same + worse), (same, worse))
# drafts
drafts = L.po_drafts_from_plan(plan)
check("E31 one PO draft per vendor", len(drafts) == len(plan["vendors"]) == 1)
d = drafts[0]
check("E32 PO total = subtotal + shipping estimate", d["total"] == round(d["subtotal"] + d["shipping_estimate"], 2) == 71.0)
check("E33 PO lines order whole packs; meta keeps each line's link for the recipe",
      all(isinstance(ln["qty_ordered"], int) for ln in d["lines"]) and all(m["url"] for m in d["meta"]["lines"]))
d12 = L.po_drafts_from_plan(p12)[0]
check("E34 pack basis carried to the PO (4 packs × $18.75)", d12["lines"][0]["qty_ordered"] == 4 and d12["lines"][0]["unit_cost"] == 18.75)

# ══ F. THE ORDERING RECIPE ═══════════════════════════════════════════════════════════════════════════════
print("§F recipe")
RECIPE = {"cart_url": "https://shop.example/cart",
          "line_steps": [{"action": "goto", "url": "{url}"}, {"action": "fill", "selector": "input[name=qty]", "value": "{qty}"},
                         {"action": "click", "selector": "button.add"}, {"action": "wait", "ms": 500}],
          "review_steps": [{"action": "goto", "url": "{cart_url}"}],
          "cart_total_pattern": r"Sub-Total:\s*(\$[\d,.]+)",
          "confirmation": {"url_pattern": "checkout_success"}}
HOSTS = {"shop.example"}
rec, e = L.parse_recipe(RECIPE, HOSTS)
check("F1 a complete recipe parses", not e and len(rec["line_steps"]) == 4, e)
check("F2 recipe status 'cart' (no submit step → the human submits in the live window)", L.recipe_status(rec)["level"] == "cart")
rec_full, _ = L.parse_recipe({**RECIPE, "submit_steps": [{"action": "click", "selector": "#confirm"}]}, HOSTS)
check("F3 with a submit step → 'full'", L.recipe_status(rec_full)["level"] == "full")
check("F4 no recipe → 'none', said plainly", L.recipe_status(L.parse_recipe(None)[0])["level"] == "none")
check("F5 cart_url only → 'open_cart'", L.recipe_status(L.parse_recipe({"cart_url": "https://shop.example/c"})[0])["level"] == "open_cart")
_, e = L.parse_recipe({"line_steps": [{"action": "eval", "selector": "x"}]})
check("F6 an unknown action is refused", any("unknown action" in x for x in e), e)
_, e = L.parse_recipe({"line_steps": [{"action": "click"}]})
check("F7 click without a selector refused", any("needs a selector" in x for x in e), e)
_, e = L.parse_recipe({"line_steps": [{"action": "goto", "url": "{password}"}]})
check("F8 an unknown placeholder refused", any("unknown placeholder" in x for x in e), e)
_, e = L.parse_recipe({"line_steps": [{"action": "goto", "url": "https://evil.example/x"}]}, HOSTS)
check("F9 a goto off the vendor's portal refused", any("not this vendor's portal" in x for x in e), e)
rs = L.render_step({"action": "goto", "url": "https://shop.example/search?q={sku}"}, {"sku": "A B/1"})
check("F10 a placeholder inside a URL is encoded", rs["url"] == "https://shop.example/search?q=A%20B%2F1", rs)
rs = L.render_step({"action": "fill", "selector": "x", "value": "{qty}"}, {"qty": 4})
check("F11 a fill value is literal", rs["value"] == "4")
rs = L.render_step({"action": "goto", "url": "{url}"}, {"url": "https://shop.example/p?id=1&x=2"})
check("F12 a whole-{url} step keeps the link as is", rs["url"] == "https://shop.example/p?id=1&x=2")
check("F13 url_allowed: own host yes, other host / javascript: no",
      L.url_allowed("https://shop.example/a", HOSTS) and not L.url_allowed("https://evil.example", HOSTS)
      and not L.url_allowed("javascript:alert(1)", HOSTS))

# ══ G/H. CART TOTAL + CONFIRMATION CAPTURE ══════════════════════════════════════════════════════════════
print("§G cart total · §H confirmation")
check("G1 default wording: sub-total first", L.parse_cart_total("Items 3  Sub-Total: $123.45  Total: $140.00") == 123.45)
check("G2 configured pattern wins", L.parse_cart_total("Amount due USD $77.10", r"due USD\s*(\$[\d.]+)") == 77.1)
check("G3 no stated total → None (never guessed)", L.parse_cart_total("Your cart") is None)
ok_page = "Thank you for your order! Your order number is 104233. Order Total: $212.40"
d1 = L.detect_confirmation("https://shop.example/index.php?main_page=checkout_success", ok_page)
check("H1 a confirmation page is detected with the number and total", d1["confirmed"] and d1["order_ref"] == "104233"
      and d1["total"] == 212.40, d1)
cart_page = "Shopping Cart — Order #: pending — Order Total: $212.40 — Checkout"
d2 = L.detect_confirmation("https://shop.example/cart", cart_page)
check("H2 NEGATIVE: a cart page that prints 'Order #' and a total is NOT a confirmation", not d2["confirmed"] and d2["order_ref"] is None, d2)
d3 = L.detect_confirmation("https://shop.example/index.php?main_page=checkout_success", "Done.", {"url_pattern": "checkout_success"})
check("H3 the configured URL pattern alone confirms (number then typed by hand)", d3["confirmed"] and d3["order_ref"] is None, d3)
d4 = L.detect_confirmation("https://s.example/done", "Order received. Ref: WS-88121", {"ref_pattern": r"Ref:\s*([A-Z0-9-]+)"})
check("H4 a vendor-configured reference pattern", d4["confirmed"] and d4["order_ref"] == "WS-88121", d4)
ev, e = L.manual_confirmation("  ", None)
check("H5 manual fallback: a blank number is refused", e)
ev, e = L.manual_confirmation("A-1", "$99.10", "phoned in", "Sam")
check("H6 manual fallback: typed number + total recorded with who/when/method", not e and ev["order_ref"] == "A-1"
      and ev["total"] == 99.1 and ev["method"] == "manual" and ev["captured_by"] == "Sam" and ev["captured_at"], ev)
_, e = L.manual_confirmation("A-1", "lots")
check("H7 a non-money total refused", e)

# ══ I. ATTENTION + DASHBOARD TILES ══════════════════════════════════════════════════════════════════════
print("§I attention + summary tiles")
from datetime import datetime, timezone, timedelta  # noqa: E402
NOW = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)
VA = {"id": "a", "is_price_source": True, "is_active": True, "free_shipping_threshold": None, "delivery_days_max": 3,
      "data_source_id": "ds", "portal_config": {}}
att = L.vendor_attention(VA, {"has_password": True, "auth_status": "authenticated"}, (NOW - timedelta(days=10)).isoformat(), NOW)
codes = {a["code"] for a in att}
check("I1 missing threshold is a warning", "no_threshold" in codes, codes)
check("I2 a 10-day-old catalog is stale (default 7)", "stale_catalog" in codes, codes)
check("I3 no recipe is INFO (the human finishes the order) — not a warning",
      any(a["code"] == "no_recipe" and a["severity"] == "info" for a in att))
att2 = L.vendor_attention({**VA, "free_shipping_threshold": 0, "portal_config": {"stale_after_days": 14}},
                          {"has_password": True, "auth_status": "needs_2fa", "auth_message": "sign in again"}, (NOW - timedelta(days=10)).isoformat(), NOW)
check("I4 per-vendor stale_after_days config; a login needing sign-in is a warning",
      {a["code"] for a in att2 if a["severity"] == "warn"} == {"login_error"}, att2)
check("I5 a plain roster vendor needs no attention", L.vendor_attention({"id": "p", "is_active": True}, None, None, NOW) == [])
orders = [
    {"status": "submitted", "submitted_at": "2026-09-10T10:00:00+00:00", "total": 100, "vendor_order_total": 97.5,
     "created_at": "2026-09-10T09:00:00+00:00", "supply_meta": {"savings": 6.0}},
    {"status": "draft", "total": 40, "created_at": "2026-09-24T09:00:00+00:00", "order_date": "2026-09-24", "supply_meta": {"savings": 2.0}},
    {"status": "received", "submitted_at": "2026-08-30T10:00:00+00:00", "total": 500, "created_at": "2026-08-30T09:00:00+00:00",
     "supply_meta": {"savings": 50}},
    {"status": "cancelled", "total": 70, "created_at": "2026-09-20T09:00:00+00:00", "supply_meta": {"savings": 9}},
    {"status": "partially_received", "submitted_at": "2026-09-12T10:00:00+00:00", "total": 60,
     "created_at": "2026-09-12T09:00:00+00:00", "supply_meta": {}}]
tiles = L.summary_tiles(orders, {"a": att, "b": [{"severity": "info"}], "c": []}, NOW)
check("I6 open orders = draft + submitted + partially received", tiles["open_orders"] == 3, tiles)
check("I7 spend MTD uses the vendor's confirmed total, excludes last month and drafts", tiles["spend_mtd"] == 157.5, tiles)
check("I8 savings MTD excludes cancelled and last month", tiles["savings_mtd"] == 8.0, tiles)
check("I9 vendors needing attention counts warnings only", tiles["vendors_needing_attention"] == 1, tiles)

# ══ K. THE ORDER SESSION ON A FAKE PAGE — it builds the cart and NEVER submits on its own ══════════════
print("§K order session (fake page, no browser)")


class FakeEl:
    def __init__(self, page, sel):
        self.page, self.sel = page, sel

    def click(self, timeout=None):
        self.page.log.append(("click", self.sel))
        if self.sel == "#confirm":
            self.page.url = "https://shop.example/index.php?main_page=checkout_success"
            self.page.text = "Thank you for your order. Order number: 55012. Total: $71.00"

    def fill(self, v):
        self.page.log.append(("fill", self.sel, v))

    def select_option(self, v):
        self.page.log.append(("select", self.sel, v))

    def inner_text(self):
        return self.page.text


class FakeFrame:
    def __init__(self, page):
        self.page, self.url, self.name = page, "https://shop.example/", ""

    def query_selector(self, sel):
        return None if sel in self.page.missing else FakeEl(self.page, sel)

    def evaluate(self, js, *a):
        return self.page.text


class FakeKb:
    def __init__(self, page):
        self.page = page

    def press(self, k):
        self.page.log.append(("press", k))


class FakePage:
    def __init__(self):
        self.url, self.text, self.log, self.missing = "https://shop.example/", "", [], set()
        self.frames = [FakeFrame(self)]
        self.keyboard = FakeKb(self)

    def goto(self, url, **k):
        self.log.append(("goto", url))
        self.url = url
        if "cart" in url:
            self.text = "Shopping Cart  Sub-Total: $71.00  Checkout"

    def wait_for_timeout(self, ms):
        pass

    def wait_for_load_state(self, *a, **k):
        pass

    def screenshot(self, **k):
        return b"\xff\xd8jpeg"


VEND = {"id": "v-1", "name": "Vendor One", "portal_url": "https://shop.example/login", "catalog_urls": [],
        "portal_config": {"ordering": RECIPE}}
LINES = [{"line_no": 1, "url": "https://shop.example/p/1", "qty": 2, "sku": "S1", "name": "Box"},
         {"line_no": 2, "url": "https://evil.example/p/2", "qty": 1, "sku": "S2", "name": "Tape"}]
pull, st = P.order_session(None, "org", VEND, "po-1", LINES)
pg = FakePage()
r = pull(pg)
check("K1 cart mode: the good line is added (goto its link, fill {qty}=2, click add)",
      ("goto", "https://shop.example/p/1") in pg.log and ("fill", "input[name=qty]", "2") in pg.log and ("click", "button.add") in pg.log, pg.log)
check("K2 a line whose link is off the vendor's portal is refused, not opened", ("goto", "https://evil.example/p/2") not in pg.log
      and r["lines_added"] == 1 and r["lines_failed"][0]["line_no"] == 2, r["lines_failed"])
check("K3 the review page is opened and the vendor's cart total captured", pg.url == "https://shop.example/cart" and r["vendor_cart_total"] == 71.0, r)
check("K4 a screenshot is captured as evidence", bool(r["shot"]))
check("K5 cart mode NEVER runs a submit step", not any(x[0] == "click" and x[1] == "#confirm" for x in pg.log))
r2 = pull(pg)
check("K6 pulling again in cart mode still never submits", not any(x[1:2] == ("#confirm",) for x in pg.log) and r2["mode"] == "cart")
st["mode"] = "submit"
r3 = pull(pg)
check("K7 'submit' with no submit step configured is REFUSED", r3["ok"] is False and "no submit step" in r3["error"], r3)
check("K8 ...and flips back to capture (a submit never repeats by itself)", st["mode"] == "capture")
r4 = pull(pg)
check("K9 capture on a cart page: not confirmed, nothing claimed", r4["mode"] == "capture" and r4["confirmed"] is False, r4)
VEND2 = {**VEND, "portal_config": {"ordering": {**RECIPE, "submit_steps": [{"action": "click", "selector": "#confirm"}]}}}
pull2, st2 = P.order_session(None, "org", VEND2, "po-2", LINES[:1])
pg2 = FakePage()
pull2(pg2)
check("K10 with a submit step, cart mode still does not submit", ("click", "#confirm") not in pg2.log)
st2["mode"] = "submit"
r5 = pull2(pg2)
check("K11 the explicit submit runs the configured step once and reads the confirmation",
      pg2.log.count(("click", "#confirm")) == 1 and r5["confirmed"] and r5["order_ref"] == "55012" and r5["total"] == 71.0, r5)
pull3, _ = P.order_session(None, "org", {**VEND, "portal_config": {}}, "po-3", LINES[:1])
pg3 = FakePage()
r6 = pull3(pg3)
check("K12 no recipe: the session adds nothing, opens nothing, and says the human finishes", r6["lines_added"] == 0
      and not pg3.log and r6["recipe_level"] == "none" and "yourself" in r6["status"], r6)
saved = []
S_save = S.save_cart_evidence
S.save_cart_evidence = lambda c, o, p, ev: saved.append((o, p, ev))
P.cart_persist(None, "org", "po-1")(r)
P.cart_persist(None, "org", "po-1")(r4)
S.save_cart_evidence = S_save
check("K13 the automatic cart build's evidence is stored on the PO (org-scoped); a capture is not written by it",
      len(saved) == 1 and saved[0][0] == "org" and saved[0][2]["vendor_cart_total"] == 71.0, saved)

# ══ J. RULE TWO + GATING ════════════════════════════════════════════════════════════════════════════════
print("§J RULE TWO + gating")
from app.modules.core import verticals as VZ  # noqa: E402
vkeys = [v["key"] for v in VZ.HOUSE_VERTICALS]
kit_hosts = {re.sub(r"^www\.", "", re.match(r"https?://([^/]+)", u).group(1)).split(".")[-2]
             for v in kit_cfg["vendors"] for u in v["start_urls"]}
for fn in sorted(os.listdir(SUP)):
    if fn.endswith(".py"):
        body = src(SUP, fn).lower()
        bad = [k for k in vkeys if k in body] + [h for h in kit_hosts if h in body] + [v["key"] for v in kit_cfg["vendors"] if v["key"] in body]
        check(f"J1 supply/{fn}: no vertical, vendor or vendor host spelled", not bad, bad)
rsrc = src(SUP, "router.py")
check("J2 the router is gated by require_module('supply_ordering')", 'dependencies=[Depends(require_module("supply_ordering"))]' in rsrc)
import ast  # noqa: E402
_tree = ast.parse(rsrc)
handlers = []
for _n in _tree.body:
    if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and isinstance(d.func.value, ast.Name)
            and d.func.value.id == "router" for d in _n.decorator_list):
        _args = _n.args.args
        _defs = dict(zip([a.arg for a in _args[len(_args) - len(_n.args.defaults):]], _n.args.defaults))
        _org = _defs.get("org_id")
        handlers.append((_n.name, isinstance(_org, ast.Name) and _org.id == "ORG_ID"))
check("J3 endpoints found", len(handlers) >= 15, len(handlers))
no_org = [n for n, has in handlers if not has]
check("J4 every endpoint takes the middleware-rewritten org_id", not no_org, no_org)

# ══ L. LOCKS — one home per fact ═════════════════════════════════════════════════════════════════════════
print("§L locks")
APP = os.path.join(HERE, "app")
writers = []
for dp, _d, files in os.walk(APP):
    for fn in files:
        if fn.endswith(".py"):
            b = src(dp, fn)
            if "vendor_catalog_price" in b:
                writers.append(os.path.relpath(os.path.join(dp, fn), APP))
check("L1 the snapshot table is named in exactly one code file (+ the lineage registry)",
      sorted(writers) == sorted([os.path.join("modules", "supply", "store.py"),
                                 os.path.join("modules", "commcalc", "data_lineage_registry.py")]), writers)
ssrc = src(SUP, "store.py")
_writes = [m.start() for m in re.finditer(r"t\(client, CATALOG_TABLE\)\.(?:insert|upsert|update|delete)\(", ssrc)]
_owner = [ssrc[:i].rsplit("\ndef ", 1)[-1].split("(")[0] for i in _writes]
check("L2 store.py writes the catalog table in ONE function (land_catalog)", _owner == ["land_catalog"], _owner)
psrc = src(SUP, "portal.py")
check("L3 both ingest routes call THE lander (kit upload + portal read)",
      "store.land_catalog(" in rsrc.split("async def catalog_upload")[1].split("\n@router")[0]
      and "store.land_catalog(" in psrc.split("def catalog_pull_on_page")[1].split("\ndef ")[0])
ccsrc = src(APP, "modules", "commcalc", "router.py")
check("L4 commcalc dereferences the supply connector kind (no second spelling)",
      '"supply_vendor"' not in ccsrc and "'supply_vendor'" not in ccsrc
      and "from app.modules.supply.portal import SUPPLY_PROCESSOR as _SUPPLY_PROCESSOR" in ccsrc
      and "_SOURCE_SCRAPERS[_SUPPLY_PROCESSOR] = _supply_catalog_scraper" in ccsrc)
live_pull = ccsrc.split("def _live_pull(")[1].split("\ndef ")[0]
check("L5 a supply login's live session pulls the CATALOG, before the VidaPay fallback",
      "_sup.is_supply_source(proc)" in live_pull and live_pull.index("_sup.is_supply_source") < live_pull.rindex("vp._pull_all_reports_on_page("))
check("L6 the order reuses the mig-301 home: next_po_number + the lifecycle rule",
      "_next_po_number" in ssrc and "_validate_status_transition" in ssrc and "purchase_order" in ssrc)
check("L7 the login reuses commcalc's own writer + the live-login path (no second credential store / login engine)",
      "save_data_source(" in rsrc and "live_login_start(" in rsrc and "live_login.start_session(" in rsrc
      and "password" not in re.sub(r"#.*", "", src(REPO, "database", "migrations", "1021_supply_ordering.sql").lower()).split("revert")[0]
      .replace("credential", ""))
mig = src(REPO, "database", "migrations", "1021_supply_ordering.sql")
check("L8 mig 1021 creates exactly ONE table (the snapshot); the vendor is po_vendor, extended",
      re.findall(r"CREATE TABLE IF NOT EXISTS ([\w.]+)", mig) == ["commcalc.vendor_catalog_price"]
      and "ALTER TABLE commcalc.po_vendor ADD COLUMN IF NOT EXISTS free_shipping_threshold" in mig)
check("L9 mig 1021 is additive + idempotent with REVERT notes, RLS on, org index",
      "-- REVERT" in mig and "ENABLE ROW LEVEL SECURITY" in mig and "ix_vcp_org ON commcalc.vendor_catalog_price (org_id)" in mig
      and not re.search(r"^\s*(DROP|DELETE|TRUNCATE|UPDATE)\b", mig, re.M | re.I)
      and all("IF NOT EXISTS" in ln for ln in mig.splitlines() if re.match(r"\s*(ALTER TABLE .* ADD COLUMN|CREATE (TABLE|INDEX))", ln)))
row = {"id": "ds", "password": "x", "session_state": "{...}", "pending_state": "y", "totp_secret": "z", "username": "u"}
pub = S.public_login(row)
check("L10 a login leaves the store with presence flags only — every secret dropped",
      pub["has_password"] and pub["has_session"] and not ({"password", "session_state", "pending_state", "totp_secret"} & set(pub)), pub)
check("L11 the router never returns the full login row", "return" not in "".join(
      ln for ln in rsrc.splitlines() if "login_row_full" in ln))
check("L12 ordering_logic re-implements no pricing primitive (it imports THE one copy)",
      "import pricing_core as core" in src(SUP, "ordering_logic.py")
      and not re.search(r"^def (parse_money|parse_pack|classify_availability|normalize_sku|group_products)\(", src(SUP, "ordering_logic.py"), re.M))
main_src = src(APP, "main.py")
check("L13 main.py registers the supply router", "from app.modules.supply.router import router as supply_router" in main_src
      and 'app.include_router(supply_router, prefix="/api/v1")' in main_src)
rbac = src(REPO, "frontend", "src", "lib", "rbac.ts")
grp = re.search(r"\{ group: 'Supply Ordering', module: 'supply_ordering', items: \[(.*?)\]\}", rbac, re.S)
check("L14 ONE nav group 'Supply Ordering' (module supply_ordering) with the four pages",
      grp is not None and rbac.count("group: 'Supply Ordering'") == 1
      and all(h in (grp.group(1) if grp else "") for h in ("'/supply/vendors'", "'/supply/compare'", "'/supply/cart'", "'/supply/orders'")))
check("L15 rbac.ts spells no vertical key", not any(f"'{k}'" in rbac or f'"{k}"' in rbac for k in vkeys))
fe = os.path.join(REPO, "frontend", "src", "app", "(platform)", "supply")
pages = sorted(os.path.relpath(os.path.join(dp, f), fe) for dp, _d, fs in os.walk(fe) for f in fs if f == "page.tsx") if os.path.isdir(fe) else []
check("L16 the four pages exist", {os.path.join(p, "page.tsx") for p in ("vendors", "compare", "cart", "orders")} <= set(pages), pages)
fe_src = ("".join(src(fe, p) for p in pages) if pages else "") + src(REPO, "frontend", "src", "components", "supply", "LiveVendorWindow.tsx")
check("L17 the pages (via components/supply/LiveVendorWindow) stream the live session from the EXISTING commcalc live-login endpoints",
      "/api/v1/commcalc/data-sources/" in fe_src and "/live-login`" in fe_src and "/frame?since=" in fe_src and "/input`" in fe_src)
check("L18 no page spells a vertical, vendor or vendor host",
      not [k for k in vkeys + sorted(kit_hosts) + [v["key"] for v in kit_cfg["vendors"]] if k in fe_src.lower()])

# L19–L21 — placing an order SPENDS MONEY: every step that acts at the vendor or records a spend asks the buyer gate,
# and the gate never degrades open on an unresolved caller (an order is never placed on behalf of nobody).
_rs = src(SUP, "router.py")


def _gated(fn_name, source):
    m = re.search(r"def %s\([\s\S]*?\n(?=@router|\Z)" % fn_name, source)
    return bool(m) and "_require_buyer(authorization, org_id)" in m.group(0)


_BUYER = ("open_order_session", "submit_order", "confirm_manual", "order_status")
check("L19 every order step that acts at the vendor or records spend asks _require_buyer",
      all(_gated(f, _rs) for f in _BUYER), [f for f in _BUYER if not _gated(f, _rs)])
_gate = re.search(r"def _require_buyer[\s\S]*?\n\n\ndef ", _rs)
check("L20 the buyer gate refuses an unresolved caller (401/403, never a silent return)",
      bool(_gate) and "raise HTTPException(401" in _gate.group(0) and "raise HTTPException(403" in _gate.group(0)
      and "_can_edit_setting(caller, \"supply_ordering\")" in _gate.group(0)
      and not re.search(r"\n\s+return\s*\n", _gate.group(0)))
check("L21 negative control: an ungated submit is caught",
      not _gated("submit_order", _rs.replace("_require_buyer(authorization, org_id)", "pass")))
check("L22 the 'supply_ordering' settings area is registered (grantable per role)",
      '"key": "supply_ordering"' in src(APP, "modules", "core", "router.py"))

print(f"\n{N - len(FAILS)}/{N} passed")
if FAILS:
    print("FAILED:", *FAILS, sep="\n  - ")
sys.exit(1 if FAILS else 0)
