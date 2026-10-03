"""IS THIS ZERO-ROW PULL A STATEMENT ABOUT THE SOURCE, OR A STATEMENT ABOUT US?

THE ONE HOME of that decision, for every sweep. PURE — stdlib only, no DB, no network, no config
reads: callers hand it the evidence they already hold and it returns a verdict and the sentence that
goes with it.

THE DEFECT THAT PRODUCED IT (house org, measured 2026-10-03, index §19.41). The portal sweep's report
registry marked the compensation report `empty_ok: True`, meaning "a zero-row pull is a legitimate
answer". That flag is not evidence of anything, and it was asked to carry the whole decision:

    2026-10-03T03:31:01Z  core.job_run  sweep:<connector>  status = SUCCEEDED
      {'reports': [{'report': 'comp_report', 'rows': 0, 'mode': 'no_data',
                    'window': '2026-10-03..2026-10-03',
                    'note': 'no compensation posted for 2026-10-03..2026-10-03 — not an error'}],
       'errors': ["... could not set the report's daily date filter — 'Summarize by' could not be
                   set to Daily (hidden field = None); the report returns an empty workbook
                   without it"]}

ONE run, ONE browser session. The second leg PROVED the shared date-filter control could not be set,
and the first leg's zero rows were still published as "the source posted nothing" — and the run as a
success. Two months of commission statements (August partial, September entirely absent) never landed,
no alert fired, and the first person to notice was the owner reading a P&L that had gone quiet.

THREE SWEEPS ALREADY HELD THREE DIFFERENT ANSWERS to this one question — the duplicate defect the
index rules forbid, and the reason this module exists rather than a fourth branch:

  * the merchant-processor sweep has the RIGHT idea and the right word for it: `empty_confirmed` — the
    portal produced an export, or displayed its own "no records" message. That is CORROBORATION: the
    source itself said so.
  * the advocate/DLAR sweep has a cruder form: zero rows from BOTH grains is treated as an expired
    session or a layout change and raises before the wipe — correct, and hand-written in that module.
  * the portal-report sweep had `empty_ok: True` — a declaration with no evidence behind it at all.

THE RULE, stated once: `empty_allowed` says a zero CAN be legitimate for this report. It never says
this particular zero IS. A zero is published as the source's own answer only when the sweep can show
it asked an answerable question and nothing it depends on was broken. Everything else is reported —
`UNVERIFIED` (we cannot tell) or `SUSPECT` (we have positive evidence that we broke it) — because a
feed that stops arriving must become visible without a human noticing a P&L looks odd.

RULE TWO: no carrier, tenant, portal or report NAME appears here. The thresholds (`arrears_days`,
`stale_after_days`) are per-org config rows the caller reads — `report_definitions` for the portal
sweep — with house defaults; this module only applies them.

Locked by `backend/harness_empty_pull_verdict.py`, which fails the build if a sweep stops
dereferencing this module or grows a silent-zero branch of its own.
"""

# ── VERDICTS ──────────────────────────────────────────────────────────────────────────────────────
# CONFIRMED  the source's own answer. Publish as "nothing posted"; it is not a failure.
# UNVERIFIED we cannot tell whether the source is quiet or we asked an unanswerable question.
# SUSPECT    positive evidence that WE broke it (a control this pull depends on failed in this run,
#            or this report may never legitimately come back empty).
CONFIRMED = "confirmed_empty"
UNVERIFIED = "unverified_empty"
SUSPECT = "suspect_empty"

#: Verdicts that may be reported as a clean, successful "no data" outcome.
TRUSTED_VERDICTS = (CONFIRMED,)

# Corroboration a caller can offer, strongest first. These are the words for EVIDENCE, not for a
# wish: each one names something the sweep actually observed.
#   source_reported_empty  the source displayed/returned its own "no records" answer
#   export_produced_empty  the source produced an export file and it had a header and no data rows
#   control_proven_broken  a control this pull depends on was proven unsettable in this same run
# The VALUE is deliberately the string the merchant sweep has published since 2026-07-28 and that the
# Email Imports screen matches on — so the one home and the existing contract are literally the same
# string, and the sweep dereferences it instead of carrying its own copy.
SOURCE_REPORTED_EMPTY = "portal_reported_empty"
EXPORT_PRODUCED_EMPTY = "export_produced_empty"
CONTROL_PROVEN_BROKEN = "control_proven_broken"

