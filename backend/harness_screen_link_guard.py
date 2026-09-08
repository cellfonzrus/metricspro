"""PROOF: when user-facing copy NAMES a screen, that name is a LINK — and the link gates itself.

OWNER DIRECTIVE 2026-09-08, verbatim: *"for dm verify it shows 'Asad Umar is still assigned as this
store's closer but is no longer an employee — clear the assignment under Cash Setup.' — need to have
a link for Cash setup if the option is presented for any menu — assign an agent to check all
references and add the menus and make a summary where all the links have been added and for what
purpose."*

THE DEFECT. `closing/closer_resolution.closer_for_day` writes a correct sentence that ends
"…clear the assignment under Cash Setup." and `components/DailyClosingVerify` printed it verbatim.
The reader is told to go somewhere and then left to find it. Same shape in a dozen other places:
"map them under Closing → Tender Config", "grant it in Roles & Access", "map them in Store Matching".

ONE MECHANISM, deliberately not two: `frontend/src/components/ScreenLink.tsx`.
  • `SCREENS`   — screen name/aliases → the NAV href that already exists in `lib/rbac.ts`.
  • `ScreenLink`/`Signpost` — the link forms; `Signpost` is the generalisation of the pre-existing
    `SalesTaxRateLink` (§23i), which is now a thin wrapper so there is still ONE signpost.
  • `LinkedText` — linkifies a plain STRING, which is what makes it work for BACKEND-authored notes.
    A backend note cannot carry JSX and should not have to: it already names the screen. Adding a
    parallel `href`/`link_label` field to every note-producing endpoint would create a SECOND source
    of truth for "where does this screen live" — exactly the drift the house rules forbid.

RBAC: every link gates itself with `canSeeItem` over the destination's OWN NAV entry — the same
predicate the sidebar uses — so a link can never advertise a page its viewer would be bounced out of.
That gate only works if the href IS a NAV href, which is what §A pins.

WHAT THIS PINS
  A. every registered destination resolves to a real NAV entry in `lib/rbac.ts` (no invented hrefs,
     and therefore no link that silently gates to `false` for everyone);
  B. the owner's exact sentence, byte-for-byte from `closer_resolution.py`, linkifies "Cash Setup"
     to /closing/cash-config — the reported defect, reproduced and closed;
  C. longest-alias-first: "Closing → Tender Config" links as ONE breadcrumb, not just its tail;
  D. word boundaries — a registered name inside a longer word is not linkified, and matching is
     case-insensitive;
  E. the render sites that were fixed still route their prose through the mechanism (a later edit
     that strips the link is caught here, not by a user);
  F. there is exactly ONE signpost implementation — `SalesTaxRateLink` delegates to it rather than
     carrying a second copy of the gate.

PURE / DB-FREE: parses the real .tsx sources as text; no network, no database, stdlib only.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FRONT = os.path.join(os.path.dirname(HERE), "frontend", "src")
SCREEN_LINK = os.path.join(FRONT, "components", "ScreenLink.tsx")
RBAC = os.path.join(FRONT, "lib", "rbac.ts")

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


# ── Parse the registry out of the real component (no JS runtime needed) ─────────────────────────
SRC = read(SCREEN_LINK)


def parse_screens(src):
    """{key: {'href':…, 'label':…, 'aliases':[…]}} read from the SCREENS literal."""
    body = src.split("export const SCREENS", 1)[1]
    out = {}
    for m in re.finditer(r"^  (\w+): \{(.*?)^  \},", body, re.S | re.M):
        key, blob = m.group(1), m.group(2)
        href = re.search(r"href: '([^']+)'", blob)
        label = re.search(r"label: '([^']+)'", blob)
        al = re.search(r"aliases: \[(.*?)\]", blob, re.S)
        if not (href and label and al):
            continue
        out[key] = {
            "href": href.group(1), "label": label.group(1),
            "aliases": re.findall(r"'([^']+)'", al.group(1)),
        }
    return out


SCREENS = parse_screens(SRC)
NAV_HREFS = set(re.findall(r"\{ href: '([^']+)'", read(RBAC)))

print("=" * 78)
print("SCREEN-LINK GUARD — a named destination is a link (owner 2026-09-08)")
print("=" * 78)
print("registry: %d destination(s); NAV: %d href(s)" % (len(SCREENS), len(NAV_HREFS)))
print()

# ── A. every destination is a REAL nav href (else the self-gate denies everybody) ───────────────
print("A. every registered destination is a real NAV entry")
check("A1 the registry parsed and is not empty", len(SCREENS) >= 10, str(sorted(SCREENS)))
for key, d in sorted(SCREENS.items()):
    base = d["href"].split("#")[0].split("?")[0]
    check("A2 %-22s %-34s is in NAV" % (key, base), base in NAV_HREFS,
          "%s is not a NAV href — the gate would return false for EVERY viewer" % base)
check("A3 no destination is registered twice under two keys",
      len({d["href"] for d in SCREENS.values()}) == len(SCREENS),
      str(sorted(d["href"] for d in SCREENS.values())))

# ── the linkifier, re-implemented from the component's own stated rules ─────────────────────────
ALIASES = sorted(((a, k) for k, d in SCREENS.items() for a in d["aliases"]),
                 key=lambda t: -len(t[0]))
ALIAS_RE = re.compile(
    r"(?<![A-Za-z0-9])(" + "|".join(re.escape(a) for a, _ in ALIASES) + r")(?![A-Za-z0-9])",
    re.I)
BY_ALIAS = {a.lower(): k for a, k in ALIASES}


def split_mentions(text):
    """Mirror of splitScreenMentions() in ScreenLink.tsx: [(text, key|None), …]."""
    out, last = [], 0
    for m in ALIAS_RE.finditer(text or ""):
        key = BY_ALIAS.get(m.group(1).lower())
        if not key:
            continue
        if m.start() > last:
            out.append((text[last:m.start()], None))
        out.append((m.group(1), key))
        last = m.end()
    if last < len(text or ""):
        out.append((text[last:], None))
    return out


def linked(text):
    return [(t, k) for t, k in split_mentions(text) if k]


# ── B. THE REPORTED DEFECT, from the backend's own source ───────────────────────────────────────
print()
print("B. the owner's exact sentence links Cash Setup")
CLOSER = read(os.path.join(HERE, "app", "modules", "closing", "closer_resolution.py"))
# rebuild the note the way closer_for_day does, so a reworded backend note fails HERE.
check("B0 closer_resolution still ends that note with 'under Cash Setup.'",
      "clear the assignment under Cash Setup." in CLOSER,
      "the backend note was reworded — re-check the registry alias")
NOTE = ("Asad Umar is still assigned as this store's closer but is no longer an "
        "employee — clear the assignment under Cash Setup.")
hits = linked(NOTE)
check("B1 exactly one destination is named in it", len(hits) == 1, str(hits))
check("B2 and it is Cash Setup", hits and hits[0] == ("Cash Setup", "cash_setup"), str(hits))
check("B3 pointing at /closing/cash-config", SCREENS["cash_setup"]["href"] == "/closing/cash-config",
      SCREENS["cash_setup"]["href"])
check("B4 the rest of the sentence is untouched",
      "".join(t for t, _ in split_mentions(NOTE)) == NOTE)
check("B5 the person's name is NOT linkified", "Asad Umar" not in [t for t, _ in hits])

# ── C. breadcrumbs link whole, not by their tail ────────────────────────────────────────────────
print()
print("C. longest alias wins (a breadcrumb links as one thing)")
UPLOAD = ("The tender matrix was found, but none of its tender labels are recognized. Map the labels "
          "listed below under Closing → Tender Config (the x_report leg), then upload the same file "
          "again.")
h = linked(UPLOAD)
check("C1 one destination", len(h) == 1, str(h))
check("C2 the WHOLE breadcrumb is the link text", h and h[0][0] == "Closing → Tender Config", str(h))
check("C3 it goes to Tender Setup", h and h[0][1] == "tender_setup", str(h))
BACKEND_UPLOAD = read(os.path.join(HERE, "app", "modules", "commcalc", "router.py"))
check("C4 the BACKEND's own x_report note names the same screen (one mechanism serves both)",
      "Closing → Tender Config" in BACKEND_UPLOAD)
h2 = linked("Map them under Closing → Tender Config (report 'x_report'), then re-upload.")
check("C5 and it linkifies identically", h2 and h2[0][1] == "tender_setup", str(h2))

# ── D. boundaries ───────────────────────────────────────────────────────────────────────────────
print()
print("D. boundaries and case")
check("D1 a name inside a longer word is NOT linked", linked("XCash Setupz") == [], str(linked("XCash Setupz")))
check("D2 matching is case-insensitive (owner typed 'Cash setup')",
      linked("clear it under Cash setup.") == [("Cash setup", "cash_setup")],
      str(linked("clear it under Cash setup.")))
check("D3 trailing punctuation does not break the match",
      linked("… in Roles & Access.") == [("Roles & Access", "roles_access")],
      str(linked("… in Roles & Access.")))
check("D4 prose with no destination is left completely alone",
      linked("2 people worked and only 1 closed, but the cash ties to the POS X-report.") == [])
check("D5 an empty/None note is safe", split_mentions("") == [] and split_mentions(None) == [])

# ── E. the fixed render sites still route their prose through the mechanism ─────────────────────
print()
print("E. the render sites still carry the mechanism")
SITES = [
    ("components/DailyClosingVerify.tsx", "<LinkedText text={s.closer_note} />",
     "DM Verify — the owner's own card"),
    ("app/(platform)/commcalc/_lib/uploadGuard.tsx", "<LinkedText text={outcome.reason || outcome.text} />",
     "every upload surface's guard banner"),
    ("app/(platform)/commcalc/targets/page.tsx", "<LinkedText text={h} />", "Daily Targets setup hints"),
    ("app/(platform)/commcalc/targets/my/page.tsx", "<LinkedText text={h} />", "My Targets setup hints"),
    ("app/(platform)/commcalc/carrier-recon/page.tsx", 'ScreenLink to="store_matching"',
     "Carrier Reconciliation unmatched-stores callout"),
    ("app/(platform)/admin/dashboards/page.tsx", "<LinkedText text={err} />", "Dashboard Designer 403"),
    ("app/(platform)/pos/sales/page.tsx", "<LinkedText text={issue} />", "Register checkout blockers"),
    ("app/(platform)/vision/page.tsx", "<LinkedText text={body} />", "Vision notices"),
    ("app/(platform)/commcalc/pay-simulator/_components/PaySimulator.tsx", "<LinkedText text={msg} />",
     "Pay simulator unavailable-reason"),
    ("app/portal/page.tsx", 'ScreenLink to="roles_access"', "employee portal login problems"),
]
for rel, needle, why in SITES:
    body = read(os.path.join(FRONT, *rel.split("/")))
    check("E %-52s (%s)" % (rel, why), needle in body, "missing: %s" % needle)

# ── F. one signpost implementation, not two ─────────────────────────────────────────────────────
print()
print("F. one mechanism")
STX = read(os.path.join(FRONT, "components", "SalesTaxRateLink.tsx"))
# CODE only — the file's header prose legitimately explains the NAV/canSeeItem gate it delegates to.
STX_CODE = "\n".join(l for l in STX.split("\n") if not l.lstrip().startswith("//"))
check("F1 SalesTaxRateLink delegates to the shared component",
      "from '@/components/ScreenLink'" in STX)
check("F2 and no longer carries its own copy of the sidebar gate",
      "canSeeItem" not in STX_CODE and "NAV" not in STX_CODE,
      "a second gate would drift from the sidebar's")
check("F3 the shared component gates with the sidebar's OWN predicate",
      "canSeeItem" in SRC and "from '@/lib/rbac'" in SRC)
check("F4 an unregistered href is refused rather than guessed",
      "if (!item) return false" in SRC)
check("F5 inline links degrade to plain text, standalone signposts to nothing",
      "if (!allowed) return <b>{body}</b>" in SRC and "if (!allowed) return null" in SRC)
check("F6 RULE TWO — no carrier/tenant branch in the registry",
      not re.search(r"\b(boost|verizon|at&t|t-?mobile|luxelink)\b", SRC, re.I))

print()
print("=" * 78)
print("RESULT: %d passed, %d failed" % (P, F))
print("=" * 78)
sys.exit(1 if F else 0)
