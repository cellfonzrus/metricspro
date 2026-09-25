#!/usr/bin/env python3
"""PROOF — the royalty report books the P&L, profit centers are scopes of the ONE engine, the unclaimed sale line is
reported, and every existing tenant's P&L is BYTE-IDENTICAL (mig 1022, index §37). Needs the app's dependencies (it
drives the REAL coa.build_inputs, engine._assemble, statement_engine, statement_filter and the royalty router over an
in-memory client that genuinely filters) — CI: the finance-royalty-proof job (pip install) in carrier-vocab-guard.yml.

  §A BYTE-IDENTITY, the house org path: the P&L payload (consolidated + every store scope, engine._assemble over
     coa.PL_SPEC) equals the FROZEN ORACLE captured from the pre-change coa.py (commit 2f2bdfd) over the same fixture —
     with the new tables ABSENT (pre-migration), PRESENT-and-empty, and populated for ANOTHER org (no leak). The
     unclaimed sale lines appear ONLY in the side meta, never on the payload. (Set OLD_COA=<path to the 2f2bdfd coa.py>
     to re-derive the oracle live instead of reading the frozen copy.)
  §B the royalty report books: the heads, the store (via its profit center), the note, the fees below gross profit,
     excluded / unmapped in statement meta; `book_pl=false` books nothing and still reports.
  §C the unclaimed-line map books to a revenue line; a target the royalty report books is suppressed (never both).
  §D PL_SPEC carries the new heads as `auto_opt`, in the sections the vocabulary books to.
  §E statement_engine._scopes: no profit center = byte-identical scope list; a profit center is a scope whose P&L is
     the sum of its stores; its journal is its stores' entries only (never a tenant-level entry).
  §F statement_filter.scope_predicate('profit_center:…') — members only; unknown center / failure = NOTHING (fail closed).
  §G the router over the fake client: parse writes nothing; import stores the REPORT's figures + flags; a re-import
     replaces; manual entry; recon against raw_sales_product; the dashboard summary; every write org-scoped.

Run: cd backend && python3 harness_royalty_pl.py        (no DB, no network)
"""
import copy
import json
import os
import sys
import uuid
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.account import coa, engine, statement_engine as SE, statement_filter as SF  # noqa: E402
from app.modules.account import balance_sheet as BS, royalty as R, centers as C, royalty_router as RR  # noqa: E402

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  " + label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:600]))


def section(t):
    print("\n── " + t)


