"""Proof harness — a weekly shift template derived from POS work history.

Owner request 2026-10-03: "Add the 9 sales reps from the 9 stores we worked on a schedule as they
have been working in the past, this needs analysis based on work history and then assign schedule,
again nothing is hard coded just as user entered and editable."

DB-free, pure stdlib. Proves `backend/app/modules/storeops/schedule_from_history.py`:

  1. The weekday arithmetic (Zeller) matches the calendar for every day over four years, and the
     month weekday counts match the month lengths. The template's `weekday` column and
     `date.weekday()` agree on 0 = Monday, so an applied template never lands a day out.
  2. Percentile is NEAREST-RANK and pinned, and the rounding is OUTWARD: a derived start is never
     LATER than the observed open percentile and a derived end never EARLIER than the observed
     close. The inference may widen the shift, never narrow it -- a rep is on the floor before the
     first sale and after the last.
  3. RELIEF IS NOT A WEEKLY COMMITMENT. A rep below `regular_min_days` in every analysed month gets
     ZERO recurring rows and is reported in `relief` with its day counts. (Live: one rep covered
     five single days across four stores -- every one of them a day the store's regular rep was
     absent. A recurring template would have scheduled that rep 52 times a year at four stores.)
  4. A weekday worked below `weekday_min_fraction` of its occurrences is NOT scheduled, and the
     denominator is the days the STORE traded -- a store shut on Sundays never makes its rep look
     absent on Sundays.
  5. NO INVENTED MEASUREMENT. A store whose day group has fewer than `min_samples_per_group` timed
     days, or none at all, takes the house pattern and is stamped `house:thin` / `house:none`; a pair
     with no history at all is stamped `evidence='none'`. The caller can therefore never present an
     assumption as a finding. With nothing measured anywhere there is no fallback and no row.
  6. STORE RESOLUTION IS INJECTED AND REPORTED. `resolve_rows` binds the feed's store string through
     the caller's resolver; a string that binds to nothing is returned in `unresolved`, never
     dropped and never bound to a guessed code.
  7. A MONTH WITHOUT TIMESTAMPS still counts for DAYS. The feed carried no `trans_ts` before August
     2026, so July proves attendance and only August can speak to times; both must be usable at
     once without July's untimed rows dragging a start time to midnight.
  8. CONFIG IS CONFIG (RULE TWO). Day groups, percentiles, rounding step and thresholds change the
     answer when changed; an unknown key raises rather than being silently ignored. No store, rep,
     carrier or tenant name appears in the module -- asserted by reading the source.
  9. DETERMINISM. The emitted rows are ordered by (store, rep, weekday): the same history produces
     byte-identical SQL twice.
 10. The money cross-check reports BOTH directions (dollars at a rate, and the rate an entered
     monthly figure implies) so neither is ever shown alone.

REGRESSION reproducing a defect found while deriving this (2026-10-03, live house org):
 R1  An unordered PostgREST page walk over `commcalc.raw_sales` lost 58 of one rep's 1,109 August
     rows and hid a seventh off-roster salesperson entirely. Days-worked and first/last-transaction
     evidence are COUNTS over those rows, so a lost page silently shortens a real schedule. The
     harness pins that `day_spans` is order-INDEPENDENT (so the fix belongs in the fetch, where the
     tool now orders by id) and that a dropped day lowers the derived coverage -- i.e. the loss is
     real and cannot be absorbed.
 R2  The feed writes a store as '2778 Ephraim Ave ' (trailing space) for a store whose
     `store_mapping.store_address` is '1598 Mount Ephraim Ave'. An exact-address match finds ZERO
     history and concludes the rep has none; the platform's resolver binds it through
     `store_aliases`. Pinned as a whitespace/alias case in the injected resolver.
"""
import datetime as dt
import re
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from app.modules.storeops import schedule_from_history as S   # noqa: E402

PASS = FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL {label}\n    got  {got!r}\n    want {want!r}")


def ok(label, cond):
    check(label, bool(cond), True)


def row(store, rep, date, time=None, month="M1"):
    return {"store_code": store, "rep": rep, "date": date, "time": time, "month": month}


def days_in(year, month):
    d = dt.date(year, month, 1)
    out = []
    while d.month == month:
        out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


# ── 1. calendar arithmetic ────────────────────────────────────────────────────────────────────────
print("1. weekday + month arithmetic")
bad = [d for d in (dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(1461))
       if S.weekday_of(d.isoformat()) != d.weekday()]
