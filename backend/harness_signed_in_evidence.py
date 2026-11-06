#!/usr/bin/env python3
"""A SUCCESSFUL LOGIN IS RECOGNISED BY EVIDENCE, NOT BY A GUESSED VOCABULARY (index §COMMCALC-55).

DB-free and browser-free.

THE DEFECT. The owner reported on 2026-11-05 that the Boost owners-portal sweep still would not
pull, with the status "The sign-in page went away and we did not recognise what replaced it". His
screenshot showed that the sign-in had SUCCEEDED: the signed-in shell, greeting him by his e-mail
address, offering "Multi-Door Owner Portal", "Commissions" and "Boost Activations" — and carrying
not one of the six words `login_outcome.DEFAULT_SIGNED_IN_MARKERS` holds. Three carrier feeds stayed
frozen on a login that worked.

THE CLASS. §COMMCALC-52 was right to delete the previous test — "the vendor's name is in the
document title", a carrier name in code (RULE TWO) — but it replaced one guess with another: a list
of house words standing in for a vocabulary this platform does not own. The same shape as the
device-line departments defaulting to one tenant's POS labels (owner directive 2026-10-30): a
DEFAULT that is really somebody's vocabulary, with a plausible-looking answer behind it.

THE FIX THIS LOCKS, in two halves:
  • EVIDENCE, which needs no declaration: a page showing back the account name we signed in WITH
    cannot be an anonymous page. `login_outcome.greeted_by_name`.
  • CONFIG for the portal that greets nobody: a fourth `kind` on `commcalc.portal_block_marker`
    (mig 1114), read through the loader the other three kinds already use. A ROW, never a branch.

It fails the build if:
  • the owner's own page stops reading as signed in;
  • the greeting is believed where it must not be — with the form still on screen, on a challenge or
    refusal page, or from a user name short enough to sit inside ordinary prose;
  • a driver stops handing the one home the user name, or an org's declared wording;
  • the declared wording stops being config: the kind disappears, becomes a verdict, or the house
    seeds are COPIED into the marker table's module instead of dereferenced from their one home;
  • an empty declaration can switch the word list off (the `or None` rule);
  • one tenant's declared words can be reached from another tenant's read;
  • the migration and the code disagree about which kinds exist.

Run: python3 harness_signed_in_evidence.py
"""
import ast
import io
import os
import re
import sys
import tokenize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import login_outcome as lo          # noqa: E402
from app.modules.commcalc import portal_backoff as pb         # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MODULES = os.path.join(HERE, "app/modules/commcalc")
MOD = os.path.join(MODULES, "login_outcome.py")
BACKOFF = os.path.join(MODULES, "portal_backoff.py")
EPAY = os.path.join(MODULES, "epay_sweep.py")
DLAR = os.path.join(MODULES, "dlar_sweep.py")
MIG = os.path.join(HERE, "../database/migrations/1114_portal_block_marker_signed_in.sql")

_passed = _failed = 0


def check(label, ok, detail=""):
    global _passed, _failed
    if ok:
        _passed += 1
        print("  PASS  %s" % label)
    else:
        _failed += 1
        print("  FAIL  %s%s" % (label, ("   %s" % detail) if detail else ""))


def executable_text(path):
    """A file's source with every comment and docstring stripped — what actually RUNS.

    House precedent (2026-10-30): a comment naming the defect a fix is about otherwise reads as that
    fix's own violation, so a scan of prose is not a scan."""
    src = open(path).read()
    kept = [t[:2] for t in tokenize.generate_tokens(io.StringIO(src).readline)
            if t.type != tokenize.COMMENT]
    tree = ast.parse(tokenize.untokenize(kept))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body.pop(0)
    return ast.unparse(tree)


def function_source(path, name):
    """One function's source, by name. '' when it is gone — itself a failure."""
    src = open(path).read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    return ""


def function_exec(path, name):
    """One function's EXECUTABLE text — comments and docstring stripped.

    §C asks about the ORDER the code reads its signals in, and a docstring that explains the order
    mentions every one of them near the top. Scanning prose would have this harness pass on a file
    whose code does the wrong thing, which is the one failure mode a lock may not have.
    """
    import textwrap
    seg = function_source(path, name)
    if not seg:
        return ""
    seg = textwrap.dedent(seg)
    kept = [t[:2] for t in tokenize.generate_tokens(io.StringIO(seg).readline)
            if t.type != tokenize.COMMENT]
    tree = ast.parse(tokenize.untokenize(kept))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body.pop(0)
    return ast.unparse(tree)