# ══ THE IN-MEMORY CLIENT — filters for real; an ABSENT table raises like Postgres (42P01) ═════════════
class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.op, self.rows_in = db, table, [], "select", None
        self._limit = self._range = None
        self.on_conflict = None

    def select(self, *_a, **_k): self.op = "select"; return self
    def insert(self, rows): self.op, self.rows_in = "insert", rows if isinstance(rows, list) else [rows]; return self
    def upsert(self, rows, on_conflict=None):
        self.op, self.rows_in, self.on_conflict = "upsert", rows if isinstance(rows, list) else [rows], on_conflict
        return self
    def update(self, row): self.op, self.rows_in = "update", [row]; return self
    def delete(self): self.op = "delete"; return self
    def eq(self, k, v): self.filters.append(("eq", k, v)); return self
    def in_(self, k, v): self.filters.append(("in", k, list(v))); return self
    def gte(self, k, v): self.filters.append(("gte", k, v)); return self
    def lt(self, k, v): self.filters.append(("lt", k, v)); return self
    def lte(self, k, v): self.filters.append(("lte", k, v)); return self
    def ilike(self, k, v): self.filters.append(("ilike", k, v)); return self
    def is_(self, k, v): self.filters.append(("is", k, v)); return self
    def neq(self, k, v): self.filters.append(("neq", k, v)); return self
    def like(self, k, v): self.filters.append(("like", k, v)); return self
    def order(self, *_a, **_k): return self
    def limit(self, n): self._limit = n; return self
    def range(self, lo, hi): self._range = (lo, hi); return self

    def _ok(self, r):
        for op, k, v in self.filters:
            x = r.get(k)
            if op == "eq" and x != v: return False
            if op == "neq" and x == v: return False
            if op == "in" and x not in v: return False
            if op == "gte" and not (x is not None and str(x) >= str(v)): return False
            if op == "lt" and not (x is not None and str(x) < str(v)): return False
            if op == "lte" and not (x is not None and str(x) <= str(v)): return False
            if op == "ilike" and not (x is not None and str(v).strip("%").lower() in str(x).lower()): return False
            if op == "like" and not (x is not None and str(x).startswith(str(v).rstrip("%"))): return False
            if op == "is" and not (v == "null" and x is None): return False
        return True

    def execute(self):
        db = self.db
        if self.table in db.absent:
            raise RuntimeError('42P01 relation "%s" does not exist' % self.table)
        t = db.tables.setdefault(self.table, [])
        if self.op == "select":
            out = [dict(r) for r in t if self._ok(r)]
            if self._range:
                out = out[self._range[0]:self._range[1] + 1]
            if self._limit is not None:
                out = out[:self._limit]
            return SimpleNamespace(data=out)
        db.writes.append((self.op, self.table, list(self.filters), [dict(r) for r in (self.rows_in or [])]))
        if self.op == "insert":
            out = []
            for r in self.rows_in:
                row = {"id": str(uuid.uuid4()), **r}
                t.append(row)
                out.append(dict(row))
            return SimpleNamespace(data=out)
        if self.op == "upsert":
            keys = [k.strip() for k in (self.on_conflict or "org_id").split(",")]
            for r in self.rows_in:
                hit = next((x for x in t if all(x.get(k) == r.get(k) for k in keys)), None)
                if hit:
                    hit.update(r)
                else:
                    t.append({"id": str(uuid.uuid4()), **r})
            return SimpleNamespace(data=[])
        if self.op == "delete":
            gone = [r for r in t if self._ok(r)]
            t[:] = [r for r in t if not self._ok(r)]
            if self.table == "royalty_report":                   # ON DELETE CASCADE
                ids = {r["id"] for r in gone}
                db.tables["royalty_report_line"] = [l for l in db.tables.get("royalty_report_line", []) if l.get("report_id") not in ids]
            return SimpleNamespace(data=[])
        if self.op == "update":
            for r in t:
                if self._ok(r):
                    r.update(self.rows_in[0])
            return SimpleNamespace(data=[])
        raise RuntimeError(self.op)


class _Schema:
    def __init__(self, db): self.db = db
    def table(self, n): return _Q(self.db, n)
    def rpc(self, *_a, **_k): return _Q(self.db, "__rpc__")


class Client:
    def __init__(self, tables, absent=()):
        self.tables = copy.deepcopy(tables)
        self.absent, self.writes = set(absent), []
    def schema(self, _n): return _Schema(self)
    def table(self, n): return _Q(self, n)


# ══ FIXTURE — street addresses and invented figures only ════════════════════════════════════════════════
HOUSE = coa.ORG_ID
FR, OTHER = "org-franchise", "org-other"
PER = "June 2026"
A, B = "100 Main St", "200 Oak Ave"
NEW_TABLES = ("royalty_report", "royalty_report_line", "royalty_line_def", "royalty_config", "pl_sales_line_map",
              "finance_center", "profit_center_store", "pl_line_cost_center")


def base_tables(org):
    return {
        "store_mapping": [{"org_id": org, "store_code": "S1", "store_address": A}, {"org_id": org, "store_code": "S2", "store_address": B}],
        "raw_sales": [
            {"org_id": org, "period": PER, "trans_id": "T1", "department": "IPHONE - XP", "category": "Phones", "product_desc": "Phone X",
             "ext_price": 500.0, "gp": 60.0, "voided": "", "store": A},
            {"org_id": org, "period": PER, "trans_id": "T2", "department": "Ondigo", "category": "Cases", "product_desc": "Case",
             "ext_price": 40.0, "gp": 30.0, "voided": "", "store": A},
            {"org_id": org, "period": PER, "trans_id": "T3", "department": "Shipping", "category": "UPS Ground", "product_desc": "Ground",
             "ext_price": 25.5, "gp": 25.5, "voided": "", "store": B},
            {"org_id": org, "period": PER, "trans_id": "T4", "department": "Services", "category": "Notary", "product_desc": "Notary",
             "ext_price": 10.0, "gp": 10.0, "voided": "", "store": A},
            {"org_id": org, "period": PER, "trans_id": "T5", "department": "Shipping", "category": "UPS Ground", "product_desc": "Ground",
             "ext_price": 99.0, "gp": 99.0, "voided": "Voided", "store": B},
        ],
        "rep_commissions": [{"org_id": org, "period": PER, "store": A, "total_payout": 30.0}],
        "store_expenses": [{"org_id": org, "period": PER, "store_code": "S1", "expense_name": "Rent", "amount": 1000.0,
                            "expense_type": "fixed", "source_key": None}],
    }


