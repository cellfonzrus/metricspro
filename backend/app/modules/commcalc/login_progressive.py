"""WHAT A LOGIN PAGE WANTS BEFORE IT WILL SHOW THE PASSWORD FIELD — the one home (index §COMMCALC-46).

WHY THIS MODULE EXISTS. A portal's sign-in is not always one page. Plenty of them ask for something
first — an account or company id, or just the user id — and reveal the password only after that step
is completed. Every login driver here has to answer the same question before it can type anything:
*is the password on this page, and if not, what does this page want first?*

That question was answered TWICE, and both times by naming one portal's markup:

  - `live_login._companyid_step` filled `#companyId`, pressed `#btnSubmit`, waited for `#password`;
  - `vidapay_sweep.begin_login_b2bsoft` filled `#companyId`, looked for `#username`, pressed
    `#btnSubmit`.

and the GENERIC `vidapay_sweep.begin_login` — the path every other portal takes — answered it not at
all: it looked for a password field, did not find one, and reported *"could not find the password
field"*. That is what the owner's own UPS billing login hit on 2026-10-27: a two-page sign-in, a
first page with a user id and a Continue button, and a platform that called it a broken form.

THE CLASS, named rather than the instance. "The password is on a later page" is not a fact about one
vendor's SSO. It is a shape of login form, and a platform that hardcodes one vendor's ids for it has
not implemented progressive login — it has implemented that vendor. The next portal with the same
shape fails exactly as UPS did, and fixing it for UPS alone would leave the third one broken.

SO: this module decides, from a page's OWN controls and OWN words, what the first step is. It names
no portal, no tenant and no element id (RULE TWO), and a tenant whose page does not say what its
fields are for can DECLARE them in `data_source.portal_calibration["login"]` — a config row, never
code.

WHAT IT REUSES rather than rebuilds:
  - `portal_report_pull.read_page_controls` — the page reader already shipped for report calibration
    (§COMMCALC-39). It already returns every visible input, select, button and link with its id, its
    name, the words printed beside it, and the input's own `type` as `kind`. Nothing was added to it.
  - `portal_report_pull.control_selector` — the one rule for the selector a control is addressed by,
    escaped for framework-generated ids.
  - `report_columns.norm` — THE spelling rule (§COMMCALC-38). A second normalizer is how two spellings
    of one store code once became two stores.

THE PLANNER IS PURE. `plan_first_step` takes plain dicts and returns a plan; it touches no browser and
no database, which is why `backend/harness_login_progressive.py` can prove both page shapes — this
platform cannot reach either portal from CI, and a mechanism that can only be tested against a live
vendor is a mechanism nobody will ever re-test.

Proof: `backend/harness_login_progressive.py`. It fails the build if a login path hardcodes an element
id again, or if a second copy of this decision appears.
"""
from app.modules.commcalc import report_columns as rc


# ── the house vocabulary ────────────────────────────────────────────────────────────────────────────
# What a field's OWN words say it is for. House defaults; a tenant adds to them through
# `portal_calibration["login"]["field_words"]` (RULE TWO — an unseen wording is a config row).
#
# `account_id` sits above `username` deliberately: a page carrying both asks for them in that order,
# and a company id typed into the user box fails the step with no way to see why.
FIELD_WORDS = {
    "account_id": ("company id", "companyid", "company number", "company", "account id", "accountid",
                   "account number", "access code", "accesscode", "dealer code", "dealer id",
                   "location id", "location number", "store id", "store number", "agent id",
                   "customer id", "customer number", "client id"),
    "username": ("user id", "userid", "user name", "username", "login id", "loginid", "email address",
                 "email", "e mail", "user", "login"),
}
FIELD_ORDER = ("account_id", "username")

# A control that ADVANCES the form. "continue" before "sign in": a page that offers both is telling us
# which one is the step, and pressing the final one early submits a form with no password in it.
STEP_WORDS = ("continue", "next", "proceed", "go on")
FINAL_WORDS = ("sign in", "signin", "log in", "login", "logon", "submit", "enter")

# Never pressed, whatever else its words say. Each of these LEAVES the login — a password manager's
# "remember me" aside, pressing one ends the attempt somewhere we cannot come back from, and an
# account-recovery or registration flow pressed by a robot is worse than a failed login.
NEVER_WORDS = ("forgot", "forgotten", "reset", "recover", "register", "sign up", "signup", "create",
               "new user", "enroll", "cancel", "back", "help", "support", "privacy", "cookie",
               "terms", "remember", "language", "español", "espanol", "chat")

