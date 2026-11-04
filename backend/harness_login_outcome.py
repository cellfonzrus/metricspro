#!/usr/bin/env python3
"""A LOGIN OUTCOME IS NEVER READ OFF MARKUP AGAIN (index §COMMCALC-50.9).

DB-free and browser-free. The defect this locks is not a wrong word list — it is a DECISION TAKEN
OFF `page.content()`. The owner reported on 2026-10-31 that three carrier feeds had not refreshed
since 2026-10-09; the scheduler had fired 1,665 times and 1,056 consecutive runs reported "epay
login failed — credentials rejected" from this:

    body = (page.content() or "").lower()
    if any(w in body for w in ("invalid", "incorrect", "failed")) and "passwordinput" in body:

`page.content()` serialises inline scripts, templates, comments and hidden nodes, so a single-page
application that merely BUNDLES the word "invalid" read as having said it — and a human check and a
changed sign-in flow were both reported as a wrong password, sending every operator who looked to
re-type a credential that may well have been correct.

It fails the build if:
  • a driver takes a login decision off `page.content()` / markup again;
  • a driver stops dereferencing `commcalc/login_outcome` for the outcome;
  • a second home for "what happened to this login" appears beside it;
  • the credential-problem test stops being an ALLOWLIST;
  • an outcome loses its operator sentence;
  • a portal/tenant NAME re-enters the decision (RULE TWO), in the module or in the driver's verdict;
  • the evidence arguments grow defaults, so a caller that has not looked at the page can get an
    answer out of the one home.

Run: python3 harness_login_outcome.py
"""
import ast
import io
import os
import re
import sys
import tokenize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import login_outcome as lo        # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "app/modules/commcalc/login_outcome.py")
EPAY = os.path.join(HERE, "app/modules/commcalc/epay_sweep.py")

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

    Both are stripped on the house precedent that a comment naming the defect a fix is about
    otherwise reads as that fix's own violation (2026-10-30). A scan of prose is not a scan.
    """
    src = open(path).read()
    kept = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        kept.append(tok[:2])
    tree = ast.parse(tokenize.untokenize(kept))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body.pop(0)
    return ast.unparse(tree)


MOD_EXEC = executable_text(MOD)
EPAY_EXEC = executable_text(EPAY)
EPAY_SRC = open(EPAY).read()


def function_source(path, name):
    """One function's executable text, by name. '' when it is gone — which is itself a failure."""
    src = open(path).read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            seg = ast.get_source_segment(src, node) or ""
            try:
                return executable_text_from_string(seg)
            except Exception:
                return seg
    return ""


def executable_text_from_string(src):
    import textwrap
    src = textwrap.dedent(src)
    kept = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        kept.append(tok[:2])
    tree = ast.parse(tokenize.untokenize(kept))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body.pop(0)
    return ast.unparse(tree)


# ── §A the regression itself: the exact page that produced 1,056 identical failures ─────────────
print("\n§A the shape that produced the outage (house portal, 2026-10-09 -> 2026-10-31)")

SPA_VISIBLE = "Portal Commissions Reports Logout"
SPA_MARKUP = ('<script>var M={invalid:"Invalid user name or password",failed:"Login failed"};'
              '</script><div id="passwordInput" style="display:none"></div>Logout')

check("A1 the word 'invalid' in MARKUP is not a refusal",
      lo.rejection_said(SPA_VISIBLE) is None)
v = lo.verdict(form_present=False, visible_text=SPA_VISIBLE)
check("A2 the signed-in shell reads as signed in", lo.signed_in(v["outcome"]), repr(v))
check("A3 …and is therefore NOT a credential problem",
      not lo.is_credential_problem(v["outcome"]))
check("A4 the old conjunction (an 'invalid' + a password node) decides nothing",
      "invalid" in SPA_MARKUP.lower() and "passwordinput" in SPA_MARKUP.lower()
      and lo.verdict(form_present=False, visible_text=SPA_VISIBLE)["outcome"] != lo.REJECTED)
check("A5 a GENUINE refusal beside a live form is still caught",
      lo.verdict(form_present=True,
                 visible_text="Sign in. Invalid user name or password.")["outcome"] == lo.REJECTED)


# ── §B no login decision is taken off markup, in any driver ─────────────────────────────────────
print("\n§B the class: a page judged by what it is DOING, never by how it is BUILT")

LOGIN_FNS = ("_login", "login_form_present", "visible_text", "_page_verdict", "_login_verdict")
for fn in LOGIN_FNS:
    body = function_source(EPAY, fn)
    check("B:%s exists in the driver" % fn, bool(body))
    check("B:%s never reads page.content()" % fn,
          ".content()" not in body, body[:200])

