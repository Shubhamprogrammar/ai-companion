from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.api.dependencies import ChatServiceDep, CurrentUserDep, user_rate_limit
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ChatStreamDelta,
    ChatStreamError,
    ChatStreamStart,
    ChatStreamToolStarted,
)
from app.schemas.conversations import ConversationResponse, MessageResponse
from app.schemas.tools import ToolActionResponse
from app.services.chat import (
    ChatStreamEvent,
    ChatTurnResult,
    StreamCompleted,
    StreamDelta,
    StreamFailed,
    StreamStarted,
    StreamToolFinished,
    StreamToolStarted,
)

router = APIRouter(prefix="/chat", tags=["chat"])


def _chat_response(result: ChatTurnResult) -> ChatResponse:
    return ChatResponse(
        conversation=ConversationResponse.model_validate(result.conversation),
        user_message=MessageResponse.model_validate(result.user_message),
        assistant_message=MessageResponse.model_validate(result.assistant_message),
        actions=[ToolActionResponse.from_activity(a) for a in result.actions],
    )


@router.post(
    "",
    response_model=ChatResponse,
    dependencies=[Depends(user_rate_limit("chat"))],
    responses={503: {"description": "The AI provider is unavailable"}},
)
async def chat(body: ChatRequest, user: CurrentUserDep, chat: ChatServiceDep) -> ChatResponse:
    """Send a message and get the companion's reply. Omit `conversation_id` to start a new one."""
    result = await chat.send(user, body.message, body.conversation_id)
    return _chat_response(result)


async def _open_stream(
    body: ChatRequest, user: CurrentUserDep, chat: ChatServiceDep
) -> AsyncIterator[ChatStreamEvent]:
    # Runs as a dependency, i.e. *before* the SSE response starts, so problems such as an
    # unknown conversation still produce a normal HTTP error instead of a broken stream.
    return await chat.stream(user, body.message, body.conversation_id)


@router.post(
    "/stream",
    response_class=EventSourceResponse,
    dependencies=[Depends(user_rate_limit("chat"))],
)
async def chat_stream(
    body: ChatRequest,
    events: Annotated[AsyncIterator[ChatStreamEvent], Depends(_open_stream)],
) -> AsyncIterator[ServerSentEvent]:
    """Server-Sent Events stream of the reply.

    Events: `start` {conversation_id} → `delta` {text} / `tool_started` {tool} /
    `tool` {action}* → `done` (same body as POST /chat) or `error` {code, message}.
    Messages are saved only when `done` is sent; executed actions are saved as they run.
    """
    async for event in events:
        match event:
            case StreamStarted(conversation_id=cid):
                yield ServerSentEvent(event="start", data=ChatStreamStart(conversation_id=cid))
            case StreamDelta(text=text):
                yield ServerSentEvent(event="delta", data=ChatStreamDelta(text=text))
            case StreamToolStarted(tool=tool):
                yield ServerSentEvent(event="tool_started", data=ChatStreamToolStarted(tool=tool))
            case StreamToolFinished(activity=activity):
                yield ServerSentEvent(event="tool", data=ToolActionResponse.from_activity(activity))
            case StreamCompleted(result=result):
                yield ServerSentEvent(event="done", data=_chat_response(result))
            case StreamFailed(code=code, message=message):
                yield ServerSentEvent(
                    event="error", data=ChatStreamError(code=code, message=message)
                )