check("weekday_of matches date.weekday() over 4 years", bad, [])
import calendar as _cal
mism = [(y, m) for y in (2024, 2025, 2026, 2027) for m in range(1, 13)
        if S.month_weekday_counts(y, m)[1] != _cal.monthrange(y, m)[1]
        or sum(S.month_weekday_counts(y, m)[0]) != _cal.monthrange(y, m)[1]]
check("month_weekday_counts matches the calendar", mism, [])
check("Feb 2024 is a leap February", S.month_weekday_counts(2024, 2)[1], 29)
check("Monday is 0 (the shift_templates convention)", S.weekday_of("2026-08-31"), 0)
check("Sunday is 6", S.weekday_of("2026-08-30"), 6)

# ── 2. percentile + outward rounding ──────────────────────────────────────────────────────────────
print("2. percentile is nearest-rank; rounding is OUTWARD")
check("p0 is the minimum", S.percentile([5, 1, 9, 3], 0), 1)
check("p100 is the maximum", S.percentile([5, 1, 9, 3], 100), 9)
check("single value", S.percentile([7], 10), 7)
check("empty is None", S.percentile([], 50), None)
check("no interpolation: a percentile is one of the observations",
      S.percentile([0, 10], 50) in (0, 10), True)
check("nearest rank rounds up past the midpoint", S.percentile([0, 10], 60), 10)
check("nearest rank rounds down before the midpoint", S.percentile([0, 10], 40), 0)
check("floor_to 30 on 09:53", S.to_hhmm(S.floor_to(S.to_minutes("09:53"), 30)), "09:30")
check("ceil_to 30 on 18:52", S.to_hhmm(S.ceil_to(S.to_minutes("18:52"), 30)), "19:00")
check("floor_to is a no-op on the step", S.floor_to(600, 30), 600)
check("ceil_to is a no-op on the step", S.ceil_to(600, 30), 600)
check("to_minutes rejects rubbish", S.to_minutes("nonsense"), None)
check("to_minutes accepts HH:MM:SS", S.to_minutes("09:45:31"), 585)

# A whole store's worth: the derived envelope must CONTAIN the observed one.
cfg = S.config()
hist = []
for i, d in enumerate(days_in(2026, 8)):
    wd = S.weekday_of(d)
    if wd == 6:
        hist += [row("S1", "R1", d, "12:20"), row("S1", "R1", d, "16:35")]
    else:
        hist += [row("S1", "R1", d, "10:12"), row("S1", "R1", d, "18:44")]
h = S.store_hours(S.day_spans(hist), cfg)
ok("derived start is never later than the observed open percentile",
   all(S.to_minutes(v["start"]) <= S.to_minutes(v["observed_open"]) for v in h.values()))
ok("derived end is never earlier than the observed close percentile",
   all(S.to_minutes(v["end"]) >= S.to_minutes(v["observed_close"]) for v in h.values()))
check("Mon-Sat envelope", (h[("S1", 0)]["start"], h[("S1", 0)]["end"], h[("S1", 0)]["hours"]),
      ("10:00", "19:00", 9.0))
check("Sunday envelope", (h[("S1", 1)]["start"], h[("S1", 1)]["end"], h[("S1", 1)]["hours"]),
      ("12:00", "17:00", 5.0))
check("the Sunday group kept its own, smaller sample", h[("S1", 1)]["samples"], 5)

# ── 3. relief gets NO recurring rows ──────────────────────────────────────────────────────────────
print("3. relief is not a weekly commitment")
aug = days_in(2026, 8)
regular = [row("S1", "REG", d, "10:12") for d in aug] + [row("S1", "REG", d, "18:44") for d in aug]
cover = [row("S1", "REL", aug[9], "10:30"), row("S1", "REL", aug[9], "18:20")]
out = S.weekly_template(S.day_spans(regular + cover), cfg)
check("the regular rep is scheduled", sorted({r["rep"] for r in out["rows"]}), ["REG"])
check("the relief rep has no recurring row", [r for r in out["rows"] if r["rep"] == "REL"], [])
check("the relief rep is REPORTED, with its days", [(x["rep"], x["days"]) for x in out["relief"]],
      [("S1", "REL")[1:] and ("REL", 1)])
check("relief classification", out["profiles"][("S1", "REL")]["kind"], "relief")
check("regular classification", out["profiles"][("S1", "REG")]["kind"], "regular")
# A relief rep's cover days STILL inform the store's hours (open/close is a store fact).
ok("relief transactions still count toward the store's hours",
   S.store_hours(S.day_spans(cover), cfg)[("S1", 0)]["samples"] == 1)
