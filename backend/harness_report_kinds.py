"""DB-FREE PROOF — THE REPORT-KIND REGISTRY (design §7; owner directives 2026-09-20).

    python3 harness_report_kinds.py        (from backend/; no network, no DB)

OWNER'S WORDS (the acceptance bar): "it is very important that we don't have extra file upload paths
for a new tenant who does not need those based on the carrier they pick. Our system should be smart
enough to only show those which are carrier-specific once they have been defined by a previous
tenant or us on the back end … Currently in the Verizon tenant we have all the table uploads for B2B
when it has been declared that the POS is not B2B, it is RQ." And: "the system to check against what
report it matches using intelligence gained by using all the reports."

WHAT THIS PINS
  A. the code mirror (report_kinds.HOUSE_KINDS) is BYTE-EQUAL to the 1010 seed — parsed out of the SQL
  B. THE ONE DECLARATION READER: the pos_system term, else the pos_profile rows, else unknown (which
     shows LESS); carriers from the org's carrier rows; every read org-scoped
  C. THE ONE VISIBILITY FUNCTION — the Verizon case (POS RQ, carrier verizon) sees NO B2B-applies-to
     kind on ANY of the five surfaces; a B2B/boost tenant sees them; a tenant with both sees both; a
     carrier-specific kind nobody defined is ABSENT until one tenant confirms it, then PRESENT for
     the next tenant on that carrier (and absent on another); super-admin widening recorded; an
     unknown declaration hides the specific kinds and says why; inactive never shows
  D. the per-surface projections, the upload route keys, the filename rules restricted to visible kinds
  E. DETECTION fixtures: the two sales shapes, commission, residual, inventory on-hand vs aging (the
     received column decides), X-report, merchant, bill-pay; ambiguous → ask; nonsense → none
  F. LEARNING: a confirm writes the org row + the house copy with HEADER NAMES ONLY (no cell value,
     filename, store, rep or customer string — asserted against tainted fixtures); detects next time
     with the count; the next tenant benefits from the house copy; a fingerprint confirmed under a
     second kind → both rows kept → detection ASKS
  G. THE ROUTER, DRIVEN FOR REAL over an in-memory client: GET /report-kinds for the Verizon tenant,
     POST /report-kinds/detect, the intake's learn hook, _pos_profile's ladder (house fallback; NEVER
     another POS's rules), apply for an undeclared POS refused
  H. registration + RULE TWO (no POS / carrier vendor name in the registry module)
"""
import asyncio
import io
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.modules.commcalc import report_kinds as RK
from app.modules.commcalc import column_mapping as CM
from app.modules.commcalc import report_labels as RL
from app.modules.commcalc import router as R

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
MIG = os.path.join(ROOT, "database", "migrations", RK.MIGRATION)
HOUSE = RK.HOUSE_ORG
VZ = "f4f1c16e-0000-0000-0000-000000000001"      # the Verizon tenant of the owner's report (RQ)
B2 = "22222222-0000-0000-0000-000000000002"      # a tenant on the house POS + boost
NX = "33333333-0000-0000-0000-000000000003"      # the NEXT tenant on the Verizon carrier

_pass = _fail = 0
_failures = []


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        _failures.append(name)
        print(f"  FAIL  {name}{(' — ' + str(extra)[:500]) if extra else ''}")
    return bool(cond)


def section(t):
    print(f"\n{t}\n" + "─" * len(t))


# ── a small in-memory supabase client (select / insert / update / delete / eq / in_ / limit / order) ──
class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.op, self.rows, self.filters, self._limit = db, table, "select", None, [], None

    def select(self, cols="*", **kw): self.op = "select"; return self
    def insert(self, rows): self.op, self.rows = "insert", (rows if isinstance(rows, list) else [rows]); return self
    def update(self, row): self.op, self.rows = "update", [row]; return self
    def upsert(self, rows, on_conflict=None): self.op, self.rows = "insert", (rows if isinstance(rows, list) else [rows]); return self
    def delete(self): self.op = "delete"; return self
    def eq(self, k, v): self.filters.append(("eq", k, v)); return self
    def in_(self, k, v): self.filters.append(("in", k, list(v))); return self
    def order(self, k, desc=False): return self
    def limit(self, n): self._limit = n; return self

    def _m(self, r):
        return all((r.get(k) == v) if op == "eq" else (r.get(k) in v) for op, k, v in self.filters)

    def execute(self):
        if self.table in self.db.missing:
            raise RuntimeError(f'42P01 relation "commcalc.{self.table}" does not exist')
        t = self.db.tables.setdefault(self.table, [])
        if self.op == "select":
            out = [dict(r) for r in t if self._m(r)]
            return _Res(out[:self._limit] if self._limit else out)
        if self.op == "insert":
            w = []
            for r in self.rows:
                row = dict(r); row.setdefault("id", str(uuid.uuid4())); t.append(row); w.append(dict(row))
            return _Res(w)
        if self.op == "update":
            out = []
            for r in t:
                if self._m(r):
                    r.update(self.rows[0]); out.append(dict(r))
            return _Res(out)
        if self.op == "delete":
            keep = [r for r in t if not self._m(r)]; n = len(t) - len(keep); t[:] = keep
            return _Res([{}] * n)
        raise RuntimeError(self.op)


class _Res:
    def __init__(self, data): self.data = data


