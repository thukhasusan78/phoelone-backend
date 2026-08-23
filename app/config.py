from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_TRUTHY = {"1", "true", "yes", "on", "y"}
_FALSY = {"0", "false", "no", "off", "n", ""}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["development", "production", "test"] = "development"
    log_level: str = "info"
    host: str = "0.0.0.0"
    port: int = 8000

    public_http_origin: str = "http://127.0.0.1:8000"
    public_ws_origin: str = "ws://127.0.0.1:8000"

    database_url: str = "memory://"
    redis_url: str = "redis://127.0.0.1:6379/0"

    auth_pepper: str = "dev-pepper-change-me"
    allow_auto_provision: bool = False
    metrics_token: str = ""

    gemini_api_keys: str = ""
    gemini_model: str = "gemini-3.1-flash-live-preview"

    tts_voice: str = "my-MM-NilarNeural"
    tts_fallback_voice: str = "my-MM-ThihaNeural"
    tts_rate: str = "+45%"
    tts_pitch: str = "+100Hz"
    tts_volume: str = "+100%"

    timezone_offset_minutes: int = 390
    # Used when the user does not name a city. Prefer this over ISP IP geolocation
    # (Myanmar public IPs often geolocate to Yangon).
    default_weather_location: str = ""
    firmware_version: str = "0.0.0"
    firmware_url: str = ""

    tavily_key: str = ""
    openweather_api_key: str = ""

    # Optional Jamendo client id for full Creative Commons tracks.
    # iTunes and Deezer 30s previews work with no key.
    jamendo_client_id: str = ""
    # 0 or negative = play the full track (no ffmpeg -t cap).
    music_max_seconds: float = 0.0
    music_download_timeout_s: float = 30.0
    music_max_bytes: int = 12_000_000
    # Auto-discovered local audio under this directory (Artist - Title.mp3).
    music_local_dir: str = "data/local_music"
    # Optional curated Myanmar catalog JSON (HTTPS URLs only; overlay after local scan).
    music_catalog_path: str = ""
    # YouTube via yt-dlp is last-resort only; default off for production ToS safety.
    music_ytdlp_enabled: bool = False
    music_ytdlp_bin: str = "yt-dlp"
    music_ytdlp_timeout_s: float = 25.0
    # Netscape cookies.txt for YouTube bot checks. Empty = clients-only workaround.
    music_ytdlp_cookies: str = ""
    # Comma-separated yt-dlp YouTube player clients (avoid web; it needs a PO token).
    music_ytdlp_player_clients: str = "android,ios,tv"
    # Minimum token-overlap score (0-1) before a global-provider track may play.
    # Whole-word coverage; 0.6 rejects a single short-name hit like "Joe" in "BukJoe858".
    music_relevance_threshold: float = 0.6

    # Optional SMTP for send_email. Tool returns configured=false when unset.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True

    # MQTT+UDP is firmware-optional. Never advertise it in OTA while WebSocket voice is primary.
    mqtt_enabled: bool = False

    hello_timeout_s: float = 8.0
    device_idle_timeout_s: float = 100.0
    keepalive_interval_s: float = 30.0
    max_utterance_seconds: float = 30.0
    max_utterance_bytes: int = 512_000
    # Hard cap on PCM forwarded to Gemini even if VAD stays open.
    max_forwarded_audio_seconds: float = 15.0
    pcm_queue_size: int = 80
    outbound_queue_size: int = 200
    mcp_timeout_s: float = 8.0
    tool_timeout_s: float = 8.0
    tts_timeout_s: float = 45.0
    turn_timeout_s: float = 40.0
    listen_idle_timeout_s: float = 8.0
    gemini_connect_timeout_s: float = 12.0
    gemini_send_timeout_s: float = 10.0
    max_tool_rounds: int = 3

    # Server-owned endpointing. Production uses Silero (speech vs fan/noise).
    # ``energy`` is for tests only — it cannot reject high-RMS environmental noise.
    vad_backend: Literal["silero", "energy"] = "silero"
    vad_speech_threshold: float = 0.5
    vad_min_speech_ms: float = 180.0
    vad_min_silence_ms: float = 800.0
    vad_preroll_chunks: int = 3
    vad_energy_speech_rms: float = 500.0
    vad_warmup_ms: float = 1500.0
    vad_warmup_energy_rms: float = 1800.0

    max_concurrent_sessions: int = 32
    ota_rate_limit_per_minute: int = 30
    ws_rate_limit_per_minute: int = 20
    max_tts_chars: int = 800
    redis_cache_ttl_s: int = 300

    @field_validator("public_http_origin", "public_ws_origin", "firmware_url")
    @classmethod
    def strip_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("music_ytdlp_enabled", mode="before")
    @classmethod
    def parse_ytdlp_enabled(cls, value: object) -> bool:
        """Accept true/True/1/yes/on regardless of case or surrounding space."""
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            folded = value.strip().casefold()
            if folded in _TRUTHY:
                return True
            if folded in _FALSY:
                return False
        return False

    @property
    def gemini_keys(self) -> list[str]:
        return [k.strip() for k in self.gemini_api_keys.split(",") if k.strip()]

    @property
    def websocket_url(self) -> str:
        return f"{self.public_ws_origin}/xiaozhi/v1/"

    @property
    def vision_url(self) -> str:
        return f"{self.public_http_origin}/vision/explain"

    @property
    def ota_websocket_version(self) -> int:
        return 1

    @property
    def resolved_firmware_url(self) -> str:
        if self.firmware_url:
            return self.firmware_url
        return f"{self.public_http_origin}/firmware/none.bin"

    @property
    def uses_memory_db(self) -> bool:
        return self.database_url.startswith("memory")

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