# The threshold is config, not a constant: lower it and the same rep becomes regular.
out_lo = S.weekly_template(S.day_spans(regular + cover),
                           S.config({"regular_min_days": 1, "weekday_min_fraction": 0.1}))
ok("regular_min_days is config", any(r["rep"] == "REL" for r in out_lo["rows"]))
check("a reclassified relief rep is scheduled only on the weekday it actually worked",
      sorted(r["weekday"] for r in out_lo["rows"] if r["rep"] == "REL"),
      [S.weekday_of(aug[9])])

# ── 4. the coverage fraction, and its denominator ─────────────────────────────────────────────────
print("4. a weekday below the coverage fraction is not scheduled")
sparse = []
for d in aug:
    wd = S.weekday_of(d)
    if wd == 2 and d != aug[4]:                    # only ONE of the month's four Wednesdays
        continue
    sparse += [row("S1", "REG", d, "10:12"), row("S1", "REG", d, "18:44")]
cal = S.config({"weekday_denominator": "calendar"})
o = S.weekly_template(S.day_spans(sparse), cal)
check("Wednesday is dropped at 1 of 4", 2 in {r["weekday"] for r in o["rows"]}, False)
check("the other six weekdays survive", sorted({r["weekday"] for r in o["rows"]}),
      [0, 1, 3, 4, 5, 6])
half = [r for d in aug if not (S.weekday_of(d) == 2 and d not in (aug[4], aug[11]))
        for r in (row("S1", "REG", d, "10:12"), row("S1", "REG", d, "18:44"))]
check("the coverage fraction is a floor, not a ceiling: exactly 0.5 is kept",
      2 in {r["weekday"] for r in S.weekly_template(S.day_spans(half), cal)["rows"]}, True)
o2 = S.weekly_template(S.day_spans(sparse), S.config({"weekday_min_fraction": 0.2,
                                                      "weekday_denominator": "calendar"}))
check("weekday_min_fraction is config", 2 in {r["weekday"] for r in o2["rows"]}, True)
# THE DENOMINATOR IS THE ONE JUDGEMENT CALL, so both readings are pinned.
check("under 'traded' a single-rep store's absence reads as a closure, so nothing is dropped",
      sorted({r["weekday"] for r in S.weekly_template(S.day_spans(sparse), cfg)["rows"]}),
      [0, 1, 2, 3, 4, 5, 6])
check("'calendar' counts every occurrence between the first and last traded day",
      S.weekday_occurrences(S.day_spans(sparse), "calendar")["S1"],
      S.month_weekday_counts(2026, 8)[0])
check("'traded' counts only the days the store transacted",
      S.weekday_occurrences(S.day_spans(sparse), "traded")["S1"][2], 1)
check("_date_range spans a month boundary",
      len(S._date_range("2026-07-30", "2026-08-02")), 4)
check("_date_range spans a year boundary",
      S._date_range("2026-12-31", "2027-01-01"), ["2026-12-31", "2027-01-01"])
check("_date_range spans a leap day", "2024-02-29" in S._date_range("2024-02-28", "2024-03-01"),
      True)
# A store SHUT on Sundays: the rep worked every day the store traded, so nothing is dropped.
six = [r for d in aug if S.weekday_of(d) != 6
       for r in (row("S1", "REG", d, "10:12"), row("S1", "REG", d, "18:44"))]
o3 = S.weekly_template(S.day_spans(six), cfg)
check("a store shut on Sunday schedules no Sunday", 6 in {r["weekday"] for r in o3["rows"]}, False)
check("under 'calendar' that same closure would read as absence and still drop Sunday",
      6 in {r["weekday"] for r in S.weekly_template(S.day_spans(six), cal)["rows"]}, False)
check("and loses no weekday it DID trade", sorted({r["weekday"] for r in o3["rows"]}),
      [0, 1, 2, 3, 4, 5])
ok("the denominator is the store's trading days, so coverage is 1.0 on every scheduled day",
   all(r["days_worked"] == r["days_possible"] for r in o3["rows"]))