MOD_EXEC = executable_text(MOD)
BACKOFF_EXEC = executable_text(BACKOFF)
EPAY_EXEC = executable_text(EPAY)

# ── §A the regression: the owner's own page, as a person reads it ───────────────────────────────
print("\n§A the page in the owner's 2026-11-05 screenshot")

# The visible text of that page, transcribed from the screenshot. No house signed-in word is in it;
# that is the whole point.
OWNER_PAGE = ("Home Hello rajiv.jaggi@cellularservices.net\n"
              "Multi-Door Owner Portal\n"
              "Commissions Boost Activations\n"
              "© 2015 Privacy Security")
OWNER_USER = "rajiv.jaggi@cellularservices.net"

check("A1 the page carries NONE of the house signed-in words, which is why it broke",
      lo.signed_in_said(OWNER_PAGE) is None,
      repr(lo.signed_in_said(OWNER_PAGE)))
v = lo.verdict(form_present=False, visible_text=OWNER_PAGE, signed_in_user=OWNER_USER)
check("A2 and it reads as SIGNED IN all the same, because the page is showing us our own name",
      v["outcome"] == lo.SIGNED_IN, repr(v))
check("A3 the status quotes the evidence, so an operator can see WHY it was believed",
      OWNER_USER in str(v.get("marker") or ""), repr(v.get("marker")))
check("A4 WITHOUT the user name it is honestly unrecognised — the old answer, now the fallback",
      lo.verdict(form_present=False, visible_text=OWNER_PAGE)["outcome"] == lo.UNKNOWN)
check("A5 the evidence needs no declaration: a page that names no portal still works",
      lo.verdict(form_present=False, visible_text="Welcome back, " + OWNER_USER,
                 signed_in_user=OWNER_USER)["outcome"] == lo.SIGNED_IN)

# ── §B the greeting is believed only where it proves something ──────────────────────────────────
print("\n§B the signal is careful, because a false positive here reads a refusal as a success")

check("B1 the form still on screen beats the greeting — a refused form re-renders with the name in it",
      lo.verdict(form_present=True, visible_text="Sign in " + OWNER_USER,
                 signed_in_user=OWNER_USER)["outcome"] != lo.SIGNED_IN)
check("B2 a REJECTION naming the user is a rejection",
      lo.verdict(form_present=True, visible_text="%s — password incorrect" % OWNER_USER,
                 signed_in_user=OWNER_USER)["outcome"] == lo.REJECTED)
check("B3 a human check naming the user is a human check, not a sign-in",
      lo.verdict(form_present=False, visible_text="Hello %s, are you a robot" % OWNER_USER,
                 page_verdict={"kind": pb.CHALLENGE_KIND, "marker": "are you a robot"},
                 signed_in_user=OWNER_USER)["outcome"] == lo.HUMAN_CHECK)
check("B4 a REFUSAL naming the user is a refusal",
      lo.verdict(form_present=False, visible_text="%s: access denied" % OWNER_USER,
                 page_verdict={"kind": pb.REFUSAL_KIND, "marker": "access denied"},
                 signed_in_user=OWNER_USER)["outcome"] == lo.REFUSED)
check("B5 a user name too short to be distinctive is not evidence (it sits inside ordinary prose)",
      lo.greeted_by_name("Innovation in progress", "nova") is None)
check("B6 …and the threshold is DECLARED, not a number buried in the test",
      isinstance(lo.MIN_GREETING_NAME, int) and lo.MIN_GREETING_NAME >= 5)
check("B7 a blank user name is never evidence",
      lo.greeted_by_name(OWNER_PAGE, "") is None and lo.greeted_by_name(OWNER_PAGE, None) is None)
check("B8 matching is case-insensitive on BOTH sides",
      lo.greeted_by_name("HELLO RAJIV.JAGGI@CELLULARSERVICES.NET", OWNER_USER.upper())
      == OWNER_USER.lower())
check("B9 a name that is simply not on the page is not evidence",
      lo.greeted_by_name("Multi-Door Owner Portal", OWNER_USER) is None)
