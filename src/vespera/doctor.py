"""Environment checks behind the `vespera doctor` command."""

import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

OLLAMA_URL = "http://localhost:11434"
MIN_PYTHON = (3, 12)


@dataclass(frozen=True)
class CheckStatus:
    """Result of one environment check."""

    name: str
    ok: bool
    detail: str
    required: bool


def check_python(version: tuple[int, int] | None = None) -> CheckStatus:
    """Vespera needs Python 3.12 or newer."""
    current = version or (sys.version_info.major, sys.version_info.minor)
    ok = current >= MIN_PYTHON
    wanted = f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}"
    detail = f"Python {current[0]}.{current[1]}" + ("" if ok else f" (need {wanted}+)")
    return CheckStatus(name="python", ok=ok, detail=detail, required=True)


def check_ollama(url: str = OLLAMA_URL, timeout: float = 1.0) -> CheckStatus:
    """Ollama is optional for now: tests run on MockProvider without it."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            ok = response.status == 200
    except OSError:  # URLError, timeouts and refused connections are all OSError
        ok = False
    detail = f"reachable at {url}" if ok else f"not reachable at {url} (needed for real models)"
    return CheckStatus(name="ollama", ok=ok, detail=detail, required=False)


def run_all() -> list[CheckStatus]:
    return [check_python(), check_ollama()]


def all_required_ok(results: list[CheckStatus]) -> bool:
    return all(r.ok for r in results if r.required)