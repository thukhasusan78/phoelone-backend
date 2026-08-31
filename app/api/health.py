from __future__ import annotations

import hmac

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from app.observability.metrics import metrics_payload

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    checks = {
        "database": True,
        "redis": True,
        "gemini_keys": bool(settings.gemini_keys),
        "ffmpeg": True,
    }
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            await redis.ping()
        except Exception:
            checks["redis"] = False
    engine = getattr(request.app.state, "engine", None)
    if engine is not None:
        try:
            async with engine.connect() as conn:
                await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        except Exception:
            checks["database"] = False
    from app.audio.opus import ffmpeg_available

    checks["ffmpeg"] = ffmpeg_available()
    ok = checks["gemini_keys"] and checks["ffmpeg"]
    if not settings.uses_memory_db:
        ok = ok and checks["database"]
    status = "ok" if ok else "degraded"
    code = 200 if ok else 503
    return JSONResponse({"status": status, "checks": checks}, status_code=code)


@router.get("/metrics")
async def metrics(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Response:
    token = request.app.state.settings.metrics_token
    if token:
        expected = f"Bearer {token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="unauthorized")
    return PlainTextResponse(metrics_payload(), media_type="text/plain; version=0.0.4")