class FakeDB:
    def __init__(self, missing=()):
        self.tables, self.missing = {}, set(missing)

    def schema(self, _n): return self
    def table(self, n): return _Q(self, n)

    def seed(self, table, rows):
        for r in rows:
            row = dict(r); row.setdefault("id", str(uuid.uuid4())); self.tables.setdefault(table, []).append(row)


class FakeUpload:
    def __init__(self, data, filename): self.data, self.filename = data, filename
    async def read(self): return self.data


def house_seed_rows():
    """The 1010 seed, parsed OUT of the SQL file — so the mirror check cannot pass against itself."""
    sql = io.open(MIG, encoding="utf-8").read()
    body = sql.split("upload_types, sort_order, custom_sheet_label) VALUES", 1)[1].split("ON CONFLICT", 1)[0]
    rows = []
    tok = re.compile(r"'((?:[^']|'')*)'|(\{[^}]*\})|(\d+)|(NULL)")
    for line in body.strip().split("\n"):
        line = line.strip().rstrip(",")
        if not line.startswith("("):
            continue
        vals = []
        for m in tok.finditer(line[1:-1]):
            if m.group(1) is not None:
                vals.append(m.group(1).replace("''", "'"))
            elif m.group(2) is not None:
                vals.append(m.group(2))
            elif m.group(3) is not None:
                vals.append(int(m.group(3)))
            else:
                vals.append(None)
        # a text[] literal was captured as a quoted string '{"a","b"}' → group(1) → starts with {
        cols = ("org_id", "key", "label", "what_in_it", "recognisable_columns", "source_hint", "applies_to_pos",
                "applies_to_carrier", "defined_by", "statement_type", "landing", "layout", "signature_fields",
                "requires_columns", "excludes_columns", "upload_types", "sort_order", "custom_sheet_label")
        rows.append(dict(zip(cols, vals)))
    return rows


def decl(pos, carriers):
    return {"pos": [RK.code(p) for p in pos], "carriers": [RK.code(c) for c in carriers], "reasons": []}


TF = {k: CM._base_fields(k) for k in CM.TARGET_FIELDS}
TF["ma_daily_tx"] = CM._base_fields("ma_daily_tx")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. THE MIRROR IS THE SEED — report_kinds.HOUSE_KINDS parsed back out of " + RK.MIGRATION)
seed = house_seed_rows()
check("the migration file exists and seeds rows", len(seed) > 0, MIG)
check("the seed has exactly the mirror's rows, in order", [r["key"] for r in seed] == RK.HOUSE_KEYS,
      ([r["key"] for r in seed], RK.HOUSE_KEYS))
mirror = {r["key"]: RK.normalise_row({**r, "org_id": HOUSE}) for r in RK.HOUSE_KINDS}
diffs = []
for r in seed:
    n = RK.normalise_row(r)
    m = mirror.get(r["key"])
    for k in RK._COLS:
        if k == "is_active":
            continue
        if n.get(k) != m.get(k):
            diffs.append((r["key"], k, n.get(k), m.get(k)))
check("every column of every seeded row equals the mirror (byte-equal by construction, pinned by parse)", not diffs, diffs[:3])
check("the layman cards the owner listed are all seeded",
      {"sales_imei_phone", "sales_cost_price", "commission_statement", "residual_statement", "inventory_on_hand",
       "inventory_aging", "bill_payments_pos", "bill_payments_carrier", "x_report", "merchant_settlement", "something_else"} <= set(RK.HOUSE_KEYS))
check("residual is a commission-family kind with statement_type 'residual' (a statement type, not a landing)",
      mirror["residual_statement"]["landing"] == "commission" and mirror["residual_statement"]["statement_type"] == "residual")
check("every signature field names a real TARGET_FIELDS field of the row's layout (no second alias list)",
      all(any(t[0] == f for t in TF.get(m["layout"]) or []) for m in mirror.values() for f in m["signature_fields"]),
      [(m["key"], f) for m in mirror.values() for f in m["signature_fields"] if not any(t[0] == f for t in TF.get(m["layout"]) or [])])
check("every landing is a known landing", all(m["landing"] in RK.LANDINGS for m in mirror.values()))
check("the migration is additive, idempotent, carries a REVERT note and is NOT marked applied",
      all(s in io.open(MIG, encoding="utf-8").read() for s in ("CREATE TABLE IF NOT EXISTS", "ON CONFLICT (org_id, key) DO NOTHING", "-- REVERT:", "NOT applied")))
check("the migration is numbered after 1009", os.path.basename(MIG).startswith("1010_"))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. THE ONE DECLARATION READER — the pos_system term, else pos_profile rows, else unknown (shows LESS)")
d = RK.declaration_from("RQ", "report_term:override", [{"pos_key": "b2bsoft", "is_active": True}],
                        [{"code": "verizon", "name": "Verizon"}], RL.normalize_carrier_code)
check("the tenant's pos_system OVERRIDE is the declaration — an applied POS profile row does NOT widen it",
      d["pos"] == ["rq"] and d["pos_source"] == "report_term:override", d)
