"""Proof harness: AN INVISIBLE HUMAN CHECK IS STILL A CHECK (index §COMMCALC-11).

OWNER, 2026-10-15, on his first `pos_report` login: the live login could not sign in. Measured
against the LIVE portal page once the host was allowed from this container:

  · `https://frs.theupsstore.com/UI/Reporting/POS/POSReports.aspx` → 302 in ~1s to
    `/Framework/SignIn.aspx`. No bot wall, no IP block, no proxy needed.
  · the sign-in form is Username + Password only — no account / company id.
  · the page carries `<div id="SignInCaptcha" class="g-recaptcha"
    data-sitekey="…" data-size="invisible" data-callback="captchaSuccessCallback">` and its own
    script reads `renderCaptcha = 'True'`, so EVERY sign-in is held until reCAPTCHA clears.

THE DEFECT. `vidapay_sweep._looks_like_captcha` required the widget to be VISIBLE. `.g-recaptcha` was
already in its selector list, but a `data-size="invisible"` widget is 0x0, so `is_visible()` is False
and the detector skipped it. The live session then took the FAST PATH and auto-submitted — which is
precisely what that module's own comment says it must never do, because the portal rejects such a
submit as if the password were wrong. The operator is then told to "double-check the three
credentials": sent to re-type a perfectly good password, the same mistake the RATE-LIMIT GATE
comments in that file already exist to prevent.

THE CLASS. *A human check was recognised by whether it was PAINTED, not by whether it GATES the
submit.* Google's invisible reCAPTCHA is the documented `data-size="invisible"` + `data-callback`
pair: the form is posted only from that callback. Nothing is drawn until the check decides to
challenge, so "visible" was never the right question.

WHAT THIS PROVES
  §A  `human_check` is THE answer, with three states — and they are three because the two kinds need
      DIFFERENT instructions to the operator.
  §B  the owner's REAL widget (attributes copied from the live page) reads as a check.
  §C  the false positive the old comment guarded is still guarded: a bare reCAPTCHA v3 script include
      renders no widget, and a widget that is neither visible nor armed stays NOT a check — pushing
      every portal into human_action would stop the carrier feeds signing in unattended.
  §D  every pre-existing answer is unchanged (the visible widget, the unresolvable widget, the
      human-facing text, the clean page, the raising driver).
  §E  the live session hands the operator the right words for each kind, and the invisible wording
      does NOT tell them to complete a box that is not on the page yet.
  §F  the siblings: BOTH post-submit sites that would blame the credentials ask the human-check
      question first, through one home.
  §G  controls, including the pre-change shape (visibility-only) going RED.

No DB, no network, no browser. Run:  cd backend && python3 harness_human_check_kind.py
"""
import io
import os
import re
import sys

sys.path.insert(0, ".")

PASS = FAIL = 0
VP_PATH = os.path.join("app", "modules", "commcalc", "vidapay_sweep.py")
LL_PATH = os.path.join("app", "modules", "commcalc", "live_login.py")
VP_SRC = io.open(VP_PATH, encoding="utf-8").read()
LL_SRC = io.open(LL_PATH, encoding="utf-8").read()


def ok(cond, msg, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % msg)
    else:
        FAIL += 1
        print("  ✗ %s%s" % (msg, ("   " + str(detail)[:300]) if detail else ""))


def eq(got, want, msg):
    ok(got == want, msg if got == want else "%s (got %r, want %r)" % (msg, got, want))


