#!/usr/bin/env python3
"""Build the downloadable kit: dist/vendor_price_compare.zip.

    python tools/vendor_price_compare/build_zip.py

The kit holds no copy of the pricing logic or the catalog reader — those live ONCE in the backend
(backend/app/modules/supply/pricing_core.py + catalog_scrape.py, index §36). The zip needs them to run on
the owner's computer, so this script copies them into the zip at pricecompare/_bundled/ (the path
pricecompare/_shared.py looks in when the repo is not there). The bundled files exist only inside the zip;
they are never written into the repo tree, so there is still exactly one copy under version control.

Never packs credentials.csv, shopping_list.csv, output/, .venv/ or dist/ (the owner's logins and results).
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from pricecompare import _shared  # noqa: E402

SKIP_DIRS = {"output", ".venv", "dist", "__pycache__", "_bundled"}
SKIP_FILES = {"credentials.csv", "shopping_list.csv"}


def kit_files():
    for p in sorted(HERE.rglob("*")):
        rel = p.relative_to(HERE)
        if p.is_dir() or any(part in SKIP_DIRS for part in rel.parts) or p.name in SKIP_FILES:
            continue
        if p.suffix in (".pyc",):
            continue
        yield p, rel


def build(out=None):
    out = Path(out or HERE / "dist" / "vendor_price_compare.zip")
    out.parent.mkdir(parents=True, exist_ok=True)
    names = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p, rel in kit_files():
            arc = f"vendor_price_compare/{rel.as_posix()}"
            z.write(p, arc)
            names.append(arc)
        for name in _shared.SHARED:
            src = _shared.REPO_HOME / f"{name}.py"
            arc = f"vendor_price_compare/pricecompare/_bundled/{name}.py"
            z.write(src, arc)
            names.append(arc)
    return out, names


if __name__ == "__main__":
    path, files = build(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"wrote {path} ({len(files)} files)")
