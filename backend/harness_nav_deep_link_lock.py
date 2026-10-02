"""LOCK: a menu entry or a link that opens a TAB names a tab its page really has — and the place pay is SET is
reachable by name (owner 2026-10-02, index §19.40).

OWNER, 2026-10-02: make "Employees & Pay" its own menu item, and fix the stale wording that points people to the
wrong place to edit pay. He looked for "Employees & Pay" and could not find it: it was only a TAB of /hr, held in
`useState`, so nothing could link to it — the menu reached /hr only through the "HR · Total Comp" tile, which
opens the Total Comp tab. Meanwhile /hr's own intro said "Edit pay on StoreOps Admin" (stale since #325 — pay is
set per row on HR → Employees & Pay, §19.35), StoreOps Admin carried a tab ALSO labelled "Employees & Pay" that
edits no pay, the "no pay rate set" notice sent people to "HR → People" (the add-a-person form) via a link to /hr's
default tab, and the Payroll hub described the Employee Database — which has no pay column — as "profile, pay, …".

THE CLASS, NOT THE INSTANCE. "A link that opens a tab" had no mechanism: four pages each re-implemented "read
?tab= from window.location once, on mount" (which also ignores a link followed while the page is already open —
the new menu entry would have looked dead from /hr itself), and nothing checked that a `?tab=` link names a tab
its page declares. A stale or misspelled key renders the DEFAULT tab: it compiles, it renders, it is wrong, and
only a reader can see it. So:

  • ONE reader: `frontend/src/lib/useUrlTab.ts` (hook) over `lib/urlTab.ts` (pure). The tab keys are a literal
    `const X = [...] as const` the page passes to it, so this file can read them.
  • a deep-link NAV entry (`/hr?tab=employees`) is a door into its page, never a second gate: `rbac.canSeeItem`
    delegates to the page's entry, every gate keys on `rbac.navPath`, the Roles screen lists no switch for it.
    The behaviour is proved by frontend/prove_nav_deep_link.mjs (the real rbac.ts under Node); this file pins
    the wiring so it cannot quietly un-wire.

WHAT FAILS THE BUILD
  A. a page reads `?tab=` any way other than `useUrlTab`; a `useUrlTab` page has no <Suspense> boundary; its keys
     are not a literal array; its default is not one of its keys.
  B. ANY `?tab=` deep link — in NAV, a ScreenLink, a hub tile, JSX, a backend notice, a help doc — names a route
     with no page, a page that does not read `?tab=`, or a key the page does not declare.
  C. the "Employees & Pay" menu entry is missing, hidden behind a tile, or points anywhere but the HR tab; a
     deep-link entry has no page entry, or does not repeat its page's module + scopes verbatim; the Payroll hub
     or ScreenLink stop naming it.
  D. the one-gate wiring un-wires: canSeeItem / navBlockReason stop delegating; ScreenLink or the carrier /
     vertical gates keep their own path-stripping copy; the Roles screen offers a switch for a door.
  E. a breadcrumb that names a registered screen ("X → Y", ScreenLink `SCREENS` aliases — §23o) appears in
     page copy un-linked, beyond the pre-existing sites frozen below (a ratchet: it may only shrink, and a frozen
     site that has been fixed must be removed); a second surface is labelled "Employees & Pay"; the stale
     where-to-edit-pay sentences come back.

Every rule carries a NEGATIVE control (the defect patched back in → RED) and a positive one.
PURE / DB-FREE: reads the real sources as text (and the backend through `ast`); stdlib only.
"""
import ast
import json
import os
import re
import sys
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FRONT = os.path.join(ROOT, "frontend", "src")
APP = os.path.join(FRONT, "app")
BACK_APP = os.path.join(HERE, "app")
RBAC = os.path.join(FRONT, "lib", "rbac.ts")
SCREEN_LINK = os.path.join(FRONT, "components", "ScreenLink.tsx")
URL_TAB_HOOK = os.path.join(FRONT, "lib", "useUrlTab.ts")
URL_TAB_PURE = os.path.join(FRONT, "lib", "urlTab.ts")
ROLES_PAGE = os.path.join(APP, "(platform)", "admin", "roles", "page.tsx")
PAYROLL_HUB = os.path.join(APP, "(platform)", "payroll", "page.tsx")
HR_PAGE = os.path.join(APP, "(platform)", "hr", "page.tsx")
HELP_SEED = os.path.join(HERE, "app", "data", "support_docs_seed.json")
STOREOPS_ATTENTION = os.path.join(HERE, "app", "modules", "storeops", "attention.py")