check("B10 it is PURE — no page object, no client, no clock",
      not re.search(r"\b(page|client|datetime|requests)\b", function_source(MOD, "greeted_by_name")
                    .split('"""')[-1]))

# ── §C the ranking, in the one home, is the thing that makes §B true ────────────────────────────
print("\n§C the greeting is ranked, not merely present")

VERD = function_exec(MOD, "verdict")
check("C1 the greeting is read only where the form is GONE",
      re.search(r"if not form_present:\s*\n\s*named = greeted_by_name", VERD) is not None, VERD[-400:])
check("C2 …and after the page-verdict (challenge / refusal) branch, never before it",
      VERD.index("page_verdict") < VERD.index("greeted_by_name"))
check("C3 the rejection check still comes first of all", VERD.index("rejection_said") < VERD.index("greeted_by_name"))
check("C4 there is exactly ONE place the greeting is believed", VERD.count("greeted_by_name") == 1)
check("C5 the evidence arguments keep NO defaults that would let a caller answer without looking",
      re.search(r"def verdict\(\*,\s*form_present,\s*visible_text\b", MOD_EXEC) is not None)

# ── §D the drivers dereference it ───────────────────────────────────────────────────────────────
print("\n§D every driver that asks 'are we in' hands over the evidence")

LV = function_source(EPAY, "_login_verdict")
check("D1 the browser driver hands the one home the user name it TYPED",
      "signed_in_user=user" in LV, LV)
check("D2 …and the org's declared wording", "signed_in_markers=signed_in_markers" in LV)
check("D3 the login function carries the declaration through, rather than dropping it",
      re.search(r"def _login\(page, user, pw, signed_in_markers=None\)", EPAY_EXEC) is not None)
RES = function_source(EPAY, "run_epay_sweep")
check("D4 the sweep READS the declaration once for the run",
      "_declared_signed_in_markers(client, org_id)" in RES)
_logins = re.findall(r"_login\(page, user, pw[^)]*\)", RES)
check("D5 EVERY login on that run passes it — the re-login after a reload included",
      len(_logins) >= 2 and all("signed_in_markers=" in c for c in _logins),
      repr(_logins))
# The sibling that is EXCUSED, with its reason in the file rather than in a reviewer's memory.
_dlar = re.sub(r"\s+", " ", open(DLAR).read())
check("D6 the HTTP driver's excuse is written down where the next reader will find it",
      "DELIBERATELY NOT GIVEN THE SIGNED-IN EVIDENCE" in _dlar and "PROTECTED PAGE" in _dlar)
check("D7 …and it really does not take the verdict off a greeting",
      "signed_in_user" not in executable_text(DLAR))

# ── §E the declared wording is CONFIG, never code (RULE TWO) ─────────────────────────────────────
print("\n§E the tenant's own wording is a row, not a branch")

check("E1 the kind exists", pb.SIGNED_IN_KIND == "signed_in")
check("E2 it is NOT a verdict — the three verdicts each carry an operator sentence and this is no failure",
      pb.SIGNED_IN_KIND not in pb.VERDICT_KINDS and pb.verdict_note(pb.SIGNED_IN_KIND) == "")
check("E3 …and it IS a kind of the table, so an enumeration of the vocabulary finds it",
      pb.SIGNED_IN_KIND in pb.MARKER_KINDS and set(pb.VERDICT_KINDS) < set(pb.MARKER_KINDS))
check("E4 it is read through the loader the other three kinds already use — no sibling reader",
      "def load_markers" in BACKOFF_EXEC
      and len(re.findall(r"""table\(['"]portal_block_marker['"]\)""", BACKOFF_EXEC)) == 1,
      repr(re.findall(r"""table\(['"][a-z_]+['"]\)""", BACKOFF_EXEC)))
# ONE FACT, ONE HOME, DEREFERENCED. The house words live in login_outcome; a copy here would answer
# the same question a second way the next time either is edited.
check("E5 the house signed-in words are DEREFERENCED, not copied into the marker module",
      "DEFAULT_SIGNED_IN_MARKERS" in BACKOFF_EXEC)
_copies = [w for w in lo.DEFAULT_SIGNED_IN_MARKERS if ('"%s"' % w) in BACKOFF_EXEC
           or ("'%s'" % w) in BACKOFF_EXEC]
