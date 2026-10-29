"""
INTEGRATION PROOF — the BYOD-and-upgrade watchdog, END TO END (index §COMMCALC-43).

This is the WIRING half of the §COMMCALC-43 pair. `harness_byod_upgrade_day.py` proves the detector's
logic over fixtures; this one drives the REAL endpoint and the REAL sweep against a stand-in
Supabase/PostgREST client, so what is proved here is the part a pure test cannot reach: the query,
the columns it selects, the config it resolves, the scope filter, and the row it writes.

Every defect it locks is one that has actually shipped in this repo:

 A. THE ENDPOINT ANSWERS FROM THE REAL READ. `GET /commcalc/watchdog/byod-upgrade` is called as a
    function with a fake client holding the live case's shape, and the cases come back.

 B. THE SELECT CARRIES EVERY FIELD THE CLASS PREDICATE CAN READ — the SILENT ZERO. The sweep's
    shared sales read did NOT include `contract_type` when this detector was wired in, and
    `line_class.HOUSE_FIELDS` is exactly that column: without it every line classifies as None, no
    activation event exists, and the sweep reports a clean period it never looked at. Found and fixed
    on 2026-10-28; §B fails the build if the column leaves either select again.

 C. WHICH SALES TABLE IS ASKED, NEVER ASSUMED. Both the endpoint and the sweep go through
    `_read_sales` → `router._open_month_source` (§19.16). The void sweep and the void register both
    named the month-end archive literally and therefore read ZERO rows for the month in progress —
    the only month anybody can act on. §C proves the open month is read from the daily feed.

 D. THE SPAN FILTER RUNS BEFORE ANYTHING IS COUNTED, so a market manager's report cannot show a
    store their flag queue hides.

 E. THE SWEEP WRITES THE FINDING, through `flag_persist`, under its OWN source — and a run that
    finds nothing does not retire another detector's rows.

 F. THE CONFIG IS RESOLVED FROM THE ORG'S OWN ROW, not from a house default: a tenant whose POS
    spells the activation kind in `category` is served, and an org with no declared box departments
    gets "not measured" rather than a guess.

 G. A MISSING TABLE DEGRADES, NEVER 500s. `watchdog_rule` not yet migrated, or the flags table
    absent, must leave the report readable.

Run: `cd backend && python3 harness_byod_upgrade_wiring.py`
"""
import os
import re
import sys

sys.path.insert(0, ".")

from app.modules.commcalc import byod_upgrade_day as M          # noqa: E402
from app.modules.commcalc import byod_upgrade_drilldown as _byod_drill  # noqa: E402
from app.modules.commcalc import flag_registry as REG           # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"   [{detail}]" if detail and not cond else ""))


def section(t):
    print(f"\n── {t} " + "─" * max(0, 92 - len(t)))


ORG = "00000000-0000-0000-0000-000000000001"
PERIOD = "October 2026"
BOX = ["Android - XP", "IPHONE - XP", "TABLET - XP"]


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# A stand-in for the PostgREST client. Deliberately NOT the shared harness_fakedb one: that models
# column TYPES (the comp-upload 22P02 defect) and this needs the SELECT STRING, which it discards.
# §B cannot be written without seeing the columns the caller asked for.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
class Q:
    def __init__(self, rows, log, table, cols):
        self._rows, self._log, self._t, self._cols = list(rows), log, table, cols
        self._log.append({"table": table, "cols": cols})

    def select(self, cols="*", **_k):
        self._cols = cols
        self._log[-1]["cols"] = cols
        return self

    def eq(self, c, v):
        self._rows = [r for r in self._rows if r.get(c) == v]
        return self

    def in_(self, c, vs):
        vs = list(vs)
        self._rows = [r for r in self._rows if r.get(c) in vs]
        return self

    def gte(self, c, v):
        self._rows = [r for r in self._rows if str(r.get(c) or "") >= str(v)]
        return self

    def limit(self, _n):
        return self

    def range(self, a, b):
        self._rows = self._rows[int(a):int(b) + 1]
        return self

    def is_(self, c, v):
        want = None if str(v).lower() in ("null", "none") else v
        self._rows = [r for r in self._rows if r.get(c) == want]
        return self

    def neq(self, c, v):
        self._rows = [r for r in self._rows if r.get(c) != v]
        return self

    def upsert(self, payload, **_k):
        return self.insert(payload)

    def single(self):
        return self

    def maybe_single(self):
        return self

    def order(self, *_a, **_k):
        return self

    def delete(self):
        self._log[-1]["op"] = "delete"
        return self

    def insert(self, payload):
        self._log[-1]["op"] = "insert"
        self._log[-1]["payload"] = payload if isinstance(payload, list) else [payload]
        return self

    def update(self, payload):
        self._log[-1]["op"] = "update"
        self._log[-1]["payload"] = [payload]
        return self

    def execute(self):
        return type("R", (), {"data": self._rows, "count": len(self._rows)})()


