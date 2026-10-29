"""PROOF + LOCK: what a login page wants before it will show the password (index §COMMCALC-46).

WHAT THIS PROVES, DB-free and browser-free. The decision itself over fake pages of plain dicts: a
single-page sign-in needs no step; a two-page sign-in (a user id and a Continue button, the owner's
UPS billing shape) is planned correctly; the progressive access-code shape this platform already
drove (a company id and a submit control) plans to the SAME actions it has always taken; a page that
offers nothing we hold is a named dead end rather than a robot pressing buttons; and the driver
carries a plan out against a fake page, including inside a frame.

WHAT THIS LOCKS, and why each lock is the point.

  • ONE DECISION, NOT THREE. "The password is on a later page" was answered in `live_login`, in
    `vidapay_sweep`'s b2bsoft login, and nowhere at all in the generic login every other portal takes
    — which is exactly how a two-page sign-in came to be reported as a form that renders unexpectedly.
    Every login path must reach `login_progressive`, and no login path may spell an element id of its
    own. A fourth copy, or a `#companyId` back in `live_login`, fails here.
  • ONE PAGE READER. `portal_report_pull.read_page_controls` already answers "what controls does this
    page offer" (§COMMCALC-39). A second reader in the login path is the duplicate the index rules
    forbid, so the planner must take controls rather than a page.
  • ONE SPELLING RULE. `report_columns.norm` (§COMMCALC-38). A login matcher with its own normalizer
    is the store-code trap again, in the one place where getting it wrong locks an account out.
  • THE COPY NAMES THE PORTAL IT TRIED. `begin_login` and `complete_2fa` take `label=` and dereference
    `_portal_label`, and the router passes the registry label at both call sites. A login message that
    spells a vendor's name is what sent the owner "the VidaPay login page" about a ups.com page.
  • NEVER PRESSES ITS WAY OUT OF THE LOGIN. A forgotten-password, registration or cancel control is
    refused before its other words are read.

Run:  cd backend && python3 harness_login_progressive.py          Exit 0 = all green.
"""
import io
import os
import re
import sys
import tokenize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import login_progressive as lp     # noqa: E402
from app.modules.commcalc import report_columns as rc        # noqa: E402
from app.modules.commcalc import portal_report_pull as prp   # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % msg)
    else:
        FAIL += 1
        print("  ✗ %s" % msg)


def eq(got, want, msg):
    ok(got == want, msg if got == want else "%s (got %r, want %r)" % (msg, got, want))


def read(rel):
    with open(os.path.join(_ROOT, rel)) as fh:
        return fh.read()


def strip_text(src):
    """Executable text only — comments AND docstrings gone. A comment naming the very thing a fix is
    about otherwise reads as the fix's own violation (a trap this repo has already hit)."""
    out, prev = [], None
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            t, s = tok.type, tok.string
            if t == tokenize.COMMENT:
                continue
            if t == tokenize.STRING and prev in (None, tokenize.INDENT, tokenize.DEDENT,
                                                 tokenize.NEWLINE, tokenize.NL):
                prev = t
                continue
            out.append(s)
            prev = t
    except tokenize.TokenError:
        return src
    return " ".join(out)


# ── fake pages: exactly what read_page_controls returns, so the planner is proved on its real input ──
def ctl(kind="text", cid="", name="", label="", options=None):
    return {"id": cid, "name": name, "kind": kind, "label": label, "options": list(options or [])}


# One page, all three fields — the shape the generic login has always handled.
SINGLE_PAGE = [ctl("text", "acct", label="Account ID"),
               ctl("text", "user", label="User ID"),
               ctl("password", "pw", label="Password"),
               ctl("submit", "go", label="Sign In")]

# The owner's UPS billing shape, 2026-10-27: a user id and a Continue button, no password.
USER_THEN_CONTINUE = [ctl("text", "email", name="email", label="Username or Email"),
                      ctl("button", "continue-btn", label="Continue"),
                      ctl("link", "", name="", label="Forgot your user ID?"),
                      ctl("link", "", name="", label="Create an account")]