_POSITIVE_CORROBORATION = (SOURCE_REPORTED_EMPTY,)

# House defaults, used only when the caller's config supplies nothing. A feed that posts in arrears
# must be ASKED for a window that can contain data; a one-day window on an in-arrears feed returns
# zero rows forever and every one of them looks legitimate.
DEFAULT_ARREARS_DAYS = 1
DEFAULT_STALE_AFTER_DAYS = 7


def window_days(begin_iso, end_iso):
    """How many days a [begin, end] inclusive ISO window covers. None when either end is unusable.

    PURE. 'YYYY-MM-DD' only — the one date shape every sweep's window already speaks."""
    from datetime import date
    def _d(s):
        s = str(s or "").strip()
        if len(s) < 10 or s[4] != "-" or s[7] != "-":
            return None
        try:
            return date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
        except ValueError:
            return None
    b, e = _d(begin_iso), _d(end_iso)
    if not b or not e or e < b:
        return None
    return (e - b).days + 1


def required_window_days(arrears_days=None):
    """The narrowest window that can contain data for a feed posting up to `arrears_days` late.

    A source that posts day D's compensation on day D+N cannot answer a one-day question about day D
    until N days have passed, so the window must reach back N days as well as forward to today."""
    try:
        n = int(arrears_days)
    except (TypeError, ValueError):
        n = DEFAULT_ARREARS_DAYS
    return max(1, n)


def classify_empty_pull(*, empty_allowed, corroboration=None, broken_controls=None,
                        window=None, window_span_days=None, arrears_days=None,
                        ever_landed=None, days_since_last_row=None, stale_after_days=None,
                        label=None):
    """Classify ONE zero-row pull. PURE. Returns

        {"verdict": …, "trusted": bool, "reason": <machine key>, "sentence": <operator English>,
         "evidence": {…what the decision was made on…}}

    `empty_allowed`        does this report EVER have a legitimate empty answer (the registry flag)
    `corroboration`        what the source itself said, if anything — one of the module constants
    `broken_controls`      names of controls PROVEN unsettable in this same run that this pull
                           depends on (a non-empty sequence is positive evidence against the zero)
    `window` / `window_span_days`
                           the slice asked for: ('YYYY-MM-DD','YYYY-MM-DD') and/or its day count
    `arrears_days`         how many days late this source posts (per-org config; house default 1)
    `ever_landed`          has this path EVER landed a row (False ⇒ a zero proves nothing)
    `days_since_last_row`  age of the newest row this path has landed
    `stale_after_days`     how long a silence may run before it stops being believable (config)

    Ordered most-specific first, so the strongest piece of evidence decides. Unknown evidence is
    never read as good news: an argument left None simply cannot trigger its own rule.
    """
    what = label or "this report"
    span = window_span_days
    if span is None and window:
        try:
            span = window_days(window[0], window[1])
        except (IndexError, TypeError):
            span = None
    need = required_window_days(arrears_days)
    broken = [str(c) for c in (broken_controls or []) if c]
    evidence = {"empty_allowed": bool(empty_allowed), "corroboration": corroboration,
                "broken_controls": broken, "window": list(window) if window else None,
                "window_span_days": span, "required_window_days": need,
                "ever_landed": ever_landed, "days_since_last_row": days_since_last_row,
                "stale_after_days": stale_after_days}

    def out(verdict, reason, sentence):
        return {"verdict": verdict, "trusted": verdict in TRUSTED_VERDICTS,
                "reason": reason, "sentence": sentence, "evidence": evidence}

    # (1) A control this pull depends on was PROVEN broken in this same run. The strongest evidence
    #     there is, and it outranks even the source's own "no records" — a filter that did not take
    #     makes the source answer a different question from the one we asked.
    if broken:
        return out(SUSPECT, "control_proven_broken",
                   f"{what}: 0 rows, and {', '.join(broken)} was proven unsettable in this same "
                   f"run — the source answered a question we did not ask. NOT reported as 'nothing "
                   f"posted'.")

    # (2) This report may never legitimately come back empty (the DLAR shape: an empty pull is an
    #     expired session or a layout change, never a real empty month).
    if not empty_allowed:
        return out(SUSPECT, "empty_never_legitimate",
                   f"{what}: 0 rows, and a zero-row pull is never a legitimate answer for it — "
                   f"treat as an expired session or a source change. Nothing was written.")

    # (3) The source said so itself, in its own words. This is what `empty_confirmed` has always
    #     meant on the merchant sweep, and it is the only thing that earns a clean 'no data'.
    if corroboration in _POSITIVE_CORROBORATION:
        return out(CONFIRMED, corroboration,
                   f"{what}: the source ran and reported no records — its own answer, not a pull "
                   f"problem.")

    # (4) We asked a question the source cannot answer yet. An in-arrears feed asked for a window
    #     narrower than its arrears returns zero rows every time, forever, and each one looks fine.
    if span is not None and span < need:
        return out(UNVERIFIED, "window_narrower_than_arrears",
                   f"{what}: 0 rows for a {span}-day window, but this source posts up to {need} "
                   f"day(s) in arrears — the window cannot contain data, so the zero says nothing "
                   f"about the source. Widen it (arrears_days) before believing a zero.")

    # (5) This path has never delivered. A first-ever zero is indistinguishable from a path that
    #     does not work at all, and must not be published as the source's answer.
    if ever_landed is False:
        return out(UNVERIFIED, "never_landed",
                   f"{what}: 0 rows, and this path has never landed a single row — a zero cannot be "
                   f"told apart from a pull that does not work. Not reported as 'nothing posted'.")

    # (6) The silence has run longer than a silence is believable for.
    if days_since_last_row is not None:
        limit = stale_after_days if stale_after_days is not None else DEFAULT_STALE_AFTER_DAYS
        try:
            age, limit = int(days_since_last_row), int(limit)
        except (TypeError, ValueError):
            age, limit = None, None
        if age is not None and limit is not None and age > limit:
            return out(UNVERIFIED, "silent_longer_than_allowed",
                       f"{what}: 0 rows, and nothing has landed for {age} day(s) — longer than the "
                       f"{limit} day(s) a quiet source is given. Something has stopped working.")

    # (7) An answerable question, a working path, a recent arrival. A quiet slice is just quiet.
    if corroboration == EXPORT_PRODUCED_EMPTY:
        return out(CONFIRMED, corroboration,
                   f"{what}: the source produced an export for the window and it carried no data "
                   f"rows — a real empty result.")
    return out(CONFIRMED, "answerable_window_quiet_source",
               f"{what}: 0 rows for {_window_text(window, span)} on a path that is landing rows "
               f"normally — nothing posted for that slice.")