class Schema:
    def __init__(self, tables, log, name, missing):
        self._t, self._log, self._n, self._missing = tables, log, name, missing

    def table(self, name):
        if (self._n, name) in self._missing:
            raise RuntimeError(f'relation "{self._n}.{name}" does not exist')
        return Q(self._t.get((self._n, name), []), self._log, f"{self._n}.{name}", "*")

    def rpc(self, name, args=None):
        return Rpc(self._log, name, args or {}, self._missing)


class Rpc:
    """`commcalc.flags` is written through the migration-287 RPCs, never an INSERT — so the write is
    only observable here. `flags_sync_batch` answers with the counts the real function returns."""

    def __init__(self, log, name, args, missing):
        self._log, self._name, self._args = log, name, args
        if ("rpc", name) in missing:
            raise RuntimeError(f"Could not find the function commcalc.{name}")
        log.append({"rpc": name, "args": args})

    def execute(self):
        rows = list(self._args.get("p_rows") or ())
        data = ({"inserted": len(rows), "updated": 0, "reopened": 0}
                if self._name == "flags_sync_batch" else {"resolved": 0, "superseded": 0})
        return type("R", (), {"data": data})()


class Client:
    def __init__(self, tables, missing=()):
        self._t, self.log, self._missing = tables, [], set(missing)

    def schema(self, name):
        return Schema(self._t, self.log, name, self._missing)

    def table(self, name):
        return self.schema("public").table(name)

    def rpc(self, name, args=None):
        return Rpc(self.log, name, args or {}, self._missing)


def line(**kw):
    base = {"org_id": ORG, "period": PERIOD, "store": "1 S 60th street",
            "trans_date": "2026-10-02", "trans_id": "215344", "mdn": "2674832829",
            "contract_type": "BYOD", "product_desc": "Autopay", "department": "", "category": "",
            "serial_1": "", "ext_price": 0.0, "salesperson": "Jaladhi, Akhila",
            "voided": "", "trans_type": "Sale", "customer": "Smith, Jane"}
    base.update(kw)
    return base


#: The handset's device identifier, and the carrier money standing against it.
MEID = "351449743101051"
PAID = 61.00

#: THE CARRIER'S PER-LINE PAYMENT FEED, the other half of the chargeback amount. `payment_type` is
#: a type the org has DECLARED as device money below; `imei` is the key it is paid against.
def paid_line(**kw):
    base = {"org_id": ORG, "period": PERIOD, "business_address": "1 S 60th street",
            "amount": PAID, "payment_type": "2026 Q4 Promo Upgrade", "imei": MEID,
            "payment_date": "2026-10-20", "rep_username": "akhila"}
    base.update(kw)
    return base


#: The org's own declaration of what that payment type IS, and the routing that makes the
#: reimbursement component device money (§58/§69 — empty routing means "nothing declared", which is
#: `configured: false`, not a measured zero).
DECLARED = [{"org_id": ORG, "description": "2026 Q4 Promo Upgrade", "category": "Device Rebate"}]
ROUTED = [{"org_id": ORG,
           "carrier_class_category_components": {"device rebate": "REIMBURSEMENT"},
           "carrier_component_lines": {"REIMBURSEMENT": "vip_reimb"},
           "device_reimb_recon_config": {}}]


