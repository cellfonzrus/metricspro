#!/usr/bin/env python3
"""THE LOCK — inventory integrity cannot un-wire (index §11b, owner 2026-09-24). Stdlib only, no app import.

CLAUDE.md, "A fix is a DESIGN fix": *"Lock it so it cannot un-wire. A design fix ships with a check that FAILS
THE BUILD if a caller stops dereferencing the shared fact, or if a second copy appears."*

THE CLASSES THIS FEATURE CLOSED, and what fails the build if any comes back:
  (a) A LANDING PATH WITHOUT THE GUARD. Every write that lands a serialised unit — `.table("inventory_serial")
      .insert(` / `.upsert(`, or the onboarding closure's `insert("inventory_serial", …)` — anywhere under
      backend/app must be preceded, in the same function, by THE landing guard (`guard_landing(` /
      `guard_import(`). The named paths (the receive, the edit, the onboarding bring-over) must call it, and the
      POS router's two endpoints must stay one-line delegations.
  (b) A STATUS CHANGE WITHOUT A LEDGER ROW. Every `.table("inventory_serial").update(` under backend/app lives in
      `apply_status_change` (which plans through `plan_adjustment` and inserts the ledger row) or in
      `_update_fields` (which refuses a status). The ledger is inserted only by the integrity router.
  (c) A SECOND IMEI NORMALISER. No device-key normaliser shape (trim → '.0' → upper) anywhere under pos/ or in
      the integrity files; the pure modules take the key INJECTED (they never import device_cost_recon); the
      router injects `_dcr.device_key`.
  (d) THE COMMISSION FEED COPIED. `COMMISSION_PER_DEVICE_FEED` is defined once and the registry spells its table
      once; no integrity reader spells the table or the feed's money / chargeback columns; the engine reads
      COMMISSION_PER_DEVICE_COLUMNS; both routers read through the ONE commission reader.
  (e) A SIBLING ENGINE. classify_unit / integrity / commission_index / sales_detail_index / receive_check each
      defined once; integrity, receive_check and reconcile all call classify_unit; the POS module asks the
      engine (receive_check / integrity) and derives no finding of its own.
  (f) A SECOND CUSTOMER MATCHER. The one-click calls `match_or_create(`; the integrity files never insert a
      customer.
  (g) Org scope: harness_org_scope_guard.py scans the integrity router. RULE TWO: the carrier-vocab guard lists
      the new files. Registered: index §11b + CI + migration 1018 with REVERT notes.
  (h) NEGATIVE CONTROLS — each rule broken in memory must go RED.

  python3 backend/harness_inventory_integrity_lock.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BE = os.path.join(HERE, "app")
sys.path.insert(0, HERE)
from harness_report_links_lock import walk_py, func_body, normaliser_defs   # noqa: E402  (stdlib helpers of the sibling lock)

ENGINE = "modules/commcalc/inventory_sold_recon.py"
PURE = "modules/pos/inventory_integrity.py"
IROUTER = "modules/pos/inventory_integrity_router.py"
POSROUTER = "modules/pos/router.py"
ONBOARD = "modules/core/onboarding.py"
CROUTER = "modules/commcalc/router.py"
REGISTRY = "modules/commcalc/data_lineage_registry.py"
INTEGRITY_FILES = (ENGINE, PURE, IROUTER)
INDEX = os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md")
CI = os.path.join(ROOT, ".github", "workflows", "carrier-vocab-guard.yml")
ORG_GUARD = os.path.join(HERE, "harness_org_scope_guard.py")
VOCAB = os.path.join(HERE, "harness_carrier_vocab_guard.py")
MIG = os.path.join(ROOT, "database", "migrations", "1018_pos_inventory_integrity.sql")

WRITE_RE = re.compile(r"""\.table\(\s*["']inventory_serial["']\s*\)\s*\.\s*(?:insert|upsert)\(|\binsert\(\s*["']inventory_serial["']""")
UPDATE_RE = re.compile(r"""\.table\(\s*["']inventory_serial["']\s*\)\s*\.\s*update\(""")
LEDGER_RE = re.compile(r"""\.table\(\s*["']inventory_adjustments["']\s*\)\s*\.\s*(?:insert|upsert)\(""")
GUARD_CALL = re.compile(r"\bguard_(?:landing|import)\(")
FEED_LITERAL = re.compile(r"""["']raw_vendor_rebate["']""")
COM_COL_LITERAL = re.compile(r"""["'](?:earned_amount|charge_back)["']""")
STATUS_WRITERS = ("apply_status_change", "_update_fields")
ENGINE_DEFS = ("classify_unit", "integrity", "commission_index", "sales_detail_index", "receive_check")

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:500]))


