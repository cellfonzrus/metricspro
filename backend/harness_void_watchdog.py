"""PROOF HARNESS — voids and returns made visible (index §52). DB-free, stdlib only.

CLAUDE.md: pure logic ships with a DB-free proof harness, and a design fix ships with a check that
FAILS THE BUILD if a caller stops dereferencing the shared fact.

The shared fact here is `gp_report.countable_sale_skip_reason` — the pay path's own rule for "is this
a countable sale line, and if not, why not". Section W is the lock: if this module ever grows a
private void test, the build fails. That matters because the alternative was measured in the code:
`is_voided` is dereferenced about a dozen times and every one of them only EXCLUDES the line, so a
second, private reading of "what is a void" would drift the first time a carrier changed a token.

Run: `python backend/harness_void_watchdog.py`
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "app", "modules", "commcalc", "void_watchdog.py")

PASS, FAIL = [], []


def ok(n):
    PASS.append(n)


def bad(n, d=""):
    FAIL.append(f"{n}{(' — ' + d) if d else ''}")


def line(**kw):
    """A `commcalc.raw_sales` row with the columns migration 002 actually created."""
    base = {"store": "B-1", "salesperson": "Rep One", "ext_price": 100.0, "trans_id": "T1",
            "trans_date": "2026-10-01", "product_desc": "Phone", "serial_1": "IMEI1",
            "mdn": "5550001", "tender_type": "Cash", "contract_type": "Activation",
            "voided": "", "trans_type": "Sale"}
    base.update(kw)
    return base


def kinds(flags):
    return sorted(f["flag_type"] for f in flags)


def main():
    sys.path.insert(0, HERE)
    from app.modules.commcalc import flag_registry as reg
    from app.modules.commcalc import void_watchdog as vw
    from app.modules.commcalc import gp_report

    SRC = open(MOD, "r", encoding="utf-8").read()

    # ── W. THE LOCK: one rule, two readings. No private void test may appear here. ──────────────
    if "countable_sale_skip_reason" in SRC:
        ok("W1 the module dereferences the pay path's own countable-sale rule")
    else:
        bad("W1 the shared countable-sale rule is no longer dereferenced",
            "a private void test would drift from what actually pays")

    # The specific regressions: re-implementing the token test, or re-spelling the tokens.
    private = []
    if re.search(r"VOID_TOKENS\s*=", SRC):
        private.append("re-declares VOID_TOKENS")
    if re.search(r"def\s+is_voided", SRC):
        private.append("defines its own is_voided")
    if re.search(r"in\s*\(\s*['\"]true['\"]", SRC) or re.search(r"\.lower\(\)\s*==\s*['\"]true['\"]", SRC):
        private.append("tests a void token itself")
    if not private:
        ok("W2 no second copy of 'what counts as a void' exists in this module")
    else:
        bad("W2 a private void test appeared", "; ".join(private))

    # And the dereference must actually work on the real rule, not just be mentioned in a comment.
    if (vw.classify(line(voided="true")) == vw.VOIDED
            and vw.classify(line(trans_type="Return")) == vw.RETURNED
            and vw.classify(line(salesperson="")) == vw.UNATTRIBUTED
            and vw.classify(line()) == vw.COUNTABLE):
        ok("W3 all four classifications come back from the shared rule")
    else:
        bad("W3 the classification does not match the shared rule")

    # Every token the pay path treats as a void must classify as a void here. This is the arm that
    # catches a carrier adding a token: the two readings move together or the build fails.
    missed = [t for t in gp_report.VOID_TOKENS if vw.classify(line(voided=t)) != vw.VOIDED]
    if not missed:
        ok(f"W4 all {len(gp_report.VOID_TOKENS)} void tokens the pay path honours are honoured here")
    else:
        bad("W4 a void token the pay path honours is not a void here", ", ".join(missed))

    # ── S. The register: the list that did not exist. ───────────────────────────────────────────
    rows = [line(), line(trans_id="T2", voided="yes", ext_price=250.0),
            line(trans_id="T3", trans_type="Return", ext_price=75.0),
            line(trans_id="T4", salesperson="", voided="true", ext_price=40.0)]
    s = vw.summarise(rows)
    if s["counts"] == {"countable": 1, "voided": 2, "return": 1, "unattributed": 0}:
        ok("S1 every line is counted in exactly one bucket")
    else:
        bad("S1 the buckets do not add up", str(s["counts"]))

    if len(s["lines"]) == 3 and all(l["kind"] != "countable" for l in s["lines"]):
        ok("S2 every non-countable line is kept and listed — the thing that did not exist before")
    else:
        bad("S2 the void register is incomplete", f"{len(s['lines'])} lines")

    if abs(s["amounts"]["voided"] - 290.0) < 0.01:
        ok("S3 the money attached to the voided lines is reported, not just the count")
    else:
        bad("S3 the voided value is wrong", str(s["amounts"]))

    if s["void_share"] == round(2 / 4, 4):
        ok("S4 the void share is a share of LINES, on the same basis as the counts")
    else:
        bad("S4 the void share is computed on a different basis", str(s["void_share"]))

    empty = vw.summarise([])
    if empty["void_share"] is None and empty["total_lines"] == 0:
        ok("S5 no lines means no void rate — null, never 0%, which would read as clean")
    else:
        bad("S5 an empty feed reports a fabricated 0% void rate", str(empty["void_share"]))

    # A rep with lines but no voids DOES have a rate, and it is zero. The distinction in S5 is
    # between "no denominator" and "a real zero", so both must be right.
    clean = vw.summarise([line(), line(trans_id="T9")])
    if clean["void_share"] == 0.0:
        ok("S6 a rep who sold and voided nothing has a real 0% rate, not null")
    else:
        bad("S6 a genuine zero is being reported as unknown", str(clean["void_share"]))

    if s["by_rep"] and s["by_store"]:
        ok("S7 the register breaks down by rep and by store, so somebody owns each number")
    else:
        bad("S7 there is no per-rep or per-store breakdown")

    # ── A. The rate rules, and the floor that stops them being noise. ──────────────────────────
    many = [line(trans_id=f"T{i}") for i in range(30)]
    many += [line(trans_id=f"V{i}", voided="true") for i in range(5)]
    out = vw.calc_void_flags(many, period="October 2026", period_month=10, period_year=2026)
    if "VOID_RATE_HIGH" in kinds(out):
        ok("A1 a rep voiding 5 of 35 lines (14%) is above the 5% house share and is raised")
    else:
        bad("A1 a 14% void rate is not raised", str(kinds(out)))

    tiny = [line(trans_id="T1"), line(trans_id="V1", voided="true")]
    out2 = vw.calc_void_flags(tiny, period="October 2026")
    if "VOID_RATE_HIGH" not in kinds(out2):
        ok("A2 one void out of two lines is 50% but below the line floor, so it is not raised "
           "(the alert noise the house removed in §19.17)")
    else:
        bad("A2 a rep who barely sold is being flagged on a meaningless share", str(kinds(out2)))

    out3 = vw.calc_void_flags(tiny, period="October 2026",
                              rules=[{"flag_type": "VOID_RATE_HIGH",
                                      "params": {"min_lines": 1, "max_share": 0.2}}])
    if "VOID_RATE_HIGH" in kinds(out3):
        ok("A3 a tenant lowering the floor catches the same rep (RULE TWO: config, never code)")
    else:
        bad("A3 the per-org floor is not honoured", str(kinds(out3)))

    returns = [line(trans_id=f"T{i}") for i in range(30)]
    returns += [line(trans_id=f"R{i}", trans_type="Return") for i in range(5)]
    out4 = vw.calc_void_flags(returns, period="October 2026")
    if "RETURN_RATE_HIGH" in kinds(out4):
        ok("A4 the return rate is watched separately from the void rate")
    else:
        bad("A4 a 14% return rate is not raised", str(kinds(out4)))

    # ── B. The same device sold and voided. ────────────────────────────────────────────────────
    pair = [line(serial_1="IMEI9"), line(trans_id="T2", serial_1="IMEI9", voided="true")]
    out5 = vw.calc_void_flags(pair, period="October 2026")
    f5 = next((f for f in out5 if f["flag_type"] == "VOID_AFTER_SALE"), None)
    if f5 and f5["imei"] == "IMEI9":
        ok("B1 one device on both a counted sale and a voided line is raised, naming the device")
    else:
        bad("B1 the sold-and-voided device is not raised", str(kinds(out5)))

    if f5 and f5["severity"] == reg.CRITICAL and reg.grain_of("VOID_AFTER_SALE") == "transaction":
        ok("B2 it is CRITICAL and transaction-grain, so a manager can click through to the device")
    else:
        bad("B2 the sold-and-voided finding is mis-graded")

    # THE HONESTY RULE. The feed is a DATE with no clock, so the finding must not imply a sequence.
    if f5 and "no time of day" in (f5["description"] or ""):
        ok("B3 the finding says the order of the two is unknown — the feed carries no clock, and a "
           "'voided minutes later' claim would be invented")
    else:
        bad("B3 the finding implies a sequence the data cannot prove",
            (f5 or {}).get("description", "")[:100])

    # Two different devices must NOT pair up.
    nopair = [line(serial_1="IMEI1"), line(trans_id="T2", serial_1="IMEI2", voided="true")]
    if "VOID_AFTER_SALE" not in kinds(vw.calc_void_flags(nopair, period="October 2026")):
        ok("B4 two different devices do not pair into a false accusation")
    else:
        bad("B4 unrelated devices were paired")

    # A line with no device at all must not pair on the empty string.
    blank = [line(serial_1=""), line(trans_id="T2", serial_1="", voided="true")]
    if "VOID_AFTER_SALE" not in kinds(vw.calc_void_flags(blank, period="October 2026")):
        ok("B5 lines carrying no device do not pair on an empty identifier")
    else:
        bad("B5 blank device identifiers were matched to each other")

    # ── C. The void nobody can be asked about. ─────────────────────────────────────────────────
    anon = [line(trans_id="V1", salesperson="", voided="true", ext_price=40.0),
            line(trans_id="V2", salesperson="admin", voided="true", ext_price=60.0)]
    out6 = vw.calc_void_flags(anon, period="October 2026")
    f6 = next((f for f in out6 if f["flag_type"] == "VOID_UNATTRIBUTED"), None)
    if f6 and abs(float(f6["amount"]) - 100.0) < 0.01:
        ok("C1 voids with nobody named — blank AND the admin pseudo-rep — are counted per store")
    else:
        bad("C1 unattributed voids are not raised with their value", str(out6))

    named = [line(trans_id="V1", salesperson="Rep One", voided="true")]
    if "VOID_UNATTRIBUTED" not in kinds(vw.calc_void_flags(named, period="October 2026")):
        ok("C2 a void with a rep on it is not counted as unattributed")
    else:
        bad("C2 an attributed void was counted as anonymous")

    # ── D. Everything written is registered and on the one scale. ──────────────────────────────
    everything = vw.calc_void_flags(many + pair + anon + returns, period="October 2026",
                                    period_month=10, period_year=2026,
                                    rules=[{"flag_type": "VOID_RATE_HIGH",
                                            "params": {"min_lines": 1}}])
    offscale = sorted({f["severity"] for f in everything} - set(reg.SEVERITIES))
    if not offscale:
        ok("D1 every severity this module writes is on the registry's scale")
    else:
        bad("D1 a severity was written off the scale", ", ".join(offscale))

    unreg = sorted({f["flag_type"] for f in everything if reg.canon_type(f["flag_type"]) is None})
    if not unreg:
        ok("D2 every flag type this module writes is registered")
    else:
        bad("D2 an unregistered type was written", ", ".join(unreg))

    if all(str(f.get("source_ref") or "").strip() for f in everything):
        ok("D3 every finding carries a stable identity, so a re-run refreshes rather than duplicates")
    else:
        bad("D3 a finding has no identity and would be re-keyed on every run")

    if all(f.get("period") == "October 2026" for f in everything):
        ok("D4 the period is written in the spelling the rest of the table uses")
    else:
        bad("D4 the period spelling diverges")

    # ── Z. ARMED: quiet when there is nothing to find, and silenceable. ────────────────────────
    if not vw.calc_void_flags([line() for _ in range(50)], period="October 2026"):
        ok("Z1 fifty clean sales raise nothing at all")
    else:
        bad("Z1 clean sales produced findings")

    if not vw.calc_void_flags([], period="October 2026") and not vw.calc_void_flags(
            None, period="October 2026"):
        ok("Z2 empty and None inputs are handled without raising")
    else:
        bad("Z2 empty input produced findings")

    all_off = vw.calc_void_flags(many + pair + anon, period="October 2026",
                                 rules=[{"flag_type": t, "enabled": False} for t in vw.FLAG_TYPES])
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
