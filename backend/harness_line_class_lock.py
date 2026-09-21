"""THE LOCK — a sale line's activation type has ONE predicate and ONE rules home, and every caller reads it.

CLAUDE.md, "A fix is a DESIGN fix": *"Lock it so it cannot un-wire. A design fix ships with a check that
FAILS THE BUILD if a caller stops dereferencing the shared fact, or if a second copy appears."*

Owner (2026-09-21): *"sales report shows 88 txns but not a break up in to activations and upgrade etc,
also nothing on exec mtd"* — the activation type was read from ONE fixed column by token lists copied
into three places; a POS carrying the fact elsewhere read zero everywhere.

WHAT FAILS THE BUILD
  (a) ONE HOME. The token lists (`HOUSE_TOKENS` / `HOUSE_HINTS`, the retired `PREMIUM_ACT` /
      `_PREMIUM_KEYS` / `BYOD_ACT` / `UPGRADE_ACT` / `_AUTO_ACT_CATEGORY_KEYS`) and the retired functions
      (`_exec_act_class`, `_resolve_ct_bucket`) exist NOWHERE in backend/app but line_class.py; a
      contract-type token check (`'byod' in ct` and kin) outside line_class.py is a second predicate →
      RED, unless the file is EXCUSED by name with a reason that is still true (a different feed, a
      different question — each re-verified below).
  (b) EVERY CALLER DEREFERENCES. Each module that classifies a sale line imports the predicate
      (`classify_line` / `line_class`) and calls no `classify_contract_type(` (the bare-column alias) —
      the pay path reads `cfg['line_class_rules']`, the aggregation calls `_lc.activation_class(` and no
      longer reads the exec-MTD `activation` tokens.
  (c) ONE RULES HOME. `accessory_config.activation_details_rules` is read by the one loader
      (`_accessory_config_uncached`) and written by the one writer (`put_accessory_config`); the 2.5a
      step (`onboarding_intake_put_line_class`) writes ONLY through `put_accessory_config` and
      `put_exec_metric_config` (no insert / upsert / update / delete of its own).
  (d) THE SURFACES. stage2.tsx carries step 2.5a and renders LineClassStep (line-class-step.tsx, the one
      caller of /line-class — rules, metric_rules and the attestation); the Executive MTD page renders
      `landing.classified` with a ScreenLink to the intake; the metric-definitions editor no longer
      offers the retired `activation` token row.
  (e) NEGATIVE CONTROLS over synthetic sources: a second token list → RED; a caller that reverts to the
      bare alias → RED; a stale excuse → RED; a step that upserts on its own → RED.

Runs beside the carrier-vocab / report-kind / landing-identity locks (.github/workflows/carrier-vocab-guard.yml).

  python3 backend/harness_line_class_lock.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BE_APP = os.path.join(ROOT, "backend", "app")
FE = os.path.join(ROOT, "frontend", "src")
HOME = "modules/commcalc/line_class.py"
STAGE2 = "app/(platform)/onboarding/intake/stage2.tsx"
STEP_FILE = "app/(platform)/onboarding/intake/line-class-step.tsx"
MTD_PAGE = "app/(platform)/commcalc/exec/mtd/page.tsx"
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "carrier-vocab-guard.yml")

# (a) token-check excuses — (relative path) → reason; each must STILL carry a token check (stale → RED)
EXCUSED = {
    "modules/commcalc/activation_bucketing.py":
        "the Activation-Details BASIS (a carrier-portal sheet, not a POS sale line): its own family vocabulary "
        "(Home Internet / Edge / Tablet / BYOD Upgrade) and its own precedence (Upgrade before BYOD, owner recon "
        "2026-08-26); folding it into the line predicate would move 'BYOD Upgrade' out of the excluded family (§15)",
    "modules/asset/router.py":
        "_promo_type — the carrier hotsheet's PROMO-column taxonomy (Upgrade / AAL / Port-In / Non-Port), a "
        "different question (which promo column is expected), on a carrier-gated page",
    "modules/commcalc/sale_installment_engine.py":
        "the residual-installment CATEGORY chain over the carrier's residual feed rows (bill_payment / rebate / "
        "upgrade / swap / activation / misc), not a POS sale line's activation type (§7)",
}
# (b) the callers and the dereference each must show
CALLERS = {
    "modules/commcalc/calculator.py": ["from app.modules.commcalc import line_class", "classify_line(r, _line_rules)", "line_class_rules"],
    "modules/commcalc/router.py": ["_lc.activation_class(r, line_rules)", "_lc.classify_line(", "'line_class_rules': _acfg['line_rules']", "_lc.count_classes("],
    "modules/commcalc/payout_accrual.py": ["line_class_rules"],
    "modules/commcalc/commission_engine.py": ["_lc.classify_line(r, line_rules)"],
    "modules/commcalc/plan_options.py": ["_lc.classify_line("],
    "modules/commcalc/whatif.py": ["classify_line(r, _rules)"],
    "modules/commcalc/sales_comparison.py": ["classify_line(ln, line_rules)"],
    "modules/closing/router.py": ["classify_line(r, _lr)"],
    "modules/marketing/event_sales.py": ["classify_line(row, rules)"],
}
RETIRED_NAMES = ("PREMIUM_ACT", "_PREMIUM_KEYS", "BYOD_ACT", "UPGRADE_ACT", "_AUTO_ACT_CATEGORY_KEYS",
                 "_exec_act_class", "_resolve_ct_bucket", "HOUSE_TOKENS", "HOUSE_HINTS")
TOKEN_CHECK = re.compile(r"""['"](?:byod|upgrade|activation|port(?:-in| in)?)['"]\s+in\s+(?:ct|cl|ctl|c|_ct|contract_type|tt|nm)\b""")
RULES_HOME_ALLOWED = {"modules/commcalc/router.py", "modules/commcalc/line_class.py", "modules/commcalc/activation_bucketing.py",
                      "modules/commcalc/commission_engine.py"}

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


