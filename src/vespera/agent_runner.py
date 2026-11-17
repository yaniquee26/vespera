"""The agent runner: gives the agent a task and plays the conversation with simulated tools."""

import json
import time
from typing import Any

from vespera.agent_contracts import (
    DEFAULT_TOOL_RESULT,
    UNKNOWN_TOOL_PREFIX,
    AgentCase,
    AgentTrace,
    ToolCall,
    TraceEvent,
)
from vespera.contracts import Attempt
from vespera.providers import AgentProvider, ProviderError
from vespera.runner import ProgressCallback, RunAborted, RunSummary
from vespera.snapshot import now_iso
from vespera.storage import Storage

MAX_STEPS = 5


def simulate_tool(case: AgentCase, call: ToolCall) -> str:
    """Tools are harmless stand-ins: they return the scripted text, or a plain OK."""
    if call.name not in {tool.name for tool in case.tools}:
        return f"{UNKNOWN_TOOL_PREFIX} '{call.name}'"
    return case.tool_results.get(call.name, DEFAULT_TOOL_RESULT)


def run_agent_case(
    provider: AgentProvider, case: AgentCase, system_prompt: str, max_steps: int = MAX_STEPS
) -> AgentTrace:
    """One task, played turn by turn until the agent answers in text or runs out of steps."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": case.task},
    ]
    events: list[TraceEvent] = []
    for step in range(1, max_steps + 1):
        turn = provider.chat(messages, case.tools)
        events.append(TraceEvent(kind="assistant", text=turn.text, tool_calls=turn.tool_calls))
        if not turn.tool_calls:
            return AgentTrace(events=events, stopped="answered")
        ids = [call.id or f"call_{step}_{i}" for i, call in enumerate(turn.tool_calls)]
        messages.append(
            {
                "role": "assistant",
                "content": turn.text,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                    for call_id, call in zip(ids, turn.tool_calls, strict=True)
                ],
            }
        )
        for call_id, call in zip(ids, turn.tool_calls, strict=True):
            result = simulate_tool(case, call)
            events.append(TraceEvent(kind="tool_result", text=result, tool_name=call.name))
            messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
    return AgentTrace(events=events, stopped="max_steps")


def run_agent_experiment(
    provider: AgentProvider,
    cases: list[AgentCase],
    system_prompt: str,
    storage: Storage,
    run_id: int,
    repeats: int,
    *,
    max_steps: int = MAX_STEPS,
    max_consecutive_errors: int = 3,
    on_progress: ProgressCallback | None = None,
) -> RunSummary:
    """Same rules as run_experiment: one at a time, saved at once, resumable."""
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
            trace: AgentTrace | None = None
            try:
                trace = run_agent_case(provider, case, system_prompt, max_steps)
            except ProviderError as exc:
                error = str(exc)
            latency_ms = int((time.perf_counter() - started) * 1000)
            storage.add_attempt(
                run_id,
                Attempt(
                    case_id=case.id,
                    repeat=repeat,
                    reply=trace.final_reply() if trace else "",
                    error=error,
                    latency_ms=latency_ms,
                    created_at=now_iso(),
                ),
            )
            if trace is not None:
                storage.add_trace(run_id, case.id, repeat, trace)
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