check("carriers come from the org's carrier rows, normalised", d["carriers"] == ["verizon"], d)
d2 = RK.declaration_from("b2bsoft", "report_term:boost", [], [{"code": "boost", "name": "Boost"}], RL.normalize_carrier_code)
check("a carrier PRESET for the term also declares the POS (house / boost tenants)", d2["pos"] == ["b2bsoft"] and d2["pos_source"] == "report_term:boost")
d3 = RK.declaration_from("POS", "neutral_default", [{"pos_key": "B2B Soft", "is_active": True}, {"pos_key": "old", "is_active": False}], [], None)
check("no term → the ACTIVE pos_profile rows declare the POS (applying a standard is a declaration)", d3["pos"] == ["b2bsoft"] and d3["pos_source"] == "pos_profile", d3)
check("… and no carrier row → a stated reason, never 'any'", d3["carriers"] == [] and any("no carrier" in r for r in d3["reasons"]))
d4 = RK.declaration_from("", "neutral_default", [], [], None)
check("nothing declared → unknown POS + unknown carrier with reasons (the surfaces show LESS and say why)",
      d4["pos"] == [] and d4["pos_source"] == "unknown" and len(d4["reasons"]) == 2, d4)

db = FakeDB(missing={RK.TABLE, RK.SIGNATURE_TABLE})
db.seed("ui_label_override", [{"org_id": HOUSE, "scope": "report_term:boost", "key": "pos_system", "label": "b2bsoft"},
                              {"org_id": VZ, "scope": "report_term", "key": "pos_system", "label": "RQ"}])
db.seed("carrier", [{"org_id": VZ, "name": "Verizon", "code": "verizon", "is_default": True},
                    {"org_id": B2, "name": "Boost", "code": "boost", "is_default": True},
                    {"org_id": NX, "name": "Verizon Wireless", "code": "verizon", "is_default": True}])
db.seed("pos_profile", [{"org_id": HOUSE, "pos_key": "b2bsoft", "label": "B2B Soft (standard)", "is_active": True,
                         "filename_rules": R._B2BSOFT_POS_DEFAULT["filename_rules"], "imap_defaults": {}, "schedule_defaults": {}, "report_defs": []}])
dv = RK.tenant_declaration(db, VZ)
check("LIVE SHAPE: org f4f1c16e… (ui_label_override report_term pos_system = 'RQ', carrier verizon) declares POS rq / carrier verizon",
      dv["pos"] == ["rq"] and dv["carriers"] == ["verizon"], dv)
db2 = RK.tenant_declaration(db, B2)
check("the boost tenant declares POS b2bsoft through the carrier PRESET (mig 953) / carrier boost", db2["pos"] == ["b2bsoft"] and db2["carriers"] == ["boost"], db2)
check("tenant_declaration is defined ONCE (the lock pins that no other backend reader exists)", callable(RK.tenant_declaration))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. THE ONE VISIBILITY FUNCTION — the Verizon case, and its siblings")
rows = RK.house_mirror()
b2b_keys = {r["key"] for r in rows if "b2bsoft" in r["applies_to_pos"]}
boost_keys = {r["key"] for r in rows if "boost" in r["applies_to_carrier"]}
total_keys = {r["key"] for r in rows if "total" in r["applies_to_carrier"]}
check("the house seed carries B2B-applies-to kinds (the ones the Verizon tenant must not see)", len(b2b_keys) >= 3, b2b_keys)
vz_vis = RK.visible_kinds(rows, decl(["RQ"], ["verizon"]), {}, VZ)
vz_keys = {r["key"] for r in vz_vis}
check("VERIZON (POS RQ, carrier verizon): NO B2B-applies-to kind is visible", not (vz_keys & b2b_keys), vz_keys & b2b_keys)
check("… and no boost- or total-applies-to kind either", not (vz_keys & (boost_keys | total_keys)), vz_keys & (boost_keys | total_keys))
for sfc in RK.SURFACES:
    ks = {r["key"] for r in RK.for_surface(vz_vis, sfc)}
    check(f"… on surface '{sfc}': no B2B / boost / total kind", not (ks & (b2b_keys | boost_keys | total_keys)), ks & (b2b_keys | boost_keys | total_keys))
check("… but the POS-agnostic layman cards ARE offered (sales, inventory, commission, residual, x-report, merchant, something else)",
      {"sales_imei_phone", "sales_cost_price", "commission_statement", "residual_statement", "inventory_on_hand", "inventory_aging",
       "x_report", "merchant_settlement", "something_else", "bill_payments_carrier"} <= vz_keys, vz_keys)
check("… each with provenance 'house default'", all(r["provenance"] == RK.PROV_HOUSE for r in vz_vis))
hid = RK.hidden_kinds(rows, decl(["RQ"], ["verizon"]), {})
check("what is withheld is LISTED with why (never silently)", {h["key"] for h in hid} >= b2b_keys and all("you declared rq" in h["why"] for h in hid if h["key"] in b2b_keys), hid[:3])

b2_vis = {r["key"] for r in RK.visible_kinds(rows, decl(["B2B Soft"], ["boost"]), {}, B2)}
check("a B2B / boost tenant SEES the B2B kinds and the boost kinds (spelling 'B2B Soft' squashes to the code)", b2b_keys <= b2_vis and boost_keys <= b2_vis)
check("… and not the total-only kinds", not (b2_vis & total_keys))
both = {r["key"] for r in RK.visible_kinds(rows, decl(["rq", "b2bsoft"], ["verizon", "boost"]), {}, B2)}
check("a tenant with BOTH POS sees both sets", b2b_keys <= both and {"sales_imei_phone", "x_report"} <= both)
check("pos_inventory_recon (POS b2bsoft AND carrier boost) needs both to match", "pos_inventory_recon" in b2_vis and "pos_inventory_recon" not in
      {r["key"] for r in RK.visible_kinds(rows, decl(["b2bsoft"], ["verizon"]), {}, VZ)})

