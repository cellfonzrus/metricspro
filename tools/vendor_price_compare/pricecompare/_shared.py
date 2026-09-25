"""Find and load the ONE copy of the shared logic (index §36) — never a second copy in the kit.

The pricing logic and the catalog reader live in the MetricsPro backend:
    backend/app/modules/supply/pricing_core.py     (pure: prices, packs, stock, matching, comparison)
    backend/app/modules/supply/catalog_scrape.py   (browser: read-only catalog walk)

Run from the repo, the kit loads those files by relative path. Unzipped on the owner's computer, it loads
the copies build_zip.py placed in `_bundled/` next to this file (generated at zip time, git-ignored, never
committed). Nothing here contains pricing logic.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_HOME = HERE.parents[2] / "backend" / "app" / "modules" / "supply"   # repo layout
BUNDLED = HERE / "_bundled"                                              # zip layout (build_zip.py)
SHARED = ("pricing_core", "catalog_scrape")


def source_path(name):
    """Where `name` (pricing_core | catalog_scrape) is loaded from — the repo home first."""
    for d in (REPO_HOME, BUNDLED):
        p = d / f"{name}.py"
        if p.exists():
            return p
    raise ImportError(f"{name}.py not found in {REPO_HOME} or {BUNDLED} — run the kit from the repo or "
                      f"from the zip that build_zip.py makes")


def load(name):
    """Import the shared module once and register it under its own name (catalog_scrape imports
    `pricing_core` by that name when it is not inside the backend package)."""
    if name in sys.modules:
        return sys.modules[name]
    if name == "catalog_scrape":
        load("pricing_core")
    path = source_path(name)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