check("E6 …and not one of them is spelled out there as a literal", not _copies, repr(_copies))
check("E7 no portal, carrier or tenant is named in the loader (RULE TWO)",
      not re.search(r"\b(boost|epay|ups|cloudflare|vidapay|b2bsoft|verizon|total)\b",
                    function_source(BACKOFF, "load_markers").split('"""')[-1], re.I))


class _Client:
    """The smallest thing `load_markers` reads. DB-free."""

    def __init__(self, rows):
        self._rows = rows

    def schema(self, *_a):
        return self

    def table(self, *_a):
        return self

    def select(self, *_a):
        return self

    def in_(self, *_a):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        return type("R", (), {"data": list(self._rows)})()


ORG = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


def _row(org, marker, kind="signed_in", enabled=True, processor=None):
    return {"org_id": org, "processor": processor, "marker": marker, "kind": kind,
            "enabled": enabled}


check("E8 a tenant's declared wording is what it gets",
      pb.load_markers(_Client([_row(ORG, "multi-door owner portal")]), ORG,
                      kind=pb.SIGNED_IN_KIND) == ["multi-door owner portal"])
check("E9 …and declaring it changes the answer, which is the point of declaring it",
      lo.verdict(form_present=False, visible_text="Multi-Door Owner Portal",
                 signed_in_markers=("multi-door owner portal",))["outcome"] == lo.SIGNED_IN)
check("E10 a signed-in row is NOT served to the refusal question, and vice versa",
      pb.load_markers(_Client([_row(ORG, "multi-door owner portal")]), ORG,
                      kind=pb.REFUSAL_KIND) == list(pb.DEFAULT_REFUSAL_MARKERS)
      and "you shall not pass" not in pb.load_markers(
          _Client([_row(ORG, "you shall not pass", kind="refusal")]), ORG, kind=pb.SIGNED_IN_KIND))
check("E11 a row an operator switched OFF is not served",
      pb.load_markers(_Client([_row(ORG, "switched off", enabled=False)]), ORG,
                      kind=pb.SIGNED_IN_KIND) == list(lo.DEFAULT_SIGNED_IN_MARKERS))
check("E12 nothing declared falls back to the house words, dereferenced from their one home",
      pb.load_markers(_Client([]), ORG, kind=pb.SIGNED_IN_KIND)
      == list(lo.DEFAULT_SIGNED_IN_MARKERS))
check("E13 an unreachable database never raises — it reads as 'nothing declared'",
      pb.load_markers(object(), ORG, kind=pb.SIGNED_IN_KIND)
      == list(lo.DEFAULT_SIGNED_IN_MARKERS))
check("E14 a row that predates the kind column is still a THROTTLE row, not a signed-in one",
      pb.load_markers(_Client([{"org_id": ORG, "processor": None, "marker": "slow down",
                                "enabled": True}]), ORG, kind=pb.SIGNED_IN_KIND)
      == list(lo.DEFAULT_SIGNED_IN_MARKERS))

# ── §F the 'or None' rule: an empty declaration must not switch the words off ────────────────────
print("\n§F an empty declaration means 'house policy', never 'no words count'")

check("F1 login_outcome reads an EMPTY list as 'no words count' — the trap this rule avoids",
      lo.signed_in_said("Dashboard", markers=()) is None
      and lo.signed_in_said("Dashboard") == "dashboard")
DECL = function_source(EPAY, "_declared_signed_in_markers")
check("F2 so the driver's loader converts empty to None", re.search(r"\bor None\b", DECL) is not None, DECL)
check("F3 …and never raises: a driver cannot be stopped by a config read",
      "except Exception" in DECL and DECL.count("return None") >= 2)
check("F4 the rule is written down where the next editor will see it",
      "or None" in DECL and "is not tidying" in DECL)

# ── §G EVERY TENANT (owner directive 2026-10-30) ─────────────────────────────────────────────────
print("\n§G every tenant, measured against its own declaration")

HOUSE = [_row(pb.HOUSE_ORG, "owner portal")]
check("G1 a tenant with no rows of its own inherits the HOUSE row",
      pb.load_markers(_Client(HOUSE), OTHER, kind=pb.SIGNED_IN_KIND) == ["owner portal"])
check("G2 …and its OWN row wins, which is what per-org config means",
      pb.load_markers(_Client(HOUSE + [_row(OTHER, "my own shell")]), OTHER,
                      kind=pb.SIGNED_IN_KIND) == ["my own shell"])
