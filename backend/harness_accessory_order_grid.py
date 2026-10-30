"""BUILD LOCK + PROOF — one phone is one row, and the order is bought at the grain it is bought at
(index §COMMCALC-49.11 / §COMMCALC-49.12).

WHY A LOCK AND NOT JUST A MODULE. CLAUDE.md: *"A design fix ships with a check that FAILS THE BUILD
if a caller stops dereferencing the shared fact, or if a second copy appears."* Two facts on this
report have already been copied once each — the velocity arithmetic and the device-department
vocabulary — and the second copy of each shipped with a docstring explaining that it was a copy. The
two facts this file locks are the next candidates:

  · **which sold lines are the same phone** (`device_model_family`), because every report that lists
    models is one `.replace('128GB','')` away from its own answer, and
  · **what kinds of accessory exist** (`accessory_order_catalog`), which sits ONE WORD away from
    `accessory_catalog`, the classifier of SOLD accessory lines. That near-miss is real: writing
    this feature overwrote that module once before the name was fixed, so the lock asserts the two
    stay distinct and neither imports the other.

Run: `cd backend && python3 harness_accessory_order_grid.py`
DB-FREE. No client, no network, no environment — drive it under `env -i`.
"""
import ast
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import accessory_order_catalog as cat   # noqa: E402
from app.modules.commcalc import accessory_order_plan as aop      # noqa: E402
from app.modules.commcalc import device_model_family as dmf       # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HERE = os.path.dirname(os.path.abspath(__file__))
FAMILY_HOME = os.path.join(HERE, "app", "modules", "commcalc", "device_model_family.py")
CATALOG_HOME = os.path.join(HERE, "app", "modules", "commcalc", "accessory_order_catalog.py")
#: Every file that asks "which lines are the same phone". A caller added here without
#: `resolve_colour_tokens` fails A4.
FAMILY_CALLERS = (os.path.join(HERE, "app", "modules", "commcalc", "router.py"),)

#: Words that would mean a tenant's own COLOUR vocabulary had been written into code. A colour word
#: in a feed is one tenant's spelling (`BLK` here, `Midnight` there), and CLAUDE.md's "every feature
#: is for EVERY tenant" rule bans a house default that is really one tenant's vocabulary.
COLOUR_WORDS = ("blk", "black", "midnight", "jetblack", "navy", "pink", "graphite", "titanium",
                "dblue", "corange", "cloud silver", "space gray", "space grey")
#: Words that would mean a carrier, a tenant or a brand had been written into code (RULE TWO).
BRAND_WORDS = ("boost", "verizon", "iphone", "samsung", "galaxy", "motorola", "moto", "apple",
               "luxelink", "cellfonz", "vzone", "tcl", "celero", "nxtpaper", "ppd-usa")