# ── 5. no invented measurement ────────────────────────────────────────────────────────────────────
print("5. a thin or absent group is flagged, never dressed as a measurement")
rich = [r for d in aug for r in (row("S2", "A", d, "10:12"), row("S2", "A", d, "18:44"))]
# S3's Sundays have ONE timed day: below min_samples_per_group.
thin = []
for d in aug:
    if S.weekday_of(d) == 6 and d != aug[1]:
        thin += [row("S3", "B", d)]                 # worked, but untimed
    else:
        thin += [row("S3", "B", d, "10:12"), row("S3", "B", d, "18:44")]
o = S.weekly_template(S.day_spans(rich + thin), cfg)
sun3 = [r for r in o["rows"] if r["store_code"] == "S3" and r["weekday"] == 6]
check("the thin Sunday took the house pattern and says so",
      [(r["evidence"], r["start_time"], r["end_time"]) for r in sun3],
      [("house:thin", o["house"][1]["start"], o["house"][1]["end"])])
ok("the measured Mon-Sat rows are still stamped measured",
   all(r["evidence"] == "measured" for r in o["rows"] if r["weekday"] != 6))
# A pair with no history at all: an ASSUMPTION, stamped as one.
o4 = S.weekly_template(S.day_spans(rich), cfg, assignments=[{"store_code": "S9", "rep": "NEW"}])
new = [r for r in o4["rows"] if r["store_code"] == "S9"]
check("an assumed pair gets all seven days", sorted(r["weekday"] for r in new), [0, 1, 2, 3, 4, 5, 6])
ok("every assumed row is stamped evidence='none'", all(r["evidence"] == "none" for r in new))
ok("and carries no sample count it does not have", all(r["samples"] == 0 for r in new))
check("an assumption never overrides real history",
      [r["evidence"] for r in S.weekly_template(S.day_spans(rich), cfg,
       assignments=[{"store_code": "S2", "rep": "A"}])["rows"]], ["measured"] * 7)
# Nothing measured anywhere => no fallback exists => no rows, rather than a guess.
check("with nothing measured there is no house pattern", S.house_pattern({}, cfg), {})
check("and no assumed rows are emitted",
      S.weekly_template({}, cfg, assignments=[{"store_code": "S9", "rep": "NEW"}])["rows"], [])

# ── 6. store resolution is injected and reported ──────────────────────────────────────────────────
print("6. an unbindable store string is reported, never guessed")
alias = {"1598 mount ephraim ave": "B-X", "2778 ephraim ave": "B-X", "b-x": "B-X"}
resolved, unresolved = S.resolve_rows(
    [{"store": "2778 Ephraim Ave ", "rep": "A", "date": "2026-08-03", "time": "10:00"},
     {"store": "1598 Mount Ephraim Ave", "rep": "A", "date": "2026-08-04", "time": "10:00"},
     {"store": "Somewhere Else", "rep": "A", "date": "2026-08-05", "time": "10:00"},
     {"store": "Somewhere Else", "rep": "A", "date": "2026-08-06", "time": "10:00"}],
    lambda raw: alias.get(" ".join((raw or "").strip().split()).lower()))
check("R2 the alias AND the trailing-space variant both bind to one code",
      sorted({r["store_code"] for r in resolved}), ["B-X"])
check("R2 two spellings of one store are not two stores", len(resolved), 2)
check("what binds to nothing is reported with its row count", unresolved, {"Somewhere Else": 2})
check("and is not in the rows", [r for r in resolved if r["store_code"] is None], [])

# ── 7. an untimed month still counts for days ─────────────────────────────────────────────────────
print("7. days from an untimed month, times from the timed one")
jul = [row("S1", "REG", d, None, month="Jul") for d in days_in(2026, 7)]
augt = [r for d in aug for r in (row("S1", "REG", d, "10:12", month="Aug"),
                                 row("S1", "REG", d, "18:44", month="Aug"))]
sp = S.day_spans(jul + augt)
check("July's days are counted", len([k for k, v in sp.items() if v["month"] == "Jul"]), 31)
ok("July's days carry no time", all(v["first"] is None for v in sp.values() if v["month"] == "Jul"))
hh = S.store_hours(sp, cfg)
check("an untimed day never drags the start toward midnight", hh[("S1", 0)]["start"], "10:00")
check("nor the end", hh[("S1", 0)]["end"], "19:00")
check("only timed days are sampled", hh[("S1", 0)]["samples"], 26)
check("both months count toward the regular/relief call",
      S.rep_profiles(sp, cfg)["S1", "REG"]["days"], 62)
check("and the per-month split is kept", S.rep_profiles(sp, cfg)["S1", "REG"]["days_by_month"],
      {"Jul": 31, "Aug": 31})