# The fake client below deliberately does NOT honour `.in_("org_id", ...)`, so this asks the
# question the database would normally answer for us: if a third tenant's row ever reaches the
# loader, does it serve those words to somebody else? Writing this check is what found that it did.
check("G3 one tenant's words NEVER leak into another tenant's read, even if the query widens",
      pb.load_markers(_Client(HOUSE + [_row(ORG, "cellfonz words")]), OTHER,
                      kind=pb.SIGNED_IN_KIND) == ["owner portal"],
      repr(pb.load_markers(_Client(HOUSE + [_row(ORG, "cellfonz words")]), OTHER,
                           kind=pb.SIGNED_IN_KIND)))
check("G4 the EVIDENCE half needs no declaration at all, so it serves every tenant equally",
      all(lo.verdict(form_present=False, visible_text="Hi %s" % u, signed_in_user=u)["outcome"]
          == lo.SIGNED_IN
          for u in ("owner@tenant-a.example", "manager@tenant-b.example", "ops@tenant-c.example")))
check("G5 a per-processor row applies only to that processor, so two portals can differ",
      pb.load_markers(_Client([_row(ORG, "portal a", processor="alpha")]), ORG, "alpha",
                      kind=pb.SIGNED_IN_KIND) == ["portal a"]
      and pb.load_markers(_Client([_row(ORG, "portal a", processor="alpha")]), ORG, "beta",
                          kind=pb.SIGNED_IN_KIND) == list(lo.DEFAULT_SIGNED_IN_MARKERS))

# ── §H the migration and the code agree ─────────────────────────────────────────────────────────
print("\n§H the migration says what the code believes")

sql = open(MIG).read()
check("H1 the check constraint allows exactly the kinds the code knows",
      all(("'%s'" % k) in sql for k in pb.MARKER_KINDS))
check("H2 it is idempotent", "drop constraint if exists" in sql)
check("H3 it carries a REVERT note", "-- REVERT:" in sql)
check("H4 it grants what it touches, and to service_role ONLY — the browser never reads this table",
      "to service_role" in sql
      and not re.search(r"^\s*grant\b(?!.*to service_role)", sql, re.I | re.M))
check("H5 it declares itself not money-touching", "NOT MONEY-TOUCHING" in sql)
check("H6 it seeds NO signed-in phrase, because those have one home in code",
      not re.search(r"insert\s+into", sql, re.I))
check("H7 nothing existing changes meaning: no row is deleted or re-pointed",
      not re.search(r"^\s*(delete|update)\s", sql, re.I | re.M))

# ── §I the old shape cannot come back ───────────────────────────────────────────────────────────
print("\n§I the guess cannot return")

check("I1 the vendor-name-in-the-title test is gone and stays gone",
      not re.search(r"\.title\b", MOD_EXEC) and "document.title" not in MOD_EXEC)
check("I2 the house word list has ONE home",
      MOD_EXEC.count("DEFAULT_SIGNED_IN_MARKERS = ") == 1
      and "DEFAULT_SIGNED_IN_MARKERS = " not in BACKOFF_EXEC
      and "DEFAULT_SIGNED_IN_MARKERS = " not in EPAY_EXEC)
check("I3 no driver decides 'we are in' for itself, beside the one home",
      not any(re.search(r"(logout|my account)['\"]?\s*(in|==)", executable_text(os.path.join(MODULES, d)))
              for d in ("epay_sweep.py", "dlar_sweep.py", "vidapay_sweep.py")))
check("I4 the decision is still taken off VISIBLE text, never markup",
      "content()" not in function_source(EPAY, "_login_verdict"))
check("I5 no portal is named in the one home (RULE TWO)",
      not re.search(r"\b(boost|epay|multi-door|cellularservices)\b", MOD_EXEC, re.I))
check("I6 the loader is reachable only through the one table's one reader",
      len(re.findall(r"def load_markers", BACKOFF_EXEC)) == 1)

print("\n%d passed, %d failed" % (_passed, _failed))
if _failed:
    print("\nFAILED — a successful login must be recognised from evidence, with a tenant's own "
          "wording a ROW rather than a code change.")
    sys.exit(1)
print("\nOK — the page showing us our own name is the evidence; a tenant's wording is config.")
