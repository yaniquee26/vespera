"""The runner: asks every case to a model, repeat by repeat, and saves each answer."""

import time
from collections.abc import Callable
from dataclasses import dataclass

from vespera.contracts import Attempt, Case
from vespera.providers import BaseProvider, ProviderError
from vespera.snapshot import now_iso
from vespera.storage import Storage

ProgressCallback = Callable[[int, int, str, int, str | None], None]


class RunAborted(Exception):
    """Too many errors in a row: the model server is most likely down."""


@dataclass(frozen=True)
class RunSummary:
    total: int
    attempted: int
    skipped: int
    errors: int


def run_experiment(
    provider: BaseProvider,
    cases: list[Case],
    system_prompt: str,
    storage: Storage,
    run_id: int,
    repeats: int,
    *,
    max_consecutive_errors: int = 3,
    on_progress: ProgressCallback | None = None,
) -> RunSummary:
    """One request at a time. Attempts that already succeeded are skipped (resume)."""
    done = storage.done_keys(run_id)
    total = len(cases) * repeats
    attempted = skipped = errors = streak = 0

    for repeat in range(1, repeats + 1):
        for case in cases:
            if (case.id, repeat) in done:
                skipped += 1
                continue
            started = time.perf_counter()
            error: str | None = None
            reply = ""
            try:
                reply = provider.generate(system_prompt, case.prompt)
            except ProviderError as exc:
                error = str(exc)
            latency_ms = int((time.perf_counter() - started) * 1000)
            storage.add_attempt(
                run_id,
                Attempt(
                    case_id=case.id,
                    repeat=repeat,
                    reply=reply,
                    error=error,
                    latency_ms=latency_ms,
                    created_at=now_iso(),
                ),
            )
            attempted += 1
            if error is None:
                streak = 0
            else:
                errors += 1
                streak += 1
                if streak >= max_consecutive_errors:
                    raise RunAborted(
                        f"{streak} errors in a row, stopping. Last error: {error}. "
                        f"Fix the problem and continue with --resume {run_id}."
                    )
            if on_progress is not None:
                on_progress(attempted + skipped, total, case.id, repeat, error)

    storage.finish_run(run_id)
    return RunSummary(total=total, attempted=attempted, skipped=skipped, errors=errors)