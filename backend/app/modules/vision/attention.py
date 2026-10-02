"""Vision → the platform's own attention/health machinery.

WHY THIS FILE IS SIX LINES OF WIRING AND A LONG COMMENT. The platform already had everything needed
to notice dark cameras, and had had it for a month:

  * `core.import_health.register_provider` — register once and a module contributes items to the
    admin attention popup;
  * `control_box_api._provider_specs` — "a module registering a provider gains a lamp with no code
    change and no migration here", so the same registration also lights the super-admin board;
  * `core.system_check` (mig 971) — a self-scheduling DAILY run that walks every org and evaluates
    those lamps, and which re-registers its own cron on every backend boot so a lost job heals.

Vision was simply never plugged into it. `GET /vision/status` has known since migration 900 whether
Google was linked, whether events were arriving and whether a store PC was alive — and nothing ever
asked it unless a person opened the settings page. That is why this estate's cameras could be dark
for weeks and the first signal was somebody wondering why a chart was flat.

So this file does NOT add a cron, a table, an alert channel or a board. It registers vision with the
machinery that already exists, which is what the owner meant by "anything which is automated in the
system should have this mechanism already built in".

THE PROVIDER CONTRACT, which the findings were built to satisfy (import_health, owner 2026-07-26):
"a notification MUST clear when the check says everything is OK", and the deep link must land
somewhere that completing the action makes the item disappear. Every finding in health.py carries a
`where` that does exactly that — /vision/settings to reconnect, /vision/lines to draw a counting
line, /vision/onboarding to finish setup — and every finding is computed from LIVE state, so fixing
the thing removes the item on the next call. Nothing here is a stored flag that can go stale.
"""

from app.modules.core.import_health import register_provider
from app.modules.vision import health as HLTH

# health.py's severities -> the attention popup's. A camera estate that is DOWN is an error; a
# degraded or unfinished one is a warning. Nothing from vision is ever "info": every finding here
# means a number somebody reads is wrong or missing.
_SEVERITY = {HLTH.DOWN: "error", HLTH.DEGRADED: "warning", HLTH.SETUP: "warning"}


@register_provider("vision_cameras", label="Cameras", group="config", cost="cheap")
def _p_vision(client, org_id, ctx=None):
    """Whatever is wrong with this tenant's cameras, as attention items.

    Cheap on purpose: it reads one status payload, which is a handful of indexed reads. A login
    popup must never pay for a scan, and this one runs on every call.
    """
    try:
        from app.modules.vision.router import _status_payload       # lazy: avoids an import cycle
        snap = _status_payload(org_id)
    except Exception:
        # A tenant with the module off, or a transient read failure. Returning nothing is correct:
        # an attention item that cannot be substantiated is noise, and the provider framework
        # reports the exception separately rather than letting it break the popup for other modules.
        return []

    if not (snap.get("config") or {}).get("enabled"):
        return []                        # module off for this tenant — not a fault, nothing to say

    out = []
    for f in HLTH.assess(snap):
        out.append({
            "group": "config",
            # Keyed per finding, so fixing one clears one — "cameras are unhealthy" as a single
            # item would stay lit while three of four problems were resolved.
            "key": "vision_%s" % f["code"],
            "severity": _SEVERITY.get(f["severity"], "warning"),
            "label": f["title"],
            # The popup is read in passing, so the remedy travels WITH the diagnosis. An item that
            # says only what is broken makes the reader go and find out what to do about it.
            "detail": "%s  →  %s" % (f["detail"], f["remedy"]),
            "count": 1,
            "deep_link": f.get("where") or "/vision/settings",
            "deep_link_label": "Open Vision",
        })
    return out
