"""Format registry — the ONE place that knows the set of POS formats. The upload endpoint asks the
tenant which POS they're uploading from and looks the parser up here; adding a POS = one import + one
row. `default_source` lets a tenant's remembered choice pre-select the picker."""
from __future__ import annotations

from . import b2b, rq

# ordered for the picker
_FORMATS = [
    {"source": rq.POS_SOURCE, "label": rq.LABEL, "parse": rq.parse},
    {"source": b2b.POS_SOURCE, "label": b2b.LABEL, "parse": b2b.parse},
]
_BY_SOURCE = {f["source"]: f for f in _FORMATS}


def list_formats() -> list[dict]:
    """[{source,label}] for the upload picker (no functions)."""
    return [{"source": f["source"], "label": f["label"]} for f in _FORMATS]


def get(source: str):
    return _BY_SOURCE.get((source or "").strip().lower())


def parse(source: str, pages_words) -> dict:
    """Parse with the named format's parser. Raises KeyError if the source is unknown (the caller
    validates against list_formats first)."""
    f = _BY_SOURCE[(source or "").strip().lower()]
    return f["parse"](pages_words)
