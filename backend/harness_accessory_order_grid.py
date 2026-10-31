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
from app.modules.pos import device_condition as devcond           # noqa: E402

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
#: Words that would mean a tenant's own CONDITION vocabulary had been written into code. The
#: CONDITIONS are the platform's policy and have a home; the WORDS a feed marks one with are the
#: tenant's (`CPO` in one feed, `CERTIFIED_PREOWNED` in another), so they may only ever be declared.
CONDITION_WORDS = ("cpo", "certified preowned", "certified_preowned", "certified pre-owned",
                   "pre-owned", "preowned", "refurb", "renewed", "open box", "openbox")
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

_undeclared = dmf.family_of("Apple iPhone 13 128GB Pink CPO TO", colour_tokens=(), condition_tokens={})
check("A3 an org that has declared no colour words gets NO collapsing and the basis SAYS so — never "
      "another tenant's colour list, because a confident wrong grouping is worse than an ungrouped "
      "list and an ungrouped list sends somebody to the setup panel",
      _undeclared["basis"] == "no_vocabulary"
      and _undeclared["label"] == "Apple iPhone 13 Pink CPO TO", str(_undeclared))

_off = [os.path.basename(c) for c in FAMILY_CALLERS
        if _calls(c, "group_units")
        and ("resolve_colour_tokens" not in open(c, encoding="utf-8").read()
             or "resolve_feed_tokens" not in open(c, encoding="utf-8").read())]
check("A4 every caller that groups models reads BOTH of the org's vocabularies through their one "
      "homes (`resolve_colour_tokens`, `resolve_feed_tokens`), never a list of its own",
      not _off, f"off the declaration: {_off}")

_cf = ("blk", "black", "blue")
_ll = ("black", "midnight", "pink")
check("A5 one tenant's colour words never collapse another tenant's spelling: a feed that says "
      "`Midnight` is untouched by a vocabulary that only knows `BLK`",
      dmf.family_of("Apple iPhone 13 Midnight TO", colour_tokens=_cf, condition_tokens={})["label"]
      == "Apple iPhone 13 Midnight TO"
      and dmf.family_of("Apple iPhone 13 Midnight TO", colour_tokens=_ll, condition_tokens={})["label"]
      == "Apple iPhone 13 TO")

check("A6 THE OWNER'S OWN DISTINCTION: a Pro is not a colour of the non-Pro, and nothing but a "
      "declared colour and a capacity is ever dropped",
      dmf.family_of("Apple iPhone 13 Pro 128GB Black TO", colour_tokens=_ll, condition_tokens={})["key"]
      != dmf.family_of("Apple iPhone 13 128GB Black TO", colour_tokens=_ll, condition_tokens={})["key"])

check("A7a the capacity is still dropped with NO colour vocabulary, because it is a format and not "
      "a vocabulary — but the basis keeps saying `no_vocabulary`, because that is the question a "
      "reader needs answered",
      dmf.family_of("Apple iPhone 13 128GB Pink CPO TO", colour_tokens=(), condition_tokens={})["label"]
      == "Apple iPhone 13 Pink CPO TO")
check("A7 the capacity goes by PATTERN, glued or split, because a number followed by a storage unit "
      "is a format and not anybody's vocabulary",
      dmf.family_of("SAM GALAXY S25 FE 128 GB", colour_tokens=(), condition_tokens={})["label"] == "SAM GALAXY S25 FE"
      and dmf.family_of("SAM GALAXY S25 FE 256GB", colour_tokens=(), condition_tokens={})["label"] == "SAM GALAXY S25 FE")

_u, _v, _fam = dmf.group_units(
    {"Apple iPhone 13 128GB Pink CPO TO": {"Sep": 23},
     "Apple iPhone 13 128GB Black CPO TO": {"Sep": 6},
     "Apple iPhone 13 Pro 128GB Black TO": {"Sep": 4}},
    colour_tokens=_ll, condition_tokens={})
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


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. A PRE-OWNED DEVICE IS A DIFFERENT DEVICE (owner 2026-10-31) ==")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Owner, verbatim: *"cpo is certified pre owned is adifferent device than new  , imnei is unique"*.
# Before this, a refurbished handset stayed off the new one's row only because the word `CPO`
# happened to survive the model parse — an accident nothing asserted, and one curated alias row or
# one promo token would have merged two different devices into one order line silently. These
# checks are the lock on the fix: the vocabulary has one home, the condition is part of the row's
# identity, and neither a declaration nor a curation can un-split them.
CONDITION_HOME = os.path.join(HERE, "app", "modules", "pos", "device_condition.py")
PLAN_HOME = os.path.join(HERE, "app", "modules", "commcalc", "accessory_order_plan.py")
INVENTORY_PAGE = os.path.join(HERE, os.pardir, "frontend", "src", "app", "(platform)", "pos",
                              "inventory", "page.tsx")
CONDITION_LIB = os.path.join(HERE, os.pardir, "frontend", "src", "lib", "device-conditions.ts")
MIG_725 = os.path.join(HERE, os.pardir, "database", "migrations", "725_pos_sales.sql")

