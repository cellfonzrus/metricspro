"""HARNESS — the acting tenant is a CHOICE, never a guess (platform-core, 2026-08-09).

WHAT THIS PROTECTS. `_pick_active_org` honored the untrusted `x-active-org` header when it named one of
the login's tenants, and otherwise fell back to `default_org` — which is the `is_default_org` row, ELSE
THE EARLIEST-CREATED MEMBERSHIP. Verified live on 2026-08-09: **no `storeops.app_users` row anywhere has
`is_default_org` set**, so for every multi-tenant login the fallback was, in practice, "whichever tenant
you joined first." A login belonging to three companies was served its oldest (26 stores, 138k sales
rows) while the human believed they were in a brand-new empty one.

Nothing was disclosed that the user was not entitled to see — they administer all three. But "entitled
to it" is not "asked for it", and the next such login may not be entitled. The fix refuses to guess.

  1  a SINGLE-membership login is never ambiguous — its one tenant is the only answer. 92 of the 96
     logins alive when this shipped are single-membership, so this is the case that must not move.
  2  >1 membership + a VALID x-active-org → honored, not ambiguous (the switcher keeps working)
  3  >1 membership + an INVALID/foreign x-active-org → AMBIGUOUS. Deliberate: silently downgrading a
     header that names a tenant you do NOT belong to, to "your oldest tenant", is how the original bug
     read. A wrong answer is worse than a refusal.
  4  >1 membership + NO header → AMBIGUOUS (the exact 2026-08-09 case)
  5  >1 membership + no header but an EXPLICIT is_default_org → honored. A stated home tenant IS a
     choice; only the created-first fallback is a guess.
  6  zero memberships → not ambiguous here (fail-closed 401 already handles it upstream, H2 2026-08-05)
  7  the header is still never trusted on its own — `_pick_active_org` must not return a non-member org
  8  NEGATIVE CONTROL: the pre-fix logic (fall back to member_orgs[0]) must FAIL case 4. A harness that
     passes against the bug it was written for is decoration.
  9  the kill switch exists and defaults ON (AMBIGUOUS_TENANT_STRICT=0 restores the old behaviour)
 10  the refusal is 409 + code `tenant_choice_required` — NOT 401. The session is valid; tearing it down
     would sign the user out when the fix is to pick a company.

Pure/offline: every function under test is side-effect free.
    python3 backend/harness_ambiguous_tenant.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core import tenant_middleware as tm   # noqa: E402

A = "00000000-0000-0000-0000-000000000001"   # house / Cellfonz — the oldest membership in the live case
B = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"   # Luxelink
C = "f4f1c16e-2acf-4221-854a-c29a605754a7"   # Vzone — newest, the one the human thought they were in
FOREIGN = "99999999-9999-9999-9999-999999999999"

_passed, _failed = 0, 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}{(' — ' + str(detail)[:200]) if detail else ''}")


def amb(orgs, requested, default_org):
    """`default_org` is what _resolve_identity yields: an explicit is_default_org row, the single
    membership when there is only one, or None when there are several and none is declared."""
    return tm._tenant_is_ambiguous(orgs, requested, default_org)


def main():
    print(__doc__.splitlines()[0])

    # 1 — the case that must not move.
    check("1a. single membership, no header → NOT ambiguous", amb((A,), "", A) is False)
    check("1b. single membership, foreign header → NOT ambiguous", amb((A,), FOREIGN, A) is False)
    check("1c. single membership still resolves to its one org",
          tm._pick_active_org((A,), A, "") == A)

    # 2 — the switcher keeps working.
    check("2a. 3 memberships + valid header → NOT ambiguous", amb((A, B, C), C, None) is False)
    check("2b. …and it resolves to the REQUESTED tenant, not the oldest",
          tm._pick_active_org((A, B, C), A, C) == C)

    # 3 — a header naming a tenant you do not belong to.
    check("3. 3 memberships + FOREIGN header → ambiguous", amb((A, B, C), FOREIGN, None) is True)

    # 4 — the live 2026-08-09 case.
    check("4a. 3 memberships + no header → AMBIGUOUS", amb((A, B, C), "", None) is True)
    check("4b. 2 memberships + no header → AMBIGUOUS", amb((A, B), "", None) is True)

    # 5 — a stated home tenant is a choice, not a guess.
    check("5a. 3 memberships + no header + explicit is_default_org → NOT ambiguous",
          amb((A, B, C), "", B) is False)
    check("5b. …and it resolves to that explicit default", tm._pick_active_org((A, B, C), B, "") == B)

    # 6 — unprovisioned is someone else's job (H2, fail-closed 401 upstream).
    check("6. zero memberships → not flagged here", amb((), "", None) is False)

    # 7 — the header is a hint, never the authority.
    check("7a. foreign header never becomes the acting org",
          tm._pick_active_org((A, B), A, FOREIGN) == A)
    check("7b. empty header falls back, never to a non-member",
          tm._pick_active_org((A, B), A, "") in (A, B))

    # 8 — NEGATIVE CONTROL. Reimplement the pre-fix rule and prove it gets case 4 wrong.
    def pre_fix_pick(member_orgs, requested):
        if requested and requested in member_orgs:
            return requested
        return member_orgs[0] if member_orgs else None
    old = pre_fix_pick((A, B, C), "")
    check("8a. pre-fix logic silently picked the OLDEST tenant (the bug)", old == A, old)
    check("8b. pre-fix logic would NOT have flagged it", old is not None and old == A)
    check("8c. the fix flags exactly that input", amb((A, B, C), "", None) is True)

    # 9 — break-glass.
    prev = os.environ.get("AMBIGUOUS_TENANT_STRICT")
    try:
        os.environ.pop("AMBIGUOUS_TENANT_STRICT", None)
        check("9a. strict defaults ON when unset", tm._ambiguous_tenant_strict() is True)
        for off in ("0", "false", "no", "off", "OFF"):
            os.environ["AMBIGUOUS_TENANT_STRICT"] = off
            if tm._ambiguous_tenant_strict() is not False:
                check(f"9b. AMBIGUOUS_TENANT_STRICT={off} disables it", False, off)
                break
        else:
            check("9b. every documented off-value disables it", True)
        os.environ["AMBIGUOUS_TENANT_STRICT"] = "1"
        check("9c. =1 re-enables it", tm._ambiguous_tenant_strict() is True)
    finally:
        os.environ.pop("AMBIGUOUS_TENANT_STRICT", None)
        if prev is not None:
            os.environ["AMBIGUOUS_TENANT_STRICT"] = prev

    # 10 — the wire response. 409, not 401: the session is fine.
    sent = []

    async def fake_send(msg):
        sent.append(msg)
    asyncio.run(tm._reject_tenant_choice(fake_send))
    start = next((m for m in sent if m["type"] == "http.response.start"), {})
    body = next((m for m in sent if m["type"] == "http.response.body"), {}).get("body", b"")
    check("10a. status is 409, NOT 401 (the session must survive)", start.get("status") == 409, start)
    parsed = json.loads(body.decode())
    check("10b. carries code tenant_choice_required",
          parsed.get("code") == "tenant_choice_required", parsed)
    check("10c. the prose names the actual remedy",
          "company" in parsed.get("detail", "").lower(), parsed)
    check("10d. content-length matches the body (no truncated JSON)",
          any(k == b"content-length" and int(v) == len(body) for k, v in start.get("headers", [])))

    print(f"\n{_passed} passed / {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
