import pytest
from pydantic import ValidationError

from app.schemas.auth import RegisterRequest
from app.schemas.users import PreferencesUpdateRequest, ProfileUpdateRequest


def test_email_is_normalised_to_lowercase() -> None:
    assert RegisterRequest(email="Asha@Example.COM", password="password1").email == (
        "asha@example.com"
    )


@pytest.mark.parametrize("password", ["short", " " * 10, "x" * 129])
def test_invalid_passwords_rejected(password: str) -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(email="a@example.com", password=password)


def test_invalid_timezone_rejected() -> None:
    with pytest.raises(ValidationError, match="IANA"):
        RegisterRequest(email="a@example.com", password="password1", timezone="Mars/Olympus")


def test_unknown_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(email="a@example.com", password="password1", is_admin=True)


def test_profile_update_tracks_only_sent_fields() -> None:
    update = ProfileUpdateRequest(display_name=None)
    assert update.model_dump(exclude_unset=True) == {"display_name": None}


def test_profile_update_rejects_null_language() -> None:
    with pytest.raises(ValidationError, match="cannot be null"):
        ProfileUpdateRequest(preferred_language=None)


@pytest.mark.parametrize("speed", [0.1, 4.5])
def test_speech_speed_bounds(speed: float) -> None:
    with pytest.raises(ValidationError):
        PreferencesUpdateRequest(speech_speed=speed)


def test_unknown_personality_rejected() -> None:
    with pytest.raises(ValidationError):
        PreferencesUpdateRequest(personality="pirate")


def test_voice_null_resets_to_default() -> None:
    assert PreferencesUpdateRequest(voice=None).model_dump(exclude_unset=True) == {"voice": None}


def test_voice_name_pattern_enforced() -> None:
    with pytest.raises(ValidationError):
        PreferencesUpdateRequest(voice="Robert'); DROP TABLE")