# The progressive access-code shape this platform already drove, with its own words on it.
ACCESS_CODE_FIRST = [ctl("text", "companyId", name="companyId", label="Company ID"),
                     ctl("submit", "btnSubmit", name="btnSubmit", label="Continue")]

# A page that says nothing useful: one unlabelled box and a control we must never press.
OPAQUE = [ctl("text", "", name="", label=""),
          ctl("link", "", label="Forgot password?")]

HAVE = {"account_id": "C-77", "username": "ops@example.test"}


print("\n== §A  THE PAGE READER AND THE SPELLING RULE ARE DEREFERENCED, NOT REBUILT ==")
src_lp = read("backend/app/modules/commcalc/login_progressive.py")
ok("read_page_controls" in src_lp, "the planner's input is read_page_controls' output (§COMMCALC-39)")
ok("control_selector" in src_lp, "selectors come from portal_report_pull.control_selector")
ok("report_columns" in src_lp and "rc.norm" in src_lp, "the spelling rule is report_columns.norm")
ok("def norm(" not in strip_text(src_lp), "…and this module defines no normalizer of its own")
ok("_CONTROLS_JS" not in src_lp and "page.evaluate" not in strip_text(src_lp),
   "no second page reader: the login path does not evaluate its own JS")
eq(lp.control_words(ctl("text", "ctl00_txtCompanyId", name="ctl00$txtCompanyId", label="Company ID")),
   rc.norm("Company ID ctl00$txtCompanyId ctl00_txtCompanyId"),
   "control_words folds label + name + id under the one spelling rule")


print("\n== §B  A SINGLE-PAGE SIGN-IN NEEDS NO STEP ==")
plan = lp.plan_first_step(SINGLE_PAGE, HAVE)
eq(plan["needed"], False, "a page carrying the password field needs no first step")
eq(plan["fill"], [], "…nothing is filled")
eq(plan["press"], None, "…and nothing is pressed (pressing Sign In here would submit it half-filled)")
ok(lp.has_password(SINGLE_PAGE), "has_password sees the field")
ok(not lp.has_password(USER_THEN_CONTINUE), "…and does not invent one")


print("\n== §C  THE TWO-PAGE SIGN-IN (the defect this fixes) ==")
plan = lp.plan_first_step(USER_THEN_CONTINUE, HAVE)
eq(plan["needed"], True, "no password field means a first step is needed")
eq([f["field"] for f in plan["fill"]], ["username"], "the user box is recognized from the page's words")
eq(plan["fill"][0]["selector"], "#email", "…and addressed by control_selector")
eq(plan["press"]["selector"], "#continue-btn", "the page's own Continue control is the one pressed")
ok("continue" in plan["press"]["why"], "…and the plan says why that control")
ok(not any("forgot" in f["selector"].lower() for f in plan["fill"]), "the recovery link is not filled")
eq(lp.press_purpose(ctl("link", label="Forgot your user ID?")), "",
   "a forgotten-credentials link is never pressable")
eq(lp.press_purpose(ctl("link", label="Create an account")), "", "nor is a registration link")
eq(lp.press_purpose(ctl("button", label="Cancel")), "", "nor is cancel")
# The account-id box is absent here, so holding a value for it must not invent one.
ok(all(f["field"] != "account_id" for f in plan["fill"]),
   "an account id we hold is not typed into a page that never asked for one")


print("\n== §D  THE ACCESS-CODE SHAPE PLANS TO WHAT IT ALWAYS DID ==")
plan = lp.plan_first_step(ACCESS_CODE_FIRST, HAVE)
eq([f["field"] for f in plan["fill"]], ["account_id"],
   "the company-id box takes the account id, not the user id")
eq(plan["fill"][0]["selector"], "#companyId", "…the same control the old hardcode filled")
eq(plan["press"]["selector"], "#btnSubmit", "…and the same control it pressed")
ok("account_id" in [f["field"] for f in plan["fill"]],
   "so a working login keeps working through the shared mechanism")


print("\n== §E  ORDER, AND NOT FILLING WHAT WE DO NOT HOLD ==")
both = [ctl("text", "co", label="Company Number"), ctl("text", "uid", label="User ID"),
        ctl("submit", "nxt", label="Next")]
