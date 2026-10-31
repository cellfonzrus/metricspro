"""WE SUBMITTED A PORTAL LOGIN. WHAT ACTUALLY HAPPENED?

THE ONE HOME of that decision, for every portal driver. PURE — stdlib only, no DB, no network, no
page object: a caller hands it the facts it has already READ off the page and gets back a named
outcome plus the one sentence an operator can act on.

THE DEFECT THAT PRODUCED IT (house org, measured 2026-10-31, index §COMMCALC-50.9). The owner
reported that three carrier reports — the comprehensive comp report, the processor's commission
payment detail and the monthly-incentive report — had not refreshed since 8-9 October although all
three are meant to arrive by a scheduled portal pull. Measured from production:

    commcalc.epay_sweep_config   enabled=true   frequency=daily   last_attempt_at 2026-10-31T19:30Z
                                 last_run_at    2026-10-09T10:07Z
                                 last_detail    "epay login failed - credentials rejected
                                                 (or the application/2FA changed)."
    core.job_run  sweep:epay_sweep_config   1,665 rows, every 30 minutes, cron never missed a tick
                                 2026-10-09T19:30Z  failed   "Login OK - <menu hover timed out>"
                                 2026-10-09T20:00Z  failed   "credentials rejected"
                                 ... 1,056 consecutive runs, all identical, to the moment of writing

The scheduler was never the problem. ONE login broke, and all three reports ride it, which is why
three independent feeds froze on the same evening. What made it unfixable for twenty-two days is the
sentence above: nobody could tell whether it was TRUE. It was produced like this —

    body = (page.content() or "").lower()                   # the MARKUP: scripts, templates,
                                                            # comments, hidden nodes, all of it
    if any(w in body for w in ("invalid", "incorrect", "failed")) and "passwordinput" in body:
        raise EpayLoginError("credentials rejected")

THE CLASS, and it is the second sighting this week: **a page judged by how it is BUILT, not by what
it is DOING.** `page.content()` serialises every inline script and hidden validation template, so a
single-page app that merely KNOWS the word "invalid" reads as having said it. The same shape hit
`portal_backoff.page_verdict`, where "captcha" in the markup of a perfectly working sign-in form
read as a bot wall (index §COMMCALC-50.8). The remedy is identical: ask the page what it is doing.

Two more faults rode along in that one predicate, both fixed by moving the decision here:

  * SUCCESS WAS A BRAND NAME. The only way to be told the login had WORKED was the vendor's own
    words appearing in the document title. That is a carrier name in code (RULE TWO) and it means a
    portal that merely retitles its shell reads as a failed login, for every tenant on it.
  * THREE OUTCOMES WORE ONE COAT. "The portal rejected this password", "the portal served a human
    check" and "the sign-in page changed" need three DIFFERENT actions - re-enter the credential,
    sign in on the live screen or route via a proxy, and a developer. They were reported as the
    first, so every operator who read the status went and re-typed a password that may well have
    been correct, and the two real remedies were never on offer.

THE RULE, stated once: an outcome is named from STRUCTURE and from VISIBLE text - whether the form
is still there, whether the application is on screen, and what the page SAYS where a person would
read it. Never from markup. A caller that cannot supply those facts does not get an answer.

RULE TWO: no carrier, tenant or portal name appears here, and none may. The marker vocabularies are
POLICY (what any application's signed-in shell and any form's error region look like), carried as
house defaults a caller may override - not one tenant's words standing in for everybody's.

Locked by `backend/harness_login_outcome.py`, which fails the build if a driver stops dereferencing
this module, if a login verdict grows a second home, or if a login decision is taken off markup
again.
"""

# ── OUTCOMES ──────────────────────────────────────────────────────────────────────────────────────
# SIGNED_IN   we are inside the application. Nothing to report.
# REJECTED    the portal itself said these credentials are not accepted. An operator can fix this.
# HUMAN_CHECK the portal served an anti-automation / human check instead of letting us through.
#             NOTHING an operator types will help; this wants a live sign-in or a different egress.
# REFUSED     the portal refused the request outright - it never offered a sign-in at all.
# CHANGED     we reached the form, submitted it, and the portal neither let us in nor said why.
#             The flow has changed. This is a developer's fix, never a password.
# UNKNOWN     we left the sign-in page and found nothing we recognise. Say so; do not guess.
SIGNED_IN = "signed_in"
REJECTED = "credentials_rejected"
HUMAN_CHECK = "human_check"
REFUSED = "portal_refused"
CHANGED = "login_page_changed"
UNKNOWN = "unknown"

#: Every outcome this module can return, so a caller (or a test) can enumerate them.
ALL_OUTCOMES = (SIGNED_IN, REJECTED, HUMAN_CHECK, REFUSED, CHANGED, UNKNOWN)

#: The outcomes that mean we are NOT in. `SIGNED_IN` is the only one that means we are.
FAILURE_OUTCOMES = (REJECTED, HUMAN_CHECK, REFUSED, CHANGED, UNKNOWN)

