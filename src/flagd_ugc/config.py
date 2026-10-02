from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigurationError


DEFAULT_GEMINI_TEXT_MODEL = "gemini-3.6-flash"
DEFAULT_FONT_CANDIDATES = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/opt/X11/share/system_fonts/Supplemental/Arial Bold.ttf"),
)


def default_font_file() -> Path:
    """Return the first bundled/system bold font available on this platform."""
    return next(
        (path for path in DEFAULT_FONT_CANDIDATES if path.is_file()),
        DEFAULT_FONT_CANDIDATES[0],
    )


def _gemini_text_model() -> str:
    configured = os.environ.get("GEMINI_TEXT_MODEL", DEFAULT_GEMINI_TEXT_MODEL)
    retired_aliases = {
        "gemini-2.5-flash": DEFAULT_GEMINI_TEXT_MODEL,
        "models/gemini-2.5-flash": DEFAULT_GEMINI_TEXT_MODEL,
    }
    return retired_aliases.get(configured, configured)


def load_local_env(path: Path = Path(".env")) -> None:
    """Load a small, conventional .env file without overriding shell variables."""
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    except PermissionError as exc:
        already_configured = any(
            os.environ.get(name)
            for name in ("FAL_KEY", "GEMINI_API_KEY", "KLING_API_KEY")
        )
        if already_configured:
            return
        raise ValueError(f"could not read {path}: permission denied") from exc
    for number, raw_line in enumerate(content.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"invalid .env line {number}: expected NAME=value")
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name or not name.replace("_", "a").isalnum() or name[0].isdigit():
            raise ValueError(f"invalid .env variable name on line {number}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


def _positive_int(name: str, default: int) -> int:
    value = int(os.environ.get(name, default))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _non_negative_int(name: str, default: int) -> int:
    value = int(os.environ.get(name, default))
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


def _telegram_creator_chats() -> dict[str, str]:
    raw = os.environ.get("TELEGRAM_CREATOR_CHAT_IDS", "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError("TELEGRAM_CREATOR_CHAT_IDS must be a JSON object") from exc
    if not isinstance(value, dict):
        raise ConfigurationError("TELEGRAM_CREATOR_CHAT_IDS must be a JSON object")
    chats: dict[str, str] = {}
    for creator_id, chat_id in value.items():
        if (
            not isinstance(creator_id, str)
            or not creator_id.strip()
            or isinstance(chat_id, bool)
            or not isinstance(chat_id, (str, int))
            or not str(chat_id).strip()
        ):
            raise ConfigurationError("each creator must have a non-empty Telegram chat ID")
        chats[creator_id] = str(chat_id).strip()
    if len(set(chats.values())) != len(chats):
        raise ConfigurationError("each creator must have a different Telegram chat ID")
    original_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if (
        original_chat
        and "creator-01" not in chats
        and original_chat in chats.values()
    ):
        raise ConfigurationError("each creator must have a different Telegram chat ID")
    return chats


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    gemini_api_key: str
    kling_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    fal_api_key: str = ""
    fal_kling_model: str = "fal-ai/kling-video/v3/standard/image-to-video"
    gemini_text_model: str = DEFAULT_GEMINI_TEXT_MODEL
    gemini_image_model: str = "gemini-3-pro-image"
    kling_api_base_url: str = "https://api.klingai.com/v1"
    kling_model: str = "kling-v3"
    timezone: str = "America/Los_Angeles"
    run_time: str = "08:00"
    video_count: int = 4
    request_timeout_seconds: float = 120.0
    fal_poll_seconds: float = 60.0
    fal_initial_poll_delay_seconds: float = 180.0
    kling_poll_seconds: float = 15.0
    kling_timeout_seconds: float = 1800.0
    transient_retries: int = 3
    quality_retries: int = 2
    final_retention_days: int = 30
    intermediate_retention_days: int = 7
    font_file: Path = field(default_factory=default_font_file)
    telegram_creator_chat_ids: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data_dir=Path(os.environ.get("DATA_DIR", "/data")).expanduser().resolve(),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
            kling_api_key=os.environ.get("KLING_API_KEY", ""),
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
            telegram_creator_chat_ids=_telegram_creator_chats(),
            fal_api_key=os.environ.get("FAL_KEY", ""),
            fal_kling_model=os.environ.get(
                "FAL_KLING_MODEL", "fal-ai/kling-video/v3/standard/image-to-video"
            ),
            gemini_text_model=_gemini_text_model(),
            gemini_image_model=os.environ.get(
                "GEMINI_IMAGE_MODEL", "gemini-3-pro-image"
            ),
            kling_api_base_url=os.environ.get(
                "KLING_API_BASE_URL", "https://api.klingai.com/v1"
            ).rstrip("/"),
            kling_model=os.environ.get("KLING_MODEL", "kling-v3"),
            timezone=os.environ.get("TIMEZONE", "America/Los_Angeles"),
            run_time=os.environ.get("RUN_TIME", "08:00"),
            video_count=4,
            request_timeout_seconds=float(
                os.environ.get("REQUEST_TIMEOUT_SECONDS", "120")
            ),
            fal_poll_seconds=float(os.environ.get("FAL_POLL_SECONDS", "60")),
            fal_initial_poll_delay_seconds=float(
                os.environ.get("FAL_INITIAL_POLL_DELAY_SECONDS", "180")
            ),
            kling_poll_seconds=float(os.environ.get("KLING_POLL_SECONDS", "15")),
            kling_timeout_seconds=float(
                os.environ.get("KLING_TIMEOUT_SECONDS", "1800")
            ),
            transient_retries=_positive_int("TRANSIENT_RETRIES", 3),
            quality_retries=_non_negative_int("QUALITY_RETRIES", 2),
            final_retention_days=_positive_int("FINAL_RETENTION_DAYS", 30),
            intermediate_retention_days=_positive_int("INTERMEDIATE_RETENTION_DAYS", 7),
            font_file=(
                Path(os.environ["FONT_FILE"]).expanduser()
                if os.environ.get("FONT_FILE")
                else default_font_file()
            ),
        )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "pipeline.sqlite3"

    @property
    def music_dir(self) -> Path:
        return self.data_dir / "music"

    @property
    def demos_dir(self) -> Path:
        return self.data_dir / "demos"

    @property
    def characters_dir(self) -> Path:
        return self.data_dir / "characters"

    @property
    def renders_dir(self) -> Path:
        return self.data_dir / "renders"

    def create_directories(self) -> None:
        for path in (
            self.db_path.parent,
            self.music_dir,
            self.demos_dir,
            self.characters_dir,
            self.renders_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def missing_secrets(self, *, include_telegram: bool = True) -> list[str]:
        required = {
            "GEMINI_API_KEY": self.gemini_api_key,
            "FAL_KEY": self.fal_api_key,
        }
        if include_telegram:
            required.update(
                {
                    "TELEGRAM_BOT_TOKEN": self.telegram_bot_token,
                    "TELEGRAM_CHAT_ID": self.telegram_chat_for_creator("creator-01"),
                }
            )
        return [name for name, value in required.items() if not value]

    def telegram_chat_for_creator(self, creator_id: str) -> str:
        """Resolve an explicit destination; preserve the original creator's legacy chat."""
        if chat_id := self.telegram_creator_chat_ids.get(creator_id):
            return chat_id
        if creator_id == "creator-01":
            return self.telegram_chat_id
        raise ConfigurationError(f"no Telegram chat configured for creator {creator_id!r}")