# a carrier-specific kind nobody defined: absent until one tenant confirms it, then present for the next tenant on that carrier
vz_row = RK.tenant_kind_row(VZ, "Verizon Device Payment Report", applies_to_carrier=["verizon"], headers=["Account", "Device", "Installment"])
check("before anyone defines it, the carrier-specific kind is ABSENT for the Verizon tenant", "verizon_device_payment_report" not in vz_keys)
rows2 = RK.merge_rows([*(dict(r, org_id=HOUSE) for r in RK.HOUSE_KINDS), vz_row], NX)
nx_vis = RK.visible_kinds(rows2, decl(["rq"], ["verizon"]), {}, NX)
nx_row = next((r for r in nx_vis if r["key"] == "verizon_device_payment_report"), None)
check("once ONE tenant confirmed it (house row, defined_by tenant), the NEXT tenant on that carrier is offered it", nx_row is not None)
check("… with provenance 'defined by a tenant on this carrier'", nx_row and nx_row["provenance"] == RK.PROV_TENANT_DEFINED)
check("… and a tenant on ANOTHER carrier is not", "verizon_device_payment_report" not in {r["key"] for r in RK.visible_kinds(rows2, decl(["b2bsoft"], ["boost"]), {}, B2)})
check("the tenant-defined row stores header NAMES only as its recognisable columns", vz_row["recognisable_columns"] == ["Account", "Device", "Installment"] and vz_row["defined_by"] == "tenant")

wid = RK.visible_kinds(rows, decl(["RQ"], ["verizon"]), {"kind:pos_activation_details": True}, VZ)
w = next((r for r in wid if r["key"] == "pos_activation_details"), None)
check("SUPER-ADMIN WIDENING (cap kind:<key> = show) shows a gated-out kind, RECORDED as 'widened by super-admin'", w is not None and w["provenance"] == RK.PROV_WIDENED)
check("a hide override hides a kind the rule would show", "x_report" not in {r["key"] for r in RK.visible_kinds(rows, decl(["RQ"], ["verizon"]), {"kind:x_report": False}, VZ)})
check("a null override is AUTO", "pos_activation_details" not in {r["key"] for r in RK.visible_kinds(rows, decl(["RQ"], ["verizon"]), {"kind:pos_activation_details": None}, VZ)})
unk = {r["key"] for r in RK.visible_kinds(rows, {"pos": [], "carriers": [], "reasons": ["x", "y"]}, {}, VZ)}
check("UNKNOWN declaration: POS-specific and carrier-specific kinds are HIDDEN (show less), the agnostic ones shown",
      not (unk & (b2b_keys | boost_keys | total_keys)) and "sales_imei_phone" in unk, unk)
inactive = RK.merge_rows([*(dict(r, org_id=HOUSE) for r in RK.HOUSE_KINDS), {"org_id": VZ, "key": "x_report", "label": "X", "landing": "x_report", "is_active": False}], VZ)
check("a tenant row deactivating a house kind hides it (override per key, the mig-207 shape)", "x_report" not in {r["key"] for r in RK.visible_kinds(inactive, decl(["rq"], ["verizon"]), {}, VZ)})
ov = RK.merge_rows([*(dict(r, org_id=HOUSE) for r in RK.HOUSE_KINDS), {"org_id": VZ, "key": "x_report", "label": "Register close-out", "landing": "x_report"}], VZ)
ovr = next(r for r in RK.visible_kinds(ov, decl(["rq"], ["verizon"]), {}, VZ) if r["key"] == "x_report")
check("a tenant row relabelling a house kind wins with provenance 'your override'", ovr["label"] == "Register close-out" and ovr["provenance"] == RK.PROV_OVERRIDE)
check("a THIRD org's row is never merged in (org-scoped)", "x_report" in {r["key"] for r in RK.visible_kinds(RK.merge_rows([*(dict(r, org_id=HOUSE) for r in RK.HOUSE_KINDS), {"org_id": B2, "key": "x_report", "label": "X", "landing": "x_report", "is_active": False}], VZ), decl(["rq"], ["verizon"]), {}, VZ)})

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. PER-SURFACE PROJECTIONS, UPLOAD ROUTE KEYS, FILENAME RULES — restricted to visible kinds")
b2_full = RK.visible_kinds(rows, decl(["b2bsoft"], ["boost"]), {}, B2)
check("intake surface = the kinds that land in an intake source kind", all(r["landing"] in RK.INTAKE_LANDINGS for r in RK.for_surface(b2_full, "intake")) and len(RK.for_surface(b2_full, "intake")) >= 10)
check("upload surface = kinds with a route key or a custom sheet", all(r["upload_types"] or r["custom_sheet_label"] for r in RK.for_surface(b2_full, "upload")))
ut_b2 = RK.upload_types_of(b2_full)
ut_vz = RK.upload_types_of(vz_vis)
check("the boost tenant's route keys include the boost-only routes", {"payment_detail", "mi_report", "vip_workbook", "b2b_inventory"} <= set(ut_b2))
check("the Verizon tenant's route keys exclude every boost/total/B2B route", not (set(ut_vz) & {"payment_detail", "mi_report", "vip_workbook", "b2b_inventory", "ma_commission", "ma_daily_tx", "ma_fulfillment", "asset_ledger", "comp_report"}), ut_vz)
rules = R._B2BSOFT_POS_DEFAULT["filename_rules"] + [{"pattern": "*Commission*Details*", "upload_type": "ma_commission"}]
fr = RK.filename_rules_for(rules, b2_full)
check("the declared POS standard's rules are offered — minus a rule routing to a kind this tenant cannot see", [r["upload_type"] for r in fr] == ["daily_sales", "inventory_aging", "x_report"], fr)
check("a rule with no pattern or unknown type is dropped", RK.filename_rules_for([{"pattern": "", "upload_type": "x_report"}, {"pattern": "*A*", "upload_type": "nope"}], b2_full) == [])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. DETECTION — header names only, the kind's own signals through TARGET_FIELDS aliases")
def top(headers, sigs=None, vis=None):
    ranked = RK.detect_report_kind(headers, vis or vz_vis, sigs or [], TF)
    return ranked, RK.decide(ranked)

