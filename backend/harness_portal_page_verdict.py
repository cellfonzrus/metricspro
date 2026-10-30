#!/usr/bin/env python3
"""ONE VOCABULARY FOR "WHAT IS THIS PAGE TELLING US INSTEAD OF SHOWING A FORM" (index §COMMCALC-50).

DB-free and browser-free. The defect this locks is not a wrong phrase — it is THREE vocabularies for
one question, two of them hardcoded, so a portal's plain refusal fell through every one of them and
the operator was told to go and read form markup on a page with no form on it.

It fails the build if a second phrase list appears in the driver, if a caller stops dereferencing the
one home, if a marker row's kind stops being honoured, if the throttle question starts borrowing
refusal rows (or the reverse), or if the operator message stops leading with what the page said.

Run: python3 harness_portal_page_verdict.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import portal_backoff as pb       # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SWEEP = open(os.path.join(HERE, "app/modules/commcalc/vidapay_sweep.py")).read()
LIVE = open(os.path.join(HERE, "app/modules/commcalc/live_login.py")).read()
BACKOFF = open(os.path.join(HERE, "app/modules/commcalc/portal_backoff.py")).read()
MIG = os.path.join(os.path.dirname(HERE), "database/migrations/1105_portal_block_marker_kind.sql")

_passed = _failed = 0


def check(label, ok, detail=""):
    global _passed, _failed
    if ok:
        _passed += 1
        print("  PASS  %s" % label)
    else:
        _failed += 1
        print("  FAIL  %s%s" % (label, ("   %s" % detail) if detail else ""))


# ── §A the owner's actual page, read correctly ──────────────────────────────────────────────────
print("\n§A the page that started this (owner's UPS billing login, 2026-10-30)")

UPS = ("UPS\nSomething went wrong, or you are trying to perform an action that is not allowed.\n"
       "Please hit back on your browser and try again.\nError Code: 0.c9ab3717.1793366535.1fdc2afb")

v = pb.page_verdict(UPS)
check("A1 it is recognised at all (this is the regression)", v is not None)
check("A2 …as a REFUSAL, not a challenge and not a throttle",
      (v or {}).get("kind") == pb.REFUSAL_KIND, repr(v))
check("A3 …and it is NOT read as a throttle, which would park it in a needless cooldown",
      pb.detect_block(UPS) is None, repr(pb.detect_block(UPS)))
check("A4 the operator is told to check the ADDRESS, not the form markup",
      "address" in (v or {}).get("note", "") and "markup" not in (v or {}).get("note", ""))
check("A5 the note does not blame the form",
      "render differently" not in (v or {}).get("note", ""))

# ── §B the three kinds are three answers ────────────────────────────────────────────────────────
print("\n§B a throttle, a refusal and a challenge are not the same fact")

check("B1 a throttle page is a throttle", (pb.detect_block("you have too many requests") or {})
      .get("marker") == "too many requests")
check("B2 …and page_verdict does not claim it (throttle has its own home, with retry-after)",
      pb.page_verdict("you have too many requests") is None)
check("B3 a challenge page is a challenge",
      (pb.page_verdict("Just a moment... checking your browser") or {}).get("kind")
      == pb.CHALLENGE_KIND)
check("B4 a challenge wins over a refusal when both match (the remedy is more specific)",
      (pb.page_verdict("access denied — please verify you are human") or {}).get("kind")
      == pb.CHALLENGE_KIND)
check("B5 the three notes are three different sentences",
      len({pb.verdict_note(k) for k in pb.VERDICT_KINDS}) == 3)
check("B6 every kind has a note", all(pb.verdict_note(k) for k in pb.VERDICT_KINDS))

# ── §C an ordinary page is left alone ───────────────────────────────────────────────────────────
print("\n§C a false positive turns a working portal into 'refused' — so narrowness is pinned")

for benign in ("Sign in to your account",
               "Username Password Sign in Forgot your password?",
               "Welcome back. Your last invoice is ready to view.",
               "Report ran successfully. 1,284 rows.",
               "",
               None):
    check("C: benign page is not a refusal (%r)" % ((benign or "")[:34],),
          pb.page_verdict(benign) is None, repr(pb.page_verdict(benign)))

# ── §D the vocabulary has ONE home ──────────────────────────────────────────────────────────────
print("\n§D one vocabulary — a second list is the defect itself")

# A phrase in a COMMENT or a DOCSTRING is documentation, not a decision — what this lock forbids is
# a second list that CODE reads. (The driver's own module docstring quotes the very interstitial
# wording it used to hardcode, which is exactly the history worth keeping written down.) So the
# driver is searched as code: docstrings dropped via the AST, then `#` comments stripped.
def _code_only(src):
    import ast as _ast
    tree = _ast.parse(src)
    spans = []
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.Module, _ast.ClassDef, _ast.FunctionDef, _ast.AsyncFunctionDef)):
            doc = node.body[0] if node.body else None
            if (isinstance(doc, _ast.Expr) and isinstance(doc.value, _ast.Constant)
                    and isinstance(doc.value.value, str)):
                spans.append((doc.lineno, doc.end_lineno))
    drop = {n for a, b in spans for n in range(a, b + 1)}
    return "\n".join("" if i + 1 in drop else ln.split("#")[0]
                      for i, ln in enumerate(src.splitlines())).lower()


SWEEP_CODE = _code_only(SWEEP)

# Just `load_markers`, as code: the reading rule of §4b.1 is about THAT read. The other loaders in
# the home read their own relations and are not this lock's business.
_LM = re.search(r"\ndef load_markers\(.*?(?=\n(?:def |_KIND_SEEDS))", BACKOFF, re.S)
LOAD_MARKERS_CODE = _code_only(_LM.group(0) if _LM else "")

# EXCUSED, with the reason, because it answers a genuinely different question (index §COMMCALC-11):
# `_CAPTCHA_TEXT` asks "must a human tick a box on this form", about a form that IS on the page and
# whose password field was found. This lock's question is whether there is a form at all. Merging the
# two would make a sign-in page carrying a captcha read as "no form here — check your address".
EXCUSED_LISTS = {"_CAPTCHA_TEXT": "the human-check widget vocabulary (§COMMCALC-11) — a different question"}

check("D1 the driver no longer carries its own refusal tuple", "_BOT_PHRASES" not in SWEEP)
check("D1b …and the one list that remains is the EXCUSED one, named with its reason",
      all(name in SWEEP for name in EXCUSED_LISTS))
for phrase in ("something doesn't look right", "are you a robot",
               "access to this page has been denied", "attention required", "unusual activity",
               "request unsuccessful", "request is blocked", "unusual traffic"):
    check("D2 %r is not a decision in the driver any more" % phrase,
          phrase not in SWEEP_CODE, "still read by code in vidapay_sweep.py")
check("D2b the excused captcha-widget list may still spell its own words",
      "verify you are human" in SWEEP_CODE and "i'm not a robot" in SWEEP_CODE)
check("D2c …but only inside that list, not in a refusal decision",
      SWEEP_CODE.count("verify you are human") == 1)
check("D3 …and it IS in the one home's seeds",
      all(p in (tuple(pb.DEFAULT_REFUSAL_MARKERS) + tuple(pb.DEFAULT_CHALLENGE_MARKERS))
          for p in ("something doesn't look right", "verify you are human", "are you a robot",
                    "access to this page has been denied", "attention required",
                    "unusual activity", "request unsuccessful")))
check("D4 the driver's predicate dereferences the one home",
      "_pb.page_verdict(" in SWEEP)
check("D5 the live login dereferences the driver's one reader, not a phrase of its own",
      "vp.page_refusal(" in LIVE)
check("D6 the live login spells no refusal phrase itself",
      not any(p in LIVE.lower() for p in pb.DEFAULT_REFUSAL_MARKERS))
check("D7 the seeds are DEFAULTS, read only where config cannot be",
      "_KIND_SEEDS" in BACKOFF and "kind=" in BACKOFF)
check("D8 the WAF check keeps only the AVAILABILITY words, which are a different fact",
      "service unavailable" in SWEEP_CODE and "temporarily unavailable" in SWEEP_CODE)
check("D9 …and no longer ORs its own refusal words with the one home",
      '"access denied",' not in SWEEP_CODE)

# ── §E the error message leads with what the page said ──────────────────────────────────────────
print("\n§E the sentence the operator actually reads")

msg_block = LIVE[LIVE.index("login_fr, pw_el = vp._password_frame(page)"):][:900]
check("E1 the no-password branch asks the page for its verdict FIRST",
      "_page_verdict(vp, page)" in msg_block)
check("E2 …and when there is one, 'Could not find the login form' is not prepended to it",
      re.search(r'_v\["note"\]\s+if\s+_v', msg_block) is not None, msg_block[:200])
check("E3 the old guess survives only as the fallback",
      "Could not find the login form" in msg_block)
check("E4 _wall_note returns the page's own note when the page gave one",
      'if v:\n        return v["note"]' in LIVE)

# ── §F kind is honoured, and an un-kinded row still means throttle ──────────────────────────────
print("\n§F a marker row's kind decides which question it answers")


class _Res:
    def __init__(self, data):
        self.data = data


class _Tbl:
    # The reading rule of index §4b.1: this table's columns arrive by hand-applied migration, so the
    # home must read the row WHOLE. A select that named columns would be a ladder waiting to happen,
    # so this stub refuses one outright and records that the read was whole.
    def __init__(self, rows, seen=None):
        self._rows, self._seen = rows, seen if seen is not None else []

    def select(self, cols):
        self._seen.append(cols)
        if str(cols).strip() != "*":
            raise AssertionError(f"the home named columns ({cols!r}) instead of reading whole")
        return self

    def in_(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return _Res(list(self._rows))


class _Client:
    def __init__(self, rows):
        self._rows, self.seen = rows, []

    def schema(self, *a):
        return self

    def table(self, *a):
        return _Tbl(self._rows, self.seen)


ORG = "11111111-1111-1111-1111-111111111111"
ROWS = [
    {"org_id": ORG, "processor": None, "marker": "slow down please", "enabled": True, "kind": "throttle"},
    {"org_id": ORG, "processor": None, "marker": "you shall not pass", "enabled": True, "kind": "refusal"},
    {"org_id": ORG, "processor": None, "marker": "tick the box", "enabled": True, "kind": "challenge"},
    {"org_id": ORG, "processor": None, "marker": "switched off", "enabled": False, "kind": "refusal"},
]
cl = _Client(ROWS)
check("F1 the throttle question gets only throttle rows",
      pb.load_markers(cl, ORG, kind=pb.THROTTLE_KIND) == ["slow down please"],
      repr(pb.load_markers(cl, ORG, kind=pb.THROTTLE_KIND)))
check("F2 the refusal question gets only refusal rows",
      pb.load_markers(cl, ORG, kind=pb.REFUSAL_KIND) == ["you shall not pass"],
      repr(pb.load_markers(cl, ORG, kind=pb.REFUSAL_KIND)))
check("F3 the challenge question gets only challenge rows",
      pb.load_markers(cl, ORG, kind=pb.CHALLENGE_KIND) == ["tick the box"])
check("F4 a disabled row is honoured for the new kinds too",
      "switched off" not in pb.load_markers(cl, ORG, kind=pb.REFUSAL_KIND))
check("F5 no kind asked ⇒ throttle, so every pre-1105 caller gets what it always got",
      pb.load_markers(cl, ORG) == pb.load_markers(cl, ORG, kind=pb.THROTTLE_KIND))

OLD = [{"org_id": ORG, "processor": None, "marker": "slow down please", "enabled": True}]
old_cl = _Client(OLD)
check("F6 a table whose rows predate the kind column: the throttle question still answers from it",
      pb.load_markers(old_cl, ORG, kind=pb.THROTTLE_KIND) == ["slow down please"],
      repr(pb.load_markers(old_cl, ORG, kind=pb.THROTTLE_KIND)))
check("F7 …and refusal falls back to its SEEDS rather than borrowing throttle rows",
      pb.load_markers(old_cl, ORG, kind=pb.REFUSAL_KIND)
      == list(pb.DEFAULT_REFUSAL_MARKERS))
check("F8 a row with no kind at all reads as throttle, never as a refusal",
      pb.load_markers(_Client(OLD), ORG, kind=pb.REFUSAL_KIND)
      == list(pb.DEFAULT_REFUSAL_MARKERS))
check("F9 a table that cannot be read at all returns the seeds for the kind asked",
      pb.load_markers(object(), ORG, kind=pb.CHALLENGE_KIND)
      == list(pb.DEFAULT_CHALLENGE_MARKERS))
check("F10 a tenant's configured list REPLACES the seeds (that is the point of config)",
      (pb.page_verdict("you shall not pass",
                       refusal=pb.load_markers(cl, ORG, kind=pb.REFUSAL_KIND)) or {}).get("kind")
      == pb.REFUSAL_KIND)
check("F11 …and a seed phrase the tenant did not configure then does NOT match",
      pb.page_verdict("access denied",
                      refusal=pb.load_markers(cl, ORG, kind=pb.REFUSAL_KIND)) is None)
check("F12 every read above was a WHOLE row, so one missing column can never hide the others",
      bool(cl.seen) and set(cl.seen) == {"*"} and set(old_cl.seen) == {"*"},
      repr(sorted(set(cl.seen) | set(old_cl.seen))))
check("F13 …and the home issues ONE read per question, never a second narrower one (no ladder)",
      LOAD_MARKERS_CODE.count(".select(") == 1 and '.select("*")' in LOAD_MARKERS_CODE,
      f"{LOAD_MARKERS_CODE.count('.select(')} select(s)")

# EVERY TENANT, not the one that surfaced it (owner directive 2026-10-30: a feature is for all
# tenants). The seeded rows are HOUSE rows, and the house org is what every tenant inherits when it
# has declared nothing of its own — the RULE TWO default. These two pin that both ways.
HOUSE_ONLY = [{"org_id": pb.HOUSE_ORG, "processor": None, "marker": "you shall not pass",
               "enabled": True, "kind": "refusal"}]
OTHER_ORG = "22222222-2222-2222-2222-222222222222"
house_cl = _Client(HOUSE_ONLY)
check("F14 a tenant with NO rows of its own inherits the HOUSE rows, so a seed serves every tenant",
      pb.load_markers(house_cl, OTHER_ORG, kind=pb.REFUSAL_KIND) == ["you shall not pass"],
      repr(pb.load_markers(house_cl, OTHER_ORG, kind=pb.REFUSAL_KIND)))
both_cl = _Client(HOUSE_ONLY + [{"org_id": OTHER_ORG, "processor": None, "marker": "my own words",
                                 "enabled": True, "kind": "refusal"}])
check("F15 …and its OWN row wins over the house row, which is what per-org config means",
      pb.load_markers(both_cl, OTHER_ORG, kind=pb.REFUSAL_KIND) == ["my own words"],
      repr(pb.load_markers(both_cl, OTHER_ORG, kind=pb.REFUSAL_KIND)))

# ── §G the migration says what the code believes ────────────────────────────────────────────────
print("\n§G the migration and the code agree")

sql = open(MIG).read()
check("G1 the column defaults to throttle, so no seeded row changes meaning",
      "default 'throttle'" in sql)
check("G2 the kinds the check constraint allows are the kinds the code knows",
      all(("'%s'" % k) in sql for k in pb.VERDICT_KINDS))
check("G3 additive and idempotent", "add column if not exists" in sql and "on conflict do nothing" in sql)
check("G4 it carries a REVERT note", "-- REVERT:" in sql)
check("G5 it grants what it touches", "to service_role" in sql)
check("G6 the UPS wording is seeded as a refusal",
      "'action that is not allowed',          'refusal'" in sql
      or "'action that is not allowed'," in sql and "'refusal'" in sql)
check("G7 every phrase moved out of the driver is seeded in the migration",
      all(p.replace("'", "''") in sql for p in
          ("something doesn't look right", "verify you are human", "are you a robot")))
check("G8 it declares itself not money-touching", "NOT MONEY-TOUCHING" in sql)

# ── §H RULE TWO — no portal is named in the home ────────────────────────────────────────────────
print("\n§H the home names no portal (RULE TWO)")

decision = BACKOFF[BACKOFF.index("THROTTLE_KIND ="):]
for vendor in ("ups", "cloudflare", "akamai", "vidapay", "t-cetra", "b2bsoft", "boost", "verizon"):
    check("H: %r is not a decision in the home" % vendor,
          not re.search(r'^\s*(if|elif|return|while).*\b%s\b' % re.escape(vendor),
                        decision, re.M | re.I))
check("H9 the Cloudflare conjunction that remains is a SHAPE, not a vocabulary",
      SWEEP.count('"cloudflare" in body') == 1 and "ray id" in SWEEP)

# ── §Z armed negative controls — each check is planted with its own defect ──────────────────────
print("\n§Z negative controls")


def control(label, ok):
    check("control: %s" % label, ok)


control("a refusal phrase removed from the seeds stops matching the owner's page",
        pb.page_verdict(UPS, refusal=()) is None)
control("…and the same page still matches with the seeds back",
        pb.page_verdict(UPS) is not None)
control("a page carrying NO marker is not a verdict",
        pb.page_verdict("perfectly ordinary sign-in page") is None)
control("a kind the code does not know gets no note", pb.verdict_note("nonsense") == "")
control("a row of an unknown kind answers no question",
        pb.load_markers(_Client([{"org_id": ORG, "processor": None, "marker": "x",
                                  "enabled": True, "kind": "weather"}]), ORG,
                        kind=pb.REFUSAL_KIND) == list(pb.DEFAULT_REFUSAL_MARKERS))
control("an empty configured list means EMPTY, not 'fall back to the seeds'",
        pb.page_verdict("access denied", refusal=[]) is None)
control("the throttle detector still refuses a refusal page (the two cannot merge)",
        pb.detect_block("access to this page has been denied") is None)
control("…and the refusal detector still refuses a throttle page",
        pb.page_verdict("rate limit exceeded") is None)
control("matching is case-insensitive on BOTH sides",
        (pb.page_verdict("ACTION THAT IS NOT ALLOWED", refusal=["Action That Is Not Allowed"])
         or {}).get("kind") == pb.REFUSAL_KIND)

print("\n%d passed, %d failed\n" % (_passed, _failed))
if _failed:
    print("FAIL — the page-verdict lock is open. A portal's own words about why it refused us must "
          "reach the operator, from ONE configurable vocabulary, or the next refusal will again read "
          "as 'the login form may render differently than expected'.")
    sys.exit(1)
print("OK — one vocabulary, three kinds, the page's own words lead, and no portal is named.")