def enclosing(src, pos):
    """(name, start, body) of the TOP-LEVEL function a position sits in (a nested closure — the onboarding
    bring-over's `insert` — belongs to the function that owns it)."""
    top = None
    for m in re.finditer(r"^def (\w+)\s*\(", src[:pos], re.M):
        top = m
    if not top:
        return None, 0, ""
    return top.group(1), top.start(), func_body(src[top.start():], top.group(1))


def defs_in(src, name):
    return len(re.findall(r"^\s*def %s\s*\(" % re.escape(name), src, re.M))


def scan(files):
    """Findings [(relpath, message)] over {relpath: source} of backend/app."""
    out = []
    # (a) every landing write is guarded, in the same function, BEFORE the write
    n_writes = 0
    for rel, src in files.items():
        for m in WRITE_RE.finditer(src):
            n_writes += 1
            name, start, _body = enclosing(src, m.start())
            before = src[start:m.start()]
            if not GUARD_CALL.search(before[-6000:]):
                out.append((rel, f"(a) `{name}` lands a serial unit with no landing guard before the write (guard_landing / guard_import)"))
    if n_writes < 2:
        out.append(("backend/app", f"(a) only {n_writes} serial-unit landing write(s) found — detection may be broken"))
    ir = files.get(IROUTER, "")
    if "guard_landing(" not in func_body(ir, "receive_unit"):
        out.append((IROUTER, "(a) receive_unit does not ask guard_landing"))
    if "guard_landing(" not in func_body(ir, "edit_unit"):
        out.append((IROUTER, "(a) edit_unit does not ask guard_landing for a new serial / IMEI"))
    ob_apply = func_body(files.get(ONBOARD, ""), "apply_import")
    if "_iir.guard_import(" not in ob_apply or "_iir.record_landing(" not in ob_apply:
        out.append((ONBOARD, "(a) the onboarding bring-over no longer lands through guard_import / record_landing"))
    posr = files.get(POSROUTER, "")
    for fn, tok in (("add_inventory_serial", "_iir.receive_unit("), ("update_inventory_serial", "_iir.edit_unit(")):
        b = func_body(posr, fn)
        if tok not in b or ".table(" in b:
            out.append((POSROUTER, f"(a) {fn} is no longer a one-line delegation to {tok}"))
    # (b) one status writer, and it writes the ledger
    for rel, src in files.items():
        for m in UPDATE_RE.finditer(src):
            name, _s, _b = enclosing(src, m.start())
            if not (rel == IROUTER and name in STATUS_WRITERS):
                out.append((rel, f"(b) `{name}` updates inventory_serial outside the one status writer (apply_status_change)"))
        for m in LEDGER_RE.finditer(src):
            if rel != IROUTER:
                out.append((rel, "(b) the adjustment ledger is written outside the integrity router"))
    asc = func_body(ir, "apply_status_change")
    if "_ii.plan_adjustment(" not in asc or not LEDGER_RE.search(asc):
        out.append((IROUTER, "(b) apply_status_change no longer plans through plan_adjustment and writes the ledger row"))
    if 'if "status" in fields' not in func_body(ir, "_update_fields"):
        out.append((IROUTER, "(b) _update_fields no longer refuses a status"))
    # (c) one IMEI key
    for rel, src in files.items():
        if rel.startswith("modules/pos/") or rel in INTEGRITY_FILES:
            for name, shape in normaliser_defs(src):
                if shape == "device":
                    out.append((rel, f"(c) a second device-key (IMEI) normaliser `{name}` — inject device_cost_recon.device_key"))
    for rel in (PURE, IROUTER):
        src = files.get(rel, "")
        for tok in (".upper(", "endswith('.0')", 'endswith(".0")'):
            if tok in src:
                out.append((rel, f"(c) spells a key normaliser of its own: {tok}"))
    for rel in (ENGINE, PURE):
        if re.search(r"^\s*(?:from|import)\s.*device_cost_recon", files.get(rel, ""), re.M):
            out.append((rel, "(c) imports device_cost_recon — the key must be INJECTED"))
    for fn in ("run_engine", "guard_landing", "guard_import"):
        if "_dcr.device_key" not in func_body(ir, fn):
            out.append((IROUTER, f"(c) {fn} does not inject _dcr.device_key"))
    # (d) the commission feed dereferenced
    reg = files.get(REGISTRY, "")
    if defs_in(reg, "commission_per_device_select") != 1 or len(re.findall(r"^COMMISSION_PER_DEVICE_FEED\s*=", reg, re.M)) != 1:
        out.append((REGISTRY, "(d) COMMISSION_PER_DEVICE_FEED / commission_per_device_select not defined exactly once"))
    if len(FEED_LITERAL.findall(reg)) != 1:
        out.append((REGISTRY, f"(d) the registry spells the commission table {len(FEED_LITERAL.findall(reg))} times (must be once — the constant)"))
    for rel in INTEGRITY_FILES:
        src = files.get(rel, "")
        if FEED_LITERAL.search(src):
            out.append((rel, "(d) spells the commission table instead of COMMISSION_PER_DEVICE_FEED"))
        if COM_COL_LITERAL.search(src):
            out.append((rel, "(d) spells a commission column instead of COMMISSION_PER_DEVICE_COLUMNS"))
    cr = files.get(CROUTER, "")
    for fn in ("_inventory_commission_rows", "inventory_sold_recon_endpoint", "_intake_sold_check_after_inventory"):
        b = func_body(cr, fn)
        if FEED_LITERAL.search(b) or COM_COL_LITERAL.search(b):
            out.append((CROUTER, f"(d) {fn} spells the commission feed / its columns"))
    if "COMMISSION_PER_DEVICE_FEED" not in func_body(cr, "_inventory_commission_rows"):
        out.append((CROUTER, "(d) _inventory_commission_rows does not read COMMISSION_PER_DEVICE_FEED"))
    for fn in ("inventory_sold_recon_endpoint", "_intake_sold_check_after_inventory"):
        if "_inventory_commission_rows(" not in func_body(cr, fn):
            out.append((CROUTER, f"(d) {fn} no longer reads the commission report through the one reader"))
    if "_cr._inventory_commission_rows(" not in func_body(ir, "read_sources"):
        out.append((IROUTER, "(d) read_sources does not read the commission report through the one reader"))
    if "_dlr.COMMISSION_PER_DEVICE_COLUMNS" not in func_body(files.get(ENGINE, ""), "commission_index"):
        out.append((ENGINE, "(d) commission_index does not dereference COMMISSION_PER_DEVICE_COLUMNS"))
    # (e) one engine
    for name in ENGINE_DEFS:
        n = sum(defs_in(s, name) for s in files.values())
        if n != 1:
            out.append(("backend/app", f"(e) `def {name}` defined {n} times (must be exactly once)"))
    eng = files.get(ENGINE, "")
    for fn in ("integrity", "receive_check", "reconcile"):
        if "classify_unit(" not in func_body(eng, fn):
            out.append((ENGINE, f"(e) {fn} no longer asks classify_unit (the one per-unit rule)"))
    pure = files.get(PURE, "")
    if "_isr.receive_check(" not in func_body(pure, "import_report"):
        out.append((PURE, "(e) import_report no longer asks the engine's receive_check"))
    for bad in ("net_sold", "classify", "commission_index"):
        if re.search(r"^\s*def \w*%s\w*\s*\(" % bad, pure + "\n" + ir, re.M):
            out.append((PURE, f"(e) the POS side defines its own `{bad}` — a sibling engine"))
    if "_isr.integrity(" not in func_body(ir, "run_engine"):
        out.append((IROUTER, "(e) run_engine does not run the engine"))
    # (f) the one customer matcher
    if "match_or_create(" not in func_body(ir, "assign_flag"):
        out.append((IROUTER, "(f) the one-click no longer finds / creates the customer through receipt_import.match_or_create"))
    for rel in INTEGRITY_FILES:
        if re.search(r"""\.table\(\s*["']customers["']\s*\)\s*\.\s*insert\(""", files.get(rel, "")):
            out.append((rel, "(f) inserts a customer itself — a second matcher"))
    return out


