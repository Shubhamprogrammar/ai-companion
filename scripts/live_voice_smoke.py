"""Manual voice round-trip against the REAL OpenAI API (costs a few cents).

For each sample utterance:
  1. TTS speaks it in a "user" voice           -> <out>/NN-user.mp3
  2. STT transcribes that audio back           (round-trip accuracy check)
  3. The companion replies in voice mode
  4. TTS speaks the reply in the companion voice -> <out>/NN-mira.mp3

Requires OPENAI_API_KEY, OPENAI_CHAT_MODEL, OPENAI_STT_MODEL, OPENAI_TTS_MODEL, OPENAI_TTS_VOICE.

    python -m scripts.live_voice_smoke [--out voice_smoke_output] [--user-voice ash]
"""

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path

from app.ai.agent import CompanionAgent, CompanionTurn
from app.ai.client import OpenAIResponsesLLM, build_openai_client
from app.ai.personality import get_personality
from app.audio.audio_utils import prepare_text_for_speech, validate_audio
from app.audio.stt import OpenAISpeechToText
from app.audio.tts import OpenAITextToSpeech, SpeechRequest
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.models.conversation import Message
from app.models.enums import Language, MessageRole, PersonalityStyle
from app.models.user import UserPreferences, UserProfile

SAMPLES = [
    "Yaar, aaj office mein bahut thak gaya. Boss ne phir se deadline aage kar di.",
    "I'm thinking of learning AI this year, but I don't know where to start.",
    "मुझे लगता है मैं बहुत धीरे सीख रहा हूँ।",
]


async def main(out: Path, user_voice: str) -> None:
    settings = get_settings()
    configure_logging("WARNING", json_logs=False)
    required = (
        "openai_api_key",
        "openai_chat_model",
        "openai_stt_model",
        "openai_tts_model",
        "openai_tts_voice",
    )
    missing = [k.upper() for k in required if not getattr(settings, k)]
    if missing:
        sys.exit(f"Set {', '.join(missing)} in .env first.")

    out.mkdir(parents=True, exist_ok=True)
    client = build_openai_client(settings)
    stt = OpenAISpeechToText(client, settings)
    tts = OpenAITextToSpeech(client, settings)
    agent = CompanionAgent(OpenAIResponsesLLM(client, settings), settings)
    personality = get_personality(PersonalityStyle.WARM_FRIEND)
    profile = UserProfile(preferred_language=Language.AUTO, timezone="Asia/Kolkata")
    preferences = UserPreferences(personality=PersonalityStyle.WARM_FRIEND)
    history: list[Message] = []
    print(
        f"chat={settings.openai_chat_model} stt={settings.openai_stt_model} "
        f"tts={settings.openai_tts_model} voice={settings.openai_tts_voice}\n"
    )

    try:
        for i, sample in enumerate(SAMPLES, 1):
            user_audio = await tts.synthesize(SpeechRequest(text=sample, voice=user_voice))
            (out / f"{i:02d}-user.mp3").write_bytes(user_audio.audio)

            start = time.perf_counter()
            transcript = await stt.transcribe(validate_audio(user_audio.audio, 25 * 1024 * 1024))
            prepared = agent.prepare(
                CompanionTurn(
                    uuid.uuid4(), profile, preferences, history, transcript.text, voice_mode=True
                )
            )
            reply = await agent.reply(prepared)
            speech = await tts.synthesize(
                SpeechRequest(
                    text=prepare_text_for_speech(reply.text),
                    voice=settings.openai_tts_voice,
                    instructions=personality.voice_instructions,
                )
            )
            total = round((time.perf_counter() - start) * 1000)
            (out / f"{i:02d}-mira.mp3").write_bytes(speech.audio)

            print(f"SAID:       {sample}")
            print(f"HEARD [{prepared.detected_language}]: {transcript.text}")
            print(f"MIRA:       {reply.text}")
            print(
                f"  stt {transcript.latency_ms} ms | llm {reply.latency_ms} ms | "
                f"tts {speech.latency_ms} ms | turn total {total} ms | "
                f"{len(speech.audio) // 1024} KB audio\n"
            )
            history += [
                Message(role=MessageRole.USER, content=transcript.text),
                Message(role=MessageRole.ASSISTANT, content=reply.text),
            ]
    finally:
        await client.close()
    print(f"Audio written to {out.resolve()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("voice_smoke_output"))
    parser.add_argument("--user-voice", default="ash")
    args = parser.parse_args()
    asyncio.run(main(args.out, args.user_voice))