# ── R1. day folding is order-independent; a lost row is a real loss ───────────────────────────────
print("R1. an unordered page walk cannot be absorbed")
import random
shuffled = list(jul + augt)
random.Random(11).shuffle(shuffled)
check("day_spans is order-independent", S.day_spans(shuffled), S.day_spans(jul + augt))
check("so is the emitted template", S.weekly_template(S.day_spans(shuffled), cfg)["rows"],
      S.weekly_template(S.day_spans(jul + augt), cfg)["rows"])
lost = [r for r in (jul + augt) if S.weekday_of(r["date"]) != 2]   # every Wednesday page lost
o_lost = S.weekly_template(S.day_spans(lost), cfg)
check("R1 dropping a weekday's rows drops that weekday from the schedule",
      2 in {r["weekday"] for r in o_lost["rows"]}, False)
ok("R1 and the schedule it produces is materially shorter",
   S.weekly_hours(o_lost["rows"]) < S.weekly_hours(S.weekly_template(S.day_spans(jul + augt), cfg)["rows"]))

# ── 8. config is config; RULE TWO ─────────────────────────────────────────────────────────────────
print("8. config is config, and no name is in the code")
try:
    S.config({"nope": 1})
    check("an unknown config key raises", False, True)
except KeyError:
    check("an unknown config key raises", True, True)
check("defaults are not mutated by an override",
      S.config({"round_minutes": 5})["round_minutes"] == 5 and S.DEFAULTS["round_minutes"] == 30,
      True)
h15 = S.store_hours(S.day_spans(hist), S.config({"round_minutes": 15}))
check("a finer rounding step gives a finer answer", (h15[("S1", 0)]["start"], h15[("S1", 0)]["end"]),
      ("10:00", "18:45"))
h_one = S.store_hours(S.day_spans(hist), S.config({"day_groups": [[0, 1, 2, 3, 4, 5, 6]]}))
check("one day group collapses Sunday into the week", sorted(h_one), [("S1", 0)])
ok("and the collapsed envelope spans both shapes",
   h_one[("S1", 0)]["start"] <= "12:00" and h_one[("S1", 0)]["end"] >= "17:00")
h_each = S.store_hours(S.day_spans(hist), S.config({"day_groups": [[i] for i in range(7)]}))
check("seven groups give seven answers", len(h_each), 7)
# A varied store: most days open near 10:30, a handful of early birds near 09:40. p10 follows the
# early tail; p50 does not. That is the whole reason the percentile is a knob.
varied = []
for i, d in enumerate(days_in(2026, 8)):
    early = (i % 7 == 0)
    varied += [row("SV", "R", d, "09:40" if early else "10:35"),
               row("SV", "R", d, "19:10" if early else "18:05")]
hv10 = S.store_hours(S.day_spans(varied), S.config({"day_groups": [[0, 1, 2, 3, 4, 5, 6]]}))
hv50 = S.store_hours(S.day_spans(varied), S.config({"day_groups": [[0, 1, 2, 3, 4, 5, 6]],
                                                    "open_percentile": 50, "close_percentile": 50}))
check("p10 follows the early tail", hv10[("SV", 0)]["observed_open"], "09:40")
check("p50 does not", hv50[("SV", 0)]["observed_open"], "10:35")
check("p90 follows the late tail", hv10[("SV", 0)]["observed_close"], "19:10")
check("p50 does not", hv50[("SV", 0)]["observed_close"], "18:05")
ok("so the percentile choice changes the derived shift",
   hv10[("SV", 0)]["hours"] > hv50[("SV", 0)]["hours"])
src = open(__file__.replace("harness_schedule_from_history.py",
                            "app/modules/storeops/schedule_from_history.py")).read()
banned = re.findall(r"(?i)\b(boost|luxelink|verizon|t-?mobile|at&t|metro|cricket|total ?wireless|"
                    r"germantown|broad|woodland|castor|ephraim|uniondale|bergenline)\b", src)
check("RULE TWO: no tenant / carrier / store name in the module", banned, [])
check("RULE TWO: no store-code literal in the module",
      re.findall(r"'B-[0-9A-Za-z]+'", src), [])

# ── 9. determinism ────────────────────────────────────────────────────────────────────────────────
print("9. the same history produces the same rows")
two = [S.weekly_template(S.day_spans(rich + thin), cfg)["rows"] for _ in range(2)]
check("two runs agree", two[0], two[1])
check("rows are ordered by (store, rep, weekday)",
      two[0], sorted(two[0], key=lambda r: (r["store_code"], r["rep"], r["weekday"])))

