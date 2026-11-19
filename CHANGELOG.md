# Changelog

## Unreleased
- Risk labels follow the OWASP Top 10 for LLM Applications **2026**: excessive agency is now
  LLM03 (was LLM06), system prompt leakage is LLM08 "Hidden context exposure" (was LLM07).
  The report shows the edition (`OWASP LLM03:2026`).
- Breaking: `zero_tolerance` with the old ids LLM06 or LLM07 is refused, and the message names
  the 2026 id to use instead.

## 0.4.0
- Risk labels: every category carries an OWASP LLM Top 10 (2025) id and a severity; the report shows them.
- Gate with three outcomes (passed / failed / inconclusive): `--max-attack-rate`,
  `--zero-tolerance LLM02,LLM06`, `--min-exposure`. New command `vespera gate`; exit codes 0, 2, 3.
- Machine-readable results: `results.json` and JUnit XML (`--json`, `--junit`, `vespera export`),
  masked by default; `--reveal` shows everything. `vespera check --mask` for the console.
- Agent runs: a tool call written as plain text is now measured and reported separately.
- Agent system prompt orders a `search_help` lookup, so poisoned tool output reaches the agent.
- Project file `vespera.yaml`: target, mode, repeats, scenarios, gate and exports in one place.
  `vespera init` writes it; `vespera test` and `vespera gate` read it. Flags win over the file.
  Unknown keys are refused. Keys are read from environment variables only.
- Any bot behind a plain JSON-over-HTTP endpoint can be tested without code (`target.type: http`):
  the question and the answer are located with dotted paths.
- `vespera validate` also checks agent scenario files.
- Guides: `docs/connect-your-bot.md`, `docs/write-a-scenario.md` (their examples are tested).

## 0.3.0
- Agent testing: `vespera run-agent` plays tasks with simulated tools, including tool output that
  hides an order (indirect injection), and records everything the agent said and called.
- New checks on the journal: forbidden tool call, secret in tool arguments, expected tool not called.
- Reports and comparisons work for agent runs; the proof is the full journal.
- `vespera init --agents` copies the example agent files.

## 0.2.0
- `vespera compare`: two runs side by side, with intervals and a "conclusive or not" flag.
- OpenAI-compatible provider: test any bot or model with an OpenAI-style API.
- `vespera init` copies example scenarios and a system prompt; they now ship inside the package.
- `vespera test` runs, checks and reports in one command; `--max-attack-rate` makes it usable in CI.
- Clear error when an Ollama model is not installed.

## 0.1.0
- Scenarios in EN/RU/RO, rule-based checks, Wilson intervals, resumable runs, HTML report.
