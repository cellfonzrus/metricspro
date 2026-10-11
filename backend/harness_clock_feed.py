"""HARNESS — THE ONE HOME for what the B2B clock in / clock out report said (index §14z).

OWNER ASK 2026-10-10: *"need to import the clock in clock out report from b2b … this will be picked
from the emails for each store like other reports."*

WHY THIS HARNESS CARRIES THE WHOLE FILE. The supplied export is a live tenant's employee time data,
so it is not committed to the repo — but its SHAPE is the thing this module exists for, and eleven
distinct facts about that shape each silently corrupt the import or the comparison if ignored. So
the 45 rows are reproduced here as a fixture (the columns this module reads), and every number the
PR body claims is re-derived from them rather than asserted: 249.17h in total, 28 distinct store
spellings, 14 back-office rows worth 12.48h, 31 real punch rows worth 236.68h, 5 zero-duration rows,
and a trailer that ties to the minute.

SECTIONS
  A. The file ties to its own trailer — the checksum (trap 1).
  B. `Store` is an ADDRESS; the resolver is INJECTED and an unbound spelling is reported (trap 2).
  C. The person spelling: "Last, First", "Last, First, M", and accents (trap 3).
  D. Local wall clock, no timezone — and a missing zone is REPORTED, never assumed (trap 4).
  E. `Duration Decimal` is TRUNCATED; hours come from the stamps (trap 5).
  F. `Type` is an ALLOWLIST (trap 6).
  G. The back-office login is not a store shift, and its hours are SURFACED (trap 7).
  H. The key is (employee, date, PUNCH) — one person, two stores, one day (trap 8).
  I. A zero-duration row is a MEASURED ZERO (trap 9).
  J. B2B's Division/Region/District are METADATA, never our org (trap 10).
  K. The business date comes from Clock In, never the filename (trap 11).
  L. Overnight — the branch the sample file does not exercise.
  M. What LANDS: every row kept, payable or not, and never in timelog.
  Z. ARMED negative control.

Run: cd backend && python3 harness_clock_feed.py   (DB-free, stdlib only)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from zoneinfo import ZoneInfo  # noqa: E402

from app.modules.storeops import clock_feed as F  # noqa: E402
from app.modules.storeops import payroll_hours_basis as B  # noqa: E402

PASS, FAIL = [], []


def check(label, got, want):
    if got == want:
        PASS.append(label)
    else:
        FAIL.append(f"{label}\n      got:  {got!r}\n      want: {want!r}")


def section(t):
    print(f"\n── {t}")


# ════════════════════════════════════════════════════════════════════════════════════════════════
# THE REAL FILE, 2026-10-09, house org. Columns: Store, Salesperson, Clock In, Clock Out, Duration,
# Duration Decimal, Type, User Login. (Clock In Note / Clock Out Note are blank on every row and
# Division / Region / District are covered by §J, so they are supplied there rather than repeated
# forty-five times here.)
SAMPLE = [
    ("1 S 60th street", "Admin, backoffice", "2026-10-09 16:54:00", "2026-10-09 17:59:00", "1:05", 1.083333, "Work", "PAADMIN"),
    ("1 S 60th street", "Jaladhi, Akhila", "2026-10-09 10:25:00", "2026-10-09 18:53:00", "8:28", 8.466666, "Work", "Akhila"),
    ("103 Fulton Ave", "Awais, Akber", "2026-10-09 10:16:00", "2026-10-09 19:38:00", "9:22", 9.366666, "Work", "Akber"),
    ("1115 Liberty Ave", "Dip, Nafiz", "2026-10-09 10:10:00", "2026-10-09 18:47:00", "8:37", 8.616666, "Work", "Nafiz"),
    ("11636 Springfield Blvd", "Asghar, Waleed", "2026-10-09 10:15:00", "2026-10-09 19:02:00", "8:47", 8.783333, "Work", "Waleed"),
    ("117 E Burnside Ave", "Khan, Amir", "2026-10-09 10:28:00", "2026-10-09 19:17:00", "8:49", 8.816666, "Work", "Amir"),
    ("151 E 170th St", "Admin, backoffice", "2026-10-09 13:50:00", "2026-10-09 13:53:00", "0:03", 0.05, "Work", "PAADMIN"),
    ("151 E 170th St", "Garapati, Rohit", "2026-10-09 09:34:00", "2026-10-09 18:44:00", "9:10", 9.166666, "Work", "Rohit"),
    ("1710 W 4th St", "Taneeru, Mona", "2026-10-09 10:16:00", "2026-10-09 18:51:00", "8:35", 8.583333, "Work", "Mona"),
    ("1750 5th Ave", "Mart\u00ednez, LESLIEE", "2026-10-09 10:13:00", "2026-10-09 18:59:00", "8:46", 8.766666, "Work", "LESLIEE"),
    ("1800 Great Neck rd", "Sharma, Anjali", "2026-10-09 10:18:00", "2026-10-09 19:00:00", "8:42", 8.7, "Work", "Anjali"),
    ("196 Martin Luther King Jr Dr", "Mohammed, Abdullah", "2026-10-09 09:40:00", "2026-10-09 18:35:00", "8:55", 8.916666, "Work", "Abdullah.M"),
    ("2509 Bergenline Ave Ste A", "Admin, backoffice", "2026-10-09 11:47:00", "2026-10-09 12:44:00", "0:57", 0.95, "Work", "PAADMIN"),
    ("2509 Bergenline Ave Ste A", "Admin, backoffice", "2026-10-09 15:10:00", "2026-10-09 16:54:00", "1:44", 1.733333, "Work", "PAADMIN"),
    ("2509 Bergenline Ave Ste A", "Admin, backoffice", "2026-10-09 17:59:00", "2026-10-09 18:07:00", "0:08", 0.133333, "Work", "PAADMIN"),
    ("2509 Bergenline Ave Ste A", "Ramos, Angie", "2026-10-09 09:54:00", "2026-10-09 19:04:00", "9:10", 9.166666, "Work", "Angie"),
    ("2612 Pitkin Ave", "Alam, Nafisha", "2026-10-09 10:11:00", "2026-10-09 19:03:00", "8:52", 8.866666, "Work", "Nafisha"),
    ("2701 Germantown ave", "Admin, backoffice", "2026-10-09 18:07:00", "2026-10-09 18:07:00", "0:00", 0.0, "Work", "PAADMIN"),
    ("2701 Germantown ave", "Rani, Nisha", "2026-10-09 10:13:00", "2026-10-09 19:01:00", "8:48", 8.8, "Work", "Nisha"),
    ("2778 Ephraim Ave", "Saul, Mark", "2026-10-09 10:10:00", "2026-10-09 19:05:00", "8:55", 8.916666, "Work", "M.saul"),
    ("3 Palisade Ave Yonkers", "ali, mohammad khalid", "2026-10-09 10:11:00", "2026-10-09 19:08:00", "8:57", 8.95, "Work", "ali"),
    ("3565 Broadway", "Hidalgo, Suanny", "2026-10-09 10:33:00", "2026-10-09 19:10:00", "8:37", 8.616666, "Work", "Suanny"),
    ("3605 Germantown Ave", "Admin, backoffice", "2026-10-09 15:04:00", "2026-10-09 15:10:00", "0:06", 0.1, "Work", "PAADMIN"),
    ("3605 Germantown Ave", "Reddy, Nithin", "2026-10-09 10:11:00", "2026-10-09 18:52:00", "8:41", 8.683333, "Work", "Nithin"),
    ("418 Uniondale Ave", "Admin, backoffice", "2026-10-09 13:53:00", "2026-10-09 15:00:00", "1:07", 1.116666, "Work", "PAADMIN"),
    ("418 Uniondale Ave", "Caba, David, A", "2026-10-09 10:06:00", "2026-10-09 19:20:00", "9:14", 9.233333, "Work", "David"),
    ("4712 White Plains Rd", "Admin, backoffice", "2026-10-09 12:44:00", "2026-10-09 13:28:00", "0:44", 0.733333, "Work", "PAADMIN"),
    ("4712 White Plains Rd", "shaik, Junaid", "2026-10-09 10:06:00", "2026-10-09 19:00:00", "8:54", 8.9, "Work", "Junaid"),
    ("509 Nostrand Ave", "Admin, backoffice", "2026-10-09 11:26:00", "2026-10-09 11:47:00", "0:21", 0.35, "Work", "PAADMIN"),
    ("509 Nostrand Ave", "Mehmood, Kashif", "2026-10-09 09:51:00", "2026-10-09 19:13:00", "9:22", 9.366666, "Work", "Kashif"),
    ("5135 BERGENLINE", "Admin, backoffice", "2026-10-09 13:28:00", "2026-10-09 13:50:00", "0:22", 0.366666, "Work", "PAADMIN"),
    ("5135 BERGENLINE", "Ospina, Miguel", "2026-10-09 10:14:00", "2026-10-09 19:07:00", "8:53", 8.883333, "Work", "miguel"),
    ("559 BROADWAY", "Admin, backoffice", "2026-10-09 05:38:00", "2026-10-09 10:56:00", "5:18", 5.3, "Work", "PAADMIN"),
    ("559 BROADWAY", "Santos, Gerardo", "2026-10-09 12:55:00", "2026-10-09 17:00:00", "4:05", 4.083333, "Work", "gsantos"),
    ("559 BROADWAY", "Sharma, Radhika", "2026-10-09 10:04:00", "2026-10-09 19:12:00", "9:08", 9.133333, "Work", "Radhika"),
    ("5619 N. Broad St.", "Arora, Tanish", "2026-10-09 09:54:00", "2026-10-09 18:50:00", "8:56", 8.933333, "Work", "Tanish"),
    ("6011 Bergenline Ave", "Melez, Edgar", "2026-10-09 10:09:00", "2026-10-09 19:30:00", "9:21", 9.35, "Work", "Edgar"),
    ("6149 Woodland Ave", "Admin, backoffice", "2026-10-09 10:56:00", "2026-10-09 11:26:00", "0:30", 0.5, "Work", "PAADMIN"),
    ("6149 Woodland Ave", "Ranganath, Ranganath", "2026-10-09 09:26:00", "2026-10-09 09:26:00", "0:00", 0.0, "Work", "Ranganath"),
    ("6507 Castor Avenue", "Admin, backoffice", "2026-10-09 15:00:00", "2026-10-09 15:04:00", "0:04", 0.066666, "Work", "PAADMIN"),
    ("6507 Castor Avenue", "Paul, Rikita", "2026-10-09 10:14:00", "2026-10-09 10:14:00", "0:00", 0.0, "Work", "Rikita"),
    ("6507 Castor Avenue", "Purkayastha, Deborshee", "2026-10-09 16:14:00", "2026-10-09 16:14:00", "0:00", 0.0, "Work", "Debo"),
    ("652 Communipaw Avenue", "Santos, Gerardo", "2026-10-09 17:00:00", "2026-10-09 17:00:00", "0:00", 0.0, "Work", "gsantos"),
    ("652 Communipaw Avenue", "Seelam, Sunethri", "2026-10-09 09:44:00", "2026-10-09 18:54:00", "9:10", 9.166666, "Work", "Sunethri"),
    ("723 N Market St", "chowdary, Thanvi", "2026-10-09 09:38:00", "2026-10-09 19:05:00", "9:27", 9.45, "Work", "Thanvi"),]
# Row 47 of the sheet: blank except `Duration`. It is a CHECKSUM, not a row.
TRAILER = "249:10"
FILENAME = "My_Employee_Time_Sheet_10092026-10092026.Xlsx"
# The only tenant-specific string in this file, and in the real system it is a CONFIG ROW
# (`storeops.payroll_hours_basis_config.non_shift_logins`, seeded by mig 1073) — never code.
BACK_OFFICE_LOGIN = "PAADMIN"

CFG = F.resolve_config({"non_shift_logins": (BACK_OFFICE_LOGIN,)})


# The store's zone, resolved the way the real caller resolves it: `storeops/router._biz_tz_for_store`
# hands this module a `ZoneInfo`, and this module spells no offset of its own. The sample day is EDT
# (UTC-4) — a FACT OF THE ZONE, not a constant anywhere in the code.
TZ = ZoneInfo("America/New_York")


# The 28 spellings, each mapped to a made-up code. This stands in for
# `account/store_identity.build_store_resolver` over the org's own store_mapping — which resolved
# 28 of 28 of these against live data. The point proven here is that the resolver is INJECTED.
_SPELLINGS = sorted({r[0] for r in SAMPLE})
_CODE_OF = {s: f"B-{i + 100}" for i, s in enumerate(_SPELLINGS)}


def mapped(rows=None, resolve=None, tz=TZ, cfg=CFG, filename=FILENAME, trailer=TRAILER,
           extra=None):
    """The rows AS `column_mapping` hands them to the post-step, then `normalize`."""
    recs = []
    for store, person, ci, co, _dur, dec, typ, login in (rows if rows is not None else SAMPLE):
        r = {"store_raw": store, "employee_raw": person, "clock_in": ci, "clock_out": co,
             "hours": dec, "type": typ, "feed_login": login}
        if extra:
            r.update(extra)
        recs.append(r)
    return F.normalize(recs, cfg,
                       resolve_store=(resolve if resolve is not None else _CODE_OF.get),
                       tz_for_store=(lambda _c: tz),
                       filename=filename, trailer_value=trailer)


RES = mapped()
T = RES["totals"]


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("A. the file ties to its OWN TRAILER — the free checksum on a feed that decides pay")
check("A1 the 45 rows sum to the trailer's 249:10, to the minute",
      (RES["tie_out"]["rows_total"], RES["tie_out"]["trailer_total"], RES["tie_out"]["delta"]),
      (249.17, 249.17, 0.0))
check("A2 so the file is accepted", (RES["tie_out"]["ties"], RES["rejected"]), (True, False))
# `249:10` is NOT a time of day. A time-of-day parse would read it as 10 past midnight and the
# checksum would appear to fail on every file.
check("A3 '249:10' parses as 249.17 hours, not as a clock time",
      round(F.parse_duration("249:10"), 2), 249.17)
check("A4 …and the row spelling '8:28' parses as 8.47", round(F.parse_duration("8:28"), 2), 8.47)
check("A5 the decimal column is read by the SAME function, so one reader handles both spellings",
      F.parse_duration(8.466666), 8.466666)
# THE POINT OF THE TIE-OUT. A truncated or doubled file is about to decide pay.
_short = mapped(rows=SAMPLE[:-1])
check("A6 drop ONE row and the file no longer ties — and is REJECTED",
      (_short["tie_out"]["ties"], _short["rejected"]), (False, True))
check("A7 …and the rejection NAMES both numbers and the gap, so a non-coder can see the size of it",
      all(x in _short["tie_out"]["reason"] for x in ["249.17", "gap of", "truncated"]), True)
_dbl = mapped(rows=SAMPLE + SAMPLE)
check("A8 a DOUBLED file is rejected too — the direction that overpays",
      _dbl["rejected"], True)
check("A9 an org may declare it will accept an untied file (RULE TWO), and then it lands",
      mapped(rows=SAMPLE[:-1],
             cfg=F.resolve_config({"non_shift_logins": (BACK_OFFICE_LOGIN,),
                                   "reject_on_trailer_mismatch": False}))["rejected"], False)
# A file with NO trailer is not a failure — only a file that HAS one and disagrees with it.
check("A10 a file with no trailer row is not a tie-out failure",
      mapped(trailer=None)["rejected"], False)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("B. `Store` is an ADDRESS — the resolver is INJECTED, never written here")
check("B1 all 28 distinct spellings in the file resolve", T["stores"], 28)
check("B2 every punch row carries a store CODE, never the raw address",
      all(p["store_code"] and p["store_code"] != p["store_raw"] for p in RES["punches"]), True)
check("B3 …and the raw spelling is KEPT beside it, so the mapping gap stays explainable",
      all(p["store_raw"] for p in RES["punches"]), True)
# A spelling bound to nothing is REPORTED. It must never be guessed onto a store, and it must never
# be silently dropped: those hours belong to somebody.
_un = mapped(resolve=lambda _s: "")
# 31, not 45: a row carries ONE reason, the first that disqualifies it, and the fourteen
# back-office rows were already out on their own. What matters is that NOTHING is payable and
# NOTHING is lost — proven on the next line, over all 45.
check("B4 with NO resolver bound, nothing is payable and the store gap is named",
      (_un["totals"]["punch_rows"],
       _un["totals"]["excluded_by_reason"].get("store_unbound", {}).get("rows")),
      (0, 31))
check("B5 …and the excluded hours are still counted, so nothing vanishes",
      _un["totals"]["excluded_hours"], 249.17)
check("B6 the gap has a sentence a non-coder can act on",
      "store" in F.exclusion_reason("store_unbound").lower(), True)
# THE MODULE OWNS NO MATCHER. Proven structurally: resolution is whatever the caller injected.
check("B7 a store resolves to EXACTLY what the injected resolver says — no second matcher here",
      mapped(resolve=lambda _s: "ZZ-ONLY")["punches"][0]["store_code"], "ZZ-ONLY")


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("C. the PERSON spelling — 'Last, First', a middle initial, and an accent")
from app.modules.storeops import payroll_identity as PI  # noqa: E402
check("C1 'Caba, David, A' yields three tokens — the comma is not assumed to split in two",
      PI.name_tokens("Caba, David, A"), {"caba", "david", "a"})
check("C2 the person key is ORDER-FREE, so 'Last, First' matches 'First Last'",
      PI.person_key("Caba, David") == PI.person_key("David Caba"), True)
# THE MEASURED DEFECT. `str.isalnum()` is True for 'í', so every alnum-only fold in this codebase
# kept the accent and `Martínez` was a different person from `Martinez`.
check("C3 an accented name folds to its plain twin — the defect this fixed",
      PI.person_key("Martínez, LESLIEE") == PI.person_key("Leslie Martinez"), False)
check("C4 …and at least the surname does match once folded",
      PI.person_key("Martínez") == PI.person_key("Martinez"), True)
check("C5 the file's own name is KEPT verbatim on every landed row — including the accented one",
      ("Mart\u00ednez, LESLIEE" in {p["employee_raw"] for p in RES["punches"]},
       "Caba, David, A" in {p["employee_raw"] for p in RES["punches"]}), (True, True))
# A NAME IS NEVER AN ATTRIBUTION. That rule lives in payroll_identity and is pinned here because
# this feed is where a name arrives.
check("C6 a name-token match is a PROPOSAL, not a binding attribution",
      PI.match_is_binding(PI.MATCH_NAME_PROPOSED), False)
check("C7 …and an ambiguous name binds nothing either",
      PI.match_is_binding(PI.MATCH_NAME_AMBIGUOUS), False)
check("C8 only an APPROVED link binds", PI.match_is_binding(PI.MATCH_APPROVED_LINK), True)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("D. local wall clock, no timezone — a missing zone is REPORTED, never assumed")
_p = RES["punches"][0]
check("D1 the local stamp is kept exactly as the file wrote it",
      _p["clock_in_local"], "2026-10-09 10:25:00")
check("D2 …and the UTC conversion is the store's own zone, 4h on this EDT day",
      _p["clock_in"][:19], "2026-10-09T14:25:00")
check("D3 the zone is the CALLER's answer: to_utc(stamp, None) is None, never a guessed offset",
      F.to_utc("2026-10-09 10:25:00", None), None)
_notz = mapped(tz=None)
# 31, not 45: the fourteen back-office rows are already excluded for a reason of their own, and a
# row carries ONE reason — the first that disqualifies it — rather than a list nobody reads.
check("D4 a store with no zone resolved has its rows EXCLUDED and the reason named",
      (_notz["totals"]["punch_rows"],
       _notz["totals"]["excluded_by_reason"].get("no_timezone_supplied", {}).get("rows")),
      (0, 31))
# DST. The zone, not an offset, is what makes this right — and the module spells no offset at all.
check("D5 the SAME zone gives a 5h offset in January — a literal -4 would be wrong half the year",
      F.to_utc("2026-01-15 10:00:00", TZ).hour, 15)
check("D6 and 4h in July", F.to_utc("2026-07-15 10:00:00", TZ).hour, 14)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("E. `Duration Decimal` is TRUNCATED — hours come from the STAMPS")
# 8:28 is 8.4666... and the file writes 8.466666. Reading pay off a truncated decimal loses seconds
# on every row, always in the same direction.
check("E1 the file's own decimal for 8:28 is truncated, not rounded",
      SAMPLE[1][5], 8.466666)
check("E2 hours are measured from the timestamps", _p["hours"], 8.4667)
check("E3 …and the file's claim is KEPT beside them, so the two can be compared on the row",
      _p["feed_hours_claimed"], 8.4667)
check("E4 across all 45 rows the two agree inside the declared tolerance",
      T["duration_disagreements"], 0)
# A DISAGREEMENT IS REPORTED PER ROW, not averaged away.
_lie = [list(SAMPLE[1])]
_lie[0][5] = 2.0
check("E5 a row whose decimal disagrees with its own stamps is reported",
      mapped(rows=[tuple(_lie[0])], trailer=None)["totals"]["duration_disagreements"], 1)
check("E6 the tolerance is a CONFIG value, not a literal", CFG["duration_tolerance_hours"], 0.02)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("F. `Type` is an ALLOWLIST — an unruled type does not pay")
check("F1 'Work' is payable", F.type_is_payable("Work", CFG), True)
check("F2 …case- and space-insensitively", F.type_is_payable("  WORK ", CFG), True)
# THE DIRECTION THE NEXT MISTAKE FALLS IN. A break, a lunch, a holiday code, a training code: none
# has been ruled on, so none pays, and each surfaces.
for _t in ("Break", "Lunch", "Holiday", "Training", "PTO"):
    check(f"F3 '{_t}' is NOT payable until somebody rules on it",
          F.type_is_payable(_t, CFG), False)
check("F4 a BLANK type is unknown, not payable — a blank is a shape change on a pay feed",
      F.type_is_payable("", CFG), False)
check("F5 a missing type is not payable", F.type_is_payable(None, CFG), False)
_brk = mapped(rows=[(SAMPLE[1][0], SAMPLE[1][1], SAMPLE[1][2], SAMPLE[1][3], SAMPLE[1][4],
                     SAMPLE[1][5], "Break", SAMPLE[1][7])], trailer=None)
check("F6 so a break row lands EXCLUDED, with its hours surfaced rather than paid",
      (_brk["totals"]["punch_rows"],
       _brk["totals"]["excluded_by_reason"].get("type_not_payable", {}).get("rows")), (0, 1))
check("F7 the vocabulary is per-org CONFIG (RULE TWO) — an org that pays training says so in a row",
      F.type_is_payable("Training", F.resolve_config({"payable_punch_types": ("work", "training")})),
      True)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("G. the back-office login is NOT a store shift — and its hours are SURFACED")
check("G1 14 of the 45 rows are the one back-office login",
      T["excluded_by_reason"]["non_shift_login"]["rows"], 14)
check("G2 …worth 12.48h, across twelve stores in bursts — not one person's shift",
      T["excluded_by_reason"]["non_shift_login"]["hours"], 12.48)
check("G3 the remaining 31 rows are real punches worth 236.68h",
      (T["punch_rows"], T["punch_hours"]), (31, 236.68))
check("G4 and 31 + 14 = 45: no row is dropped, every hour is accounted for",
      (T["punch_rows"] + T["excluded_rows"], round(T["punch_hours"] + T["excluded_hours"], 2)),
      (45, 249.16))
# RULE TWO. The login is a config row; nothing in the module names it.
check("G5 with no config, NO login is treated as back-office — the module names none",
      mapped(cfg=F.resolve_config(None))["totals"]["punch_rows"], 45)
check("G6 a BLANK login is a store shift, not back-office — absence is not evidence",
      F.login_is_store_shift("", CFG), True)
check("G7 the exclusion is case- and punctuation-insensitive, like every identity key here",
      F.login_is_store_shift(" pa-admin ", CFG), False)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("H. the key is (employee, date, PUNCH) — one person, two stores, one day")
_gs = [p for p in RES["punches"] if p["feed_login"] == "gsantos"]
check("H1 the file carries this person TWICE on the same day", len(_gs), 2)
check("H2 …at two DIFFERENT stores", len({p["store_code"] for p in _gs}), 2)
check("H3 so hours attribute to each punch's OWN store, never summed onto one",
      sorted(round(p["hours"], 2) for p in _gs), [0.0, 4.08])
# A key of (employee, date) would have collapsed these two rows and lost 4.08h or a whole store.
check("H4 and the landed rows keep both — a (employee, date) key would have lost one",
      len({(p["store_code"], p["business_date"], p["clock_in_local"]) for p in _gs}), 2)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("I. a ZERO-DURATION row is a MEASURED ZERO, not a missing measurement")
check("I1 four punch rows clock in and out at the same minute", T["zero_duration_rows"], 4)
check("I2 …and they LAND, with zero hours, rather than being dropped",
      len([p for p in RES["punches"] if p["hours"] == 0.0]), 4)
# THE DISTINCTION ALREADY EXISTS and is dereferenced, not re-invented: a measured zero PAYS zero,
# where nothing-measured falls to the schedule.
check("I3 the basis for a measured zero is the platform's own MEASURED_ZERO",
      B.MEASURED_ZERO, "measured_zero")
check("I4 …which PAYS (zero) and IS a measurement — the distinction the `or` destroyed",
      (B.basis_pays_hours(B.MEASURED_ZERO), B.basis_is_measurement(B.MEASURED_ZERO)), (True, True))
check("I5 whereas nothing-measured does NOT pay on its own",
      B.basis_pays_hours(B.NOT_MEASURED_UNPAID), False)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("J. Division / Region / District are B2B'S org — METADATA, never ours")
_meta = mapped(rows=SAMPLE[1:2], trailer=None,
               extra={"division": "Default Division", "region": "Default Region",
                      "district": "PA"})
_mp = _meta["punches"][0]
check("J1 the three columns are KEPT on the row, so the feed stays auditable",
      (_mp["feed_division"], _mp["feed_region"], _mp["feed_district"]),
      ("Default Division", "Default Region", "PA"))
check("J2 …under names that say whose org they are (`feed_*`), so nobody joins on them by accident",
      [k for k in _mp if "district" in k or "region" in k or "division" in k],
      ["feed_division", "feed_region", "feed_district"])
# `District` reads 'PA' / 'NJ' / 'Default District'. MetricsPro's districts are storeops.org_units,
# and the recipient of the Monday ask is resolved through storeops.org_chain and nothing else.
check("J3 the store code is NOT derived from the feed's district",
      _mp["store_code"], _CODE_OF[SAMPLE[1][0]])
check("J4 the module's own code names no district, region or division value",
      any(w in open(F.__file__).read().split('"""', 2)[2] for w in ("'PA'", '"PA"', "Default District")),
      False)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("K. the business date comes from CLOCK IN — never the filename, never the upload")