# ── 10. the money cross-check, both directions ────────────────────────────────────────────────────
print("10. hours x rate against the entered monthly figure")
o = S.weekly_template(S.day_spans(hist), cfg)
check("weekly hours", S.weekly_hours(o["rows"]), 59.0)
check("August has 26 Mon-Sat days and 5 Sundays", S.month_weekday_counts(2026, 8)[0],
      [5, 4, 4, 4, 4, 5, 5])
check("August hours = 26 x 9.0 + 5 x 5.0", S.monthly_hours(o["rows"], 2026, 8), 259.0)
# September 2026: 26 Mon-Sat days and 4 Sundays -> 26 x 9.0 + 4 x 5.0.
check("September hours differ with the calendar", S.monthly_hours(o["rows"], 2026, 9), 254.0)
rc = S.rate_check(259.0, 17.0, 4500.0)
check("dollars at the proposed rate", rc["dollars"], 4403.0)
check("the shortfall against the entered figure", rc["delta"], -97.0)
check("the rate the entered figure implies", rc["rate_implied_by_entered"], 17.3745)
ok("the shortfall is reported as a percentage too", abs(rc["delta_pct"] + 2.16) < 0.01)
check("a zero entered figure does not divide by zero", S.rate_check(259.0, 17.0, 0.0)["delta_pct"],
      None)
check("zero hours do not divide by zero", S.rate_check(0.0, 17.0, 4500.0)["rate_implied_by_entered"],
      None)
# S2's rep traded the same hours on Sundays too, so its Sunday group is the weekday shape: 31 x 9.0.
check("per-rep scoping", S.monthly_hours(two[0], 2026, 8, store_code="S2", rep="A"), 279.0)
check("scoping to the other store gives the other answer",
      S.monthly_hours(two[0], 2026, 8, store_code="S3", rep="B"),
      S.monthly_hours(two[0], 2026, 8) - 279.0)

# ── 11. THE UN-WIRING LOCK: one derivation, one destination table ────────────────────────────────
# A design fix that can quietly un-wire is not a design fix. The two facts this pins:
#   (a) the derivation has ONE home -- the tool that emits the SQL DEREFERENCES the module and does
#       not re-implement the arithmetic;
#   (b) the template has ONE destination -- `storeops.shift_templates` (mig 040), the table the
#       existing save-week / apply endpoints already own. A second schedule-generation table or a
#       direct write into `storeops.shifts` is the sibling derivation the index rules forbid, so it
#       fails the build here rather than being discovered later as drift.
print("11. the un-wiring lock")
import os
_here = os.path.dirname(os.path.abspath(__file__))
tool = open(os.path.join(_here, "tools/derive_schedule_from_history.py")).read()
ok("the tool imports the one derivation module",
   "from app.modules.storeops import schedule_from_history" in tool)
ok("and calls it rather than deriving its own pattern", "sfh.weekly_template(" in tool)
for fn in ("percentile", "floor_to", "ceil_to", "weekday_of", "store_hours", "month_weekday_counts"):
    check(f"the tool does not re-implement {fn}", f"def {fn}(" in tool, False)
inserts = sorted(set(re.findall(r"(?i)INSERT\s+INTO\s+([a-z_]+\.[a-z_]+)", tool)))
check("the tool writes templates and nothing else", inserts, ["storeops.shift_templates"])
check("the tool never inserts a shift directly (that is the apply endpoint's job)",
      [t for t in inserts if t.endswith(".shifts")], [])
check("RULE TWO: no store code literal in the tool", re.findall(r"'B-[0-9A-Za-z]+'", tool), [])
check("RULE TWO: no tenant / carrier name in the tool",
      re.findall(r"(?i)\b(boost|luxelink|verizon|t-?mobile|at&t|cricket)\b", tool), [])
ok("the fetch is ordered (R1's fix lives where the rows are read)", '.order(order)' in tool)
# No sibling derivation anywhere else in the module tree.
sibling = []
_mods = os.path.join(_here, "app/modules/storeops")
for fn in sorted(os.listdir(_mods)):
    if not fn.endswith(".py") or fn == "schedule_from_history.py":
        continue
    body = open(os.path.join(_mods, fn)).read()
    if re.search(r"def\s+(weekly_template|store_hours|house_pattern)\s*\(", body):
        sibling.append(fn)
check("no second module derives a weekly pattern", sibling, [])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
