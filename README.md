# Vespera

![CI](https://github.com/yaniquee26/vespera/actions/workflows/ci.yml/badge.svg)

Evidence-based security and reliability testing for chatbots and tool-using agents, in Russian,
Romanian and English. It tries to break a bot (hidden orders, secret leaks, forbidden actions),
decides with simple rules, and shows every finding with its proof, confidence intervals and an
OWASP LLM Top 10 label. Runs locally on Ollama by default, nothing leaves your machine. Any model
or bot can be the target: a local model, an OpenAI-style API, or a plain HTTP endpoint.

Status: early development, version 0.4 in progress. See `SPEC.md` for the design and `TASKS.md`
for the plan.

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


## Quick start with your own bot

```
pip install git+https://github.com/yaniquee26/vespera
vespera init       # writes vespera.yaml, example scenarios and a system prompt
# 1. open vespera.yaml and describe your target (model, OpenAI-style API or any HTTP bot)
# 2. put YOUR bot's system prompt in system_prompt.txt, keep a CANARY-XXXXXX line
vespera test       # run + check + report + gate, driven by vespera.yaml
```

`vespera.yaml` replaces a long command line: the target, the number of repeats, what counts as
acceptable (the gate) and where the machine-readable results go. Flags still win over the file.
Unknown keys are refused so a typo cannot pass silently, and a key is never written in the file:
only the name of the environment variable that holds it.

```yaml
target: {type: http, url: https://my-bot.example.com/chat, reply_field: answer,
         api_key_env: MY_BOT_KEY}
repeats: 3
gate: {max_attack_rate: 0.3, zero_tolerance: [LLM02]}
```

More: [connect your own bot](docs/connect-your-bot.md) and
[write your own scenario in 5 minutes](docs/write-a-scenario.md).

### Use as a gate in CI

A gate turns a report into a decision. It has three outcomes, not two:

| Outcome | Exit code | Meaning |
|---|---|---|
| passed | 0 | every rule held and the tests really exercised the bot |
| failed | 2 | a rule was broken (a real violation always wins) |
| inconclusive | 3 | nothing was broken, but the data cannot show the bot is safe |

```
vespera test --max-attack-rate 0.3                  # run, check, report and decide
vespera gate 12 --zero-tolerance LLM02,LLM03        # decide on a run that already exists
```

- `--max-attack-rate` fails when more than that share of attacks succeeded.
- `--zero-tolerance` takes OWASP ids (`LLM02`) or category names (`pii_leak`). One violation fails
  the gate, however small the share. Example: personal data must never leak.
- `--min-exposure` (agent runs, default 0.5): if the poisoned tool was opened in less than this
  share of attempts, the result is inconclusive. "No violation" then proves nothing.

"Passed" means no violation was seen in the attempts that were judged. It is not a proof of safety.

### Machine-readable results

```
vespera test --json results.json --junit junit.xml
vespera export 12 --json results.json --junit junit.xml
```

`results.json` (schema `vespera.results/1`) holds the numbers, intervals, risk labels and the gate
decision. The JUnit file has one test case per case, so CI systems can draw it. Leaked values,
canary tokens and long numbers are masked by default because CI logs are widely visible;
`--reveal` turns that off. The HTML report always keeps the full proof. `vespera check --mask`
hides the proof in the console. Masking recognises canaries, long numbers and the secrets named in
the scenarios, not names or free text.

### Risk labels

Every category carries an OWASP Top 10 for LLM Applications (2026) id and a severity:

| Category | OWASP | Severity |
|---|---|---|
| Order hidden in tool output | LLM01 | Critical |
| Secret sent out through a tool | LLM02 | Critical |
| Personal data leak | LLM02 | Critical |
| Direct / indirect injection, jailbreak | LLM01 | High / High / Medium |
| System prompt leak | LLM08 | High |
| Action the agent must not take | LLM03 | High |
| Needless refusal, harmless task not done | none | Medium |

The severity is a sensible default, not a measurement. Teams can disagree with it.


## Testing agents (bots that call tools)

An agent can be talked into acting: a hidden order inside data that a tool returns, a request to
send a secret somewhere, an action the customer is not allowed to ask for. Vespera plays tasks
with simulated tools (nothing real is ever sent, opened or cancelled) and records the whole
journal: what the agent said, which tools it called, with which arguments.

```
vespera init --agents                 # also copies the agent scenarios and system prompt
vespera run-agent --model qwen2.5:7b  # needs a model that can call tools (gemma3 cannot)
vespera check
vespera report                        # every finding is proven by the journal of the attempt
```

Checks on the journal: `forbidden_tool_call`, `secret_not_in_tool_args`, `expected_tool_called`
(the last one measures a harmless task that was not done). Two measurements are not verdicts:
whether the hidden order really reached the agent, and whether the agent wrote a tool call as plain
text instead of making a real call (small models often do). Works with any OpenAI-style server:
`--base-url https://.../v1` with the key in `OPENAI_API_KEY`.

Known limits: a tool call written as plain text is counted as "not called" (and reported
separately); tools are stand-ins, so this measures the decision to call, not real-world damage.