def walk(root, exts):
    files = {}
    for dp, dirs, fs in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "node_modules", ".next")]
        for f in fs:
            if f.endswith(exts):
                p = os.path.join(dp, f)
                files[os.path.relpath(p, root).replace(os.sep, "/")] = read(p)
    return files


def code_lines(src):
    """Python source without comments and docstrings (a mention in prose is not a second copy)."""
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    src = re.sub(r"'''[\s\S]*?'''", "", src)
    return "\n".join(ln for ln in src.split("\n") if not ln.strip().startswith("#"))


def strip_comment(ln):
    return ln.split("#", 1)[0]


# ── (a) one home ─────────────────────────────────────────────────────────────────────────────────
def scan_one_home(files, excused):
    v = []
    for rel, src in sorted(files.items()):
        if rel == HOME:
            continue
        body = code_lines(src)
        for name in RETIRED_NAMES:
            if re.search(r"^\s*%s\s*=" % re.escape(name), body, re.M) or re.search(r"^\s*def\s+%s\s*\(" % re.escape(name), body, re.M):
                v.append((rel, "defines " + name))
            if re.search(r"\b%s\s*\(" % re.escape(name), body) and name in ("_exec_act_class", "_resolve_ct_bucket"):
                v.append((rel, "calls " + name))
        hits = [ln.strip() for ln in body.split("\n") if TOKEN_CHECK.search(strip_comment(ln))]
        if hits and rel not in excused:
            v.append((rel, "contract-type token check: " + hits[0][:80]))
    stale = [rel for rel in excused if rel not in files or not any(TOKEN_CHECK.search(strip_comment(ln)) for ln in code_lines(files[rel]).split("\n"))]
    return v, stale


