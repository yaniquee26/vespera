"""Experiment Snapshot: a fingerprint of everything that can change a result."""

import hashlib
import platform
from datetime import UTC, datetime

from vespera import __version__
from vespera.contracts import Snapshot


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def build_snapshot(
    *,
    model: str,
    temperature: float,
    num_predict: int,
    repeats: int,
    system_prompt: str,
    scenarios_text: str,
) -> Snapshot:
    return Snapshot(
        vespera_version=__version__,
        python_version=platform.python_version(),
        model=model,
        temperature=temperature,
        num_predict=num_predict,
        repeats=repeats,
        system_prompt_sha256=sha256_text(system_prompt),
        scenarios_sha256=sha256_text(scenarios_text),
        created_at=now_iso(),
    )