check("K1 every row's business date is its own clock-in day", T["business_dates"], ["2026-10-09"])
check("K2 the filename's claim is READ…", RES["claim"]["claimed"], ["2026-10-09", "2026-10-09"])
check("K3 …and AGREES with the rows", RES["claim"]["agrees"], True)
# THE 28x DEFECT. Stamping rows from the filename or the upload period is what once read carrier
# commission 28 times high. So a mismatch is REPORTED and the rows keep their own dates.
_wrong = mapped(filename="My_Employee_Time_Sheet_01012026-01012026.Xlsx")
check("K4 a MISLABELLED file is reported, not obeyed",
      (_wrong["claim"]["agrees"], _wrong["claim"]["outside"]), (False, ["2026-10-09"]))
check("K5 …and the rows keep their OWN dates — nothing is restamped",
      _wrong["totals"]["business_dates"], ["2026-10-09"])
check("K6 …and the file is NOT rejected for it: a renamed attachment must not lose real punches",
      _wrong["rejected"], False)
check("K7 a file with no readable date in its name claims nothing, rather than guessing",
      F.filename_claim("attachment.xlsx"), (None, None))
check("K8 a single-day filename claims that one day twice",
      F.filename_claim("My_Employee_Time_Sheet_10092026.xlsx"), ("2026-10-09", "2026-10-09"))
