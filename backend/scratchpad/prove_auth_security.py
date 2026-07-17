"""Proof harness for auth-hardening PURE logic (core/auth_security.py) + the anti-enumeration invariant
of the self-serve reset endpoint. Offline, no DB / network. Run:  python3 backend/scratchpad/prove_auth_security.py

Covers (Gate-1 evidence):
  A. validate_password matrix — min/max/classes/hard-128-cap/128+ reject/config variants
  B. gen_temp_password ALWAYS satisfies the owner default policy (1000 iterations)
  C. OTP: gen_otp format, hash determinism + email-binding, verify decision (expiry/attempts/used/mismatch)
  D. OTP rate-limit + expiry pure decisions
  E. 2FA marker mint/verify (valid / tampered / expired / wrong-login / wrong-org / account-scoped)
  F. masking (email/phone)
  G. ANTI-ENUMERATION: /auth/forgot-password returns a byte-identical body for an existing vs a
     non-existing email (driven against the REAL endpoint with a faked client + no-op sender).
"""
import sys, os, asyncio, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.modules.core import auth_security as S

P = F = 0
def ck(name, cond):
    global P, F
    if cond: P += 1; print(f"  ok  {name}")
    else: F += 1; print(f"  XX  {name}")

DEF = S.DEFAULT_PASSWORD_POLICY

print("A. validate_password / password_errors matrix")
ck("owner default rejects <8", S.password_errors(DEF, "Aa1!xy") != [])          # 6 chars
ck("owner default rejects >12", S.password_errors(DEF, "Aa1!aaaaaaaaaa") != [])  # 14 chars
ck("owner default accepts 10-char all-class", S.password_errors(DEF, "Aa1!bcdefg") == [])
ck("missing upper flagged", any("upper" in e.lower() for e in S.password_errors(DEF, "aa1!bcdefg")))
ck("missing lower flagged", any("lower" in e.lower() for e in S.password_errors(DEF, "AA1!BCDEFG")))
ck("missing digit flagged", any("number" in e.lower() for e in S.password_errors(DEF, "Aa!bcdefgh")))
ck("missing special flagged", any("special" in e.lower() for e in S.password_errors(DEF, "Aa1bcdefgh")))
# HARD CAP — 129 chars rejected regardless of a permissive tenant policy
loose = {"min_length": 4, "max_length": 999, "require_upper": False, "require_lower": False,
         "require_digit": False, "require_special": False}
ck("normalize clamps max to 128", S.normalize_policy(loose)["max_length"] == 128)
ck("hard cap rejects 129 even on loose policy", S.password_errors(loose, "a" * 129) != [])
ck("hard cap message is the ONLY error at 129", S.password_errors(loose, "a" * 129) == ["Password must be at most 128 characters."])
ck("10000-char DoS body rejected instantly", S.password_errors(DEF, "a" * 10000) == ["Password must be at most 128 characters."])
ck("128 chars allowed by loose policy", S.password_errors(loose, "a" * 128) == [])
ck("bool as length is ignored (not treated as int)", S.normalize_policy({"min_length": True})["min_length"] == DEF["min_length"])
ck("min floor >=4", S.normalize_policy({"min_length": 1})["min_length"] == 4)
ck("max never below min", S.normalize_policy({"min_length": 10, "max_length": 5})["max_length"] == 10)
# a stricter tenant policy
strict = {"min_length": 12, "max_length": 20, "require_upper": True, "require_lower": True, "require_digit": True, "require_special": True}
ck("strict tenant rejects 10-char", S.password_errors(strict, "Aa1!bcdefg") != [])
ck("strict tenant accepts 12-char all-class", S.password_errors(strict, "Aa1!bcdefghi") == [])

print("B. gen_temp_password satisfies the DEFAULT policy (1000x)")
allpass = True
for _ in range(1000):
    tp = S.gen_temp_password()
    if S.password_errors(DEF, tp):
        allpass = False; break
ck("1000 auto temp passwords all pass the owner default policy", allpass)
ck("temp pw also passes when generated for a strict tenant", all(
    S.password_errors(strict, S.gen_temp_password(strict)) == [] for _ in range(200)))
ck("temp pw for a low-max tenant still >=8 (satisfies default)", all(
    len(S.gen_temp_password({"min_length": 6, "max_length": 6})) >= 8 for _ in range(100)))

print("C. OTP hash + verify decision")
ck("gen_otp is 6 numeric digits", all(len(S.gen_otp()) == 6 and S.gen_otp().isdigit() for _ in range(50)))
ck("gen_otp preserves leading zeros (length always 6)", all(len(S.gen_otp()) == 6 for _ in range(500)))
h = S.hash_otp("123456", "A@B.com")
ck("hash deterministic + case-normalizes email", h == S.hash_otp("123456", "a@b.com"))
ck("hash binds to email (diff email → diff hash)", h != S.hash_otp("123456", "c@d.com"))
ck("otp_matches true on correct", S.otp_matches(h, "123456", "a@b.com"))
ck("otp_matches false on wrong code", not S.otp_matches(h, "000000", "a@b.com"))
now = 1000.0
good = {"code_hash": S.hash_otp("111222", "u@x.com"), "attempts": 0, "max_attempts": 5,
        "expires_at": now + 600, "consumed_at": None}