def main():
    print("INVENTORY-INTEGRITY LOCK — one engine, one landing guard, one status writer, one IMEI key, one commission fact\n")
    files = walk_py(BE)
    found = scan(files)
    for rel, msg in found:
        print("  · %s: %s" % (rel, msg))
    for tag, label in (("(a)", "(a) every serial-unit landing asks THE landing guard first; the receive / edit / bring-over are wired; the POS router delegates"),
                       ("(b)", "(b) every status change goes through apply_status_change (plan + ledger row); the ledger has one writer"),
                       ("(c)", "(c) no second IMEI normaliser; the key is injected (_dcr.device_key)"),
                       ("(d)", "(d) the commission feed is ONE registry fact, dereferenced by every reader; one commission reader"),
                       ("(e)", "(e) one engine: the classifier and the reports defined once, every report on classify_unit"),
                       ("(f)", "(f) the one-click uses THE customer matcher; no customer insert of its own")):
        check(label, not any(m.startswith(tag) for _r, m in found), [m for _r, m in found if m.startswith(tag)])
    og = open(ORG_GUARD, encoding="utf-8").read()
    check("(g) the org-scope guard scans the integrity router (every chain org-scoped)",
          '"inventory_integrity_router.py"' in og and "_pos_integrity_guard()" in og)
    vg = open(VOCAB, encoding="utf-8").read()
    check("(g) RULE TWO: the carrier-vocab guard lists the new files in POS_BACKEND_LOGIC",
          all(f'"{p}"' in vg for p in ("pos/inventory_integrity.py", "pos/inventory_integrity_router.py", "commcalc/inventory_sold_recon.py")))
    idx = open(INDEX, encoding="utf-8").read()
    ci = open(CI, encoding="utf-8").read()
    mig = open(MIG, encoding="utf-8").read() if os.path.exists(MIG) else ""
    check("(g) registered: index §11b names the lock and the harness; CI runs the lock; migration 1018 carries REVERT notes",
          "### 11b." in idx and "harness_inventory_integrity_lock.py" in idx and "harness_inventory_integrity.py" in idx
          and "python3 harness_inventory_integrity_lock.py" in ci and "-- REVERT" in mig and "pos.inventory_flags" in mig)

    print("\n  negative controls")
    base = dict(files)
    ir, posr, ob, eng, reg, cr = (base[k] for k in (IROUTER, POSROUTER, ONBOARD, ENGINE, REGISTRY, CROUTER))

    def red(name, mutated, needle):
        f = scan({**base, **mutated})
        check("NEG " + name + " → RED", any(needle in m for _r, m in f), f)

    red("a receive that lands without the guard", {IROUTER: ir.replace("chk = guard_landing(client, org_id, ins)", "chk = _isr.receive_check(ins, [], None, None)")},
        "(a) `receive_unit` lands a serial unit with no landing guard")
    red("a NEW landing path (a bulk import that inserts units unguarded)",
        {"modules/pos/some_import.py": "def bulk(client, org_id, rows):\n    client.schema('pos').table('inventory_serial').insert(rows).execute()\n"},
        "(a) `bulk` lands a serial unit with no landing guard")
    red("the onboarding bring-over losing its guard", {ONBOARD: ob.replace("_iir.guard_import(", "_iir.no_guard(")},
        "(a) the onboarding bring-over no longer lands")
    red("the POS router receiving units itself again", {POSROUTER: posr.replace("    return _iir.receive_unit(body, authorization, org_id)",
                                                                                 "    return sb().schema('pos').table('inventory_serial').insert(body).execute()")},
        "(a) add_inventory_serial is no longer a one-line delegation")
    red("a second status writer (a PATCH that sets status directly)",
        {POSROUTER: posr + "\n\ndef sneaky(org_id, uid):\n    sb().schema('pos').table('inventory_serial').update({'status': 'sold'}).eq('org_id', org_id).eq('id', uid).execute()\n"},
        "(b) `sneaky` updates inventory_serial outside the one status writer")
    red("apply_status_change without its ledger row", {IROUTER: ir.replace('.table("inventory_adjustments").insert(row)', '.table("inventory_log").insert(row)')},
        "(b) apply_status_change no longer plans")
    red("_update_fields that no longer refuses a status", {IROUTER: ir.replace('if "status" in fields:', 'if False:')},
        "(b) _update_fields no longer refuses a status")
    red("a second IMEI normaliser in the POS module",
        {PURE: base[PURE] + "\n\ndef imei_key(v):\n    s = str(v or '').strip()\n    if s.endswith('.0'):\n        s = s[:-2]\n    return s.upper()\n"},
        "(c) a second device-key (IMEI) normaliser `imei_key`")
    red("the router no longer injecting THE key", {IROUTER: ir.replace("_isr.integrity(src[\"units\"], src[\"sales\"], src[\"commission\"], _dcr.device_key,",
                                                                      "_isr.integrity(src[\"units\"], src[\"sales\"], src[\"commission\"], str,")},
        "(c) run_engine does not inject _dcr.device_key")
    red("the commission table copied as a literal into the router",
        {IROUTER: ir.replace("_dlr.COMMISSION_PER_DEVICE_FEED).select(", '"raw_vendor_rebate").select(')},
        "(d) spells the commission table")
    red("a commission column copied into the engine", {ENGINE: eng.replace('c["earned"]', '"earned_amount"')},
        "(d) spells a commission column")
    red("the registry's ingest list spelling the table a second time",
        {REGISTRY: reg.replace("        COMMISSION_PER_DEVICE_FEED,", '        "raw_vendor_rebate",')},
        "(d) the registry spells the commission table 2 times")
    red("the Inventory-vs-Sold endpoint dropping the one commission reader",
        {CROUTER: cr.replace("com, com_ok, com_cut = _inventory_commission_rows(client, org_id)", "com, com_ok, com_cut = [], False, False")},
        "(d) inventory_sold_recon_endpoint no longer reads the commission report")
    red("a sibling classifier in the POS module", {PURE: base[PURE] + "\n\ndef classify_again(u):\n    return None\n"},
        "(e) the POS side defines its own `classify`")
    red("reconcile no longer on the one classifier", {ENGINE: eng.replace("kind, _bucket, sold_on = classify_unit(sdet.get(k), comm[k], None)",
                                                                         "kind, _bucket, sold_on = (None, None, None)")},
        "(e) reconcile no longer asks classify_unit")
    red("the one-click creating its customer itself",
        {IROUTER: ir.replace("matched = _ri.match_or_create(", "matched = _ri.other_matcher(")},
        "(f) the one-click no longer finds / creates the customer")

    print("\n%d passed, %d failed" % (P, F))
    return 1 if F else 0


if __name__ == "__main__":
    sys.exit(main())