def scan_callers(files, callers):
    v = []
    for rel, needs in callers.items():
        src = files.get(rel)
        if src is None:
            v.append((rel, "missing"))
            continue
        body = code_lines(src)
        for n in needs:
            if n not in body:
                v.append((rel, "no longer carries: " + n))
    for rel, src in sorted(files.items()):
        if rel in (HOME, "modules/commcalc/calculator.py"):
            continue
        if re.search(r"\bclassify_contract_type\s*\(", code_lines(src)):
            v.append((rel, "calls the bare-column alias classify_contract_type("))
    return v


def scan_rules_home(files):
    v = []
    for rel, src in sorted(files.items()):
        if "activation_details_rules" in code_lines(src) and rel not in RULES_HOME_ALLOWED:
            v.append((rel, "touches activation_details_rules outside the loader / writer / two resolvers"))
    return v


def fn_body(src, name):
    m = re.search(r"^(?:async\s+)?def\s+%s\s*\(" % re.escape(name), src, re.M)
    if not m:
        return None
    rest = src[m.start():]
    nxt = re.search(r"^(?:@router\.|def |class |async def )", rest[m.end() - m.start():], re.M)
    return rest[: (m.end() - m.start()) + nxt.start()] if nxt else rest


def scan_step_writes(router_src):
    v = []
    body = fn_body(router_src, "onboarding_intake_put_line_class")
    if body is None:
        return [("router.py", "onboarding_intake_put_line_class missing")]
    for must in ("put_accessory_config(", "put_exec_metric_config(", "_intake_save_state("):
        if must not in body:
            v.append(("router.py", "2.5a step no longer writes through " + must))
    if ".table(" in code_lines(body):
        v.append(("router.py", "2.5a step touches a table on its own (.table( … ) — writes go through the two writers only"))
    agg = fn_body(router_src, "_sales_cell_agg") or ""
    if "exec_cfg.get('activation'" in agg or 'exec_cfg.get("activation"' in agg:
        v.append(("router.py", "_sales_cell_agg reads the retired exec-MTD activation tokens"))
    if "_lc.activation_class(" not in agg:
        v.append(("router.py", "_sales_cell_agg does not call the predicate"))
    loader = fn_body(router_src, "_accessory_config_uncached") or ""
    if "activation_details_rules" not in loader or "_lc.resolve_rules(" not in loader:
        v.append(("router.py", "_accessory_config_uncached no longer resolves line_rules from activation_details_rules"))
    writer = fn_body(router_src, "put_accessory_config") or ""
    if "activation_details_rules" not in writer or "_lc.merge_into_raw(" not in writer:
        v.append(("router.py", "put_accessory_config no longer writes activation_details_rules through merge_into_raw"))
    return v


def scan_surfaces(fe):
    v = []
    s2 = fe.get(STAGE2, "")
    if "'2.5a'" not in s2 or "LineClassStep" not in s2:
        v.append((STAGE2, "no 2.5a step / does not render LineClassStep"))
    st = fe.get(STEP_FILE, "")
    if "/line-class" not in st or "no_activations" not in st or "metric_rules" not in st:
        v.append((STEP_FILE, "the step no longer reads / saves through /line-class (rules, metric_rules, the attestation)"))
    mtd = fe.get(MTD_PAGE, "")
    if "classified" not in mtd or 'ScreenLink to="onboarding_intake"' not in mtd:
        v.append((MTD_PAGE, "does not render landing.classified with a ScreenLink to the intake"))
    if re.search(r"activation:\s*\[\s*'byod'", mtd):
        v.append((MTD_PAGE, "the metric editor still offers the retired activation token row"))
    return v


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("line-class lock — one predicate, one rules home, every caller dereferences\n")
BE = walk(BE_APP, (".py",))
FE_FILES = walk(FE, (".ts", ".tsx"))
assert HOME in BE, "line_class.py missing"

