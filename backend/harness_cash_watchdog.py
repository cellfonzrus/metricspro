"""PROOF HARNESS — the cash watchdog (index §53). DB-free, stdlib only, no app import.

CLAUDE.md: *"Pure logic ships with a DB-free proof harness"*, and a design fix ships with the
regression that reproduces the reported defect. Section R is that regression: the measured
B-117 / 2026-10-01 store-day, entered and never corrected, which sat unseen for four days.

Every rule is ARMED — section Z proves the detector goes QUIET when the condition is absent, so a
rule that fires on everything (or on nothing) fails here rather than on the board.

Run: `python backend/harness_cash_watchdog.py`
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODULES = os.path.join(HERE, "app", "modules")

PASS, FAIL = [], []


def ok(n):
    PASS.append(n)


def bad(n, d=""):
    FAIL.append(f"{n}{(' — ' + d) if d else ''}")


def load():
    """Plain imports, the convention `harness_closing_unfinished_day.py` already uses: run from
    `backend/` with `sys.path` containing '.'.

    All three modules are PURE — `flag_registry` imports nothing from the app, `unfinished_day` does
    no I/O and no framework import, and `cash_watchdog` imports only those two. So this harness needs
    no database, no fake client and no fastapi, which is the property the purity rule exists to give.
    """
    sys.path.insert(0, HERE)
    from app.modules.commcalc import flag_registry as reg
    from app.modules.closing import unfinished_day as day
    from app.modules.closing import cash_watchdog as cash
    return vars(reg), vars(day), vars(cash)


def attempt(**kw):
    """A `commcalc.closing_attempt` row with the shape migration 103 actually created."""
    base = {"store_code": "B-1", "close_date": "2026-10-01", "employee_name": "Rep One",
            "attempt_no": 1, "entered_cash": 1000.0, "entered_credit": 100.0,
            "b2b_cash": 1000.0, "b2b_credit": 100.0, "cash_dir": "ok", "credit_dir": "ok",
            "blocked": False, "accepted": True, "auto_accepted": False}
    base.update(kw)
    return base


def closing(**kw):
    base = {"store_code": "B-1", "close_date": "2026-10-01", "store_address": "1 High St"}
    base.update(kw)
    return base


def kinds(flags):
    return sorted(f["flag_type"] for f in flags)


def main():
    reg, day, cash = load()
    calc = cash["calc_cash_flags"]

    # ── R. THE REGRESSION. The measured B-117 / 2026-10-01 case. ────────────────────────────────
    # Two blocked tries two seconds apart, $194.17 cash over and $123.41 credit over, no closing row
    # at all. Before this module nothing raised it, and on 2026-10-05 it was still waiting.
    burnside = [attempt(store_code="B-117", employee_name="Rana", attempt_no=n,
                        entered_cash=2826.00, entered_credit=270.00,
                        b2b_cash=2631.83, b2b_credit=146.59,
                        cash_dir="over", credit_dir="over", blocked=True, accepted=False)
                for n in (1, 2)]
    out = calc(burnside, [])
    if kinds(out) == ["CASH_AWAITING_CORRECTION"]:
        ok("R1 the entered-and-never-corrected store-day is raised")
    else:
        bad("R1 the B-117 case is not detected", str(kinds(out)))

    if out and out[0]["severity"] == reg["CRITICAL"]:
        ok("R2 it is CRITICAL — real declared money with nobody's ruling on it")
    else:
        bad("R2 the unresolved day is not critical", out[0]["severity"] if out else "no flag")

    if out and "194.17" in out[0]["description"] and "123.41" in out[0]["description"]:
        ok("R3 the finding names both measured variances, from the gate's own record")
    else:
        bad("R3 the recorded variances are missing from the finding",
            out[0]["description"] if out else "")

    # The identity must be the store-day, so a re-run refreshes the SAME row and a manager's ruling
    # survives — the whole point of flag_persist's additive merge.
    if out and out[0]["source_ref"] == "B-117|2026-10-01":
        ok("R4 the finding is keyed on the store-day, so a re-run cannot duplicate or re-accuse")
    else:
        bad("R4 the finding has no stable store-day identity", out[0].get("source_ref") if out else "")

    if out and out[0]["period"] == "October 2026" and out[0]["period_month"] == 10:
        ok("R5 the period is written in the spelling every other writer of the table uses")
    else:
        bad("R5 the period spelling would not be found by a period query",
            repr(out[0].get("period")) if out else "")

    # ── A. Drawer over / short, and the asymmetry between them. ─────────────────────────────────
    over = calc([attempt(entered_cash=1100.0, b2b_cash=1000.0, cash_dir="over")], [closing()])
    if "CASH_OVER" in kinds(over):
        ok("A1 a drawer over tolerance is raised")
    else:
        bad("A1 a $100 over day is not raised", str(kinds(over)))

    short = calc([attempt(entered_cash=900.0, b2b_cash=1000.0, cash_dir="short")], [closing()])
    if "CASH_SHORT" in kinds(short):
        ok("A2 a drawer short of tolerance is raised")
    else:
        bad("A2 a $100 short day is not raised", str(kinds(short)))

    sev_over = next((f["severity"] for f in over if f["flag_type"] == "CASH_OVER"), None)
    sev_short = next((f["severity"] for f in short if f["flag_type"] == "CASH_SHORT"), None)
    if reg["SEVERITY_RANK"][sev_short] < reg["SEVERITY_RANK"][sev_over]:
        ok("A3 a short outranks an over — an over is a miscount, a short is money that is not there")
    else:
        bad("A3 a short does not outrank an over", f"{sev_short} vs {sev_over}")

    # ── B. Tolerance is config, and it is honoured in both directions. ──────────────────────────
    small = calc([attempt(entered_cash=1005.0, b2b_cash=1000.0, cash_dir="over")], [closing()])
    if "CASH_OVER" not in kinds(small):
        ok("B1 a $5 variance is below the house tolerance and is not raised")
    else:
        bad("B1 coin-level rounding is being flagged", str(kinds(small)))

    tightened = calc([attempt(entered_cash=1005.0, b2b_cash=1000.0, cash_dir="over")], [closing()],
                     rules=[{"flag_type": "CASH_OVER", "params": {"tolerance": 1.0}}])
    if "CASH_OVER" in kinds(tightened):
        ok("B2 a tenant tightening the tolerance to $1 catches the same $5 day (RULE TWO: config)")
    else:
        bad("B2 the per-org tolerance row is not honoured", str(kinds(tightened)))

    off = calc([attempt(entered_cash=1100.0, b2b_cash=1000.0)], [closing()],
               rules=[{"flag_type": "CASH_OVER", "enabled": False}])
    if "CASH_OVER" not in kinds(off):
        ok("B3 a tenant switching the check off silences exactly that check")
    else:
        bad("B3 a disabled check still fires")

    null_enabled = calc([attempt(entered_cash=1100.0, b2b_cash=1000.0)], [closing()],
                        rules=[{"flag_type": "CASH_OVER", "enabled": None}])
    if "CASH_OVER" in kinds(null_enabled):
        ok("B4 `enabled` left blank means the house default, not off — a cleared cell cannot "
           "silently stop a cash check")
    else:
        bad("B4 a NULL enabled disabled the check")

    # ── C. Accepted on the last try while still mismatched. ─────────────────────────────────────
    auto = calc([attempt(attempt_no=3, entered_cash=1100.0, b2b_cash=1000.0, cash_dir="over",
                         accepted=True, auto_accepted=True)], [closing()])
    if "CASH_AUTO_ACCEPTED" in kinds(auto):
        ok("C1 a day waved through on the final try is raised")
    else:
        bad("C1 an auto-accepted mismatched day is not raised", str(kinds(auto)))

    # Size-independent: a $1 auto-accept is still a day nobody agreed.
    tiny_auto = calc([attempt(attempt_no=3, entered_cash=1001.0, b2b_cash=1000.0,
                              accepted=True, auto_accepted=True)], [closing()])
    if "CASH_AUTO_ACCEPTED" in kinds(tiny_auto):
        ok("C2 being waved through IS the finding, whatever the amount")
    else:
        bad("C2 a small auto-accepted variance is swallowed", str(kinds(tiny_auto)))

    # ── D. No point-of-sale figure: silence, never a fabricated agreement. ──────────────────────
    nopos = calc([attempt(b2b_cash=None, b2b_credit=None, entered_cash=1000.0)], [closing()])
    if not [f for f in nopos if f["flag_type"] in ("CASH_OVER", "CASH_SHORT", "CREDIT_VARIANCE")]:
        ok("D1 a store-day the feed never carried produces no variance finding "
           "(a zero here would be a fabricated agreement)")
    else:
        bad("D1 a variance was invented for a day with no point-of-sale figure", str(kinds(nopos)))

    # ── E. A day nobody submitted is not this module's business. ────────────────────────────────
    none_at_all = calc([], [])
    if not none_at_all:
        ok("E1 nothing submitted, nothing raised — the missing-closing nag owns that, not this")
    else:
        bad("E1 a finding was raised for a store-day with no submit", str(kinds(none_at_all)))

    # A REFUSED submit never reached the gate, so it is not a recount anyone owes — `is_real_try`
    # (the refusal registry's own rule, dereferenced by `unfinished_day`) is what tells them apart.
    turned = calc([attempt(refused=True, blocked=False, accepted=False,
                           entered_cash=2826.0, b2b_cash=2631.83)], [])
    if not turned:
        ok("E2 a submit turned away before the gate is not read as an unresolved variance")
    else:
        bad("E2 a refused submit was read as money awaiting a correction", str(kinds(turned)))

    # And the same row WITHOUT the refusal is the Burnside case again — so E2 is not passing because
    # the input was inert.
    not_refused = calc([attempt(refused=False, blocked=True, accepted=False,
                                entered_cash=2826.0, b2b_cash=2631.83)], [])
    if kinds(not_refused) == ["CASH_AWAITING_CORRECTION"]:
        ok("E3 the same row with the refusal removed IS raised, so E2 tests the refusal and not "
           "an inert input")
    else:
        bad("E3 the E2 control does not reproduce", str(kinds(not_refused)))

    # ── F. An unreadable close date is skipped, never filed under a guessed period. ─────────────
    junk = calc([attempt(close_date="not a date", entered_cash=1100.0, b2b_cash=1000.0)],
                [closing(close_date="not a date")])
    if not junk:
        ok("F1 a day whose date cannot be read is skipped, not filed under a guessed month")
    else:
        bad("F1 a finding was written against an unreadable date", str(kinds(junk)))

    # ── G. The store-identity join. The measured 2026-10-04 trap. ──────────────────────────────
    # Attempt trail spelled one way, closing row the other. A raw-string join reported seven banked
    # days as unfinished; the resolver is what stops it.
    mixed_att = [attempt(store_code="1800GreatNeckRd", entered_cash=1100.0, b2b_cash=1000.0,
                         cash_dir="over", accepted=True)]
    mixed_close = [closing(store_code="B-1800")]
    raw_join = calc(mixed_att, mixed_close)
    if "CASH_AWAITING_CORRECTION" in kinds(raw_join):
        ok("G1 control: without a resolver the two spellings do NOT join (the 2026-10-04 trap)")
    else:
        bad("G1 the control case no longer reproduces the trap", str(kinds(raw_join)))

    resolved = calc(mixed_att, mixed_close,
                    resolve=lambda c: "B-1800" if str(c).strip() in
                    ("1800GreatNeckRd", "B-1800") else c)
    if ("CASH_AWAITING_CORRECTION" not in kinds(resolved)) and "CASH_OVER" in kinds(resolved):
        ok("G2 with the store resolver the banked day joins and is read as an over, not unfinished")
    else:
        bad("G2 the resolver does not fix the join", str(kinds(resolved)))

    # ── H. The pattern rule: several days, and it says so. ─────────────────────────────────────
    many = []
    for d in ("2026-10-01", "2026-10-02", "2026-10-03"):
        many.append(attempt(close_date=d, entered_cash=1100.0, b2b_cash=1000.0,
                            cash_dir="over", accepted=True))
    many_close = [closing(close_date=d) for d in ("2026-10-01", "2026-10-02", "2026-10-03")]
    rep = calc(many, many_close)
    if "CASH_REPEAT_VARIANCE" in kinds(rep):
        ok("H1 three variance days by one person raise the pattern as well as the days")
    else:
        bad("H1 the repeat pattern is not raised", str(kinds(rep)))

    two_only = calc(many[:2], many_close[:2])
    if "CASH_REPEAT_VARIANCE" not in kinds(two_only):
        ok("H2 two days is below the house minimum and raises no pattern")
    else:
        bad("H2 the pattern fires below its own minimum")

    pat = next((f for f in rep if f["flag_type"] == "CASH_REPEAT_VARIANCE"), {})
    if reg["grain_of"]("CASH_REPEAT_VARIANCE") == "rep_period" and "3 days" in (
            pat.get("description") or ""):
        ok("H3 the pattern is declared rep_period grain and names its days, so no drill-down lies")
    else:
        bad("H3 the pattern pretends to be a single transaction",
            pat.get("description", "")[:90])

    # ── I. Every emitted severity is on the one scale. ─────────────────────────────────────────
    everything = calc(burnside + many + auto, many_close + [closing()])
    offscale = sorted({f["severity"] for f in everything} - set(reg["SEVERITIES"]))
    if not offscale:
        ok("I1 every severity this module writes is on the registry's scale")
    else:
        bad("I1 a severity was written off the scale", ", ".join(offscale))

    unreg = sorted({f["flag_type"] for f in everything if reg["canon_type"](f["flag_type"]) is None})
    if not unreg:
        ok("I2 every flag type this module writes is registered")
    else:
        bad("I2 an unregistered type was written", ", ".join(unreg))

    if sorted(cash["FLAG_TYPES"]) == sorted(set(cash["FLAG_TYPES"])) and all(
            reg["canon_type"](t) for t in cash["FLAG_TYPES"]):
        ok("I3 the module's declared FLAG_TYPES are distinct and all registered "
           "(the retire step needs the full list, including types that produced nothing)")
    else:
        bad("I3 FLAG_TYPES is not a clean, registered set")

    # ── Z. ARMED: the detector goes quiet when there is nothing to find. ───────────────────────
    clean = calc([attempt()], [closing()])
    if not clean:
        ok("Z1 a day that balanced raises nothing at all")
    else:
        bad("Z1 a balanced day produced a finding", str(kinds(clean)))

    if not calc([], []) and not calc(None, None):
        ok("Z2 empty and None inputs are handled without raising")
    else:
        bad("Z2 empty input produced findings")

    # A rule that cannot be silenced is a rule that fires on everything.
    all_off = calc(burnside + many + auto, many_close + [closing()],
                   rules=[{"flag_type": t, "enabled": False} for t in cash["FLAG_TYPES"]])
    if not all_off:
        ok("Z3 every rule can be switched off, so none of them is unconditional")
    else:
        bad("Z3 a rule fires even when switched off", str(kinds(all_off)))

    print("\n".join(f"  PASS  {p}" for p in PASS))
    if FAIL:
        print("\n".join(f"  FAIL  {f}" for f in FAIL))
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
