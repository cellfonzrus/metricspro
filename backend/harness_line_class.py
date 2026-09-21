"""DB-FREE PROOF — THE ONE activation-type predicate (line_class), its per-org rules, the money
compatibility pin, the suggestion engine over the measured vocabulary, and the reports that count it.

    python3 harness_line_class.py        (from backend/; no network, no DB)

OWNER (2026-09-21, verbatim): "sales report shows 88 txns but not a break up in to activations and
upgrade etc, also nothing on exec mtd"

THE CLASS: a sale line's activation type was classified from ONE fixed column (contract_type) by token
lists hard-coded in three places, and the Exec-MTD metric buckets from a house vocabulary — a POS that
carries the same fact in another field yields silent zeros on every report and every commission calc.

    §A the predicate: precedence, the exact map, disabled classes, blank rows, unknown fields
    §B THE COMPATIBILITY PIN (money): the retired classifiers — calculator.classify_contract_type,
       router._resolve_ct_bucket (+ the mig-213 auto-count) and router._exec_act_class — replayed
       VERBATIM over every contract-type spelling in the seeds, the migrations and the proof fixtures,
       under an empty map, a foreign map and a map that pins the value to each bucket: BYTE-IDENTICAL
       to the new predicate under house-default rules. A custom legacy port token is honoured too.
    §C the suggestion engine over the MEASURED vocabulary (the category PATH leaf carries the type,
       contract_type is blank on every row): it PROPOSES category 'new activation' → activation,
       'upgrade' → upgrade, 'customer provided' / product 'customer owned' → BYOD, 'hardware only' →
       hardware_only, and 'smartphone' / 'basic phone' → phones; the department word inside every
       category path ('activation') is reported TOO BROAD and never proposed.
    §D after the person confirms: _sales_cell_agg (the pass the Sales Report, Executive MTD and Daily
       Targets share) over the same fixture counts the split by DISTINCT INVOICE — 40 new-activation
       lines on 39 invoices, 25 upgrade lines on 25, 24 BYOD lines on 23, 4 hardware-only lines on 1
       (not an activation) — and Total Phones 67; and the pay path (calc_rep_commissions) reaches the
       same sets through cfg['line_class_rules'].
    §E exec_metric_defs.line_match: category_contains / department_contains are additive (a rule
       without them is byte-identical); a gap bucket with no hint hit proposes nothing.
    §F the gate: zero activation-type lines over a slice with lines is open; an attestation closes it.
"""
import os
import random
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from app.modules.commcalc import line_class as LC
from app.modules.commcalc import exec_metric_defs as EMD
from app.modules.commcalc import calculator as CALC
from app.modules.commcalc import onboarding_intake as OI

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
_pass = _fail = 0


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}{(' — ' + str(extra)[:700]) if extra else ''}")
    return bool(cond)


