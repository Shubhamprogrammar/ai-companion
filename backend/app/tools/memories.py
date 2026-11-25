import uuid
from typing import Annotated, Any

from pydantic import Field

from app.memory.embeddings import EmbeddingProvider
from app.services.memories import MemoryService
from app.tools.base import Tool, ToolArgs, ToolContext


class FindMemories(Tool["FindMemories.Args"]):
    name = "find_memories"
    description = (
        "Search what you remember about the user (e.g. when they ask 'what do you know about "
        "X' or want you to forget something). Returns memory_ids for delete_memory."
    )

    class Args(ToolArgs):
        query: Annotated[str, Field(min_length=1, max_length=500)]

    def __init__(self, embeddings: EmbeddingProvider) -> None:
        self._embeddings = embeddings

    async def run(self, ctx: ToolContext, args: Args) -> dict[str, Any]:
        results = await MemoryService(ctx.session, self._embeddings, ctx.settings).search(
            ctx.user, args.query, limit=5
        )
        return {
            "memories": [
                {"memory_id": str(m.id), "content": m.content, "similarity": round(1 - d, 2)}
                for m, d in results
            ]
        }


class DeleteMemory(Tool["DeleteMemory.Args"]):
    name = "delete_memory"
    description = (
        "Forget one specific memory when the user asks you to. Find its memory_id with "
        "find_memories first. Needs the user's confirmation."
    )
    confirmation = "always"

    class Args(ToolArgs):
        memory_id: uuid.UUID

    def __init__(self, embeddings: EmbeddingProvider) -> None:
        self._embeddings = embeddings

    def _service(self, ctx: ToolContext) -> MemoryService:
        return MemoryService(ctx.session, self._embeddings, ctx.settings)

    async def confirmation_summary(self, ctx: ToolContext, args: Args) -> str:
        memory = await self._service(ctx).get(ctx.user, args.memory_id)
        return f'Forget: "{memory.content}"'

    async def run(self, ctx: ToolContext, args: Args) -> dict[str, Any]:
        await self._service(ctx).delete(ctx.user, args.memory_id)
        return {"forgotten": True}