EMPLOYEES_PAY_HREF = "/hr?tab=employees"

P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, detail))


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def is_comment(line):
    s = line.strip()
    return s.startswith(("//", "/*", "*", "{/*"))


BLOCK_COMMENT = re.compile(r"^[ \t]*\{?/\*.*?\*/\}?", re.S | re.M)


def strip_block_comments(text):
    """Blank out `/* … */` and JSX `{/* … */}` blocks that start a line (keeping line numbers): their
    continuation lines carry no comment marker, so a line-wise test alone would read them as copy."""
    return BLOCK_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def frontend_files():
    for root, _, files in os.walk(FRONT):
        for f in files:
            if f.endswith((".ts", ".tsx")):
                yield os.path.join(root, f)


# ── routes: app/**/page.tsx → URL path (route groups "(x)" are transparent) ────────────────────────────────────
def build_routes():
    out = {}
    for root, _, files in os.walk(APP):
        if "page.tsx" not in files:
            continue
        segs = [s for s in os.path.relpath(root, APP).split(os.sep) if s != "." and not (s.startswith("(") and s.endswith(")"))]
        out["/" + "/".join(segs) if segs else "/"] = os.path.join(root, "page.tsx")
    return out


def route_page(routes, path):
    if path in routes:
        return routes[path]
    want = [s for s in path.strip("/").split("/") if s]
    for route, page in routes.items():
        have = [s for s in route.strip("/").split("/") if s]
        if len(have) != len(want):
            continue
        if all(h == w or (h.startswith("[") and h.endswith("]")) for h, w in zip(have, want)):
            return page
    return None


# ── a page's declared tabs: useUrlTab(NAME, 'default') + const NAME = ['a', 'b'] as const ───────────────────────
USE_RE = re.compile(r"useUrlTab\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*'([^']+)'\s*\)")


def declared_tabs(src):
    """→ (keys, default) or None when the page does not read ?tab= through useUrlTab. Raises ValueError when the
    call is there but its keys are not a literal array (the lock cannot verify a link against it)."""
    m = USE_RE.search(src)
    if not m:
        return None
    name, default = m.group(1), m.group(2)
    d = re.search(r"const\s+%s\s*(?::[^=]+)?=\s*\[([^\]]*)\]\s*as\s+const" % re.escape(name), src)
    if not d:
        raise ValueError("useUrlTab(%s, …) — %s is not a literal `[…] as const` array in the same file" % (name, name))
    return re.findall(r"'([^']+)'", d.group(1)), default


# ── every ?tab= deep link in the product ──────────────────────────────────────────────────────────────────────
TS_HREF = re.compile(r"""(['"`])(/[A-Za-z0-9_\-/\[\].]*\?[^'"`\s]+)\1""")
SEED_HREF = re.compile(r"`(/[A-Za-z0-9_\-/.]*\?[^`\s]+)`")


def tab_of(href):
    q = parse_qs(urlsplit(href).query)
    v = q.get("tab")
    return v[0] if v else None


def collect_frontend_links(text, where):
    out = []
    text = strip_block_comments(text)
    for i, line in enumerate(text.splitlines(), 1):
        if is_comment(line):
            continue
        for m in TS_HREF.finditer(line):
            href = m.group(2)
            if "${" in href or tab_of(href) is None:
                continue
            out.append((href, "%s:%d" % (where, i)))
    return out


def collect_python_links(src, where):
    out = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if v.startswith("/") and " " not in v and "\n" not in v and tab_of(v) is not None:
                out.append((v, "%s:%d" % (where, node.lineno)))
    return out