def other_org_rows():
    rid = "rep-other"
    return {
        "royalty_report": [{"id": rid, "org_id": OTHER, "center_code": "1", "store_ref": A, "period": PER, "status": "ok"}],
        "royalty_report_line": [{"org_id": OTHER, "report_id": rid, "section": "sales", "line_key": "copies", "label": "Copies",
                                 "role": "line", "amount": 777.0, "adjusted_amount": 777.0}],
        "pl_sales_line_map": [{"org_id": OTHER, "match_field": "department", "match_value": "Shipping", "pl_line_key": "service_sales"}],
        "finance_center": [{"org_id": OTHER, "center_type": "profit", "code": "PC1", "name": "x"}],
        "profit_center_store": [{"org_id": OTHER, "store_ref": A, "profit_center_code": "PC1"}],
    }


def payload(inputs, spec, stores):
    out = {"consolidated": engine._assemble(inputs, [], spec, {}, SE.PL_SECTIONS, "consolidated", None, True)}
    for s in stores:
        out["store:" + s] = engine._assemble(inputs, [], spec, {}, SE.PL_SECTIONS, "store:" + s, {s}, False)
    return json.loads(json.dumps(out, sort_keys=True, default=str))


# The P&L payload the PRE-CHANGE coa.py (commit 2f2bdfd) produced over base_tables(HOUSE) — captured once with
# OLD_COA=<that file> and frozen here, so CI proves byte-identity against the old code without git.
ORACLE = None
_ORACLE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "harness_royalty_pl_oracle.json")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. BYTE-IDENTITY — the house org path, against the pre-change coa.py")
old_path = os.environ.get("OLD_COA")
if old_path:
    import importlib.util
    spec = importlib.util.spec_from_file_location("coa_2f2bdfd", old_path)
    coa_old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(coa_old)
    ORACLE = payload(coa_old.build_inputs(Client(base_tables(HOUSE)), HOUSE, PER), coa_old.PL_SPEC, [A, B])
    OLD_SPEC = [list(x) for x in coa_old.PL_SPEC]
    with open(_ORACLE_FILE, "w") as fh:
        json.dump({"payload": ORACLE, "pl_spec": OLD_SPEC}, fh, sort_keys=True, indent=1)
    print("  (oracle re-derived live from %s and written to %s)" % (old_path, os.path.basename(_ORACLE_FILE)))
else:
    with open(_ORACLE_FILE) as fh:
        _o = json.load(fh)
    ORACLE, OLD_SPEC = _o["payload"], _o["pl_spec"]
check("the oracle is a real statement (revenue booked from the fixture's device + accessory lines)",
      ORACLE["consolidated"]["sections"][0]["subtotal"] == 540.0, ORACLE["consolidated"]["sections"][0]["subtotal"])
L_absent = coa.build_inputs(Client(base_tables(HOUSE), absent=NEW_TABLES), HOUSE, PER)
L_empty = coa.build_inputs(Client(base_tables(HOUSE)), HOUSE, PER)
L_other = coa.build_inputs(Client({**base_tables(HOUSE), **other_org_rows()}), HOUSE, PER)
for name, L in (("new tables ABSENT (pre-migration)", L_absent), ("new tables present, empty", L_empty),
                ("another org's royalty report / map rules / centers present", L_other)):
    check("P&L payload == the pre-change oracle — " + name, payload(L, coa.PL_SPEC, [A, B]) == ORACLE)
_mut = base_tables(HOUSE)
_mut["raw_sales"][1]["ext_price"] = 40.01
check("NEGATIVE CONTROL: one cent moved in the fixture breaks the comparison (the pin is not vacuous)",
      payload(coa.build_inputs(Client(_mut), HOUSE, PER), coa.PL_SPEC, [A, B]) != ORACLE)
