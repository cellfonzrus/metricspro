"""PROOF — the vendor price comparison pure logic (ONE copy: backend/app/modules/supply/pricing_core.py).

Owner request 2026-09-24 (UPS Store tenant): log in to the ordering vendors, list every product with price and
availability, compare. Phase 1 is a standalone kit the owner runs on their own machine; this harness pins the parts
that decide money: which price is read, what a pack holds, what "in stock" means, which rows are the same product,
which vendor is cheapest, and that the kit carries no vendor name / URL / credential in code (vendors are rows in
vendors.json, logins stay in the owner's local credentials.csv, which is git-ignored).

Phase 2 (index §36) moved the pure logic and the read-only catalog reader into the backend
(backend/app/modules/supply/pricing_core.py + catalog_scrape.py) so the platform and the kit run ONE copy;
the kit loads them by relative path (pricecompare/_shared.py) and build_zip.py bundles them into the zip.
§I is the lock: it fails the build if the kit stops dereferencing the home or a second copy appears.

Stdlib only: `python backend/harness_vendor_price_compare.py`.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
KIT = os.path.join(REPO, "tools", "vendor_price_compare")
HOME = os.path.join(HERE, "app", "modules", "supply")
sys.path.insert(0, HERE)

from app.modules.supply import pricing_core as core  # noqa: E402  (THE one copy)

FAILS = []
N = 0


def check(name, cond, detail=""):
    global N
    N += 1
    if not cond:
        FAILS.append(f"{name} {detail}")
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  → {detail}"))


print("§A money")
check("A1 plain", core.parse_money("Price: $18.75 each") == 18.75)
check("A2 thousands", core.parse_money("$1,234.50") == 1234.5)
check("A3 no decimals", core.parse_money("$20") == 20.0)
check("A4 none", core.parse_money("call for price") is None)
check("A5 all", core.all_money("was $61.00 now $54.00") == [61.0, 54.0])

print("§B pack size")
check("B1 25/bundle", core.parse_pack("12 x 12 x 12 Box - 25/bundle") == 25)
check("B2 36 rolls/case", core.parse_pack("Tape 2in x 110yd - 36 rolls/case") == 36)
check("B3 pack of", core.parse_pack("Mailers, pack of 50") == 50)
check("B4 ct", core.parse_pack("Poly Mailer 10 x 13 - 100 ct") == 100)
check("B5 dims are not a pack", core.parse_pack("Box 12 x 12 x 12") is None)
check("B6 1/RL is no pack", core.parse_pack("Bubble 1/RL") is None)
check("B7 25/BDL", core.parse_pack("25/BDL") == 25)

print("§C availability — unknown is never in stock")
check("C1 units in stock", core.classify_availability("Units in Stock: 340") == ("in_stock", 340))
check("C2 zero units", core.classify_availability("Units in Stock: 0") == ("out_of_stock", 0))
check("C3 out", core.classify_availability("Out of Stock")[0] == "out_of_stock")
check("C4 backorder", core.classify_availability("Backorder")[0] == "backorder")
check("C5 blank", core.classify_availability("") == ("unknown", None))
check("C6 item number not a qty", core.classify_availability("Model: BX121212 Units in Stock: 7") == ("in_stock", 7))

print("§D identity + matching")
check("D1 sku normalise", core.normalize_sku("bx-12.12.12") == "BX121212")
check("D2 sku needs digit", core.normalize_sku("Add") == "")
check("D3 dims sorted", core.dimensions('12 x 10 x 8"') == ("10", "12", "8"))
check("D4 dims inch mark", core.dimensions('Bubble roll 12" x 175\'') == ("12", "175"))


def P(vendor, name, price, sku="", pack=None, avail="in_stock"):
    return {"vendor": vendor, "name": name, "sku": sku, "price": price, "pack_qty": pack if pack else core.parse_pack(name),
            "availability": avail, "url": f"u:{vendor}:{name}"}


a_box = P("a", "Box 12x12x12 200# test", 16.9, "51-1212", 25)
b_box = P("b", "12 x 12 x 12 Corrugated Box - 25/bundle", 18.75, "BX121212")
b_box10 = P("b", "10 x 10 x 10 Corrugated Box - 25/bundle", 14.0, "BX101010")
a_tape = P("a", "Packing tape clear 2 inch 110 yds", 49.5, "51-TAPE", 36)
b_tape = P("b", "Heavy Duty Packing Tape 2in x 110yd - 36 rolls/case", 54.0, "TP2110", avail="out_of_stock")
fa, fb, fb10 = (core.product_features(x) for x in (a_box, b_box, b_box10))
check("D5 same size + word + pack → likely", core.match_score(fa, fb)[0] >= 0.75, core.match_score(fa, fb))
check("D6 different size never matches", core.match_score(fa, fb10) == (0.0, "different size"))
check("D7 same part number exact", core.match_score(core.product_features(P("a", "x", 1, "AB-100")),
                                                    core.product_features(P("b", "totally other", 2, "ab100")))[0] == 1.0)
s, _ = core.match_score(core.product_features(a_tape), core.product_features(b_tape))
check("D8 tape wording differs but matches", s >= 0.45, s)
check("D9 different numbers do not match",
      core.match_score(core.product_features(P("a", "Thermal labels 4 in roll 250", 9)),
                       core.product_features(P("b", "Thermal labels 6 in roll 500", 9)))[0] < 0.45)

print("§E grouping + comparison")
groups = core.group_products([a_box, a_tape, b_box, b_box10, b_tape])
sizes = sorted(len(g["members"]) for g in groups)
check("E1 two pairs + one single", sizes == [1, 2, 2], sizes)
check("E2 one row per vendor per group", all(len(g["members"]) == len(set(g["members"])) for g in groups))
check("E3 deterministic", [sorted(g["members"]) for g in core.group_products([a_box, a_tape, b_box, b_box10, b_tape])]
      == [sorted(g["members"]) for g in groups])
compared, singles = core.comparison_rows(groups, ["a", "b"])
box = next(r for r in compared if r["a"] is a_box)
check("E4 per-unit basis when both packs known", box["basis"] == "per unit")
check("E5 cheapest per unit", box["best_vendor"] == "a" and abs(box["best_price"] - 0.676) < 1e-9, box)
check("E6 savings", abs(box["savings"] - 0.074) < 1e-9, box["savings"])
tape = next(r for r in compared if r["a"] is a_tape)
check("E7 out of stock never wins", tape["best_vendor"] == "a")
check("E8 single listed", singles == [b_box10])
only_cheap_oos = core.comparison_rows(core.group_products([P("a", "Widget blue 5", 10.0, "W5"), P("b", "Widget blue 5", 8.0, "w-5", avail="out_of_stock")]), ["a", "b"])[0][0]
check("E9 cheaper-but-out-of-stock is said", only_cheap_oos["best_vendor"] == "a" and "out of stock" in only_cheap_oos["note"], only_cheap_oos)
mixed = core.comparison_rows(core.group_products([P("a", "Widget blue 5 - 10 ct", 10.0, "W5"), P("b", "Widget blue 5", 8.0, "w-5")]), ["a", "b"])[0][0]
check("E10 listed-price basis when a pack is unknown", mixed["basis"] == "listed price")

print("§F order plan")
plan = core.plan_order([{"search": "12x12x12 box", "qty": 3}, {"search": "packing tape", "qty": 2},
                        {"search": "foam peanuts", "qty": 1}], [a_box, b_box, b_box10, a_tape, b_tape])
check("F1 box → cheapest in stock", plan[0]["pick"] is a_box and plan[0]["line_total"] == 50.7, plan[0]["pick"])
check("F2 size respected (10x10 not offered)", b_box10 not in plan[0]["options"])
check("F3 tape skips out of stock", plan[1]["pick"] is a_tape)
check("F4 no match said", plan[2]["pick"] is None and plan[2]["note"] == "no match found")

print("§G RULE TWO + secrets — vendors are config, logins are never in the repo")
cfg = json.load(open(os.path.join(KIT, "vendors.json")))
hosts = {re.sub(r"^www\.", "", re.match(r"https?://([^/]+)", u).group(1)) for v in cfg["vendors"] for u in v["start_urls"]}
G1_FILES = [os.path.join(HOME, "pricing_core.py"), os.path.join(HOME, "catalog_scrape.py"),
            os.path.join(HOME, "ordering_logic.py"), os.path.join(HOME, "portal.py"), os.path.join(HOME, "router.py"),
            os.path.join(HOME, "store.py")] + \
           [os.path.join(KIT, f) for f in ("pricecompare/core.py", "pricecompare/scrape.py", "pricecompare/report.py",
                                           "pricecompare/_shared.py", "compare_prices.py", "build_zip.py")]
for path in G1_FILES:
    src = open(path).read().lower()
    bad = [h for h in hosts if h.split(".")[-2] in src] + [v["key"] for v in cfg["vendors"] if v["key"] in src]
    check(f"G1 no vendor in {os.path.relpath(path, REPO)}", not bad, bad)
ign = open(os.path.join(KIT, ".gitignore")).read().split()
check("G2 credentials.csv ignored", "credentials.csv" in ign and "output/" in ign)
tpl = open(os.path.join(KIT, "credentials_TEMPLATE.csv")).read().strip().splitlines()
check("G3 template carries no password", all(line.rstrip(",").count(",") <= 1 and line.endswith(",,") for line in tpl[1:]), tpl)
secret_like = re.compile(r"(password|passwd|pwd)\s*[=:]\s*['\"][^'\"]{4,}['\"]", re.I)
leaks = []
for root, dirs, files in os.walk(KIT):
    dirs[:] = [d for d in dirs if d not in (".venv", "output", "__pycache__")]
    for fn in files:
        path = os.path.join(root, fn)
        if fn.endswith((".py", ".json", ".md", ".csv", ".bat", ".sh")) and fn not in ("credentials.csv",) \
                and secret_like.search(open(path, encoding="utf-8").read()):
            leaks.append(os.path.relpath(path, KIT))
check("G4 no hard-coded password anywhere in the kit", not leaks, leaks)

print("§H read-only — the crawler refuses cart / order / logout links (negative controls)")
from app.modules.supply import catalog_scrape as _s  # noqa: E402  (THE one reader; imports no browser at load)
for u in ["https://x/index.php?main_page=product_info&action=buy_now", "https://x/shopping_cart", "https://x/checkout_shipping",
          "https://x/ordadd.html?i=1", "https://x/logoff", "https://x/place_order", "https://x/AddToCart?id=3"]:
    check(f"H1 skip {u.split('/', 3)[3]}", bool(_s.SAFE_SKIP.search(u)))
for u in ["https://x/index.php?main_page=index&cPath=1_115", "https://x/cat.html?c=2", "https://x/index.php?main_page=product_info&products_id=11"]:
    check(f"H2 follow {u.split('/', 3)[3]}", not _s.SAFE_SKIP.search(u))
check("H3 session id stripped", _s.clean_url("https://x/i.php?main_page=index&zenid=abc#top", {"zenid"}) == "https://x/i.php?main_page=index")

print("§I ONE COPY — the kit dereferences the backend home; a second copy of the logic fails the build")
sys.path.insert(0, KIT)
import importlib  # noqa: E402
kcore = importlib.import_module("pricecompare.core")
kscrape = importlib.import_module("pricecompare.scrape")
check("I1 the kit's core IS backend/app/modules/supply/pricing_core.py",
      os.path.samefile(kcore.__file__, os.path.join(HOME, "pricing_core.py")), kcore.__file__)
check("I2 the kit's scrape IS backend/app/modules/supply/catalog_scrape.py",
      os.path.samefile(kscrape.__file__, os.path.join(HOME, "catalog_scrape.py")), kscrape.__file__)
check("I3 the kit's reader uses the kit's (= the one) pricing module", kscrape.core is kcore)
for shim in ("pricecompare/core.py", "pricecompare/scrape.py", "pricecompare/_shared.py"):
    body = open(os.path.join(KIT, shim)).read()
    check(f"I4 {shim} holds no pricing logic", not re.search(r"def (parse_money|parse_pack|classify_availability|"
                                                             r"group_products|comparison_rows|match_score)\b|EXTRACT_JS\s*=", body))

# The signatures of the shared logic. Each may be DEFINED only in its home file.
SIGNATURES = {r"^def parse_pack\(": "pricing_core.py", r"^def classify_availability\(": "pricing_core.py",
              r"^def group_products\(": "pricing_core.py", r"^def comparison_rows\(": "pricing_core.py",
              r"^def match_score\(": "pricing_core.py", r"^def plan_order\(": "pricing_core.py",
              r"^_PACK_PATTERNS\s*=": "pricing_core.py", r"^EXTRACT_JS\s*=": "catalog_scrape.py",
              r"^SAFE_SKIP\s*=": "catalog_scrape.py", r"^class VendorScraper\b": "catalog_scrape.py"}
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "output", "_bundled", "__pycache__", ".next", ".claude"}


def second_copies(root):
    """[(relpath, signature)] for every definition of the shared logic outside its one home. PURE-ish (reads files)."""
    hits = []
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dp, fn)
            try:
                src = open(path, encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                continue
            for sig, home in SIGNATURES.items():
                if re.search(sig, src, re.M) and not os.path.samefile(path, os.path.join(HOME, home)):
                    hits.append((os.path.relpath(path, root), sig))
    return hits


dupes = second_copies(REPO)
check("I5 no second copy of the pricing logic / catalog reader anywhere in the repo", not dupes, dupes)
import tempfile  # noqa: E402
with tempfile.TemporaryDirectory() as td:
    with open(os.path.join(td, "copy_of_core.py"), "w") as fh:
        fh.write("def group_products(x):\n    return x\n")
    with open(os.path.join(td, "fine.py"), "w") as fh:
        fh.write("from pricing_core import group_products\n")
    planted = second_copies(td)
    check("I6 negative control: a planted copy IS caught (and an import is not)",
          [p for p, _ in planted] == ["copy_of_core.py"], planted)
    import zipfile  # noqa: E402
    build = importlib.import_module("build_zip")
    zpath, names = build.build(os.path.join(td, "kit.zip"))
    zn = set(zipfile.ZipFile(zpath).namelist())
    check("I7 the zip bundles the one copy of both shared modules",
          {"vendor_price_compare/pricecompare/_bundled/pricing_core.py",
           "vendor_price_compare/pricecompare/_bundled/catalog_scrape.py"} <= zn, sorted(zn))
    with zipfile.ZipFile(zpath) as z:
        same = z.read("vendor_price_compare/pricecompare/_bundled/pricing_core.py") == \
            open(os.path.join(HOME, "pricing_core.py"), "rb").read()
    check("I8 the bundled pricing_core is byte-identical to the home", same)
    check("I9 the zip never carries a login or results", not any(n.endswith(("credentials.csv", "shopping_list.csv"))
                                                                 or "/output/" in n for n in zn), sorted(zn))
ign2 = open(os.path.join(KIT, ".gitignore")).read().split()
check("I10 the bundled copies and the built zip are git-ignored (never a committed second copy)",
      "pricecompare/_bundled/" in ign2 and "dist/" in ign2)

print(f"\n{N - len(FAILS)}/{N} passed")
sys.exit(1 if FAILS else 0)
