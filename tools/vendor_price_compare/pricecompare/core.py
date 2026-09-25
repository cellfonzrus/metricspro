"""The kit's name for the shared pricing logic. It holds NO logic: the one copy is
backend/app/modules/supply/pricing_core.py (see _shared.py). `from pricecompare import core` gets that module."""
import sys

from pricecompare._shared import load

sys.modules[__name__] = load("pricing_core")
