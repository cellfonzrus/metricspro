"""Shared request-model base for typed, strictly-validated endpoints (Security Controls Spec §4,
item 15). Rolled out INCREMENTALLY — see the roadmap. Endpoints migrated from `body: dict` to a
`StrictModel` subclass get: type coercion/validation and, because `extra='forbid'`, rejection of any
undeclared field (a 422), which is the "strict schema validation / drop unexpected fields" control from
the External Threat Defense Plan §3.1.

Only apply StrictModel where BOTH ends are known (endpoints whose only callers are this app's own
frontend), so a legacy client sending an extra field never gets a surprise 422. For broader/legacy
endpoints, migrate deliberately and consider `extra='ignore'` first.
"""
from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Reject unknown fields (422) and validate declared ones."""
    model_config = ConfigDict(extra="forbid")


class LaxModel(BaseModel):
    """Validate declared fields but IGNORE unknown ones — for migrating a legacy endpoint whose callers
    may still send extra keys, without breaking them."""
    model_config = ConfigDict(extra="ignore")