#: The live case, as the 95 measured ones carry it: a BYOD activation with no handset, then a
#: SEPARATE receipt the same day upgrading the same number onto a promo device.
LIVE_CASE = [
    line(product_desc="Unlimited Data, Talk & Text + 30GB"),
    line(product_desc="Autopay"),
    line(trans_id="215350", contract_type="Upgrade", product_desc="Unlimited+ $50"),
    line(trans_id="215350", contract_type="Upgrade", product_desc="Autopay"),
    line(trans_id="215350", contract_type="Upgrade", department="Android - XP",
         product_desc="moto g play - 2026 Q4 Promo Upgrade", ext_price=0.99,
         # THE DEVICE IDENTIFIER. It is the key the carrier pays against, so without it on the
         # fixture no chargeback amount could ever be found and §H would prove nothing.
         serial_1=MEID),
    line(trans_id="215350", contract_type="Upgrade", ext_price=35.0,
         product_desc="Device Setup Charge Upgrade (non-refundable)"),
]


def tables(rows=None, *, box=BOX, adr=None, rule_rows=None, archive=None,
           paid=None, declared=None, routed=None):
    """`paid` / `declared` / `routed` default to EMPTY, so every section written before the
    chargeback amount sees exactly the world it saw before: an org that has declared no device-money
    routing, whose amounts are therefore unmeasured rather than zero."""
    return {
        ("commcalc", "daily_sales_feed"): list(rows if rows is not None else LIVE_CASE),
        ("commcalc", "raw_sales"): list(archive or ()),
        ("commcalc", "accessory_config"): [
            {"org_id": ORG, "box_departments": box, "activation_details_rules": adr or {},
             "contract_type_map": {}, "activation_rules": []}],
        ("commcalc", "watchdog_rule"): list(rule_rows or ()),
        ("commcalc", "flags"): [],
        ("commcalc", "store_mapping"): [],
        ("commcalc", "store_aliases"): [],
        ("storeops", "tenants"): [{"org_id": ORG}],
        ("commcalc", "closing_attempt"): [],
        ("commcalc", "daily_closing"): [],
        ("commcalc", "raw_payment_detail"): list(paid or ()),
        ("commcalc", "payment_categories"): list(declared or ()),
        ("commcalc", "commission_org_config"): list(routed or ()),
    }


def call_endpoint(client, **kw):
    """The REAL endpoint function, with the real app's dependency resolved to our client."""
    from app.modules.commcalc import watchdog_router as W
    from app.modules.commcalc import router as CR
    from app.modules.commcalc import accessory_catalog as AC
    try:
        AC.invalidate(ORG)              # the config memo is process-wide; never serve a stale org
    except Exception:
        pass
    _orig, _orig_cr = W.get_supabase, CR.get_supabase
    W.get_supabase = lambda: client
    CR.get_supabase = lambda: client
    try:
        return W.watchdog_byod_upgrade(period=kw.pop("period", PERIOD), org_id=ORG, **kw)
    finally:
        W.get_supabase, CR.get_supabase = _orig, _orig_cr


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. the endpoint answers from the real read")

cl = Client(tables())
R = call_endpoint(cl)
check("A1 the live case comes back through the real endpoint", len(R.get("cases") or []) == 1)
C = (R.get("cases") or [{}])[0]
check("A2 it names the store, the day and the phone line",
      C.get("store") == "1 S 60th street" and C.get("day") == "2026-10-02"
      and C.get("number") == "2674832829")
check("A3 the two receipts are both named, so a manager can pull them",
      C.get("receipts") == ["215344", "215350"])
check("A4 the handset counts are real numbers, read off the org's own box departments",
      C.get("first_handsets") == 0 and C.get("second_handsets") == 1)
check("A5 the signal names what was OBSERVED and nothing more",
      "no handset on the first activation, one on the second" in (C.get("signal") or []))
check("A6 the report states which classes it paired, from config and not from code",
      R.get("classes") == ["byod", "upgrade"] and R.get("class_labels"))
check("A7 every line is carried for the drill-down", len(C.get("lines") or []) == 6)
check("A8 the summary reports the measurable coverage, so a low one can be shown",
      (R.get("summary") or {}).get("numbered_share") == 1.0)
check("A9 an empty feed says so rather than reporting a clean period",
      call_endpoint(Client(tables([]))).get("has_feed") is False)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. the select carries every field the class predicate can read — THE SILENT ZERO")

_selects = [e["cols"] for e in cl.log
            if e["table"] == "commcalc.daily_sales_feed" and isinstance(e["cols"], str)]
