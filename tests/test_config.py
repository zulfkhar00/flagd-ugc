import json

import pytest

from flagd_ugc import config
from flagd_ugc.config import Settings
from flagd_ugc.errors import ConfigurationError


def test_default_text_model_uses_current_gemini_flash(monkeypatch):
    monkeypatch.delenv("GEMINI_TEXT_MODEL", raising=False)

    assert Settings.from_env().gemini_text_model == "gemini-3.6-flash"


def test_default_schedule_uses_california_time(monkeypatch):
    monkeypatch.delenv("TIMEZONE", raising=False)
    monkeypatch.delenv("RUN_TIME", raising=False)

    settings = Settings.from_env()

    assert settings.timezone == "America/Los_Angeles"
    assert settings.run_time == "08:00"


def test_retired_text_model_setting_is_migrated(monkeypatch):
    monkeypatch.setenv("GEMINI_TEXT_MODEL", "gemini-2.5-flash")

    assert Settings.from_env().gemini_text_model == "gemini-3.6-flash"


def test_daily_pipeline_requires_fal_not_a_direct_kling_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini")
    monkeypatch.setenv("FAL_KEY", "fal")
    monkeypatch.delenv("KLING_API_KEY", raising=False)

    assert Settings.from_env().missing_secrets(include_telegram=False) == []


def test_default_font_uses_first_available_platform_candidate(tmp_path, monkeypatch):
    missing = tmp_path / "missing.ttf"
    available = tmp_path / "available.ttf"
    available.write_bytes(b"font")
    monkeypatch.setattr(
        config, "DEFAULT_FONT_CANDIDATES", (missing, available)
    )
    monkeypatch.delenv("FONT_FILE", raising=False)

    assert Settings.from_env().font_file == available


def test_explicit_font_override_is_preserved(monkeypatch):
    monkeypatch.setenv("FONT_FILE", "~/custom-font.ttf")

    assert Settings.from_env().font_file.name == "custom-font.ttf"


def test_creator_chat_mapping_preserves_original_and_requires_new_destinations(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "original-chat")
    monkeypatch.setenv(
        "TELEGRAM_CREATOR_CHAT_IDS",
        json.dumps({"creator-02": -5280000000, "creator-03": "-5040000000"}),
    )
    settings = Settings.from_env()

    assert settings.telegram_chat_for_creator("creator-01") == "original-chat"
    assert settings.telegram_chat_for_creator("creator-02") == "-5280000000"
    assert settings.telegram_chat_for_creator("creator-03") == "-5040000000"
    with pytest.raises(ConfigurationError, match="creator-04"):
        settings.telegram_chat_for_creator("creator-04")


@pytest.mark.parametrize(
    "value",
    [
        "not-json",
        "[]",
        '{"creator-02": ""}',
        '{"creator-02": true}',
        '{"creator-02": "same", "creator-03": "same"}',
        '{"creator-02": "original-chat"}',
    ],
)
def test_invalid_creator_chat_mappings_fail_before_delivery(monkeypatch, value):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "original-chat")
    monkeypatch.setenv("TELEGRAM_CREATOR_CHAT_IDS", value)
    with pytest.raises(ConfigurationError):
        Settings.from_env()
