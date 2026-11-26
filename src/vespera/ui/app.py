"""The Studio application: settings, security, pages and static files."""

import json
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import escape
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from vespera import __version__
from vespera.catalog import FIELD_HELP, TARGET_KINDS, target_kind
from vespera.config import TargetConfig
from vespera.doctor import CheckStatus, run_all
from vespera.model_identity import LoadedModel
from vespera.providers import BaseProvider
from vespera.services.errors import TargetError
from vespera.services.results import junit_xml, report_html, results_json
from vespera.services.targets import (
    build_target,
    delete_target,
    env_var_is_set,
    get_target,
    save_target,
    text_provider,
    try_target,
)
from vespera.storage import Storage
from vespera.ui.security import (
    ALLOWED_HOSTS,
    SECURITY_HEADERS,
    allowed_origins,
    request_problem,
)
from vespera.ui.views import (
    PLACEHOLDERS,
    TABS,
    RunDetailView,
    highlight,
    local_ollama_models,
    overview_view,
    run_detail_view,
    runs_view,
    settings_view,
    target_settings,
    targets_view,
)
from vespera.workspace import Workspace

HERE = Path(__file__).parent
DEFAULT_PORT = 8765

# Pages in the side menu, in order: (path, label, icon name in static/icons.svg)
NAVIGATION = (
    ("/", "Overview", "overview"),
    ("/runs", "Runs", "runs"),
    ("/targets", "Targets", "targets"),
    ("/settings", "Settings", "settings"),
)


@dataclass(frozen=True)
class UiSettings:
    workspace: Workspace
    port: int = DEFAULT_PORT
    csrf_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    environment_checks: Callable[[], list[CheckStatus]] = run_all
    loaded_models: Callable[[], list[LoadedModel] | None] = local_ollama_models
    text_provider: Callable[[TargetConfig], BaseProvider] = text_provider


MAX_MESSAGE = 4000  # characters of one Playground message


def percent(value: float) -> str:
    return f"{round(value * 100)}%"


def bar_width(value: float) -> str:
    """A share as a coordinate on a 0..100 SVG axis (an attribute, so the CSP holds)."""
    return f"{value * 100:.1f}"


