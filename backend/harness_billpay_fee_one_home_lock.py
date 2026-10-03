"""THE LOCK — "which product_desc is the bill-payment SERVICE FEE" has ONE home, and every caller that
answers a question about a fee line dereferences it.

CLAUDE.md, "A fix is a DESIGN fix": *"Lock it so it cannot un-wire. A design fix ships with a check that
FAILS THE BUILD if a caller stops dereferencing the shared fact, or if a second copy appears."*

THE CLASS (index §19.42, owner report 2026-10-03). The Sales Report told the owner that 693 October
transactions "have no contract type and no activation rule matched — map them … so they count as
activations". 690 of them were walk-in BILL PAYMENTS: the payment line was suppressed by the RTR exclusion,
and the customer SERVICE FEE line, rung as its own sales line, was not — so every bill payment read as an
activation-capable transaction. The general fact that was wrong is not "Boost's fee line is unmapped"; it is
that a classifier asking *"could this have been an activation?"* re-decided what a fee line is instead of
reading the one registry that already knew.

WHAT FAILS THE BUILD
  (a) ONE REGISTRY. The fee vocabulary and its house default live in
      `commcalc/epay_fee_recon.py` (`FEE_DESC` / `HOUSE_FEE_DESCS` / `resolve_fee_descs` / `is_fee_desc`)
      and nowhere else: no other backend app file spells the literal fee wording.
  (b) ONE RESOLUTION PER READER. Every place that turns the per-org mig-1045 column
      `accessory_config.billpay_fee_product_desc` into a vocabulary goes through `resolve_fee_descs` —
      a reader that starts lower-casing / defaulting that column itself is a second copy.
  (c) THE DEREFERENCING CALLERS, by name:
        · `router._accessory_config_uncached` — resolves it onto `acfg['billpay_fee_descs']`;
        · `router._txn_activation_candidate` — reads that key (the fix this lock exists for);
        · `router._billpay_fee_tokens` — the pickup-netting / fee-cash basis;
        · `account/coa.py` — the P&L booking of the fee.
  (d) RULE TWO. The predicate carries no carrier / tenant / POS vendor name: it must not test the
      fee by a literal wording of its own.
  (e) THE PROOF + CI. The proof harness exists and the workflow runs both it and this lock.
  (f) NEGATIVE CONTROLS over synthetic text: a second literal copy of the vocabulary → RED; a caller
      that stops dereferencing → RED; a hardcoded fee wording inside the predicate → RED.

Runs beside the other locks (.github/workflows/carrier-vocab-guard.yml). stdlib only, no I/O.

  python3 backend/harness_billpay_fee_one_home_lock.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BE = os.path.join(ROOT, "backend")
BE_APP = os.path.join(BE, "app")
REGISTRY_REL = os.path.join("modules", "commcalc", "epay_fee_recon.py")
REGISTRY = os.path.join(BE_APP, REGISTRY_REL)
ROUTER = os.path.join(BE_APP, "modules", "commcalc", "router.py")
COA = os.path.join(BE_APP, "modules", "account", "coa.py")
PROOF = os.path.join(BE, "harness_billpay_fee_not_activation.py")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "carrier-vocab-guard.yml")

# The house fee wording, spelled once HERE so the scan below can look for copies of it. This file is a
# static scanner, not a classifier: nothing reads this to decide anything about a sales line.
FEE_WORDING = "epay service charge"
VENDOR_WORDS = ("boost", "total wireless", "vidapay", "t-cetra", "luxelink", "xfinity", "acima")

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name}{(' — ' + detail) if detail else ''}")


def read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def block(src, header_re):
    """The body of the first def whose header matches, to its dedent. Pure text."""
    m = re.search(header_re, src)
    if not m:
        return ""
    rest = src[m.start():]
    lines = rest.splitlines()
    out = [lines[0]]
    for ln in lines[1:]:
        if ln.strip() and not ln.startswith((" ", "\t")):
            break
        out.append(ln)
    return "\n".join(out)


def app_py_files():
    for base, _dirs, files in os.walk(BE_APP):
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(base, f)


registry = read(REGISTRY)
router = read(ROUTER)
coa = read(COA)

# ── (a) ONE REGISTRY ──────────────────────────────────────────────────────────────────────────────
ok("a1 the registry module exists", bool(registry), REGISTRY_REL)
for sym in ("FEE_DESC", "HOUSE_FEE_DESCS", "def resolve_fee_descs", "def is_fee_desc"):
    ok(f"a2 the registry declares {sym}", sym in registry)
# A second COPY is the wording used as a TEST of a sales line — a comparison or a containment — not the
# wording appearing as a display label or in prose. The P&L's account label ("ePay service charge (fee
# income)") and the ingest / alert docstrings are text a human reads; they classify nothing.
_MATCH_OPS = ("==", "!=", " in ", "startswith", "endswith", "find(", "match(", "search(")
copies = []
for path in app_py_files():
    if os.path.abspath(path) == os.path.abspath(REGISTRY):
        continue
    src = read(path)
    for i, ln in enumerate(src.splitlines(), 1):
        low = ln.lower()
        if FEE_WORDING in low and not ln.lstrip().startswith("#") and any(o in low for o in _MATCH_OPS):
            copies.append(f"{os.path.relpath(path, ROOT)}:{i}")
ok("a3 NO second copy of the fee wording used to TEST a line in backend app code",
   not copies, ", ".join(copies[:6]))

# ── (b) ONE RESOLUTION PER READER ─────────────────────────────────────────────────────────────────
col = "billpay_fee_product_desc"
readers = []
for path in app_py_files():
    src = read(path)
    if col in src:
        readers.append(os.path.relpath(path, ROOT))
ok("b1 the mig-1045 column is read only in the router", readers == ["backend/app/modules/commcalc/router.py"],
   ", ".join(readers))
for name, body in (("_billpay_fee_tokens", block(router, r"def _billpay_fee_tokens\(")),
                   ("_accessory_config_uncached", block(router, r"def _accessory_config_uncached\("))):
    ok(f"b2 {name} resolves the column through the registry, never itself",
       col in body and "resolve_fee_descs(" in body, name)
    ok(f"b3 {name} carries no fee wording of its own — no second default",
       FEE_WORDING not in body.lower(), name)

# ── (c) THE DEREFERENCING CALLERS, BY NAME ────────────────────────────────────────────────────────
cand = block(router, r"def _txn_activation_candidate\(")
ok("c1 _txn_activation_candidate exists", bool(cand))
ok("c2 it READS the resolved fact off the config (it does not decide it)",
   "billpay_fee_descs" in cand, "the fee test must dereference acfg['billpay_fee_descs']")
ok("c3 the config loader puts it there",
   "\"billpay_fee_descs\": tuple(" in router or "'billpay_fee_descs': tuple(" in router)
ok("c4 the predicate actually SKIPS a fee line",
   re.search(r"_fee_descs[\s\S]{0,200}continue", cand) is not None)
ok("c5 the pickup-netting / fee-cash basis still dereferences the registry",
   "_fr.aggregate_fee_cash(" in router and "_billpay_fee_tokens(" in router)
ok("c6 the P&L booking still dereferences the registry", "is_fee_desc(" in coa)

# ── (d) RULE TWO ──────────────────────────────────────────────────────────────────────────────────
# Strip BOTH the docstring (prose naming the tenants the defect was measured on) and the `#` comments:
# RULE TWO is about executable code.
_body = cand.split('"""', 2)
code_only = "\n".join(ln for ln in (_body[2] if len(_body) == 3 else cand).splitlines()
                       if not ln.lstrip().startswith("#"))
