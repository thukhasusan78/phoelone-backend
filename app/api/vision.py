from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.auth.service import AuthError
from app.observability.logging import get_logger

log = get_logger(__name__)
router = APIRouter()


@router.post("/vision/explain")
@router.post("/vision/explain/")
async def vision_explain(
    request: Request,
    device_id: str | None = Header(default=None, alias="Device-Id"),
    client_id: str | None = Header(default=None, alias="Client-Id"),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Firmware camera POSTs multipart question + JPEG here. No-camera Otto never calls it."""
    if not device_id or not client_id:
        raise HTTPException(status_code=400, detail="Device-Id and Client-Id are required")
    try:
        await request.app.state.auth.authenticate_ws(device_id, client_id, authorization)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    question = ""
    has_file = False
    content_type = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in content_type:
        form = await request.form()
        raw_question = form.get("question")
        question = str(raw_question or "").strip()
        upload = form.get("file")
        if upload is not None and hasattr(upload, "read"):
            data = await upload.read()
            has_file = bool(data)
        elif isinstance(upload, (bytes, str)) and upload:
            has_file = True
    elif request.headers.get("content-length") not in (None, "0"):
        body = await request.body()
        has_file = bool(body)

    if not has_file:
        payload = {"success": False, "text": "no image"}
    else:
        payload = {
            "success": False,
            "text": "camera not available",
            "question": question,
        }
    log.info(
        "vision.explain",
        device_id=device_id,
        has_file=has_file,
        question_len=len(question),
    )
    return JSONResponse(payload)