# The kinds `read_page_controls` reports for something that takes typing, and for something pressable.
TEXT_KINDS = ("text", "email", "tel", "number", "")
PRESS_KINDS = ("button", "submit", "image", "link")
PASSWORD_KIND = "password"


def control_words(control) -> str:
    """One normalized string of everything this control says about itself: the words printed beside it,
    then its name, then its id.

    The label first, because it is the page's own sentence to a human and the only part a framework
    does not mangle. The name and id are included because plenty of pages print no label at all, and a
    field called `txtCompanyId` has told us what it is even when nothing beside it does.
    """
    c = control or {}
    return rc.norm(" ".join([str(c.get("label") or ""), str(c.get("name") or ""), str(c.get("id") or "")]))


def _says(words: str, phrases) -> str:
    """The LONGEST phrase in `phrases` that `words` contains, else "".

    Longest first for the same reason the header matcher does it: "account id" and "id" both match a
    field labelled "Account ID", and the shorter one would claim fields it has no business claiming.
    """
    for p in sorted({str(p) for p in phrases}, key=len, reverse=True):
        n = rc.norm(p)
        if n and n in words:
            return n
    return ""


def field_purpose(control, vocabulary=None) -> str:
    """Which value this control takes — "password", "account_id", "username" — or "" when its own words
    do not say. PURE.

    A control whose KIND is password is a password field whatever its words say; that is the page
    telling us directly, and it outranks any wording. Everything else is decided by the vocabulary,
    in `FIELD_ORDER`, so a page with both an account box and a user box fills them in that order.
    """
    c = control or {}
    kind = str(c.get("kind") or "").lower()
    if kind == PASSWORD_KIND:
        return "password"
    if kind not in TEXT_KINDS:
        return ""
    vocab = rc.merge_synonyms(FIELD_WORDS, vocabulary)
    words = control_words(c)
    for field in FIELD_ORDER:
        if _says(words, vocab.get(field, ())):
            return field
    return ""


def press_purpose(control) -> str:
    """"step" for a control that advances this form, "final" for one that submits it, "" for anything
    else — including everything in `NEVER_WORDS`, which is refused before its other words are read.
    PURE.
    """
    c = control or {}
    if str(c.get("kind") or "").lower() not in PRESS_KINDS:
        return ""
    words = control_words(c)
    if _says(words, NEVER_WORDS):
        return ""
    if _says(words, STEP_WORDS):
        return "step"
    if _says(words, FINAL_WORDS):
        return "final"
    return ""


def has_password(controls) -> bool:
    """Is the password field already on this page? The whole question this module exists to answer
    first: when it is, there is no step to take and nothing should be pressed.
    """
    return any(str((c or {}).get("kind") or "").lower() == PASSWORD_KIND for c in (controls or []))