def all_deep_links():
    links = []
    for p in frontend_files():
        links += collect_frontend_links(read(p), rel(p))
    for root, _, files in os.walk(BACK_APP):
        for f in files:
            if f.endswith(".py"):
                p = os.path.join(root, f)
                links += collect_python_links(read(p), rel(p))
    for m in SEED_HREF.finditer(read(HELP_SEED)):
        if tab_of(m.group(1)) is not None:
            links.append((m.group(1), rel(HELP_SEED)))
    return links


def link_problem(routes, href):
    """None when `href`'s ?tab= names a tab its page declares, else the reason."""
    path, tab = urlsplit(href).path, tab_of(href)
    page = route_page(routes, path)
    if not page:
        return "no page serves %s" % path
    try:
        decl = declared_tabs(read(page))
    except ValueError as e:
        return str(e)
    if decl is None:
        return "%s does not read ?tab= (no useUrlTab) — the link lands on its default view" % rel(page)
    if tab not in decl[0]:
        return "%s declares %s; '%s' is not one of them — the link silently lands on '%s'" % (
            rel(page), decl[0], tab, decl[1])
    return None


# ── NAV items, parsed from the real rbac.ts literal ───────────────────────────────────────────────────────────
NAV_ITEM_RE = re.compile(r"\{ href: '([^']+)', label: '([^']+)', icon: '[^']*', module: '([^']+)'(.*?)\}")


def nav_items(src):
    body = src.split("export const NAV: NavGroup[] = [", 1)[1]
    items = []
    for m in NAV_ITEM_RE.finditer(body):
        rest = m.group(4)
        sc = re.search(r"scopes: \[([^\]]*)\]", rest)
        items.append({
            "href": m.group(1), "label": m.group(2), "module": m.group(3),
            "scopes": tuple(re.findall(r"'([^']+)'", sc.group(1))) if sc else None,
            "tileOnly": "tileOnly: true" in rest, "platformOnly": "platformOnly: true" in rest,
        })
    return items


def deep_link_problems(items):
    """Every deep-link NAV entry must open a page that has its own entry, and repeat its module + scopes."""
    probs = []
    by_href = {}
    for it in items:
        by_href.setdefault(it["href"], it)
    for it in items:
        path = it["href"].split("#")[0].split("?")[0]
        if path == it["href"]:
            continue
        page = by_href.get(path)
        if not page:
            probs.append("%s opens %s, which has no NAV entry of its own" % (it["href"], path))
        elif (page["module"], page["scopes"]) != (it["module"], it["scopes"]):
            probs.append("%s declares module=%s scopes=%s but its page %s is module=%s scopes=%s" % (
                it["href"], it["module"], it["scopes"], path, page["module"], page["scopes"]))
    return probs


def employees_pay_entry(items):
    hits = [it for it in items if it["label"] == "Employees & Pay"]
    return hits[0] if len(hits) == 1 else None


# ── E. breadcrumbs that name a registered screen must be links (§23o) ─────────────────────────────────────────
def screen_breadcrumbs(src):
    body = src.split("export const SCREENS", 1)[1]
    out = set()
    for al in re.findall(r"aliases: \[(.*?)\]", body, re.S):
        for a in re.findall(r"'((?:[^'\\]|\\.)+)'", al):
            if "→" in a:
                out.add(a.replace("\\'", "'"))
    return out


OPEN_TAG = re.compile(r"<(ScreenLink|Link|a)\b[^<>]*>\s*$")
CLOSE_TAG = re.compile(r"^\s*</(ScreenLink|Link|a)>")


def unlinked_breadcrumbs(text, crumbs):
    """[(alias, line_no)] — a registered breadcrumb in copy that is not inside a link. A tooltip (inside a
    `title=` attribute) cannot carry a link and is not counted; a string handed to <LinkedText> on the same line
    is linked by the §23o mechanism."""
    text = strip_block_comments(text)
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if is_comment(line) or "<LinkedText" in line:
            continue
        norm = line.replace("&amp;", "&")
        for a in crumbs:
            start = 0
            while True:
                k = norm.find(a, start)
                if k < 0:
                    break
                start = k + len(a)
                before, after = norm[:k], norm[k + len(a):]
                if OPEN_TAG.search(before) and CLOSE_TAG.search(after):
                    continue
                if before.rfind("title=") > before.rfind(">"):
                    continue
                out.append((a, i))
    return out