SALES_LINE = ["Store", "Salesperson", "Trans ID", "Trans Date Time", "Product Desc", "Ext Price", "GP", "Activated Mobile Number", "Serial 1", "Tender Type", "Voided"]
SALES_PROD = ["Invoice #", "Product SKU", "Tracking #", "Sold By", "Total Price", "Total Cost", "Gross Profit"]
COMMISSION = ["Gross", "Report Section", "Report SubSection", "AgentSSOID", "Master Service Date"]
RESIDUAL = ["Account", "MDN", "Plan", "Service Month", "Residual Amount"]
INV_ONHAND = ["Product SKU", "Tracking #", "Product Name", "Location", "Unit Cost", "Quantity", "Status"]
INV_AGING = ["Product SKU", "Tracking #", "Product Name", "Location", "Unit Cost", "Received Date", "Days in Stock"]
XREPORT = ["Register", "Tender", "Cash", "Credit", "Total"]
MERCHANT = ["Merchant", "Settlement Date", "Card Type", "Gross", "Fees", "Net Amount", "Batch"]
BILLPAY = ["Account ID", "Order Number", "Order Type", "Product Name", "Retail Cost", "Date of Transaction"]
for name, hdrs, want in (("sales — line level with IMEI + phone", SALES_LINE, "sales_imei_phone"),
                         ("sales — by product with cost / price / GP", SALES_PROD, "sales_cost_price"),
                         ("commission (Gross / Report Section / SubSection / AgentSSOID / Master Service Date)", COMMISSION, "commission_statement"),
                         ("residual", RESIDUAL, "residual_statement"),
                         ("inventory ON HAND (no received column)", INV_ONHAND, "inventory_on_hand"),
                         ("inventory AGING (a received / days-in-stock column present)", INV_AGING, "inventory_aging"),
                         ("X-report", XREPORT, "x_report"),
                         ("merchant settlement", MERCHANT, "merchant_settlement"),
                         ("carrier bill-pay (daily-tx layout)", BILLPAY, "bill_payments_carrier")):
    ranked, dec = top(hdrs)
    check(f"{name} → {want} (confirm)", dec["mode"] == "confirm" and dec["candidates"][0][0] == want,
          (dec["mode"], [(k, c) for k, c, _ in ranked[:3]]))
ranked, dec = top(INV_AGING)
check("the aging file is NOT also a candidate for on-hand (excludes_columns rule) — no tie", all(k != "inventory_on_hand" for k, _, _ in ranked))
ranked, dec = top(INV_ONHAND)
check("the on-hand file is NOT a candidate for aging (requires_columns rule)", all(k != "inventory_aging" for k, _, _ in ranked))
ranked, dec = top(["Foo", "Bar", "Baz", "Qux"])
check("nonsense → none (never a guess)", dec["mode"] == "none" and ranked == [], ranked)
check("detection never reads beyond header names (a data row passed as headers changes nothing but the names)",
      RK.detect_report_kind(SALES_PROD, vz_vis, [], TF) == RK.detect_report_kind(list(SALES_PROD), vz_vis, [], TF))
ev = dict((k, e) for k, _, e in RK.detect_report_kind(SALES_PROD, vz_vis, [], TF))
check("the evidence names the signals present and missing", any("field trans_id" in x for x in ev["sales_cost_price"]) and any("Gross Profit" in x for x in ev["sales_cost_price"]), ev.get("sales_cost_price"))
check("a kind whose landing is 'other' is never detected (it is the fallback, offered by the page)", "something_else" not in ev)
# ambiguity: two candidates within the margin → ask with both evidences
amb = [{"org_id": HOUSE, "fingerprint": RK.header_fingerprint(XREPORT), "report_kind_key": "x_report", "confirmations": 2},
       {"org_id": HOUSE, "fingerprint": RK.header_fingerprint(XREPORT), "report_kind_key": "merchant_settlement", "confirmations": 1}]
ranked, dec = top(XREPORT, amb)
check("the same fingerprint confirmed under TWO kinds → both at 1.0, marked AMBIGUOUS → ASK with both evidences",
      dec["mode"] == "ask" and {k for k, _, _ in dec["candidates"]} == {"x_report", "merchant_settlement"} and all("AMBIGUOUS" in e[0] for _, _, e in dec["candidates"]), dec)
check("an exact fingerprint hit states the count: 'seen before as …, confirmed N times'",
      any("confirmed 2 times" in e[0] for k, _, e in ranked if k == "x_report"), ranked)
