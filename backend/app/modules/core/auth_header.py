"""THE ONE HOME for "what is this `authorization` argument, as a string?" (2026-11-04, index §CORE-36).

An endpoint function declares `authorization: str = Header(None)`. On a real request FastAPI
replaces that default with a `str` or `None`, so every HTTP caller sees what the annotation claims.
A caller that invokes the function DIRECTLY — a proof harness, an in-process sweep, a cron, one
router calling another — gets the default itself: a `fastapi.Header` object, which is TRUTHY and has
no string methods. So `(authorization or "").strip()` reads as careful and raises AttributeError.

That is what happened in `storeops.router.person_write_allowed`: the guard landed, HTTP was fine,
and three proof harnesses crashed the moment anything ran them. Nothing had been running them.

Twelve other modules carry their own copy of the same `isinstance(authorization, str)` line. That is
the §19.18 defect that made this possible — a fact every caller needs and nothing to dereference —
and those copies are NOT rewired by the change that added this file. Said plainly rather than
quietly: the next change to touch one of them should point it here.
"""


def auth_str(authorization) -> str:
    """The authorization value as a string, or `""` when there is not one.

    Answers WHAT IT IS, not whether it is blank — a caller that means "is there a token" strips the
    result itself, so that this function never decides on anyone's behalf that a padded token is no
    token at all.
    """
    return authorization if isinstance(authorization, str) else ""