# ── load the two modules WITHOUT importing playwright / fastapi ─────────────────────────────────
def _load(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


vp = _load("vp_human_check", VP_PATH)
ll = _load("ll_human_check", LL_PATH)


# ── the smallest page a detector can be asked about ─────────────────────────────────────────────
class El:
    """One widget element. `attrs` is what the real page carries; `vis` is what is PAINTED."""

    def __init__(self, vis=False, attrs=None, raise_vis=False, no_attrs=False):
        self._vis, self._attrs = vis, dict(attrs or {})
        self._raise_vis, self._no_attrs = raise_vis, no_attrs

    def is_visible(self):
        if self._raise_vis:
            raise RuntimeError("detached")
        return self._vis

    def get_attribute(self, name):
        if self._no_attrs:
            raise RuntimeError("this driver has no get_attribute")
        return self._attrs.get(name)


class Frame:
    def __init__(self, el=None, sel_match=None):
        self._el = el
        self._sel_match = sel_match      # only answer for these selectors

    def query_selector(self, sel):
        if self._el is None:
            return None
        if self._sel_match is not None and sel not in self._sel_match:
            return None
        return self._el


class Page:
    def __init__(self, frames=None, content="username password sign in", raise_frames=False):
        self._frames, self._content = (frames if frames is not None else [Frame()]), content
        self._raise_frames = raise_frames

    @property
    def frames(self):
        if self._raise_frames:
            raise RuntimeError("no frames")
        return self._frames

    def query_selector(self, sel):
        return self._frames[0].query_selector(sel) if self._frames else None

    def title(self):
        # The real `_page_text` reads title() + content(); a fake without it made the whole text path
        # read as "no check" and the harness would have passed while proving nothing about it.
        return "Sign in"

    def content(self):
        return self._content

    def inner_text(self, *a, **k):
        return self._content


# The owner's ACTUAL widget, attributes copied from the live sign-in page 2026-10-15.
UPS_WIDGET = {"class": "g-recaptcha", "data-sitekey": "6Let-lgpAAAAAOP35z0jUQyjNPizzeEmxJ84Wriv",
              "data-size": "invisible", "data-callback": "captchaSuccessCallback"}

print("\n— A. one answer, three states —")
ok(callable(getattr(vp, "human_check", None)), "vp.human_check is the detector")
ok(callable(getattr(vp, "_looks_like_captcha", None)), "the old boolean still exists for its callers")
src = re.sub(r"#.*", "", (re.search(r"def _looks_like_captcha\(.*?\n\n", VP_SRC, re.S) or [""])[0]
             if isinstance(re.search(r"def _looks_like_captcha\(.*?\n\n", VP_SRC, re.S), re.Match) else "")
ok("human_check" in VP_SRC[VP_SRC.index("def _looks_like_captcha("):][:600],
   "…and it READS human_check rather than keeping a second detector")
eq(sorted(ll._HUMAN_CHECK_MESSAGE), ["invisible", "visible"],
   "the live session has a message for each kind and no third")

print("\n— B. the owner's real widget reads as a check —")
p = Page([Frame(El(vis=False, attrs=UPS_WIDGET), sel_match=(".g-recaptcha",))])
eq(vp.human_check(p), "invisible", "the LIVE UPS widget (invisible + callback) IS a human check")
eq(vp._looks_like_captcha(p), True, "…so every existing caller now sees it — that widening IS the fix")
eq(ll._human_check_kind(vp, p), "invisible", "and the live session gets the kind, not just a bool")
eq(ll._captcha_present(vp, p), True, "the back-compat boolean agrees")

print("\n— C. the false positive the old comment guarded is STILL guarded —")
# reCAPTCHA v3: the api.js script include only. No widget element, so no selector matches.
eq(vp.human_check(Page([Frame(None)], content="sign in")), "",
   "a bare reCAPTCHA v3 script include renders no widget → NOT a check")
# A widget that is neither painted nor armed: left alone on purpose.
eq(vp.human_check(Page([Frame(El(vis=False, attrs={"class": "g-recaptcha"}), (".g-recaptcha",))])), "",
   "a widget with no data-callback and not visible stays NOT a check (unattended carrier feeds)")
eq(vp.human_check(Page([Frame(El(vis=False, attrs={"data-size": "invisible"}), (".g-recaptcha",))])), "",
   "…invisible WITHOUT a callback is not armed either")
eq(vp.human_check(Page([Frame(El(vis=False, attrs={"data-callback": "cb"}), (".g-recaptcha",))])), "",
   "…and a callback WITHOUT data-size=invisible is not the documented pair")
eq(vp.human_check(Page([Frame(El(no_attrs=True), (".g-recaptcha",))])), "",
   "a driver with no get_attribute reads as not-armed — the pre-change answer for shapes we cannot inspect")

print("\n— D. every pre-existing answer is unchanged —")
eq(vp.human_check(Page([Frame(El(vis=True), (".g-recaptcha",))])), "visible",
   "a PAINTED widget is still 'visible'")
eq(vp.human_check(Page([Frame(El(raise_vis=True), (".g-recaptcha",))])), "visible",
   "a widget whose visibility cannot be resolved is still treated as on screen")
eq(vp.human_check(Page([Frame(None)], content="Please complete the I'm not a robot check")), "visible",
   "human-facing TEXT alone is still 'visible'")
eq(vp.human_check(Page([Frame(None)], content="username password sign in")), "",
   "a clean login page is still no check")
eq(vp.human_check(Page(raise_frames=True, content="clean")), "",
   "a driver that cannot list frames still answers, and answers no")
# A VISIBLE check outranks an armed invisible one: it needs the clearer instruction.
both = Page([Frame(El(vis=False, attrs=UPS_WIDGET), (".g-recaptcha",)),
             Frame(El(vis=True), ("#g-recaptcha",))])
eq(vp.human_check(both), "visible", "a visible challenge outranks an armed invisible one")

print("\n— E. the operator gets the right words for the right kind —")
vis_msg = ll._HUMAN_CHECK_MESSAGE["visible"]
inv_msg = ll._HUMAN_CHECK_MESSAGE["invisible"]
ok("on the screen" in vis_msg, "the VISIBLE wording points at the box on screen")
ok("not a robot" not in inv_msg,
   "the INVISIBLE wording does NOT send them hunting for a box that is not on the page yet", inv_msg)
ok("Sign in" in inv_msg, "…it tells them to press Sign in themselves")
ok("password" in inv_msg.lower(),
   "…and says plainly that signing in for them would read as a bad password")
for k, m in ll._HUMAN_CHECK_MESSAGE.items():
    ok("captcha" not in m.lower() and "recaptcha" not in m.lower(),
       "the %s wording avoids jargon the operator did not choose" % k)
    ok(len(m) < 400, "the %s wording fits a status line" % k)
# The branch must use the KIND, not a boolean, or both kinds get one message again.
drive = LL_SRC[LL_SRC.index("def _drive("):]
drive = drive[:drive.index("\n    def ", 10)]
ok("_human_check_kind" in drive, "_drive asks for the KIND")
ok("_HUMAN_CHECK_MESSAGE[" in drive, "…and reads the message from the one home")
ok("Human check detected" not in drive,
   "…with no message spelled inline, which is how one kind got the other's words", drive[-300:])

print("\n— F. the siblings: neither post-submit site blames the credentials first —")
ok(callable(getattr(vp, "human_check_blocked_message", None)),
   "there is ONE home for 'why are we still on the form?'")
eq(vp.human_check_blocked_message(Page([Frame(El(vis=False, attrs=UPS_WIDGET), (".g-recaptcha",))])),
   vp._HUMAN_CHECK_BLOCKED["invisible"], "it answers for the owner's widget")
eq(vp.human_check_blocked_message(Page([Frame(None)], content="sign in")), "",
   "and returns nothing when no check gates the page, so the caller keeps its own wording")
ok("NOT a wrong password" in vp._HUMAN_CHECK_BLOCKED["invisible"],
   "the invisible message says outright that it is not a bad password")
# Both sites, by name — a fix to one and not the other is the same defect wearing a hat.
for fn in ("begin_login", "begin_login_b2bsoft"):
    body = VP_SRC[VP_SRC.index("def %s(" % fn):]
    body = body[:body.index("\ndef ", 10)]
    i = body.find('if state == "login":')
    ok(i >= 0, "%s still has its still-on-the-form branch" % fn)
    ok("human_check_blocked_message" in body[i:i + 700],
       "%s asks the human-check question BEFORE blaming the credentials" % fn, body[i:i + 300])
ok(VP_SRC.count("human_check_blocked_message(page)") >= 2,
   "both siblings dereference the same home")

print("\n— H. a prefill that could not fill does not report that it did —")
# Owner, 2026-10-31: the UPS reporting portal's sign-in screen with the user id and password empty.
# The challenge false-positive that kept him off the form is locked by harness_portal_page_verdict
# §I; THIS is the half that made the screen unexplainable — the invisible wording asserted "Your
# details are filled in" while prefill_login swallowed every failure and returned nothing.
ok(callable(getattr(vp, "prefill_login", None)), "the fill exists")
ok(vp.PREFILL_FIELDS == ("account id", "user id", "password"),
   "the three field names are declared once, in the operator's words")
ok(vp.PREFILL_REQUIRED == ("user id", "password"),
   "an account id the portal never asks for is not a shortfall (the UPS form is user + password)")
for k in ("filled", "no_value", "no_control", "ok"):
    ok(k in vp._prefill_report(), "the report carries %r" % k)
ok(vp._prefill_report(filled=["password"], no_value=["user id"])["ok"] is False,
   "a shortfall is not ok")
ok(vp._prefill_report(filled=["user id", "password"])["ok"] is True, "a complete fill is ok")
rep_broken = vp.prefill_login(None, None, None, None, "u", "p")
ok(rep_broken["ok"] is False and sorted(rep_broken["no_control"]) == ["password", "user id"],
   "a driver that answers nothing reports BOTH fields unfilled rather than claiming a fill",
   rep_broken)
ok("def _put(" in VP_SRC and VP_SRC.count("el.type(str(value), delay=25)") == 1,
   "ONE typing rule, shared with drive_typed_login — two copies is how one path stops working")
ok("input_value()" in VP_SRC,
   "a fill is proved by reading the box back, not by the fill call not raising")
# The operator's words.
ok(callable(getattr(ll, "_prefill_note", None)), "there is ONE home for what is in the form")
for kind, m in ll._HUMAN_CHECK_MESSAGE.items():
    ok("filled in" not in m.lower(),
       "the %s wording no longer asserts a fill it never looked at" % kind, m)
ok("filled in" in ll._prefill_note(vp._prefill_report(filled=["user id", "password"])).lower(),
   "a fill that HAPPENED is confirmed")
_nv = ll._prefill_note(vp._prefill_report(filled=["password"], no_value=["user id"]))
_nc = ll._prefill_note(vp._prefill_report(filled=["password"], no_control=["user id"]))
ok("user id" in _nv.lower() and "saved" in _nv.lower(),
   "nothing saved sends the operator to the login's settings", _nv)
ok("user id" in _nc.lower() and _nc != _nv,
   "a control that could not be found is a DIFFERENT sentence — two causes, two places", _nc)
ok(ll._prefill_note(None) == "" and ll._prefill_note({}) == "",
   "no report says NOTHING rather than claiming a fill")
ok("filled in" not in ll._prefill_note(vp._prefill_report()).lower(),
   "a fill with nothing to fill makes no claim either")
# The wiring. Writing the registry and not reading it has happened three times here (index §19.18).
drive_h = LL_SRC[LL_SRC.index("def _drive("):]
drive_h = drive_h[:drive_h.index("\n    def ", 10)]
ok("prefill_login(" in drive_h, "the live session still pre-fills on the human-driven path")
ok("_prefill_note(" in drive_h, "...and READS the report it gets back — the wiring, locked")
ok("filled in" not in drive_h, "with no wording spelled at the call site")
ok(LL_SRC.count("def _prefill_note(") == 1 and LL_SRC.count("_PREFILL_NOTE = {") == 1,
   "one home for the sentence, one home for its words")

print("\n— G. controls —")
ok(vp._INVISIBLE_MARK == ("data-size", "invisible") and vp._ARMED_MARK == "data-callback",
   "the two marks are declared, not spelled at the call site")
# ARMED: the pre-change shape was visibility ONLY. Reproduce it and show it misses the real widget.
def _visibility_only(page):
    for fr in page.frames:
        for sel in vp._CAPTCHA_SELECTORS:
            el = fr.query_selector(sel)
            if el is not None and el.is_visible():
                return True
    return False
real = Page([Frame(El(vis=False, attrs=UPS_WIDGET), (".g-recaptcha",))])
ok(_visibility_only(real) is False,
   "ARMED: the pre-change (visibility-only) rule MISSES the owner's widget — the defect, reproduced")
ok(vp._looks_like_captcha(real) is True, "…and the fixed rule catches it")
ok(".g-recaptcha" in vp._CAPTCHA_SELECTORS,
   "control: the selector was always there — visibility was the bug, not the selector list")
ok(ll._human_check_kind(type("V", (), {})(), real) == "",
   "control: a driver with NEITHER helper reads as no check rather than raising")
ok(ll._human_check_kind(type("V", (), {"_looks_like_captcha": staticmethod(lambda p: True)})(), real)
   == "visible",
   "control: a driver with only the OLD helper still answers (the documented fallback)")

print("\n%d passed, %d failed" % (PASS, FAIL))
if FAIL:
    print("\nFAIL — a human check that gates the sign-in was missed, or an operator was told the wrong "
          "thing about it. The detector is vidapay_sweep.human_check (three states); the operator's "
          "words are live_login._HUMAN_CHECK_MESSAGE and vidapay_sweep._HUMAN_CHECK_BLOCKED. No "
          "caller keeps its own copy of either.")
    sys.exit(1)
print("OK — a check that gates the submit counts whether or not it is painted, and each kind gets its "
      "own words.")
sys.exit(0)
