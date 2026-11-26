"""WHERE A DECLARED REPORT'S ROWS LAND — the one home for that fact (index §COMMCALC-85).

Found 2026-11-26, with 27 per-store back-office logins freshly imported (§COMMCALC-83) and not one
of them able to accept a file.

THE DEFECT, STATED AS A CLASS. A report's `target` — which landing its rows go to — is read in
exactly two places (`processor_backoffice_sweep.ingest_report`, and the download console's
`arrival_type`), and it could be DECLARED in neither. The editor that owns a declaration's round
trip, `report_declaration`, did not carry the field at all: `row()` never surfaced it and
`columns()` never wrote it. So the only writer of a declaration silently dropped the one fact
without which the pull refuses the rows and the console offers no upload door. Measured in
production: of every declared report on the platform — 15 on the Team Portal login, 0 on the 27 new
ones — **not one carries a target**. A fact every caller needs and no writer can write is the same
defect as a fact copied twice, wearing the opposite hat.

WHICH TARGETS ARE LEGAL IS THE CONNECTOR'S OWN ANSWER, not a list spelled here (RULE TWO, and the
2026-10-30 rule that a vocabulary is never defaulted to one party's words). Each connector module
already owns `TARGETS` — the map from a declared target to the function that lands it — and this
registry DEREFERENCES that map rather than copying its keys. A connector that lands nothing declares
no target, and the editor then offers no field, which is honest: there is nowhere for its rows to go
until someone writes a landing for it.

WHY A REGISTRY AND NOT AN IMPORT AT EACH CALLER. `report_declaration` is pure and must not import a
sweep (the sweeps import the pull, which imports the feed); and a caller that reached for one
connector's `TARGETS` directly would be the second copy this module exists to prevent. So the fact
"connector X lands these targets" lives here once, keyed by the connector's own `CONNECTOR` slug,
and is resolved lazily so importing this module costs nothing.

THE REFUSAL. `clean_target` answers "" for anything the named connector does not land — it never
passes a value through on the grounds that it looks plausible. A target stored that no `TARGETS`
knows is refused by name at ingest time, hours later, by a loop nobody is watching; refused here it
is refused on the screen where someone can fix it.
"""

#: connector slug → a callable giving that connector's own landing targets. LAZY and DERIVED: the
#: value is read from the connector module's `TARGETS` at call time, so a landing added there is
#: offered here with no edit, and a landing removed there stops being offerable.
_TARGETS = {}


def _register(slug, fn):
    _TARGETS[str(slug or "").strip().lower()] = fn
    return fn


def _processor_backoffice():
    from app.modules.commcalc import processor_backoffice_sweep as _pbs
    return tuple(sorted(_pbs.TARGETS))


def _registry():
    """The registry, built on first use. One entry per connector that lands something.

    The slug is read from the connector's own `CONNECTOR` constant rather than spelled, so a
    connector renamed in one place cannot be addressed by a stale name here.
    """
    if not _TARGETS:
        from app.modules.commcalc import processor_backoffice_sweep as _pbs
        _register(_pbs.CONNECTOR, _processor_backoffice)
    return _TARGETS


def connectors():
    """Every connector slug that declares at least one landing. Sorted, for a stable answer."""
    return tuple(sorted(_registry()))


def targets_for(processor):
    """The landing targets the named connector knows, or `()` when it lands nothing.

    Never raises: a connector module that cannot be imported lands nothing as far as a screen is
    concerned, which shows as "no target can be declared yet" rather than as a 500.
    """
    fn = _registry().get(str(processor or "").strip().lower())
    if fn is None:
        return ()
    try:
        return tuple(str(t).strip().lower() for t in (fn() or ()) if str(t).strip())
    except Exception:
        return ()


def lands_anything(processor):
    """Whether this connector can land a report at all — what a screen asks before offering a field."""
    return bool(targets_for(processor))


def clean_target(processor, value):
    """The declared target, or "" — the one cleaner, for every writer of a declaration.

    "" means NOTHING DECLARED, which every reader already treats as "do not store these rows and say
    so". A value this connector does not land is refused here rather than stored to fail later.
    """
    v = str(value or "").strip().lower()
    return v if v and v in targets_for(processor) else ""


def target_problem(processor, value):
    """The one sentence an operator can act on, or "" when there is nothing wrong. Advice, not a
    refusal — a half-declared report is still saved, and §COMMCALC-37 already refuses to EXPORT one
    whose declaration is incomplete."""
    v = str(value or "").strip().lower()
    known = targets_for(processor)
    if not known:
        # ADVICE MUST BE ACTIONABLE. A connector that lands nothing is not a fault in this report's
        # declaration and there is nothing an operator can pick, so this says nothing about the row.
        # That the connector lands nothing is a fact about the CONNECTOR: the editor reads it from an
        # empty `landing_targets` and says it once, rather than on fifteen rows nobody can fix.
        return ""
    if not v:
        return ("No landing is declared, so this report's rows will not be stored and an uploaded "
                "file has nowhere to go. Choose where they land: %s." % ", ".join(known))
    if v not in known:
        return ("This connector does not land “%s”. It lands: %s."
                % (v, ", ".join(known) or "nothing yet"))
    return ""
