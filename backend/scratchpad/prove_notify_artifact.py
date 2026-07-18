"""Proof harness — notify no-login download ROUTER behaviors (Gate-1 rework fixes B1 / M1 / M2 / m1 / m2).

Run:  cd backend && python3 scratchpad/prove_notify_artifact.py
Exercises the router-level helpers with a STUBBED Supabase client (no network):
  • B1 — _store_artifact swallows a PostgREST permission-denied APIError → None (live-report-link fallback),
         proving the try/except catches a real permission-denied error object, not merely a missing table.
  • m1 — _store_artifact skips storage over the 8MB cap → None, without touching the DB.
  • M2 — with NO download secret, sign() is None → _store_artifact None; the /dl endpoint 404s (verify None).
  • m2 — _content_disposition is latin-1 safe for CRLF / CJK / emoji / quotes (no 500 on a VALID token).
  • M1 — 'notify_policy' is a registered settings area and _can_edit_setting gates it per the doctrine.
Pure decision logic; the only side effects are the in-process stubs. No network.
"""
import os
import sys
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_SERVICE_KEY", "proof-secret-A")

from fastapi import HTTPException                               # noqa: E402
from app.modules.notify import router as R                     # noqa: E402
from app.modules.notify import download_token as DT            # noqa: E402
from app.modules.core import router as CR                       # noqa: E402

passed = failed = 0


def ok(name, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL: {name}")


# A realistic permission-denied error: PostgREST raises postgrest.exceptions.APIError (a plain Exception
# subclass). If the shape differs, fall back to an equivalent local Exception subclass.
try:
    from postgrest.exceptions import APIError                   # noqa: E402
    PERM_ERR = APIError({"message": "permission denied for table send_artifact",
                         "code": "42501", "hint": None, "details": None})
except Exception:
    class APIError(Exception):
        pass
    PERM_ERR = APIError("permission denied for table send_artifact")


class _RaiseExec:
    def execute(self):
        raise PERM_ERR


class _RaiseTable:
    def insert(self, row):
        return _RaiseExec()


class _RaiseSchema:
    def table(self, name):
        return _RaiseTable()


class _OkExec:
    def execute(self):
        return type("Res", (), {"data": [{"id": "aid-happy-1"}]})()


class _OkTable:
    def insert(self, row):
        return _OkExec()


class _OkSchema:
    def table(self, name):
        return _OkTable()


_orig_sb = R.sb
_orig_expiry = R._download_expiry_days
R._download_expiry_days = lambda org: 7   # avoid a real DB read for expiry

# ── B1: permission-denied APIError on insert → None (caught, link fallback), NEVER a crash ──
R.sb = lambda: _RaiseSchema()
crashed = False
res = None
try:
    res = R._store_artifact("org-1", "report.pdf", "application/pdf", b"hello")
except Exception as e:
    crashed = True
    print("    unexpected raise:", repr(e))
ok("B1: permission-denied APIError on insert → None (caught)", res is None and not crashed)
ok("B1: the simulated error is a PostgREST APIError (Exception subclass)", isinstance(PERM_ERR, Exception))
ok("B1: message signals permission-denied (NOT a missing table)", "permission denied" in str(PERM_ERR).lower())

# ── m1: over-cap payload → None BEFORE any DB call ──
R.sb = lambda: (_ for _ in ()).throw(AssertionError("DB must not be touched over-cap"))
big = b"x" * (R.MAX_ARTIFACT_BYTES + 1)
ok("m1: over-cap artifact → None (no DB touched)",
   R._store_artifact("org-1", "big.pdf", "application/pdf", big) is None)
ok("m1: empty payload → None", R._store_artifact("org-1", "e.pdf", "application/pdf", b"") is None)

# ── happy path: insert ok + secret configured → a /notify/dl URL whose token verifies ──
R.sb = lambda: _OkSchema()
url = R._store_artifact("org-1", "report.pdf", "application/pdf", b"hello")
ok("happy: returns a /api/v1/notify/dl/ URL", isinstance(url, str) and "/api/v1/notify/dl/" in url)
ok("happy: URL token verifies to the inserted id", bool(url) and DT.verify(url.rsplit("/", 1)[-1]) == "aid-happy-1")

# ── M2: no secret → sign None → _store_artifact None even on a good insert; endpoint 404s ──
_orig_secret = DT._secret
DT._secret = lambda: None
ok("M2: no secret → _store_artifact returns None (link fallback)",
   R._store_artifact("org-1", "report.pdf", "application/pdf", b"hello") is None)
try:
    asyncio.run(R.download_artifact("whatever.token"))
    ok("M2: no-secret download endpoint 404s", False)
except HTTPException as e:
    ok("M2: no-secret download endpoint 404s", e.status_code == 404)
except Exception as e:
    ok("M2: no-secret download endpoint 404s", False)
    print("    unexpected:", repr(e))
DT._secret = _orig_secret

R.sb = _orig_sb
R._download_expiry_days = _orig_expiry


# ── m2: Content-Disposition safe for ANY filename (latin-1 encodable → no 500 on a valid token) ──
def _cd_ok(fn):
    cd = R._content_disposition(fn)
    if "\r" in cd or "\n" in cd:
        return False
    try:
        cd.encode("latin-1")       # the ASGI server encodes headers as latin-1; must never raise
    except Exception:
        return False
    return cd.startswith("attachment;") and 'filename="' in cd


ok("m2: CRLF filename → safe (no CR/LF, latin-1 ok)", _cd_ok("evil\r\nSet-Cookie: x.pdf"))
ok("m2: CJK filename → safe + filename* present",
   _cd_ok("报表.pdf") and "filename*=UTF-8''" in R._content_disposition("报表.pdf"))
ok("m2: emoji filename → safe", _cd_ok("sales-🚀.xlsx"))
ok("m2: quote/backslash filename → safe", _cd_ok('a"b\\c.pdf'))
ok("m2: empty/None filename → report fallback",
   R._content_disposition(None) == "attachment; filename=\"report\"; filename*=UTF-8''report")
ok("m2: control-only filename → non-empty ascii fallback", _cd_ok("\x01\x02\x03"))

# ── M1: notify_policy registered + _can_edit_setting gate logic (per-setting edit-permissions doctrine) ──
ok("M1: notify_policy registered in SETTING_AREAS", any(a["key"] == "notify_policy" for a in CR.SETTING_AREAS))
ok("M1: super_admin may edit notify_policy", CR._can_edit_setting({"super_admin": True}, "notify_policy") is True)
ok("M1: scope=all admin may edit notify_policy", CR._can_edit_setting({"perms": {"scope": "all"}}, "notify_policy") is True)
ok("M1: 'admin' role may edit notify_policy", CR._can_edit_setting({"role": "admin", "perms": {}}, "notify_policy") is True)
ok("M1: plain rep may NOT edit notify_policy", CR._can_edit_setting({"role": "rep", "perms": {}}, "notify_policy") is False)
ok("M1: explicit role grant enables a non-admin",
   CR._can_edit_setting({"role": "rep", "perms": {"settings": {"notify_policy": True}}}, "notify_policy") is True)
ok("M1: explicit deny overrides admin",
   CR._can_edit_setting({"role": "admin", "perms": {"settings": {"notify_policy": False}}}, "notify_policy") is False)
ok("M1: no caller → cannot edit", CR._can_edit_setting(None, "notify_policy") is False)

print(f"\nprove_notify_artifact: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