check("B6 the driver reads the page through document.body.innerText",
      "innerText" in EPAY_SRC)
check("B7 the replaced markup predicate is GONE from the driver",
      not re.search(r"content\(\)[^\n]*lower\(\)", EPAY_EXEC))
check("B8 the driver has no inline rejection word list of its own",
      not re.search(r"\(\s*[\"']invalid[\"']\s*,\s*[\"']incorrect[\"']", EPAY_EXEC))


# ── §C the driver dereferences the one home, and there is only one ──────────────────────────────
print("\n§C one home, dereferenced — never copied")

check("C1 the driver imports login_outcome", "login_outcome" in EPAY_SRC)
check("C2 the driver asks it for the outcome", "_lo.verdict(" in EPAY_EXEC)
check("C3 the driver asks it whether we are in (never its own test)",
      "_lo.signed_in(" in EPAY_EXEC)
check("C4 the driver takes the operator sentence from it (never its own prose)",
      "_lo.message(" in EPAY_EXEC)
check("C5 'are we signed in' is not re-spelled in the driver",
      not re.search(r"(logout|sign out|dashboard)[\"'],", EPAY_EXEC.lower()),
      "a signed-in marker list reappeared in the driver")

# A second home would be another module answering the same question with its own word list.
MODULES = os.path.join(HERE, "app/modules/commcalc")
second = []
for name in sorted(os.listdir(MODULES)):
    if not name.endswith(".py") or name == "login_outcome.py":
        continue
    try:
        txt = executable_text(os.path.join(MODULES, name))
    except Exception:
        continue
    if re.search(r"credentials?\s+rejected", txt, re.I):
        second.append(name)
check("C6 no module spells its own 'credentials rejected' verdict", not second, str(second))


# ── §D the credential-problem test is an ALLOWLIST ──────────────────────────────────────────────
print("\n§D only a rejection sends anybody to re-type a password")

check("D1 a rejection is a credential problem", lo.is_credential_problem(lo.REJECTED))
for o in (lo.SIGNED_IN, lo.HUMAN_CHECK, lo.REFUSED, lo.CHANGED, lo.UNKNOWN):
    check("D:%s is NOT a credential problem" % o, not lo.is_credential_problem(o))
check("D2 an UNRULED outcome reads as not a credential problem (allowlist, not denylist)",
      not lo.is_credential_problem("invented_later") and not lo.is_credential_problem(None))
check("D3 the allowlist is a membership test, not a keyword search",
      "_CREDENTIAL_OUTCOMES" in MOD_EXEC and " in _CREDENTIAL_OUTCOMES" in MOD_EXEC)


# ── §E every outcome carries its own remedy ─────────────────────────────────────────────────────
print("\n§E five outcomes, five remedies — the thing that wore one coat")

for o in lo.ALL_OUTCOMES:
    n = lo.note(o)
    if o == lo.SIGNED_IN:
        check("E:%s has no remedy (nothing is wrong)" % o, n == "")
    else:
        check("E:%s carries an operator sentence" % o, isinstance(n, str) and len(n) > 40)
check("E1 note() never raises and never returns None",
      all(lo.note(b) == "" for b in (None, "", 0)))
check("E2 the remedies are DISTINCT (not one sentence five times)",
      len({lo.note(o) for o in lo.FAILURE_OUTCOMES}) == len(lo.FAILURE_OUTCOMES))
check("E3 the two that are not about the password say so",
      "password is not implicated" in lo.note(lo.HUMAN_CHECK)
      and "password is not implicated" in lo.note(lo.REFUSED))
check("E4 a changed flow names a developer, not a credential",
      "developer" in lo.note(lo.CHANGED)
      and "will not help" in lo.note(lo.CHANGED))
check("E5 the status line quotes the portal's own words",
      '"account is locked"' in lo.message(lo.REJECTED, marker="account is locked"))


# ── §F the evidence is REQUIRED, never defaulted ────────────────────────────────────────────────
print("\n§F a caller that has not looked at the page gets no answer")

import inspect                                                              # noqa: E402
sig = inspect.signature(lo.verdict)
for req in ("form_present", "visible_text"):
    p = sig.parameters.get(req)
    check("F:%s is a parameter" % req, p is not None)
    if p is not None:
        check("F:%s is keyword-only" % req, p.kind == inspect.Parameter.KEYWORD_ONLY)
        check("F:%s has NO default" % req, p.default is inspect.Parameter.empty)
ok = False
try:
    lo.verdict()
except TypeError:
    ok = True
check("F1 verdict() with no evidence raises", ok)


