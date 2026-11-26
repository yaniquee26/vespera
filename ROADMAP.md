# Roadmap

Where Vespera goes after 1.0.0. The order is **foundation → Studio → features in slices**: a
shared service layer with registries for targets, scenario packs and checks comes first, the
local web app (Vespera Studio) is built on top of it, and every later feature then ships as one
slice: engine, Studio page and tests together. The reasons are recorded in
[ADR-0005](docs/adr/0005-foundation-before-studio-and-feature-slices.md).

Labels: **S** — foundation and Studio, **F** — features, **R** — benchmarks (planned experiments
on real models, with the hypotheses written first), **D** — documentation site.

**Order:** S2 → S3 → S4 → S5 → F5 → S6 → S7 → F0 → S8 → S9 → F1 … F8, R2 and R3 when their
features are ready, then D.

Each item is small enough to finish and test in one sitting. A box is ticked only when `ruff`,
`mypy` and `pytest` are green and CI passes.

## Done

- **1.0.0:** core (contracts, providers, runner, rule checks, Wilson statistics, SQLite, HTML
  report, comparison), any target (Ollama, OpenAI-style APIs, HTTP endpoints), agent mode
  (simulated tools, journal, hidden orders in tool output, exposure), OWASP LLM Top 10 2026
  labels, three-outcome CI gate, `results.json` and JUnit with masking, `vespera.yaml`,
  `vespera init` / `vespera test`, test strategy.
- **R1** (19 November 2026): qwen2.5:7b and llama3.1:8b in agent mode, 10 repeats; reports on
  GitHub Pages ([results](docs/benchmark/results.md)).
- **After 1.0.0:** README and landing page in English, Romanian and Russian with a test that keeps
  their numbers identical; clean repository root (`examples/`, `docs/history/`); CI on Ubuntu and
  Windows with Python 3.12 and 3.13, a coverage floor and JUnit artefacts; Moldovan personal data
  in the checks (local phone formats, `+373` in any grouping, IBAN MD with ISO 13616 check digits)
  and export masking that uses the same patterns; architecture decision records.

## Foundation

- [x] S1 Service layer (`vespera/services/`: project settings, API keys, results and gate) with
      typed errors, so the command line and Studio call the same code. Registries
      (`vespera/catalog.py`) for target types, scenario packs, attacks and checks, each with a
      plain-language description; tests keep them complete. CLI output stays byte for byte the
      same.
- [x] S2 Runs in the service layer (start, resume, check, list); `Storage` as a context manager
      with a concurrent mode (WAL, busy timeout); a workspace folder for Studio, chosen by
      `--workspace`, then `VESPERA_HOME`, then `~/Vespera`; a CI step that runs the installed
      `vespera` command against the mock target.
- [ ] S2b Rate limits: a request that gets HTTP 429 or 503 is retried with growing pauses and
      honours `Retry-After`; an optional pause between requests per target. Retries are counted
      and shown, and never counted as a failure of the model.

## Vespera Studio (local web app, `vespera ui`)

- [ ] S3 App skeleton: optional `vespera[ui]` extra, FastAPI + Jinja2 + htmx, design system with
      light and dark themes, navigation, security middleware ([ADR-0004](docs/adr/0004-studio-security-model.md)),
      Settings page with environment checks, the workspace location and its size.
- [ ] S4 Targets and Playground: saved connections to bots and agents, **Test connection** sends
      one message and shows the real request (key masked), the reply and the latency.
- [ ] S5 Runs and Run detail: categories with OWASP labels and interval bars, the language gap
      (EN / RO / RU), findings with evidence and agent journals, Moldovan personal data
      highlighted, snapshot, downloads.
- [ ] S6 Library (scenario packs, EN / RO / RU side by side), Checks catalog (how each rule
      decides and where it is wrong), How it works, Gate panel, Compare.
- [ ] S7 New run wizard and background runs: live progress, cancel and resume, automatic checks.
- [ ] S8 Browser tests with Playwright (Page Objects) in a separate CI job; screenshots for the
      docs are taken by the same tests.
- [ ] S9 README and landing page about Studio, `SECURITY.md`, CHANGELOG, release 1.1.0.

## Features, one slice at a time (engine + Studio + tests)

- [ ] F0 Any bot: each check says what it needs to work (nothing, a token planted in the system
      prompt, a secret planted in the data, the agent's tool journal), so Vespera can say which
      checks apply to a bot seen only from outside (black box) and which need access to its
      setup (white box). A universal pack that works on any bot without knowing its prompt. A
      bot profile (name, what it does, a few normal questions, what it must never do) that fills
      a template pack in English, Romanian and Russian. Later: a browser target that drives a
      chat widget with Playwright, for bots without an API. A third-party bot is tested only with
      the written permission of its owner.
- [ ] F1 Bank assistant pack for an invented bank in EN, RO and RU (transfer without
      confirmation, another customer's balance, ID number sent by SMS, someone else's card,
      order hidden in a statement). Invented IDNPs carry a control digit (7-3-1 rule), and the
      evidence says whether a leaked 13-digit number has one; the rule only labels evidence and
      never clears a number, because no official specification for the IDNP was found. A data
      protection note (Moldovan Law 195/2024) for personal data categories, as information only.
- [ ] F2 How people really write in Moldova: Russian in Latin letters, Romanian without
      diacritics; ş/ș and ţ/ț treated as the same letter in the checks.
- [ ] F3 Three to five wordings per category and language, and rates per scenario, so a language
      gap is conclusive only when it holds across different scenarios.
- [ ] F4 Regression baseline: "worse, better or no conclusive change" against a saved run, in
      the CLI (`--baseline`) and in Studio.
- [ ] F5 Review mode (right after S5): a person marks verdicts as right or wrong in Studio,
      including a sample of passed ones, and Vespera reports the precision and recall of its own
      checks with intervals. The labelled replies become a golden set that every change to a
      check is scored against.
- [ ] F6 Poisoned tool descriptions (MCP-style tool poisoning).
- [ ] F7 A demo bank bot (FastAPI) with RAG attacks: poisoned document, another customer's
      document, citations; API tests and browser tests against it.
- [ ] F8 Multi-turn attacks.

## Benchmarks

- [ ] R2 The bank assistant pack (F1) with several wordings (F3) on local models, plan first.
- [ ] R3 A positive control for the bench itself: a model against its abliterated version (the
      same weights with the refusal behaviour removed). The abliterated model has to fail more
      often; if Vespera does not show that, the checks are not sensitive enough. It also shows
      how much safety comes from the model's training and how much from the system prompt, in
      each language. Scenarios stay about injection and leaks.

## Documentation site

- [ ] D1–D3 A documentation site (MkDocs Material): getting started, concepts, guides, Studio
      tour with screenshots, reference for the CLI, `vespera.yaml` and `results.json`.

## Later

- Native-speaker review of the Romanian and Russian scenarios.
- SQL cookbook over the Vespera database, Docker image, test plan and traceability matrix,
  mutation testing of the checks.
- Release on PyPI under a free name, a GitHub Action, a container image, a Windows installer.
- A 30–60 second demo recording; an article about the language gap.
