"""A cron sweep is public for the METHOD it declares, never for every method on its path.

THE DEFECT (access probe run 328, 2026-11-24, module `recovery`). The 2026-10-28 fix made the sweep
allowlist dereference the route table so a PARAMETERISED path could not be reached by typing
"run-due" into it. It fixed the PATH axis and left the METHOD axis open: a literal sweep path is
declared public method-agnostically, and FastAPI will route a DIFFERENT method on that same path to a
parameterised route that relied on the middleware for its authentication.

Measured on production, anonymous, no Authorization header at all:

    GET /api/v1/storeops/salary-advance/additional-payroll/run-due  ->  500
    (every one of the other 38 sweep paths  ->  405)

The 405s are the middleware standing down and route matching refusing the method. The 500 is
`get_additional_payroll` — a payroll read with no permission gate of its own — actually RUNNING for
an anonymous caller with `period="run-due"`, because `GET /salary-advance/additional-payroll/{period}`
matches that path too. The static sibling is `PATCH /api/v1/commcalc/connectors/{cid}`, an
unauthenticated write handler reachable the same way.

THE CLASS: "which paths are public" and "for which methods" are two questions, and the derived cron
set answered only the first. Same shape as _PUBLIC_METHODS, whose own comment says it exists to stop
"a future GET on the same path being public by inheritance".
"""
import app.core.tenant_middleware as tm


SWEEP = "/api/v1/storeops/salary-advance/additional-payroll/run-due"
CONNECTORS = "/api/v1/commcalc/connectors/run-due"


def _derived():
    """The cron declaration as the app itself makes it, not a copy of it."""
    from app.main import app
    return tm.cron_paths_for_app(app)


def test_the_sweep_itself_is_still_public_for_post_no_lockout():
    """pg_cron must keep reaching every sweep: a 401 here is a silent outage, not a fix."""
    cron = _derived()
    assert tm.public_surface_allows(SWEEP, "POST", cron_paths=cron) is True
    assert tm.public_surface_allows(CONNECTORS, "POST", cron_paths=cron) is True


def test_every_declared_sweep_stays_public_for_post():
    cron = _derived()
    assert len(cron) >= 20, len(cron)
    for path in cron:
        assert tm.public_surface_allows(path, "POST", cron_paths=cron) is True, path


def test_a_sweep_path_is_not_public_for_a_method_it_does_not_declare():
    """The measured defect: GET on a POST-only sweep must authenticate normally."""
    cron = _derived()
    assert tm.public_surface_allows(SWEEP, "GET", cron_paths=cron) is False
    assert tm.public_surface_allows(CONNECTORS, "PATCH", cron_paths=cron) is False


def test_no_sweep_path_is_public_for_any_method_it_does_not_declare():
    """The class, over every sweep the app declares and every other HTTP method."""
    cron = _derived()
    for path in cron:
        declared = {m.upper() for m in cron[path]}
        for method in ("GET", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "POST"):
            if method in declared:
                continue
            assert tm.public_surface_allows(path, method, cron_paths=cron) is False, (path, method)


def test_the_derivation_carries_the_methods_the_route_table_declares():
    cron = _derived()
    assert {m.upper() for m in cron[SWEEP]} == {"POST"}, cron[SWEEP]


def test_fail_safe_an_unreadable_route_table_still_reaches_the_sweeps():
    """An empty or absent derivation means "could not read the route table", never "no sweeps" —
    37 cron jobs must not 401 because a FastAPI internal changed shape."""
    for empty in (None, frozenset(), {}):
        assert tm.public_surface_allows(SWEEP, "POST", cron_paths=empty) is True
        # With no derivation there is no method fact either, so the historical behaviour stands.
        assert tm.public_surface_allows(SWEEP, "GET", cron_paths=empty) is True


def test_the_other_allowlist_entries_are_untouched():
    cron = _derived()
    assert tm.public_surface_allows("/health", "GET", cron_paths=cron) is True
    assert tm.public_surface_allows("/api/v1/core/auth-config", "GET", cron_paths=cron) is True
    assert tm.public_surface_allows("/api/v1/core/auth-config", "PUT", cron_paths=cron) is False
    assert tm.public_surface_allows("/api/v1/core/members", "GET", cron_paths=cron) is False
