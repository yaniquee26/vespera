"""Example files shipped inside the package, so a fresh install has something to run."""

from importlib.resources import files

BUNDLED_FILES = ("scenarios_v1.yaml", "system_prompt.txt")
AGENT_FILES = ("agent_scenarios_v1.yaml", "agent_system_prompt.txt")
CONFIG_TEMPLATE = "vespera.yaml"  # a template for `vespera init`, not a scenario/prompt pair


def read_bundled(name: str) -> str:
    if name not in (*BUNDLED_FILES, *AGENT_FILES, CONFIG_TEMPLATE):
        raise ValueError(f"no bundled file called {name}")
    return (files("vespera") / "data" / name).read_text(encoding="utf-8")