def _window_text(window, span):
    if window:
        try:
            if window[0] == window[1]:
                return str(window[0])
            return f"{window[0]}..{window[1]}"
        except (IndexError, TypeError):
            pass
    return f"a {span}-day window" if span else "the requested window"


class ControlLedger:
    """What ONE run proved broken, shared by every leg of that run.

    The 2026-10-03 run is the whole argument for this being run-scoped rather than per-leg: the leg
    that proved the date filter unsettable ran SECOND, so a per-leg check would have cleared the
    first leg's zero. Legs therefore record their zeros as PROVISIONAL and the run settles them all
    once every leg's evidence is in — see `settle`.

    PURE: a dict and a list. Controls are named by the caller; this holds no vocabulary of its own.
    """

    def __init__(self):
        self.broken = {}          # control name -> why
        self._provisional = []    # [(result_dict, kwargs_for_classify, controls_this_leg_depends_on)]

    def control_failed(self, control, detail=None):
        """Record that `control` could not be set in this run (a leg may call this and still raise)."""
        if control:
            self.broken.setdefault(str(control), str(detail or "could not be set"))
        return self

    def defer(self, result, controls=(), **evidence):
        """Hold a zero-row leg's result until the run ends. `result` is the caller's own dict; it is
        returned unchanged so the caller can keep appending it to its results list as before."""
        self._provisional.append((result, dict(evidence), tuple(controls or ())))
        return result

    def settle(self):
        """Classify every deferred zero now that the run's evidence is complete.

        Returns [(result, verdict)] in the order deferred, and writes the verdict onto each result
        under `empty_verdict` so the run summary and the stored journal carry it verbatim."""
        out = []
        for result, evidence, controls in self._provisional:
            broken = [c for c in controls if c in self.broken]
            v = classify_empty_pull(broken_controls=broken, **evidence)
            if isinstance(result, dict):
                result["empty_verdict"] = v
                if not v["trusted"]:
                    result["mode"] = "unverified_no_data"
                    result["note"] = v["sentence"]
            out.append((result, v))
        return out

    @property
    def deferred(self):
        return [r for r, _e, _c in self._provisional]
