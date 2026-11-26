"""THE ONBOARDING STAGE VOCABULARY — one home, reachable without the framework (index §STOREOPS-14).

The six stages a new hire's onboarding passes through have been right since migration 073. They
lived inside `hr/router.py`, which is a FastAPI module: a pure reader — a proof harness, or the
employee-setup flow that ends at this very pack — could not dereference them without importing the
whole web framework, and the only remaining way to use them was to type the six words again.

That is the copy this module exists to prevent. `hr/router` keeps its own names and dereferences
these; nothing else may spell a stage word.

PURE: no I/O, no framework import, no tenant's words — these six are the platform's own stages.
"""
from __future__ import annotations

#: The stages, in the order onboarding moves through them.
WORKFLOW_STATUSES = ["invited", "in_progress", "docs_submitted", "docs_verified",
                     "provisioned", "active"]

#: Where each stage sits in that order — so "moving backwards" is an answerable question.
STATUS_ORDER = {s: i for i, s in enumerate(WORKFLOW_STATUSES)}

#: What a person reads for each stage. No table, schema or hosting name may appear (index §19.38).
STATUS_LABELS = {
    "invited": "Invited", "in_progress": "In progress", "docs_submitted": "Docs submitted",
    "docs_verified": "Docs verified", "provisioned": "Provisioned", "active": "Active",
}

#: The stages that mean HR has finished its part of the pack.
SETTLED_STATUSES = ("provisioned", "active")

#: Where a profile that exists but has never moved sits.
FIRST_STATUS = WORKFLOW_STATUSES[0]


def is_stage(value) -> bool:
    """True when `value` is one of the six. An unknown word is REPORTED, never coerced."""
    return str(value or "").strip() in STATUS_ORDER


def is_settled(value) -> bool:
    return str(value or "").strip() in SETTLED_STATUSES


def label(value) -> str:
    """The person-facing word, or the raw value when it is not a stage this platform knows."""
    key = str(value or "").strip()
    return STATUS_LABELS.get(key, key)
