"""PROOF + LOCK — supply: price per each, box strength, minimum order, favourites, vendor delete, the login status
sentence (owner 2026-10-01, index §36 / §41a).

Live defects this reproduces (UPS Store tenant, 2026-10-01):
  1. "$5.4000 per EA … (10/BDL)" was read as a 10-pack: $5.40 shown as $0.54 each, on every such row.
  2. My Box Choice's "24X18X6 200lb UPS BRANDED BOX" was matched to "24X6X18 275#BC (10/BDL)" (same size, a
     different board) instead of UPSCTUPS241806 "24X18X6 200K" — strength was never compared.
  3. A vendor that had just priced 194 items still said "The vendor portal did not accept the saved login".
  + the asks: minimum order quantity, favourites + reorder, supplier / item filters, delete a vendor.

  §A per-each prices (pricing_core.effective_pack) — at landing AND at reading (rows already landed read right)
  §B box strength + synonyms (pricing_core.match_score) on the live rows
  §C minimum order / steps (pricing_core.parse_min_order, ordering_logic.order_packs + the optimizer's line note)
  §D favourites + compare filters (ordering_logic.mark_favorites / filter_compare_rows / clean_favorite_keys)
  §E vendor delete plan (archive when purchase orders name the vendor)
  §F status sentence (commcalc/source_status.with_fresh_message)
  §G source locks — one home for each rule, every caller dereferences it

Run: `python3 harness_supply_favorites_moq.py` from the backend dir. DB-free, stdlib only.
"""
import ast
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from app.modules.supply import pricing_core as core  # noqa: E402
from app.modules.supply import ordering_logic as L  # noqa: E402
from app.modules.commcalc import source_status as SS  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


# The live rows (2026-10-01), verbatim.
S1_EA = {"vendor_id": "s1", "id": "r1", "sku": "UPSCTUPS241806", "price": 3.59, "pack_qty": 15, "item_key": "s:UPSCTUPS241806",
         "name": "Item #: UPSCTUPS241806 Cust Item #: CT-UPS241806 CTN RSC 24X18X6 200K P1C2P (15/BDL)",
         "description": "Item #: UPSCTUPS241806 Cust Item #: CT-UPS241806 CTN RSC 24X18X6 200K P1C2P (15/BDL) $3.5900 per EA Add to Cart"}
S1_BDL = {"vendor_id": "s1", "id": "r2", "price": 51.1, "pack_qty": 10, "item_key": "u:aaa",
          "name": "UPSCT24X6X18 CT-24X6X18",
          "description": "UPSCT24X6X18 CT-24X6X18 CTN FOL 24X6X18 275#BC (10/BDL) 10 $51.10"}
MBC = {"vendor_id": "mbc", "id": "r3", "price": 3.05, "item_key": "u:bbb",
       "name": "24X18X6 200lb UPS BRANDED BOX", "description": "24X18X6 200lb UPS BRANDED BOX $3.05 DETAILS"}

print("§A per-each prices")
check("A1 '$3.5900 per EA' is a per-each price", core.price_is_per_unit(S1_EA["description"]))
check("A2 '10 $51.10' is not", not core.price_is_per_unit(S1_BDL["description"]))
check("A3 per-each + (15/BDL) → no pack, sold in steps of 15", core.effective_pack(S1_EA) == (None, 15), core.effective_pack(S1_EA))
check("A4 a bundle price keeps its pack", core.effective_pack(S1_BDL) == (10, None))
o = L.offer_from_row(S1_EA)
check("A5 a row LANDED before the rule reads right (offer_from_row): $3.59 each, step 15",
      o["pack_qty"] is None and o["order_multiple"] == 15 and core.unit_price(o) == 3.59, (o["pack_qty"], o["order_multiple"]))
row, _ = L.normalize_catalog_row({k: v for k, v in S1_EA.items() if k not in ("id", "vendor_id", "item_key")})
check("A6 landing applies the same rule (normalize_catalog_row)", row["pack_qty"] is None and row["order_multiple"] == 15, row)

print("§B box strength")
check("B1 strengths read", (core.strength(MBC["name"]), core.strength(S1_EA["name"]), core.strength(S1_BDL["description"]))
      == ("200", "200", "275"))
offers = [L.offer_from_row(r) for r in (S1_BDL, MBC, S1_EA)]
groups = core.group_products(offers)
pair = next((g for g in groups if set(g["members"]) == {"s1", "mbc"}), None)
check("B2 the 200lb box pairs with UPSCTUPS241806 (was: the 275# box)",
      pair is not None and pair["members"]["s1"]["row_id"] == "r1", [(list(g["members"]), g["method"]) for g in groups])
check("B3 …as 'likely' (same size + strength)", pair is not None and core.confidence_label(pair["score"]) == "likely"
      and pair["method"] == "same size + strength", pair and (pair["score"], pair["method"]))
check("B4 same size, different strength never match",
      core.match_score(core.product_features(L.offer_from_row(MBC)), core.product_features(L.offer_from_row(S1_BDL)))[1]
      == "different strength")

print("§C minimum order / steps")
check("C1 'Min: 10 Units: 5'", core.parse_min_order("Min: 10 Units: 5") == (10, 5))
check("C2 'Units in Stock: 340' is stock, not a step", core.parse_min_order("Units in Stock: 340") == (None, None))
check("C3 'minimum 2 days' is a lead time", core.parse_min_order("minimum 2 days delivery") == (None, None))
check("C4 'MOQ 50' / 'sold in multiples of 6'", core.parse_min_order("MOQ 50") == (50, None)
      and core.parse_min_order("sold in multiples of 6") == (None, 6))