# Pre-existing un-linked breadcrumbs (2026-10-02), each in another module's copy. FROZEN: this list may only
# shrink — a new un-linked breadcrumb fails the build, and an entry here whose site has been linked must be
# deleted (a stale entry fails too). (file, breadcrumb) → occurrences.
UNLINKED_BASELINE = {
    ("frontend/src/app/(platform)/commcalc/exec/mtd/page.tsx", "Sales Report → ⚙ Classification settings"): 1,
    ("frontend/src/app/(platform)/commcalc/asset/page.tsx", "Settings → Stores"): 1,
    ("frontend/src/app/(platform)/vision/page.tsx", "Vision → Settings"): 1,
    ("frontend/src/app/(platform)/vision/activity/page.tsx", "Vision → Settings"): 1,
    ("frontend/src/app/(platform)/vision/busy-hours/page.tsx", "Vision → Settings"): 1,
    ("frontend/src/app/(platform)/vision/heatmap/page.tsx", "Vision → Settings"): 1,
    ("frontend/src/app/(platform)/vision/behavior/page.tsx", "Vision → Settings"): 1,
    ("frontend/src/app/(platform)/onboarding/intake/stage2.tsx", "Closing → Tender Config"): 1,
    ("frontend/src/lib/pos-config.ts", "POS Settings → Sales Tax"): 1,
}
# Linked at render, not on the line: the string is a constant printed through <LinkedText> (§23o). Verified below.
LINKED_AT_RENDER = {
    ("frontend/src/app/(platform)/commcalc/_lib/uploadGuard.tsx", "Closing → Tender Config"):
        "XREPORT_ZERO_FIX is rendered through <LinkedText> in the same file (§23o)",
}


def breadcrumb_census(crumbs):
    found = {}
    for p in frontend_files():
        if p == SCREEN_LINK:
            continue
        for a, _ in unlinked_breadcrumbs(read(p), crumbs):
            key = (rel(p), a)
            found[key] = found.get(key, 0) + 1
    return found


def ratchet_problems(found, baseline, exempt):
    probs = []
    for key, n in sorted(found.items()):
        if key in exempt:
            continue
        if n > baseline.get(key, 0):
            probs.append("NEW un-linked breadcrumb %r in %s (%d > frozen %d) — wrap it in <ScreenLink>" % (
                key[1], key[0], n, baseline.get(key, 0)))
    for key, n in sorted(baseline.items()):
        if found.get(key, 0) < n:
            probs.append("frozen entry %r in %s is fixed — delete it from UNLINKED_BASELINE" % (key[1], key[0]))
    return probs


# Sentences that sent people to the wrong place to set pay. None may come back, anywhere a reader sees it.
STALE_PAY_COPY = [
    "Edit pay on StoreOps Admin",
    "Editing pay stays on\n// StoreOps Admin",
    "Pay rates are managed in the HR module",
    "Manage employees, pay rates, and stores",
    "Set it at HR → People",
    "profile, pay, documents, history",
    "**Pay rate is edited here** (moved",
    "StoreOps Employees or HR → Employees & Pay",
    "(StoreOps\\n  Employees or HR · Employees & Pay)",
]
# The ONE surface named "Employees & Pay" is the HR tab; these files name it (its menu entry, its tab, its hub
# tile, its ScreenLink). Anywhere else a label "Employees & Pay" is a second surface claiming the name.
EMPLOYEES_PAY_NAMERS = {
    "frontend/src/lib/rbac.ts", "frontend/src/app/(platform)/hr/page.tsx",
    "frontend/src/app/(platform)/payroll/page.tsx", "frontend/src/components/ScreenLink.tsx",
}
EP_LABEL = re.compile(r"""['"`>][^'"`<>]{0,4}Employees (?:&|&amp;) Pay['"`<]""")