check("the unclaimed sale lines (voids excluded) are REPORTED in the side entry, not booked",
      L_empty["_unbooked_sales"]["unbooked"] == [
          {"department": "Shipping", "category": "UPS Ground", "amount": 25.5, "rows": 1, "stores": [B]},
          {"department": "Services", "category": "Notary", "amount": 10.0, "rows": 1, "stores": [A]}]
      and L_empty["_unbooked_sales"]["unbooked_total"] == 35.5, L_empty["_unbooked_sales"])
check("an org with no royalty rows carries no royalty side entry", "_royalty_coverage" not in L_empty)
check("side entries carry no dollars on by_store / company_wide (no reader can book them)",
      L_empty["_unbooked_sales"]["by_store"] == {} and L_empty["_unbooked_sales"]["company_wide"] == 0.0)
meta = SE.side_meta(L_empty)
check("statement meta carries `unbooked_sales` (the P&L payload does not)", meta["unbooked_sales"]["unbooked_total"] == 35.5
      and "unbooked" not in json.dumps(payload(L_empty, coa.PL_SPEC, [A])))
# the legacy writer still walks every input value
try:
    all_st = set()
    for ln in L_empty.values():
        all_st.update(ln["by_store"].keys())
    check("the legacy engine.compute_and_store walk (ln['by_store'] on every value) stays safe with the side entries "
          "(the unclaimed store B books nothing, so it opens no scope)", all_st == {A}, all_st)
except Exception as e:
    check("the legacy engine.compute_and_store walk stays safe", False, e)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. THE ROYALTY REPORT BOOKS THE P&L")
FIX = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "harness_royalty.py"), encoding="utf-8").read()
FIX = FIX.split('FIX = """', 1)[1].split('"""', 1)[0]
hv = R.house_vocab()
parsed = R.parse(FIX, hv)
val = R.validate(parsed, hv)
RID = "rep-fr-1"
fr_royalty = {
    "tenant_vertical": [dict(r) for r in __import__("app.modules.core.verticals", fromlist=["x"]).HOUSE_VERTICALS],
    "tenants": [{"org_id": FR, "vertical": "ups_store"}],
    "royalty_report": [{"id": RID, "org_id": FR, "center_code": "9001", "store_ref": None, "period": PER,
                        **R.header_fields(parsed, val)}],
    "royalty_report_line": [{**l, "org_id": FR, "report_id": RID} for l in R.line_rows(parsed)],
    "finance_center": [{"org_id": FR, "center_type": "profit", "code": "PC-9001", "name": "Center 9001", "external_ref": "9001"}],
    "profit_center_store": [{"org_id": FR, "store_ref": "S1", "profit_center_code": "PC-9001"}],
}
cw_client = Client({**base_tables(FR), **fr_royalty})
L_cw = coa.build_inputs(cw_client, FR, PER)
check("a report whose center has no store books company-wide and SAYS so on the line",
      L_cw["service_sales"]["company_wide"] == 1550.25 and "9001" in (L_cw["service_sales"].get("note") or ""), L_cw["service_sales"])
fr_royalty["royalty_report"][0]["store_ref"] = "S1"
fr = Client({**base_tables(FR), **fr_royalty})
L_fr = coa.build_inputs(fr, FR, PER)
check("with the store set, every line books at the store (resolved through the P&L's resolver: S1 → 100 Main St)",
      L_fr["service_sales"]["by_store"] == {A: 1550.25} and L_fr["shipping_sales"]["by_store"] == {A: 10000.0}
      and L_fr["merchandise_sales"]["by_store"] == {A: 2000.1} and L_fr["commission_income"]["by_store"] == {A: 26501.0})
check("the drill-down names the report's lines", L_fr["service_sales"]["detail"] == {"Mailbox Service": 1000.0, "Copies": 500.25, "Notary": 50.0})
pl_fr = engine._assemble(L_fr, [], coa.PL_SPEC, {}, SE.PL_SECTIONS, "consolidated", None, True)
opex = {l["key"]: l["amount"] for s in pl_fr["sections"] if s["type"] == "opex" for l in s["lines"]}
check("the fees book below gross profit as expenses: 2,000.02 / 400.01 / 1,000.01",
      (opex.get("royalty_fee"), opex.get("marketing_fee"), opex.get("ad_fund_fee")) == (2000.02, 400.01, 1000.01), opex)
rev = {l["key"]: l["amount"] for s in pl_fr["sections"] if s["type"] == "revenue" for l in s["lines"]}
check("sales tax / deposits never reach revenue (excluded with reasons)",
      set(L_fr["_royalty_coverage"]["excluded"]) == {"sales_tax", "deposits", "excl_sales_tax", "excl_deposits", "excl_iship_proc_fee"}
      and "sales_tax" not in json.dumps(rev))
