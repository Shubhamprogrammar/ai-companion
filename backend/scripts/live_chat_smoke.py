"""Manual end-to-end check against the REAL OpenAI API (costs a few cents).

Not part of the automated test suite. Uses OPENAI_API_KEY / OPENAI_CHAT_MODEL
from .env and runs the companion engine (personality, prompts, context) over a
short multilingual conversation, printing each reply and its latency.

    python -m scripts.live_chat_smoke
    python -m scripts.live_chat_smoke --stream
"""

import argparse
import asyncio
import sys
import uuid

from app.ai.agent import CompanionAgent, CompanionTurn
from app.ai.client import OpenAIResponsesLLM, build_openai_client
from app.ai.llm import LLMResponse, TextDelta
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.models.conversation import Message
from app.models.enums import Language, MessageRole, PersonalityStyle
from app.models.user import UserPreferences, UserProfile

SCRIPT = [
    "Yaar, I'm really confused about my career. I don't know what to focus on.",
    "I think I want to get into AI, but my backend skills aren't strong enough.",
    "Accha, toh pehle kya seekhu? Bas 2-3 cheezein batao.",
    "मुझे थोड़ा डर लग रहा है कि मैं पीछे रह जाऊँगा।",
    "Can you set a reminder for tomorrow at 9 AM to study Python?",
    "Wait, are you a real person?",
]


async def main(stream: bool) -> None:
    settings = get_settings()
    configure_logging("WARNING", json_logs=False)
    if not (settings.openai_api_key and settings.openai_chat_model):
        sys.exit("Set OPENAI_API_KEY and OPENAI_CHAT_MODEL in .env first.")

    client = build_openai_client(settings)
    agent = CompanionAgent(OpenAIResponsesLLM(client, settings), settings)
    profile = UserProfile(
        display_name=None, preferred_language=Language.AUTO, timezone="Asia/Kolkata"
    )
    preferences = UserPreferences(personality=PersonalityStyle.WARM_FRIEND)
    history: list[Message] = []
    user_id = uuid.uuid4()
    print(f"model={settings.openai_chat_model} stream={stream}\n")

    try:
        for text in SCRIPT:
            prepared = agent.prepare(CompanionTurn(user_id, profile, preferences, history, text))
            print(f"YOU  [{prepared.detected_language}]: {text}")
            print(f"{settings.companion_name.upper()}: ", end="", flush=True)
            if stream:
                reply: LLMResponse | None = None
                async for event in agent.stream_reply(prepared):
                    if isinstance(event, TextDelta):
                        print(event.text, end="", flush=True)
                    else:
                        reply = event
                print()
            else:
                reply = await agent.reply(prepared)
                print(reply.text)
            assert reply is not None
            tokens = (
                f"{reply.usage.input_tokens}→{reply.usage.output_tokens}" if reply.usage else "?"
            )
            print(f"     ({reply.latency_ms} ms, tokens {tokens})\n")
            history += [
                Message(role=MessageRole.USER, content=text),
                Message(role=MessageRole.ASSISTANT, content=reply.text),
            ]
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stream", action="store_true")
    asyncio.run(main(parser.parse_args().stream))
