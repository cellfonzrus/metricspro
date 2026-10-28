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


#: The live case, as the 95 measured ones carry it: a BYOD activation with no handset, then a
#: SEPARATE receipt the same day upgrading the same number onto a promo device.
LIVE_CASE = [
    line(product_desc="Unlimited Data, Talk & Text + 30GB"),
    line(product_desc="Autopay"),
    line(trans_id="215350", contract_type="Upgrade", product_desc="Unlimited+ $50"),
    line(trans_id="215350", contract_type="Upgrade", product_desc="Autopay"),
    line(trans_id="215350", contract_type="Upgrade", department="Android - XP",
         product_desc="moto g play - 2026 Q4 Promo Upgrade", ext_price=0.99),
    line(trans_id="215350", contract_type="Upgrade", ext_price=35.0,
         product_desc="Device Setup Charge Upgrade (non-refundable)"),
]


def tables(rows=None, *, box=BOX, adr=None, rule_rows=None, archive=None):
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
print("\n" + "═" * 100)
print(f"  {len(PASS)} ok, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print(f"    FAIL {f}")
print("═" * 100)
sys.exit(1 if FAIL else 0)