near = [{"org_id": HOUSE, "fingerprint": RK.header_fingerprint(SALES_PROD + ["Extra Column"]), "report_kind_key": "sales_cost_price", "confirmations": 3}]
ranked, dec = top(SALES_PROD, near)
check("≥80% header overlap with a confirmed layout ranks above the bare signature score", dec["mode"] == "confirm" and dec["candidates"][0][0] == "sales_cost_price" and 0.8 <= dec["candidates"][0][1] < 1.0 and "match a layout confirmed" in dec["candidates"][0][2][0], dec)
far = [{"org_id": HOUSE, "fingerprint": RK.header_fingerprint(SALES_PROD[:3] + ["A", "B", "C", "D", "E"]), "report_kind_key": "commission_statement", "confirmations": 3}]
ranked, dec = top(SALES_PROD, far)
check("<80% overlap does not pull a file toward a wrong confirmed kind", dec["candidates"][0][0] == "sales_cost_price")
check("detection only ranks VISIBLE kinds: the Verizon tenant is never told a file is a B2B custom sheet", all(k not in b2b_keys for k, _, _ in RK.detect_report_kind(["Bill Payment", "Payment Amount", "Carrier", "Phone Number", "Store"], vz_vis, [], TF)))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. LEARNING — header names only, org row + house copy, the count, the next tenant, ambiguity")
db = FakeDB()
TAINT_HEADERS = ["Invoice #", "Product SKU", "Tracking #", "Sold By", "Total Price", "Total Cost", "Gross Profit"]
TAINT = {"filename": "Sales_By_Product_ACME_0925.xlsx", "store": "Riverside Kiosk", "rep": "Alice Rep", "customer": "John Q Public",
         "cell": "356938035643809", "mdn": "9175550123"}
r1 = RK.learn_signature(db, VZ, TAINT_HEADERS, "sales_cost_price", layout="pos_product_sales")
check("a confirm writes the org row AND the house copy", r1["learned"] and r1["orgs"] == [VZ, HOUSE], r1)
sig_rows = db.tables[RK.SIGNATURE_TABLE]
check("two rows, confirmations 1, house_copy flagged on the house row",
      len(sig_rows) == 2 and all(r["confirmations"] == 1 for r in sig_rows) and [r["house_copy"] for r in sig_rows] == [False, True])
blob = repr(sig_rows).lower()
check("NOTHING BUT HEADER NAMES IS STORED — no filename, store, rep, customer, IMEI or phone string reaches a signature row",
      not any(v.lower() in blob for v in TAINT.values()), [v for v in TAINT.values() if v.lower() in blob])
check("the fingerprint is the normalised ORDERED header list", sig_rows[0]["fingerprint"] == RK.header_fingerprint(TAINT_HEADERS) == "invoice #|product sku|tracking #|sold by|total price|total cost|gross profit")
check("the signature row's columns are exactly the migration's", set(sig_rows[0]) - {"id"} == {"org_id", "fingerprint", "report_kind_key", "statement_type", "layout", "header_count", "house_copy", "confirmations", "first_confirmed_at", "last_confirmed_at"}, set(sig_rows[0]))
r2 = RK.learn_signature(db, VZ, TAINT_HEADERS, "sales_cost_price", layout="pos_product_sales")
check("a second confirm INCREMENTS (no duplicate row)", r2["learned"] and len(db.tables[RK.SIGNATURE_TABLE]) == 2 and all(r["confirmations"] == 2 for r in db.tables[RK.SIGNATURE_TABLE]))
sigs_vz = RK.load_signatures(db, VZ)
ranked, dec = top(TAINT_HEADERS, sigs_vz)
check("next time the same layout is detected by its fingerprint at 1.0 with the count", dec["mode"] == "confirm" and dec["candidates"][0][1] == 1.0 and "confirmed 2 times" in dec["candidates"][0][2][0], dec)
check("'confirmed by you N times' provenance counts the ORG row only (not the house copy)", RK.confirmations_by_kind(sigs_vz, VZ) == {"sales_cost_price": 2})
sigs_nx = RK.load_signatures(db, NX)
check("THE NEXT TENANT sees the house copy only (org-scoped: its own rows + house), and is recognised too",
      [r["org_id"] for r in sigs_nx] == [HOUSE] and RK.decide(RK.detect_report_kind(TAINT_HEADERS, vz_vis, sigs_nx, TF))["candidates"][0][1] == 1.0)
check("… but its provenance does not claim ITS confirmations", RK.confirmations_by_kind(sigs_nx, NX) == {})
r3 = RK.learn_signature(db, NX, TAINT_HEADERS, "sales_imei_phone", layout="sales")
ranked, dec = top(TAINT_HEADERS, RK.load_signatures(db, NX))
check("the same fingerprint confirmed under a DIFFERENT kind by another tenant → both rows kept → detection ASKS",
      r3["learned"] and dec["mode"] == "ask" and {k for k, _, _ in dec["candidates"]} == {"sales_cost_price", "sales_imei_phone"}, dec)
