# Roadmap

Where Vespera goes after 1.0.0. Each item is small enough to finish and test in one sitting.
The checkbox is ticked only when `ruff`, `mypy` and `pytest` are green.

## Done (up to 1.0.0)

- Core: contracts, providers, runner, rule checks, Wilson statistics, SQLite, HTML report,
  comparison of two runs.
- Any target: Ollama, OpenAI-style APIs, plain HTTP endpoints.
- Agent mode: simulated tools, full journal, hidden orders in tool output, exposure, language drift.
- OWASP LLM Top 10 2026 labels and severities, three-outcome CI gate, `results.json` and JUnit
  with masking, `vespera.yaml`, `vespera init` / `vespera test`, one-word targets.
- Benchmark R1 (qwen2.5:7b and llama3.1:8b, agent mode, 10 repeats, hypotheses written first),
  reports on GitHub Pages, test strategy.

## Phase 0. Documentation and repository

- [x] 0.2 README: a short summary, a table of the testing practice behind the tool, short
      `README.ro.md` and `README.ru.md`; a test keeps the benchmark numbers identical in all three.
- [ ] 0.3 A 30–60 second demo recording on the mock target: `init` → `test` → report → gate.
- [x] 0.4 Landing page in English, Romanian and Russian with a shared stylesheet; a test keeps
      its numbers and language links in line with the README.
- [x] 0.5 Clean repository root: the original spec moved to `docs/history/`, example scenarios
      and prompts moved to `examples/` and checked against the copies shipped in the package.
- [ ] 0.6 CI matrix (Ubuntu and Windows, Python 3.12 and 3.13), coverage threshold, JUnit artefact.

## Phase 1. Moldovan edition

- [ ] 1.1 Moldovan personal data in the checks: IBAN MD with its mod-97 check, IDNP with its
      control digit, local phone formats (`069 123 456`, `+373 69 123456`).
- [ ] 1.2 Bank assistant scenarios for an invented bank in EN, RO and RU: transfer without
      confirmation, another customer's balance, ID number sent by SMS, blocking someone else's card.
- [ ] 1.3 How people really write in Moldova: Russian in Latin letters, Romanian without
      diacritics; ş/ș and ţ/ț treated as the same letter in the checks.
- [ ] 1.3b More scenarios (3–5 wordings per category and language) and rates per scenario, so a
      language gap is conclusive only when it holds across different scenarios.
- [ ] 1.4 Data protection note in the report for personal data categories (Moldovan Law 195/2024,
      aligned with the GDPR), marked as information, not legal advice.
- [ ] 1.5 Native-speaker review of the Romanian and Russian scenarios.
- [ ] 1.6 Benchmark R2 "bank assistant": plan first, qwen2.5:7b and llama3.1:8b, results page.

## Phase 2. Full QA practice on a demo target

- [ ] 2.1 `demo/bank-bot/`: a small FastAPI assistant of an invented bank with FAQ lookup,
      simulated tools and a chat page; a deterministic stub answers in CI.
- [ ] 2.2 Test pyramid on the demo bot: API tests (pytest + httpx), UI tests (Playwright for
      Python, Page Object), Vespera on top as the AI-safety layer.
- [ ] 2.3 SQL cookbook: queries over the Vespera database that answer QA questions, each one tested.
- [ ] 2.4 Docker image and `compose.yaml` (demo bot + Vespera).
- [ ] 2.5 Team artefacts: test plan, test cases in CSV for TestRail/Xray, traceability matrix, bug
      reports in GitHub Issues for the benchmark findings, issue template, project board.
- [ ] 2.6 Code health: split `cli.py` into a package, one run-selection helper, `Storage` as a
      context manager, explicit config errors, shared HTTP client, stronger exit-code-only tests,
      `SECURITY.md`, `CONTRIBUTING.md`, Dependabot, pre-commit.

## Phase 3. Beyond the basics (pick two or three)

- [ ] 3.1 Accuracy of the checks themselves: precision and recall on a hand-labelled sample.
- [ ] 3.2 Regression baseline: `vespera test --baseline` says worse, better or no conclusive change.
- [ ] 3.3 Poisoned tool descriptions (MCP-style tool poisoning).
- [ ] 3.4 RAG attacks on the demo bot: poisoned document, another customer's document, citations.
- [ ] 3.5 Multi-turn attacks.
- [ ] 3.6 Mutation testing of the checks.
- [ ] 3.7 Release: PyPI under a free name, GitHub Action, container image.

## Phase 4. Writing about it

- [ ] Article in English, Russian and Romanian about the language gap.
- [ ] A one-page audit summary for readers outside engineering.
