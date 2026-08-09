"""HARNESS — consent before collection (people, 2026-08-09, BIPA 740 ILCS 14/15(b)).

WHAT THIS PROTECTS. 15(b) forbids CAPTURING a biometric identifier until the subject has been informed
in writing and has signed a written release. Before migration 424 nothing enforced it: the enrollment
endpoint checked only whether the FEATURE was on. An employee recorded as `declined` was blocked; an
employee with NO consent record at all enrolled freely — and "no record" described all 77 descriptors
on file on 2026-08-09.

  1  no consent record at all → REFUSED (the case that was previously wide open)
  2  consent explicitly `declined` → REFUSED
  3  consent `signed` but with NO date → REFUSED. A record that cannot show consent PRECEDED
     collection does not satisfy 15(b), and preceding is the whole requirement.
  4  consent `signed` and dated in the FUTURE → REFUSED (same reason, from the other side)
  5  consent `signed` and dated in the past → ALLOWED
  6  the read failing → REFUSED. This is the one guard in the codebase that fails CLOSED rather than
     open: the cost of a wrong refusal is one retry at a kiosk; the cost of a wrong acceptance is a
     per-person statutory exposure that deleting the row afterwards does not undo.
  7  every refusal message names the remedy, because the person reading it is a cashier at a kiosk,
     not an engineer.

The DATABASE half (migration 424's trigger) is what actually binds every write path; it was proven
separately against the live database with a rolled-back probe covering: no-consent INSERT blocked,
consented INSERT allowed, DELETE still permitted (the retention job must never be blocked by a consent
problem — that would keep biometric data LONGER), and future-dated consent blocked.

Pure/offline — no database, no keys.
    python3 backend/harness_face_consent_gate.py
"""
import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")


class FakeQuery:
    def __init__(self, rows, boom=False):
        self._rows = rows
        self._boom = boom

    def select(self, *_a, **_k):
        if self._boom:
            raise RuntimeError("PostgREST is down")
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


class FakeSB:
    def __init__(self, rows, boom=False):
        self._rows, self._boom = rows, boom

    def table(self, _n):
        if self._boom:
            raise RuntimeError("PostgREST is down")
        return FakeQuery(self._rows)


def run():
    import app.modules.storeops.router as R

    def with_rows(rows, boom=False):
        R.sb = lambda: FakeSB(rows, boom)          # noqa: E731 — deliberate test double
        return R._face_consent_ok("ORG", "E1")

    past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()

    ok, why = with_rows([{}])
    check("1. no consent record at all → REFUSED", ok is False)
    check("1b. …and the message says a signed form is needed", "signed" in why.lower())

    ok, _ = with_rows([{"face_consent_status": "declined", "face_consent_at": past}])
    check("2. consent declined → REFUSED", ok is False)

    ok, why = with_rows([{"face_consent_status": "signed", "face_consent_at": None}])
    check("3. signed but undated → REFUSED", ok is False)
    check("3b. …and the message asks for the date", "date" in why.lower())

    ok, why = with_rows([{"face_consent_status": "signed", "face_consent_at": future}])
    check("4. signed but dated in the FUTURE → REFUSED", ok is False)
    check("4b. …and the message says why a future date fails", "future" in why.lower())

    ok, _ = with_rows([{"face_consent_status": "signed", "face_consent_at": past}])
    check("5. signed and dated in the past → ALLOWED", ok is True)

    ok_z, _ = with_rows([{"face_consent_status": "signed",
                          "face_consent_at": past.replace("+00:00", "Z")}])
    check("5b. a Z-suffixed timestamp is accepted (not every writer emits +00:00)", ok_z is True)

    ok_naive, _ = with_rows([{"face_consent_status": "signed",
                              "face_consent_at": "2026-07-01T10:00:00"}])
    check("5c. a naive timestamp is treated as UTC, not rejected", ok_naive is True)

    ok_date, _ = with_rows([{"face_consent_status": "signed", "face_consent_at": "2026-07-01"}])
    check("5d. a plain date (what an admin actually types) is accepted", ok_date is True)

    ok, why = with_rows([], boom=True)
    check("6. an unreadable consent record → REFUSED (fails CLOSED)", ok is False)
    check("6b. …and says nothing was saved", "nothing was saved" in why.lower())

    ok, why = with_rows([{"face_consent_status": "signed", "face_consent_at": "not-a-date"}])
    check("6c. an unparseable date → REFUSED, never silently accepted", ok is False)

    msgs = []
    for rows in ([{}],
                 [{"face_consent_status": "declined", "face_consent_at": past}],
                 [{"face_consent_status": "signed", "face_consent_at": None}],
                 [{"face_consent_status": "signed", "face_consent_at": future}]):
        msgs.append(with_rows(rows)[1])
    check("7. every refusal names who can fix it (manager)",
          all("manager" in m.lower() for m in msgs))
    check("7b. no refusal leaks a column name or a stack detail",
          all("face_consent" not in m and "Exception" not in m for m in msgs))

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print("  FAILED: " + f)
        return 1
    return 0


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    sys.exit(run())