check("B1 the endpoint did read the daily feed", _selects != [])
# THE REGRESSION. `line_class.HOUSE_FIELDS` is ("contract_type",) and a tenant may declare
# `category` / `product_desc` instead. Any of them missing from the select is not an error — it is a
# clean zero over a period nobody looked at.
_FIELD_COLS = ("contract_type", "category", "product_desc", "mdn", "serial_1")
_missing_ep = [c for c in _FIELD_COLS if not any(c in s for s in _selects)]
check("B2 REGRESSION the ENDPOINT's select carries every field a tenant's rules can classify on",
      _missing_ep == [], str(_missing_ep))

WSRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "app", "modules", "commcalc", "watchdog_router.py")).read()
# The SWEEP shares one feed read with every detector on the tick, so its select is a single string in
# `_run_for_org`. Read it out of the source rather than running the whole sweep for one assertion.
_sweep_sel = re.search(r"sales_rows, sales_table = _read_sales\((.{0,1200}?)\)\n", WSRC, re.S)
_sweep_cols = _sweep_sel.group(1) if _sweep_sel else ""
_missing_sw = [c for c in _FIELD_COLS if c not in _sweep_cols]
check("B3 REGRESSION and so does the SWEEP's shared read — the column that was actually missing "
      "when this detector was wired in, which would have reported every period clean",
      _missing_sw == [], str(_missing_sw))
check("B4 the detector really does go silent without it, so B2/B3 are not decoration",
      call_endpoint(Client(tables(
          [{k: v for k, v in r.items() if k != "contract_type"} for r in LIVE_CASE]
      ))).get("cases") == []
      and (call_endpoint(Client(tables(
          [{k: v for k, v in r.items() if k != "contract_type"} for r in LIVE_CASE]
      ))).get("summary") or {}).get("events") == 0)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. which sales table is ASKED, never assumed")

check("C1 the endpoint reads through the one home for which table answers a period",
      "_read_sales" in WSRC
      and "_open_month_source" in WSRC)
check("C2 no sales table is named as a literal in this file — the void sweep's 2026-10-12 defect",
      "daily_sales_feed" not in WSRC and '"raw_sales"' not in WSRC)
# The month in progress: the archive is EMPTY and the daily feed holds the day. A surface naming the
# archive reads zero rows here and reports no cases.
_open = Client(tables(LIVE_CASE, archive=[]))
check("C3 REGRESSION the OPEN month is answered from the daily feed, not from an empty archive",
      len((call_endpoint(_open).get("cases") or [])) == 1
      and call_endpoint(_open).get("sales_table") == "daily_sales_feed")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. the span filter runs BEFORE anything is counted")

_ep_src = WSRC.split("def watchdog_byod_upgrade", 1)[1].split("\n@router", 1)[0]
_i_scope = _ep_src.find("scope_keyset")
_i_count = min([i for i in (_ep_src.find("number_days"), _ep_src.find(".cases(")) if i > 0])
check("D1 the keyset is resolved before the first thing is counted",
      0 < _i_scope < _i_count, f"scope@{_i_scope} count@{_i_count}")
check("D2 it is the SAME span filter the findings board applies, not a private one",
      "from app.modules.storeops.router import scope_keyset, in_keyset" in _ep_src)
check("D3 the store is matched through that home, never with a bare string equality on the label "
      "(§64 — a store spelled two ways is the normal case, not an edge)",
      '.eq("store"' not in _ep_src and "!= store" not in _ep_src)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. the sweep writes the finding, under its own source")

from app.modules.commcalc import watchdog_router as W       # noqa: E402
from app.modules.commcalc import router as CR               # noqa: E402

sweep_cl = Client(tables())
_o, _ocr = W.get_supabase, CR.get_supabase
W.get_supabase, CR.get_supabase = (lambda: sweep_cl), (lambda: sweep_cl)
try:
    SW = W._run_for_org(sweep_cl, ORG, period=PERIOD)
finally:
    W.get_supabase, CR.get_supabase = _o, _ocr

check("E1 the tick has a byod-upgrade block at all", "byod_upgrade" in SW)
check("E2 it did not error", "error" not in (SW.get("byod_upgrade") or {}),
      str((SW.get("byod_upgrade") or {}).get("error"))[:120])
check("E3 it reports its summary, so an operator sees the gaps and not only the count",
      (SW["byod_upgrade"].get("summary") or {}).get("events", 0) > 0)
# `commcalc.flags` is written through the migration-287 RPCs, so the row is observable only there.
_batches = [e for e in sweep_cl.log if e.get("rpc") == "flags_sync_batch"]
_written = [r for e in _batches for r in (e["args"].get("p_rows") or ())
            if r.get("flag_type") == M.FLAG_TYPE]