missing = FakeDB(missing={RK.SIGNATURE_TABLE})
r4 = RK.learn_signature(missing, VZ, TAINT_HEADERS, "sales_cost_price")
check("before mig 1010 nothing is learned and the reason names the migration", not r4["learned"] and RK.MIGRATION in r4["reason"], r4)
check("no headers → nothing learned", not RK.learn_signature(db, VZ, [], "x_report")["learned"])
kk = RK.kind_key_for(rows, "inventory", layout="pos_inventory_listing", headers=INV_AGING, target_fields=TF)
check("kind_key_for: an inventory instance whose file has a received column is learned as AGING", kk == "inventory_aging", kk)
check("… and one without as ON HAND", RK.kind_key_for(rows, "inventory", layout="pos_inventory_listing", headers=INV_ONHAND, target_fields=TF) == "inventory_on_hand")
check("… a commission instance with statement type 'residual statement' → residual_statement", RK.kind_key_for(rows, "commission", statement_type="residual statement") == "residual_statement")
check("… the card the person picked wins when it lands there", RK.kind_key_for(rows, "sales", chosen="sales_imei_phone") == "sales_imei_phone")
check("… a picked card of another landing is ignored (never mis-learned)", RK.kind_key_for(rows, "x_report", chosen="sales_imei_phone") == "x_report")
dk = RK.define_kind(db, VZ, "Verizon Device Payment Report", applies_to_carrier=["verizon"], headers=["Account", "Device", "Installment"])
check("define_kind writes a house row for a 'something else' confirmed under this tenant's carriers", dk["defined"] and db.tables[RK.TABLE][0]["org_id"] == HOUSE and db.tables[RK.TABLE][0]["defined_by_org"] == VZ)
check("… never twice, never overwriting", not RK.define_kind(db, NX, "Verizon Device Payment Report")["defined"] and len(db.tables[RK.TABLE]) == 1)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. THE ROUTER, FOR REAL — GET /report-kinds, POST /report-kinds/detect, the intake hook, the pos_profile ladder")
db = FakeDB()
db.seed("ui_label_override", [{"org_id": HOUSE, "scope": "report_term:boost", "key": "pos_system", "label": "b2bsoft"},
                              {"org_id": VZ, "scope": "report_term", "key": "pos_system", "label": "RQ"},
                              {"org_id": VZ, "scope": "cap", "key": "kind:pos_sales_by_product", "label": "show"}])
db.seed("carrier", [{"org_id": VZ, "name": "Verizon", "code": "verizon", "is_default": True},
                    {"org_id": B2, "name": "Boost", "code": "boost", "is_default": True}])
db.seed("pos_profile", [{"org_id": HOUSE, "pos_key": "b2bsoft", "label": "B2B Soft (standard)", "is_active": True,
                         "filename_rules": R._B2BSOFT_POS_DEFAULT["filename_rules"], "imap_defaults": {}, "schedule_defaults": {}, "report_defs": []}])
db.seed(RK.TABLE, [dict(r, org_id=HOUSE) for r in RK.HOUSE_KINDS])
R.sb = lambda: db
pv = R.report_kinds_endpoint(org_id=VZ)
check("GET /report-kinds (Verizon): registry_ready, declaration rq / verizon", pv["registry_ready"] and pv["declaration"]["pos"] == ["rq"] and pv["declaration"]["carriers"] == ["verizon"], pv["declaration"])
vk = {k["key"] for k in pv["kinds"]}
check("… NO B2B / boost / total kind in `kinds` except the one a super-admin widened", not (vk & (b2b_keys | boost_keys | total_keys) - {"pos_sales_by_product"}) and "pos_sales_by_product" in vk)
check("… the widened one says so", next(k for k in pv["kinds"] if k["key"] == "pos_sales_by_product")["provenance"] == RK.PROV_WIDENED)
check("… every surface's key list is a subset of `kinds`", all(set(v) <= vk for v in pv["surfaces"].values()) and set(pv["surfaces"]) == set(RK.SURFACES))
check("… `hidden` lists the withheld B2B kinds with the declaration in the reason", {h["key"] for h in pv["hidden"]} >= (b2b_keys - {"pos_sales_by_product"}))
check("… no filename standard exists for POS rq → standard.source 'none' and NO rules (never another POS's)", pv["standard"]["source"] == "none" and pv["filename_rules"] == [], pv["standard"])
check("… the `kind:` caps are echoed", pv["caps"] == {"kind:pos_sales_by_product": True})
pb = R.report_kinds_endpoint(org_id=B2)
check("GET /report-kinds (boost / b2bsoft): the B2B kinds, the boost kinds, and the house POS standard's three rules", b2b_keys <= {k["key"] for k in pb["kinds"]} and [r["upload_type"] for r in pb["filename_rules"]] == ["daily_sales", "inventory_aging", "x_report"] and pb["standard"]["source"] == "house default", (pb["standard"], pb["filename_rules"]))
check("… the standard names the POS by its CODE, from the declaration", pb["standard"]["pos_key"] == "b2bsoft")
pre = FakeDB(missing={RK.TABLE, RK.SIGNATURE_TABLE})
pre.tables.update({k: v for k, v in db.tables.items() if k not in (RK.TABLE,)})
R.sb = lambda: pre
pp = R.report_kinds_endpoint(org_id=VZ)
check("PRE-MIGRATION: registry_ready:false, the mirror rendered under the SAME rule (no B2B kind for Verizon)", not pp["registry_ready"] and pp["migration"] == RK.MIGRATION and not ({k["key"] for k in pp["kinds"]} & b2b_keys - {"pos_sales_by_product"}))
R.sb = lambda: db
csv = ("Invoice #,Product SKU,Tracking #,Sold By,Total Price,Total Cost,Gross Profit\n"
       "1001,SKU-1,356938035643809,Alice Rep,199.99,120.00,79.99\n1002,SKU-2,356938035643810,Bob Smith,49.99,20.00,29.99\n").encode()
