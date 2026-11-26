"""★ THE ONE HOME for "what paths does this application actually serve". PURE, no network, no DB.

WHY THIS EXISTS. Ten files in this backend answered that question with their own copy of a walk over
`app.routes`, and the copies disagree because the question has TWO traps that have to be dodged
together. The one copy that dodged neither was the SECURITY control
(`tests/integration/test_public_path_reaches_its_handler.py`), built on 2026-11-23 to make sure a
declared-public link — the W-9 form a wholesale customer is emailed — can never again be locked out
by a router-level auth gate. Measured 2026-11-26 (access probe run 330): it was sweeping FIVE paths
of an app serving 1,558, and not one of them a module route. Its own non-vacuity tripwire,
`checked >= 1`, was satisfied by `/health`.

TRAP 1 — `app.routes` does not hold the routes. Since fastapi 0.141 `include_router()` leaves ONE
lazy `_IncludedRouter` wrapper per included router instead of copying the sub-routes up. It has no
`.path` and no `.methods`, so a walk that reads those gets `None` and either raises
(`AttributeError`, which at least shows) or — as here — quietly skips every module route. For this
app `len(app.routes)` is 37 while the app serves 1,558 paths.

TRAP 2 — the leaf's `.path` is PREFIX-STRIPPED, and this one survives the fix for trap 1. Flattening
through `.original_router` recovers 1,879 leaf route objects, but each carries the path RELATIVE to
its mount: `/remediation/whatsapp-webhook`, never `/api/v1/remediation/whatsapp-webhook`. Every
declaration in this codebase is keyed by the SERVED path, so asking one about a relative path gets a
polite, confident `False`. For a default-deny declaration (`tenant_middleware.public_surface_allows`,
`rep_surface.api_allowed`) `False` means "not declared", so the caller skips the route and reports
clean. A control wired this way cannot fail, which is the only kind of broken control nobody notices.

THE ANSWER: ask the framework's PUBLIC contract, not its private structure. `app.openapi()` resolves
every prefix and every level of nested inclusion and is the same document the frontend and the
outside access probe (§CORE-42) read, so an in-process control and the live probe can no longer
disagree about which paths exist.

Locked by `harness_route_table.py`: a second copy of the walk fails the build.
"""
from __future__ import annotations


def route_surface(application) -> set:
    """Every `(served_path, METHOD)` pair the application exposes, METHOD upper-cased.

    `served_path` is the path as a browser addresses it, templates and all
    (`/api/v1/referral/redeem/{token}`), which is the form every declaration here is keyed by."""
    paths = (application.openapi() or {}).get("paths") or {}
    return {(p, str(m).upper()) for p, ops in paths.items() for m in (ops or {})}


def served_paths(application, method: str | None = None) -> list:
    """The sorted served paths, optionally only those serving `method`."""
    want = str(method).upper() if method else None
    return sorted({p for p, m in route_surface(application) if want is None or m == want})


def serves(application, path: str, method: str) -> bool:
    """Does the application serve exactly this `(path, method)`? Templates must match literally."""
    return (str(path), str(method).upper()) in route_surface(application)