check("C5 order_packs: 3 wanted, min 10 step 5 → 10", L.order_packs(3, None, "packs", 10, 5) == 10)
check("C6 order_packs: 12 wanted, steps of 15 → 15", L.order_packs(12, None, "packs", None, 15) == 15)
check("C7 nothing wanted → nothing ordered (a minimum never conjures a line)", L.order_packs(0, None, "packs", 10, 5) == 0)
check("C8 units basis still rounds to whole packs first", L.order_packs(30, 25, "units") == 2)
plan = L.optimize_cart([{"key": "k", "label": "box", "qty": 1, "offers": [dict(L.offer_from_row(S1_EA), vendor="s1")]}],
                       {"s1": {"name": "Supply One", "free_shipping_threshold": 100, "delivery_days_max": 5}})
ln = plan["lines"][0] if plan.get("lines") else {}
check("C9 the cart orders the vendor's step (15) for 1 wanted, and says why",
      ln.get("packs") == 15 and any("raised from 1 to 15" in f for f in ln.get("flags", [])), ln)
drafts = L.po_drafts_from_plan(plan)
check("C10 the purchase order and the vendor cart carry the raised quantity",
      drafts and drafts[0]["lines"][0]["qty_ordered"] == 15 and drafts[0]["meta"]["lines"][0]["qty"] == 15, drafts)

print("§D favourites + filters")
rows = [{"product": "box", "offers": {"s1": {"item_key": "s:UPSCTUPS241806"}, "mbc": {"item_key": "u:bbb"}}},
        {"product": "tape", "offers": {"mbc": {"item_key": "u:ccc"}}}]
L.mark_favorites(rows, [{"vendor_id": "mbc", "item_key": "u:bbb", "reorder_qty": 4}])
check("D1 a row is a favourite when ANY of its offers is starred", rows[0]["favorite"] and not rows[1]["favorite"])
check("D2 …and carries the saved reorder quantity", rows[0]["reorder_qty"] == 4)
check("D3 favourites-only filter", [r["product"] for r in L.filter_compare_rows(rows, favorites_only=True)] == ["box"])
check("D4 supplier filter keeps rows that supplier sells", [r["product"] for r in L.filter_compare_rows(rows, vendor="s1")] == ["box"])
check("D5 keys are validated and de-duplicated",
      L.clean_favorite_keys([{"vendor_id": "a", "item_key": "x"}, {"vendor_id": "a", "item_key": "x"}, {"vendor_id": ""}, "junk"])
      == [{"vendor_id": "a", "item_key": "x"}])

print("§E vendor delete")
check("E1 no purchase orders → delete", L.vendor_delete_plan(0)["mode"] == "delete")
check("E2 purchase orders name it → archive (history keeps its vendor)", L.vendor_delete_plan(3)["mode"] == "archive")

print("§F the login status sentence")
check("F1 a green patch replaces the stale failure sentence",
      SS.with_fresh_message({"auth_status": "authenticated", "last_status": "Read 400 pages"})["auth_message"] == SS.SIGNED_IN)
check("F2 a green patch with its own sentence keeps it",
      SS.with_fresh_message({"auth_status": "authenticated", "auth_message": "Signed in via live"})["auth_message"] == "Signed in via live")
check("F3 a failure keeps its own reason", "auth_message" not in SS.with_fresh_message({"auth_status": "needs_2fa"}))

print("§G source locks")
ol = (HERE / "app/modules/supply/ordering_logic.py").read_text()
router_src = (HERE / "app/modules/supply/router.py").read_text()
store_src = (HERE / "app/modules/supply/store.py").read_text()
cc = (HERE / "app/modules/commcalc/router.py").read_text()
check("G1 the optimizer computes quantity only through order_packs(..., min, multiple)",
      re.search(r"packs = order_packs\(qty, o\[\"pack_qty\"\], basis, o\[\"min_order_qty\"\], o\[\"order_multiple\"\]\)", ol) is not None
      and len(re.findall(r"math\.ceil\(", ol)) == 2)
check("G2 landing and reading both use pricing_core.effective_pack",
      re.search(r"def normalize_catalog_row[\s\S]*?core\.effective_pack\(", ol) is not None
      and re.search(r"def offer_from_row[\s\S]*?core\.effective_pack\(", ol) is not None)
check("G3 land_catalog probes the order-rule columns (any-subset rule)",
      re.search(r"def land_catalog[\s\S]*?present_columns\([\s\S]*?ORDER_RULE_COLS", store_src) is not None)
check("G4 the favourites list IS the comparison (no second comparison route)",
      "filter_compare_rows(out" in router_src and not re.search(r'@router\.get\("/favorites', router_src))
check("G5 delete decides through vendor_delete_plan", re.search(r"def delete_vendor[\s\S]*?L\.vendor_delete_plan\(", router_src) is not None)
check("G6 the one status writer applies the sentence rule", re.search(r"def _source_stamp[\s\S]{0,900}?with_fresh_message\(patch\)", cc) is not None)
# Every authenticated patch WITHOUT its own sentence must go through _source_stamp.
bad = []
for path in (HERE / "app/modules/commcalc").glob("*.py"):
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    stamped = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == "_source_stamp":
            for a in n.args:
                stamped.add(id(a))
    for n in ast.walk(tree):
        if isinstance(n, ast.Dict):
            keys = [k.value for k in n.keys if isinstance(k, ast.Constant)]
            vals = {k.value: v for k, v in zip(n.keys, n.values) if isinstance(k, ast.Constant)}
            if ("auth_status" in keys and isinstance(vals.get("auth_status"), ast.Constant)
                    and vals["auth_status"].value == "authenticated" and "auth_message" not in keys and id(n) not in stamped):
                bad.append(f"{path.name}:{n.lineno}")
check("G7 no authenticated status is written without a fresh sentence outside _source_stamp", not bad, bad)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