def _string_values(path):
    """Every string CONSTANT in the file, excluding docstrings. AST, not a text search, so a module
    header may still NAME what a defect cost without failing its own lock."""
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
               and isinstance(body[0].value.value, str):
                docs.add(id(body[0].value))
    return [n.value.lower() for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def _imports(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            for a in n.names:
                out.add(f"{n.module or ''}.{a.name}")
        elif isinstance(n, ast.Import):
            for a in n.names:
                out.add(a.name)
    return out


def _calls(path, name):
    tree = ast.parse(open(path, encoding="utf-8").read())
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if (isinstance(f, ast.Name) and f.id == name) or \
               (isinstance(f, ast.Attribute) and f.attr == name):
                return True
    return False


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("== A. ONE PHONE IS ONE ROW, AND THE COLOUR WORDS ARE THE ORG'S ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_sig = inspect.signature(dmf.family_of)
check("A1 `colour_tokens` is KEYWORD-ONLY and REQUIRED — a caller that has not asked the org for its "
      "colour words cannot answer which lines are the same phone. An optional default holding one "
      "tenant's spellings is the exact defect the owner reported on 2026-10-30",
      _sig.parameters["colour_tokens"].default is inspect.Parameter.empty
      and _sig.parameters["colour_tokens"].kind is inspect.Parameter.KEYWORD_ONLY)

_colour_literals = [w for w in COLOUR_WORDS if any(w == v or w in v.split(",")
                                                   for v in _string_values(FAMILY_HOME))]
check("A2 no colour WORD is a value in the home (AST, so the header may still quote the live "
      "spellings that motivated the fix)", not _colour_literals, f"found: {_colour_literals}")

_undeclared = dmf.family_of("Apple iPhone 13 128GB Pink CPO TO", colour_tokens=())
check("A3 an org that has declared no colour words gets NO collapsing and the basis SAYS so — never "
      "another tenant's colour list, because a confident wrong grouping is worse than an ungrouped "
      "list and an ungrouped list sends somebody to the setup panel",
      _undeclared["basis"] == "no_vocabulary"
      and _undeclared["label"] == "Apple iPhone 13 Pink CPO TO", str(_undeclared))

_off = [os.path.basename(c) for c in FAMILY_CALLERS
        if _calls(c, "group_units") and "resolve_colour_tokens" not in open(c, encoding="utf-8").read()]
check("A4 every caller that groups models reads the org's colour words through the one home "
      "(`resolve_colour_tokens`), never a list of its own", not _off, f"off the declaration: {_off}")

_cf = ("blk", "black", "blue")
_ll = ("black", "midnight", "pink")
check("A5 one tenant's colour words never collapse another tenant's spelling: a feed that says "
      "`Midnight` is untouched by a vocabulary that only knows `BLK`",
      dmf.family_of("Apple iPhone 13 Midnight TO", colour_tokens=_cf)["label"]
      == "Apple iPhone 13 Midnight TO"
      and dmf.family_of("Apple iPhone 13 Midnight TO", colour_tokens=_ll)["label"]
      == "Apple iPhone 13 TO")

check("A6 THE OWNER'S OWN DISTINCTION: a Pro is not a colour of the non-Pro, and nothing but a "
      "declared colour and a capacity is ever dropped",
      dmf.family_of("Apple iPhone 13 Pro 128GB Black TO", colour_tokens=_ll)["key"]
      != dmf.family_of("Apple iPhone 13 128GB Black TO", colour_tokens=_ll)["key"])

check("A7a the capacity is still dropped with NO colour vocabulary, because it is a format and not "
      "a vocabulary — but the basis keeps saying `no_vocabulary`, because that is the question a "
      "reader needs answered",
      dmf.family_of("Apple iPhone 13 128GB Pink CPO TO", colour_tokens=())["label"]
      == "Apple iPhone 13 Pink CPO TO")
check("A7 the capacity goes by PATTERN, glued or split, because a number followed by a storage unit "
      "is a format and not anybody's vocabulary",
      dmf.family_of("SAM GALAXY S25 FE 128 GB", colour_tokens=())["label"] == "SAM GALAXY S25 FE"
      and dmf.family_of("SAM GALAXY S25 FE 256GB", colour_tokens=())["label"] == "SAM GALAXY S25 FE")

_u, _v = dmf.group_units(
    {"Apple iPhone 13 128GB Pink CPO TO": {"Sep": 23},
     "Apple iPhone 13 128GB Black CPO TO": {"Sep": 6},
     "Apple iPhone 13 Pro 128GB Black TO": {"Sep": 4}},
    colour_tokens=_ll)
check("A8 the folded family carries BOTH the total and what was actually sold under it, so the "
      "drill-down costs no second query",
      _u.get("Apple iPhone 13 CPO TO") == {"Sep": 29}
      and len(_v["Apple iPhone 13 CPO TO"]) == 2
      and len(_u) == 2, f"{_u}")

check("A9 the family is named by its BEST-SELLING spelling, and the name cannot flip between two "
      "reads of the same rows",
      _v["Apple iPhone 13 CPO TO"][0]["model"] == "Apple iPhone 13 128GB Pink CPO TO")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. THE CATALOG IS A VOCABULARY, NOT A CLASSIFIER, AND NOT A SECOND COPY OF ONE ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("B1 the ordering catalog does NOT import the SOLD-line accessory classifier, and the "
      "classifier does not import it — one word apart in the filename, and two different questions. "
      "Writing this feature overwrote that module once; this check is why it cannot happen quietly",
      not any("accessory_catalog" in i and "order" not in i for i in _imports(CATALOG_HOME)))

_brands = [w for w in BRAND_WORDS if any(w in v for v in _string_values(CATALOG_HOME))]
check("B2 RULE TWO — no carrier, tenant or brand name is a value in the catalog (AST)",
      not _brands, f"found: {_brands}")

_cats, _basis = cat.resolve_catalog(None)
_keys = [c["key"] for c in _cats]
check("B3 the house catalog carries the five kinds the owner named",
      set(_keys) == {"case", "screen_protector", "cable", "charger", "earphones"}, str(_keys))

_split = {c["key"] for c in _cats if c["grain"] == cat.GRAIN_SPLIT}
_assigned = {c["key"] for c in _cats if c["grain"] == cat.GRAIN_ASSIGNED}
check("B4 THE LOAD-BEARING DISTINCTION: a case and a protector are bought FOR a phone and split "
      "across styles; a cable, a cube and earphones are bought BY CONNECTOR and consolidated across "
      "phones — *\"the cables are common to a lot of phones\"*",
      _split == {"case", "screen_protector"}
      and _assigned == {"cable", "charger", "earphones"}, f"{_split} / {_assigned}")

_case = next(c for c in _cats if c["key"] == "case")
_shares, _sbasis = cat.resolve_option_mix(_case, None)
check("B5 an undeclared option mix is an EVEN split that SAYS it is one, and the shares sum to "
      "exactly 1.0 — a starting sheet, never presented as a measurement",
      _sbasis == "even_split" and abs(sum(s["share"] for s in _shares) - 1.0) < 1e-9)

_dec, _dbasis = cat.resolve_option_mix(_case, [{"key": "silicone", "share": 70},
                                               {"key": "kickstand", "share": 30}])
check("B6 a declared mix is read as percentages or fractions by its TOTAL, so 70/30 and 0.7/0.3 "
      "cannot be read differently — and it is the SAME rule the colour mix reads",
      _dbasis == "declared"
      and cat.resolve_option_mix(_case, [{"key": "silicone", "share": 0.7},
                                         {"key": "kickstand", "share": 0.3}])[0] == _dec)

_ch = next(c for c in _cats if c["key"] == "charger")
check("B7 a charger is chosen on BOTH dimensions the owner named, ports and wattage",
      [d["key"] for d in _ch["dimensions"]] == ["ports", "wattage"]
      and len(_ch["dimensions"][1]["options"]) == 6)

_spec, _sb = cat.spec_for("any phone", _ch, None)
check("B8 the house standard applies until somebody chooses otherwise, and the row SAYS which it "
      "was — *\"assigned ot the phone model in the beinging but ... editable as a drop down\"*",
      _sb == "house_default" and _spec == {"ports": "single_usb_c", "wattage": "20w"}, str(_spec))

_spec2, _sb2 = cat.spec_for("A Phone", _ch, cat.resolve_model_specs(
    [{"model": "A Phone", "category": "charger", "dimension": "wattage", "option": "45w"},
     {"model": "A Phone", "category": "charger", "dimension": "ports", "option": "not_a_port"}]))
check("B9 a declared option the catalog does not offer is IGNORED, not honoured — deleting a "
      "connector cannot leave an order line nobody can buy",
      _spec2 == {"ports": "single_usb_c", "wattage": "45w"} and _sb2 == "declared", str(_spec2))

_added, _abasis = cat.resolve_catalog([
    {"key": "case", "dimensions": [{"key": "style", "options": [
        {"key": "silicone", "label": "Silicone"}, {"key": "wallet", "label": "Wallet",
                                                   "image_url": "https://example.test/w.png"}]}]}])
check("B10 adding one style keeps every other house category — *\"option to add more\"* may not "
      "silently delete the kinds the declaration did not restate",
      _abasis == "declared" and {c["key"] for c in _added} == set(_keys)
      and [o["key"] for o in next(c for c in _added if c["key"] == "case")["dimensions"][0]["options"]]
      == ["silicone", "wallet"])

check("B11 a picture survives the round trip, because that is what the owner asked a style to carry",
      next(o for o in next(c for c in _added if c["key"] == "case")["dimensions"][0]["options"]
           if o["key"] == "wallet").get("image_url") == "https://example.test/w.png")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. THE ORDER FOOTS, AND AN ABSENCE IS NEVER A ZERO ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
PLAN = aop.plan(
    units_by_model={"Phone A": {"M1": 100, "M2": 140}, "Phone B": {"M1": 30, "M2": 30},
                    "Phone C": {"M1": 9, "M2": 11}},
    months=["M1", "M2"], horizon_months=2,
    lifecycle={"Phone A": "current", "Phone B": "eol"},     # Phone C is deliberately unruled
    variants={"Phone A": [{"model": "Phone A Black", "units_total": 200,
                           "units_by_month": {"M1": 100, "M2": 100}}]})

check("C1 the grid's columns are the SPLIT categories only — a cable never becomes a column, "
      "because it is not bought per phone",
      [c["category"] for c in PLAN["grid_columns"]] == ["case", "screen_protector"])

_a = next(r for r in PLAN["model_orders"] if r["model"] == "Phone A")
check("C2 every style column foots to its category total, so a buyer can add the row up by eye",
      all(sum(o["qty"] for o in cell["options"]) == cell["qty"]
          for cell in _a["cells"].values() if cell["qty"] is not None))

check("C3 the colour split foots to the case total as well, by the same largest-remainder rule",
      sum(c["qty"] for c in _a["cells"]["case"]["colours"]) == _a["cells"]["case"]["qty"])

_c = next(r for r in PLAN["model_orders"] if r["model"] == "Phone C")
check("C4 AN UNRULED PHONE'S CELLS ARE None, NEVER 0 — a zero in the grid would read as a decision "
      "to stock none of it, which is the one thing this report must not say on a human's behalf",
      all(cell["qty"] is None for cell in _c["cells"].values())
      and all(o["qty"] is None for o in _c["cells"]["case"]["options"])
      and _c["cells"]["case"]["reason"] == "awaiting_declaration")

_b = next(r for r in PLAN["model_orders"] if r["model"] == "Phone B")
check("C5 a phone ruled END OF LIFE orders a real nothing, which is a 0 and not a dash — the two "
      "absences are different facts and the grid keeps them apart",
      _b["cells"]["case"]["qty"] == 0)

_cable = next(c for c in PLAN["consolidated"] if c["category"] == "cable")
check("C6 the consolidated lines sum to the category total, and the total is the projected devices "
      "at the declared rate",
      sum(l["qty"] for l in _cable["lines"]) == _cable["qty"] == PLAN["projected_devices"])

check("C7 the line NAMES the phones it covers, so a buyer can see what one cable order is for",
      {m["model"] for l in _cable["lines"] for m in l["models"]} == {"Phone A", "Phone B"})

check("C8 a phone with no projection is listed as AWAITING on the category rather than quietly "
      "left out, so the sheet says how much of the order is not yet plannable",
      [a["model"] for a in _cable["awaiting"]] == ["Phone C"])

_ear = next(c for c in PLAN["consolidated"] if c["category"] == "earphones")
check("C9 a kind of accessory with NO DECLARED RATE is not ordered and says `no_rate` — it does not "
      "invent one per device, and it does not read as a stocking decision either",
      _ear["qty"] == 0 and _ear["reason"] == "no_rate" and not _ear["lines"]
      and len(_ear["awaiting"]) == 3)

check("C10 the flat totals the first version shipped with still agree with the grid, so the "
      "drill-down declaration and the snapshot sender cannot drift from the sheet",
      next(a for a in PLAN["accessories"] if a["key"] == "case")["qty"]
      == sum(r["cells"]["case"]["qty"] for r in PLAN["model_orders"]
             if r["cells"]["case"]["qty"] is not None))

check("C11 the variants ride on the model row, so the screen can open a phone without a second "
      "query against the feed",
      _a["variants"] and _a["variants"][0]["model"] == "Phone A Black")


print("\n" + "=" * 70)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
print("=" * 70)
sys.exit(1 if FAIL else 0)