def second_surfaces(files_text):
    out = []
    for path, text in files_text:
        if path in EMPLOYEES_PAY_NAMERS:
            continue
        for i, line in enumerate(strip_block_comments(text).splitlines(), 1):
            if is_comment(line) or "<ScreenLink" in line:
                continue
            if EP_LABEL.search(line):
                out.append("%s:%d" % (path, i))
    return out


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════
print("=" * 78)
print("NAV DEEP-LINK LOCK — a link to a tab names a tab its page has (owner 2026-10-02, §19.40)")
print("=" * 78)
ROUTES = build_routes()
RBAC_SRC = read(RBAC)
SL_SRC = read(SCREEN_LINK)
ITEMS = nav_items(RBAC_SRC)
print("routes: %d pages; NAV: %d entries" % (len(ROUTES), len(ITEMS)))

# ── A. ONE reader ─────────────────────────────────────────────────────────────────────────────────────────────
print()
print("A. one reader for ?tab=")
HOOK, PURE = read(URL_TAB_HOOK), read(URL_TAB_PURE)
check("A1 lib/useUrlTab.ts exports the hook and reads the LIVE url (useSearchParams)",
      "export function useUrlTab" in HOOK and "useSearchParams()" in HOOK)
HOOK_CODE = "\n".join(l for l in strip_block_comments(HOOK).splitlines() if not is_comment(l))
check("A2 …never a once-on-mount window.location read", "window.location.search" not in HOOK_CODE
      and "window.history.pushState" in HOOK_CODE)
check("A3 lib/urlTab.ts holds the pure meaning (parseTab, tabHref, TAB_PARAM) with no React/Next import",
      all(s in PURE for s in ("export function parseTab", "export function tabHref", "export const TAB_PARAM"))
      and "from 'react'" not in PURE and "next/" not in PURE)
OTHER_READER = re.compile(r"""\.get\(\s*(?:'tab'|"tab"|TAB_PARAM)\s*\)""")


def other_readers(files_text):
    return ["%s:%d" % (p, i) for p, t in files_text for i, l in enumerate(strip_block_comments(t).splitlines(), 1)
            if not is_comment(l) and OTHER_READER.search(l)]


FRONT_TEXT = [(rel(p), read(p)) for p in frontend_files()]
readers = other_readers([(p, t) for p, t in FRONT_TEXT if p not in (rel(URL_TAB_HOOK), rel(URL_TAB_PURE))])
check("A4 no page reads ?tab= any other way (one reader, not five copies)", not readers, readers)
OLD_NOTIFY = "    const t = new URLSearchParams(window.location.search).get('tab') as NotifyTab | null\n"
check("A5 control: the old on-mount reader is caught", other_readers([("x.tsx", OLD_NOTIFY)]) == ["x.tsx:1"])

callers = [(p, t) for p, t in FRONT_TEXT if USE_RE.search(t) and p != rel(URL_TAB_HOOK)]
check("A6 the five tabbed pages read through it (HR, Notify, Helpdesk Settings, Training, Payables)",
      {p for p, _ in callers} >= {
          "frontend/src/app/(platform)/hr/page.tsx", "frontend/src/app/(platform)/notify/page.tsx",
          "frontend/src/app/(platform)/helpdesk/settings/page.tsx", "frontend/src/app/(platform)/training/page.tsx",
          "frontend/src/app/(platform)/commcalc/payables/page.tsx"}, sorted(p for p, _ in callers))


def caller_problems(path, text):
    probs = []
    if "<Suspense" not in text or "Suspense" not in text.split("from 'react'")[0].rsplit("import", 1)[-1]:
        probs.append("%s calls useUrlTab without a <Suspense> boundary (useSearchParams needs one)" % path)
    try:
        keys, default = declared_tabs(text)
        if default not in keys:
            probs.append("%s: default '%s' is not one of its keys %s" % (path, default, keys))
        if len(set(keys)) != len(keys):
            probs.append("%s: duplicate tab keys %s" % (path, keys))
    except ValueError as e:
        probs.append("%s: %s" % (path, e))
    return probs


cp = [x for p, t in callers for x in caller_problems(p, t)]
check("A7 every caller has a Suspense boundary, literal keys, and a default among them", not cp, cp)
GOOD_CALLER = ("import { useState, Suspense } from 'react'\nconst T = ['a', 'b'] as const\n"
               "export default function P() { return <Suspense fallback={null}><B /></Suspense> }\n"
               "function B() { const [t] = useUrlTab(T, 'a') }\n")