def plan_first_step(controls, have, declared=None):
    """THE plan for getting from this page to the password field. PURE.

    `controls` is `portal_report_pull.read_page_controls(scope)`. `have` names the values the caller
    actually holds — `{"account_id": ..., "username": ...}`; a field we hold no value for is never
    filled, because typing an empty string into a required box fails the step and looks like the page
    rejected us. `declared` is the tenant's `portal_calibration["login"]`:

        {"fields": {"account_id": "#x", "username": "#y"}, "press": "#z",
         "field_words": {"account_id": ["werks nummer"]}}

    A declared selector WINS over anything read off the page, and is used even when the page says
    nothing — that is the escape hatch for a portal whose markup explains nothing, and it is config
    rather than a branch in this function.

    Returns:
        {"needed": bool, "fill": [{"selector", "field", "why"}], "press": {"selector", "why"}|None,
         "why": str}

    `needed` False means the password is already here. `needed` True with an empty `fill` and no
    `press` is the honest dead end: this page wants something we do not have, and the caller reports
    that rather than pressing buttons to see what happens.
    """
    from app.modules.commcalc import portal_report_pull as prp

    controls = list(controls or [])
    decl = dict(declared or {})
    held = {k: v for k, v in dict(have or {}).items() if str(v or "").strip()}

    if has_password(controls):
        return {"needed": False, "fill": [], "press": None,
                "why": "the password field is already on this page"}

    vocabulary = decl.get("field_words") or None
    decl_fields = {str(k): str(v) for k, v in dict(decl.get("fields") or {}).items() if v}

    fill, claimed = [], set()
    for field in FIELD_ORDER:
        if field not in held or field in claimed:
            continue
        if decl_fields.get(field):
            fill.append({"selector": decl_fields[field], "field": field,
                         "why": "declared on this source"})
            claimed.add(field)
            continue
        for c in controls:
            if field_purpose(c, vocabulary) != field:
                continue
            sel = prp.control_selector(c)
            if not sel:
                continue
            fill.append({"selector": sel, "field": field,
                         "why": f"the page calls it {control_words(c)[:60]!r}"})
            claimed.add(field)
            break

    press = None
    if decl.get("press"):
        press = {"selector": str(decl["press"]), "why": "declared on this source"}
    else:
        # A step control first, wherever it sits on the page; the final submit only as a fallback, and
        # only when we have something to submit. Pressing "Sign in" on a page we filled nothing into
        # just reloads it, and three of those look to a portal like a failed-login attempt.
        #
        # With nothing filled, pressing depends on WHY nothing was filled. A page with no text box at
        # all and a Continue control is an interstitial, and pressing it is the only way forward. A
        # page that DOES ask for something we could not supply must not be submitted empty: a required
        # box left blank comes back as "rejected", which reads as wrong credentials and sends whoever
        # is looking at the screen to re-type a password that was never the problem.
        asks_for_something = any(str((c or {}).get("kind") or "").lower() in TEXT_KINDS
                                 for c in controls)
        for want in ("step", "final"):
            if not fill and (want == "final" or asks_for_something):
                break
            for c in controls:
                if press_purpose(c) != want:
                    continue
                sel = prp.control_selector(c)
                if not sel:
                    continue
                press = {"selector": sel,
                         "why": ("the page's own " + ("step" if want == "step" else "submit")
                                 + f" control, {control_words(c)[:60]!r}")}
                break
            if press:
                break

    if not fill and not press:
        return {"needed": True, "fill": [], "press": None,
                "why": ("no password field, and nothing on this page takes a value we hold — "
                        "declare the login fields on this source to say which control is which")}
    return {"needed": True, "fill": fill, "press": press,
            "why": ("fill " + ", ".join(f["field"] for f in fill) if fill else "press")
                   + (" then press" if (fill and press) else "")}


# ── the driver: the one place a plan is carried out ──────────────────────────────────────────────────
def advance_past_first_step(page, have, declared=None, timeout_ms=15000, scopes=None):
    """Carry out `plan_first_step` against a live page, and wait for the password field to appear.

    THE one driver. Both the unattended login and the human-driven live session call this, so a portal
    that signs in one way signs in the same way the other way — the two used to carry their own copy of
    this step, keyed to one vendor's element ids.

    Frames: the plan is made and applied PER FRAME, because a login form inside an iframe answers
    `read_page_controls` on that frame and not on the document that holds it (the owner's UPS sign-in
    page carries ten frames). The first frame that offers a plan is the login form.

    Never raises. Returns a short note for the operator — what it did, or why it could not — because
    this runs inside a login whose failure the operator reads as a sentence on a screen, and an
    exception here would replace that sentence with a stack trace.
    """
    from app.modules.commcalc import portal_report_pull as prp

    def _frames():
        if scopes is not None:
            return list(scopes)
        out = [page]
        try:
            out += [f for f in (page.frames or []) if f is not getattr(page, "main_frame", None)]
        except Exception:
            pass
        return out

    tried = 0
    for scope in _frames():
        controls = prp.read_page_controls(scope)
        if not controls:
            continue
        plan = plan_first_step(controls, have, declared)
        if not plan["needed"]:
            return "the password field was already on the page"
        if not plan["fill"] and not plan["press"]:
            tried += 1
            continue

        for item in plan["fill"]:
            try:
                el = scope.query_selector(item["selector"])
            except Exception:
                el = None
            if not el:
                continue
            value = str(dict(have or {}).get(item["field"]) or "")
            try:
                el.click()
                el.fill("")
                # type(), not fill(): a progressive form listens for keystrokes to decide whether its
                # next step may be enabled, and a value set in one assignment fires none of them.
                el.type(value, delay=15)
            except Exception:
                try:
                    el.fill(value)
                except Exception:
                    pass

        if plan["press"]:
            try:
                b = scope.query_selector(plan["press"]["selector"])
                if b:
                    b.click()
            except Exception:
                pass

        try:
            scope.wait_for_selector("input[type=password]", state="visible", timeout=timeout_ms)
        except Exception:
            pass
        return "took this page's first step (" + plan["why"] + ")"

    if tried:
        return ("this page shows no password field and nothing on it takes a value we hold — declare "
                "the login fields on this source")
    return ""
