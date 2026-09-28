"""One home for "an operator-typed base URL" (owner 2026-09-28, index §16).

Every service address the platform calls or hands out (the sweeps worker, its own public API, the frontend, Supabase)
is typed by a human into the host's environment settings. Hosts show the address WITHOUT a scheme
("worker-production.up.railway.app"), so that is what gets pasted — and httpx then refuses it ("Request URL is missing
an 'http://' or 'https://' protocol"), pg_net posts to nowhere, and a link in an email goes to a relative path. The
supply catalog reader surfaced it (live 2026-09-28); every browser endpoint behind the proxy was equally broken.

base_url() is the ONE normaliser; settings (config.py) and service_role.browser_service_url() both call it, and
harness_base_url.py fails the build if a caller reads one of these addresses from the environment around it.
Stdlib only.
"""
import re

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)
# Private-network hosts carry no TLS certificate, so a bare private host means plain http.
_PLAIN_HTTP_HOST = re.compile(r"^(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|"
                              r"172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|\[?::1\]?|[^/:]+\.internal|[^/:.]+)(:\d+)?(/|$)",
                              re.I)


def base_url(raw) -> str:
    """The address as a caller can use it: trimmed, a scheme ("https://", or "http://" for a private host such as
    `*.internal`, localhost or a bare single-label name), no trailing slash. "" stays "" (unset means unset)."""
    s = str(raw or "").strip().strip("'\"").strip()
    if not s:
        return ""
    if s.startswith("//"):
        s = s[2:]
    if not _SCHEME.match(s):
        s = ("http://" if _PLAIN_HTTP_HOST.match(s) else "https://") + s
    return s.rstrip("/")