# THE ALLOWLIST THAT MATTERS. Only these outcomes may be reported to a person as "fix the stored
# credential". An outcome absent from this tuple reads as NOT a credential problem - deliberately an
# allowlist and not a denylist, so a new outcome added later is excluded until someone rules on it.
# This is the thing the old predicate got wrong: it told the operator to re-type a password for a
# bot wall, a retitled shell and a changed form alike.
_CREDENTIAL_OUTCOMES = (REJECTED,)

# What any signed-in application shell shows. POLICY, not a tenant's vocabulary: these are the words
# a web application uses about ITSELF, in every portal any tenant of ours logs in to.
DEFAULT_SIGNED_IN_MARKERS = (
    "logout",
    "log out",
    "sign out",
    "signout",
    "my account",
    "dashboard",
)

# What a form's own error region says when a credential is refused. Also POLICY. Matched against
# VISIBLE text only - see `verdict`'s contract - which is the whole point of this module.
DEFAULT_REJECTION_MARKERS = (
    "invalid",
    "incorrect",
    "not recognised",
    "not recognized",
    "try again",
    "no match",
    "wrong password",
    "wrong user",
    # A locked account is a refusal, and portals phrase it four ways. All four are here because the
    # remedy is the same and because a phrasing nobody listed is a feed that goes quiet (the exact
    # shape of §COMMCALC-50.9): "is locked" / "has been locked" / "locked out" / "account locked".
    "is locked",
    "been locked",
    "account locked",
    "locked out",
    "disabled",
    "expired",
    "too many attempts",
)


def visible_from_html(html):
    """The text a PERSON would read, recovered from an HTML document. PURE, stdlib only.

    FOR DRIVERS WITH NO BROWSER. A driver holding a live page asks the page
    (`document.body.innerText`) and must keep doing so. A driver that speaks plain HTTP has only the
    document, and this is how it gets the same fact: scripts, styles, comments and tags removed,
    entities unescaped, whitespace collapsed.

    This is NOT the defect it looks like. The ban is on DECIDING from markup — on a word counting
    because the page ships it. Recovering the readable text first, and deciding from THAT, is the
    remedy, not a loophole: the bundled `var M={invalid:...}` that produced the outage lives inside
    a <script> and does not survive this function.
    """
    import html as _html
    import re as _re
    s = str(html or "")
    if not s:
        return ""
    s = _re.sub(r"(?is)<!--.*?-->", " ", s)
    s = _re.sub(r"(?is)<(script|style|template|noscript)\b.*?</\1\s*>", " ", s)
    s = _re.sub(r"(?is)<[^>]+>", " ", s)
    s = _html.unescape(s)
    return _re.sub(r"\s+", " ", s).strip()


def form_in_html(html):
    """Does this HTML document carry a sign-in form? PURE, stdlib only — the structural half of the
    answer for a driver with no browser, matching `visible_from_html`'s textual half.

    Keyed on the password INPUT, which is what a sign-in form is: a type that cannot appear by
    accident and is not a word anybody writes in prose.
    """
    import re as _re
    s = str(html or "")
    if not s:
        return False
    return bool(_re.search(r"(?is)<input\b[^>]*type\s*=\s*[\"']?password\b", s))


def rejection_said(visible_text, markers=None):
    """The portal's OWN words refusing the credential, or None. PURE.

    `visible_text` is what a person would READ on the page - `document.body.innerText` and the like.
    Passing markup here is the defect this module exists to end, and the harness fails the build for
    a caller that does it.

    Returns the matched marker so a status line can quote the portal rather than paraphrase it.
    """
    body = str(visible_text or "").lower()
    if not body:
        return None
    for m in (DEFAULT_REJECTION_MARKERS if markers is None else markers):
        m = str(m or "").strip().lower()
        if m and m in body:
            return m
    return None


def signed_in_said(visible_text, markers=None):
    """The application's own evidence that we are INSIDE it, or None. PURE. Visible text only.

    This replaces "the vendor's name is in the document title", which was both a carrier name in
    code and a false negative waiting for the next rebrand.
    """
    body = str(visible_text or "").lower()
    if not body:
        return None
    for m in (DEFAULT_SIGNED_IN_MARKERS if markers is None else markers):
        m = str(m or "").strip().lower()
        if m and m in body:
            return m
    return None


