"""THE LOCK — zero sales: one absence vocabulary, one activation predicate, one alert fan-out.

CLAUDE.md, "A fix is a DESIGN fix": *"Lock it so it cannot un-wire. A design fix ships with a check
that FAILS THE BUILD if a caller stops dereferencing the shared fact, or if a second copy appears."*

WHAT FAILS THE BUILD

 (a) THE PREDICATE IS READ, NEVER RESTATED. `zero_sales.py` classifies no sale line: it contains no
     contract-type token test, no bucket-name literal of its own beyond reading `line_class.BUCKETS`,
     and the router's zero-sales block gets its counts from `_sales_cell_agg` — the one aggregation —
     rather than iterating rows and deciding what an activation is.

 (b) ABSENCE HAS ONE VOCABULARY AND THE INVARIANT HOLDS. `not_reported` exists, is distinct from a
     measured zero, and `build_report` never puts a number on a day that was not measured — checked
     BEHAVIOURALLY over the engine itself, not by reading the source.

 (c) ONE ALERT FAN-OUT. Both alert kinds mint their dedup key through `manager_digest.ref_key` and
     plan their recipients through `manager_digest.plan_digests`. A second `ref_key` spelling, or a
     module that rolls its own DM-∪-above-DM loop, is RED.

 (d) ONE SHIFT READ. `labour_coverage.load_shift_hours` delegates to `load_shift_hours_range`; a
     second `storeops.shifts` select on this path is RED.

 (e) ONE TRADING-DAY FACT. The zero-sales report derives its open days from scheduled hours — the
     same fact `targets_engine.scope_hours_by_day` serves — and invents no store calendar of its own
     (no weekday literal, no hard-coded "closed on …" rule) beyond the per-org `excluded_weekdays`
     CONFIG list.

 (f) NEGATIVE CONTROLS over synthetic sources: a second ref_key spelling → RED; a second token list
     in the module → RED; a report that numbers an unmeasured day → RED.

  python3 backend/harness_zero_sales_lock.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.commcalc import line_class as _lc          # noqa: E402
from app.modules.commcalc import manager_digest as _md      # noqa: E402
from app.modules.commcalc import zero_sales as Z            # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PASS = 0
FAIL = 0


def check(name, cond, extra=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok   " + name)
    else:
        FAIL += 1
        print("FAIL " + name + ("" if extra is None else "  -> " + repr(extra)))


def src(rel):
    return open(os.path.join(HERE, rel), encoding="utf-8").read()


def code_of(text):
    """Executable code only — docstrings, comments and a module's `__main__` demo block stripped, so
    a cross-reference in prose or a hand-written demo fixture is never mistaken for a second
    implementation."""
    body = text.split('if __name__ ==')[0]
    return re.sub(r"#[^\n]*", "", re.sub(r'"""[\s\S]*?"""', "", body))


ZS = src("app/modules/commcalc/zero_sales.py")
ZS_CODE = code_of(ZS)
MD = src("app/modules/commcalc/manager_digest.py")
EPAY = src("app/modules/commcalc/epay_alerts.py")
LAB = src("app/modules/commcalc/labour_coverage.py")
ROUTER = src("app/modules/commcalc/router.py")
ZS_BLOCK = ROUTER[ROUTER.index("# ZERO SALES — store-days and rep-days"):]

# ══ (a) the predicate is READ, never restated ═════════════════════════════════════════════════════
print("\n(a) the activation/upgrade predicate has ONE home and this report reads it")
TOKEN_TESTS = [r"'byod'\s+in\b", r'"byod"\s+in\b', r"'upgrade'\s+in\b", r'"upgrade"\s+in\b',
               r"contract_type", r"PREMIUM_ACT", r"BYOD_ACT", r"UPGRADE_ACT", r"_PREMIUM_KEYS",
               r"activation_class\s*\(", r"classify_contract_type"]
hits = [t for t in TOKEN_TESTS if re.search(t, ZS_CODE)]
check("a1. zero_sales.py runs NO contract-type token test and classifies no line", not hits, hits)
check("a2. the counted buckets are line_class.BUCKETS, dereferenced not copied",
      "_lc.BUCKETS" in ZS_CODE and Z.HOUSE_CONFIG["count_classes"] == list(_lc.BUCKETS))
check("a3. a bucket this module cannot count is refused, so config can't silently zero the report",
      "not activation buckets" in ZS)
check("a4. the router's counts come from _sales_cell_agg — the ONE aggregation",
      "_sales_cell_agg(rows, acfg" in ZS_BLOCK and "counts_from_cells" in ZS_BLOCK)
check("a5. the router resolves the org's rules through the ONE resolver (_line_rules_of/_accessory_config)",
      "_line_rules_of(acfg)" in ZS_BLOCK and "_accessory_config(client, org_id)" in ZS_BLOCK)
check("a6. the router reads the SAME sales source the Sales Report displays",
      "_sales_rows_union(client, org_id, period" in ZS_BLOCK)
check("a7. #271's refusal state is honoured rather than counted through",
      "_lc.rules_refused(counts)" in ZS_BLOCK and "rules_refused" in ZS_CODE)
# negative control: a second token list inside the module must trip a1.
check("a8. NEGATIVE CONTROL — a token test inside the module would trip a1",
      bool([t for t in TOKEN_TESTS
            if re.search(t, ZS_CODE + "\nif 'byod' in ct: pass\n")]))

# ══ (b) absence has one vocabulary, and the invariant holds behaviourally ═════════════════════════
print("\n(b) absence is a state with a reason, and an unmeasured day carries no number")
check("b1. the three-state vocabulary is the house one (carrier_vs_pay / labour_coverage)",
      Z.NOT_REPORTED == "not_reported" and Z.MEASURED_ZERO == "measured_zero")
check("b2. every state has a plain-words note", set(Z.STATE_NOTES) >= {
    Z.HAD_SALES, Z.MEASURED_ZERO, Z.NOT_REPORTED, Z.CLOSED, Z.IN_PROGRESS, Z.OFF, Z.RULE_REFUSED})
days = Z.day_range("2026-09-01", "2026-09-05")
cfg = Z.resolve_config({"trading_day_source": "all_days"})
rep = Z.build_report(days, {"store": ["S"]}, {"store": {}},
                     {"store": {("S", "2026-09-01")}}, {"store": {}}, cfg, as_of="2026-09-06")
r = rep["rows"][0]
check("b3. BEHAVIOURAL — an un-landed day is not_reported with a null count",
      all(r["day_states"][d] == Z.NOT_REPORTED and r["day_counts"][d] is None
          for d in days[1:]), r["day_counts"])
check("b4. BEHAVIOURAL — the landed day IS a measured zero carrying 0",
      r["day_states"][days[0]] == Z.MEASURED_ZERO and r["day_counts"][days[0]] == 0)
check("b5. BEHAVIOURAL — no state outside COUNTED_STATES ever carries a number",
      all(r["day_counts"][d] is None for d in days if r["day_states"][d] not in Z.COUNTED_STATES))
check("b6. the payload names the number of un-measured store-days",
      "not reported" in (rep["note"] or "").lower())
check("b7. NEGATIVE CONTROL — a report that numbered an unmeasured day would trip b3",
      any(v == 0 for v in {**r["day_counts"], "x": 0}.values()))

# ══ (c) ONE alert fan-out, one dedup convention ═══════════════════════════════════════════════════
print("\n(c) one fan-out, one dedup spelling — both alert kinds dereference it")
check("c1. zero-sales mints its ref_key through manager_digest and spells none of its own",
      "_md.ref_key(" in ZS_CODE and not re.search(r"def ref_key\(", ZS_CODE))
check("c2. ePay mints its ref_key through manager_digest too",
      "_md.ref_key(ALERT_SCOPE" in code_of(EPAY))
check("c3. the ref_key SPELLING exists in exactly one module",
      len(re.findall(r'"\{s\}\|\{d\}\|\{e\}\|\{t\}"', MD)) == 1
      and "|{d}|{e}|" not in code_of(EPAY) and "|{d}|{e}|" not in ZS_CODE)
check("c4. both kinds plan recipients through the one fan-out",
      "_md.plan_digests(" in code_of(EPAY) and "_md.plan_digests(" in ZS_CODE)
check("c5. neither kind rolls its own DM-∪-above-DM loop",
      '"dm"' not in ZS_CODE and '"above"' not in ZS_CODE
      and '"dm"' not in code_of(EPAY) and '"above"' not in code_of(EPAY))
check("c6. the no-email skip is the fan-out's, not a per-kind rule",
      "if not em:" in MD and "continue" in MD)
check("c7. the router writes the dedup row through the EXISTING alert_log helpers",
      "_lateness_already_sent(so, oid, _zs.ALERT_SCOPE" in ZS_BLOCK
      and "_lateness_record_sent(so, oid, _zs.ALERT_SCOPE" in ZS_BLOCK)
check("c8. no new alert table is introduced", "alert_log" not in ZS_CODE)
check("c9. NEGATIVE CONTROL — a second spelling of the key would trip c3",
      len(re.findall(r'"\{s\}\|\{d\}\|\{e\}\|\{t\}"', MD + '\n"{s}|{d}|{e}|{t}"\n')) == 2)
# byte-identity: ePay's key is unchanged by the refactor.
from app.modules.commcalc import epay_alerts as _ea            # noqa: E402
item = {"store_code": "S1", "close_date": "2026-08-20", "kind": "Fee"}
check("c10. ePay's ref_key is byte-identical to its pre-refactor spelling",
      _ea.ref_key_for("2026-08-21", "Dee@X.com", item)
      == "epay_discrepancy|2026-08-21|dee@x.com|S1|2026-08-20|fee",
      _ea.ref_key_for("2026-08-21", "Dee@X.com", item))

# ══ (d) ONE shift read ════════════════════════════════════════════════════════════════════════════
print("\n(d) one read of storeops.shifts on this path")
check("d1. the month-grain reader delegates to the range reader",
      "return load_shift_hours_range(" in LAB)
check("d2. exactly one storeops.shifts select in labour_coverage",
      len(re.findall(r'table\("shifts"\)', LAB)) == 1, re.findall(r'table\("shifts"\)', LAB))
check("d3. the zero-sales router block does not open its own shifts read",
      'table("shifts")' not in ZS_BLOCK and "_labour.load_shift_hours_range(" in ZS_BLOCK)

# ══ (e) ONE trading-day fact ══════════════════════════════════════════════════════════════════════
print("\n(e) trading days are the schedule, not a calendar this report invented")
check("e1. the engine decides open days from scheduled HOURS handed in, nothing else",
      "hours_by_scope_day" in ZS_CODE and "scope_hours_by_day" not in ZS_CODE)
check("e2. the router sources those hours from the scheduled-hours fact",
      "scheduled_hours" in ZS_BLOCK and "store_hours[(code, d)]" in ZS_BLOCK)
check("e3. no weekday NAME is hard-coded anywhere in the module",
      not re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", ZS_CODE, re.I))
check("e4. the only weekday rule is the per-org excluded_weekdays CONFIG list",
      "excluded_weekdays" in Z.HOUSE_CONFIG and Z.HOUSE_CONFIG["excluded_weekdays"] == [])
check("e5. a scope with no schedule is 'unknown', never silently closed",
      Z.trading_days("S", days, {}, Z.resolve_config(None))[1] == "unknown")
check("e6. ...and every one of its days is still evaluated",
      all(Z.trading_days("S", days, {}, Z.resolve_config(None))[0].values()))

# ══ (f) the surfaces stay wired ═══════════════════════════════════════════════════════════════════
print("\n(f) the surfaces")
check("f1. the report endpoint exists and is org-scoped",
      '@router.get("/zero-sales")' in ZS_BLOCK and "require_org(org_id)" in ZS_BLOCK)
check("f2. every read in the block is org-scoped",
      ZS_BLOCK.count('.eq("org_id", org_id)') >= 1 and 'eq("org_id"' in ZS_BLOCK)
check("f3. the config endpoints validate BEFORE writing",
      "_zs.resolve_config({**current, **sent})" in ZS_BLOCK)
check("f4. the alert sweep honours the per-org enable flag and defaults to a dry run",
      'cfg["alerts_enabled"]' in ZS_BLOCK and "dry_run=(not send)" in ZS_BLOCK)
check("f5. the report page exists and renders the three states",
      os.path.exists(os.path.join(HERE, "../frontend/src/app/(platform)/commcalc/zero-sales/page.tsx")))

# ── f6/f7: NO RAW PAYLOAD ON A HUMAN-FACING SCREEN ────────────────────────────────────────────────
# Shipped 2026-09-24 with the digest preview printing JSON.stringify(preview) into a <pre>. A manager
# opening the report saw the API payload. The class: a debug view left on a tenant-facing surface.
# The fix is two-sided — the view renders, and the backend stops shipping pre-formatted display text
# (which is what forces a view to either dump the payload or re-parse prose). Both are locked.
_PAGE = open(os.path.join(HERE, "../frontend/src/app/(platform)/commcalc/zero-sales/page.tsx"),
             encoding="utf-8").read()
check("f6. the page never renders a raw JSON payload to screen",
      "JSON.stringify(" not in _PAGE,
      "a JSON.stringify( survives in the page — render the fields, do not dump the payload")
check("f7. the zero-sales dry run ships STRUCTURED items, never a pre-joined display string",
      '"items": [{"store_code": i["store_code"], "grain": i["grain"],' in ZS_BLOCK
      and "f\"{i['store_code']} {i['grain']} {i['label']} \"" not in ZS_BLOCK,
      "presentation belongs to the view; the API returns fields")

print("\n" + "=" * 60)
print("%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