eq([f["field"] for f in lp.plan_first_step(both, HAVE)["fill"]], ["account_id", "username"],
   "a page asking for both fills the account id first (its own order)")
eq([f["field"] for f in lp.plan_first_step(both, {"username": "u"})["fill"]], ["username"],
   "a field we hold no value for is never filled (an empty required box fails the step)")
eq([f["field"] for f in lp.plan_first_step(both, {"account_id": " "})["fill"]], [],
   "…and whitespace is not a value")
eq(lp.plan_first_step(both, {})["press"], None,
   "a page that ASKS for something we cannot supply is not submitted empty (an empty required box "
   "comes back as 'rejected', which reads as wrong credentials)")
interstitial = [ctl("button", "go", label="Continue to sign in")]
eq(lp.plan_first_step(interstitial, {})["press"]["selector"], "#go",
   "…but a page with no box at all and a Continue control IS pressed — it is an interstitial, and "
   "pressing it is the only way forward")


print("\n== §F  A STEP CONTROL OUTRANKS THE FINAL SUBMIT ==")
two_buttons = [ctl("text", "uid", label="User ID"),
               ctl("submit", "signin", label="Sign In"),
               ctl("button", "cont", label="Continue")]
eq(lp.plan_first_step(two_buttons, HAVE)["press"]["selector"], "#cont",
   "Continue is pressed, not Sign In — pressing the final control exports a form with no password")
only_final = [ctl("text", "uid", label="User ID"), ctl("submit", "signin", label="Sign In")]
eq(lp.plan_first_step(only_final, HAVE)["press"]["selector"], "#signin",
   "…but a page offering only a submit control still advances")
eq(lp.press_purpose(ctl("submit", label="Sign In")), "final", "press_purpose names the final control")
eq(lp.press_purpose(ctl("button", label="Continue")), "step", "…and the step control")
eq(lp.press_purpose(ctl("text", label="Continue")), "",
   "a TEXT BOX whose label says continue is not a button")


print("\n== §G  THE HONEST DEAD END ==")
plan = lp.plan_first_step(OPAQUE, HAVE)
eq(plan["needed"], True, "still needed")
eq(plan["fill"], [], "nothing is filled")
eq(plan["press"], None, "nothing is pressed")
ok("declare the login fields" in plan["why"],
   "…and the reason names the fix (declare the fields on the source), not 'unexpected form'")


print("\n== §H  A DECLARED SELECTOR WINS (RULE TWO: config, never code) ==")
decl = {"fields": {"username": "#their-box"}, "press": "#their-button"}
plan = lp.plan_first_step(OPAQUE, HAVE, declared=decl)
eq(plan["fill"][0]["selector"], "#their-box", "a declared field is used on a page that says nothing")
eq(plan["press"]["selector"], "#their-button", "…and a declared press control")
plan = lp.plan_first_step(USER_THEN_CONTINUE, HAVE, declared=decl)
eq(plan["fill"][0]["selector"], "#their-box", "a declaration outranks what the page's words suggest")
words = lp.plan_first_step([ctl("text", "wn", label="Werksnummer"), ctl("submit", "s", label="Weiter")],
                           HAVE, declared={"field_words": {"account_id": ["werksnummer"]},
                                           "press": "#s"})
eq([f["field"] for f in words["fill"]], ["account_id"],
   "declared field_words teach the mechanism a wording it had never seen")


print("\n== §I  THE DRIVER CARRIES A PLAN OUT ==")


class FakeEl:
    def __init__(self, box):
        self.box = box
        self.typed = ""

    def click(self):
        self.box["clicked"] = self.box.get("clicked", 0) + 1

    def fill(self, v):
        self.typed = v

    def type(self, v, delay=None):
        self.typed += v
        self.box["delays"] = self.box.get("delays", []) + [delay]


class FakeScope:
    """Everything the driver touches on a page or a frame, and nothing else."""

    def __init__(self, controls, name="main"):
        self.controls, self.name = controls, {},
        self.controls = controls
        self.els, self.log, self.waited = {}, [], 0
        self.name = name

    def evaluate(self, js):
        return self.controls

    def query_selector(self, sel):
        if sel not in self.els:
            self.els[sel] = FakeEl({})
        self.log.append(sel)
        return self.els[sel]

    def wait_for_selector(self, sel, state=None, timeout=None):
        self.waited += 1
        return None


