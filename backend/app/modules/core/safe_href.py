"""SHARED href-safety primitive — H6 (stored XSS via `javascript:` URLs), 2026-08-05 security audit.

WHY THIS EXISTS. Several config values in this product are TENANT-WRITABLE and then RENDERED AS LINKS:
`core.training_tour.start_href` / `training_tour_step.page_href` (the guided walk-through — a step's
href is followed AUTOMATICALLY by TourRunner via `?tour=<slug>`, no click required),
`core.release_note.deep_link` (What's New), `core.import_feed.deep_link` (Import Health) and
`storeops.portal_reports.href`. A `javascript:` payload in any of them runs in the victim's session;
the Supabase JWT **and** the 2FA marker live in `localStorage`, so one stored payload steals both.

TWO LAYERS, ON PURPOSE. This module is the FIRST one — the value never reaches the database. The
render-side twin is `frontend/src/lib/safe-url.ts` (`safeHref`), which also protects rows written
before this shipped and rows written by any code path that forgets to call this. Neither layer is
allowed to be the only one.

ALLOW-LIST, NEVER A DENY-LIST. A deny-list is defeated by `JaVaScRiPt:`, `java&#9;script:`,
`\\x01javascript:` and by every scheme invented after the list was written. Accepted:

  · a SAME-SITE reference — `/admin/roles`, `/commcalc/reports?tab=1#x`, `#section`, `?q=1`,
    or a bare relative path (`commcalc/upload`) — i.e. anything with no scheme at all;
  · an absolute `http:` / `https:` URL;
  · `mailto:` / `tel:`.

Everything else → `None`. Callers store NULL, and the link simply does not render (the established
"degrade, never crash" posture: a bad href must never 500 an admin page).

`//evil.tld/x` is REJECTED even though it has no scheme: protocol-relative URLs are an off-site
navigation dressed up as a path, which is not what any of these fields is for.

PURE + DEPENDENCY-FREE — no DB, no network, no imports beyond `re`. Unit-proven in
`backend/harness_export_xss_upload.py`.

Other module agents: `from app.modules.core.safe_href import safe_href` (same import shape as
`import_health.register_provider`). Do NOT re-implement it in your tree.
"""
import re

# The only schemes a stored, user-supplied href may carry.
ALLOWED_SCHEMES = ("http", "https", "mailto", "tel")

# RFC 3986 scheme grammar. Anchored, so `/foo:bar` (a path containing a colon) is NOT a scheme.
_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*)[ \t\r\n]*:")

# Characters a browser strips/ignores while resolving a scheme — the classic smuggling set
# (`java\tscript:`, `\x01javascript:`, a leading NUL).
_IGNORED_RE = re.compile(r"[\x00-\x20\x7f]")

_ENT_HEX_RE = re.compile(r"&#[xX]0*([0-9a-fA-F]{1,6});?")
_ENT_DEC_RE = re.compile(r"&#0*([0-9]{1,7});?")


def _decode_entity(match, base):
    try:
        cp = int(match.group(1), base)
    except (TypeError, ValueError):
        return match.group(0)
    return chr(cp) if 0 <= cp <= 0x10FFFF else match.group(0)


def _canonical(value: str) -> str:
    """The string a BROWSER would see when deciding the scheme: HTML entities decoded, then every
    character it ignores removed. Used ONLY for the decision — the value returned to the caller is
    always the untouched original, so a legitimate href is never rewritten."""
    t = _ENT_HEX_RE.sub(lambda m: _decode_entity(m, 16), value)
    t = _ENT_DEC_RE.sub(lambda m: _decode_entity(m, 10), t)
    return _IGNORED_RE.sub("", t)


def is_safe_href(value) -> bool:
    """True when `value` is safe to render as a link target. Empty/None → False."""
    if value is None:
        return False
    probe = _canonical(str(value)).strip()
    if not probe:
        return False
    m = _SCHEME_RE.match(probe)
    if m:
        return m.group(1).lower() in ALLOWED_SCHEMES
    # No scheme at all ⇒ a relative reference, which cannot be javascript:/data:/vbscript:.
    # The one exception is the protocol-relative form, which navigates off-site.
    return not probe.startswith("//")


def safe_href(value, default=None):
    """Return `value` unchanged when it is a safe link target, else `default` (None).

    Deliberately NON-REWRITING: a legitimate href comes back byte-identical, so this can be dropped
    into an existing `clean_*` normalizer without changing a single stored value in practice.
    """
    return value if is_safe_href(value) else default