check("A8 control: a well-formed caller passes", caller_problems("good.tsx", GOOD_CALLER) == [])
check("A9 control: no Suspense → RED",
      any("Suspense" in x for x in caller_problems("x.tsx", GOOD_CALLER.replace("<Suspense fallback={null}>", "<>")
                                                     .replace("</Suspense>", "</>").replace(", Suspense", ""))))
check("A10 control: a default outside the keys → RED",
      any("default" in x for x in caller_problems("x.tsx", GOOD_CALLER.replace("useUrlTab(T, 'a')", "useUrlTab(T, 'z')"))))
check("A11 control: keys that are not a literal array → RED",
      any("literal" in x for x in caller_problems("x.tsx", GOOD_CALLER.replace("const T = ['a', 'b'] as const",
                                                                                "const T = makeTabs()"))))

# ── B. every ?tab= link resolves ──────────────────────────────────────────────────────────────────────────────
print()
print("B. every ?tab= deep link names a tab its page declares")
LINKS = all_deep_links()
hrefs = sorted({h for h, _ in LINKS})
print("  found %d ?tab= link(s) at %d site(s): %s" % (len(hrefs), len(LINKS), ", ".join(hrefs)))
must_see = {EMPLOYEES_PAY_HREF, "/notify?tab=subs", "/notify?tab=log", "/helpdesk/settings?tab=settings",
            "/training?tab=flowcharts"}
check("B1 the census reaches NAV, ScreenLink, JSX, backend notices and help docs (not vacuous)",
      must_see <= set(hrefs), sorted(must_see - set(hrefs)))
sites = {w.split(":")[0] for h, w in LINKS if h == EMPLOYEES_PAY_HREF}
check("B2 /hr?tab=employees is linked from NAV, ScreenLink, the Payroll hub, the no-pay-rate notice and help",
      {rel(RBAC), rel(SCREEN_LINK), rel(PAYROLL_HUB), rel(STOREOPS_ATTENTION), rel(HELP_SEED)} <= sites, sorted(sites))
bad = ["%s (%s): %s" % (h, w, link_problem(ROUTES, h)) for h, w in LINKS if link_problem(ROUTES, h)]
check("B3 every one resolves to a declared tab", not bad, bad)
check("B4 control: /hr?tab=employees resolves", link_problem(ROUTES, EMPLOYEES_PAY_HREF) is None)
check("B5 control: a bogus key on a real tabbed page → RED",
      "not one of them" in (link_problem(ROUTES, "/hr?tab=bogus") or ""), link_problem(ROUTES, "/hr?tab=bogus"))
check("B6 control: a key on a page that has no tabs → RED",
      "does not read ?tab=" in (link_problem(ROUTES, "/storeops/payroll?tab=x") or ""),
      link_problem(ROUTES, "/storeops/payroll?tab=x"))
check("B7 control: a route with no page → RED", "no page" in (link_problem(ROUTES, "/no-such-page?tab=x") or ""))
check("B8 control: the census finds a link in JSX, in a TS literal and in Python",
      collect_frontend_links('<Link href="/hr?tab=bogus">x</Link>', "j") == [("/hr?tab=bogus", "j:1")]
      and collect_frontend_links("  { href: '/hr?tab=bogus' }", "t") == [("/hr?tab=bogus", "t:1")]
      and collect_python_links('X = ("a", "/hr?tab=bogus")', "p") == [("/hr?tab=bogus", "p:1")])
check("B9 control: a commented-out or prose mention is not a link",
      collect_frontend_links("// see /hr?tab=bogus", "c") == []
      and collect_python_links('"""Send them to /hr?tab=bogus to fix it."""', "d") == [])

# ── C. the Employees & Pay menu entry ─────────────────────────────────────────────────────────────────────────
print()
print("C. the \"Employees & Pay\" menu entry")
ep = employees_pay_entry(ITEMS)
check("C1 exactly one NAV entry is labelled \"Employees & Pay\"", ep is not None,
      [it["href"] for it in ITEMS if it["label"] == "Employees & Pay"])
