# Architecture decision records

Each record explains one decision: the situation, the options that were considered, the choice
and what it costs. A record is never rewritten after it is accepted; a later decision that
changes it gets a new number and marks the old one as superseded.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-rule-based-oracles.md) | Verdicts come from deterministic rules, not from an LLM judge | Accepted |
| [0002](0002-wilson-intervals-and-conclusive-rule.md) | Wilson 95% intervals, and "conclusive" only when intervals do not overlap | Accepted |
| [0003](0003-local-web-studio.md) | A local web app (Vespera Studio) built with FastAPI, Jinja2 and htmx | Accepted |
| [0004](0004-studio-security-model.md) | Security model of the local web app | Accepted |
| [0005](0005-foundation-before-studio-and-feature-slices.md) | A shared service layer first, then Studio, then features as vertical slices | Accepted |

Format: Context, Decision, Options considered, Trade-off analysis, Consequences, Action items.