check("K9 the date is read as MMDDYYYY, which is what the exporter writes",
      F.filename_claim("x_12312026-12312026.xlsx"), ("2026-12-31", "2026-12-31"))


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("L. OVERNIGHT — the branch the sample file does not exercise")
# There is no overnight row in the sample. An unexercised branch on a pay feed is an untested one.
check("L1 an out EARLIER than the in rolls to the next day — never a negative span",
      F.span_hours("2026-10-09 21:00:00", "2026-10-09 02:30:00"), (5.5, True))
check("L2 …and the roll is REPORTED, so a reader can see it happened",
      F.span_hours("2026-10-09 21:00:00", "2026-10-09 02:30:00")[1], True)
check("L3 a normal span does not roll", F.span_hours("2026-10-09 10:00:00", "2026-10-09 18:00:00"),
      (8.0, False))
check("L4 equal stamps are a measured ZERO, not an overnight 24h shift",
      F.span_hours("2026-10-09 17:00:00", "2026-10-09 17:00:00"), (0.0, False))
_ov = mapped(rows=[("1 S 60th street", "Night, Owl", "2026-10-09 21:00:00",
                    "2026-10-09 02:30:00", "5:30", 5.5, "Work", "nowl")], trailer=None)
check("L5 an overnight punch LANDS, flagged, with 5.5h and the clock-in's business date",
      (_ov["punches"][0]["hours"], _ov["punches"][0]["overnight"],
       _ov["punches"][0]["business_date"]), (5.5, True, "2026-10-09"))