# ── §G RULE TWO — no portal or tenant name decides anything ─────────────────────────────────────
print("\n§G RULE TWO: config, never code")

BANNED = ("epay", "vidapay", "boost", "cellfonz", "luxelink", "vzone", "t-cetra", "b2bsoft",
          "total access", "owner portal", "carrierportal")
for w in BANNED:
    check("G:%r does not appear in the one home's executable text" % w, w not in MOD_EXEC.lower())
vd = function_source(EPAY, "_login_verdict") + function_source(EPAY, "_login")
for w in ("owner portal", "carrierportal"):
    check("G:%r no longer decides the driver's outcome" % w, w not in vd.lower())
check("G1 the marker vocabularies are POLICY lists a caller may override",
      "signed_in_markers" in MOD_EXEC and "rejection_markers" in MOD_EXEC)


# ── §H the refusal vocabulary recognises the appliance page measured on the live host ───────────
print("\n§H the edge appliance's own rejection page is recognised")

from app.modules.commcalc import portal_backoff as pb                        # noqa: E402
F5 = ("Request Rejected. The requested URL was rejected. Please consult with your administrator. "
      "Your support ID is: <13809694667310282925>")
pv = pb.page_verdict(F5)
check("H1 it is recognised at all (it matched nothing before)", pv is not None, repr(pv))
check("H2 …as a REFUSAL", (pv or {}).get("kind") == pb.REFUSAL_KIND, repr(pv))
lv = lo.verdict(form_present=False, visible_text=F5, page_verdict=pv)
check("H3 the login outcome is REFUSED, not a rejected credential",
      lv["outcome"] == lo.REFUSED, repr(lv))
check("H4 …so nobody is sent to re-type a password for it",
      not lo.is_credential_problem(lv["outcome"]))


# ── §I controls: the fix does not over-reach ────────────────────────────────────────────────────
print("\n§I controls")

check("I1 a challenge is DISBELIEVED while the form is on screen (§COMMCALC-50.8's rule)",
      lo.verdict(form_present=True, visible_text="Sign in. Please verify you are human.",
                 page_verdict={"kind": "challenge", "marker": "captcha"})["outcome"] == lo.CHANGED)
check("I2 …and BELIEVED when it is not",
      lo.verdict(form_present=False, visible_text="Please verify you are human",
                 page_verdict={"kind": "challenge", "marker": "verify you are human"}
                 )["outcome"] == lo.HUMAN_CHECK)
check("I3 a live form that is complaining beats a stray shell marker",
      lo.verdict(form_present=True,
                 visible_text="Logout Sign in Your password is incorrect")["outcome"] == lo.REJECTED)
check("I4 matching is case-insensitive on both sides",
      lo.rejection_said("INVALID USER NAME") == "invalid")
check("I5 an empty page says UNKNOWN rather than guessing",
      lo.verdict(form_present=False, visible_text="")["outcome"] == lo.UNKNOWN)
check("I6 every failure outcome is in ALL_OUTCOMES and none is signed_in",
      set(lo.FAILURE_OUTCOMES) | {lo.SIGNED_IN} == set(lo.ALL_OUTCOMES)
      and not any(lo.signed_in(o) for o in lo.FAILURE_OUTCOMES))


# ── §J THE SIBLING: every driver that answers this question dereferences the same home ──────────
print("\n§J the sibling driver (no browser, plain HTTP) names its outcome from the same home")

DLAR = os.path.join(HERE, "app/modules/commcalc/dlar_sweep.py")
DLAR_EXEC = executable_text(DLAR)
check("J1 it imports login_outcome", "login_outcome" in open(DLAR).read())
check("J2 it takes the operator sentence from the one home", "_lo.message(" in DLAR_EXEC)
check("J3 it asks the one home for the outcome", "_lo.verdict(" in DLAR_EXEC)
check("J4 its own 'credentials rejected' sentence is GONE",
      not re.search(r"credentials?\s+rejected", DLAR_EXEC, re.I))
check("J5 the structural half comes from the one home too (no second password-input regex)",
      "_lo.form_in_html(" in DLAR_EXEC
      and not re.search(r"type\s*=\s*.\\?[\"']?password", DLAR_EXEC))
check("J6 the readable half comes from the one home too",
      "_lo.visible_from_html(" in DLAR_EXEC)
check("J7 the sound probe is UNTOUCHED (a page that needs a session is still what proves it)",
      "/reports/dlar" in DLAR_EXEC and "status_code != 200" in DLAR_EXEC)

