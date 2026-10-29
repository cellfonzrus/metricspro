#!/usr/bin/env python3
"""LOCK — the app leaves a page to the BROWSER in exactly one place, and it says why (index §CORE-02.10).

WHY THIS EXISTS. Next.js 16.4.3 ships a new lint rule,
`@next/next/no-location-assign-relative-destination`, which refuses
`window.location.href = '/some/page'` for an internal page and tells you to use `useRouter().push()`
instead. On the dependency bump of 2026-10-28 it found SEVEN such assignments across FIVE files, and
taking the rule's advice literally at all of them would have been a security regression.

THE CLASS, NOT THE INSTANCE (CLAUDE.md). The seven were not one kind of code wearing one hat. Two were
plain row-click navigation inside the app, where `router.push()` is simply better — a client
navigation instead of a full document load. The other FIVE sit on an IDENTITY BOUNDARY:

    startImpersonation   an admin begins viewing the app as an employee
    stopImpersonation    …and stops
    onImpersonationInvalid   the server ended that session out from under the tab
    operator `enter()`   a platform operator enters a tenant
    operator `leave()`   …and leaves

At each of those, the acting identity the next request will carry has just changed. `router.push()`
re-renders the tree but KEEPS every mounted component, every cached lookup and every piece of state
the previous identity put there — so an admin would walk into the employee's app still holding the
admin's rows, and an operator would leave a tenant with that tenant's numbers on screen. The
call sites already said so in prose ("Hard reload: … A soft refresh would leave components holding
the admin's data."). A full document load is not a lazy shortcut here; it is the thing that discards
the old identity's data, and the lint rule does not know that.

So the general fact that was wrong is not "this line uses the wrong navigation". It is **"the app had
no way to say *discard everything and come back as someone else*, so five call sites each spelled it
out by hand"** — which is why a rule aimed at the spelling could not tell the five from the two.

THE FIX IS A ONE HOME. `frontend/src/lib/navigate-away.ts` is now the only place in the frontend
that hands a destination to the browser. The five call sites dereference it; the two row clicks use
the router. The ONE suppression of the lint rule lives in that file, next to the reason, instead of
five suppressions next to five retellings of it.

WHAT THIS PROVES (DB-free, stdlib only — it reads frontend source as text):
  A. the one home exists, exports `reloadAs`, and really does a FULL-DOCUMENT navigation
     (a registry that quietly became a `router.push()` would defeat the whole point)
  B. it needs NO linter suppression at all — the destination is a parameter, so the rule has
     nothing to read; the frontend's pinned directive count does not grow to buy this fix
  C. NOTHING ELSE under frontend/src hands an internal path to `location` — the derived set of
     offenders is compared with a DECLARED set (index §CORE-10: a lower bound would let a site
     leave the scan and take its assertion with it)
  D. the five identity-boundary call sites each dereference the one home — declared BOTH ways, so a
     call site that stops importing it names itself
  E. the two row clicks navigate with the router, and did not get "fixed" by being pointed at the
     one home (a plain link is not an identity change)
  N. negative controls — each check really does fail on the shape it is meant to catch

Run: python3 backend/harness_navigate_away_one_home.py
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "frontend", "src")

ONE_HOME = "lib/navigate-away.ts"
RULE = "@next/next/no-location-assign-relative-destination"

# ── THE DECLARED SETS ───────────────────────────────────────────────────────────────────────────────
# (C) Files permitted to hand a destination to `location`. Exactly one, and adding a second is the
# edit this lock exists to make impossible to do quietly.
ALLOWED_TO_NAVIGATE = {ONE_HOME}

# (D) The identity boundaries: every place where the acting identity changes and the old identity's
# mounted state must therefore be thrown away. Each MUST dereference the one home.
IDENTITY_BOUNDARIES = {
    "lib/auth-context.tsx",
    "app/(operator)/operator/tenants/page.tsx",
    "components/PlatformBanners.tsx",
}

# (E) The two sites the new rule also flagged, which are NOT identity changes — plain in-app
# navigation. They must use the router, and must NOT dereference the one home: pointing a row click
# at a full document load would be a performance regression dressed as a fix.
ROW_CLICK_NAVIGATION = {
    "app/(platform)/commcalc/asset/purchase-orders/page.tsx",
    "app/(platform)/crm/lookup/page.tsx",
}

# (F) The THIRD category, which the lint rule never saw because its destination is a variable: a
# navigation OUT of the app to an external provider's page. The scan found it anyway (an unreadable
# destination is reported, not waved through), and it is declared here rather than excused — it goes
# through the same one home, which refuses anything that is not absolute https.
EXTERNAL_PROVIDER_REDIRECT = {
    "app/(platform)/vision/settings/page.tsx",
}

PASS = FAIL = 0


def check(name, cond, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"  -> {detail!r}" if detail is not None else ""))


# ── PURE HELPERS (the things the negative controls drive) ────────────────────────────────────────────
# `location.href = <relative>` / `location.assign(<relative>)` / `.replace(<relative>)`, under any of
# the global spellings the lint rule itself recognises. A destination starting with a scheme or `//`
# is absolute and leaves the app, so it is not this lock's business.
_NAV = re.compile(
    r"""(?:^|[^\w.])(?:(?:window|globalThis|self|document)\s*\.\s*)?location
        \s*(?:
            \.\s*href\s*=\s*
          | \.\s*(?:assign|replace)\s*\(\s*
        )(?P<dest>.{0,80})""",
    re.X | re.S,
)
_ABSOLUTE = re.compile(r"^(?:[a-z][\d+.a-z-]*:|//)", re.I)
# A quoted destination, so a literal can be read and judged. Anything NOT matching this is a variable
# or an expression — unreadable here, and therefore reported: handing a computed destination to
# `location` is precisely the shape this lock wants kept to the one home.
_LITERAL = re.compile(r"""^(['"`])([^'"`]*)\1""")
_BLANKET = re.compile(r"eslint-disable(?:-next-line|-line)?[ \t]*(?=$|\*/|--|\n)", re.M)
# ANY suppression directive, counted the same way harness_frontend_lint_lock counts them against
# its ceiling — so this lock and that one cannot disagree about what a suppression is.
_ANY_DISABLE = re.compile(r"eslint-disable(?:-next-line|-line)?")


def code_only(src: str) -> str:
    """Source with `//` line comments and `/* */` blocks removed, QUOTE-AWARE.

    Two reasons it cannot be a regex. Without comment stripping the lock reads its OWN subjects'
    prose: every one of these files explains in a comment what it used to do, quoting the very
    expression being forbidden (the trap `harness_anonymous_write_surface` hit on 2026-10-28, and
    `encryption_backfill`'s AST scan before it). But without quote awareness the stripper eats
    `'//example.test/x'` and `'https://a.test'` as comments, which silently blinds the scan on
    exactly the absolute URLs it is supposed to tell apart from internal paths. So walk the text.
    """
    out = []
    i, n = 0, len(src)
    quote = None          # the delimiter of the string we are inside, or None
    while i < n:
        c = src[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:          # an escape carries its next character along
                out.append(src[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"`":
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def navigations(src: str):
    """The destinations this source hands to `location`, minus the ones that leave the app. PURE.

    A destination that is a readable string literal and absolute (`https:…`, `//host/…`) is a link
    out of the application, which this lock has no opinion about. Everything else — an internal path
    or an expression this scan cannot read — is a navigation.
    """
    out = []
    for m in _NAV.finditer(code_only(src)):
        dest = m.group("dest").strip()
        lit = _LITERAL.match(dest)
        if lit and _ABSOLUTE.match(lit.group(2)):
            continue
        out.append(lit.group(2) if lit else dest.split(")")[0].strip())
    return out


def rule_suppressions(src: str, rule: str):
    """The `eslint-disable*` directives in `src` that name `rule`. PURE."""
    return re.findall(r"eslint-disable(?:-next-line|-line)?[^\n]*" + re.escape(rule) + r"[^\n]*", src)


def rel_sources():
    out = {}
    for base, _dirs, files in os.walk(SRC):
        for f in files:
            if f.endswith((".ts", ".tsx")):
                p = os.path.join(base, f)
                out[os.path.relpath(p, SRC).replace(os.sep, "/")] = open(p, encoding="utf-8").read()
    return out


sources = rel_sources()

print("A. the one home exists, and it really does leave the page")
home = sources.get(ONE_HOME)
check(f"frontend/src/{ONE_HOME} exists", home is not None, sorted(k for k in sources if "identity" in k))
home = home or ""
check("it exports `reloadAs`", re.search(r"export\s+(?:async\s+)?function\s+reloadAs\b", home) is not None)
check("it performs a FULL-DOCUMENT navigation (not a router.push in registry clothing)",
      len(navigations(home)) >= 1 or bool(re.search(r"location\.(?:assign|replace)|location\.href\s*=", code_only(home))),
      home[:200])
check("it does NOT import next/navigation (a router push would keep the old identity's state)",
      "next/navigation" not in home)

print("B. it buys nothing by silencing the linter")
# The first draft of this fix carried a rule-named, justified suppression here, and it was not
# needed: the lint rule only reports a destination it can READ as a literal relative path, and this
# one arrives as a parameter. Keeping the suppression would have pushed the frontend's directive
# count from 92 to 93 — over the ceiling `harness_frontend_lint_lock.PINNED_SUPPRESSIONS` pins — to
# buy nothing at all. So the lock asserts the stronger fact: the one home is outside the rule's
# reach, rather than exempted from it.
check(f"the one home does NOT suppress {RULE}", rule_suppressions(home, RULE) == [],
      rule_suppressions(home, RULE))
check("…and carries no linter suppression of any kind", not _ANY_DISABLE.search(home),
      _ANY_DISABLE.findall(home))
check("no blanket directive in the one home", not _BLANKET.search(home), _BLANKET.findall(home))
check("the destination is a PARAMETER, which is why no suppression is needed",
      re.search(r"location\.assign\(\s*(?:path|parsed[^)]*)\s*\)", code_only(home)) is not None,
      code_only(home)[-400:])

print("C. nothing else hands an internal path to `location` (derived set == declared set)")
offenders = {p for p, s in sources.items() if navigations(s)}
check("the files that navigate are EXACTLY the declared set", offenders == ALLOWED_TO_NAVIGATE,
      {"derived": sorted(offenders), "declared": sorted(ALLOWED_TO_NAVIGATE)})

print("D. every identity boundary dereferences the one home")
derefs = {p for p, s in sources.items()
          if re.search(r"from\s+['\"][^'\"]*navigate-away['\"]", s)}
want = IDENTITY_BOUNDARIES | EXTERNAL_PROVIDER_REDIRECT
check("the files that dereference it are EXACTLY the declared callers",
      derefs == want, {"derived": sorted(derefs), "declared": sorted(want)})
for p in sorted(IDENTITY_BOUNDARIES):
    s = sources.get(p, "")
    check(f"{p} calls reloadAs(", "reloadAs(" in code_only(s), p)

print("F. an external provider redirect goes through the one home, which demands https")
check("the one home exports `redirectToProvider`",
      re.search(r"export\s+function\s+redirectToProvider\b", home) is not None)
check("it refuses a destination that is not https",
      "'https:'" in home and "throw new Error" in home)
check("it parses the destination rather than string-matching it (so `https:evil` cannot pass)",
      "new URL(" in home)
for p in sorted(EXTERNAL_PROVIDER_REDIRECT):
    s_ = sources.get(p, "")
    check(f"{p} calls redirectToProvider(", "redirectToProvider(" in code_only(s_), p)
    check(f"{p} no longer assigns location itself", not navigations(s_), navigations(s_))

print("E. a row click is a router push, not an identity change")
for p in sorted(ROW_CLICK_NAVIGATION):
    s = sources.get(p)
    check(f"{p} exists", s is not None)
    s = s or ""
    check(f"{p} navigates with the router", "useRouter" in s and ".push(" in code_only(s))
    check(f"{p} does NOT dereference the identity reload", "navigate-away" not in s)

print("N. negative controls")
check("control: a relative href assignment is caught",
      navigations("window.location.href = '/commcalc'") == ["/commcalc"])
check("control: location.assign is caught too",
      navigations("location.assign('/x')") == ["/x"])
check("control: an ABSOLUTE destination is not this lock's business",
      navigations("window.location.href = 'https://example.test/x'") == [])
check("control: a protocol-relative destination is absolute as well",
      navigations("location.assign('//example.test/x')") == [])
check("control: the same expression inside a COMMENT is not a navigation",
      navigations("// we used to do window.location.href = '/commcalc' here\n") == [])
check("control: …and inside a block comment",
      navigations("/* window.location.href = '/commcalc' */\n") == [])
check("control: a URL in a string is not mistaken for a comment",
      navigations("const u = 'https://a.test//b'; location.assign('/x')") == ["/x"])
check("control: a destination this scan cannot READ is reported, not waved through",
      navigations("window.location.assign(path)") == ["path"])
check("control: …including a template with an interpolation",
      navigations("location.href = `/crm/leads/${id}`") == ["/crm/leads/${id}"])
check("control: a blanket directive is recognised as blanket",
      bool(_BLANKET.search("// eslint-disable-next-line\nx()\n")))
check("control: a rule-named directive is not blanket",
      not _BLANKET.search("// eslint-disable-next-line " + RULE + " -- why\nx()\n"))
check("control: rule_suppressions finds the named rule and ignores another",
      len(rule_suppressions("// eslint-disable-next-line " + RULE + " -- why\n", RULE)) == 1
      and rule_suppressions("// eslint-disable-next-line react-hooks/purity\n", RULE) == [])
check("control: _ANY_DISABLE counts a directive the same way the lint lock's ceiling does",
      len(_ANY_DISABLE.findall("// eslint-disable-next-line x\n// eslint-disable-line y\n")) == 2)
check("control: the three declared sets are pairwise disjoint (a site has exactly one character)",
      IDENTITY_BOUNDARIES.isdisjoint(ROW_CLICK_NAVIGATION)
      and IDENTITY_BOUNDARIES.isdisjoint(EXTERNAL_PROVIDER_REDIRECT)
      and ROW_CLICK_NAVIGATION.isdisjoint(EXTERNAL_PROVIDER_REDIRECT))
check("control: the scan saw a real tree (an empty walk would pass every set comparison)",
      len(sources) > 300, len(sources))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