check("C2 it opens the HR tab", ep and ep["href"] == EMPLOYEES_PAY_HREF, ep)
check("C3 it is a sidebar entry, not hidden behind a tile", ep and not ep["tileOnly"] and not ep["platformOnly"], ep)
hr_entry = next((it for it in ITEMS if it["href"] == "/hr"), None)
check("C4 it carries /hr's module + scopes verbatim (nobody gains access they did not have)",
      ep and hr_entry and (ep["module"], ep["scopes"]) == (hr_entry["module"], hr_entry["scopes"]), (ep, hr_entry))
dl = deep_link_problems(ITEMS)
check("C5 every deep-link NAV entry has a page entry and repeats its module + scopes", not dl, dl)
REMOVED = RBAC_SRC.replace("    { href: '/hr?tab=employees', label: 'Employees & Pay',", "    { href: '/x', label: 'X',")
check("C6 control: the entry removed → RED", employees_pay_entry(nav_items(REMOVED)) is None)
check("C7 control: the entry tile-only → RED",
      employees_pay_entry(nav_items(RBAC_SRC.replace(
          "label: 'Employees & Pay', icon: '👥', module: 'hr', scopes: ['all', 'market'] }",
          "label: 'Employees & Pay', icon: '👥', module: 'hr', scopes: ['all', 'market'], tileOnly: true }")))["tileOnly"])
WIDER = [dict(it) for it in ITEMS]
for it in WIDER:
    if it["href"] == EMPLOYEES_PAY_HREF:
        it["scopes"] = ("all", "market", "store")
check("C8 control: a door with WIDER scopes than its page → RED", bool(deep_link_problems(WIDER)))
check("C9 control: a door to a page with no entry → RED",
      bool(deep_link_problems(ITEMS + [{"href": "/nowhere?tab=a", "label": "N", "module": "hr", "scopes": None,
                                        "tileOnly": False, "platformOnly": False}])))
check("C10 the Payroll hub carries an Employees & Pay tile to the tab",
      "href: '/hr?tab=employees'" in read(PAYROLL_HUB) and "title: 'Employees & Pay'" in read(PAYROLL_HUB))
check("C11 ScreenLink registers the destination (so copy naming it links)",
      re.search(r"employees_pay: \{\s*href: '/hr\?tab=employees'", SL_SRC) is not None)
check("C12 the HR page declares the tab the entry opens",
      declared_tabs(read(HR_PAGE)) and "employees" in declared_tabs(read(HR_PAGE))[0])

# ── D. one gate ───────────────────────────────────────────────────────────────────────────────────────────────
print()
print("D. a door is not a gate — the wiring")


def fn_body(src, name):
    i = src.find("export function %s(" % name)
    if i < 0:
        return ""
    j = src.find("\nexport ", i + 10)
    return src[i:j if j > 0 else len(src)]


def delegates(src, name):
    b = fn_body(src, name)
    head = b.split("\n")[1:4]
    return any("isDeepLinkItem(item)" in l for l in head) and "deepLinkPage(item)" in b


check("D1 rbac.navPath is THE path-stripping home", "export function navPath(href: string)" in RBAC_SRC)
check("D2 canSeeItem delegates a door to its page FIRST", delegates(RBAC_SRC, "canSeeItem"))
check("D3 navBlockReason delegates likewise (the Roles UI explains the same gate)", delegates(RBAC_SRC, "navBlockReason"))
UNWIRED = RBAC_SRC.replace(
    "  if (isDeepLinkItem(item)) { const page = deepLinkPage(item); return !!page && canSeeItem(perms, page) }\n", "")
check("D4 control: canSeeItem without the delegation → RED", not delegates(UNWIRED, "canSeeItem"))
for fn in ("carrierOK", "carrierOKActive", "verticalOK"):
    check("D5 %s keys on navPath (a door gates as its page)" % fn, "navPath(" in fn_body(RBAC_SRC, fn))
check("D6 ScreenLink uses rbac.navPath — no local path-stripping copy",
      "navPath" in SL_SRC and "function gateHref" not in SL_SRC and ".split('#')[0].split('?')[0]" not in SL_SRC)
check("D7 control: a re-introduced local copy → RED",
      ".split('#')[0].split('?')[0]" in SL_SRC + "\nfunction gateHref(h) { return h.split('#')[0].split('?')[0] }")