# The readers themselves: the bundled word must not survive into the readable text.
BUNDLED = ('<html><head><script>var M={invalid:"Invalid user name or password"};</script>'
           '<style>.x{content:"incorrect"}</style></head>'
           '<body><!-- failed --><div>Commissions Reports Logout</div></body></html>')
vis = lo.visible_from_html(BUNDLED)
check("J8 a <script>'s words do not survive into the readable text", "invalid" not in vis.lower(), vis)
check("J9 a <style>'s words do not either", "incorrect" not in vis.lower(), vis)
check("J10 an HTML comment's words do not either", "failed" not in vis.lower(), vis)
check("J11 …and what a person reads DOES survive", "Logout" in vis, vis)
check("J12 so the bundled-word page does not read as a rejection from HTML either",
      lo.verdict(form_present=lo.form_in_html(BUNDLED), visible_text=vis)["outcome"] == lo.SIGNED_IN)
check("J13 a real form in HTML is recognised structurally",
      lo.form_in_html('<form><input name="password" type="password"></form>'))
check("J14 …and the word 'password' in prose alone is not a form",
      not lo.form_in_html("<p>Please enter your password on the portal</p>"))
check("J15 a real refusal in HTML is still caught",
      lo.verdict(form_present=True,
                 visible_text=lo.visible_from_html(
                     '<form><input type="password"></form><p>Invalid credentials.</p>')
                 )["outcome"] == lo.REJECTED)
check("J16 entities are unescaped so the words match",
      "don't" in lo.visible_from_html("<p>don&#39;t</p>").lower())


# ── §K THE THIRD DRIVER: still-on-the-form is NAMED, not hedged ─────────────────────────────────
print("\n§K the live-login driver names which of the two it is, rather than asking the operator")

VIDA = os.path.join(HERE, "app/modules/commcalc/vidapay_sweep.py")
VIDA_SRC = open(VIDA).read()
VIDA_EXEC = executable_text(VIDA)
check("K1 it imports login_outcome", "login_outcome" in VIDA_SRC)
check("K2 it asks the one home for the outcome", "_lo.verdict(" in VIDA_EXEC)
check("K3 it takes the sentence from the one home", "_lo.message(" in VIDA_EXEC)
check("K4 the hedging prose is GONE (the operator is not asked to read a dict)",
      "if 'filled' shows your values" not in VIDA_EXEC
      and "the form didn't accept the fill" not in VIDA_EXEC)
check("K5 the human-check message still comes FIRST (§COMMCALC-11 keeps precedence)",
      re.search(r"blocked\s+or\s+_lo\.message\(", VIDA_EXEC) is not None)
check("K6 the evidence is the portal's own VISIBLE error region, not the markup",
      "_b2b_error(page)" in VIDA_EXEC and ".content()" not in
      (function_source(VIDA, "_b2b_error") or ""))
check("K7 no driver asserts a rejection without evidence any more",
      not re.search(r"[\"']Login rejected", VIDA_EXEC))

# All three PORTAL drivers, one home, no second copy of the sentence anywhere.
#
# EXCUSED BY NAME, with its reason: `router.py`'s mailbox sweep says "login rejected" for an IMAP
# `[AUTHENTICATIONFAILED]`. That is a different mechanism, not a second copy of this one — a mail
# server's response code is an AUTHORITATIVE statement from the protocol, there is no page, no form
# and nothing to misread. This module exists because a PAGE had to be interpreted. The excusal is
# pinned at one file and may only shrink.
MAILBOX_EXCUSED = {"router.py"}
copies = []
for name in sorted(os.listdir(MODULES)):
    if not name.endswith(".py") or name == "login_outcome.py" or name in MAILBOX_EXCUSED:
        continue
    try:
        txt = executable_text(os.path.join(MODULES, name))
    except Exception:
        continue
    if re.search(r"(credentials?\s+rejected|login\s+rejected)", txt, re.I):
        copies.append(name)
check("K8 no portal driver spells a login-rejection verdict of its own", not copies, str(copies))
check("K9 the mailbox excusal is pinned at one file (shrink-only)", len(MAILBOX_EXCUSED) == 1)
check("K10 …and it really is a protocol answer, not a page read",
      "AUTHENTICATIONFAILED" in open(os.path.join(MODULES, "router.py")).read())
for drv in ("epay_sweep.py", "dlar_sweep.py", "vidapay_sweep.py"):
    check("K:%s dereferences the one home" % drv,
          "login_outcome" in open(os.path.join(MODULES, drv)).read())


print("\n%d passed, %d failed" % (_passed, _failed))
if _failed:
    print("\nFAILED — a login decision must be read off what the page is DOING, from one home.")
    sys.exit(1)
print("\nOK — one home for a login outcome, read off structure and visible text, never markup.")
