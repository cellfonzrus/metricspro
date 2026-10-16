"""WHAT A PORTAL CELL MEANS, AND WHETHER THE PORTAL SENT IT AT ALL — the one home.

Owner report 2026-10-16, verbatim: *"Check why atu information for atu and other metrics is not
pulling up same as in elevate go for boost , need to fix it asap"*.

WHAT WAS WRONG, MEASURED LIVE (house org, `commcalc.raw_dlar_store`, read-only via REST):

    period            pulled        gross_adds  total_upgrades  total_acts   atu (rate)
    September 2026    2026-10-02          1061             494        1555    populated
    October 2026      2026-10-16             0               0           0    populated

Every RATE arrived and every UNIT COUNT landed as zero, in the same rows, on the same pull. A store
cannot attach 66.67% of nothing: those zeros are the portal no longer sending those fields, written
down as a measurement of zero. Nothing failed, because the sweep's own status line only watches the
four columns the registry declares, and because an absent count went through a coercion that returns
`0.0` for a cell that was never there.

THE CLASS, NOT THE INSTANCE. `dlar_sweep._rate` already existed for exactly this reason at the RATE
grain (owner defect 2026-09-26: "no denominator is not a score of zero"). The same fact was never
true of counts, in the same file, two functions apart — so the next shape change landed as data
instead of as an alarm. And the deeper half: the only record of WHICH source field a column comes
from was the `rec.get("…")` literal inside the normalizer, so when a portal RENAMES a field, the
platform cannot say so. It reads the old name, gets nothing, and stores a zero.

So two facts live here, once:

  1. **A cell the source did not send is not a value of zero.** `measured()` is the one parse; `rate()`
     and `count()` are its two named readings and are deliberately identical in behaviour — the
     distinction is what the caller MEANS, not how the text is read. `dlar_vs_platform` already
     speaks this vocabulary on the read side (`reported` / `measured_zero` / `not_reported`, and
     "the figure is None, NEVER 0"); this is the same vocabulary at the landing point, where the
     distinction was being destroyed before any consumer could see it.
  2. **Which source fields a feed consumes is a DECLARATION, so a rename can be named.**
     `field_arrival()` compares the declaration against the records the portal actually returned and
     answers three things: which declared fields did not arrive, which arrived carrying nothing, and
     — only when something is missing — which fields the source IS sending that nobody reads. That
     last list is the renamed field's new name, printed by the feed itself on the first run after a
     rename, instead of a month of wrong numbers.

RULE TWO: no portal, carrier, tenant or report is named in this file. It takes dicts and returns
dicts. PURE: stdlib only, no DB, no network. Proof: `backend/harness_portal_cell.py`.
"""

# A cell whose text means "the source has no measurement here". Kept in one tuple so the rate and
# count readings cannot drift apart in the spellings they accept.
NO_MEASUREMENT = ("n/a", "na", "-", "--", "none")


def measured(v):
    """A source cell → float, or **None when the source reported no measurement at all**.

    `None`, an empty/whitespace cell and the `NO_MEASUREMENT` spellings are ABSENT (None). A cell that
    SAYS '0' is a measured zero and returns `0.0`. Tolerates the formatting a report puts around a
    number: a percent sign, thousands commas, and the `'0% / 71.43%'` two-part cell (the period value
    is the one on the right). Non-numeric text is absent, never zero. PURE."""
    if v is None:
        return None
    if isinstance(v, bool):          # a flag is not a measurement
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("%", "").replace(",", "")
    if not s or s.lower() in NO_MEASUREMENT:
        return None
    if "/" in s:
        s = s.split("/")[-1].strip().replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


def rate(v):
    """A PERCENTAGE cell → float, or None when the source reported no rate.

    NO DENOMINATOR IS NOT A SCORE OF ZERO (owner defect 2026-09-26). A KPI rate is a measurement; an
    absent measurement stored as `0.0` is indistinguishable from a rep who genuinely attached nothing,
    and it fails that rep against a target off a number nobody observed."""
    return measured(v)