check("E4 the finding is WRITTEN to the findings table", len(_written) == 1, str(len(_written)))
check("E5 under this detector's OWN source, so the commission recalculation's own pass cannot "
      "retire its rows (the 2026-10-12 defect: a writer that borrows 'sales' empties itself)",
      bool(_written) and _written[0].get("source") == M.SOURCE
      and M.SOURCE not in (getattr(CR, "_CALC_FLAG_SOURCES", ()) or ()))
check("E6 with the registry's severity, not a fifth vocabulary of its own",
      bool(_written) and _written[0].get("severity") == REG.severity_for(M.FLAG_TYPE))
check("E7 the row names the phone line and the store it belongs to",
      bool(_written) and _written[0].get("mdn") == "2674832829"
      and _written[0].get("store_address") == "1 S 60th street")
check("E8 and the evidence blob is NOT written as a column — it is a screen's payload, and the "
      "findings table has no column for it",
      bool(_written) and "evidence" not in _written[0])
# The retire step is scoped to the sources the CALLER owns. Passing a wider list here would have a
# tick with no findings resolve away another detector's open rows.
_stale = [e for e in sweep_cl.log if e.get("rpc") == "flags_resolve_stale"
          and M.SOURCE in (e["args"].get("p_sources") or ())]
check("E9 the retire step names ONLY this detector's source, so a quiet tick cannot resolve "
      "another detector's findings",
      len(_stale) == 1 and _stale[0]["args"]["p_sources"] == [M.SOURCE], str(len(_stale)))
check("E10 and it is bounded to the period being swept, in every spelling",
      _stale and PERIOD in (_stale[0]["args"].get("p_periods") or ()))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. the config is resolved from the ORG's own row")

# A tenant whose POS spells the activation kind in `category`, declared on its own config row. The
# house `contract_type` column is BLANK on these rows, so only a resolver reading that org's declared
# fields can classify them — a house default would read zero and report a clean period.
TENANT_ADR = {"fields": ["category", "product_desc"],
              "tokens": {"byod": ["customer provided"], "upgrade": ["upgrade"],
                         "port": ["port"], "activation": ["new activation"]}}
TEN_ROWS = [
    line(contract_type="", category="Customer Provided", product_desc="Plan $50"),
    line(contract_type="", category="Customer Provided", product_desc="Autopay"),
    line(trans_id="215350", contract_type="", category="Upgrade", product_desc="Plan $50"),
    line(trans_id="215350", contract_type="", category="Upgrade", department="Android - XP",
         product_desc="moto g play", ext_price=0.99),
]
check("F1 a tenant whose rules read `category` is classified by ITS OWN declaration",
      len(call_endpoint(Client(tables(TEN_ROWS, adr=TENANT_ADR))).get("cases") or []) == 1)
check("F2 and the same rows under the HOUSE rules find nothing — so F1 proves the resolution, "
      "not the fixture",
      call_endpoint(Client(tables(TEN_ROWS))).get("cases") == [])
# THE FAKE ZERO THAT IS REACHABLE IN PRODUCTION, and it is NOT this report's to fix.
# `router._accessory_config` has fallen back to the house department labels since mig 218 whenever an
# org declared none, so that the house box numbers stay byte-identical. The consequence for ANY
# surface reading `box_departments` is that an org with no declaration is handed a vocabulary that
# may match nothing, and `is_box` then answers a confident "no handset" rather than "we cannot tell".
# Changing that fallback would move box counts on the money paths, so it is reported rather than
# quietly rewritten here: this report MEASURES whether the vocabulary fitted and says so.
_nobox = call_endpoint(Client(tables(box=[])))
check("F3 an undeclared box set does NOT silence the report — the finding still lands",
      len(_nobox.get("cases") or []) == 1)
check("F4 and the config layer's house fallback is visible on the payload rather than implied, so "
      "a reader can see which labels answered",
      (_nobox.get("box_departments") or []) != [])
# A tenant whose POS spells departments its own way: the house labels match nothing, and the figure
# that must not read as a finding is the handset count.
_foreign = [dict(r, department=("Handset" if r.get("department") else ""))
            for r in LIVE_CASE]
