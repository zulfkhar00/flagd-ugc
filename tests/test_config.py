from flagd_ugc import config
from flagd_ugc.config import Settings


def test_default_text_model_uses_current_gemini_flash(monkeypatch):
    monkeypatch.delenv("GEMINI_TEXT_MODEL", raising=False)

    assert Settings.from_env().gemini_text_model == "gemini-3.6-flash"


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