def create_app(settings: UiSettings) -> FastAPI:
    app = FastAPI(
        title="Vespera Studio", version=__version__, docs_url=None, redoc_url=None,
        openapi_url=None,
    )
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(
        version=__version__, navigation=NAVIGATION, csrf_token=settings.csrf_token
    )
    templates.env.filters.update(highlight=highlight, pct=percent, w=bar_width)
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
        view = settings_view(
            settings.workspace, settings.port, settings.environment_checks, settings.loaded_models
        )
        return page(request, "settings.html", view=view)

    # ------------------------------------------------------------ targets and the Playground

    def not_found(request: Request) -> Response:
        return templates.TemplateResponse(
            request, "not_found.html", {"current": ""}, status_code=404
        )

    def problem(message: str) -> HTMLResponse:
        return HTMLResponse(
            f'<p class="alert bad" data-testid="form-problem">{escape(message)}</p>'
        )

    @app.get("/targets", response_class=HTMLResponse)
    def targets_page(request: Request) -> HTMLResponse:
        rows, trouble = targets_view(settings.workspace)
        return page(request, "targets.html", targets=rows, problem=trouble, kinds=TARGET_KINDS)

    @app.get("/targets/new", response_model=None)
    def new_target(request: Request, type: str = "ollama") -> Response:
        try:
            kind = target_kind(type)
        except KeyError:
            return not_found(request)
        return page(
            request, "target_form.html", kind=kind, help=FIELD_HELP, placeholders=PLACEHOLDERS
        )

    @app.post("/targets", response_model=None)
    async def create_target(request: Request) -> Response:
        form = await request.form()
        kind_key = str(form.get("type", ""))
        try:
            kind = target_kind(kind_key)
            fields = {name: str(form.get(name, "")) for name in kind.fields}
            saved = save_target(
                settings.workspace,
                str(form.get("name", "")).strip(),
                build_target({"type": kind_key, **fields}),
            )
        except KeyError:
            return problem(f"unknown target type {kind_key}")
        except TargetError as exc:
            return problem(str(exc))
        return Response(status_code=204, headers={"HX-Redirect": f"/targets/{saved.name}"})

    @app.get("/targets/{name}", response_model=None)
    def target_page(request: Request, name: str) -> Response:
        try:
            target = get_target(settings.workspace, name)
        except TargetError:
            return not_found(request)
        key_set = bool(target.key_env and env_var_is_set(target.key_env))
        return page(
            request,
            "target_detail.html",
            target=target,
            kind=target_kind(target.config.type),
            settings=target_settings(target),
            key_set=key_set,
            current="/targets",
        )

    @app.post("/targets/{name}/try", response_model=None)
    async def try_page(request: Request, name: str) -> Response:
        try:
            target = get_target(settings.workspace, name)
        except TargetError as exc:
            return problem(str(exc))
        message = str((await request.form()).get("message", "")).strip()[:MAX_MESSAGE]
        if not message:
            return problem("write a message to send")
        result = try_target(target.config, message, make_provider=settings.text_provider)
        request_json = json.dumps(result.request, indent=2, ensure_ascii=False)
        return templates.TemplateResponse(
            request, "_try_result.html", {"result": result, "request_json": request_json}
        )

    @app.post("/targets/{name}/delete", response_model=None)
    def remove_target(name: str) -> Response:
        try:
            delete_target(settings.workspace, name)
        except TargetError as exc:
            return problem(str(exc))
        return Response(status_code=204, headers={"HX-Redirect": "/targets"})

    # ------------------------------------------------------------ runs and run detail

    def run_or_none(run_id: str) -> RunDetailView | None:
        if not run_id.isdigit():
            return None
        return run_detail_view(settings.workspace, int(run_id))

    @app.get("/runs", response_class=HTMLResponse)
    def runs_page(request: Request) -> HTMLResponse:
        return page(
            request,
            "runs.html",
            runs=runs_view(settings.workspace),
            database=str(settings.workspace.database),
        )

    @app.get("/runs/{run_id}", response_model=None)
    def run_page(request: Request, run_id: str, tab: str = "categories") -> Response:
        view = run_or_none(run_id)
        if view is None or tab not in dict(TABS):
            return not_found(request)
        return page(
            request,
            "run_detail.html",
            v=view,
            tab=tab,
            tabs=TABS,
            database=str(settings.workspace.database),
            current="/runs",
        )

    @app.get("/runs/{run_id}/tabs/{tab}", response_model=None)
    def run_tab(request: Request, run_id: str, tab: str) -> Response:
        view = run_or_none(run_id)
        if view is None or tab not in dict(TABS):
            return not_found(request)
        return templates.TemplateResponse(
            request, "_run_tabs.html", {"v": view, "tab": tab, "tabs": TABS}
        )

    downloads = {
        "results.json": "application/json",
        "junit.xml": "application/xml",
        "report.html": "text/html; charset=utf-8",
    }

    @app.get("/runs/{run_id}/download/{name}", response_model=None)
    def download(request: Request, run_id: str, name: str) -> Response:
        """A masked copy of the results: these files are meant to leave this computer."""
        view = run_or_none(run_id)
        if view is None or view.report is None or name not in downloads:
            return not_found(request)
        with Storage(settings.workspace.database, concurrent=True) as storage:
            if name == "results.json":
                body = results_json(storage, view.run_id, view.report, None, reveal=False)
            elif name == "junit.xml":
                body = junit_xml(storage, view.run_id, reveal=False)
            else:
                body = report_html(storage, view.run_id, view.report, reveal=False)
        filename = f"vespera-run-{view.run_id}-{name}"
        return Response(
            body,
            media_type=downloads[name],
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code == 404:
            return templates.TemplateResponse(
                request, "not_found.html", {"current": ""}, status_code=404
            )
        return PlainTextResponse(str(exc.detail), status_code=exc.status_code)

    return app
