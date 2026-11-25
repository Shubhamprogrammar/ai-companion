import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.memory.extraction import MAX_MEMORY_CHARS
from app.models.enums import MemoryCategory, MemorySource
from app.schemas.common import ORMResponse, StrictRequest

MemoryContent = Annotated[str, StringConstraints(min_length=3, max_length=MAX_MEMORY_CHARS)]
Importance = Annotated[int, Field(ge=1, le=5)]


class MemoryCreateRequest(StrictRequest):
    content: MemoryContent
    category: MemoryCategory = MemoryCategory.OTHER
    importance: Importance = 4  # things users add themselves are usually important


class MemoryUpdateRequest(StrictRequest):
    content: MemoryContent | None = None
    category: MemoryCategory | None = None
    importance: Importance | None = None

    @model_validator(mode="after")
    def _no_nulls(self) -> Self:
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class MemorySearchRequest(StrictRequest):
    query: Annotated[str, StringConstraints(min_length=1, max_length=1000)]
    limit: Annotated[int, Field(ge=1, le=20)] = 5


class MemoryResponse(ORMResponse):
    id: uuid.UUID
    content: str
    category: MemoryCategory
    importance: int
    source: MemorySource
    source_conversation_id: uuid.UUID | None
    last_used_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MemoryPage(BaseModel):
    items: list[MemoryResponse]
    limit: int
    offset: int
    has_more: bool


class MemorySearchResult(BaseModel):
    memory: MemoryResponse
    similarity: float = Field(description="1 = identical meaning, 0 = unrelated")


class MemorySearchResponse(BaseModel):
    results: list[MemorySearchResult]


class MemoryDeleteAllResponse(BaseModel):
    deleted: int
