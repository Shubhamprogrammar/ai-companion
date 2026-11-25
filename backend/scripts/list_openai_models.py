"""List models your OPENAI_API_KEY can access, grouped by purpose (free API call).

python -m scripts.list_openai_models
"""

import asyncio
import re

from app.ai.client import build_openai_client
from app.core.config import get_settings

GROUPS = {
    "chat (OPENAI_CHAT_MODEL)": r"^(gpt-|o\d)(?!.*(transcribe|tts|realtime|audio|image|search))",
    "speech-to-text (OPENAI_STT_MODEL)": r"transcribe|whisper",
    "text-to-speech (OPENAI_TTS_MODEL)": r"tts",
    "embeddings (OPENAI_EMBEDDING_MODEL)": r"embedding",
}


async def main() -> None:
    client = build_openai_client(get_settings())
    if client is None:
        raise SystemExit("OPENAI_API_KEY is not set in .env")
    try:
        ids = sorted([m.id async for m in await client.models.list()])
    finally:
        await client.close()
    for label, pattern in GROUPS.items():
        print(f"\n{label}:")
        for model_id in (i for i in ids if re.search(pattern, i)):
            print(f"  {model_id}")


if __name__ == "__main__":
    asyncio.run(main())