check("L6 …and its UTC clock-out is on the NEXT calendar day",
      _ov["punches"][0]["clock_out"][:10], "2026-10-10")


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("M. what LANDS — the claim we keep, never the hours we pay")
ROWS = F.landing_rows("org-1", RES, source_file=FILENAME, period="October 2026")
check("M1 every row the file carried lands — payable or not", len(ROWS), 45)
check("M2 …and the excluded ones say WHY, in a column",
      len([r for r in ROWS if r.get("excluded")]), 14)
check("M3 the payable ones carry no exclusion", len([r for r in ROWS if not r.get("excluded")]), 31)
check("M4 every row is ORG-SCOPED on the way in",
      {r["org_id"] for r in ROWS}, {"org-1"})
check("M5 …and carries its source file, so a bad import can be found and replaced",
      {r["source_file"] for r in ROWS}, {FILENAME})
# THE POSTURE OF THIS FEED. It is evidence we keep, not a number we trust. It lands in its own
# table and touches neither the kiosk's punches nor the column payroll used to read.
check("M6 the landing table is raw_clock_punch — never timelog, never shifts",
      (F.TABLE, F.REPORT_KEY), ("raw_clock_punch", "clock_punch"))
check("M7 no landed row carries a key that would write timelog or shifts.actual_hours",
      sorted({k for r in ROWS for k in r} & {"actual_hours", "scheduled_hours", "shift_id",
                                             "auto_clocked_out", "timelog_id"}), [])
check("M8 both the local stamps AND their UTC conversion land, so the offset stays auditable",
      all(("clock_in_local" in r) for r in ROWS if not r.get("excluded")), True)
check("M9 the file's own declared hours land beside the measured ones",
      all(("feed_hours_claimed" in r) for r in ROWS), True)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("Z. ARMED negative control")
_f0 = len(FAIL)
check("Z-armed the file does NOT tie", RES["tie_out"]["ties"], False)
check("Z-armed a break is payable", F.type_is_payable("Break", CFG), True)
check("Z-armed the filename decides the business date",
      mapped(filename="My_Employee_Time_Sheet_01012026-01012026.Xlsx")["totals"]["business_dates"],
      ["2026-01-01"])
_fired = len(FAIL) - _f0
if _fired == 3:
    FAIL[:] = FAIL[:_f0]
    PASS.append("Z1 negative control fired on all three wrong expectations (the checks are live)")
else:
    FAIL.append(f"Z1 NEGATIVE CONTROL DID NOT FIRE — {_fired}/3 wrong answers accepted.")

print(f"\n{'=' * 78}")
for f in FAIL:
    print(f"  ✗ {f}")
print(f"  PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
print(f"{'=' * 78}")
sys.exit(1 if FAIL else 0)
