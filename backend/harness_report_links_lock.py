"""THE LOCK — ONE device / phone-number pairing implementation, and the report links call it.

CLAUDE.md, "A fix is a DESIGN fix": *"Lock it so it cannot un-wire. A design fix ships with a check
that FAILS THE BUILD if a caller stops dereferencing the shared fact, or if a second copy appears."*
Index §30.11 (Stage D): the inventory-vs-activations check (#254) and the Stage-4 report links pair
lines to devices through ONE rule — `inventory_sold_recon.line_pairings` (a line's own serial, else
THROUGH a sale line carrying its phone number via `sales_mobile_index`, else unpairable with the
reason) — with the three key normalisers living in their homes (`device_cost_recon.device_key`,
`inventory_sold_recon.mobile_key`, `device_cost_recon.norm_order`).

WHAT FAILS THE BUILD
  (a) the pairing core defined more than once: `def line_pairings`, `def activation_index`,
      `def sales_mobile_index`, `def mobile_key`, `def device_key` each exactly ONCE across
      backend/app; `activation_index` no longer a fold of `line_pairings`.
  (b) `report_links.py` spelling a normaliser or a pairing of its own (a `def` named like one, or a
      body that strips digits / upper-cases / trims '.0' / uses a regex), or not calling
      `line_pairings(` and `sales_mobile_index(`.
  (c) the router's link readers not injecting the three normalisers from their homes, not passing
      `inventory_sold_recon.line_pairings` / `sales_mobile_index` to `report_links.report`, or
      querying a table of their own instead of the kinds' existing re-reads; the inventory auto-check
      no longer riding `reconcile` (→ `activation_index` → `line_pairings`).
  (d) a SECOND phone-number normaliser (a `def` whose body strips to digits and keeps the last ten)
      or device-key normaliser (trim → drop '.0' → upper) anywhere in backend/app/modules/commcalc
      outside the ALLOW set below, where every entry carries its reason and a STALE entry fails too.
  (e) NEGATIVE CONTROLS over synthetic sources through the same scanner: a second `def mobile_key`
      → RED; a digit-stripping helper in report_links → RED; a report_links that never calls the
      core → RED; a router link reader with its own `.table(` → RED; a stale allow entry → RED.

Extends the carrier-vocab guard's posture (a dependency-free static scan on bare Python, one CI job):
.github/workflows/carrier-vocab-guard.yml runs it beside the report-kind and mapping-key locks.

  python3 backend/harness_report_links_lock.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BE = os.path.join(ROOT, "backend", "app")
CC = os.path.join(BE, "modules", "commcalc")
CORE = os.path.join(CC, "inventory_sold_recon.py")
LINKS = os.path.join(CC, "report_links.py")
ROUTER = os.path.join(CC, "router.py")
DCR = os.path.join(CC, "device_cost_recon.py")

CORE_DEFS = ("line_pairings", "activation_index", "sales_mobile_index", "mobile_key")
DEVICE_DEF = "device_key"
LINK_ROUTER_FUNCS = ("_intake_link_normalisers", "_intake_link_source", "_intake_link_activations",
                     "_intake_report_links_compute", "_intake_report_links")

# a phone-number normaliser: strips to digits and keeps the last ten
MOBILE_SHAPE = re.compile(r"isdigit\(\)[\s\S]{0,160}\[-10:\]|\[-10:\][\s\S]{0,160}isdigit\(\)")
# a device-key normaliser: drops a trailing '.0' and upper-cases
DEVICE_SHAPE = re.compile(r"endswith\(['\"]\.0['\"]\)[\s\S]{0,200}\.upper\(\)")
NORMALISER_TOKENS = re.compile(r"isdigit\(|\.upper\(|endswith\(['\"]\.0['\"]\)|re\.sub\(|^\s*import re\b", re.M)

# ── ALLOW SET — (relative path, function name) → reason. A stale entry fails. ────────────────────
ALLOW = {
    ("modules/commcalc/inventory_sold_recon.py", "mobile_key"): "THE home of the phone-number key (§11a / §30.11)",
    ("modules/commcalc/device_cost_recon.py", "device_key"): "THE home of the cross-source device key (mig 009's spelling, §11)",
    ("modules/commcalc/whatif.py", "_norm_mdn"):
        "PRE-EXISTING sibling of mobile_key (what-if BYOD MDN set), reported as a seam in index §30.11 — not a link-family caller; "
        "routing it through inventory_sold_recon.mobile_key is its own PR",
}
assert all(v for v in ALLOW.values()), "every allow entry carries a reason"

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:400]))


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def walk_py(base):
    out = {}
    for dp, _dn, fns in os.walk(base):
        for fn in fns:
            if fn.endswith(".py"):
                full = os.path.join(dp, fn)
                out[os.path.relpath(full, BE).replace(os.sep, "/")] = read(full)
    return out


def defs_in(src, name):
    return len(re.findall(r"^\s*def %s\s*\(" % re.escape(name), src, re.M))


_DEF_RE = re.compile(r"^([ \t]*)def (\w+)\s*\(")


def _bodies(src):
    """[(name, body)] for EVERY def in `src` (top-level or nested) in one pass: a body runs from its
    `def` line to the first following non-blank line indented no deeper than the def itself."""
    lines = src.split("\n")
    indents = [(len(ln) - len(ln.lstrip(" \t"))) if ln.strip() else None for ln in lines]
    out = []
    for i, ln in enumerate(lines):
        m = _DEF_RE.match(ln)
        if not m:
            continue
        ind = len(m.group(1))
        j = i + 1
        while j < len(lines) and (indents[j] is None or indents[j] > ind):
            j += 1
        out.append((m.group(2), "\n".join(lines[i:j])))
    return out


def func_body(src, name):
    """The source of the FIRST def named `name` in `src`."""
    return next((b for n, b in _bodies(src) if n == name), "")


def normaliser_defs(src):
    """[(name, shape)] — every def in `src` whose body has the phone-number or device-key shape."""
    out = []
    for name, body in _bodies(src):
        if MOBILE_SHAPE.search(body):
            out.append((name, "mobile"))
        elif DEVICE_SHAPE.search(body):
            out.append((name, "device"))
    return out


def scan(files, allow):
    """The (a)–(d) findings over a {relpath: source} map. Returns a list of (relpath, finding)."""
    findings = []
    # (a) the core defined once
    for name in CORE_DEFS + (DEVICE_DEF,):
        n = sum(defs_in(s, name) for s in files.values())
        if n != 1:
            findings.append(("backend/app", f"`def {name}` defined {n} times (must be exactly once)"))
    core = files.get("modules/commcalc/inventory_sold_recon.py", "")
    if core and "line_pairings(" not in func_body(core, "activation_index"):
        findings.append(("modules/commcalc/inventory_sold_recon.py", "activation_index no longer folds line_pairings"))
    # (b) report_links defines no normaliser / pairing of its own, and calls the core
    rl = files.get("modules/commcalc/report_links.py", "")
    if rl:
        for name in CORE_DEFS + (DEVICE_DEF, "norm_order"):
            if defs_in(rl, name):
                findings.append(("modules/commcalc/report_links.py", f"defines its own `{name}`"))
        for m in NORMALISER_TOKENS.finditer(rl):
            findings.append(("modules/commcalc/report_links.py", f"spells a normaliser of its own: {m.group(0).strip()}"))
        if "line_pairings(" not in rl or "sales_mobile_index(" not in rl:
            findings.append(("modules/commcalc/report_links.py", "does not call the pairing core (line_pairings / sales_mobile_index)"))
    # (c) the router injects the homes and passes the core; the readers make no query of their own
    rt = files.get("modules/commcalc/router.py", "")
    if rt:
        norm = func_body(rt, "_intake_link_normalisers")
        for tok in ("_dcr.device_key", "_isr.mobile_key", "_dcr.norm_order"):
            if tok not in norm:
                findings.append(("modules/commcalc/router.py", f"_intake_link_normalisers does not inject {tok}"))
        comp = func_body(rt, "_intake_report_links_compute")
        for tok in ("_isr.line_pairings", "_isr.sales_mobile_index", "_rl.report("):
            if tok not in comp:
                findings.append(("modules/commcalc/router.py", f"_intake_report_links_compute does not pass {tok}"))
        for fn in ("_intake_link_source", "_intake_link_activations"):
            if ".table(" in func_body(rt, fn):
                findings.append(("modules/commcalc/router.py", f"{fn} queries a table of its own instead of the kinds' re-reads"))
        # 2026-09-21 — the invoice export (sales by invoice) links by invoice number / store / date through ITS re-read
        if "_intake_reread_invoice(" not in func_body(rt, "_intake_link_source"):
            findings.append(("modules/commcalc/router.py", "_intake_link_source has no invoice branch through _intake_reread_invoice (the invoice export would be unlinkable)"))
        if "_isr.reconcile(" not in func_body(rt, "_intake_sold_check_after_inventory"):
            findings.append(("modules/commcalc/router.py", "_intake_sold_check_after_inventory no longer rides inventory_sold_recon.reconcile"))
    # (d) a second normaliser anywhere in commcalc outside the allow set
    seen = set()
    for rel, src in files.items():
        if not rel.startswith("modules/commcalc/"):
            continue
        for name, shape in normaliser_defs(src):
            key = (rel, name)
            seen.add(key)
            if key not in allow:
                findings.append((rel, f"a second {shape}-key normaliser `{name}` (route it through the home, or allow it with a reason)"))
    for key in allow:
        if key not in seen:
            findings.append((key[0], f"STALE allow entry `{key[1]}` — the def is gone; remove the entry"))
    return findings


def main():
    print("REPORT-LINKS LOCK — one pairing implementation, called by the links and the inventory check\n")
    files = walk_py(BE)
    findings = scan(files, ALLOW)
    for rel, msg in findings:
        print("  · %s: %s" % (rel, msg))
    check("(a) line_pairings / activation_index / sales_mobile_index / mobile_key / device_key each defined ONCE; activation_index folds line_pairings",
          not any("defined" in m or "folds" in m for _r, m in findings), findings)
    check("(b) report_links.py defines no normaliser or pairing of its own and calls the core",
          not any(r.endswith("report_links.py") for r, _m in findings), findings)
    check("(c) the router injects the three homes, passes the core, reads only through the re-reads; the inventory check rides reconcile",
          not any(r.endswith("router.py") for r, _m in findings), findings)
    check("(d) no second phone-number / device-key normaliser in commcalc outside the allow set; no stale allow entry",
          not any("normaliser" in m or "STALE" in m for _r, m in findings), findings)
    check("the allow set is small and reasoned (the two homes + the one pre-existing sibling, reported as a seam)",
          len(ALLOW) == 3 and all(len(v) > 20 for v in ALLOW.values()))

    # (e) NEGATIVE CONTROLS — synthetic sources through the same scanner
    print("\n  negative controls")
    base = {k: v for k, v in files.items()}
    core_src = base["modules/commcalc/inventory_sold_recon.py"]
    rl_src = base["modules/commcalc/report_links.py"]
    rt_src = base["modules/commcalc/router.py"]

    f1 = scan({**base, "modules/commcalc/report_links.py": rl_src + "\n\ndef mobile_key(v):\n    d = ''.join(ch for ch in str(v or '') if ch.isdigit())\n    return d[-10:] if len(d) >= 10 else None\n"}, ALLOW)
    check("NEG a second `def mobile_key` (in report_links) → RED on (a) and (b)",
          any("defined 2 times" in m for _r, m in f1) and any("defines its own `mobile_key`" in m for _r, m in f1), f1)
    f2 = scan({**base, "modules/commcalc/report_links.py": rl_src + "\n\ndef _digits(v):\n    return ''.join(ch for ch in str(v or '') if ch.isdigit())[-10:]\n"}, ALLOW)
    check("NEG a digit-stripping helper in report_links → RED (spells a normaliser) and RED on (d) (a second phone-number normaliser)",
          any("spells a normaliser" in m for _r, m in f2) and any("second mobile-key normaliser `_digits`" in m for _r, m in f2), f2)
    f3 = scan({**base, "modules/commcalc/report_links.py": rl_src.replace("line_pairings(", "LINE_PAIRINGS_GONE(")}, ALLOW)
    check("NEG a report_links that never calls the pairing core → RED", any("does not call the pairing core" in m for _r, m in f3), f3)
    f4 = scan({**base, "modules/commcalc/inventory_sold_recon.py": core_src.replace("line_pairings(rows, key_of, sale_rows, serial_field, mobile_field, id_field, sale_key_field, sale_mobile_field)", "[]")}, ALLOW)
    check("NEG an activation_index that stops folding line_pairings → RED", any("no longer folds" in m for _r, m in f4), f4)
    f5 = scan({**base, "modules/commcalc/router.py": rt_src.replace(
        "def _intake_link_source(client, org_id, inst, registry_rows, store_resolve, rep_resolve):",
        "def _intake_link_source(client, org_id, inst, registry_rows, store_resolve, rep_resolve):\n    _x = client.schema('commcalc').table('raw_sales').select('*').eq('org_id', org_id).execute()")}, ALLOW)
    check("NEG a router link reader with a query of its own → RED", any("queries a table of its own" in m for _r, m in f5), f5)
    f6 = scan({**base, "modules/commcalc/router.py": rt_src.replace("_isr.line_pairings, _isr.sales_mobile_index", "None, None")}, ALLOW)
    check("NEG the router no longer passing the core to report_links.report → RED", any("does not pass _isr.line_pairings" in m for _r, m in f6), f6)
    f7 = scan({**base, "modules/commcalc/other_thing.py": "def phone_of(v):\n    d = ''.join(ch for ch in str(v or '') if ch.isdigit())\n    return d[-10:]\n"}, ALLOW)
    check("NEG a second phone-number normaliser in another commcalc module → RED", any("second mobile-key normaliser `phone_of`" in m for _r, m in f7), f7)
    f8 = scan({**base, "modules/commcalc/other_thing.py": "def key_of(v):\n    s = str(v or '').strip()\n    if s.endswith('.0'):\n        s = s[:-2]\n    return s.upper()\n"}, ALLOW)
    check("NEG a second device-key normaliser (trim → '.0' → upper) → RED", any("second device-key normaliser `key_of`" in m for _r, m in f8), f8)
    f10 = scan({**base, "modules/commcalc/router.py": rt_src.replace("_intake_reread_invoice(client, org_id, id_values, span[\"from\"], span[\"to\"], kind=p.get(\"layout\") or None)", "[]")}, ALLOW)
    check("NEG the link reader dropping the invoice branch → RED", any("no invoice branch" in m for _r, m in f10), f10)
    f9 = scan(base, {**ALLOW, ("modules/commcalc/gone.py", "nope"): "a def that no longer exists"})
    check("NEG a stale allow entry → RED", any("STALE allow entry" in m for _r, m in f9), f9)

    print("\n%d passed, %d failed" % (P, F))
    return 1 if F else 0


if __name__ == "__main__":
    sys.exit(main())