_fr = call_endpoint(Client(tables(_foreign, box=[])))
check("F5 REGRESSION a box vocabulary that matches NO line in the period is reported as unmatched, "
      "never as zero handsets sold",
      (_fr.get("summary") or {}).get("box_vocabulary_matched") is False
      and (_fr.get("summary") or {}).get("handset_lines") == 0)
check("F6 and a vocabulary that DOES fit says so, so the screen can tell the two apart",
      (R.get("summary") or {}).get("box_vocabulary_matched") is True
      and (R.get("summary") or {}).get("handset_lines") == 1)
_off = call_endpoint(Client(tables(rule_rows=[
    {"org_id": ORG, "flag_type": M.FLAG_TYPE, "enabled": False, "params": None}])))
check("F7 a tenant that switched the check off reads enabled=false, not an empty clean period",
      _off.get("enabled") is False)
_pair = call_endpoint(Client(tables(rule_rows=[
    {"org_id": ORG, "flag_type": M.FLAG_TYPE, "enabled": True,
     "params": {"classes": ["byod", "port"]}}])))
check("F8 a config row changes which pair is watched, with no code change (RULE TWO)",
      _pair.get("classes") == ["byod", "port"] and _pair.get("cases") == [])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. a missing table degrades, never 500s")

_norule = call_endpoint(Client(tables(), missing=[("commcalc", "watchdog_rule")]))
check("G1 `watchdog_rule` not yet migrated → the house pair, and the report still reads",
      len(_norule.get("cases") or []) == 1 and _norule.get("classes") == ["byod", "upgrade"])
# The RPC, not the table: `commcalc.flags` is written through the migration-287 functions, so
# "migration 287 has not run" is a MISSING RPC and that is the path that has to degrade.
_sweep_noflags = Client(tables(), missing=[("rpc", "flags_sync_batch")])
_o, _ocr = W.get_supabase, CR.get_supabase
W.get_supabase, CR.get_supabase = (lambda: _sweep_noflags), (lambda: _sweep_noflags)
try:
    SW2 = W._run_for_org(_sweep_noflags, ORG, period=PERIOD)
finally:
    W.get_supabase, CR.get_supabase = _o, _ocr
check("G2 migration 287 not run → the tick SAYS the finding cannot be recorded yet, rather than "
      "reporting a clean period or taking the whole sweep down with it",
      (SW2.get("byod_upgrade") or {}).get("skipped") == "findings cannot be recorded yet",
      str(SW2.get("byod_upgrade"))[:140])
check("G3 and every other detector on the tick still ran",
      {"cash", "voids", "multi_handset"} <= set(SW2))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("H. the chargeback amount, and the drill-down of both transactions")
# OWNER ASK 2026-10-28, verbatim: *"inmclude the $ amount of the rebate and title it as chargeback
# amount, make it oneline summary top show the sales rep teh store the phone sold and the phone
# number meim and teh $ amount and thebn drill down like other menus to get mroe detail sof both
# trasnactions"*.
#
# EVERY CHECK HERE IS ABOUT A FIGURE THAT IS NOT MEASURED, because that is the state most rows are
# in: a rebate lands months after the sale, so on a recent day the honest answer is "not yet" and a
# $0.00 would be a measurement nobody made (§19.26). The amount is proved to arrive when the feed
# carries it, and proved to stay ABSENT in all four ways it can be absent.
_full = Client(tables(paid=[paid_line()], declared=DECLARED, routed=ROUTED))
R_full = call_endpoint(_full)
_c0 = (R_full.get("cases") or [{}])[0]

check("H1 THE ONE-LINE SUMMARY carries a person, a place, a phone and a figure — the four things "
      "the owner asked the top line to show",
      _c0.get("rep") == "Jaladhi, Akhila" and _c0.get("store")
      and _c0.get("number") == "2674832829" and _c0.get("meid") == MEID
      and "moto g play" in (_c0.get("phone_sold") or ""),
      str({k: _c0.get(k) for k in ("rep", "store", "number", "meid", "phone_sold")})[:200])
check("H2 the phone named is the one the store SOLD — the handset on the UPGRADE side. The first "
      "side is the device the customer brought in, and naming that would report the phone the "
      "store did not sell, the opposite of the question",
      _c0.get("phone_sold") == "moto g play - 2026 Q4 Promo Upgrade")
