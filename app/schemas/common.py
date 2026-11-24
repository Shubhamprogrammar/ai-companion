from pydantic import BaseModel, ConfigDict


class ORMResponse(BaseModel):
    """Base for responses built from ORM objects (explicit fields only, never the model)."""

    model_config = ConfigDict(from_attributes=True)


class StrictRequest(BaseModel):
    """Base for request bodies: unknown fields are rejected, strings trimmed."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
