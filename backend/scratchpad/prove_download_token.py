"""Proof harness — notify no-login download token signer/verifier + expiry semantics.

Run:  cd backend && python3 scratchpad/prove_download_token.py
Covers: sign/verify round-trip · tamper (sig + id) → None · wrong-file isolation · malformed/empty →
None · secret rotation invalidates · expiry (otp_is_expired reuse) · anti-enumeration uniformity of the
'None' verdict. Pure — no DB, no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ensure a deterministic secret for the proof (config falls back to this when unset).
os.environ.setdefault("SUPABASE_SERVICE_KEY", "proof-secret-A")

from app.modules.notify import download_token as DT           # noqa: E402
from app.modules.core import auth_security as SEC             # noqa: E402

passed = failed = 0


def ok(name, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL: {name}")


AID_A = "11111111-1111-1111-1111-111111111111"
AID_B = "22222222-2222-2222-2222-222222222222"

# ── round trip ────────────────────────────────────────────────────────────────
tok_a = DT.sign(AID_A)
tok_b = DT.sign(AID_B)
ok("round-trip A", DT.verify(tok_a) == AID_A)
ok("round-trip B", DT.verify(tok_b) == AID_B)
ok("two ids → two distinct tokens", tok_a != tok_b)
ok("token has body.sig shape", tok_a.count(".") == 1 and all(tok_a.split(".")))

# ── wrong-file isolation: a token for A can never resolve to B ──────────────────
ok("A-token resolves to A only", DT.verify(tok_a) != AID_B)
# swap A's encoded id for B's body but keep A's signature → must fail (sig binds the id)
body_b = tok_b.split(".")[0]
sig_a = tok_a.split(".")[1]
ok("spliced (B-body + A-sig) → None", DT.verify(f"{body_b}.{sig_a}") is None)

# ── tamper ─────────────────────────────────────────────────────────────────────
body_a, sa = tok_a.split(".")
flipped_sig = sa[:-1] + ("A" if sa[-1] != "A" else "B")
ok("tampered signature → None", DT.verify(f"{body_a}.{flipped_sig}") is None)
ok("tampered body (unsigned id) → None", DT.verify(f"{body_a}x.{sa}") is None)
ok("swapped halves → None", DT.verify(f"{sa}.{body_a}") is None)

# ── malformed / empty ──────────────────────────────────────────────────────────
for bad in ("", None, "no-dot", ".", "a.", ".b", "....", "!!!.???", "a.b.c"):
    ok(f"malformed {bad!r} → None", DT.verify(bad) is None)

# ── secret rotation invalidates every outstanding token ────────────────────────
# recompute the signature under a different secret and confirm it no longer matches the live verifier.
import hashlib, hmac, base64                                   # noqa: E402


def _b64u(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


forged_sig = _b64u(hmac.new(b"proof-secret-B", AID_A.encode(), hashlib.sha256).digest())
ok("token signed under a DIFFERENT secret → None", DT.verify(f"{body_a}.{forged_sig}") is None)

# ── expiry (the endpoint enforces it via SEC.otp_is_expired against the artifact row) ──
now = SEC.now_ts()
ok("expired artifact (past) → expired", SEC.otp_is_expired(now, now - 60) is True)
ok("live artifact (future) → not expired", SEC.otp_is_expired(now, now + 3600) is False)
from datetime import datetime, timezone, timedelta            # noqa: E402
future_iso = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
past_iso = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
ok("ISO future not expired", SEC.otp_is_expired(now, future_iso) is False)
ok("ISO past expired", SEC.otp_is_expired(now, past_iso) is True)
ok("null expiry treated expired (safe)", SEC.otp_is_expired(now, None) is True)

# ── anti-enumeration: every failure verdict is the identical `None` (no oracle) ─
verdicts = {DT.verify(v) for v in ("", "junk", f"{body_a}.{flipped_sig}", f"{body_b}.{sig_a}")}
ok("all failure modes collapse to a single None verdict", verdicts == {None})

print(f"\nprove_download_token: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