def section(t):
    print(f"\n{t}\n" + "─" * len(t))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE RETIRED CLASSIFIERS, VERBATIM (the pin's oracle — these are what every tenant was paid on)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
OLD_PREMIUM_ACT = {'Activation', 'Port-In', 'Add A Line', 'Port-In Add A Line', 'Eligible Port-In Activation',
                   'Activation Add A Line', 'Eligible Port-In Add A Line'}
OLD_PREMIUM_KEYS = ('activation', 'port-in', 'port in', 'add a line', 'add-a-line', 'new line', ' aal', 'aal ',
                    'idv', 'port with idv')
OLD_AUTO_ACT_CATEGORY_KEYS = ('home internet', 'home-internet', 'fixed wireless', 'fwa', 'fios', 'tablet', 'edge')
OLD_EXEC_ACT_RULES = {'byod': ['byod'], 'upgrade': ['upgrade'], 'port': ['port']}


def old_classify_contract_type(ct):
    c = (ct or '').strip()
    if not c:
        return None
    cl = c.lower()
    if 'byod' in cl:
        return 'byod'
    if 'upgrade' in cl:
        return 'upgrade'
    if c in OLD_PREMIUM_ACT or any(k in cl for k in OLD_PREMIUM_KEYS):
        return 'premium'
    return None


def old_resolve_ct_bucket(ct, ct_map=None):
    if ct_map:
        b = ct_map.get(str(ct or "").strip().lower())
        if b:
            return None if b == "none" else b
    base = old_classify_contract_type(ct)
    if base:
        return base
    if ct_map:
        cl = str(ct or "").strip().lower()
        if cl and any(k in cl for k in OLD_AUTO_ACT_CATEGORY_KEYS):
            return 'premium'
    return None


def old_exec_act_class(ct, rules):
    c = (ct or '').strip().lower()
    if not c:
        return None
    if any(t in c for t in (rules.get('upgrade') or [])):
        return 'upgrade'
    if any(t in c for t in (rules.get('byod') or [])):
        return 'byod'
    if any(t in c for t in (rules.get('port') or [])):
        return 'port'
    return 'activation'


def old_pipeline(ct, ct_map, exec_rules):
    """(bucket, is_port) — exactly what _sales_cell_agg computed before the design."""
    b = old_resolve_ct_bucket(ct, ct_map)
    port = bool(b == 'premium' and old_exec_act_class(ct, exec_rules) == 'port')
    return b, port


def new_pipeline(ct, ct_map, legacy=None):
    rules = LC.resolve_rules(None, ct_map, legacy)
    full = LC.activation_class({"contract_type": ct}, rules)
    return LC.bucket_of(full), full == 'port'


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("§A the predicate")
H = LC.HOUSE_RULES
check("A1 house rules read contract_type only, with the retired token lists verbatim",
      H["fields"] == ["contract_type"] and H["tokens"]["activation"] == list(OLD_PREMIUM_KEYS)
      and H["tokens"]["byod"] == ["byod"] and H["tokens"]["upgrade"] == ["upgrade"] and H["tokens"]["port"] == ["port"]
      and H["tokens"]["hardware_only"] == [] and H["exact"] == {} and H["source"] == "house", H)
check("A2 'Activation' → activation → bucket premium; 'Port-In' → port → premium; 'BYOD Port' → byod (byod first); 'Upgrade' → upgrade",
      LC.activation_class({"contract_type": "Activation"}) == "activation" and LC.classify_line({"contract_type": "Activation"}) == "premium"
      and LC.activation_class({"contract_type": "Port-In"}) == "port" and LC.classify_line({"contract_type": "Port-In"}) == "premium"
      and LC.activation_class({"contract_type": "BYOD Port"}) == "byod" and LC.activation_class({"contract_type": "Upgrade"}) == "upgrade")
check("A3 blank / None / a non-activation word → None; 'Support' is NOT a port (port only refines an activation)",
      LC.activation_class({"contract_type": ""}) is None and LC.activation_class({}) is None and LC.activation_class(None) is None
      and LC.activation_class({"contract_type": "Support"}) is None and LC.activation_class({"contract_type": "Accessory"}) is None)
R2 = LC.resolve_rules({"fields": ["category", "product_desc"],
                       "tokens": {"activation": ["new activation"], "upgrade": ["upgrade"], "byod": ["customer provided", "customer owned"],
                                  "hardware_only": ["hardware only"], "port": ["port"]}})
check("A4 a tenant's fields: the type is read from category / product_desc; contract_type (blank) is ignored",
      R2["fields"] == ["category", "product_desc"] and R2["source"] == "tenant"
      and LC.activation_class({"contract_type": "", "category": "x >> New Activation (c)", "product_desc": "Phone"}, R2) == "activation"
      and LC.activation_class({"category": "x >> Cellular Equipment >> Customer Provided Device", "product_desc": "Customer Owned Device"}, R2) == "byod"
      and LC.activation_class({"category": "x >> Prepaid/Hardware Only (Equip)"}, R2) == "hardware_only"
      and LC.bucket_of("hardware_only") is None
      and LC.activation_class({"category": "x >> Upgrades (c)"}, R2) == "upgrade")
check("A5 precedence: hardware_only beats byod beats upgrade beats activation; port refines only an activation",
      LC.activation_class({"category": "hardware only customer provided"}, R2) == "hardware_only"
      and LC.activation_class({"category": "customer provided upgrade"}, R2) == "byod"
      and LC.activation_class({"category": "upgrade new activation"}, R2) == "upgrade"
      and LC.activation_class({"category": "new activation port"}, R2) == "port"
      and LC.activation_class({"category": "port only"}, R2) is None)
R3 = LC.resolve_rules({"tokens": {"upgrade": []}}, {"Device Upgrade": "none", "Weird Label": "premium", "Other": "byod"})
check("A6 the exact map (mig 213 absorbed): 'none' excludes, 'premium' reads as activation (+ port refinement), a class word maps; [] disables a class",
      LC.activation_class({"contract_type": "Device Upgrade"}, R3) is None
      and LC.activation_class({"contract_type": "weird label"}, R3) == "activation"
      and LC.activation_class({"contract_type": "Other"}, R3) == "byod"
      and LC.activation_class({"contract_type": "Upgrade"}, R3) is None       # upgrade disabled → not an upgrade, not an activation
      and R3["exact"] == {"device upgrade": "none", "weird label": "activation", "other": "byod"})
check("A7 the mig-213 auto-count: a tenant WITH an exact map counts 'Home Internet' / 'Tablet' as an activation; the house (no map) does not",
      LC.activation_class({"contract_type": "Home Internet"}, R3) == "activation"
      and LC.activation_class({"contract_type": "Tablet Port"}, R3) == "port"
      and LC.activation_class({"contract_type": "Home Internet"}) is None)
check("A8 junk config → house defaults; unknown keys ignored; a legacy exec 'activation' row supplies port/byod/upgrade tokens only where the home does not declare them",
      LC.resolve_rules("junk")["tokens"] == H["tokens"] and LC.resolve_rules({"nonsense": 1})["fields"] == ["contract_type"]
      and LC.resolve_rules(None, None, {"port": ["port-in"]})["tokens"]["port"] == ["port-in"]
      and LC.resolve_rules({"tokens": {"port": ["ported"]}}, None, {"port": ["port-in"]})["tokens"]["port"] == ["ported"])
check("A9 calculator.classify_line / classify_contract_type are the predicate (a row or a bare value), house by default",
      CALC.classify_line({"contract_type": "BYOD Activation"}) == "byod" and CALC.classify_contract_type("Activation AAL") == "premium"
      and CALC.classify_contract_type({"contract_type": "Upgrade"}) == "upgrade" and CALC.classify_contract_type("") is None
      and CALC.classify_line({"category": "x >> New Activation"}, R2) == "premium"
      and not hasattr(CALC, "PREMIUM_ACT") and not hasattr(CALC, "_PREMIUM_KEYS"))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("§B THE COMPATIBILITY PIN — every spelling, byte-identical under house defaults")
BATTERY = set(OLD_PREMIUM_ACT) | {
    'BYOD', 'BYOD Port-In', 'BYOD Add A Line', 'BYOD Port-In Add A Line', 'BYOD Swap', 'BYOD Eligible Port-In',
    'Upgrade', 'Upgrade Port-In', 'Device Upgrade', 'BYOD Upgrade', 'Tablet Upgrade', 'Upgrade with IDV',
    'Port with IDV', 'Port w/ IDV', 'Activation With IDV', 'BYOD Port with IDV', 'IDV', 'New Activation',
    'Standard Activation', 'Eligible Port In Activation', 'Activation AAL', 'AAL', 'AAL Activation', 'New Line',
    'Home Internet', 'BYOD Home Internet', 'FiOS', 'Fixed Wireless', 'FWA', 'Tablet', 'Tablet Port', 'Edge',
    'Edge Activation', 'Motorola Edge 2025', 'Knowledge', 'Support', 'Report', 'Import', 'Swap', 'SIM Swap',
    'Customer Phone Act', 'Prepaid', 'Accessory', 'Bill Payment', 'RTR', 'Return', 'Refund', '', ' ', None,
    'ACTIVATION', 'activation', ' Activation ', 'Port-In ', 'port in', 'Add-A-Line', 'add a line',
}
# every quoted string in the migrations / seeds / proof fixtures that names an activation family
_lit = re.compile(r"""['"]([^'"\n]{2,60})['"]""")
_fam = re.compile(r"activation|upgrade|byod|port|aal|idv|new line|add a line", re.I)
scanned_files = 0
for sub in ("database/migrations", "backend/app/data", "backend/scratchpad", "backend"):
    d = os.path.join(ROOT, sub)
    if not os.path.isdir(d):
        continue
    for f in sorted(os.listdir(d)):
        p = os.path.join(d, f)
        if not os.path.isfile(p) or not f.endswith((".sql", ".json", ".py")):
            continue
        scanned_files += 1
        try:
            src = open(p, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        for m in _lit.finditer(src):
            v = m.group(1)
            if _fam.search(v) and len(v.split()) <= 6 and "{" not in v and "%" not in v:
                BATTERY.add(v)
BATTERY = sorted(BATTERY, key=lambda x: (x is None, str(x)))
print(f"  battery: {len(BATTERY)} contract-type spellings ({scanned_files} files scanned)")
FOREIGN_MAP = {"some other label": "premium"}
mismatch = []
for ct in BATTERY:
    for cm in (None, {}, FOREIGN_MAP):
        if old_pipeline(ct, cm, OLD_EXEC_ACT_RULES) != new_pipeline(ct, cm):
            mismatch.append((ct, cm, old_pipeline(ct, cm, OLD_EXEC_ACT_RULES), new_pipeline(ct, cm)))
    key = str(ct or "").strip().lower()
    if key:
        for b in ("premium", "upgrade", "byod", "none"):
            cm = {key: b, **FOREIGN_MAP}
            if old_pipeline(ct, cm, OLD_EXEC_ACT_RULES) != new_pipeline(ct, cm):
                mismatch.append((ct, cm, old_pipeline(ct, cm, OLD_EXEC_ACT_RULES), new_pipeline(ct, cm)))
check(f"B1 bucket + port BYTE-IDENTICAL for all {len(BATTERY)} spellings × (no map, empty map, foreign map, pinned to each bucket / none)",
      not mismatch, mismatch[:6])
check("B2 the retired exact set is reproduced by the keyword list alone (every member classifies premium, and stays premium lower / upper-cased)",
      all(new_pipeline(v, None)[0] == "premium" and new_pipeline(v.upper(), None)[0] == "premium" for v in OLD_PREMIUM_ACT))
rnd = random.Random(20260921)
WORDS = ["activation", "port-in", "port", "byod", "upgrade", "add a line", "aal", "idv", "new line", "tablet", "edge", "home internet",
         "swap", "with", "eligible", "device", "customer", "phone", "act", "in", "line", "support", "report", "fios", "prepaid", "kit"]
fuzz_bad = []
for _ in range(4000):
    ct = " ".join(rnd.choice(WORDS) for _ in range(rnd.randint(1, 4)))
    if rnd.random() < 0.3:
        ct = ct.title()
    for cm in (None, FOREIGN_MAP):
        if old_pipeline(ct, cm, OLD_EXEC_ACT_RULES) != new_pipeline(ct, cm):
            fuzz_bad.append((ct, cm))
check("B3 4,000 fuzzed labels from the activation vocabulary: byte-identical (no map / foreign map)", not fuzz_bad, fuzz_bad[:5])
LEG = {"byod": ["byod"], "upgrade": ["upgrade"], "port": ["port-in", "ported"]}
leg_bad = [ct for ct in BATTERY if old_pipeline(ct, None, LEG) != new_pipeline(ct, None, LEG)]
check("B4 a tenant that had tuned its exec 'activation' PORT token keeps that split (the legacy layer), byte-identical", not leg_bad, leg_bad[:5])
# the one STATED divergence: a tenant-authored byod/upgrade token wider than the hard-coded word used to only
# SUPPRESS the port sub-split; under one predicate it classifies the line. Measured, not hidden.
LEG2 = {"byod": ["byod", "customer owned"], "upgrade": ["upgrade"], "port": ["port"]}
div = [(ct, old_pipeline(ct, None, LEG2), new_pipeline(ct, None, LEG2)) for ct in BATTERY if old_pipeline(ct, None, LEG2) != new_pipeline(ct, None, LEG2)]
print(f"  stated divergence (tenant-authored byod token wider than 'byod'): {len(div)} spelling(s) — {div[:3]}")
check("B5 that divergence is confined to labels the widened token names (no other spelling moves)",
      all("customer owned" in str(ct).lower() for ct, _o, _n in div))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("§C the suggestion engine over the MEASURED vocabulary")
from harness_intake_fakes import measured_fixture_rows, CAT, PROD, DEPT, P   # the shared measured-vocabulary fixture  # noqa: E402
ROWS = measured_fixture_rows()
n_inv = len({r["trans_id"] for r in ROWS})
by_kind = {k: sum(1 for r in ROWS if r["category"] == CAT[k]) for k in CAT}
check(f"C0 the fixture is the measured shape: {len(ROWS)} lines, 88 invoices, contract_type blank on every row, 40/25/24/4 type lines, 67 handsets",
      n_inv == 88 and all(not r["contract_type"] for r in ROWS) and by_kind["na"] == 40 and by_kind["upg"] == 25 and by_kind["byod"] == 24
      and by_kind["hw"] == 4 and by_kind["apple"] + by_kind["samsung"] + by_kind["tcl"] == 67, (n_inv, by_kind))
cur = LC.count_classes(ROWS, LC.HOUSE_RULES)
check("C1 under HOUSE rules (contract_type only) not one line classifies — the silent zero, now measured; the gate is OPEN",
      cur["scanned"] == len(ROWS) and cur["activation_type_lines"] == 0 and LC.gate_open(cur) and "contract_type" in LC.gate_sentence(cur))
S = LC.suggest_rules(ROWS, LC.HOUSE_RULES)
prop = S["proposal"]
pc = S["per_class"]


def toks(cls):
    return [c["token"] for c in pc[cls]]


S_BARE = LC.suggest_rules(ROWS, LC.resolve_rules({"hints": {"activation": ["new activation", "activation"]}}))
check("C2 PROPOSES category contains 'new activation' → activation (40 lines); the bare word 'activation' is NOT a house hint any more (decision 2026-09-21) and, as a tenant hint, the department word inside every path is reported TOO BROAD, never proposed",
      any(c["field"] == "category" and c["token"] == "new activation" and c["lines"] == 40 for c in pc["activation"])
      and "activation" not in toks("activation") and "activation" not in LC.HOUSE_HINTS["activation"] and "port" not in LC.HOUSE_HINTS["port"]
      and not any(b["class"] == "activation" for b in S["too_broad"])
      and "activation" not in S_BARE["proposal"]["tokens"]["activation"]
      and any(b["class"] == "activation" and b["token"] == "activation" and b["field"] == "category" and b["ratio"] > 0.8 for b in S_BARE["too_broad"]),
      (toks("activation"), S["too_broad"][:3], S_BARE["proposal"]["tokens"]["activation"]))
check("C3 PROPOSES category contains 'upgrade' → upgrade (25 lines; the leaf spells it 'Upgrades')",
      any(c["field"] == "category" and c["token"] == "upgrade" and c["lines"] == 25 for c in pc["upgrade"]), pc["upgrade"])
check("C4 PROPOSES category 'customer provided' AND product 'customer owned' → BYOD (24 lines each)",
      any(c["field"] == "category" and c["token"] == "customer provided" and c["lines"] == 24 for c in pc["byod"])
      and any(c["field"] == "product_desc" and c["token"] == "customer owned" and c["lines"] == 24 for c in pc["byod"]), pc["byod"])
check("C5 PROPOSES category 'hardware only' → hardware_only (4 lines); 'prepaid' names the same lines and yields to it",
      toks("hardware_only") == ["hardware only"] or set(toks("hardware_only")) <= {"hardware only", "prepaid"},
      pc["hardware_only"])
check("C6 the proposed FIELDS keep contract_type first and add the fields the words live in",
      prop["fields"][0] == "contract_type" and "category" in prop["fields"] and "product_desc" in prop["fields"], prop["fields"])
pv = S["preview"]
check("C7 the PREVIEW is the predicate over the proposal: 40 / 25 / 24 / 4 lines; 39 / 25 / 23 / 1 distinct invoices; a 'port' word on an accessory classifies nothing",
      pv["classes"]["activation"]["lines"] == 40 and pv["classes"]["activation"]["transactions"] == 39
      and pv["classes"]["upgrade"]["lines"] == 25 and pv["classes"]["upgrade"]["transactions"] == 25
      and pv["classes"]["byod"]["lines"] == 24 and pv["classes"]["byod"]["transactions"] == 23
      and pv["classes"]["hardware_only"]["lines"] == 4 and pv["classes"]["hardware_only"]["transactions"] == 1
      and pv["classes"]["port"]["lines"] == 0 and pv["activation_type_lines"] == 89 and pv["activation_type_transactions"] == 87
      and not LC.gate_open(pv), pv["classes"])
RESOLVED_EXEC = EMD.resolve([], "org", [])
M = LC.suggest_metric_rules(ROWS, RESOLVED_EXEC)
ph = M["buckets"]["phones"]
check("C8 phones matched 0 under the house rule (exact category 'cellphone'); PROPOSES category_contains 'smartphone' + 'basic phone' (+ the brand words the hints also hit) → 67 lines, the house tokens kept beside them",
      ph["matched"] == 0 and ph["proposal"] and {"basic phone", "smartphone"} <= set(ph["proposal"].get("category_contains") or [])
      and ph["preview"] == 67 and ph["proposal"].get("category") == ["cellphone", "kittedbranded"], ph)
check("C9 a gap bucket with no hint hit proposes NOTHING (bill_payment here) and says so — never a guess",
      M["buckets"]["bill_payment"]["matched"] == 0 and M["buckets"]["bill_payment"]["proposal"] is None)
check("C10 'accessory' matched 0 under the house rule; PROPOSES a contains token for the accessory line — the hint word, over the file's own values",
      M["buckets"]["accessory"]["matched"] == 0 and M["buckets"]["accessory"]["proposal"] is not None and M["buckets"]["accessory"]["preview"] >= 1)
check("C11 the tenant's own hint words are config: a tenant hint list replaces the house list for that class",
      LC.suggest_rules(ROWS, LC.resolve_rules({"hints": {"activation": ["device rebate"]}}))["proposal"]["tokens"]["activation"] == ["device rebate"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("§D after the person confirms — the shared aggregation and the pay path count the split")
from app.modules.commcalc import router as R          # noqa: E402  (the real pass; no DB touched)
CONFIRMED = LC.resolve_rules({"fields": prop["fields"], "tokens": prop["tokens"]})
acfg = {"departments": set(), "categories": set(), "products": set(), "departments_list": [], "categories_list": [], "products_list": [],
        "acima_tenders_list": [], "box_departments": set(), "setup_fee_products": set(), "setup_fee_keywords_list": [],
        "billpay_products": set(), "contract_type_map": {}, "activation_rules": [], "line_rules": CONFIRMED,
        "box_count_buckets": set(), "catalog_classify_enabled": False, "catalog_classifier": None}
exec_cfg = EMD.strip_sources(RESOLVED_EXEC)
exec_cfg["phones"]["rules"] = ph["proposal"]
cells = R._sales_cell_agg(ROWS, acfg, exec_cfg=exec_cfg)
prem = set().union(*(c["_prem"] for c in cells.values()))
upg = set().union(*(c["_upg"] for c in cells.values()))
byod = set().union(*(c["_byod"] for c in cells.values()))
port = set().union(*(c["_port"] for c in cells.values()))
phones = sum(c["total_phones"] for c in cells.values())
check("D1 _sales_cell_agg (Sales Report / Executive MTD / Daily Targets) over the confirmed rules: 39 activations, 25 upgrades, 23 BYOD by DISTINCT invoice, 0 ports, Total Activation 87 of 88 invoices (the hardware-only invoice is not one)",
      len(prem) == 39 and len(upg) == 25 and len(byod) == 23 and len(port) == 0 and len(prem | upg | byod) == 87, (len(prem), len(upg), len(byod), len(port)))
check("D2 …and Total Phones 67 from the confirmed phones rule (category_contains), where the house rule read 0",
      phones == 67, phones)
cells0 = R._sales_cell_agg(ROWS, {**acfg, "line_rules": LC.HOUSE_RULES}, exec_cfg=EMD.strip_sources(RESOLVED_EXEC))
check("D3 the SAME pass under house rules reads 0 / 0 / 0 and 0 phones — the owner's screen, reproduced",
      not any(c["_prem"] or c["_upg"] or c["_byod"] for c in cells0.values()) and sum(c["total_phones"] for c in cells0.values()) == 0)
res = CALC.calc_rep_commissions(sales=ROWS, pay_detail=[], dlar_rep=[], dlar_store=[], mi_rows=[], catalog=[],
                                cfg={"line_class_rules": CONFIRMED}, store_mapping=[], shifts=[], employees=[], stores=[],
                                period="August 2026", name_map=[], carrier_mode="boost")
comms = {str(r.get("epay_salesperson") or r.get("salesperson") or "").lower(): r for r in res.get("commissions") or []}
pay_prem = sum(int(r.get("premium_acts") or 0) for r in comms.values())
pay_byod = sum(int(r.get("byod_acts") or 0) for r in comms.values())
pay_upg = sum(int(r.get("upgrade_acts") or 0) for r in comms.values())
check("D4 the PAY PATH (calc_rep_commissions) reaches the same distinct-invoice sets through cfg['line_class_rules']: 39 / 23 / 25",
      (pay_prem, pay_byod, pay_upg) == (39, 23, 25), (pay_prem, pay_byod, pay_upg, [k for k in (list(comms.values()) or [{}])[0].keys()][:30]))
res0 = CALC.calc_rep_commissions(sales=ROWS, pay_detail=[], dlar_rep=[], dlar_store=[], mi_rows=[], catalog=[],
                                 cfg={}, store_mapping=[], shifts=[], employees=[], stores=[], period="August 2026", name_map=[], carrier_mode="boost")
check("D5 …and 0 / 0 / 0 without them (house defaults — the money path is byte-identical for every existing tenant)",
      sum(int(r.get("premium_acts") or 0) + int(r.get("byod_acts") or 0) + int(r.get("upgrade_acts") or 0)
          for r in res0.get("commissions") or []) == 0)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("§E line_match: contains keys are additive")
rule = {"category": ["cellphone"], "product_desc_contains": ["kit"]}
check("E1 a rule without category_contains / department_contains is byte-identical (exact category, product substring, exclusions first)",
      EMD.line_match(rule, "d", "cellphone", "x") and not EMD.line_match(rule, "d", "cellphones", "x") and EMD.line_match(rule, "d", "c", "a kit")
      and not EMD.line_match({**rule, "exclude_category": ["cellphone"]}, "d", "cellphone", "x"))
check("E2 category_contains / department_contains match by substring; a blank cell never matches; exclusions still win",
      EMD.line_match({"category_contains": ["smartphone"]}, "d", "x >> apple smartphones", "p")
      and not EMD.line_match({"category_contains": ["smartphone"]}, "d", "", "p")
      and EMD.line_match({"department_contains": ["activations"]}, "activations (price sheet)", "", "")
      and not EMD.line_match({"category_contains": ["smartphone"], "exclude_department": ["d"]}, "d", "x smartphones", "p"))
check("E3 the intake's activation gate: open with no attestation → blocked naming step 2.5a; attested → not blocked, recorded with the name; not checked → not blocked, stated",
      OI.activation_gate(cur)["blocked"] and "2.5a" in OI.activation_gate(cur)["reason"]
      and not OI.activation_gate(cur, {"no_activations": "accessory-only kiosk"}, by="pat")["blocked"]
      and OI.activation_gate(cur, {"no_activations": "accessory-only kiosk"}, by="pat")["attested"]["by"] == "pat"
      and not OI.activation_gate(None)["blocked"] and OI.activation_gate(None)["checked"] is False
      and not OI.activation_gate(pv)["blocked"])
check("E4 merge_into_raw keeps the Activation-Details basis keys beside ours, normalises only ours, and stores ONLY the classes given (no copy of the house list in a tenant row — the resolver fills)",
      LC.merge_into_raw({"edge_contract_tokens": ["edge"], "fields": ["x"]}, fields=["category"], tokens={"byod": ["Customer Owned"]})
      == {"edge_contract_tokens": ["edge"], "fields": ["category"], "tokens": {"byod": ["customer owned"]}}
      and LC.merge_into_raw({"tokens": {"byod": ["byod"], "upgrade": ["upg"]}}, tokens={"upgrade": [], "port": ["port-in"]})["tokens"]
      == {"byod": ["byod"], "upgrade": [], "port": ["port-in"]})

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("§G THE SECOND CLASS — the effective rule is what the person confirmed (guard, no-leak, seed, pin)")
# the owner's measured state (2026-09-21): fields ['category'], the bare word 'activation' saved under activation —
# inside every category path of the export → every invoice an activation
OWNER_RAW = {"fields": ["category"], "tokens": {"activation": ["new activation", "activation", "add a line", "new act", "prepaid"],
                                                "upgrade": ["upgrade"], "byod": ["customer provided", "byod", "customer owned"],
                                                "port": ["port"], "hardware_only": ["hardware only"]}}
OWNER = LC.resolve_rules(OWNER_RAW)
own = LC.count_classes(ROWS, OWNER)
shares, n_sc = LC.token_shares(ROWS, OWNER)
bare = next(x for x in shares if x["class"] == "activation" and x["token"] == "activation")
check("G1 THE GUARD measures the EFFECTIVE rule over the rows: the saved bare word 'activation' names ≥80% of the scanned lines; count_classes carries it under `refused`; rules_refused is True; with it every invoice is an activation (87 of 88 here — only the hardware-only invoice escapes by precedence; on the live export, 88 of 88 — the silent zero's twin)",
      bare["ratio"] >= LC.BROAD_RATIO and n_sc == len(ROWS) and [(b["class"], b["token"]) for b in own["refused"]] == [("activation", "activation")]
      and LC.rules_refused(own) and own["classes"]["activation"]["transactions"] == 87
      and LC.refused_tokens(ROWS, OWNER) == own["refused"], (bare, own["refused"], own["classes"]["activation"]))
g = OI.activation_gate(own, by="pat")
check("G2 the intake's gate is BLOCKED by a refused word (no 'no activations' attestation closes it), the reason names the word and its share and step 2.5a; the Stage-4 note reads 'rule refused', never 'activations: 88 new activation'",
      g["blocked"] and g["refused"] and '"activation"' in g["reason"] and "%" in g["reason"] and "2.5a" in g["reason"]
      and OI.activation_gate(own, {"no_activations": "none here"}, by="pat")["blocked"] is True
      and OI.activation_note({"activation_classes": own, "target_table": "raw_sales"}).startswith("rule refused")
      and "87 new activation" not in OI.activation_note({"activation_classes": own, "target_table": "raw_sales"}), (g["reason"], OI.activation_note({"activation_classes": own})))
sent = LC.refusal_sentence(own["refused"], own["scanned"], saving=True)
check("G3 the refusal in words names the word, its class and its share, and says nothing was saved",
      sent.startswith("Not saved") and '"activation"' in sent and "new activation" in sent and "% of the lines" in sent, sent)
ATT_RAW = {**OWNER_RAW, "broad_attested": {"activation:activation": {"by": "pat", "at": "2026-09-21T00:00:00+00:00", "ratio": 0.99}}}
att = LC.count_classes(ROWS, LC.resolve_rules(ATT_RAW))
check("G4 an attestation BY NAME (broad_attested '<class>:<word>' in the same JSON) lifts the refusal for that word only: broad still lists it (attested), refused is empty, the gate is not blocked",
      att["broad"] and att["broad"][0]["attested"] is True and att["refused"] == [] and not LC.rules_refused(att) and not OI.activation_gate(att)["blocked"])
FIXED = LC.resolve_rules({**OWNER_RAW, "tokens": {**OWNER_RAW["tokens"], "activation": ["new activation"]}})
fx = LC.count_classes(ROWS, FIXED)
check("G5 with the bare word removed the SAME rows split 39 / 25 / 23 by distinct invoice and nothing is refused",
      fx["refused"] == [] and fx["classes"]["activation"]["transactions"] == 39 and fx["classes"]["upgrade"]["transactions"] == 25
      and fx["classes"]["byod"]["transactions"] == 23, {k: v["transactions"] for k, v in fx["classes"].items()})
# THE NO-LEAK RULE
NL = LC.resolve_rules({"fields": ["category"], "tokens": {"activation": ["new activation"]}}, {"x": "premium"}, {"port": ["port"], "byod": ["byod"]})
check("G6 NO-LEAK: under tenant-declared fields an undeclared class has NO words — no house token, no legacy exec token, no mig-213 auto token (contract-type words never reach a category column); house_fill False",
      NL["tokens"] == {"activation": ["new activation"], "upgrade": [], "byod": [], "port": [], "hardware_only": []}
      and NL["auto_activation_tokens"] == [] and NL["house_fill"] is False, NL["tokens"])
KEEP = LC.resolve_rules({"tokens": {"byod": ["bring your own"]}}, None, {"port": ["ported"]})
KEEP2 = LC.resolve_rules({"fields": ["contract_type"], "tokens": {"byod": ["bring your own"]}})
check("G7 …a tenant with declared TOKENS but the house field keeps today's behaviour (house words + the legacy port token fill the undeclared classes); declaring the house field explicitly is the same",
      KEEP["tokens"]["activation"] == LC.HOUSE_TOKENS["activation"] and KEEP["tokens"]["port"] == ["ported"] and KEEP["tokens"]["byod"] == ["bring your own"]
      and KEEP["house_fill"] is True and KEEP2["tokens"]["activation"] == LC.HOUSE_TOKENS["activation"] and KEEP2["tokens"]["port"] == ["port"])
check("G8 THE PIN: an org with no declaration resolves exactly as before — every class the house list, contract_type only, house_fill True, nothing refused over any rows (house defaults are never measured)",
      LC.resolve_rules(None)["tokens"] == {c: list(LC.HOUSE_TOKENS[c]) for c in LC.CLASSES} and LC.resolve_rules(None)["fields"] == ["contract_type"]
      and LC.resolve_rules(None)["house_fill"] is True
      and LC.count_classes([dict(r, contract_type="Activation") for r in ROWS], LC.HOUSE_RULES)["refused"] == []
      and LC.refused_tokens([dict(r, contract_type="Activation") for r in ROWS], LC.HOUSE_RULES) == [])
# THE SEED — the proposal is the person's declared words minus the refused, plus the file's hits
SP = LC.suggest_rules(ROWS, OWNER)
check("G9 THE SEED: over the owner's saved row the proposal drops the refused bare word, keeps the person's other words, adds nothing that is a hint only; an undeclared class under tenant fields proposes NO words; the preview is the split",
      SP["proposal"]["tokens"]["activation"] == ["new activation", "add a line", "new act", "prepaid"]
      and SP["proposal"]["tokens"]["port"] == ["port"] and SP["refused"] == own["refused"]
      and SP["preview"]["classes"]["activation"]["transactions"] == 39 and SP["preview"]["refused"] == []
      and LC.suggest_rules(ROWS, NL)["proposal"]["tokens"]["port"] == [], SP["proposal"]["tokens"])
check("G10 …and under HOUSE rules the proposal is the hits, with an undeclared class EMPTY once a tenant field is proposed (no house word rides into a category rule)",
      prop["tokens"]["port"] == [] and prop["tokens"]["activation"] == ["new activation"] and "category" in prop["fields"], prop["tokens"])
check("G11 norm_broad_ok accepts 'class:word' strings and {class, token} objects, drops junk and unknown classes",
      LC.norm_broad_ok(["activation:activation", {"class": "upgrade", "token": "Upg"}, "nope:x", "activation:", 3, "activation:activation"])
      == ["activation:activation", "upgrade:upg"])
m = LC.merge_into_raw(OWNER_RAW, tokens={"activation": ["new activation", "activation"]}, broad_ok=["activation:activation", "upgrade:upgrade"], by="pat",
                      shares=[{"class": "activation", "token": "activation", "ratio": 0.99}])
m2 = LC.merge_into_raw(m, tokens={"activation": ["new activation"]})
check("G12 merge_into_raw records the attestation with the name and the measured share; an attestation for a word no longer in force is pruned on the next save",
      set(m["broad_attested"]) == {"activation:activation", "upgrade:upgrade"} and m["broad_attested"]["activation:activation"]["by"] == "pat"
      and m["broad_attested"]["activation:activation"]["ratio"] == 0.99 and set(m2["broad_attested"]) == {"upgrade:upgrade"}, (m.get("broad_attested"), m2.get("broad_attested")))

print(f"\n══ line class: {_pass} passed, {_fail} failed ══")
sys.exit(1 if _fail else 0)
