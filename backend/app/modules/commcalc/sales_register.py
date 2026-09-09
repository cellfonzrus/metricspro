"""THE ONE register predicate — "which POS register was this sale rung on, and is it an event one?"

WHY THIS FILE EXISTS (CLAUDE.md duplicate-check build gate, 2026-09-09)
──────────────────────────────────────────────────────────────────────
The platform already had exactly one place that asked "is this an event-register sale":
`commcalc/flags.py`'s RSK_ACTIVATIONS flag, written inline as

    str(r.get('register', '') or '').strip().upper() == 'RSK'

The "Sales from Events" reports (§23s) ask the SAME question of the SAME `raw_sales` rows. Writing a
second copy of that expression is exactly the drift the build gate exists to prevent — the day
somebody adds a second register value to one of them, the flag and the report disagree about what an
event sale is. So the expression was EXTRACTED here and `flags.py` now calls it. Behaviour is
byte-identical (`harness_marketing_event_sales.py` §A pins the flag's own predicate against this
module on the live RSK shape).

WHAT IS A REGISTER, AND WHY NOT THE TENDER
──────────────────────────────────────────
The owner's directive says "the rsk events tender". In the live data RSK is NOT a tender: it is the
value of `raw_sales.register`, and `tender_type` on those very same rows reads Cash / Credit Card /
Debit Card / Externel Credit Card. Reading the tender would answer a different question (how the
customer paid) and would match nothing. The register is the reading, and it is the one `flags.py`
already used.

RULE TWO — the VALUE is config, this module holds only the house default
────────────────────────────────────────────────────────────────────────
`HOUSE_EVENT_REGISTERS` is the HOUSE DEFAULT, preserved verbatim from the flag that predates this
module so nothing changes for an org that configures nothing. It is not a branch: no code here (or
anywhere downstream) tests for a particular value — the value arrives as data. The owner's "the
others not sure yet but provision will be made" is served by a per-org config row
(`core.marketing_config.event_sales_registers`, migration 995), so another carrier's event register
is added by an INSERT, never a deploy.

PURE. No I/O, no clock, no DB. Proof: `backend/harness_marketing_event_sales.py` §A.
"""

#: The HOUSE default register set. One entry today, the value `flags.py` has always used. A tenant
#: row overrides it; nothing in code compares against a literal.
HOUSE_EVENT_REGISTERS = ("RSK",)


def normalize_register(value) -> str:
    """The ONE normalization of a POS register value: trimmed, upper-cased, never None.

    Byte-identical to the expression `flags.py` used inline — `.strip().upper()` over a
    None-tolerant string cast — so folding the flag onto this function cannot move a flag.
    """
    return str(value or "").strip().upper()


def normalize_registers(values) -> tuple:
    """A configured register list → the normalized, de-duplicated, ORDER-PRESERVING tuple.

    Order is preserved so a payload can echo the org's own list back in the order they typed it;
    blanks are dropped because a blank register would match every row with no register at all.
    """
    out = []
    for v in (values or []):
        n = normalize_register(v)
        if n and n not in out:
            out.append(n)
    return tuple(out)


def register_of(row) -> str:
    """The normalized register on a `raw_sales` / `daily_sales_feed` row (`''` when absent)."""
    if not isinstance(row, dict):
        return ""
    return normalize_register(row.get("register"))


def is_event_register(row, registers=HOUSE_EVENT_REGISTERS) -> bool:
    """Is this sale row rung on one of the configured event registers?

    An EMPTY configured list matches NOTHING, deliberately. The alternative ("empty means all") would
    turn an org that cleared its config into an org whose every sale is an event sale — a silent,
    flattering wrong answer. An org with no event register configured has no event sales, and the
    report says so in as many words rather than showing it the whole store.
    """
    reg = register_of(row)
    if not reg:
        return False
    return reg in normalize_registers(registers)


def filter_by_register(rows, registers=HOUSE_EVENT_REGISTERS) -> list:
    """The rows rung on the configured event register(s). The normalization runs ONCE for the whole
    call rather than per row, so a 200k-row period does not re-normalize the config 200k times."""
    wanted = normalize_registers(registers)
    if not wanted:
        return []
    return [r for r in (rows or []) if register_of(r) in wanted]


def registers_present(rows) -> dict:
    """{register: row count} over whatever rows were read — INCLUDING the blank register as `''`.

    This is what lets a report that found nothing say WHY: "your event register is X, and the rows
    for this period carry only Y and Z" is actionable; an empty table is not.
    """
    out = {}
    for r in (rows or []):
        k = register_of(r)
        out[k] = out.get(k, 0) + 1
    return out
