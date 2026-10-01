#!/usr/bin/env python3
"""The Roles UI's module checkboxes are DERIVED from NAV — one home, no second list (index §44).

OWNER REPORT 2026-10-01: "im trying to add finance module to market manager but not happening".
Two mechanisms were wrong, and this harness locks both.

(1) THE LIST WAS RETYPED. `/admin/roles` carried its own literal of 12 module keys while
    `frontend/src/lib/rbac.ts` NAV gates on 20. Eight modules covering 70 nav pages therefore had
    NO checkbox and were ungrantable from the Roles UI at all — closing (31 pages), crm (10),
    vision (9), referral (6), marketing (5), royalty (4), supply_ordering (4), franchise_ops (1).
    Four of the royalty pages live IN the Finance group, so "add Finance to this role" could not be
    completed from that screen no matter what the admin ticked.

(2) THE SECOND GATE WAS SILENT. A module tick is not sufficient for a report page: `canSeeItem`
    also requires the per-AREA `reports` grant, and `hasReport` judges a role carrying ANY explicit
    reports entry by that map alone. A market manager with `reports: {closing: true}` granted the
    `accounts` module therefore saw the Finance link open onto an EMPTY dashboard — 1 of 21 items —
    with nothing on screen naming the gate that closed. Same class as the 2026-08-03 "KPI Metrics
    is allowed for the DM role but doesn't show" report, whose fix covered only the SCOPE gate.

WHAT FAILS THE BUILD HERE:
  A. a module NAV gates on with no label in rbac.MODULE_LABELS (it would have no checkbox);
  B. a label naming a module NAV does not gate (a dead checkbox);
  C. the Roles page re-listing module keys instead of calling grantableModules();
  D. the Roles page not naming the missing report areas it derives (the silent second gate back);
  E. the report-area answer being DECLARED anywhere rather than derived through reportAreaForPath.

Stdlib only — reads the two source files as text and parses the literals it needs. No pip install,
no DB, no node.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RBAC = os.path.join(ROOT, "frontend", "src", "lib", "rbac.ts")
ROLES = os.path.join(ROOT, "frontend", "src", "app", "(platform)", "admin", "roles", "page.tsx")

_passed = 0
_failed = 0
_UNSET = object()


def check(name, got, want=_UNSET):
    global _passed, _failed
    ok = bool(got) if want is _UNSET else (got == want)
    if ok:
        _passed += 1
        print("  PASS  %s" % name)
    else:
        _failed += 1
        print("  ✗ %s" % name)
        if want is not _UNSET:
            print("        want: %r" % (want,))
        print("        got : %r" % (got,))
    return ok


def note(msg):
    print("  ·  %s" % msg)


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# ── Parsing the two literals we need out of rbac.ts ───────────────────────────────────────────────
def nav_modules(src):
    """Every `module: '<key>'` on a NAV ITEM, between `export const NAV` and its terminating `]`.

    Deliberately textual: the point of this lock is that the Roles UI and the sidebar read the SAME
    literal, so the check must look at that literal and not at a restatement of it.
    """
    i = src.index("export const NAV")
    # NAV is followed by the Assets/Reports groups and ends at the line `]` in column 0.
    m = re.search(r"\n\]\s*\n", src[i:])
    assert m, "could not find the end of the NAV literal"
    body = src[i:i + m.start()]
    return set(re.findall(r"\bmodule:\s*'([a-z_]+)'", body))


def module_labels(src):
    """Keys of `export const MODULE_LABELS`, in declaration order (that order is the render order)."""
    i = src.index("export const MODULE_LABELS")
    j = src.index("\n}", i)
    body = src[i:j]
    return [k for k in re.findall(r"^\s{2}([a-z_]+):\s*'", body, re.M)]


def group_modules(src):
    """`module:` on a NavGROUP (the representative tag), which is NOT a gate — excluded from A/B."""
    i = src.index("export const NAV")
    m = re.search(r"\n\]\s*\n", src[i:])
    body = src[i:i + m.start()]
    return set(re.findall(r"\{\s*group:\s*'[^']+',\s*module:\s*'([a-z_]+)'", body))


def main():
    rbac = read(RBAC)
    roles = read(ROLES)

    print("=" * 96)
    print("THE ROLES UI'S MODULE LIST IS DERIVED FROM NAV (index §44)")
    print("=" * 96)

    # ── A/B: the label map and NAV agree, in both directions ─────────────────────────────────────
    print("\nA. every module NAV gates on has a checkbox, and no checkbox gates nothing")
    nav = nav_modules(rbac)
    labels = module_labels(rbac)
    note("NAV gates on %d modules; MODULE_LABELS declares %d" % (len(nav), len(labels)))
    check("the NAV literal parsed (a plausible module count)", len(nav) >= 15)
    check("MODULE_LABELS parsed", len(labels) >= 15)

    unlabelled = sorted(nav - set(labels))
    check("no module NAV gates on is missing a label (it would have NO checkbox): %s"
          % (unlabelled or "none"), unlabelled, [])

    # A label for a GROUP tag only is still dead (group.module is documented as not a gate).
    dead = sorted(k for k in labels if k not in nav)
    check("no label names a module NAV does not gate (a dead checkbox): %s" % (dead or "none"),
          dead, [])

    # The eight that were ungrantable — named so a future edit cannot quietly drop them again.
    for key in ("closing", "crm", "marketing", "referral", "royalty", "supply_ordering",
                "franchise_ops", "vision"):
        check("the previously-ungrantable module %r is grantable" % key, key in labels)

    # ── C: the Roles page does not keep its own list ─────────────────────────────────────────────
    print("\nB. the Roles page DERIVES its checkboxes and re-lists nothing")
    check("roles page imports grantableModules", "grantableModules" in roles)
    check("MODULES is assigned from grantableModules()",
          re.search(r"const MODULES[^=]*=\s*grantableModules\(\)", roles) is not None)
    # The retyped literal is gone: no `{ key: '<module>', label:` pairs anywhere on the page.
    retyped = re.findall(r"\{\s*key:\s*'([a-z_]+)'\s*,\s*label:", roles)
    offenders = sorted(set(retyped) & nav)
    check("the page re-lists no NAV module key as a {key,label} pair: %s" % (offenders or "none"),
          offenders, [])

    # ── D: the second gate is named on screen ───────────────────────────────────────────────────
    print("\nC. the second gate (the report area) is NAMED, not left silent")
    check("roles page imports missingReportAreasForModule", "missingReportAreasForModule" in roles)
    check("...and calls it", "missingReportAreasForModule(" in roles)
    # The warning must name the Reports section the admin has to go tick.
    warn = re.search(r"stay hidden until you also tick", roles)
    check("the warning tells the admin what to tick", warn is not None)
    check("...and names the Reports section", "under <b>Reports</b>" in roles)

    # ── E: the report-area answer is DERIVED, never declared ────────────────────────────────────
    print("\nD. the report areas gating a module are DERIVED through reportAreaForPath")
    i = rbac.index("export function reportAreasForModule")
    j = rbac.index("\n}", i)
    body = rbac[i:j]
    check("reportAreasForModule walks NAV", "for (const g of NAV)" in body)
    check("...and asks reportAreaForPath, the function canSeeItem itself calls",
          "reportAreaForPath(it.href)" in body)
    # A hand-written area per module would be a second copy of REPORT_TREES.
    check("it declares no module→area table of its own",
          re.search(r"'(accounts|commissions|asset|vip|closing|storeops)'\s*:", body) is None)

    # The roles screen's own report checkbox was a HAND-COPY of hasReport's default rule (the same
    # second-copy class as the module list). It must dereference the shared non-bypass half instead.
    check("hasReport delegates its default rule to reportGrantedByConfig",
          re.search(r"export function hasReport[^}]*reportGrantedByConfig\(perms, area\)", rbac,
                    re.S) is not None)
    check("the roles page reads reportGrantedByConfig for its checkbox",
          re.search(r"function reportChecked[^}]*reportGrantedByConfig\(", roles, re.S) is not None)
    check("...and no longer re-implements the 'explicit map else scope==all' rule",
          re.search(r"function reportChecked[^}]*\(p\.scope \|\| 'all'\) === 'all'", roles, re.S)
          is None)

    k = rbac.index("export function missingReportAreasForModule")
    l = rbac.index("\n}", k)
    mbody = rbac[k:l]
    check("missingReportAreasForModule only fires for a GRANTED module",
          "moduleGranted(perms.modules, key)" in mbody)
    check("...and subtracts what hasReport already allows", "hasReport(perms, a)" in mbody)

    # ── F: armed negative controls — each rule goes RED when the fix is removed ──────────────────
    print("\nE. CONTROLS — each rule fails when the defect is patched back in")

    # The pre-fix Roles page: 12 keys retyped, 8 missing.
    old_ui = ["commissions", "targets", "asset", "vip", "accounts", "storeops", "pos", "hr",
              "notify", "helpdesk", "support", "admin"]
    missed = sorted(nav - set(old_ui))
    check("CONTROL: the pre-fix retyped list left 8 NAV modules ungrantable", len(missed), 8)
    check("CONTROL: ...and 'royalty' (4 pages IN the Finance group) was one of them",
          "royalty" in missed)

    fake_page = "const MODULES = [\n  { key: 'accounts', label: 'Accounts' },\n]\n"
    fake_retyped = sorted(set(re.findall(r"\{\s*key:\s*'([a-z_]+)'\s*,\s*label:", fake_page)) & nav)
    check("CONTROL: a page that re-lists a module key is caught", fake_retyped, ["accounts"])

    fake_labels = [k for k in labels if k != "accounts"]
    check("CONTROL: dropping a label leaves a NAV module with no checkbox → RED",
          sorted(nav - set(fake_labels)), ["accounts"])

    fake_labels2 = labels + ["not_a_module"]
    check("CONTROL: a label for a module NAV does not gate → RED",
          sorted(k for k in fake_labels2 if k not in nav), ["not_a_module"])

    # A realistic patchwork alternative: hand-write the module→area answer instead of deriving it.
    # That is the "second copy" the index rules forbid, and the rule above must catch it as written.
    declared = (
        "export function reportAreasForModule(key: string): string[] {\n"
        "  const T: Record<string, string[]> = {\n"
        "    'accounts': ['accounts'],\n"
        "    'commissions': ['commissions'],\n"
        "  }\n"
        "  return T[key] || []\n"
    )
    check("CONTROL: a hand-written module\u2192area table instead of the derivation \u2192 RED",
          re.search(r"'(accounts|commissions|asset|vip|closing|storeops)'\s*:", declared) is not None)
    check("CONTROL: ...and that same table never walks NAV", "for (const g of NAV)" not in declared)

    print("\n" + "=" * 96)
    print("RESULT: %d passed, %d failed" % (_passed, _failed))
    print("=" * 96)
    if _failed:
        print("FAIL  the Roles UI's module list has drifted from NAV, or the second gate went silent")
        return 1
    print("OK  one module list, derived from NAV; the report-area gate is named on screen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