ck("verify ok on valid", S.otp_verify_decision(good, "111222", "u@x.com", now) == (True, "ok"))
ck("verify mismatch", S.otp_verify_decision(good, "999999", "u@x.com", now) == (False, "mismatch"))
ck("verify expired", S.otp_verify_decision({**good, "expires_at": now - 1}, "111222", "u@x.com", now)[1] == "expired")
ck("verify used", S.otp_verify_decision({**good, "consumed_at": "2026"}, "111222", "u@x.com", now)[1] == "used")
ck("verify too-many-attempts", S.otp_verify_decision({**good, "attempts": 5}, "111222", "u@x.com", now)[1] == "too_many_attempts")
ck("verify missing row", S.otp_verify_decision(None, "111222", "u@x.com", now) == (False, "missing"))

print("D. rate-limit + expiry pures")
ck("rate limited at max", S.otp_rate_limited(5, 5) and S.otp_rate_limited(6, 5))
ck("not rate limited below max", not S.otp_rate_limited(4, 5))
ck("expired boundary", S.otp_is_expired(now, now) and not S.otp_is_expired(now, now + 1))
ck("iso expiry parse", not S.otp_is_expired(0, "2999-01-01T00:00:00+00:00"))

print("E. 2FA marker mint/verify")
exp = S.now_ts() + 600
tok = S.mint_2fa_token("auth-1", "org-A", "dev-1", exp)
ck("valid marker verifies for its login+org", S.twofa_token_valid_for(tok, "auth-1", "org-A", S.now_ts()))
ck("wrong login rejected", not S.twofa_token_valid_for(tok, "auth-2", "org-A", S.now_ts()))
ck("wrong org rejected", not S.twofa_token_valid_for(tok, "auth-1", "org-B", S.now_ts()))
ck("expired marker rejected", not S.twofa_token_valid_for(tok, "auth-1", "org-A", exp + 1))
ck("tampered signature rejected", not S.twofa_token_valid_for(tok[:-2] + ("aa" if not tok.endswith("aa") else "bb"), "auth-1", "org-A", S.now_ts()))
ck("garbage token rejected", S.verify_2fa_token("not.a.token", S.now_ts()) is None and S.verify_2fa_token("", S.now_ts()) is None)
acct_tok = S.mint_2fa_token("auth-1", "", "dev-1", exp)
ck("account-scoped marker (org '') valid for any org", S.twofa_token_valid_for(acct_tok, "auth-1", "org-Z", S.now_ts()))

print("F. masking")
ck("mask_email hides local", S.mask_email("rajiv.jaggi@celllularservices.net").endswith("@celllularservices.net")
   and S.mask_email("rajiv.jaggi@celllularservices.net")[0] == "r" and "*" in S.mask_email("rajiv.jaggi@celllularservices.net"))
ck("mask_phone shows last 4 only", S.mask_phone("+15551234567").endswith("4567") and "1234567" not in S.mask_phone("+15551234567"))

print("G. ANTI-ENUMERATION — /auth/forgot-password identical body for existing vs non-existing email")
import app.modules.core.router as R

class _FakeTable:
    def __init__(self, store): self.store = store; self._email = None
    def select(self, *a, **k): return self
    def eq(self, col, val):
        if col == "email": self._email = (val or "").lower()
        return self
    def gte(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def order(self, *a, **k): return self
    def insert(self, row): self.store.setdefault("otp", []).append(row); return self
    def update(self, *a, **k): return self
    class _R:
        def __init__(self, data): self.data = data
    def execute(self):
        # app_users lookup by email → return a live account only for the "existing" address
        if self._email == "exists@known.com":
            return _FakeTable._R([{"org_id": "org-1", "auth_id": "auth-x", "phone": None, "phone_verified": False}])
        return _FakeTable._R([])   # otp probe / rate window / unknown email → empty

class _FakeSchema:
    def __init__(self, store): self.store = store
    def table(self, name): return _FakeTable(self.store)

class _FakeClient:
    def __init__(self): self.store = {}
    def schema(self, name): return _FakeSchema(self.store)

class _FakeReq:
    headers = {}
    class client: host = "1.2.3.4"

async def _run_forgot(email):
    store_client = _FakeClient()
    R.sb = lambda: store_client                          # patch DB
    async def _noop_send(*a, **k): return [(True, "email", None)]
    R._anotify.send_reset_otp = _noop_send               # patch sender
    R._audit_auth_event = lambda *a, **k: None           # silence audit
    return await R.forgot_password({"email": email}, _FakeReq())

body_exists = asyncio.run(_run_forgot("exists@known.com"))
body_missing = asyncio.run(_run_forgot("nobody@unknown.com"))
ck("existing-email response == non-existing-email response (byte-identical)",
   json.dumps(body_exists, sort_keys=True) == json.dumps(body_missing, sort_keys=True))
ck("both are the generic 'if this email has an account' message",
   body_exists.get("message", "").lower().startswith("if this email has an account"))

print(f"\n{'PASS' if F == 0 else 'FAIL'}: {P} passed, {F} failed")
sys.exit(1 if F else 0)
