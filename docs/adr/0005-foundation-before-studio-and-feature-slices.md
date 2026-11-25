# ADR-0005: A shared service layer first, then Studio, then features as vertical slices

**Status:** Accepted
**Date:** 2026-11-25
**Deciders:** maintainers

## Context

Two things need to happen: Vespera needs a visual interface (ADR-0003), and its engine needs to
grow: new scenario packs, new checks, regression baselines, review of verdicts, RAG and
multi-turn attacks. The order matters.

Today the logic that starts runs, resolves targets, reads keys and builds gate decisions lives in
`cli.py` (about 1,170 lines), mixed with console output (`typer.echo`, `typer.Exit`). A web app
cannot call that code without duplicating it.

## Decision

1. **Foundation first.** Move the logic out of `cli.py` into `vespera/services/`, with typed
   errors in place of console exits. Add three registries, each with a plain-language description
   of its entries:
   - **targets** — how Vespera talks to a bot or an agent;
   - **scenario packs** — sets of attacks with their languages and categories;
   - **checks** — how each rule decides and where it is known to be wrong.
   Add a workspace folder so Studio works from any directory. The CLI keeps its exact output.
2. **Studio second,** built only on the services and registries.
3. **Features as vertical slices:** each new capability ships at once as engine code, a Studio
   page or panel, and tests.

## Options considered

### Option A: Features first, interface later
| Dimension | Assessment |
|---|---|
| Rework | High: the interface later has to absorb many features at once |
| Visibility | Low: the system stays hard to see and explain until the end |

### Option B: Interface first, on top of the current CLI code
| Dimension | Assessment |
|---|---|
| Rework | High: logic duplicated between CLI and web, then changed twice per feature |
| Speed to first screen | Fast |

### Option C: Foundation, then Studio, then slices (chosen)
| Dimension | Assessment |
|---|---|
| Rework | Low: new targets, packs and checks appear in Studio through the registries |
| Speed to first screen | Slower by the size of the refactor |
| Risk | The refactor must not change CLI behaviour; existing CLI tests guard it |

## Trade-off analysis

Option C delays the first screen by the cost of the refactor, but every later step is cheaper.
The registries turn "add a feature" into "add an entry": a new target type, a new pack or a new
check is described once and shown everywhere. Vertical slices keep the engine and the interface
from drifting apart.

## Consequences

- Easier: one code path for CLI and Studio; adding a pack, a target type or a check; testing the
  logic without the interface.
- Harder: the first weeks show little visible change; the refactor needs care to keep CLI output
  byte for byte the same.
- Two existing tests that replace provider classes on the `cli` module will point to the new
  module when the provider factories move; their behaviour stays the same.

## Action items

1. [ ] S1: `services/errors`, `project`, `targets`, `results`; registries.
2. [ ] S2: `services/runs`; `Storage` as a context manager with a concurrent mode; workspace.
3. [ ] S3–S9: Studio (see the [roadmap](../../ROADMAP.md)).
4. [ ] F1–F8: features as slices.
