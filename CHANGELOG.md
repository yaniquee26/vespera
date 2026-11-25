# Changelog

## Unreleased
- `vespera init`: empty lines between the questions, so the setup reads more easily.
- README and benchmark results show screenshots of the report and the comparison (light and dark).
- Repository layout: example scenarios and system prompts live in `examples/` (a test keeps them
  identical to the copies shipped in the package); the original design spec is in `docs/history/`.
- Roadmap for the releases after 1.0.0 (`ROADMAP.md`).
- README in Romanian and Russian (`README.ro.md`, `README.ru.md`), a short summary at the top of
  the English README and a table of the testing practice behind the tool. A test keeps the
  benchmark numbers and the "conclusive" marks identical in all three languages.
- Landing page on GitHub Pages in English, Romanian and Russian (`docs/index.html`, `ro.html`,
  `ru.html`, shared `style.css`), with a short "try it" block and the current release; the same
  test checks its numbers and language links.
- CI runs on Ubuntu and Windows with Python 3.12 and 3.13, fails below 98% line coverage and
  uploads the JUnit report of every run.
- `pii_not_leaked` finds Moldovan phone numbers in the local format (`069 123 456`,
  `(069) 123-456`, `022 12 34 56`), `+373` numbers in any grouping (`+373 69 12 34 56`) and
  Moldovan IBANs whose ISO 13616 check digits are valid. Dates, card numbers and toll-free
  numbers are not taken for phones.
- Export masking hides the same phones and IBANs in every grouping; before, a phone written with
  dashes (`+373-60-000-001`) reached `results.json` unmasked.

## 1.0.0 (2026-11-20)
First stable release: text and agent testing, statistics, reports, CI gate and machine-readable
results are complete and documented, and a planned benchmark shows them on real models.

- `vespera init` asks what to test (a local model, an API, or a bot behind a web address), the mode
  and the gate rules, and writes a ready `vespera.yaml`. `--no-interactive` keeps the template.
- One-word targets: `vespera test qwen2.5:7b`, `openai:MODEL`, `openrouter:MODEL`,
  `lmstudio:MODEL`, `mock`. The prefix fills in the address and the key variable.
- `vespera runs` lists saved runs with model, mode, size and attack success rate with its interval.
- `--open` on `test`, `report` and `compare` opens the HTML page in the browser.

- Risk labels follow the OWASP Top 10 for LLM Applications **2026**: excessive agency is now
  LLM03 (was LLM06), system prompt leakage is LLM08 "Hidden context exposure" (was LLM07).
  The report shows the edition (`OWASP LLM03:2026`).
- Breaking: `zero_tolerance` with the old ids LLM06 or LLM07 is refused, and the message names
  the 2026 id to use instead.
- Docs: first planned benchmark (qwen2.5:7b and llama3.1:8b, agent mode, 10 repeats) with the
  plan written before the runs (`docs/benchmark/`), a test strategy (`docs/test-strategy.md`) and
  a rewritten README with the results.
- Benchmark reports published on GitHub Pages: <https://yaniquee26.github.io/vespera/>.
- Tests: every test runs in its own empty folder, so a stray `vespera.yaml` cannot change results.
- Report: a clear sentence when every reply is too short to tell its language; the footer names the
  `vespera test` command that reproduces the run.
- Internal: text and agent runs share one code path in the command line module.
- Package metadata: keywords, classifiers and project links.

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