ROLES = read(ROLES_PAGE)
check("D8 the Roles screen offers no per-function switch for a door", "g.items.filter(it => !isDeepLinkItem(it))" in ROLES)

# ── E. copy ───────────────────────────────────────────────────────────────────────────────────────────────────
print()
print("E. copy — a named screen is a link; one surface is called Employees & Pay; no stale pay pointers")
CRUMBS = screen_breadcrumbs(SL_SRC)
check("E1 the breadcrumb registry includes HR → Employees & Pay", "HR → Employees & Pay" in CRUMBS, sorted(CRUMBS))
FOUND = breadcrumb_census(CRUMBS)
for key, why in LINKED_AT_RENDER.items():
    txt = read(os.path.join(ROOT, key[0]))
    check("E2 linked-at-render exemption still true: %s" % key[0], "<LinkedText" in txt, why)
rp = ratchet_problems(FOUND, UNLINKED_BASELINE, LINKED_AT_RENDER)
check("E3 no NEW un-linked breadcrumb; frozen sites only shrink", not rp, rp)
check("E4 HR → Employees & Pay is linked everywhere (none frozen, none found)",
      not [k for k in FOUND if k[1] == "HR → Employees & Pay"], [k for k in FOUND if k[1] == "HR → Employees & Pay"])
OLD_TIMECLOCK = "        {' '}Set these per person on the HR → Employees &amp; Pay tab.\n"
check("E5 control: the old un-linked timeclock sentence → RED",
      unlinked_breadcrumbs(OLD_TIMECLOCK, CRUMBS) == [("HR → Employees & Pay", 1)])
check("E6 control: the same words inside <ScreenLink> pass",
      unlinked_breadcrumbs("on the <ScreenLink to=\"employees_pay\">HR → Employees &amp; Pay</ScreenLink> tab", CRUMBS) == [])
check("E7a control: a multi-line JSX comment is not copy",
      unlinked_breadcrumbs("{/* this tab edits no pay —\n   pay lives on HR → Employees & Pay. */}\n", CRUMBS) == [])
check("E7 control: a tooltip is not counted",
      unlinked_breadcrumbs('<span title="designable at Vision → Settings">x</span>', CRUMBS) == [])
check("E8 control: a new site → RED, a fixed frozen site → RED",
      ratchet_problems({("new.tsx", "Vision → Settings"): 1}, {}, {})
      and ratchet_problems({}, {("old.tsx", "Vision → Settings"): 1}, {}))
ss = second_surfaces(FRONT_TEXT)
check("E9 no second surface is labelled \"Employees & Pay\" (StoreOps Admin's tab edits no pay)", not ss, ss)
check("E10 control: StoreOps Admin's old tab label → RED",
      second_surfaces([("frontend/src/app/(platform)/storeops/admin/page.tsx",
                        "            {t === 'employees' ? '👥 Employees & Pay' : '🏪 Stores'}\n")]) != [])
READER_TEXT = FRONT_TEXT + [(rel(HELP_SEED), read(HELP_SEED))]
for root, _, files in os.walk(BACK_APP):
    for f in files:
        if f.endswith(".py"):
            READER_TEXT.append((rel(os.path.join(root, f)), read(os.path.join(root, f))))
stale = ["%s: %r" % (p, s) for s in STALE_PAY_COPY for p, t in READER_TEXT if s in t]
check("E11 none of the stale where-to-edit-pay sentences is back", not stale, stale)
check("E12 control: the old HR intro → RED",
      any(s in "  Salary, payroll and total compensation in one place — scoped to your area. Edit pay on StoreOps Admin."
          for s in STALE_PAY_COPY))
att = read(STOREOPS_ATTENTION)
check("E13 the no-pay-rate notice sends people to the tab where pay is set",
      '"/hr?tab=employees"' in att and "HR → Employees & Pay" in att)

print()
print("=" * 78)
print("%d passed, %d failed" % (P, F))
if F:
    print("FAIL — a ?tab= link, the Employees & Pay entry, the one-gate wiring or the pay copy has drifted (§19.40).")
    sys.exit(1)
print("OK — every ?tab= link names a declared tab; Employees & Pay is a menu entry that gates as /hr; copy points at it.")