_fsig = inspect.signature(dmf.family_of)
_gsig = inspect.signature(dmf.group_units)
check("D1 `condition_tokens` is KEYWORD-ONLY and REQUIRED on both entry points — a caller that has "
      "not asked the org which words mark a condition must not be able to answer whether this is a "
      "different device",
      all(sig.parameters["condition_tokens"].kind is inspect.Parameter.KEYWORD_ONLY
          and sig.parameters["condition_tokens"].default is inspect.Parameter.empty
          for sig in (_fsig, _gsig)))

_cond_literals = sorted({w for w in CONDITION_WORDS
                         for v in _string_values(FAMILY_HOME) + _string_values(PLAN_HOME)
                         if w == v or w in v.split(",")})
check("D2 no condition WORD is a value in the family home or in the plan — they carry the condition, "
      "they do not know what one looks like (AST, so a header may still quote the live spellings)",
      not _cond_literals, f"found: {_cond_literals}")

_ct, _cb, _cm = devcond.resolve_feed_tokens(["CPO", "CERTIFIED_PREOWNED"])
_ll = ("black", "midnight", "pink")
_silent = dmf.family_of("Apple iPhone 13 128GB Pink CPO TO", colour_tokens=_ll, condition_tokens={})
check("D3 an org that has declared no condition words identifies NO condition and its keys are "
      "byte-identical to what they were before this question existed — a tenant the ruling does not "
      "apply to sees no change, and the surface can say the condition is not identified",
      _silent["condition"] is None and _silent["condition_basis"] == "no_vocabulary"
      # `Pink` is a declared COLOUR here, so it still folds; what must not change is that the
      # condition marker stays IN the key and nothing claims to have identified a condition.
      and _silent["key"] == "apple iphone 13 cpo to", str(_silent))

_cpo = dmf.family_of("Apple iPhone 13 128GB Pink CPO TO", colour_tokens=_ll,
                     condition_tokens=_ct, condition_markers=_cm)
_new = dmf.family_of("Apple iPhone 13 128GB Midnight TO", colour_tokens=_ll,
                     condition_tokens=_ct, condition_markers=_cm)
check("D4 THE OWNER'S RULING: a marked device and an unmarked one of the same model are different "
      "rows, while their colours still fold within each",
      _cpo["key"] != _new["key"] and _cpo["condition"] != _new["condition"],
      f"{_cpo['key']} vs {_new['key']}")

_glued = dmf.family_of("CERTIFIED_PREOWNED_IPHONE_13 Pink", colour_tokens=_ll,
                       condition_tokens=_ct, condition_markers=_cm)
check("D5 two spellings of ONE condition are one condition — the worded `CPO` and the glued "
      "`CERTIFIED_PREOWNED_` SKU form read the same, because the marker is matched over flattened "
      "words at the declaration as well as at the read",
      _glued["condition"] == _cpo["condition"] == "refurbished"
      and _glued["condition_basis"] == "declared")

_alias = {"apple iphone 13 128gb pink cpo to": "iPhone 13",
          "apple iphone 13 128gb midnight to": "iPhone 13"}
_acpo = dmf.family_of("Apple iPhone 13 128GB Pink CPO TO", colour_tokens=_ll, alias=_alias,
                      condition_tokens=_ct, condition_markers=_cm)
_anew = dmf.family_of("Apple iPhone 13 128GB Midnight TO", colour_tokens=_ll, alias=_alias,
                      condition_tokens=_ct, condition_markers=_cm)
check("D6 THE SILENT MERGE THIS LOCK EXISTS FOR: a curated alias that calls both spellings "
      "`iPhone 13` still cannot put a pre-owned handset on the new one's row — the condition is "
      "applied AFTER the alias, because a human curating a model NAME is not ruling on its condition",
      _acpo["key"] != _anew["key"] and _acpo["key"].startswith(_anew["key"]),
      f"{_acpo['key']} vs {_anew['key']}")

check("D7 the UNMARKED condition adds nothing to the key or the label — the baseline is not "
      "suffixed `New` on every row of every tenant, which would be a word on every line for no "
      "information",
      _new["key"] == "apple iphone 13 to" and _new["label"] == "Apple iPhone 13 TO"
      and _new["condition_basis"] == "unmarked")

check("D8 the ORG'S OWN WORD is what a human is shown, while the stored key stays the platform's "
      "(what mig 725 allows) — the owner calls it CPO and the schema calls it refurbished, and "
      "neither has to win",
      _cpo["condition_marker"] == "CPO" and _cpo["condition"] == "refurbished"
      and _cpo["condition_label"] == "Refurbished" and _cpo["label"].endswith("· CPO"),
      f"{_cpo}")

_other, _, _om = devcond.resolve_feed_tokens(["refurb"])
check("D9 one tenant's condition words never mark another tenant's spelling: a feed whose "
      "vocabulary is only `Refurb` reads a `CPO` line as unmarked, and says `unmarked` rather than "
      "claiming to have read it",
      devcond.condition_of("Apple iPhone 13 CPO TO", feed_tokens=_other,
                           markers=_om)["basis"] == "unmarked")

