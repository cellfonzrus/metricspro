#!/usr/bin/env python3
"""LOCK — a frontend dependency may not slide back below the version that fixed a known advisory.

WHY THIS EXISTS (security review 2026-10-28, finding 2 + finding 6).

`next` was pinned at **16.2.7**, which carries FOUR critical advisories — three of them
*unauthenticated remote code execution*:

    GHSA-2xp9-vwfh-vxw4  RCE, Image Optimization API, AVIF
    GHSA-2464-fhr4-w9xw  RCE, Image Optimization API, SVG
    GHSA-vcvr-r3jv-pc5j  RCE, next/og ImageResponse
    GHSA-673q-mjm6-r5xr  RCE, `use cache` returning typed arrays

plus high-severity SSRF in rewrites and in image optimization, and an App Router **middleware /
proxy bypass** (GHSA-6gpp-xcg3-4w24). The fix was a PATCH-level bump inside the same major.

**`npm audit` had been reporting every one of them, on every pull request, for weeks.** Nothing
failed, because every scanner in `.github/workflows/security.yml` runs `continue-on-error: true` —
the workflow's own header says the intent was to drop that "once the baseline is clean", and it never
happened. So the finding was not that nobody looked; it was that **looking had no teeth.**

THE CLASS, NOT THE INSTANCE (CLAUDE.md). Bumping `next` fixes one instance. The general fact that was
wrong is **"a dependency advisory we have already answered can be silently re-opened"** — by a
revert, a merge resolving a lockfile the wrong way, or a `package.json` edit that widens a pin. So
this is a FLOOR, in CI, enforcing: a version below one we have already fixed fails the build and
names the advisory it re-opens.

NOT A FULL `npm audit` GATE. That is the bigger piece and it is deliberately separate: turning the
scanners red today would block every PR on `jspdf` and `xlsx` (below), neither of which has a
non-breaking fix. This gate covers what we have ALREADY paid for, which is the part that must not
regress. The shape is §CORE-13's `CUTOFF` — a ratchet, read shrink-only.

WHAT THIS PROVES (DB-free, stdlib only — it parses one JSON file):
  A. the floors      — every package we have fixed is at or above the fixing version
  B. exact pins      — `next` and `eslint-config-next` stay EXACTLY pinned and in step with each
                       other (a floating `^16` would let the fixed version drift back out)
  C. lockfile        — package-lock.json agrees with package.json, so CI installs what we pinned
  D. known-unfixed   — the packages with NO safe upgrade are an explicit, SHRINKING list with a
                       written reason each; a new entry must be added deliberately
  N. negative controls — each floor really does reject the version it was raised from

Run: python3 backend/harness_frontend_dep_floor.py
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG = os.path.join(ROOT, "frontend", "package.json")
LOCK = os.path.join(ROOT, "frontend", "package-lock.json")

PASS = FAIL = 0


def check(name, cond, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"  -> {detail!r}" if detail is not None else ""))


# ── THE FLOORS. package -> (minimum safe version, what a lower version re-opens) ────────────────
# Add a row the day an advisory is answered. NEVER lower one: a floor coming down means a fix is
# being given back, and that is the whole thing this file exists to refuse.
FLOORS = {
    "next": ("16.4.3",
             "4 critical unauthenticated RCEs (image optimizer AVIF/SVG, next/og, `use cache`), "
             "SSRF in rewrites + image optimization, and an App Router middleware bypass "
             "(GHSA-2xp9-vwfh-vxw4 / 2464-fhr4-w9xw / vcvr-r3jv-pc5j / 673q-mjm6-r5xr / "
             "6gpp-xcg3-4w24). Fixed 2026-10-28."),
    "eslint-config-next": ("16.4.3",
             "not a vulnerability of its own — it must track `next` exactly, or `npm install` "
             "resolves a second, older Next.js into the tree to satisfy it."),
}

# ── KNOWN-UNFIXED, WITH A REASON. This list may only SHRINK. ────────────────────────────────────
# A package here has an open advisory and NO upgrade we can take today. Each one is a decision
# somebody made on purpose, written down, so it is reviewable rather than forgotten.
KNOWN_UNFIXED = {
    "xlsx": ("Prototype pollution (GHSA-4r6h-8v6p-xvw6) + ReDoS (GHSA-5pgg-2g8v-p4x9). NO fixed "
             "version exists on the npm registry — SheetJS moved releases to its own CDN. Exposure "
             "is a workbook the user chose, parsed in that user's own tab. Options: pin SheetJS's "
             "own distribution, or move workbook parsing to the backend, which already does it "
             "with pandas/openpyxl."),
    "jspdf": ("Path traversal (GHSA-f8cm-6447-x5h2) + HTML injection in new-window paths "
              "(GHSA-wfv2-pwc8-crg5), and it pulls a vulnerable dompurify. The fix is jspdf@4.2.1, "
              "a BREAKING major, and it is on the PDF-export path for every report — so it needs a "
              "pass over the exports, not a blind bump. Both advisories are read against a "
              "server-side file system and a new browser window; this runs client-side on a "
              "document the user asked for."),
    "jspdf-autotable": ("moves with jspdf (the fix is jspdf-autotable@5.0.9, also a breaking "
                        "major). Same decision, same follow-up."),
}
# The ratchet. Lower it as entries are retired; raising it must be a deliberate, reviewed act.
KNOWN_UNFIXED_MAX = 3


def parse_version(v):
    """A semver string -> (major, minor, patch). Tolerates a leading ^ ~ = or v. PURE."""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", str(v or ""))
    return tuple(int(g) for g in m.groups()) if m else None


def pinned_exactly(spec):
    """True when the spec names ONE version — no ^, ~, range or tag."""
    return bool(re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.\-]+)?", str(spec or "").strip()))


pkg = json.load(open(PKG, encoding="utf-8"))
deps = dict(pkg.get("dependencies") or {})
deps.update(pkg.get("devDependencies") or {})

print("A. every advisory we have already answered stays answered")
for name, (floor, why) in sorted(FLOORS.items()):
    spec = deps.get(name)
    check(f"{name} is declared in package.json", spec is not None, sorted(deps))
    got, want = parse_version(spec), parse_version(floor)
    check(f"{name} {spec} >= {floor}", got is not None and want is not None and got >= want,
          f"{spec} < {floor} re-opens: {why}")

print("B. the Next.js pin is exact, and its eslint config moves with it")
check("next is pinned exactly (no ^ or ~ — a range lets the fixed version drift back out)",
      pinned_exactly(deps.get("next")), deps.get("next"))
check("eslint-config-next is pinned exactly", pinned_exactly(deps.get("eslint-config-next")),
      deps.get("eslint-config-next"))
check("the two pins are the SAME version (else npm resolves a second, older Next.js)",
      parse_version(deps.get("next")) == parse_version(deps.get("eslint-config-next")),
      (deps.get("next"), deps.get("eslint-config-next")))

print("C. the lockfile agrees, so CI installs what we pinned")
lock = json.load(open(LOCK, encoding="utf-8"))
packages = lock.get("packages") or {}
for name, (floor, why) in sorted(FLOORS.items()):
    entry = packages.get(f"node_modules/{name}") or {}
    got = parse_version(entry.get("version"))
    check(f"package-lock resolves {name} to {entry.get('version')} >= {floor}",
          got is not None and got >= parse_version(floor), (entry.get("version"), floor))
root = packages.get("") or {}
root_deps = dict(root.get("dependencies") or {})
root_deps.update(root.get("devDependencies") or {})
drift = {n: (root_deps.get(n), s) for n, s in deps.items() if root_deps.get(n) != s}
check("the lockfile's own root block matches package.json exactly (no stale lock)",
      drift == {}, drift)
check("the lockfile is lockfileVersion >= 2 (it records resolved versions at all)",
      int(lock.get("lockfileVersion") or 0) >= 2, lock.get("lockfileVersion"))

print("D. the known-unfixed list is explicit and shrinking")
check(f"at most {KNOWN_UNFIXED_MAX} packages are carried with an open advisory",
      len(KNOWN_UNFIXED) <= KNOWN_UNFIXED_MAX, len(KNOWN_UNFIXED))
check("the ratchet was not raised (it may only come down)", KNOWN_UNFIXED_MAX <= 3,
      KNOWN_UNFIXED_MAX)
for name, why in sorted(KNOWN_UNFIXED.items()):
    check(f"{name} is still actually a dependency (a stale excusal is removed)",
          name in deps, sorted(deps))
    check(f"{name}'s reason names the advisory or says why no fix can be taken",
          bool(re.search(r"GHSA-|NO fixed version|breaking", why)), why[:80])
    check(f"{name} is not ALSO claimed as fixed (a package cannot be both)",
          name not in FLOORS)

print("N. negative controls")
check("control: the next floor really does reject 16.2.7, the version it was raised from",
      parse_version("16.2.7") < parse_version(FLOORS["next"][0]))
check("control: …and 16.4.3 itself passes", parse_version("16.4.3") >= parse_version(FLOORS["next"][0]))
check("control: a floating pin is caught", not pinned_exactly("^16.4.3")
      and not pinned_exactly("~16.4.3") and not pinned_exactly("latest"))
check("control: parse_version tolerates a caret and refuses junk",
      parse_version("^1.2.3") == (1, 2, 3) and parse_version("latest") is None)
check("control: the floors list is not empty (a gate that checks nothing passes forever)",
      len(FLOORS) >= 2, len(FLOORS))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
