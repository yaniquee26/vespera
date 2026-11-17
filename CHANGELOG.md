# Changelog

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
