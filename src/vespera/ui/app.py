"""The Studio application: settings, security, pages and static files."""

import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from vespera import __version__
from vespera.doctor import CheckStatus, run_all
from vespera.ui.security import (
    ALLOWED_HOSTS,
    SECURITY_HEADERS,
    allowed_origins,
    request_problem,
)
from vespera.ui.views import overview_view, settings_view
from vespera.workspace import Workspace

HERE = Path(__file__).parent
DEFAULT_PORT = 8765

# Pages in the side menu, in order: (path, label, icon name in static/icons.svg)
NAVIGATION = (
    ("/", "Overview", "overview"),
    ("/settings", "Settings", "settings"),
)


@dataclass(frozen=True)
class UiSettings:
    workspace: Workspace
    port: int = DEFAULT_PORT
    csrf_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    environment_checks: Callable[[], list[CheckStatus]] = run_all


def create_app(settings: UiSettings) -> FastAPI:
    app = FastAPI(
        title="Vespera Studio", version=__version__, docs_url=None, redoc_url=None,
        openapi_url=None,
    )
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(
        version=__version__, navigation=NAVIGATION, csrf_token=settings.csrf_token
    )
    origins = allowed_origins(settings.port)

    # Added first, so it runs inside the guard below and its refusals get the same headers.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(ALLOWED_HOSTS))

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        problem = request_problem(
            request.method, request.headers, settings.csrf_token, origins
        )
        if problem is not None:
            response: Response = PlainTextResponse(f"Forbidden: {problem}", status_code=403)
        else:
            response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def page(request: Request, name: str, **context: object) -> HTMLResponse:
        return templates.TemplateResponse(
            request, name, {"current": request.url.path, **context}
        )

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request) -> HTMLResponse:
        return page(request, "overview.html", view=overview_view(settings.workspace))

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request) -> HTMLResponse:
        view = settings_view(settings.workspace, settings.port, settings.environment_checks)
        return page(request, "settings.html", view=view)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code == 404:
            return templates.TemplateResponse(
                request, "not_found.html", {"current": ""}, status_code=404
            )
        return PlainTextResponse(str(exc.detail), status_code=exc.status_code)

    return app
