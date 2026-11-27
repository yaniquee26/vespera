# ADR-0002: Wilson 95% intervals, and "conclusive" only when intervals do not overlap

**Status:** Accepted
**Date:** 2026-11-25 (recorded; the decision was taken when the statistics were built)
**Deciders:** maintainers

## Context

Runs are small: a few dozen cases, three to ten repeats each, often on a local GPU. Rates near 0%
or 100% are common (an agent that leaks 10 times out of 10). The report must say how sure it is,
and comparisons between languages or models must not announce noise as a finding.

## Decision

- Every rate is shown with its sample size and a Wilson score interval at 95%.
- A difference between two rates is called **conclusive** only when their intervals do not
  overlap. Everything else is "not conclusive", however large it looks.
- Hypotheses and this decision rule are written down before a benchmark is run.

## Options considered

### Option A: Raw percentages
| Dimension | Assessment |
|---|---|
| Complexity | None |
| Honesty at small samples | Poor: 2/3 and 200/300 look the same |

### Option B: Normal (Wald) interval
| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Honesty at small samples | Poor: zero width at 0% and 100%, can leave [0, 1] |

### Option C: Wilson score interval (chosen)
| Dimension | Assessment |
|---|---|
| Complexity | Low: a closed formula |
| Honesty at small samples | Good, including at 0% and 100% |
| Explainability | Good: "the true rate is likely in this range" |

### Option D: Clopper-Pearson exact interval
| Dimension | Assessment |
|---|---|
| Complexity | Low with a library |
| Honesty at small samples | Good, but deliberately conservative (wider than needed) |

### Option E: Hypothesis tests with p-values (two-proportion z-test, Fisher)
| Dimension | Assessment |
|---|---|
| Power | Higher: finds smaller real differences |
| Explainability | Lower for a non-statistical reader; needs care with many comparisons |

## Trade-off analysis

Wilson keeps intervals honest at the sample sizes this tool actually sees, without the extra
width of the exact interval. The non-overlap rule is stricter than a usual 5% test, so it can
miss a real but small difference. That direction of error is preferred: the bench may stay
silent about a gap, but it should not claim one that is noise.

## Consequences

- Easier: one rule explains every "conclusive" mark in reports, the README and the landing page.
- Harder: small real differences need more repeats before they are called conclusive.
- Known limit: repeats of the same case are not independent, yet the interval treats every
  attempt as one observation. A gap measured on a single scenario per language describes that
  scenario, not the language. Rates per scenario (F3) address this.
- No correction for many comparisons; the strict rule reduces, but does not remove, that risk.

## Action items

1. [x] Wilson intervals and the non-overlap rule in reports and comparisons.
   *Correction (2026-11-27):* this item used to say "and the gate". The gate does not use the
   interval: `--max-attack-rate` is compared with the measured share (the point estimate), so 1
   violation in 9 attempts passes a 30% limit although the upper end of the interval is about
   44%. Whether the gate should use the upper end is a separate decision, not taken.
2. [ ] Several wordings per category and language, and rates per scenario (F3).
