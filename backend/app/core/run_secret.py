"""Shared verification of the run/cron shared secret — constant-time + rotation (Spec §4, item 9).

~24 scheduler/cron endpoints (every `*/run-due` sweep, plus a few dual-auth endpoints) authenticate a
JWT-less caller by a shared `x-notify-secret` header compared against settings.NOTIFY_RUN_SECRET. Two
weaknesses this closes:

  • TIMING. The comparisons used plain `!=`/`==`, whose early-exit leaks a byte-by-byte timing signal.
    Low practical risk over the network, but free to remove: use `hmac.compare_digest`.
  • ROTATION. There was ONE static secret and no way to roll it without a flag-day. This accepts an
    optional second secret `NOTIFY_RUN_SECRET_NEXT`, so an operator can set the new value on the
    schedulers, cut over, then retire the old one — zero-downtime rotation.

Fail CLOSED: no secret configured, or an empty/absent header, → False (the endpoint 403s). Verifying
against every configured secret without early exit keeps the constant-time property across rotation.
"""
import os
import hmac

from app.core.config import settings


def _configured_secrets():
    """All currently-valid secrets: the primary (settings/env) plus an optional rotation secret. Empty
    list ⇒ nothing configured ⇒ every verify fails closed."""
    out = []
    primary = (getattr(settings, "NOTIFY_RUN_SECRET", "") or "").strip()
    if primary:
        out.append(primary)
    nxt = (os.environ.get("NOTIFY_RUN_SECRET_NEXT", "") or "").strip()
    if nxt:
        out.append(nxt)
    return out


def verify_notify_secret(provided) -> bool:
    """Constant-time check of the presented header against the configured secret(s). False when nothing
    is configured or the header is empty (fail closed)."""
    provided = (provided or "")
    secrets = _configured_secrets()
    if not secrets or not provided:
        return False
    ok = False
    for s in secrets:
        # No early break: OR-accumulate so total work doesn't depend on WHICH secret matched.
        if hmac.compare_digest(provided, s):
            ok = True
    return ok


if __name__ == "__main__":
    os.environ.pop("NOTIFY_RUN_SECRET_NEXT", None)
    # With a primary configured (simulate via monkeypatch on settings):
    class _S:  # minimal stand-in
        NOTIFY_RUN_SECRET = "abc123"
    import app.core.run_secret as m
    m.settings = _S()
    assert m.verify_notify_secret("abc123") is True
    assert m.verify_notify_secret("nope") is False
    assert m.verify_notify_secret("") is False
    m.settings = type("X", (), {"NOTIFY_RUN_SECRET": ""})()
    assert m.verify_notify_secret("anything") is False       # unset → fail closed
    # rotation: NEXT secret also accepted
    os.environ["NOTIFY_RUN_SECRET_NEXT"] = "newkey"
    m.settings = _S()
    assert m.verify_notify_secret("newkey") is True and m.verify_notify_secret("abc123") is True
    print("run_secret self-tests passed")
