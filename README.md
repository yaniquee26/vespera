# Vespera

![CI](https://github.com/yaniquee26/vespera/actions/workflows/ci.yml/badge.svg)

Evidence-based reliability testing for language models in Russian, Romanian and English.
Runs locally on Ollama by default, nothing leaves your machine. Can also test any bot or model
that speaks the OpenAI chat API (see "Testing another model or bot").

Status: early development (v0.5: run, check, statistics and an HTML report with proof). See `SPEC.md` for the design and `TASKS.md` for the plan.

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
vespera report                 # build a single HTML report page (open it in a browser)
```

`vespera run --mock` uses a fake model, so everything can be tried without a GPU.

## Known limitations

- Romanian prompts were drafted with AI assistance and have not been reviewed by a native speaker.
- Checks are simple rules, not AI judges. They can miss a leak or raise a false alarm
  (for example, a model that mentions a forbidden word while refusing it).
  Every verdict keeps an excerpt of the reply so a person can review it.
- With a single repeat the numbers are observations, not statistics.


## Testing another model or bot

Any server with an OpenAI-style `/chat/completions` endpoint works (OpenAI, OpenRouter,
LM Studio, vLLM, and Ollama itself at `http://localhost:11434/v1`).

```
# PowerShell: put the key in an environment variable, never in a file
$env:OPENAI_API_KEY = "sk-..."
vespera run --provider openai --base-url https://api.openai.com/v1 --model gpt-4o-mini --repeats 3
vespera check
vespera report
```

The key is read from the environment, is never saved to the database or the report, and a
local address needs no key. Put your bot's own system prompt in `system_prompt.txt` and add a
line with a secret code such as `CANARY-ABC123` so leaks can be detected.