check("the royalty heads + the POS device / accessory lines both render (different lines, no overlap)",
      {"service_sales", "shipping_sales", "merchandise_sales", "commission_income", "device_rev", "accessory_rev"} <= set(rev))
sm = SE.side_meta(L_fr)
check("statement meta `royalty` reports booked / excluded / unmapped", sm["royalty"]["reports"] == 1 and sm["royalty"]["unmapped"] == {}
      and sm["royalty"]["booked"]["royalty_fee"] == 2000.02)
fr_off = Client({**base_tables(FR), **fr_royalty, "royalty_config": [{"org_id": FR, "book_pl": False}]})
L_off = coa.build_inputs(fr_off, FR, PER)
check("book_pl=false books nothing from the report, and still reports what it would have",
      not L_off["service_sales"]["by_store"] and not L_off["service_sales"]["company_wide"]
      and L_off["_royalty_coverage"]["book_pl"] is False and L_off["_royalty_coverage"]["booked"]["service_sales"] == 1550.25)
L_hidden = coa.build_inputs(Client({**base_tables(FR), **fr_royalty, "tenants": [{"org_id": FR, "vertical": None}]}), FR, PER)
check("a tenant OUTSIDE the vocabulary's vertical reads no house vocabulary: every line is unmapped, nothing books silently",
      not L_hidden["service_sales"]["by_store"] and L_hidden["_royalty_coverage"]["unmapped"].get("copies", {}).get("amount") == 500.25)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. THE UNCLAIMED-LINE MAP (every tenant) — books, and never double-counts the royalty report")
mp = Client({**base_tables(HOUSE), "pl_sales_line_map": [
    {"org_id": HOUSE, "match_field": "department", "match_value": "Shipping", "pl_line_key": "shipping_sales"},
    {"org_id": HOUSE, "match_field": "category", "match_value": "Notary", "pl_line_key": "store_opex"}]})
L_mp = coa.build_inputs(mp, HOUSE, PER)
check("a rule books the unclaimed line to its revenue head at its store; the voided line never books",
      L_mp["shipping_sales"]["by_store"] == {B: 25.5} and L_mp["shipping_sales"]["detail"] == {"Shipping": 25.5})
check("a rule naming a non-revenue line is ignored (the line stays reported unbooked)",
      [u["category"] for u in L_mp["_unbooked_sales"]["unbooked"]] == ["Notary"] and not L_mp["store_opex"]["detail"].get("Notary"))
check("every OTHER line of the house P&L is unchanged by the rule",
      all(payload(L_mp, coa.PL_SPEC, [A])["consolidated"]["sections"][i]["lines"] == [l for l in ORACLE["consolidated"]["sections"][i]["lines"]]
          for i in (1, 2, 3)))
both = Client({**base_tables(FR), **fr_royalty, "pl_sales_line_map": [
    {"org_id": FR, "match_field": "department", "match_value": "Shipping", "pl_line_key": "shipping_sales"}]})
L_both = coa.build_inputs(both, FR, PER)
check("a POS line mapped to a head the royalty report books this period is SUPPRESSED and reported (never both)",
      L_both["shipping_sales"]["by_store"] == {A: 10000.0} and L_both["_unbooked_sales"]["suppressed"][0]["amount"] == 25.5)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. THE CHART — the new heads")
spec = {k: (sec, kind, grain) for k, _l, sec, kind, grain in coa.PL_SPEC}
check("the sales heads are auto_opt revenue at store grain", all(spec[k] == ("revenue", "auto_opt", "store") for k in
                                                                ("service_sales", "shipping_sales", "merchandise_sales", "commission_income")))
check("the fee heads are auto_opt opex at store grain", all(spec[k] == ("opex", "auto_opt", "store") for k in
                                                           ("royalty_fee", "marketing_fee", "ad_fund_fee")))
check("every P&L key the house vocabulary names is a line of the chart",
      {r["pl_line_key"] for r in R.HOUSE_ROYALTY_LINES if r["pl_line_key"]} <= set(spec))
