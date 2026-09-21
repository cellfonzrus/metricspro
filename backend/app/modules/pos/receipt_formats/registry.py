"""Format registry — the ONE place that knows the set of POS formats. The upload endpoint asks the
tenant which POS they're uploading from and looks the parser up here; adding a POS = one import + one
row. `default_source` lets a tenant's remembered choice pre-select the picker."""
from __future__ import annotations

from . import b2b, rq

# ordered for the picker
# `module` = the format's own declarations (COLUMNS / TOTALS / TITLE / DATE_FORMAT / FINANCED_ITEMS /
# PRINT_LAYOUT …) — what a document BUILT for that POS (pos/sales_from_reports.py) and the word
# renderer read; a caller never imports a format by name, it asks the registry for the tenant's POS.
_FORMATS = [
    {"source": rq.POS_SOURCE, "label": rq.LABEL, "parse": rq.parse, "module": rq},
    {"source": b2b.POS_SOURCE, "label": b2b.LABEL, "parse": b2b.parse, "module": b2b},
]
_BY_SOURCE = {f["source"]: f for f in _FORMATS}


def list_formats() -> list[dict]:
    """[{source,label}] for the upload picker (no functions)."""
    return [{"source": f["source"], "label": f["label"]} for f in _FORMATS]


def get(source: str):
    """The registered format for a POS key ({source,label,parse,module}) or None — the ONE lookup; a
    tenant's declared POS with no entry here gets a plain sentence from its caller, never a fallback."""
    return _BY_SOURCE.get((source or "").strip().lower())


def sources() -> list[str]:
    return [f["source"] for f in _FORMATS]


def parse(source: str, pages_words) -> dict:
    """Parse with the named format's parser. Raises KeyError if the source is unknown (the caller
    validates against list_formats first)."""
    f = _BY_SOURCE[(source or "").strip().lower()]
    return f["parse"](pages_words)