check("H3 THE CHARGEBACK AMOUNT is the carrier money recorded against that handset, read through "
      "the one home (§COMMCALC-44) — the same verdict the P&L books its reimbursement line on, so "
      "a watchdog screen and the statement cannot quote two different rebates for one phone",
      _c0.get("chargeback_amount") == PAID and _c0.get("chargeback_measured") is True,
      str(_c0.get("chargeback_amount")))
check("H4 it is NOT partial — one handset was sold and the feed answered for it",
      _c0.get("chargeback_partial") is False and _c0.get("chargeback_phones_measured") == 1)
check("H5 and the summary's total is that money, not a recount of the sale",
      (R_full.get("summary") or {}).get("chargeback_dollars") == PAID
      and (R_full.get("summary") or {}).get("chargeback_measured_cases") == 1)
check("H6 the feed the figure was read from is STATED, not assumed — a reader can see which "
      "relation answered and over which months",
      (R_full.get("chargeback_source") or {}).get("feed_table") == "raw_payment_detail"
      and (R_full.get("chargeback_source") or {}).get("configured") is True
      and len((R_full.get("chargeback_source") or {}).get("lag_window") or ()) >= 1,
      str(R_full.get("chargeback_source"))[:200])

# ── THE FOUR WAYS THE AMOUNT CAN BE ABSENT, and none of them is a zero. ────────────────────────
_nopay = call_endpoint(Client(tables(paid=[], declared=DECLARED, routed=ROUTED)))
_n0 = (_nopay.get("cases") or [{}])[0]
check("H7 NO PAYMENT HAS ARRIVED → None, never 0.00. This is the common state on a recent day and "
      "it must not read as 'the carrier paid nothing for this phone'",
      _n0.get("chargeback_amount") is None and _n0.get("chargeback_measured") is False
      and (_nopay.get("summary") or {}).get("chargeback_unmeasured_cases") == 1)
check("H8 and the case is still REPORTED — the pair is the finding, the money is context, so an "
      "absent amount never suppresses a row",
      len(_nopay.get("cases") or ()) == 1)

_unconf = call_endpoint(Client(tables(paid=[paid_line()], declared=DECLARED, routed=[])))
check("H9 NOTHING DECLARED → `configured: false` and every amount unmeasured, even though the feed "
      "carries a payment. With no routing declared there is no declaration that this money is not "
      "commission, and guessing would put a lump sum in a bucket a reader takes for device money",
      (_unconf.get("chargeback_source") or {}).get("configured") is False
      and (_unconf.get("cases") or [{}])[0].get("chargeback_amount") is None,
      str(_unconf.get("chargeback_source"))[:160])

_nomeid = call_endpoint(Client(tables(
    rows=[(dict(r, serial_1="") if r.get("department") else r) for r in LIVE_CASE],
    paid=[paid_line()], declared=DECLARED, routed=ROUTED)))
_m0 = (_nomeid.get("cases") or [{}])[0]
check("H10 NO DEVICE IDENTIFIER ON THE SALE → no amount, because the identifier IS the key the "
      "carrier pays against. The row still names the phone it sold",
      _m0.get("meid") is None and _m0.get("chargeback_amount") is None
      and _m0.get("phone_sold") == "moto g play - 2026 Q4 Promo Upgrade")

_broken = Client(tables(paid=[paid_line()], declared=DECLARED, routed=ROUTED),
                 missing=[("commcalc", "raw_payment_detail")])
R_broken = call_endpoint(_broken)
check("H11 A CARRIER FEED THAT CANNOT BE READ degrades: the report still answers, every amount is "
      "unmeasured, and `chargeback_source` SAYS the feed was not read — so a reader can tell that "
      "from a carrier that has genuinely paid nothing",
      len(R_broken.get("cases") or ()) == 1
      and (R_broken.get("chargeback_source") or {}).get("available") is False
      and (R_broken.get("cases") or [{}])[0].get("chargeback_amount") is None,
      str(R_broken.get("chargeback_source"))[:160])

# ── THE DRILL-DOWN, on the house mechanism rather than a private one. ──────────────────────────
_p = R_full.get("panels") or {}
check("H12 'DRILL DOWN LIKE OTHER MENUS' IS THE SHARED PANEL (§CORE-17) — the endpoint serves a "
      "panel catalogue, so this screen keeps no private drill of its own",
      isinstance(_p, dict) and {"dollars", "chargeback_dollars"} <= set(_p), str(sorted(_p)))