def count(v):
    """A UNIT COUNT cell → float, or None when the source reported no count.

    AN UNSENT COUNT IS NOT A SOLD-NOTHING MONTH (owner report 2026-10-16). This is `rate`'s rule at
    the count grain, and the two being different was the whole defect: 28 stores landed October 2026
    with 0 activations, 0 upgrades and 0 total activity beside populated attach rates."""
    return measured(v)


def total(*parts):
    """The sum of the parts the source DID send, or None when it sent none of them. PURE.

    A derived total of absent parts is absent. Summing `None` as zero is how `total_acts` came to
    read a confident `0` for a store that had sold all month."""
    vals = [measured(p) for p in parts]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return sum(vals)


def as_int(v):
    """A measurement bound for an INTEGER column → int, or None. Postgres rejects '0.0' for an int
    column (22P02), and `None` must survive as None rather than becoming a 0."""
    if v is None:
        return None
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def carries(v):
    """Does this cell carry anything at all — text or number? PURE.

    A blank string carries nothing; the string '0' carries a zero. Used by `field_arrival` so a field
    that arrives empty in every record is reported as arriving-but-blank rather than as present."""
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip()) and v.strip().lower() not in NO_MEASUREMENT
    if isinstance(v, bool):
        return True
    if isinstance(v, (int, float)):
        return True
    return bool(str(v).strip())


def field_arrival(records, declared_fields):
    """DID THE SOURCE'S OWN FIELDS ARRIVE, AND WHAT ELSE IS IT SENDING. PURE, stdlib only.

    `declared_fields` is one entry per FIELD, each a spelling or a tuple of alternative spellings the
    reader accepts for that one field. A field with alternatives is satisfied when ANY of them
    arrives — a reader that accepts two spellings is not missing the one it did not get, and reporting
    it would make every healthy run read as a shape change.

    → `{"rows", "declared", "missing", "blank", "consumed", "unconsumed", "shape_changed"}`

      · `missing`     — a declared field NO spelling of which is in any record. The source renamed or
                        dropped it.
      · `blank`       — a declared field that arrives and carries nothing in any record.
      · `consumed`    — a declared field carrying a value in at least one record.
      · `unconsumed`  — fields the source sends that no declaration names, **reported only when
                        something is missing or blank**. A feed ignores fields by design, so listing
                        them on a healthy run would be noise that teaches an operator to ignore the
                        line; listing them beside a field that stopped arriving is the renamed
                        field's new name.
      · `shape_changed` — True when anything is missing or blank: the one boolean a status line asks.

    A missing or blank field is named by its PREFERRED spelling (the first declared), so one field
    reads as one problem however many spellings it accepts.

    With no records nothing is reported: an empty pull is a different failure and the load guards own
    it."""
    recs = [r for r in (records or []) if isinstance(r, dict)]
    groups = []
    for entry in declared_fields or ():
        spellings = ((str(entry),) if isinstance(entry, str)
                     else tuple(str(k) for k in entry))
        if spellings and spellings not in groups:
            groups.append(spellings)
    out = {"rows": len(recs), "declared": [g[0] for g in groups], "missing": [], "blank": [],
           "consumed": [], "unconsumed": [], "shape_changed": False}
    if not recs:
        return out
    seen = {}
    for r in recs:
        for k, v in r.items():
            st = seen.setdefault(str(k), {"present": 0, "filled": 0})
            st["present"] += 1
            if carries(v):
                st["filled"] += 1
    for spellings in groups:
        states = [seen.get(k) for k in spellings]
        arrived = [s for s in states if s is not None]
        if not arrived:
            out["missing"].append(spellings[0])
        elif not any(s["filled"] for s in arrived):
            out["blank"].append(spellings[0])
        else:
            out["consumed"].append(spellings[0])
    out["shape_changed"] = bool(out["missing"] or out["blank"])
    if out["shape_changed"]:
        named = {k for g in groups for k in g}
        out["unconsumed"] = sorted(k for k in seen if k not in named)
    return out
