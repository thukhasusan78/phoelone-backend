from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.rate_limit import client_key, limiter
from app.companion.auth import identity_from_request, set_companion_cookie

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()


def _pin_configured(request: Request) -> bool:
    return bool(request.app.state.settings.companion_pin)


def _page(
    request: Request,
    *,
    ok: bool | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "ok": ok,
            "error": error,
            "pin_configured": _pin_configured(request),
        },
        status_code=status_code,
    )


def pin_unlock_page(
    request: Request,
    *,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return _page(request, error=error, status_code=status_code)


def dashboard_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={},
    )


@router.get("/", response_class=HTMLResponse)
async def portal_home(request: Request) -> HTMLResponse:
    settings = request.app.state.settings
    identity = identity_from_request(request, settings)
    if identity is not None:
        record = await request.app.state.auth.repo.get(identity.device_id, identity.client_id)
        if record is not None and record.is_active:
            return dashboard_page(request)
    return _page(request)


@router.post("/activate", response_model=None)
async def portal_activate(request: Request):
    settings = request.app.state.settings
    limiter.check(client_key(request), settings.activation_rate_limit_per_minute)

    content_type = (request.headers.get("content-type") or "").lower()
    wants_json = "application/json" in content_type
    code = ""
    if wants_json:
        try:
            raw = await request.json()
        except Exception:
            raw = {}
        if isinstance(raw, dict):
            code = str(raw.get("code") or "")
    else:
        form = await request.form()
        code = str(form.get("code") or "")

    record = await request.app.state.auth.complete_activation(code)
    if record is None:
        error = "Invalid or expired code. Check the digits on the robot and try again."
        if wants_json:
            return JSONResponse({"ok": False, "error": error}, status_code=400)
        return _page(request, ok=False, error=error, status_code=400)

    if wants_json:
        response: JSONResponse | RedirectResponse = JSONResponse({"ok": True})
    else:
        response = RedirectResponse(url="/", status_code=303)
    set_companion_cookie(response, settings, record.device_id, record.client_id)
    return response
