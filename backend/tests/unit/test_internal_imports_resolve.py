"""THE LOCK: every internal `app.*` import in the backend must actually resolve.

WHY THIS FILE EXISTS. On 2026-10-31 the owner opened Productivity Insights for one store and got
"A system error occurred. Reference: 15fd7054". The reference resolved, in `core.failure_log`, to:

    File "/app/app/modules/commcalc/router.py", line 37586, in productivity_insights
        from app.modules.commcalc.staffing_heatmap import hhmm_to_hours
    ModuleNotFoundError: No module named 'app.modules.commcalc.staffing_heatmap'

`staffing_heatmap` lives in `app.modules.storeops`, and always has. The import was written against
the wrong package, and because it sits INSIDE the endpoint function it is not executed at import
time — so the app booted clean, every existing test passed, CI was green, and the defect waited in
the file until a human pressed the button.

THE CLASS, NOT THE INSTANCE (owner directive 2026-09-20). The general fact that was wrong is not
"one import names the wrong package". It is: **a function-local import of our own code is checked by
nothing in this repo.** `backend-integration` only proves the modules that its own tests happen to
reach, and this router holds thousands of lines of endpoints. The fix for the class is a check that
reads EVERY such import statically and fails the build when one cannot resolve.

THE SIBLING IT CAUGHT, in the same sweep that reproduced the reported one:

    app/modules/commcalc/router.py (GET /commcalc/my-shelf)
        from app.modules.commcalc import unsold_inventory as _unsold
    -> `unsold_inventory` is in `app.modules.payables` (every one of its eight other callers says so)

Two endpoints, one defect, and the second had not been reported yet. That is what a lock buys over a
repair: both were fixed in the PR that added this file.

HOW IT WORKS, and why it is static. It never imports anything — it parses. `importlib.util.find_spec`
locates a module without executing it, and the target module's own source is parsed for the names it
defines. That matters because importing `app.modules.*` for real would pull in config, Supabase
clients and route registration, which is exactly the slow, side-effecting work `tests/unit` must not
do. A parse cannot be fooled by a module that happens to be imported already, either.

WHAT IT DELIBERATELY DOES NOT CLAIM. It is a NAME check, not a signature check: it proves the module
and the symbol exist, not that the symbol does the right thing. A module whose names cannot be read
statically (a `from x import *`, a module-level `__getattr__`) has its symbol check skipped rather
than guessed at — the module-resolution check still applies to it. This is a floor under every
import, not a replacement for testing what they return.

Multi-tenant note (project rule 2026-10-30): an import defect is tenant-independent by construction
— `ModuleNotFoundError` fires before any org is read — so this lock needs no per-tenant pass. The
tenant dimension of the two fixed endpoints is proved in
`tests/integration/test_productivity_insights_endpoint.py`.
"""
import ast
import importlib.util
import os

import pytest

#: backend/ — the root of both `app.*` and `tests.*` (conftest puts it on sys.path).
#: This file is backend/tests/unit/<name>.py, so backend/ is three directories up.
_BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_APP = os.path.join(_BACKEND, "app")


def _py_files():
    """Every .py file under backend/app, as (absolute path, repo-relative label)."""
    for root, dirs, files in os.walk(_APP):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in sorted(files):
            if name.endswith(".py"):
                full = os.path.join(root, name)
                yield full, os.path.relpath(full, _BACKEND)


def _parse(path):
    with open(path, encoding="utf-8") as fh:
        return ast.parse(fh.read(), path)


def _internal_imports():
    """Every absolute import of our own code, as (label, lineno, module, [names]).

    Relative imports (`from . import x`) are resolved by the interpreter against the package and
    cannot name a package that does not exist, so only `level == 0` is in scope here.
    """
    found = []
    for full, label in _py_files():
        tree = _parse(full)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module and node.module.startswith("app."):
                    found.append((label, node.lineno, node.module,
                                  [a.name for a in node.names]))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("app."):
                        found.append((label, node.lineno, alias.name, []))
    return found


def _spec_origin(module):
    """The module's source file, or None when the module does not exist. Imports nothing."""
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, AttributeError, ValueError):
        # A parent package that is itself unimportable: the module cannot be reached either.
        return None
    return spec.origin if spec is not None else None


_EXPORTS_CACHE = {}


def _exported_names(module):
    """Names a module defines, parsed from its source. None = "cannot be read statically"."""
    if module in _EXPORTS_CACHE:
        return _EXPORTS_CACHE[module]
    origin = _spec_origin(module)
    if not origin or not origin.endswith(".py"):
        _EXPORTS_CACHE[module] = None          # namespace package, C extension, or missing
        return None
    names, dynamic = set(), False
    for node in ast.walk(_parse(origin)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[0])
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            dynamic = True
    if "__getattr__" in names:
        dynamic = True
    _EXPORTS_CACHE[module] = None if dynamic else names
    return _EXPORTS_CACHE[module]


@pytest.mark.parametrize("fixed", [
    # The two statements this lock was written for, pinned by their fixed form so a revert of
    # either one fails HERE with its own name rather than only in the sweep below.
    ("app/modules/commcalc/router.py", "from app.modules.storeops.staffing_heatmap import hhmm_to_hours"),
    ("app/modules/commcalc/router.py", "from app.modules.payables import unsold_inventory as _unsold"),
])
def test_the_two_reported_imports_name_the_right_package(fixed):
    label, statement = fixed
    with open(os.path.join(_BACKEND, label), encoding="utf-8") as fh:
        source = fh.read()
    assert statement in source, (
        f"{label} no longer contains `{statement}`. If the module moved, move the import with it; "
        f"do not point it back at a package that does not hold it."
    )


def test_every_internal_import_resolves_to_a_real_module():
    """A `from app.… import …` naming a module that does not exist is a 500 waiting for a user."""
    missing = [(label, lineno, module)
               for label, lineno, module, _names in _internal_imports()
               if _spec_origin(module) is None]
    assert not missing, "internal imports naming a module that does not exist:\n" + "\n".join(
        f"  {label}:{lineno}  ->  {module}" for label, lineno, module in missing)


def test_every_internal_import_names_a_symbol_that_exists():
    """`from app.x import y` where module x exists but defines no y — the same crash, one level in."""
    missing = []
    for label, lineno, module, names in _internal_imports():
        exports = _exported_names(module)
        if exports is None:
            continue                                   # not statically readable — module check only
        for name in names:
            if name == "*" or name in exports:
                continue
            if _spec_origin(f"{module}.{name}") is not None:
                continue                               # `from pkg import submodule`
            missing.append((label, lineno, module, name))
    assert not missing, "internal imports naming a symbol that does not exist:\n" + "\n".join(
        f"  {label}:{lineno}  ->  {module}.{name}" for label, lineno, module, name in missing)


def test_the_sweep_actually_sees_the_imports_it_claims_to_check():
    """A guard that silently matched nothing would pass forever. Pin the floor it must be reading.

    Measured 2026-10-31: 2,624 absolute `app.*` import statements under backend/app, of which the
    two above were broken. The floor is deliberately far below that count so ordinary refactoring
    does not touch this line — but a walk that starts returning nothing cannot pass.
    """
    found = _internal_imports()
    assert len(found) > 1500, (
        f"only {len(found)} internal imports found — the walk is broken, not the codebase"
    )
    assert any(label.endswith("commcalc/router.py") for label, _l, _m, _n in found)
