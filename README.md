# Vespera

![CI](https://github.com/yaniquee26/vespera/actions/workflows/ci.yml/badge.svg)

Evidence-based reliability testing for language models in Russian, Romanian and English.
Runs locally on Ollama, nothing leaves your machine.

Status: early development (v0.3: run scenarios on a local model and check the answers). See `SPEC.md` for the design and `TASKS.md` for the plan.

## Development setup (Windows, PowerShell)

```
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
ruff check .
mypy src
pytest
```

## Usage

```
vespera doctor                 # is the environment ready?
vespera validate               # is the scenario file valid?
vespera run --limit 6          # ask the first 6 cases to a local Ollama model
vespera run                    # ask all cases (default model: gemma3:4b)
vespera show                   # print the saved answers of the latest run
vespera check                  # find violations in the latest run
```

`vespera run --mock` uses a fake model, so everything can be tried without a GPU.

## Known limitations

- Romanian prompts were drafted with AI assistance and have not been reviewed by a native speaker.
- Checks are simple rules, not AI judges. They can miss a leak or raise a false alarm
  (for example, a model that mentions a forbidden word while refusing it).
  Every verdict keeps an excerpt of the reply so a person can review it.
- With a single repeat the numbers are observations, not statistics.