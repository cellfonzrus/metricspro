"""The kit's name for the shared read-only catalog reader. It holds NO logic: the one copy is
backend/app/modules/supply/catalog_scrape.py (see _shared.py). `from pricecompare import scrape` gets that module."""
import sys

from pricecompare._shared import load

sys.modules[__name__] = load("catalog_scrape")
