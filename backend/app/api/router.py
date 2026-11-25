from fastapi import APIRouter

from app.api.routes import (
    auth,
    chat,
    conversations,
    health,
    memories,
    reminders,
    tasks,
    tools,
    users,
    voice,
)
from app.api.websocket import voice as voice_ws

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(conversations.router)
api_router.include_router(chat.router)
api_router.include_router(voice.router)
api_router.include_router(memories.router)
api_router.include_router(reminders.router)
api_router.include_router(tasks.router)
api_router.include_router(tools.router)
api_router.include_router(voice_ws.router)