_check_sql = open(MIG_725, encoding="utf-8").read()
_declared_in_sql = None
for line in _check_sql.splitlines():
    if "condition" in line and "CHECK" in line and " IN (" in line:
        _declared_in_sql = tuple(sorted(w.strip().strip("'\"")
                                        for w in line.split(" IN (")[1].split(")")[0].split(",")))
check("D10 the house condition keys are the ones the SCHEMA allows, key by key, parsed out of the "
      "migration — a fifth condition added in code and not in the constraint would fail a write at "
      "the till, and one added to the constraint and not here would never be offered",
      _declared_in_sql is not None
      and _declared_in_sql == tuple(sorted(devcond.condition_keys())),
      f"sql={_declared_in_sql} code={tuple(sorted(devcond.condition_keys()))}")

_page = open(INVENTORY_PAGE, encoding="utf-8").read()
#: COMMENTS STRIPPED FIRST, for the same reason A2 reads the AST: a screen may still explain why a
#: word that used to be hardcoded there is gone, and that explanation is not a copy of the fact.
_page_code = "\n".join(l for l in _page.splitlines() if not l.lstrip().startswith("//"))
_page_literals = sorted({k for k in devcond.condition_keys() if f"'{k}'" in _page_code})
check("D11 the POS inventory screen carries NO condition list of its own and reads the served one "
      "— the literal array that sat beside a CHECK constraint saying the same thing is what let a "
      "refurbished handset read as new",
      "device-conditions" in _page_code and not _page_literals,
      f"literals left on the page: {_page_literals}")

_lib = "\n".join(l for l in open(CONDITION_LIB, encoding="utf-8").read().splitlines()
                 if not l.lstrip().startswith(("//", "*", "/*")))
check("D12 the browser's reader names no condition and no status either, so it cannot become the "
      "third copy of the vocabulary it was written to remove",
      not [k for k in devcond.condition_keys() if f"'{k}'" in _lib])

check("D13 `basis` is an ALLOWLIST, so an unruled basis cannot be read as a declaration — the same "
      "shape as `component_is_evidence` (§66), which exists because a surface reading a placement "
      "without its basis treated a last resort as evidence",
      set(devcond.CONDITION_BASES) == {"declared", "unmarked", "no_vocabulary"})

check("D14 the condition home is PURE — no router, no database, no clock — so every caller can ask "
      "it and no caller can make it read a row",
      not [i for i in _imports(CONDITION_HOME)
           if i.split(".")[0] in {"app", "supabase", "fastapi", "datetime", "os", "requests"}],
      f"{sorted(_imports(CONDITION_HOME))}")

_u2, _v2, _f2 = dmf.group_units(
    {"Apple iPhone 13 128GB Pink CPO TO": {"Sep": 20},
     "Apple iPhone 13 128GB Black CPO TO": {"Sep": 5},
     "Apple iPhone 13 128GB Midnight TO": {"Sep": 12}},
    colour_tokens=_ll, condition_tokens=_ct, condition_markers=_cm)
check("D15 the grouped rows carry their condition from the SAME read that folded them, so a screen "
      "shows it without asking a second time — two derivations of one fact is how the two come to "
      "disagree",
      sorted(_u2.values(), key=lambda d: -d["Sep"]) == [{"Sep": 25}, {"Sep": 12}]
      and {f["condition"] for f in _f2.values()} == {"refurbished", "new"}
      and all(f["condition_basis"] in devcond.CONDITION_BASES for f in _f2.values()),
      f"{_u2} {_f2}")

_plan_cond = aop.plan(units_by_model=_u2, months=["Sep"],
                      lifecycle={k: "current" for k in _u2},
                      conditions=_f2, variants=_v2)
check("D16 the plan hands the condition through to the row the buyer reads, and does not derive one "
      "of its own",
      {r["condition"] for r in _plan_cond["models"]} == {"refurbished", "new"}
      and "device_condition" not in str(_imports(PLAN_HOME)))


def _group_units_alias_args():
    """How every caller hands the curated alias to the one home (AST)."""
    out = []
    for c in FAMILY_CALLERS:
        for n in ast.walk(ast.parse(open(c, encoding="utf-8").read())):
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "group_units":
                kw = {k.arg: k.value for k in n.keywords}
                a = kw.get("alias")
                out.append((os.path.basename(c),
                            a is not None and not (isinstance(a, ast.Constant) and a.value is None)))
    return out


_alias_args = _group_units_alias_args()
check("D17 the curated alias is HANDED TO the one home, not applied before it — a caller that "
      "canonicalises first and asks second has already thrown the condition marker away, which is "
      "exactly what the router did until the integration test for this ruling caught it",
      _alias_args and all(ok for _c, ok in _alias_args), f"{_alias_args}")

print("\n" + "=" * 70)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
print("=" * 70)
sys.exit(1 if FAIL else 0)
