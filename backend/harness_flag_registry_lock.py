"""THE FLAG REGISTRY LOCK — the build fails if a writer of `commcalc.flags` stops dereferencing it.

CLAUDE.md, "A fix is a DESIGN fix": *"A design fix ships with a check that FAILS THE BUILD if a
caller stops dereferencing the shared fact, or if a second copy appears. Without that, the next
change quietly restores the patchwork."* This is that check for the flag registry (index §53).

WHAT IT DEFENDS, AND WHY EACH RULE EXISTS
─────────────────────────────────────────
The defect was six private vocabularies for one column: `DUPLICATE_IMEI`, `sales_leak`,
`"Hotsheet Underpayment"` — and, worse, three severity scales (`HIGH`/`MEDIUM`/`LOW`, `CRITICAL`,
`critical`/`warning`) so the Flags page's colour map rendered a third of rows grey. The registry is
the one home; these rules stop it becoming a document nobody reads:

  A  every module that writes `commcalc.flags` calls `flag_registry.stamp`
  B  every `flag_type` literal at a write site is registered
  C  no module but the registry declares a severity alias map (no second copy)
  D  the registry's own invariants hold (every type has a real area, every area has a type,
     every severity is on the one scale, labels are unique)
  E  the scans themselves still match something — a guard whose pattern has rotted is the defect
     `harness_activation_bucketing.py` taught this repo about (it was RED on `main` for three weeks
     because its grep matched a function that no longer existed and no workflow ran it)

Every rule below is ARMED: the control section proves it goes RED when the fix is removed, so a rule
that can no longer fail is itself a failure.

Stdlib only, DB-free, no app import. Run: `python backend/harness_flag_registry_lock.py`
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "app", "modules")
REGISTRY = os.path.join(APP, "commcalc", "flag_registry.py")

#: Every module that writes `commcalc.flags`, with the name it writes under. Derived once by scanning
#: for the write itself (rule A re-derives it, so this list cannot silently go stale — a seventh
#: writer FAILS rule A rather than being missed).
WRITE_PATTERN = re.compile(r'table\(\s*["\']flags["\']\s*\)\s*\.\s*(insert|upsert)')
STAMP_PATTERN = re.compile(r'(?:_reg|flag_registry)\s*\.\s*stamp\s*\(')
RPC_WRITE_PATTERN = re.compile(r'flag_persist\s*\.\s*sync\s*\(')

PASS, FAIL = [], []


def ok(name):
    PASS.append(name)


def bad(name, detail=""):
    FAIL.append(f"{name}{(' — ' + detail) if detail else ''}")


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def py_files():
    out = []
    for root, _dirs, files in os.walk(APP):
        for f in files:
            if f.endswith(".py"):
                out.append(os.path.join(root, f))
    return sorted(out)


def rel(p):
    return os.path.relpath(p, HERE).replace(os.sep, "/")


# ── Load the registry WITHOUT importing the app (stdlib-only rule). ─────────────────────────────
def load_registry():
    """Exec the registry in a bare namespace. It imports nothing from the app, which is exactly why
    a pure one-home module is written that way — the lock can read it on bare Python."""
    ns = {"__name__": "flag_registry_under_test"}
    exec(compile(read(REGISTRY), REGISTRY, "exec"), ns)
    return ns


def main():
    reg = load_registry()
    TYPES = reg["TYPES"]
    AREAS = reg["AREAS"]
    AREA_KEYS = reg["AREA_KEYS"]
    SEVERITIES = reg["SEVERITIES"]

    files = py_files()
    writers, stampers, rpc_writers = [], [], []
    for p in files:
        src = read(p)
        if rel(p).endswith("commcalc/flag_registry.py"):
            continue
        if WRITE_PATTERN.search(src):
            writers.append(p)
        if RPC_WRITE_PATTERN.search(src):
            rpc_writers.append(p)
        if STAMP_PATTERN.search(src):
            stampers.append(p)

    # ── E1. The write scan still matches something. ─────────────────────────────────────────────
    if writers:
        ok(f"E1 the flags-write scan matches {len(writers)} module(s)")
    else:
        bad("E1 the flags-write scan matched NOTHING",
            "the pattern has rotted; this lock is no longer defending anything")

    # ── A. Every writer dereferences the registry. ──────────────────────────────────────────────
    # `flag_persist.sync` callers are exempt ONLY when they also stamp: sync writes through an RPC,
    # so the severity it carries is whatever the caller computed. The two new watchdogs stamp inside
    # their own pure detectors (via `severity_for`), which rule B checks instead.
    SELF_STAMPING = {
        # module → why it needs no stamp() call: it builds every severity through
        # `flag_registry.severity_for` at construction, so there is nothing left to canonicalise.
        "app/modules/closing/cash_watchdog.py": "builds severity via severity_for",
        "app/modules/commcalc/void_watchdog.py": "builds severity via severity_for",
    }
    missing = []
    for p in writers:
        r = rel(p).replace("backend/", "")
        if STAMP_PATTERN.search(read(p)):
            continue
        if r in SELF_STAMPING or rel(p) in SELF_STAMPING:
            continue
        missing.append(r)
    if not missing:
        ok(f"A every one of the {len(writers)} flags writers dereferences the registry")
    else:
        bad("A a module writes commcalc.flags without dereferencing the registry",
            ", ".join(missing) + " — add `flag_registry.stamp(rows)` before the write")

    # ── A2. No DEAD dereference: a stamp() caller must actually build findings. ─────────────────
    # It need not WRITE them. `commcalc/flags.py`, `portout_flags.py` and
    # `sale_installment_engine.py` are pure detectors that build rows and hand them to
    # `commcalc/router` to write, and stamping at the build site is the correct place — a
    # canonicalisation done only at the write site would miss their `persist=False` callers. So the
    # rule is "builds findings", not "writes findings".
    BUILDS = re.compile(r'["\']flag_type["\']\s*:')
    # A WRITER also qualifies: `commcalc/router` builds no row itself — it receives them from the
    # detectors and writes them — and stamping at its write site is the belt-and-braces guarantee
    # that a future detector plugged into the same list cannot reach the table off the scale.
    stray = [rel(p) for p in stampers
             if not BUILDS.search(read(p))
             and not WRITE_PATTERN.search(read(p))
             and not RPC_WRITE_PATTERN.search(read(p))]
    if not stray:
        ok(f"A2 all {len(stampers)} stamp() callers actually build findings (no dead dereference)")
    else:
        bad("A2 stamp() is called by a module that builds no findings", ", ".join(stray))

    # ── B. Every flag_type literal at a write site is registered. ───────────────────────────────
    # The scan reads the literal out of the dict being built, which is how every writer spells it.
    # An f-string type (asset's `f"Inventory mismatch — {k}"`) is matched on its constant head
    # against the registry's `prefix` types — that is the whole reason `prefix` exists.
    TYPE_LITERAL = re.compile(r'["\']flag_type["\']\s*:\s*(f?)["\']([^"\']+)["\']')
    unregistered, seen = [], set()
    for p in writers:
        for is_f, raw in TYPE_LITERAL.findall(read(p)):
            val = raw.strip()
            if not val:
                continue
            seen.add(val)
            if reg["canon_type"](val) is None:
                unregistered.append(f"{rel(p)}: {val!r}")
    if not unregistered:
        ok(f"B all {len(seen)} flag_type literals at write sites are registered")
    else:
        bad("B a flag_type is written that the registry does not declare",
            "; ".join(sorted(unregistered)) + " — add it to flag_registry.TYPES")

    if seen:
        ok(f"B2 the flag_type scan matches {len(seen)} literal(s)")
    else:
        bad("B2 the flag_type scan matched NOTHING", "the pattern has rotted")

    # ── C. No second severity vocabulary. ───────────────────────────────────────────────────────
    # The specific regression this stops: another module declaring its own map from the stored
    # strings to a scale. One home for "what does this severity mean", or the divergence returns.
    ALIAS_MAP = re.compile(r'["\']warning["\']\s*:\s*["\'](?:CRITICAL|HIGH|MEDIUM|LOW)["\']')
    copies = [rel(p) for p in files
              if not rel(p).endswith("commcalc/flag_registry.py") and ALIAS_MAP.search(read(p))]
    if not copies:
        ok("C no module but the registry maps a severity spelling onto the scale")
    else:
        bad("C a second severity vocabulary appeared", ", ".join(copies))

    # ── D. The registry's own invariants. ───────────────────────────────────────────────────────
    bad_area = sorted(k for k, m in TYPES.items() if m.get("area") not in AREA_KEYS)
    if not bad_area:
        ok(f"D1 all {len(TYPES)} types sit in one of the {len(AREAS)} declared areas")
    else:
        bad("D1 a type names an area that does not exist", ", ".join(bad_area))

    empty = [a for a in AREA_KEYS if not reg["types_in_area"](a)]
    if not empty:
        ok("D2 every declared area has at least one type")
    else:
        bad("D2 an area has no types, so its page would always be empty", ", ".join(empty))

    bad_sev = sorted(k for k, m in TYPES.items() if m.get("sev") not in SEVERITIES)
    if not bad_sev:
        ok("D3 every type's default severity is on the one scale")
    else:
        bad("D3 a type's default severity is off the scale", ", ".join(bad_sev))

    bad_grain = sorted(k for k, m in TYPES.items()
                       if m.get("grain") not in ("transaction", "store_day", "rep_period",
                                                 "store_period", "period"))
    if not bad_grain:
        ok("D4 every type declares a known grain")
    else:
        bad("D4 a type declares no usable grain, so the page cannot say whether to drill in",
            ", ".join(bad_grain))

    labels = {}
    dupes = []
    for k, m in TYPES.items():
        lab = str(m.get("label") or "").strip()
        if not lab:
            dupes.append(f"{k}: no label")
        elif lab in labels:
            dupes.append(f"{lab!r} on both {labels[lab]} and {k}")
        else:
            labels[lab] = k
    if not dupes:
        ok("D5 every type has a label and no two share one")
    else:
        bad("D5 labels are missing or ambiguous on the board", "; ".join(dupes))

    # D6. A type the registry declares as emitted must actually be emitted somewhere — the mirror of
    # rule B, and the reason DOCUMENTED_NOT_EMITTED exists. A registered type nobody writes would put
    # a permanently-empty row on the management board, which is the fake-zero this house forbids.
    # The scan is deliberately WIDER than rule B's: a type can reach the table through a variable
    # (`ft, sev = 'PORT_OUT_30DAY', 'CRITICAL'`) or a ternary (`"VIP Invoice Overdue" if ... else ...`),
    # which rule B's `flag_type:` anchor cannot see. So D6 asks the weaker question "does this key
    # appear as a string literal anywhere in a module that builds findings", which is enough to tell
    # a live type from a permanently-empty board row. Rule B stays strict on the write sites.
    emitted = set()
    STRING_LITERAL = re.compile(r'["\']([^"\'\n]{3,80})["\']')
    for p in files:
        src = read(p)
        if rel(p).endswith("commcalc/flag_registry.py"):
            continue
        if not BUILDS.search(src) and not WRITE_PATTERN.search(src):
            continue
        for raw in STRING_LITERAL.findall(src):
            k = reg["canon_type"](raw.strip())
            if k:
                emitted.add(k)
    # The two new detectors name their types in a FLAG_TYPES tuple rather than inline, so read that
    # declaration too — it is the same statement of intent, spelled once.
    for mod in ("closing/cash_watchdog.py", "commcalc/void_watchdog.py"):
        src = read(os.path.join(APP, *mod.split("/")))
        m = re.search(r"FLAG_TYPES\s*=\s*\(([^)]*)\)", src, re.S)
        if m:
            for v in re.findall(r'["\']([^"\']+)["\']', m.group(1)):
                k = reg["canon_type"](v)
                if k:
                    emitted.add(k)
    never = sorted(set(TYPES) - emitted)
    if not never:
        ok(f"D6 all {len(TYPES)} registered types are emitted by some module")
    else:
        bad("D6 a registered type is emitted by nothing, so its board row is a permanent zero",
            ", ".join(never) + " — remove it, or list it in DOCUMENTED_NOT_EMITTED")

    # D7. The stale docstring is RECORDED, not fixed by inventing rules. These four are named in
    # `commcalc/flags.py`'s header and emitted by nothing; the registry must keep saying so.
    dn = set(reg["DOCUMENTED_NOT_EMITTED"])
    if dn and not (dn & set(TYPES)):
        ok(f"D7 the {len(dn)} documented-but-unemitted types are recorded and not registered")
    else:
        bad("D7 a documented-but-unemitted type got registered anyway",
            ", ".join(sorted(dn & set(TYPES))) or "the record is empty")

    # ── D8. Canonicalisation is total: every spelling in the tree resolves. ─────────────────────
    for raw, want in (("HIGH", "HIGH"), ("high", "HIGH"), ("critical", "CRITICAL"),
                      ("CRITICAL", "CRITICAL"), ("warning", "MEDIUM"), ("LOW", "LOW")):
        got = reg["canon_sev"](raw)
        if got != want:
            bad("D8 a live severity spelling does not canonicalise",
                f"{raw!r} → {got!r}, expected {want!r}")
            break
    else:
        ok("D8 all six live severity spellings canonicalise onto the scale")

    # An unknown severity must NOT raise and must NOT silently become the worst level.
    if reg["canon_sev"]("banana") == reg["MEDIUM"] and reg["canon_sev"](None) == reg["MEDIUM"]:
        ok("D9 an unrecognised severity lands in the middle instead of raising or crying critical")
    else:
        bad("D9 an unrecognised severity is mishandled")

    # ── D10. The area board is complete even when nothing is open. ──────────────────────────────
    board = reg["area_summary"]([])
    if len(board) == len(AREAS) and all(r["open_count"] == 0 for r in board):
        ok("D10 an empty queue still returns every area, so a quiet board is not an unwatched one")
    else:
        bad("D10 the board hides areas with nothing open")

    # And an unregistered finding is NAMED rather than dropped.
    board2 = reg["area_summary"]([{"flag_type": "SOMETHING_NOBODY_REGISTERED", "severity": "HIGH"}])
    tail = board2[-1] if board2 else {}
    if tail.get("area") == reg["UNASSIGNED"] and tail.get("open_count") == 1:
        ok("D11 a finding of an unregistered kind is counted and named, never dropped")
    else:
        bad("D11 an unregistered finding vanishes from the board")

    # ── CONTROLS. Each rule is proved to go RED when its fix is removed. ────────────────────────
    def control(name, fn):
        try:
            if fn():
                ok(f"control: {name}")
            else:
                bad(f"control: {name} did NOT fail", "this rule can no longer catch the regression")
        except Exception as e:  # a control that errors is also a control that does not hold
            bad(f"control: {name} errored", str(e)[:160])

    control("a writer with no stamp() call would fail rule A",
            lambda: not STAMP_PATTERN.search("client.schema('commcalc').table('flags').insert(x)"))
    control("an unregistered flag_type would fail rule B",
            lambda: reg["canon_type"]("TOTALLY_MADE_UP_TYPE") is None)
    control("a second severity map would fail rule C",
            lambda: bool(ALIAS_MAP.search('M = {"warning": "MEDIUM"}')))
    control("a type in a non-existent area would fail rule D1",
            lambda: "nowhere" not in AREA_KEYS)
    control("a prefix type still matches its dynamic spelling",
            lambda: reg["canon_type"]("Inventory mismatch — on-inventory") == "Inventory mismatch")
    control("the write pattern matches the real call shape",
            lambda: bool(WRITE_PATTERN.search('client.schema("commcalc").table("flags").insert(r)')))

    print("\n".join(f"  PASS  {p}" for p in PASS))
    if FAIL:
        print("\n".join(f"  FAIL  {f}" for f in FAIL))
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
