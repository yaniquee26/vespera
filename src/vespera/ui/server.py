"""Starting Studio: one process, 127.0.0.1 only, the browser opened once it is up."""

import threading
import webbrowser
from collections.abc import Callable

import uvicorn

from vespera.ui.app import UiSettings, create_app
from vespera.workspace import Workspace

HOST = "127.0.0.1"  # never another interface: Studio is for this computer only


def address(port: int) -> str:
    return f"http://{HOST}:{port}/"


def serve(
    space: Workspace,
    port: int,
    open_browser: bool,
    *,
    run: Callable[..., None] = uvicorn.run,
    later: Callable[[float, Callable[[], None]], None] | None = None,
    opener: Callable[[str], object] = webbrowser.open,
) -> None:
    """Run Studio until Ctrl+C. The browser opens a moment after the server starts."""
    app = create_app(UiSettings(workspace=space, port=port))
    if open_browser:

        def open_page() -> None:
            opener(address(port))

        schedule = later or (lambda delay, action: threading.Timer(delay, action).start())
        schedule(1.0, open_page)
    run(app, host=HOST, port=port, log_level="warning", proxy_headers=False)