hit = [w for w in VENDOR_WORDS if w in code_only.lower()]
ok("d1 no carrier / tenant / vendor name in the predicate's CODE", not hit, ", ".join(hit))
ok("d2 no literal fee wording in the predicate", FEE_WORDING not in code_only.lower())

# ── (e) THE PROOF + CI ────────────────────────────────────────────────────────────────────────────
proof = read(PROOF)
wf = read(WORKFLOW)
ok("e1 the proof harness exists", bool(proof), os.path.relpath(PROOF, ROOT))
ok("e2 the proof drives the REAL functions, not a copy",
   "_classification_gaps" in proof and "_txn_activation_candidate" in proof
   and "_accessory_config_uncached" in proof)
ok("e3 CI runs the proof", "harness_billpay_fee_not_activation.py" in wf)
ok("e4 CI runs this lock", "harness_billpay_fee_one_home_lock.py" in wf)

# ── (f) NEGATIVE CONTROLS ─────────────────────────────────────────────────────────────────────────
ok("f1 NEG a second literal copy would be caught",
   FEE_WORDING in "    if prod == 'ePay Service Charge':".lower())
ok("f2 NEG a caller that stops dereferencing would be caught",
   "billpay_fee_descs" not in "def _txn_activation_candidate(lines, acfg, is_excluded=None):\n    return True\n")
ok("f3 NEG a hardcoded wording inside the predicate would be caught",
   FEE_WORDING in "        if 'epay service charge' in _p: continue".lower())
ok("f4 NEG a reader that re-implements the default would be caught",
   "resolve_fee_descs(" not in "    cfg = row.get('billpay_fee_product_desc') or ['epay service charge']")

print(f"PASS {len(PASS)}")
for p in PASS:
    print(f"  [PASS] {p}")
if FAIL:
    print(f"\n❌ {len(FAIL)} failure(s):")
    for f in FAIL:
        print(f"  [FAIL] {f}")
    sys.exit(1)
print("\n✅ harness_billpay_fee_one_home_lock: ALL PASS")
