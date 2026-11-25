# ADR-0001: Verdicts come from deterministic rules, not from an LLM judge

**Status:** Accepted
**Date:** 2026-11-25 (recorded; the decision was taken when the project started)
**Deciders:** maintainers

## Context

Every attempt needs a verdict: did the attack work, did the bot leak data, did the agent call a
tool it must not call? The verdict is the oracle of the whole test bench. If it is wrong or
unstable, every rate, interval and gate decision built on it is wrong too.

Forces:
- the same reply must always get the same verdict, or repeated runs cannot be compared;
- every verdict must point to the exact text or tool call that caused it, so a person can check it;
- the replies under test are hostile input: they may contain instructions aimed at whoever reads
  them;
- runs happen locally, often on one GPU, and should cost nothing extra.

## Decision

Verdicts are produced by simple, readable rules:
- canary tokens planted in the system prompt;
- marker strings for direct injection;
- patterns for personal data (Moldovan phones, IDNP, IBAN MD);
- refusal phrases;
- checks on the agent's tool-call journal (a forbidden tool, a secret in tool arguments, an
  expected tool not called).

Each check documents its known false alarms and misses in its docstring and in the
[test strategy](../test-strategy.md). Attack rates are read as a lower bound.

## Options considered

### Option A: LLM as a judge
| Dimension | Assessment |
|---|---|
| Complexity | Medium: a judge prompt, parsing, retries |
| Cost | A second model call per attempt |
| Reproducibility | Low: the judge itself varies between runs |
| Explainability | Low: "the judge said so" |

**Pros:** catches paraphrased leaks and subtle compliance.
**Cons:** non-deterministic; the judge reads the hostile reply and can itself be injected; adds
cost and latency; its error rate is unknown and hard to measure.

### Option B: Deterministic rules (chosen)
| Dimension | Assessment |
|---|---|
| Complexity | Low: each check is a short function with unit tests |
| Cost | None |
| Reproducibility | Full: same reply, same verdict |
| Explainability | Full: the excerpt that matched is stored as evidence |

**Pros:** reproducible, traceable, cheap, cannot be talked out of a verdict.
**Cons:** misses leaks phrased in an unexpected way; raises false alarms when a refusal quotes
the forbidden word.

### Option C: Embedding similarity to known bad answers
| Dimension | Assessment |
|---|---|
| Complexity | Medium: an embedding model and thresholds |
| Cost | Low to medium |
| Reproducibility | High for a fixed model |
| Explainability | Low: a similarity score has no excerpt |

**Pros:** tolerant to paraphrase.
**Cons:** the threshold is arbitrary; it needs a model; a score is hard to defend in a report.

## Trade-off analysis

The bench exists to give numbers that can be defended. A verdict that changes between runs, or
that cannot be traced to text, defeats that purpose, however clever it is. Rules give up recall
on paraphrases in exchange for reproducibility and evidence. That loss is made visible: misses are
documented, and rates are presented as a lower bound.

## Consequences

- Easier: repeating runs, comparing models and languages, reviewing every finding by hand.
- Harder: catching creative leaks; each new kind of leak needs a new rule and its tests.
- To revisit: the accuracy of the rules has to be measured, not assumed (see Action items).

## Action items

1. [x] Document known false alarms and misses for every check.
2. [ ] Measure precision and recall of each check on hand-labelled replies (Review mode, F5).
