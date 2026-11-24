from typing import Annotated, Any, Literal

from pydantic import Field

from app.models.enums import Language
from app.schemas.users import PreferencesUpdateRequest, ProfileUpdateRequest
from app.services.users import UserService
from app.tools.base import Tool, ToolArgs, ToolContext


class GetPreferences(Tool["GetPreferences.Args"]):
    name = "get_preferences"
    description = "Read the user's app settings: voice, speech speed, language, memory on/off."

    class Args(ToolArgs):
        pass

    async def run(self, ctx: ToolContext, args: Args) -> dict[str, Any]:
        service = UserService(ctx.session, ctx.settings)
        prefs = await service.get_preferences(ctx.user)
        profile = await service.get_profile(ctx.user)
        return {
            "voice": prefs.voice or ctx.settings.openai_tts_voice,
            "available_voices": list(ctx.settings.openai_tts_voices),
            "speech_speed": prefs.speech_speed,
            "memory_enabled": prefs.memory_enabled,
            "preferred_language": profile.preferred_language.value,
            "timezone": profile.timezone,
        }


class UpdatePreferences(Tool["UpdatePreferences.Args"]):
    name = "update_preferences"
    description = (
        "Change the user's settings when they ask: voice, speech speed, preferred language, or "
        "long-term memory on/off. Pass null for anything unchanged. Turning memory off needs "
        "the user's confirmation."
    )
    confirmation = "conditional"

    class Args(ToolArgs):
        voice: str | None = Field(description="One of the available voices (see get_preferences)")
        speech_speed: Annotated[float | None, Field(ge=0.25, le=4.0)] = Field(
            description="0.25 to 4.0; 1.0 is normal"
        )
        preferred_language: Literal["auto", "en", "hi", "hinglish"] | None
        memory_enabled: bool | None

    def needs_confirmation(self, args: Args) -> bool:
        return args.memory_enabled is False

    async def confirmation_summary(self, ctx: ToolContext, args: Args) -> str:
        return (
            "Turn off long-term memory (I'll stop remembering new things "
            "and stop recalling old ones)"
        )

    async def run(self, ctx: ToolContext, args: Args) -> dict[str, Any]:
        service = UserService(ctx.session, ctx.settings)
        pref_changes = {
            k: v
            for k, v in {
                "voice": args.voice,
                "speech_speed": args.speech_speed,
                "memory_enabled": args.memory_enabled,
            }.items()
            if v is not None
        }
        if pref_changes:
            await service.update_preferences(ctx.user, PreferencesUpdateRequest(**pref_changes))
        if args.preferred_language:
            await service.update_profile(
                ctx.user, ProfileUpdateRequest(preferred_language=Language(args.preferred_language))
            )
        return {
            "updated": [*pref_changes, *(["preferred_language"] if args.preferred_language else [])]
        }
