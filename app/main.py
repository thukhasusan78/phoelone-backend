from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from app.ai.gemini import GeminiLiveBrain, KeyPool
from app.ai.tool_router import ToolRouter
from app.api import companion, health, ota, portal, vision, websocket
from app.audio.edge_tts import EdgeTtsClient
from app.audio.opus import ffmpeg_available
from app.auth.service import AuthService
from app.config import Settings, get_settings
from app.db.memory import InMemoryDeviceRepository
from app.observability.logging import configure_logging, get_logger
from app.sessions.session import SessionManager
from app.tools.http import SafeHttp
from app.tools.knowledge import KnowledgeTool
from app.tools.local_music import music_local_root, scan_local_music
from app.tools.music import MusicTool
from app.tools.news import NewsTool
from app.tools.weather import WeatherTool

log = get_logger(__name__)


def _build_repository(settings: Settings):
    if settings.uses_memory_db:
        log.warning("db.memory_backend")
        return InMemoryDeviceRepository(), None, None
    from app.db.sqlalchemy_repo import PostgresDeviceRepository, create_engine, session_factory

    engine = create_engine(settings.database_url)
    factory = session_factory(engine)
    return PostgresDeviceRepository(factory), engine, factory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level, json_logs=settings.is_production)
    if not ffmpeg_available():
        log.warning("codec.ffmpeg_missing")
    repo, engine, factory = _build_repository(settings)
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.auth = AuthService(repo, settings)
    app.state.sessions = SessionManager(settings)
    from app.companion.hub import CompanionHub

    app.state.companion = CompanionHub(app.state.sessions)
    app.state.key_pool = KeyPool(settings.gemini_keys)
    app.state.tts = EdgeTtsClient(settings)
    app.state.brain_factory = lambda: GeminiLiveBrain(settings, app.state.key_pool)

    redis = None
    try:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        await redis.ping()
        app.state.redis = redis
    except Exception as exc:  # noqa: BLE001
        log.warning("redis.unavailable", error=str(exc))
        app.state.redis = None
        if redis is not None:
            await redis.aclose()

    from app.location import DeviceLocationStore

    app.state.locations = DeviceLocationStore(app.state.redis)

    http = httpx.AsyncClient(follow_redirects=True, timeout=settings.tool_timeout_s)
    app.state.http = http
    safe = SafeHttp(http, timeout_s=settings.tool_timeout_s)
    app.state.tool_router = ToolRouter(
        settings,
        WeatherTool(safe, default_location=settings.default_weather_location),
        NewsTool(safe, settings),
        KnowledgeTool(safe, settings),
        MusicTool(safe, settings),
        redis=app.state.redis,
    )
    local_root = music_local_root(settings.music_local_dir)
    if local_root is not None:
        scan_local_music(local_root)
    log.info(
        "app.started",
        environment=settings.environment,
        memory_db=settings.uses_memory_db,
        ytdlp_enabled=settings.music_ytdlp_enabled,
    )
    try:
        yield
    finally:
        await http.aclose()
        if app.state.redis is not None:
            await app.state.redis.aclose()
        if engine is not None:
            await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    application = FastAPI(
        title="Phoe Lone Backend",
        version="0.1.0",
        lifespan=lifespan,
        redirect_slashes=False,
    )
    application.state.settings = settings
    application.include_router(health.router)
    application.include_router(portal.router)
    application.include_router(companion.router)
    application.include_router(ota.router)
    application.include_router(vision.router)
    application.include_router(websocket.router)
    return application


app = create_app()