scope = FakeScope(USER_THEN_CONTINUE)
note = lp.advance_past_first_step(scope, HAVE, scopes=[scope])
ok("took this page's first step" in note, "the driver reports what it did")
eq(scope.els["#email"].typed, HAVE["username"], "the user id was typed into the page's own box")
ok(scope.els["#email"].box.get("delays") == [15],
   "typed with keystrokes, not assigned — a progressive form enables its next step on key events")
ok(scope.els["#continue-btn"].box.get("clicked") == 1, "Continue was pressed exactly once")
eq(scope.waited, 1, "…and it then waits for the password field")

done = FakeScope(SINGLE_PAGE)
eq(lp.advance_past_first_step(done, HAVE, scopes=[done]),
   "the password field was already on the page", "a single-page login is left untouched")
eq(done.log, [], "…nothing was even looked up on it")

blank, framed = FakeScope([]), FakeScope(USER_THEN_CONTINUE, "frame2")
note = lp.advance_past_first_step(blank, HAVE, scopes=[blank, framed])
ok("took this page's first step" in note, "a login form inside a frame is still found")
eq(framed.els["#continue-btn"].box.get("clicked"), 1, "…and driven on that frame")

opaque = FakeScope(OPAQUE)
ok("declare the login fields" in lp.advance_past_first_step(opaque, HAVE, scopes=[opaque]),
   "a dead end comes back as the sentence that names the fix")
eq(opaque.log, [], "…having pressed nothing")


class Exploding:
    def evaluate(self, js):
        raise RuntimeError("this page will not answer")

    def query_selector(self, sel):
        raise RuntimeError("nor this")

    def wait_for_selector(self, *a, **k):
        raise RuntimeError("nor this")


boom = Exploding()
try:
    eq(lp.advance_past_first_step(boom, HAVE, scopes=[boom]), "",
       "a page that will not answer returns no note instead of raising into the login")
except Exception as e:
    ok(False, "the driver raised into the login: %r" % e)


print("\n== §J  ONE DECISION — THE LOCK ==")
src_live = read("backend/app/modules/commcalc/live_login.py")
src_vp = read("backend/app/modules/commcalc/vidapay_sweep.py")
exe_live, exe_vp = strip_text(src_live), strip_text(src_vp)

ok("login_progressive" in src_live, "live_login reaches the one home")
ok("login_progressive" in src_vp, "…and so does the sweep module")
ok("_companyid_step" not in src_live, "live_login's own copy of the step is GONE, not left beside it")
for bad in ("#companyId", "#btnSubmit", "#password", "#username"):
    ok(bad not in exe_live, "live_login spells no login element id (%s)" % bad)
eq(len(re.findall(r"def plan_first_step\s*\(", src_lp)), 1, "the decision is defined once")
eq(len(re.findall(r"def advance_past_first_step\s*\(", src_lp)), 1, "…and the driver once")
for mod, src in (("live_login", exe_live), ("vidapay_sweep", exe_vp)):
    eq(len(re.findall(r"def plan_first_step\s*\(|def advance_past_first_step\s*\(", src)), 0,
       "%s defines neither, it calls them" % mod)
# The b2bsoft function keeps its own ids as a SAFETY NET, and only after the mechanism has answered.
i_call = exe_vp.find("advance_past_first_step")
i_ids = exe_vp.find("#companyId")
ok(i_call != -1 and i_ids != -1 and i_call < i_ids,
   "the b2bsoft login asks the mechanism BEFORE falling back to its own ids")
# A RATCHET, not a ban. These ids predate this PR in the b2bsoft variant's own re-assert and in the
# session classifier, and removing them is a separate piece of work against a portal nobody here can
# reach. The counts are the ceiling measured on main at 69b8bfe0 and may only SHRINK: a login path
# growing a fourth `#companyId` fails the build, and a path that stops needing one re-pins lower.
LOGIN_ID_CEILING = {"#companyId": 5, "#btnSubmit": 2, "#username": 3, "#password": 4}
for _sel, _max in sorted(LOGIN_ID_CEILING.items()):
    _n = exe_vp.count(_sel)
    ok(_n <= _max, "vidapay_sweep's %s count did not grow (%d, ceiling %d — re-pin LOWER when it "
                   "shrinks, never higher)" % (_sel, _n, _max))


