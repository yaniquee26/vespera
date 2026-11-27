## Vespera 1.1.0

A release about trust: the numbers Vespera reports are harder to get wrong, its first run shows
what it catches, and its documents say plainly what it cannot do.

**Live reports:** https://yaniquee26.github.io/vespera/

### Try it in a minute

No model, no graphics card and no Git needed:

```
pip install https://github.com/yaniquee26/vespera/archive/refs/tags/v1.1.0.zip
vespera init --no-interactive
vespera test demo --open
```

`demo` is a fake bot with weaknesses planted on purpose. It holds in English and gives in in
Russian and Romanian, so one run shows prompt leaks, invented `+373` phones, IBANs and IDNPs,
obeyed injections, a language gap and a failed gate. Its report says that it describes a fake bot.

### Fixed: results that looked right but were not

- **The gate no longer passes a test that mostly never happened.** Any planned attempt without a
  reply (a technical error, or a run that stopped early) makes the result *inconclusive*. A real
  violation still fails the gate.
- **Resuming a run can no longer corrupt it.** A resume must ask exactly the questions the run
  started with, in the same order; otherwise it is refused before anything is written.
- **Exit code 2 now means a failed gate and nothing else.** A mistyped flag used to end with 2 too,
  so in CI it looked like a failed security gate. Usage errors now end with 1, as documented.
- Vespera needs Typer 0.18 or newer: older Typer releases crash, or stop checking required
  arguments, with the Click versions pip installs today (checked on Typer 0.12 to 0.29).

### New

- `vespera test demo`, the demo bot above.
- `vespera test --resume N` continues an interrupted run with the settings of `vespera.yaml`.
- Every first error names a next step that works: `vespera init` before the first test, how to
  list runs, how to resume, "Is Ollama running?", one line instead of a traceback for a file that
  is not a database.
- **Vespera Studio (preview)**: `vespera ui --workspace .` opens a local web app (127.0.0.1 only)
  with the runs of a project folder, a page per run (categories with interval bars, the language
  gap, findings with highlighted evidence, the snapshot), masked downloads, and saved targets with
  **Test connection**. Starting runs, the gate and comparisons stay in the command line for now.
- Reproducibility: for a local Ollama model, the snapshot records the digest of the weights, and a
  run cannot be resumed on different weights.
- Rate limits: HTTP targets retry after 429 and 503, honour `Retry-After`, and can be paced with
  `request_interval`; the waiting is left out of the measured latency.
- One default model, `qwen2.5:7b`, everywhere.

### Said honestly now

- Repeats of the same question are not independent, so intervals can look narrower than the real
  uncertainty (README, report, site).
- "Attack rates are a lower bound" is a reasoned guess, not a measurement.
- `--max-attack-rate` is compared with the measured share, not with the upper end of the interval.
- The check catalog lists each check's known false alarms and misses, with examples.

### Install

```
pip install https://github.com/yaniquee26/vespera/archive/refs/tags/v1.1.0.zip
# with Studio:
pip install "vespera[ui] @ https://github.com/yaniquee26/vespera/archive/refs/tags/v1.1.0.zip"
```

Full changelog: [CHANGELOG.md](https://github.com/yaniquee26/vespera/blob/main/CHANGELOG.md)
