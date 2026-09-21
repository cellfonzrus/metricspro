"""THE LOCK — the tender vocabulary has ONE home, and every tender classifier dereferences it.

CLAUDE.md, "A fix is a DESIGN fix": *"One fact, one home, dereferenced — never copied … Lock it so it
cannot un-wire."* Owner (2026-09-21): *"sales by invoice report also has the tender types on the report,
need to capture that as well — tender types is in columns."*

THE FACT: which canonical tender classes exist, what each folds to on the closing axis and which cash /
card / other gate it belongs to — `closing.router.TENDER_VOCAB`, from which `CANON_TENDERS` /
`CANON_TENDER_LABEL` are DERIVED; the header → class ladder is `closing.router.tender_class`, and
`_canon_tender` is that ladder folded to the axis. The intake's tender-column step (commcalc/
invoice_tenders.py) spells NO tender word: its classifiers are injected from that home; the per-org
overrides are `closing_tender_map` rows (mig 111) with report='invoice'; the Stage-4 tender split rides
the closing recon's own `_xreport_tenders_by_store` + `_addr_resolver`.

WHAT FAILS THE BUILD
  (a) ONE HOME. `TENDER_VOCAB` and `def tender_class` exist exactly once in backend/app; CANON_TENDERS and
      CANON_TENDER_LABEL are derived from it (no literal list); `_canon_tender` calls `tender_class` and
      `fold_to_axis` and spells no word of its own.
  (b) NO SECOND LADDER. invoice_tenders.py contains no tender word (cash / credit / debit / visa / gift …),
      imports nothing from closing (its classifiers arrive as arguments) and calls the injected `resolve` /
      `keyed_of` / `recon_class_of`; the router builds them from the home (tender_class, keyed_manually,
      TENDER_CLASSES, TENDER_RECON_CLASS) through tender_config.make_resolver for report='invoice'.
  (c) THE PRE-EXISTING SIBLINGS ARE NAMED, NOT HIDDEN. Every other tender word list in backend/app is in
      the ALLOW set with the reason it is a different question (the X-report's cash/card/other stamp, the
      mig-944 bill-pay split defaults, the closing B2B money classifier, the standard seed defs, the sample
      classifier's column-shape words); a NEW list outside it → RED; a stale entry → RED.
  (d) THE RECON IS NOT A SIBLING. `_intake_invoice_tender_recon` calls `_xreport_tenders_by_store(` and
      `_addr_resolver(` and never reads pos_tender_summary itself; the invoice map writer touches only
      report='invoice' rows.
  (e) NEGATIVE CONTROLS: a second `TENDER_VOCAB` → RED; a literal CANON_TENDERS → RED; a tender word in
      invoice_tenders → RED; a new word list in a commcalc module → RED; a recon reading the table itself →
      RED; a stale allow entry → RED.

Extends the carrier-vocab guard's posture (a dependency-free static scan on bare Python, one CI job):
.github/workflows/carrier-vocab-guard.yml runs it beside the other locks.

  python3 backend/harness_tender_vocab_lock.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BE = os.path.join(ROOT, "backend", "app")
HOME = "modules/closing/router.py"
PURE = "modules/commcalc/invoice_tenders.py"
CC_ROUTER = "modules/commcalc/router.py"

# LABEL words — what a header / label LADDER spells (brand names, label phrases). The axis KEYS (cash /
# credit / ext_cc / gift …) are column keys the closing module uses everywhere and are NOT a ladder.
TENDER_WORDS = ("visa", "mastercard", "master card", "amex", "american express", "discover", "credit card", "debit card",
                "debit pin", "gift card", "venmo", "cashapp", "cash app", "ven reb", "vendor reb", "non-integrated", "non integrated",
                "store account", "external credit", "acima lease", "coupon")
_LIT = re.compile(r"""['"]([^'"\n]{1,40})['"]""")


def tender_words_in(snippet):
    """The DISTINCT label words spelled as string literals in a snippet (case-insensitive containment)."""
    lits = {m.group(1).strip().lower() for m in _LIT.finditer(snippet)}
    return {w for w in TENDER_WORDS if any(w in l for l in lits)}


# ── ALLOW SET — (relative path, symbol) → reason. A stale entry (symbol gone) fails. ─────────────
ALLOW = {
    (HOME, "TENDER_VOCAB"): "THE home",
    (HOME, "tender_class"): "THE ladder (the one header / label → class function)",
    (HOME, "KEYED_MANUALLY_WORDS"): "THE home's 'keyed by hand' words (a non-integrated twin) — beside the vocabulary, one home",
    ("modules/commcalc/report_labels.py", "LABELABLE_COLUMNS"): "PRE-EXISTING: the carrier-label preset registry's COLUMN captions (mig 945/953 — what a report column is called on a page), not a classifier",
    ("modules/commcalc/atu_opportunity.py", "CARD_TOKENS"): "PRE-EXISTING: the ATU opportunity report's card-tender tokens for its own cash-vs-card split (§10) — a gate for one report; routing it through the home is its own PR (a seam, index §30.13)",
    ("modules/commcalc/column_mapping.py", "TARGET_FIELDS"): "PRE-EXISTING: layout header SPELLINGS (the invoice export's 'Gift Card Sales' / 'Total Coupons' are invoice FIELDS, not tenders) — a column map, not a classifier",
    (HOME, "_CARD_HINTS"): "PRE-EXISTING: closing._tender_class buckets a sales-line tender for the B2B money recon (cash / card / other) — a gate, not a class; index §12",
    ("modules/commcalc/router.py", "_XR_TENDERS"): "PRE-EXISTING: the X-report ingest's accepted-label list (mig 062), widened by _xr_canon_known → the home's ladder (§23h)",
    ("modules/commcalc/router.py", "_XR_CARD_WORDS"): "PRE-EXISTING: _xr_tender_class stamps pos_tender_summary.tender_class cash / card / other — the recon GATE per raw label (§23h), not a class vocabulary",
    ("modules/commcalc/merchant_portals.py", "_BRAND_SYNONYMS"): "PRE-EXISTING: the card-processor settlement normaliser's card BRAND synonyms (mig 955 — which brand a settlement row is, per merchant-day), not a tender class; §12a",
    ("modules/closing/tender_config.py", "STANDARD_DEFS"): "PRE-EXISTING: the seedable closing_tender_def rows for a 'standard' tenant (mig 111) — a seed of the axis, read back through tender_axis; folding it into TENDER_VOCAB is a closing-agent change (a seam, index §30.13)",
}
assert all(v for v in ALLOW.values()), "every allow entry carries a reason"

# (e) the basis — readers of the two tender tables that are NOT a tender split (named), the consumers that must
#     read through the resolver, and the only functions that may touch the config column
BASIS_READERS_ALLOW = {
    (HOME, "_xreport_rows_by_store"): "THE X-report leg of the resolver",
    (HOME, "_invoice_tenders_by_store"): "THE invoice leg of the resolver",
    (HOME, "_closing_summary_for_date"): "x_report_ever: 'has this tenant EVER had an X-report' (a presence probe, not a split; §23h)",
    (HOME, "_tender_recon_3way_day"): "the 3-WAY recon's own X-report LEG per tender key (mig 111 axis; a per-tender leg beside the closing and sales legs, by design — the basis switch is the closing recons', not the 3-way's; a seam, index §30.13)",
    (HOME, "detect_tenders"): "the tender-config wizard lists the distinct raw X-report labels to map (no amounts)",
    ("modules/commcalc/router.py", "_intake_reread_invoice_tenders"): "the intake's own re-read of the slice it just landed (the save guarantee), not a recon read",
}
BASIS_CONSUMERS = ("_closing_summary_for_date", "deposit_recon_report", "_pos_tenders_for_days")
# the column is READ by the home's reader, its pure row parser and the closing summary's once-per-request org context
# (which hands the row it already reads to the parser — harness_dmverify_parity M3 pins ONE tenants read); WRITTEN only by put_tender_basis
BASIS_CONFIG_ALLOW = {(HOME, "tender_basis"), (HOME, "tender_basis_from_row"), (HOME, "put_tender_basis"), (HOME, "_closing_summary_org_ctx")}

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


def code(src):
    """Source without comment lines and docstrings (crude, enough for literal scans)."""
    body = "\n".join(l for l in src.split("\n") if not l.strip().startswith("#"))
    body = re.sub(r'"""[\s\S]*?"""', "", body)
    body = re.sub(r"'''[\s\S]*?'''", "", body)
    return body


_DEF_RE = re.compile(r"^([ \t]*)def (\w+)\s*\(")


def func_body(src, name):
    lines = src.split("\n")
    indents = [(len(ln) - len(ln.lstrip(" \t"))) if ln.strip() else None for ln in lines]
    for i, ln in enumerate(lines):
        m = _DEF_RE.match(ln)
        if not m or m.group(2) != name:
            continue
        ind = len(m.group(1))
        j = i + 1
        while j < len(lines) and (indents[j] is None or indents[j] > ind):
            j += 1
        return "\n".join(lines[i:j])
    return ""


def _stmt_rhs(body, start):
    """The RHS of a module-level assignment from `start` up to the next line that starts at column 0."""
    lines = body[start:].split("\n")
    out = [lines[0]]
    for ln in lines[1:]:
        if ln and not ln[0].isspace():
            break
        out.append(ln)
    return "\n".join(out)


def word_lists(src):
    """[(symbol, words)] — module-level assignments and def bodies that spell ≥2 DISTINCT tender words as
    string literals (a word LIST, not a key); the cash / card / other gate triple is not one."""
    out = []
    body = code(src)
    for m in re.finditer(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", body, re.M):
        rhs = _stmt_rhs(body, m.start())
        words = tender_words_in(rhs)
        if len(words) >= 2 or m.group(1) == "TENDER_VOCAB":
            out.append((m.group(1), sorted(words)))
    for m in re.finditer(r"^\s*def (\w+)\s*\(", body, re.M):
        words = tender_words_in(func_body(body, m.group(1)))
        if len(words) >= 2:
            out.append((m.group(1), sorted(words)))
    return out


def scan(files, allow):
    findings = []
    home = files.get(HOME, "")
    # (a) one home
    n_vocab = sum(len(re.findall(r"^TENDER_VOCAB\s*=", code(s), re.M)) for s in files.values())
    n_ladder = sum(len(re.findall(r"^def tender_class\s*\(", code(s), re.M)) for s in files.values())
    if n_vocab != 1:
        findings.append((HOME, f"TENDER_VOCAB defined {n_vocab} times (must be exactly once)"))
    if n_ladder != 1:
        findings.append((HOME, f"def tender_class defined {n_ladder} times (must be exactly once)"))
    hb = code(home)
    if not re.search(r"^CANON_TENDERS\s*=\s*\[k for", hb, re.M) or not re.search(r"^CANON_TENDER_LABEL\s*=\s*\{k: l for", hb, re.M):
        findings.append((HOME, "CANON_TENDERS / CANON_TENDER_LABEL are not derived from TENDER_VOCAB (a literal list is a second copy)"))
    ct = func_body(hb, "_canon_tender")
    if "tender_class(" not in ct or "fold_to_axis(" not in ct or tender_words_in(ct):
        findings.append((HOME, "_canon_tender must be tender_class folded to the axis, spelling no word of its own"))
    # (b) no second ladder in the pure module; the router injects from the home
    pure = code(files.get(PURE, ""))
    for w in sorted(tender_words_in(pure)):
        findings.append((PURE, f"spells the tender word '{w}' — the classes must arrive injected from the home"))
    if re.search(r"^\s*(from|import)\s+app\.modules\.closing", pure, re.M) or "closing.router" in pure.replace("closing_tender_map", ""):
        findings.append((PURE, "imports the closing module — the classifiers must be injected"))
    for tok in ("resolve(", "keyed_of("):
        if tok not in pure:
            findings.append((PURE, f"no longer calls the injected {tok}"))
    rt = files.get(CC_ROUTER, "")
    res = func_body(rt, "_intake_tender_resolver")
    for tok in ("_cr.tender_class", "_cr.TENDER_CLASSES", "make_resolver(", "_cr.keyed_manually", "_invt.MAP_REPORT"):
        if tok not in res:
            findings.append((CC_ROUTER, f"_intake_tender_resolver does not build from the home / the mig-111 map ({tok})"))
    vocab_fn = func_body(rt, "_intake_tender_vocab")
    if "_cr.TENDER_VOCAB" not in vocab_fn:
        findings.append((CC_ROUTER, "_intake_tender_vocab does not read TENDER_VOCAB"))
    # (d) the recon is not a sibling
    rec = code(func_body(rt, "_intake_invoice_tender_recon"))
    for tok in ("_cr._tender_split_by_store(", "_cr._invoice_tenders_by_store(", "_cr.tender_basis_info("):
        if tok not in rec:
            findings.append((CC_ROUTER, f"_intake_invoice_tender_recon does not ride {tok}"))
    if re.search(r"table\(\s*['\"]pos_tender_summary", rec) or ".table(" in rec:
        findings.append((CC_ROUTER, "_intake_invoice_tender_recon reads a table itself instead of the closing recon's reader"))
    wm = func_body(rt, "_intake_write_tender_map")
    if '.eq("report", _invt.MAP_REPORT)' not in wm:
        findings.append((CC_ROUTER, "_intake_write_tender_map's delete is not scoped to report='invoice' (it would wipe the X-report / sales legs' rules)"))
    # (e) THE TENDER BASIS HAS ONE RESOLVER (owner 2026-09-21 "nothing on cash collected either"):
    #     tender_basis / put_tender_basis / _tender_split_by_store / _invoice_tenders_by_store defined once (in
    #     the home); _xreport_tenders_by_store is the resolver's totals; the invoice tender table is read in
    #     closing ONLY by _invoice_tenders_by_store; pos_tender_summary is read for a tender SPLIT only by the
    #     X-report leg (every other reader is a named non-split read); every closing consumer of the split
    #     calls the resolver; the intake writes the basis only through put_tender_basis.
    hb_raw = files.get(HOME, "")
    for name in ("tender_basis", "put_tender_basis", "_tender_split_by_store", "_invoice_tenders_by_store", "_xreport_rows_by_store"):
        n = sum(len(re.findall(r"^def %s\s*\(" % re.escape(name), code(x), re.M)) for x in files.values())
        if n != 1:
            findings.append((HOME, f"def {name} defined {n} times (must be exactly once, in the home)"))
    if "_tender_split_by_store(client, org_id, date, basis)[\"totals\"]" not in code(func_body(hb_raw, "_xreport_tenders_by_store")):
        findings.append((HOME, "_xreport_tenders_by_store is no longer the resolver's totals (a sibling reader)"))
    core = code(func_body(hb_raw, "_tender_split_by_store"))
    for tok in ("tender_basis(client, org_id)", "_xreport_rows_by_store(", "_invoice_tenders_by_store("):
        if tok not in core:
            findings.append((HOME, f"_tender_split_by_store no longer dereferences {tok}"))
    for rel, src in files.items():
        if not (rel.startswith("modules/commcalc/") or rel.startswith("modules/closing/")):
            continue
        body = code(src)
        for m in re.finditer(r"^\s*(?:async\s+)?def (\w+)\s*\(", body, re.M):
            fb = func_body(body, m.group(1))
            if re.search(r"table\(\s*['\"]raw_sales_invoice_tender['\"]", fb) and (rel, m.group(1)) not in BASIS_READERS_ALLOW:
                findings.append((rel, f"`{m.group(1)}` reads raw_sales_invoice_tender itself — the closing module reads it only through _invoice_tenders_by_store (allow a non-split reader by name)"))
            if rel == HOME and re.search(r"table\(\s*['\"]pos_tender_summary['\"]", fb) and (rel, m.group(1)) not in BASIS_READERS_ALLOW:
                findings.append((rel, f"`{m.group(1)}` reads pos_tender_summary itself — a tender split is read only through the resolver (allow a non-split reader by name)"))
            if re.search(r"closing_tender_basis", fb) and (rel, m.group(1)) not in BASIS_CONFIG_ALLOW:
                findings.append((rel, f"`{m.group(1)}` touches closing_tender_basis — only the home's reader / parser / writer and the summary's org context may"))
            if re.search(r"update\(\s*\{[^}]*closing_tender_basis", fb) and (rel, m.group(1)) != (HOME, "put_tender_basis"):
                findings.append((rel, f"`{m.group(1)}` WRITES closing_tender_basis — only put_tender_basis may"))
    for fn in BASIS_CONSUMERS:
        fb = code(func_body(hb_raw, fn))
        if "_tender_split_by_store(" not in fb and "_xreport_tenders_by_store(" not in fb:
            findings.append((HOME, f"closing consumer `{fn}` no longer reads the tender split through the resolver"))
    for key in BASIS_READERS_ALLOW:
        if key[0] in files and not re.search(r"^\s*(?:async\s+)?def %s\s*\(" % re.escape(key[1]), code(files[key[0]]), re.M):
            findings.append((key[0], f"STALE allow entry `{key[1]}` — the def is gone; remove the entry"))
    # (c) the siblings are named
    seen = set()
    for rel, src in files.items():
        if not (rel.startswith("modules/commcalc/") or rel.startswith("modules/closing/")):
            continue
        for sym, words in word_lists(src):
            key = (rel, sym)
            seen.add(key)
            if key not in allow:
                findings.append((rel, f"a tender word list `{sym}` outside the home ({', '.join(words)}) — dereference TENDER_VOCAB / tender_class, or allow it with the reason it is a different question"))
    for key in allow:
        if key not in seen:
            findings.append((key[0], f"STALE allow entry `{key[1]}` — the symbol is gone; remove the entry"))
    return findings


def main():
    print("TENDER-VOCAB LOCK — one tender vocabulary (closing.router.TENDER_VOCAB); every classifier dereferences it\n")
    files = walk_py(BE)
    findings = scan(files, ALLOW)
    for rel, msg in findings:
        print("  · %s: %s" % (rel, msg))
    check("(a) TENDER_VOCAB / tender_class defined once; CANON_TENDERS and CANON_TENDER_LABEL derived; _canon_tender folds the ladder",
          not any(r == HOME for r, _m in findings), findings)
    check("(b) invoice_tenders.py spells no tender word, imports nothing from closing, calls the injected classifiers; the router builds them from the home through the mig-111 map",
          not any(r == PURE for r, _m in findings) and not any("_intake_tender" in m for _r, m in findings), findings)
    check("(c) every other tender word list in commcalc / closing is a NAMED pre-existing sibling with its reason; no stale entry",
          not any("word list" in m or "STALE" in m for _r, m in findings), findings)
    check("(d) the Stage-4 tender split rides the resolver's two legs; the map writer touches only report='invoice'",
          not any("_intake_invoice_tender_recon" in m or "_intake_write_tender_map" in m for _r, m in findings), findings)
    check("(e) the tender basis has ONE resolver: the readers of both tender tables are the resolver's legs (or named non-split reads), every closing consumer reads through it, only the home touches the config column and only put_tender_basis writes it",
          not any("resolver" in m or "closing_tender_basis" in m or "reads raw_sales_invoice_tender" in m or "reads pos_tender_summary" in m or "must be exactly once, in the home" in m for _r, m in findings), findings)
    for k, why in sorted(ALLOW.items()):
        print("      allow %-52s [%s] — %s" % (k[0], k[1], why[:70]))

    print("\n  negative controls")
    base = dict(files)
    f1 = scan({**base, "modules/commcalc/other.py": 'TENDER_VOCAB = [("cash", "Cash", "cash", True, None)]\n'}, ALLOW)
    check("NEG a second TENDER_VOCAB → RED", any("TENDER_VOCAB defined 2" in m for _r, m in f1), f1)
    f2 = scan({**base, HOME: base[HOME].replace('CANON_TENDERS = [k for (k, _l, _c, ax, _f) in TENDER_VOCAB if ax]', 'CANON_TENDERS = ["cash", "credit", "ext_cc", "gift", "store_acct", "zelle", "acima"]')}, ALLOW)
    check("NEG a literal CANON_TENDERS beside the vocabulary → RED", any("not derived" in m for _r, m in f2), f2)
    f3 = scan({**base, PURE: base[PURE] + '\n\ndef my_class(h):\n    return "credit" if "visa" in h or "credit card" in h else None\n'}, ALLOW)
    check("NEG a tender word spelled in invoice_tenders → RED", any(r == PURE and "spells the tender word" in m for r, m in f3), f3)
    f4 = scan({**base, "modules/commcalc/other.py": 'MY_CARDS = ("visa", "mastercard", "discover")\n'}, ALLOW)
    check("NEG a new tender word list in another commcalc module → RED", any("word list `MY_CARDS`" in m for _r, m in f4), f4)
    f5 = scan({**base, CC_ROUTER: base[CC_ROUTER].replace("_cr._tender_split_by_store(client, org_id, d, basis=\"x_report\")[\"totals\"]", "(client.schema('commcalc').table('pos_tender_summary').select('*').execute().data and {})")}, ALLOW)
    check("NEG the recon reading pos_tender_summary itself → RED", any("reads a table itself" in m or "does not ride" in m for _r, m in f5), f5)
    f6 = scan(base, {**ALLOW, ("modules/commcalc/gone.py", "NOPE"): "a symbol that no longer exists"})
    check("NEG a stale allow entry → RED", any("STALE allow entry" in m for _r, m in f6), f6)
    f7 = scan({**base, HOME: base[HOME] + "\n\ndef my_split(client, org_id, date):\n    return client.schema('commcalc').table('raw_sales_invoice_tender').select('*').execute().data\n"}, ALLOW)
    check("NEG a second reader of the invoice tender table in closing → RED", any("reads raw_sales_invoice_tender itself" in m for _r, m in f7), f7)
    f8 = scan({**base, HOME: base[HOME].replace("        xrep_cache[dstr] = _xreport_tenders_by_store(client, org_id, dstr)", "        xrep_cache[dstr] = {}")}, ALLOW)
    check("NEG a closing consumer bypassing the resolver → RED", any("no longer reads the tender split through the resolver" in m for _r, m in f8), f8)
    f9 = scan({**base, "modules/commcalc/other.py": "def set_it(client, org_id):\n    client.schema('storeops').table('tenants').update({'closing_tender_basis': 'invoice'}).execute()\n"}, ALLOW)
    check("NEG a second writer of the basis column → RED", any("touches closing_tender_basis" in m for _r, m in f9), f9)

    print("\n%d passed, %d failed" % (P, F))
    return 1 if F else 0


if __name__ == "__main__":
    sys.exit(main())
