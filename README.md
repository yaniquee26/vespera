# Vespera

![CI](https://github.com/yaniquee26/vespera/actions/workflows/ci.yml/badge.svg)

Evidence-based reliability testing for language models in Russian, Romanian and English.
Runs locally on Ollama, nothing leaves your machine.

Status: early development (v0.1, project skeleton). See `SPEC.md` for the design and `TASKS.md` for the plan.

## Development setup (Windows, PowerShell)

```
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
ruff check .
mypy src
pytest
```