def verdict(*, form_present, visible_text, page_verdict=None,
            signed_in_markers=None, rejection_markers=None):
    """What happened to this login attempt - {"outcome", "marker", "note"}. PURE. Never raises.

    EVERY argument is keyword-only and the two that carry the evidence are REQUIRED, with no
    default. A caller that has not looked at the page must not be able to get an answer out of this:
    a default here would be this module guessing on a driver's behalf, which is how the markup
    predicate survived so long - it always had something to say.

      form_present   bool  - is the sign-in form STILL on the page? Structural: the driver asks its
                             own DOM for the password control. This is the fact that tells "we are
                             still at the door" apart from "we are through it".
      visible_text   str   - what a person would READ on the page. innerText, never page.content().
      page_verdict         - `portal_backoff.page_verdict(...)` for this page, or None. Read for its
                             "kind" only, so this module needs no marker vocabulary of its own for
                             refusals and challenges - that question already has one home.

    ORDER, and why each step is where it is:

      1. THE FORM IS STILL THERE AND IT IS COMPLAINING -> the credential was rejected. This is the
         most SPECIFIC fact obtainable: the door is still shut and the page says why, in words a
         person reads. It goes first so a stray application marker elsewhere on the page cannot
         overrule the portal's own refusal.
      2. A SIGNED-IN APPLICATION BEATS EVERY REMAINING SIGNAL. If the shell is on screen we are in,
         whatever stale text or leftover marker is also on the page. (The reverse ordering is
         incident 3 of 2026-07-17 in the sibling driver: a hidden phrase pinned the state while the
         real panel was visible.)
      3. A CHALLENGE OR REFUSAL IS BELIEVED ONLY WHEN THE FORM IS ABSENT. A page showing us the
         form has not served something instead of it - §COMMCALC-50.8, the same rule, applied here
         rather than copied here.
      4. THE FORM IS STILL THERE AND THE PAGE SAYS NOTHING -> the submit did not take us anywhere
         and nothing was explained. The flow changed; that is not a password.
      5. THE FORM IS GONE AND THE APPLICATION IS NOT THERE EITHER -> say we do not know.
    """
    vis = str(visible_text or "")
    kind = ""
    if isinstance(page_verdict, dict):
        kind = str(page_verdict.get("kind") or "").strip().lower()

    if form_present:
        said = rejection_said(vis, rejection_markers)
        if said:
            return {"outcome": REJECTED, "marker": said, "note": note(REJECTED)}

    hit = signed_in_said(vis, signed_in_markers)
    if hit:
        return {"outcome": SIGNED_IN, "marker": hit, "note": note(SIGNED_IN)}

    if not form_present and kind:
        # `portal_backoff`'s own words for the two kinds. Compared as substrings so this stays
        # correct if that module's constants are renamed around the same two concepts.
        if "challenge" in kind:
            return {"outcome": HUMAN_CHECK, "marker": (page_verdict or {}).get("marker"),
                    "note": note(HUMAN_CHECK)}
        if "refus" in kind:
            return {"outcome": REFUSED, "marker": (page_verdict or {}).get("marker"),
                    "note": note(REFUSED)}

    if form_present:
        return {"outcome": CHANGED, "marker": None, "note": note(CHANGED)}

    return {"outcome": UNKNOWN, "marker": None, "note": note(UNKNOWN)}


# What to TELL the operator, per outcome. One home, because the five want genuinely different
# actions and the wrong one wastes their day - which is precisely what happened here for 22 days.
_NOTES = {
    SIGNED_IN: "",
    REJECTED: ("The portal itself refused the saved sign-in. Open this connector's login, re-enter "
               "the user name and password, and sign in once on the live screen to confirm them. A "
               "portal will also refuse a credential it has expired, or an account it has locked "
               "after repeated attempts, so a password that is still correct may need resetting at "
               "the portal before it will work here."),
    HUMAN_CHECK: ("The portal served a human / anti-automation check instead of signing us in. "
                  "Nothing typed here will clear that: sign in once on the live screen so the "
                  "portal trusts this device, or route this connector through a proxy. The saved "
                  "password is not implicated."),
    REFUSED: ("The portal refused the request outright - it never offered a sign-in page. Check the "
              "address saved on this connector, and whether the portal has blocked this server's "
              "address. The saved password is not implicated."),
    CHANGED: ("We reached the sign-in form and submitted it, and the portal neither let us in nor "
              "said why. That means the sign-in flow has changed, not that the password is wrong - "
              "this one needs a developer, and re-entering the credential will not help."),
    UNKNOWN: ("The sign-in page went away and we did not recognise what replaced it, so we cannot "
              "say whether we are signed in. Nothing has been pulled. Sign in on the live screen to "
              "see what the portal is showing."),
}


def note(outcome):
    """The one operator sentence for an outcome. PURE. Never raises, never returns None."""
    return _NOTES.get(str(outcome or "").strip().lower(), "")


def is_credential_problem(outcome):
    """May a surface tell someone to fix the stored credential for this outcome? PURE.

    An ALLOWLIST (see `_CREDENTIAL_OUTCOMES`): an outcome nobody has ruled on reads as NOT a
    credential problem, so the worst a new outcome can do is stay quiet about the password.
    """
    return str(outcome or "").strip().lower() in _CREDENTIAL_OUTCOMES


def signed_in(outcome):
    """Did we get in? PURE. The one test for it, so no caller re-spells it."""
    return str(outcome or "").strip().lower() == SIGNED_IN


def message(outcome, marker=None, prefix=None):
    """The whole status line for a failed login: what happened, what the portal said, what to do.

    `prefix` lets a driver name the connector in the operator's own words ("the owner portal");
    it is passed IN by the caller and never spelled here (RULE TWO).
    """
    head = (str(prefix).strip() + " " if prefix else "") + "sign-in"
    said = f' The portal said: "{marker}".' if marker else ""
    return f"{head}: {note(outcome)}{said}".strip()