NEW_HEADS = {"service_sales", "shipping_sales", "merchandise_sales", "commission_income", "royalty_fee", "marketing_fee", "ad_fund_fee"}
check("no existing line moved or changed: the chart minus the seven new heads IS the pre-change chart, in order",
      [list(x) for x in coa.PL_SPEC if x[0] not in NEW_HEADS] == OLD_SPEC and not (NEW_HEADS & {x[0] for x in OLD_SPEC}))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. PROFIT CENTERS ARE SCOPES OF THE ONE ENGINE")
companies = [{"id": "co1", "name": "Co"}]
co_of = lambda s: "co1"
sc0, _st = SE._scopes(L_fr, companies, co_of)
sc1, _st1 = SE._scopes(L_fr, companies, co_of, profit_scopes=[])
check("no profit center ⇒ the scope list is byte-identical", sc0 == sc1)
ps = C.load_profit_scopes(fr, FR, coa.store_resolver(fr, FR))
sc2, _ = SE._scopes(L_fr, companies, co_of, profit_scopes=ps)
check("a profit center adds ONE scope after the existing ones, holding its resolved store",
      sc2[:len(sc0)] == sc0 and sc2[len(sc0):] == [("profit_center:PC-9001", "Profit center PC-9001 — Center 9001", {A}, False)], sc2[len(sc0):])
pc_pl = engine._assemble(L_fr, [], coa.PL_SPEC, {}, SE.PL_SECTIONS, "profit_center:PC-9001", {A}, False)
st_pl = engine._assemble(L_fr, [], coa.PL_SPEC, {}, SE.PL_SECTIONS, "store:" + A, {A}, False)
check("the profit center's P&L is its store's P&L (the same engine, the same numbers)", pc_pl["net_income"] == st_pl["net_income"])
jr = [{"statement": "balance_sheet", "account_type": "equity", "account_line": "Owner capital", "amount": 5000, "store_address": None},
      {"statement": "pl", "account_type": "opex", "account_line": "Local ad", "amount": 20, "store_address": A},
      {"statement": "pl", "account_type": "opex", "account_line": "Other ad", "amount": 30, "store_address": B}]
check("a profit center's journal = its stores' entries only (a tenant-level entry and another store's never reach it)",
      BS.journal_scope_entries(jr, "profit_center:PC-9001", {A}) == [jr[1]])
src_se = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "modules", "account", "statement_engine.py")).read()
check("statement() and compute_and_store() both hand the engine the profit scopes and put the side meta on `meta`",
      src_se.count("meta.update(side_meta(inputs))") == 2
      and len(__import__("re").findall(r"(?<!def )_profit_scopes\(client, org_id\)", src_se)) == 2)
st = SE.statement(fr, FR, PER, scope="profit_center:PC-9001", kinds=("pl",))
check("the on-demand statement serves the profit-center scope, and its meta carries the royalty coverage",
      st.get("computed") and st["pl"]["net_income"] == st_pl["net_income"] and "royalty" in st["meta"], st.get("note"))
check("an unknown profit center is computed:false (fail closed)", SE.statement(fr, FR, PER, scope="profit_center:NOPE", kinds=("pl",)).get("computed") is False)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. THE FILTER — profit_center: composes with store/market filters and fails CLOSED")
pred = SF.scope_predicate(fr, FR, "profit_center:PC-9001")
check("members only", pred(A) and pred(A.upper()) and not pred(B))
check("an unknown center matches NOTHING", not SF.scope_predicate(fr, FR, "profit_center:NOPE")(A))


class Boom(Client):
    def schema(self, _n):
        raise RuntimeError("down")


check("a resolution failure matches NOTHING (never another center's stores)", not SF.scope_predicate(Boom({}), FR, "profit_center:PC-9001")(A))
check("another org's center of the same code is never read", not SF.scope_predicate(Client({**other_org_rows()}), FR, "profit_center:PC1")(A))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. THE ROUTER — over the fake client")
db = Client({**base_tables(FR), **{k: v for k, v in fr_royalty.items() if k not in ("royalty_report", "royalty_report_line")},
             "raw_sales_product": [
                 {"org_id": FR, "period": PER, "trans_date": "2026-06-02", "store": A, "category": "Copies", "ext_price": 500.25, "voided": ""},
                 {"org_id": FR, "period": PER, "trans_date": "2026-06-03", "store": A, "category": "Mailbox Service", "ext_price": 1000.0, "voided": ""},
                 {"org_id": FR, "period": PER, "trans_date": "2026-06-03", "store": A, "category": "Gift Wrap", "ext_price": 5.0, "voided": ""},
                 {"org_id": FR, "period": PER, "trans_date": "2026-06-04", "store": B, "category": "Copies", "ext_price": 9.0, "voided": ""},
                 {"org_id": OTHER, "period": PER, "trans_date": "2026-06-04", "store": A, "category": "Copies", "ext_price": 99.0, "voided": ""}],
             "pos_tender_summary": [{"org_id": FR, "close_date": "2026-06-02", "store": "S1", "amount": 1400.0}]})
