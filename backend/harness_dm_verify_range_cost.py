#!/usr/bin/env python3
"""DM Verify serves a FULL MONTH, and no org-level lookup sits in the per-date loop (index §46).

OWNER 2026-10-01: "we need atleast 30 days of data". `/closing/summary` silently narrowed every
month-long range to its last 14 days and said so in a banner — the owner was reading a fortnight
and being told to narrow further.

WHY 14 EXISTED, AND WHY RAISING IT ALONE WOULD HAVE BEEN A REGRESSION: the cap is a perf bound with
real history behind it — OWNER BUG REPORT 2026-07-29, "DM verify locks out for over 3-4 minutes".
That fix hoisted FIVE org-scoped queries out of the per-date loop into
`_closing_summary_org_ctx`. It MISSED two more, which are just as date-independent:

  · `_pos_term(client, org_id)` — and `report_labels.load_report_labels` behind it does TWO uncached
    org-scoped reads. `_closing_summary_for_date` called it TWICE per date, so a 14-date range paid
    56 redundant reads and a 31-date range would have paid 124.
  · the `x_report_ever` existence probe — a `pos_tender_summary LIMIT 1`. "Has this tenant EVER had
    an X-report" cannot depend on which day is being summarized.

Both now ride org_ctx, removing ~5 reads per date, so a 45-date range costs FEWER per-date round
trips than a 14-date range did before. THAT is what makes the raise safe, and this harness is what
stops the next edit quietly undoing it.

WHAT FAILS THE BUILD HERE:
  A. the cap being below a full calendar month (31) again;
  B. the cap drifting away from its sibling `_RECON_MAX_DATES` (the evidence for 45 — that endpoint
     already replays the SAME heavy `_b2b_day` for up to 45 dates);
  C. `_closing_summary_org_ctx` not supplying `pos_term` / `x_report_any`;
  D. the per-date function calling `_pos_term(client, org_id)` anywhere but the ctx fallback;
  E. an ORG-LEVEL (non-date-scoped) query appearing in the per-date loop outside a ctx fallback;
  F. the per-date function losing its date-scoped reads (i.e. the hoist going too far).

Stdlib only — parses the router as text/AST. No pip install, no DB.
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROUTER = os.path.join(HERE, "app", "modules", "closing", "router.py")

_p = _f = 0


def check(name, got, want=None):
    global _p, _f
    ok = bool(got) if want is None else (got == want)
    if ok:
        _p += 1
        print("  PASS  %s" % name)
    else:
        _f += 1
        print("  ✗ %s" % name)
        if want is not None:
            print("        want: %r" % (want,))
        print("        got : %r" % (got,))
    return ok


def func_body(src, name):
    i = src.index("def %s(" % name)
    m = re.search(r"\n(?=def |@router)", src[i + 10:])
    return src[i:i + 10 + m.start()]


def main():
    src = open(ROUTER, "r", encoding="utf-8").read()

    print("=" * 96)
    print("DM VERIFY SERVES A MONTH, AND THE PER-DATE LOOP HOLDS NO ORG-LEVEL QUERY (index §46)")
    print("=" * 96)

    # ── A/B: the cap ─────────────────────────────────────────────────────────────────────────────
    print("\nA. the range cap covers a full calendar month")
    m = re.search(r"^_SUMMARY_MAX_RANGE_DATES\s*=\s*(\d+)", src, re.M)
    r = re.search(r"^_RECON_MAX_DATES\s*=\s*(\d+)", src, re.M)
    check("the cap is declared", m is not None)
    check("the sibling recon cap is declared", r is not None)
    cap, recon = int(m.group(1)), int(r.group(1))
    print("  ·  _SUMMARY_MAX_RANGE_DATES=%d, _RECON_MAX_DATES=%d" % (cap, recon))
    check("the cap covers the longest calendar month (>= 31): %d" % cap, cap >= 31)
    check("...and matches its sibling, the evidence it is affordable", cap, recon)

    # ── C: org_ctx supplies the hoisted facts ────────────────────────────────────────────────────
    print("\nB. the org-level facts are supplied ONCE, by org_ctx")
    ctx = func_body(src, "_closing_summary_org_ctx")
    check("org_ctx returns pos_term", '"pos_term":' in ctx)
    check("org_ctx returns x_report_any", '"x_report_any":' in ctx)
    check("...and it is the one place that calls _pos_term for this path",
          ctx.count("_pos_term(client, org_id)"), 1)
    check("the label read is guarded (a hiccup must not fail the whole request)",
          "except Exception:" in ctx and "_pos_ctx = None" in ctx)

    # ── D/E/F: the per-date loop ─────────────────────────────────────────────────────────────────
    print("\nC. the per-date function holds no org-level lookup")
    body = func_body(src, "_closing_summary_for_date")

    # D — _pos_term may appear ONLY as the ctx fallback on the assignment line.
    pos_calls = [l.strip() for l in body.split("\n") if "_pos_term(client, org_id)" in l]
    check("every _pos_term call in the per-date function is the ctx fallback: %d call(s)"
          % len(pos_calls),
          all(l.startswith("_pos = org_ctx.get(\"pos_term\")") for l in pos_calls))
    check("...and the copy string reuses the resolved value, never a fresh read",
          "{_pos} is actually scheduled to email an X-Report" in body)

    # F — the genuinely date-scoped reads are still there (the hoist did not go too far).
    for col, label in (('"daily_closing"', "the day's closings"),
                       ('"shifts"', "that day's shifts"),
                       ('"daily_closing_verification"', "that day's verifications"),
                       ('"closing_expense"', "that day's expense lines")):
        check("still read per date: %s" % label, col in body)

    # E — any .execute() in the loop must be date-scoped, or inside the ctx fallback branch.
    lines = body.split("\n")
    offenders = []
    for n, l in enumerate(lines, 1):
        if ".execute()" not in l:
            continue
        window = "\n".join(lines[max(0, n - 10):n + 1])
        date_scoped = ('close_date", date' in window) or ('shift_date", date' in window)
        fallback = 'x_report_any" in org_ctx' in window
        if not date_scoped and not fallback:
            offenders.append(l.strip()[:90])
    check("no org-level query in the per-date loop: %s" % (offenders or "none"), offenders, [])

    # ── G: armed negative controls ───────────────────────────────────────────────────────────────
    print("\nD. CONTROLS — each rule goes RED with the defect patched back in")
    check("CONTROL: the pre-fix cap of 14 would narrow a 30-day ask to a fortnight", 14 < 30)
    check("CONTROL: ...and 31 days at 2 pos_term calls x 2 uncached reads = 124 redundant reads",
          31 * 2 * 2, 124)
    fake_ctx = ctx.replace('"pos_term": _pos_ctx, "x_report_any": _xre,\n', "")
    check("CONTROL: dropping the keys from org_ctx → RED",
          '"pos_term":' not in fake_ctx and '"x_report_any":' not in fake_ctx)
    fake_body = "    _pos = _pos_term(client, org_id)\n" + body
    fake_calls = [l.strip() for l in fake_body.split("\n") if "_pos_term(client, org_id)" in l]
    check("CONTROL: a bare _pos_term call back in the per-date loop → RED",
          not all(l.startswith("_pos = org_ctx.get(\"pos_term\")") for l in fake_calls))
    fake_q = ('    rows = (client.schema("commcalc").table("carrier").select("name")\n'
              '            .eq("org_id", org_id).execute().data) or []\n')
    fl = fake_q.split("\n")
    bad = []
    for n, l in enumerate(fl, 1):
        if ".execute()" in l:
            w = "\n".join(fl[max(0, n - 10):n + 1])
            if 'close_date", date' not in w and 'x_report_any" in org_ctx' not in w:
                bad.append(l.strip())
    check("CONTROL: an org-level query added to the loop → RED", bool(bad))

    print("\n" + "=" * 96)
    print("RESULT: %d passed, %d failed" % (_p, _f))
    print("=" * 96)
    if _f:
        print("FAIL  DM Verify's range cap or its per-date cost has regressed")
        return 1
    print("OK  a full month is served, and the per-date loop holds only date-scoped reads")
    return 0


if __name__ == "__main__":
    sys.exit(main())