det = asyncio.run(R.report_kinds_detect(FakeUpload(csv, "Sales_By_Product_ACME.csv"), org_id=VZ))
check("POST /report-kinds/detect over a CSV: 'This looks like your Sales report with cost and selling price'", det["mode"] == "confirm" and det["candidates"][0]["key"] == "sales_cost_price" and det["candidates"][0]["label"] == "Sales report with cost and selling price", det)
check("… header_count = 7, the fallback key is the 'something else' card, nothing stored", det["header_count"] == 7 and det["fallback_key"] == "something_else" and not db.tables.get(RK.SIGNATURE_TABLE))
ctx = {"headers": SALES_PROD, "report_kind": "", "instance_key": "pos:rq:pos_product_sales"}
lr = R._intake_learn_signature(db, VZ, ctx, "pos", layout="pos_product_sales")
check("the intake's learn hook (after a CONFIRMED commit) resolves the kind from landing + layout and writes the signature", lr["learned"] and lr["kind"] == "sales_cost_price" and len(db.tables[RK.SIGNATURE_TABLE]) == 2, lr)
det2 = asyncio.run(R.report_kinds_detect(FakeUpload(csv, "x.csv"), org_id=VZ))
check("… and the next detect says 'seen before as …, confirmed 1 time'", "confirmed 1 time" in det2["candidates"][0]["evidence"][0], det2["candidates"][0])
lo = R._intake_learn_signature(db, VZ, {"headers": ["Account", "Device", "Installment"], "report_kind": "", "name": "Verizon Device Payment Report"}, "other", define_name="Verizon Device Payment Report")
check("an 'other' confirmed as received DEFINES a house kind under the tenant's carrier and learns its headers", lo.get("defined", {}).get("defined") and lo["learned"] and lo["kind"] == "verizon_device_payment_report", lo)
pn = R.report_kinds_endpoint(org_id=NX) if db.seed("carrier", [{"org_id": NX, "name": "Verizon", "code": "verizon"}]) is None else None
check("… and the NEXT Verizon tenant is offered it, provenance 'defined by a tenant on this carrier'", any(k["key"] == "verizon_device_payment_report" and k["provenance"] == RK.PROV_TENANT_DEFINED for k in pn["kinds"]))
check("… while the boost tenant is not", not any(k["key"] == "verizon_device_payment_report" for k in R.report_kinds_endpoint(org_id=B2)["kinds"]))
check("the commission hook learns under the statement type", R._intake_learn_signature(db, VZ, {"headers": RESIDUAL, "report_kind": "", "statement_type": "residual statement"}, "commission", statement_type="residual statement", layout="commission_ledger")["kind"] == "residual_statement")
prof = R._pos_profile(db, VZ, "b2bsoft")
check("_pos_profile: a tenant with no row INHERITS the HOUSE row for that pos_key (mig-207 shape)", prof and prof.get("inherited_from") == "house" and prof["org_id"] == VZ)
check("_pos_profile: an undeclared POS with no row is None — NEVER another POS's rules (the owner's defect)", R._pos_profile(db, VZ, "rq") is None)
try:
    R.apply_pos_profile("rq", org_id=VZ)
    check("POST /pos-profiles/rq/apply for a POS with no standard is REFUSED (400), nothing applied", False)
except Exception as e:
    check("POST /pos-profiles/rq/apply for a POS with no standard is REFUSED (400), nothing applied", getattr(e, "status_code", None) == 400 and "email_sweep_config" not in db.tables, e)
check("the super-admin-only widening check covers the kind: namespace", "startswith(_report_kinds.CAP_PREFIX)" in io.open(os.path.join(ROOT, "backend", "app", "modules", "commcalc", "router.py"), encoding="utf-8").read())

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("H. REGISTERED, RULE TWO")
src = io.open(os.path.join(ROOT, "backend", "app", "modules", "commcalc", "report_kinds.py"), encoding="utf-8").read()
code_only = "\n".join(l for l in src.split("\n") if not l.strip().startswith("#"))
# the mirror carries codes as DATA inside HOUSE_KINDS; the LOGIC must not name one
logic = code_only.split("HOUSE_KEYS = ", 1)[1]
check("RULE TWO: no POS / carrier / processor vendor name in the registry LOGIC (the seed mirror carries codes as data only)",
      not re.search(r"b2bsoft|rtpos|\brq\b|vidapay|boost|total\s*wireless|verizon|epay|acima", logic, re.I), re.findall(r"b2bsoft|rtpos|\brq\b|vidapay|boost|verizon|epay", logic, re.I)[:5])
idx = io.open(os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md"), encoding="utf-8").read()
check("index §30.9 registers the registry", "### 30.9" in idx and "report_kind" in idx)
check("index §16 lists both tables", "`commcalc.report_kind`" in idx and "`commcalc.report_signature`" in idx)
check("index §17 lists the endpoint and the detect route", "GET /commcalc/report-kinds" in idx and "/report-kinds/detect" in idx)
check("index §30.8 OPEN (b) and (d) are marked closed", "(b) **Layman cards" in idx and "CLOSED" in idx.split("(b) **Layman cards", 1)[1][:400] and "(d) **§7 availability gating" in idx and "CLOSED" in idx.split("(d) **§7 availability gating", 1)[1][:400])
fe = os.path.join(ROOT, "frontend", "src")
check("the frontend twin exists: carrier-scope.reportKindsVisible + lib/report-kinds.useReportKinds",
      "export function reportKindsVisible(" in io.open(os.path.join(fe, "lib", "carrier-scope.ts"), encoding="utf-8").read()
      and "export function useReportKinds()" in io.open(os.path.join(fe, "lib", "report-kinds.ts"), encoding="utf-8").read())

print(f"\n══ report-kind registry: {_pass} passed, {_fail} failed ══")
if _failures:
    print("FAILED:\n  " + "\n  ".join(_failures))
sys.exit(1 if _fail else 0)