RR.sb = lambda: db
n0 = len(db.writes)
pv = RR.royalty_parse(org_id=FR, file=None, text=FIX)
check("parse previews the lines and the validation and writes NOTHING", pv["validation"]["status"] == "ok" and len(db.writes) == n0)
imp = RR.royalty_import(org_id=FR, file=None, text=FIX.replace("March 2031", "June 2026"), center="", period="", store_ref="")
check("import stores the report under the center + canonical period read off the page, the store found through the "
      "profit center's external reference", imp["center_code"] == "9001" and imp["period"] == PER and imp["store_ref"] == A, imp)
check("…with the REPORT's figures on the header", db.tables["royalty_report"][0]["total_due"] == 3400.04
      and db.tables["royalty_report"][0]["total_adjusted_str"] == 40000.45)
check("every write carried org_id (header, lines, the replace's delete)",
      all(("eq", "org_id", FR) in w[2] or all(r.get("org_id") == FR for r in w[3]) for w in db.writes[n0:]), db.writes[n0:])
bad = FIX.replace("March 2031", "June 2026").replace("Marketing Due (1%) $400.01", "Marketing Due (1%) $400.00")
imp2 = RR.royalty_import(org_id=FR, file=None, text=bad, center="", period="", store_ref="")
check("a re-import REPLACES the center-month; a straight-rounded fee is stored as printed and FLAGGED",
      len(db.tables["royalty_report"]) == 1 and imp2["status"] == "flagged"
      and next(l for l in db.tables["royalty_report_line"] if l["line_key"] == "fee_marketing")["amount"] == 400.00
      and len([l for l in db.tables["royalty_report_line"] if l["report_id"] == imp2["id"]]) == len(db.tables["royalty_report_line"]))
rc = RR.royalty_recon(PER, org_id=FR)
c0 = rc["centers"][0]
byl = {l["line_key"]: l for l in c0["lines"]}
check("the recon sums the daily rows of the report's store only (another store / another org never counted)",
      byl["copies"]["daily"] == 500.25 and byl["copies"]["status"] == "tie" and byl["mailbox_service"]["variance"] == 0.0)
check("unclaimed categories and the tender cross-check come back", c0["unmapped_daily"][0]["category"] == "Gift Wrap"
      and c0["tenders"]["tenders"] == 1400.0)
man = RR.royalty_manual(RR.ManualIn(center_code="9002", period="2026-06", store_ref="S2",
                                    entries=[{"line_key": "copies", "amount": 10}, {"line_key": "fee_royalty", "amount": 0.5}]), org_id=FR)
check("a manual entry lands under the canonical period with derived totals marked", man["period"] == PER and "gross_sales_total" in man["derived_totals"])
summ = RR.royalty_summary(org_id=FR)
check("the dashboard summary: latest period, both centers, fees due, the recon variance, flagged count",
      summ["has_data"] and summ["period"] == PER and summ["centers"] == 2 and summ["flagged_reports"] >= 1
      and summ["recon_variance"] is not None, summ)
nomig = Client({"tenant_vertical": fr_royalty["tenant_vertical"], "tenants": fr_royalty["tenants"]}, absent=NEW_TABLES)
RR.sb = lambda: nomig
try:
    RR.royalty_import(org_id=FR, file=None, text=FIX, center="", period="", store_ref="")
    check("before the migration an import is refused naming it", False)
except Exception as e:
    check("before the migration an import is refused naming it", "1022" in str(getattr(e, "detail", e)))
check("the router is gated by the royalty module", any("require_module_royalty" in getattr(d.dependency, "__name__", "")
                                                       for d in RR.router.dependencies))

print("\n══ royalty P&L / scopes / router: %d passed, %d failed ══" % (P, F))
sys.exit(1 if F else 0)
