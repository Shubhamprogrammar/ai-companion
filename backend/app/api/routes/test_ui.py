"""Developer test page for the WebSocket voice API (``GET /voice-test``).

A single static HTML file with plain JavaScript and no build step. It logs in, opens
``/api/v1/ws/voice``, streams the microphone as pcm16 and plays Mira's streamed reply.
It is a testing tool, not a product frontend: only mounted outside production, and only
when ``TEST_UI_ENABLED`` is true.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(include_in_schema=False)

_PAGE = Path(__file__).resolve().parents[2] / "static" / "voice-test.html"


@router.get("/voice-test")
async def voice_test_page() -> FileResponse:
    return FileResponse(_PAGE, media_type="text/html; charset=utf-8")