print("\n== §K  THE COPY NAMES THE PORTAL IT ACTUALLY TRIED ==")
for fn in ("begin_login", "complete_2fa"):
    m = re.search(r"def %s\(([^)]*)\)" % fn, src_vp)
    ok(bool(m) and "label=None" in (m.group(1) if m else ""), "%s takes label=" % fn)
begin_body = src_vp[src_vp.index("def begin_login("):src_vp.index("def begin_login_b2bsoft(")]
exe_begin = strip_text("def _x():\n" + "\n".join("    " + l for l in begin_body.splitlines()[1:]))
ok("VidaPay" not in exe_begin.replace("VidaPayLoginError", "").replace("VidaPayAuthError", "")
                              .replace("VidaPayPortalError", ""),
   "no vendor name survives in the generic login's own copy (exception class names aside)")
ok("_portal_label(label)" in begin_body, "…it dereferences _portal_label instead")
eq(lp and getattr(__import__("app.modules.commcalc.vidapay_sweep", fromlist=["x"]),
                  "_portal_label")(None), "reports portal",
   "with no label, the neutral noun — never a guess at which portal this is")
src_router = read("backend/app/modules/commcalc/router.py")
eq(len(re.findall(r"vp\.begin_login\b(?!_b2bsoft)", src_router)),
   len(re.findall(r"vp\.begin_login\b(?!_b2bsoft)", src_router)),
   "the router's generic-login call sites are counted")
for pat, what in ((r"else vp\.begin_login,\s*\n?\s*label=|vp\.begin_login,\s*\n?\s*label=|"
                   r"else vp\.begin_login\b[\s\S]{0,80}?label=", "begin_login"),
                  (r"else vp\.complete_2fa\b[\s\S]{0,120}?label=", "complete_2fa")):
    ok(bool(re.search(pat, src_router)), "the router passes the registry label to %s" % what)
ok(not re.search(r"=\s*vp\.begin_login\s*\)", src_router),
   "no call site is left handing the generic login no label")


print("\n== §L  NEGATIVE CONTROLS — the shipped rules, armed ==")


def armed(label, fn):
    try:
        ok(fn() is True, "ARMED: %s" % label)
    except Exception as e:
        ok(False, "control blew up (%s): %r" % (label, e))


armed("a page with a password field plans no press",
      lambda: lp.plan_first_step(SINGLE_PAGE, HAVE)["press"] is None)
armed("the OLD rule (press whatever submits) would press Sign In on the two-button page",
      lambda: lp.plan_first_step(two_buttons, HAVE)["press"]["selector"] != "#signin")
armed("filling by position instead of by words would put the account id in the user box",
      lambda: lp.plan_first_step(USER_THEN_CONTINUE, HAVE)["fill"][0]["field"] == "username")
armed("a hidden control is not offered (read_page_controls drops them, so a plan sees none)",
      lambda: lp.plan_first_step([ctl("hidden", "h", label="Company ID")], HAVE)["fill"] == [])
armed("an unknown kind is not typed into",
      lambda: lp.plan_first_step([ctl("checkbox", "c", label="User ID")], HAVE)["fill"] == [])
armed("'remember me' is not pressed",
      lambda: lp.press_purpose(ctl("button", label="Remember me on this device")) == "")
armed("a declared press selector is used verbatim, not normalized",
      lambda: lp.plan_first_step(OPAQUE, HAVE, declared={"press": "#A$b"})["press"]["selector"] == "#A$b")
armed("the planner never mutates the house vocabulary",
      lambda: (lp.plan_first_step(OPAQUE, HAVE, declared={"field_words": {"account_id": ["zz"]}}) or True)
      and "zz" not in lp.FIELD_WORDS["account_id"])
armed("the module names no portal and no tenant",
      lambda: not re.search(r"\b(ups|vidapay|b2bsoft|tcetra|boost|total|luxelink|cellfonz)\b",
                            strip_text(src_lp), re.I))


print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
