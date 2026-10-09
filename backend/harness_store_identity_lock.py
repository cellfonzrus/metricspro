"""LOCK: a leading address token is not a store identity — and it cannot become one again.

Owner directive 2026-10-09 ("chase trhew street number matching") and the standing design rule
*"if one thing is fixed for one tenant it should be a design fix not a temporary fix"*.

THE CLASS. Every money source spells a store in its own hand. For years each report answered
"which store is this?" with the FIRST SPACE-SEPARATED TOKEN of the address — in Python, and in one
SQL function. That token is not an identity: it dropped a store's entire carrier income when the
carrier wrote `116-36 …` and the roster wrote `11636 …`, it dropped a relocated store's money when
the feed stopped using the aliased spelling, and `{token: row}` being LAST-WINS silently decided
which of two store rows (one with the carrier's door, one with NULL) a store's residual joined on.

THE DESIGN FIX. ONE home — `app/modules/account/store_identity.py` (pure) behind
`account.coa.store_resolver` (I/O) — and every caller dereferences it. This guard is what stops the
patchwork returning: it FAILS THE BUILD when

  1. a NEW leading-token site appears anywhere in the backend (every existing one is pinned below
     with a reviewed classification),
  2. a caller that must dereference the home stops doing so,
  3. a SECOND copy of the resolution chain appears,
  4. a migration teaches SQL to decide store identity again.

Classifications (the pinned inventory):
  HOME            — the one home's own weakest step, or a thin delegate to it. The rule lives here.
  NOT-AN-ADDRESS  — the first token of a DATE, a person's name or a carrier label. Different fact.
  MARKET-FALLBACK — a store→MARKET lookup, owned by §13a's canonical resolver and policed by
                    `harness_market_resolution_guard.py`. Not money attribution; excused HERE so
                    the two guards do not fight, and a NEW one still fails that guard.
  DIAGNOSTIC      — computes the token in order to SHOW it (a store-matching trace/report). It
                    explains a match, it does not make one.
  FLAG-ONLY       — used to REFUSE or flag a mismatch, never to join money onto a store.
  REPORTED-DEFECT — a money/pay attribution still keyed on a token, MEASURED and reported, left
                    for the owner's word because changing it changes computed pay. Pinned so it
                    cannot be forgotten, and so it cannot grow.

To add a site: dereference the home (preferred — then pin it HOME/DIAGNOSTIC), or justify one of
the classifications IN THE CODE COMMENT and pin it here in the same PR.

No DB, no network, no app imports.  Run:  cd backend && python3 harness_store_identity_lock.py
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.join(_HERE, "app")
_MIGRATIONS = os.path.normpath(os.path.join(_HERE, "..", "database", "migrations"))

# A "leading-token site" = taking the first space-separated token of a string, or defining a helper
# whose whole job is that. Deliberately broad: a site that is NOT about an address must still be
# pinned, because a reviewer cannot tell from the regex and the next edit might make it one.
_TOKEN_RE = re.compile(
    r"""split\(\s*['"] ['"]\s*\)\s*\[\s*0\s*\]"""      # .split(' ')[0] / .split(" ")[0]
    # EVERY spelling of "first whitespace-separated token", not just the one the defect happened to
    # use. `.split()[0]` is the same fact in different clothes, and a future address site written
    # that way would have walked straight past this guard (2026-10-09, merge review).
    r"""|split\(\s*\)\s*\[\s*0\s*\]"""                  # .split()[0]
    r"""|split\(\s*['"] ['"]\s*,\s*1\s*\)\s*\[\s*0\s*\]"""   # .split(' ', 1)[0]
    r"""|partition\(\s*['"] ['"]\s*\)\s*\[\s*0\s*\]"""         # .partition(' ')[0]
    r"""|def\s+_?street_num\b|def\s+street_number\b|def\s+_?lead_num(?:ber|_key)?\b""")

# {relative path: {enclosing def: classification}}
PINNED = {
    "modules/account/store_identity.py": {
        "lead_num_key": "HOME",          # the chain's own weakest step, unambiguous-only
    },
    "modules/account/coa.py": {
        "_lead_num_key": "HOME",         # delegates to store_identity.lead_num_key
    },
    "core/identity.py": {
        "_lead_number": "HOME",          # ingest_store_guard's last-resort matcher
    },
    "modules/commcalc/tax_collected.py": {
        "_lead_num": "FLAG-ONLY",        # flags a rename that changed the number; never a merge
    },
    "modules/storeops/google_reviews.py": {
        "street_number": "FLAG-ONLY",    # wrong_street_number REFUSES a drifted Google match
    },
    "modules/commcalc/commission_engine.py": {
        "_store_trace": "DIAGNOSTIC",    # /store-resolution trace: SHOWS first_token + its hit
        "preview": "MARKET-FALLBACK",
    },
    "modules/commcalc/router.py": {
        "commission_plan_assignment_audit": "DIAGNOSTIC",   # mirrors _store_trace on purpose
        "_norm_report_date": "NOT-AN-ADDRESS",              # drops the time part of a timestamp
        "_period_ym": "NOT-AN-ADDRESS",                     # "October 2026" -> the month word
        "_period_bounds": "NOT-AN-ADDRESS",                 # same, for the month's date range
        "_mi_resolve_numbers": "NOT-AN-ADDRESS",            # first word of a SOURCE label
    },
    # Person-NAME matching, not a store. Same shape, different fact — pinned so the widened regex
    # has a reviewed classification for each and a NEW address site cannot hide among them.
    "modules/hr/letters.py": {"_common_merge": "NOT-AN-ADDRESS"},        # employee first name
    "modules/closing/router.py": {"_name_match": "NOT-AN-ADDRESS"},
    "modules/closing/ops_chargebacks.py": {"_name_match": "NOT-AN-ADDRESS"},
    "modules/closing/closer_resolution.py": {"name_match": "NOT-AN-ADDRESS"},
    "modules/commcalc/pay_simulator.py": {
        "resolve_self": "MARKET-FALLBACK", "_rep_context": "MARKET-FALLBACK",
    },
    "modules/commcalc/commission_drilldown.py": {"_no_plan_narration": "MARKET-FALLBACK"},
    "modules/commcalc/plan_impact.py": {"_rep_context": "MARKET-FALLBACK"},
    "modules/commcalc/sale_installment_engine.py": {"compute_sale_installments": "MARKET-FALLBACK"},
    "modules/commcalc/payout_accrual.py": {
        # POS string -> store_code for the accrual ledger's own grouping. Token fallback only after
        # an exact map hit; measured 2026-10-09 to place every live house spelling. Next in line to
        # dereference the home (it needs the resolver threaded through `assemble`).
        "resolve_store_code": "REPORTED-DEFECT",
    },
    "modules/commcalc/calculator.py": {
        # The DLAR (carrier KPI export) store join that scores a rep's KPIs, i.e. it decides PAY.
        # Measured 2026-10-09: it inherits exactly this defect for any store whose DLAR address and
        # roster address lead with different tokens. NOT changed in the §63 PR because moving it
        # moves computed rep payouts, which needs the owner's word (CLAUDE.md: money-touching
        # changes are surfaced for approval).
        "calc_rep_commissions": "REPORTED-DEFECT",
    },
}

# Callers that MUST dereference the one home. {relative path: (needle, why)}
MUST_DEREFERENCE = {
    "modules/commcalc/gp_report.py": (
        "_sid.store_key", "the GP report keys every money source by store identity"),
    "modules/commcalc/flags.py": (
        "_sid.store_key", "flags 7/8 compare the sales side and the payment side of one store"),
    "modules/commcalc/router.py": (
        "_store_identity.store_key", "the commission-leg trend/breakout keys carrier rows"),
    "modules/account/residual_subs.py": (
        "_sid.store_key", "the residual report joins rep pay to a store"),
    "modules/closing/unfinished_day.py": (
        "_sid.store_key", "a closing store-day is matched on the resolver, not the raw code"),
    "modules/account/coa.py": (
        "_sid.build_store_resolver", "store_resolver is the I/O wrapper around the one home"),
    "modules/account/ma_store_pnl.py": (
        "store_resolver", "the mig-314 account->store index collapses spellings through it"),
}

PASS = FAIL = 0


def ok(cond, msg, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ {msg}" + (f"\n      {detail}" if detail else ""))


def enclosing_def(src, pos):
    """The name of the def the match belongs to. A match ON a `def` line is that def itself."""
    line_start = src.rfind("\n", 0, pos) + 1
    own = re.match(r"\s*(?:async\s+)?def\s+(\w+)", src[line_start:src.find("\n", pos)])
    if own:
        return own.group(1)
    best = "<module>"
    for m in re.finditer(r"^(?:async\s+)?def\s+(\w+)", src[:pos], re.M):
        best = m.group(1)
    return best


def main():
    print("=" * 78)
    print("A. No unpinned leading-token site anywhere in the backend")
    print("=" * 78)
    seen = {}
    for root, _dirs, names in os.walk(_APP):
        for n in sorted(names):
            if not n.endswith(".py"):
                continue
            path = os.path.join(root, n)
            rel = os.path.relpath(path, _APP).replace(os.sep, "/")
            src = open(path, encoding="utf-8").read()
            for m in _TOKEN_RE.finditer(src):
                fn = enclosing_def(src, m.start())
                line = src.count("\n", 0, m.start()) + 1
                seen.setdefault(rel, {}).setdefault(fn, []).append(line)
                cls = (PINNED.get(rel) or {}).get(fn)
                ok(bool(cls),
                   f"{rel}:{line} `{fn}` takes a leading token and is NOT pinned",
                   "Dereference account.store_identity (store_key / build_store_resolver), or pin "
                   "it in harness_store_identity_lock.py with a reviewed classification.")
    # a pin that no longer matches anything is a stale pin — it must not keep a future site green
    for rel, fns in PINNED.items():
        for fn in fns:
            ok(fn in (seen.get(rel) or {}),
               f"STALE PIN {rel} `{fn}` matches nothing — remove it so it cannot cover a new site")
    print(f"  … {sum(len(v) for fns in seen.values() for v in fns.values())} token sites, "
          f"all pinned")

    print()
    print("=" * 78)
    print("B. The money paths dereference the one home")
    print("=" * 78)
    for rel, (needle, why) in MUST_DEREFERENCE.items():
        path = os.path.join(_APP, rel)
        src = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
        ok(needle in src, f"{rel} no longer dereferences the store-identity home ({why})")
    gp = open(os.path.join(_APP, "modules/commcalc/gp_report.py"), encoding="utf-8").read()
    ok("def street_num" not in gp,
       "gp_report.py defines street_num again — the join this whole guard exists to prevent")
    ok("store_by_num" not in gp,
       "gp_report.py has a token-keyed store map again (`store_by_num`)")
    flg = open(os.path.join(_APP, "modules/commcalc/flags.py"), encoding="utf-8").read()
    ok("def street_num" not in flg, "flags.py defines street_num again")

    print()
    print("=" * 78)
    print("C. The GP resolver is UNGATED — it was `if ma_income:` and that was the bug")
    print("=" * 78)
    rt = open(os.path.join(_APP, "modules/commcalc/router.py"), encoding="utf-8").read()
    i = rt.find("_resolve_canonical = _coa_gp.store_resolver(")
    ok(i > 0, "router.py no longer builds the GP store resolver at all")
    if i > 0:
        before = rt[max(0, i - 600):i]
        ok("if ma_income" not in before.split("# ")[-1] and "if ma_income:" not in before[-400:],
           "the GP store resolver is gated again (only ePay-LESS orgs would get it, which is "
           "exactly how the house org kept the leading-token join)")
    ok(rt.count("_leg_store_index(client, org_id)") >= 2
       and "store_identity_index(rows, _leg_resolve)" in rt,
       "the commission-leg store index is no longer keyed by canonical identity")

    print()
    print("=" * 78)
    print("D. One chain, one copy")
    print("=" * 78)
    for root, _dirs, names in os.walk(_APP):
        for n in sorted(names):
            if not n.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(root, n), _APP).replace(os.sep, "/")
            if rel == "modules/account/store_identity.py":
                continue
            src = open(os.path.join(root, n), encoding="utf-8").read()
            ok(not ("addr_by_num" in src and "alias_addr" in src),
               f"{rel} builds a SECOND copy of the store-resolution chain "
               "(addr_by_num + alias_addr) — read the home instead")

    print()
    print("=" * 78)
    print("E. SQL does not decide store identity")
    print("=" * 78)
    def _sql_code(path):
        """The SQL with its `--` comments stripped — a comment that MENTIONS split_part (this
        guard's own revert note does) is prose, not a tokenizing join."""
        src = open(path, encoding="utf-8").read()
        return "\n".join(re.sub(r"--.*$", "", ln) for ln in src.splitlines())

    sql_hits = []
    for n in sorted(os.listdir(_MIGRATIONS)):
        if not n.endswith(".sql"):
            continue
        code = _sql_code(os.path.join(_MIGRATIONS, n))
        for m in re.finditer(r"split_part\(([^;]{0,200}?)\)\s*,", code):
            frag = m.group(1)
            if "business_address" in frag or "store_address" in frag:
                sql_hits.append(n)
    # mig 274 is history and stays on disk; its function must be SUPERSEDED by a later definition
    # that carries the raw address, and no OTHER migration may tokenize an address.
    ok(sorted(set(sql_hits)) == ["274_commission_leg_split.sql"],
       f"a migration tokenizes a store address: {sorted(set(sql_hits))}")
    _def_re = re.compile(r"FUNCTION\s+commcalc\.commission_leg_label_rollup", re.I)
    superseding = [n for n in sorted(os.listdir(_MIGRATIONS))
                   if n.endswith(".sql") and not n.startswith("274_")
                   and _def_re.search(_sql_code(os.path.join(_MIGRATIONS, n)))]
    ok(bool(superseding),
       "nothing supersedes mig 274's token-returning commission_leg_label_rollup")
    for n in superseding:
        code = _sql_code(os.path.join(_MIGRATIONS, n))
        ok("btrim(coalesce(pd.business_address" in code and "split_part" not in code,
           f"{n} redefines the rollup but still tokenizes the address")

    print()
    print("=" * 78)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    print("=" * 78)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