v, stale = scan_one_home(BE, EXCUSED)
check("(a) no second token list, no retired classifier, no contract-type token check outside line_class.py (excused files named with a reason)", not v, v)
check("(a) every excuse is still true (the file still carries its own token check)", not stale, stale)
check("(a) the home carries the house tokens, the predicate, the resolver, the suggestion engine and the gate",
      all(x in BE[HOME] for x in ("HOUSE_TOKENS", "def activation_class(", "def resolve_rules(", "def suggest_rules(", "def suggest_metric_rules(", "def gate_open(")))

v = scan_callers(BE, CALLERS)
check("(b) every caller dereferences the predicate; no app module calls the bare-column alias", not v, v)

v = scan_rules_home(BE)
check("(c) activation_details_rules is touched only by the loader, the writer and the two resolvers", not v, v)
v = scan_step_writes(BE["modules/commcalc/router.py"])
check("(c) the 2.5a step writes only through put_accessory_config / put_exec_metric_config / the stage row; the aggregation calls the predicate; the loader and the writer hold the home", not v, v)

v = scan_surfaces(FE_FILES)
check("(d) the surfaces: stage2 2.5a + /line-class, the Exec MTD classified banner with its ScreenLink, no retired editor row", not v, v)
wf = read(WORKFLOW) if os.path.exists(WORKFLOW) else ""
check("(d) the CI job runs this lock and the line-class proof beside the other locks",
      "harness_line_class_lock.py" in wf and "harness_line_class.py" in wf and "backend/app/modules/commcalc/line_class.py" in wf)

# ── (e) negative controls ────────────────────────────────────────────────────────────────────────
syn = dict(BE)
syn["modules/commcalc/sneaky.py"] = "def f(ct):\n    cl = ct.lower()\n    if 'byod' in cl:\n        return 'byod'\n"
v, _ = scan_one_home(syn, EXCUSED)
check("(e) a new contract-type token check in another module → RED", any(r == "modules/commcalc/sneaky.py" for r, _ in v))
syn = dict(BE)
syn["modules/commcalc/copy.py"] = "PREMIUM_ACT = {'Activation'}\n"
v, _ = scan_one_home(syn, EXCUSED)
check("(e) a second copy of the retired token set → RED", any("PREMIUM_ACT" in d for _, d in v))
syn = dict(BE)
syn["modules/commcalc/whatif.py"] = BE["modules/commcalc/whatif.py"].replace("classify_line(r, _rules)", "classify_contract_type(r.get('contract_type'))")
v = scan_callers(syn, CALLERS)
check("(e) a caller reverting to the bare alias → RED", any(r == "modules/commcalc/whatif.py" for r, _ in v))
v, stale = scan_one_home({k: x for k, x in BE.items() if k != "modules/asset/router.py"}, EXCUSED)
check("(e) a stale excuse (the excused file no longer carries its check) → RED", "modules/asset/router.py" in stale)
rt = BE["modules/commcalc/router.py"]
body = fn_body(rt, "onboarding_intake_put_line_class")
bad = rt.replace(body, body.replace("put_accessory_config(PutAccessoryConfigIn(activation_details_rules=merged), org_id, authorization)",
                                    "client.schema('commcalc').table('accessory_config').upsert({'x': 1}).execute()"))
v = scan_step_writes(bad)
check("(e) the 2.5a step upserting on its own → RED", any("writes on its own" in d or "no longer writes through" in d for _, d in v))
syn = dict(BE)
syn["modules/commcalc/other.py"] = "x = row.get('activation_details_rules')\n"
check("(e) a second reader / writer of the rules column → RED", scan_rules_home(syn))
syn = dict(FE_FILES)
syn[MTD_PAGE] = FE_FILES.get(MTD_PAGE, "").replace("classified", "klassified")
check("(e) the Exec MTD page dropping the classified banner → RED", scan_surfaces(syn))

print("\n%d passed, %d failed" % (P, F))
if F:
    sys.exit(1)
print("OK — one activation-type predicate, one rules home, every caller dereferences it.")