def _sources(panel):
    """The relations a panel's rows actually CITE — the served shape, not the declaration."""
    return sorted({t for r in (panel.get("rows") or ()) for t in (r.get("sources") or ())})


check("H13 BOTH GRAINS ARE SERVED, because the two figures rest on DIFFERENT feeds: the flagged "
      "sale comes from the sales feed and the chargeback amount from the carrier's payment feed. "
      "One panel for both would attribute a carrier payment to the sales feed (§CORE-17.3j)",
      _sources(_p["dollars"]) == ["daily_sales_feed"]
      and _sources(_p["chargeback_dollars"]) == ["raw_payment_detail"],
      f"{_sources(_p['dollars'])} vs {_sources(_p['chargeback_dollars'])}")
check("H14 each panel's figure TIES to the summary figure it explains — a panel that opens to rows "
      "summing to something else is worse than no panel",
      abs(float(_p["chargeback_dollars"]["figure"]["amount"] or 0)
          - float((R_full["summary"] or {}).get("chargeback_dollars") or 0)) < 0.005
      and abs(float(_p["dollars"]["figure"]["amount"] or 0)
              - float((R_full["summary"] or {}).get("dollars") or 0)) < 0.005,
      f"{_p['chargeback_dollars']['figure']} / {_p['dollars']['figure']}")
check("H15 the chargeback row's ADDRESS is the device and the line, not the store and the day — "
      "the carrier's payment feed carries neither of those columns, so keying it that way would "
      "hand a reader an address that relation cannot be looked up by",
      set((_p["chargeback_dollars"]["rows"][0]["origins"][0].get("keys") or {})) == {"meid", "number"}
      and set((_p["dollars"]["rows"][0]["origins"][0].get("keys") or {})) == {"store", "day", "number"},
      str(_p["chargeback_dollars"]["rows"][0]["origins"][0].get("keys")))

# ── THE REASON MUST NAME THE RIGHT ABSENCE. Found by writing this lock: with nothing declared, every
#    row blamed the carrier feed for a lookup that had never happened — a confident statement about
#    an unmeasured thing, which is the fake zero in words instead of in a number (§19.26).
check("H16 an absent amount still SERVES its panel — the rows then name each handset and the feed "
      "they looked in, and a link that explains an absence is worth more than no link",
      "chargeback_dollars" in (_nopay.get("panels") or {})
      and (_nopay["panels"]["chargeback_dollars"]["rows"][0].get("measured") is False),
      str(sorted((_nopay.get("panels") or {}))))

def _reason(payload):
    _pp = (payload.get("panels") or {}).get("chargeback_dollars") or {}
    return ((_pp.get("rows") or [{}])[0]).get("reason")


check("H17 NO PAYMENT, FEED READ → the reason is the carrier's, because the feed really was looked "
      "at and really carries nothing for this handset",
      _reason(_nopay) == _byod_drill.REASON_NOT_PAID_YET, str(_reason(_nopay)))
check("H18 NOTHING DECLARED → the reason is the DECLARATION's, never the carrier's. No payment "
      "could be called a rebate, so none was looked for, and blaming the feed would assert "
      "something about rows nobody read",
      _reason(_unconf) == _byod_drill.REASON_NOT_DECLARED, str(_reason(_unconf)))
check("H19 FEED UNREADABLE → the reason says nothing was looked at, which a reader must be able to "
      "tell from a carrier that has genuinely paid nothing",
      _reason(R_broken) == _byod_drill.REASON_FEED_UNREAD, str(_reason(R_broken)))
check("H20 NO HANDSET ON THE SALE → the reason is the SALE's: there is no device to look up, which "
      "is not a carrier that has not paid",
      _reason(_nomeid) == _byod_drill.REASON_NO_DEVICE, str(_reason(_nomeid)))
check("H21 and the four reasons are four DIFFERENT sentences — collapsing any two would lose the "
      "distinction this section exists to keep",
      len({_byod_drill.REASON_NOT_PAID_YET, _byod_drill.REASON_NOT_DECLARED,
           _byod_drill.REASON_FEED_UNREAD, _byod_drill.REASON_NO_DEVICE}) == 4)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n" + "═" * 100)
print(f"  {len(PASS)} ok, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print(f"    FAIL {f}")
print("═" * 100)
sys.exit(1 if FAIL else 0)
