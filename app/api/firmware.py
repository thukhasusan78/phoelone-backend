from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

router = APIRouter()

_FIRMWARE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.bin$")


def _safe_firmware_path(root: Path, name: str) -> Path | None:
    if name == "none.bin" or not _FIRMWARE_NAME.match(name):
        return None
    try:
        base = root.resolve()
        path = (base / name).resolve()
    except OSError:
        return None
    if path.parent != base or not path.is_file():
        return None
    return path


@router.get("/firmware/none.bin")
async def dummy_firmware() -> Response:
    return Response(status_code=404)


@router.get("/firmware/{name}")
async def firmware_bin(name: str, request: Request) -> FileResponse:
    settings = request.app.state.settings
    path = _safe_firmware_path(settings.firmware_root, name)
    if path is None:
        raise HTTPException(status_code=404, detail="firmware not found")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=name,
        content_disposition_type="attachment",
    )
