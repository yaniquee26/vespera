# ADR-0003: A local web app (Vespera Studio) built with FastAPI, Jinja2 and htmx

**Status:** Accepted
**Date:** 2026-11-25
**Deciders:** maintainers

## Context

Vespera is a command-line tool. That suits CI, but many people who test chatbots work with tools
like Postman: they want to connect a bot, send a message, start a run and read the results
without a terminal. The concepts of the bench (targets, scenario packs, rule checks, intervals,
the gate) are also easier to understand when they are visible.

Constraints:
- everything stays local: no cloud service, no account;
- API keys are never typed into or stored by the app, only the names of environment variables;
- the project is Python; one language is easier to maintain and to test;
- the existing HTML report must stay a standalone file that can be sent to someone.

## Decision

A local web app, **Vespera Studio**, started with `vespera ui` and served on `127.0.0.1`:
- server: FastAPI; pages: Jinja2 templates; interactivity: htmx 2.x, vendored into the package
  (no CDN);
- shipped as an optional extra, `pip install vespera[ui]`; the core tool does not depend on it;
- interface text in English;
- pages reuse the existing data objects (`ReportData`, `CompareData`), so every number has one
  source;
- the standalone HTML report stays as it is.

## Options considered

### Option A: React + TypeScript single-page app with a FastAPI API
| Dimension | Assessment |
|---|---|
| Complexity | High: two toolchains, a build step, Node.js |
| Look and interactivity | Excellent |
| Testability | Good, with a second test stack |
| Fit with the codebase | Low: a second language |

### Option B: FastAPI + Jinja2 + htmx (chosen)
| Dimension | Assessment |
|---|---|
| Complexity | Low to medium: server-rendered pages, small partial updates |
| Look and interactivity | Good with a deliberate design system |
| Testability | Good: route tests with FastAPI's test client, browser tests with Playwright for Python |
| Fit with the codebase | High: one language, reuses Jinja2 |

### Option C: Streamlit or Gradio
| Dimension | Assessment |
|---|---|
| Complexity | Very low |
| Look and interactivity | Looks like a data-science demo; layout control is limited |
| Testability | Weak for browser tests |

### Option D: Desktop GUI (Qt, Tkinter)
| Dimension | Assessment |
|---|---|
| Complexity | Medium to high, platform quirks |
| Look and interactivity | Dated unless heavily styled |
| Testability | Weak |

## Trade-off analysis

Option A gives the richest interface, but doubles the toolchain for a tool whose screens are
mostly tables, forms and evidence views. Option B delivers those screens with one language and
keeps the test stack uniform (pytest for routes, Playwright for Python for the browser). Options C
and D trade either professional look or testability.

## Consequences

- Easier: CLI and Studio share one service layer (ADR-0005); browser tests use the same language
  as the rest of the suite.
- Harder: very rich client-side interactions (drag and drop, live charts) need more effort than
  in a single-page app.
- New dependencies, only in the `ui` extra: `fastapi`, `uvicorn`, `python-multipart`; for
  development also `playwright` and `pytest-playwright`.
- To revisit: if the interface grows beyond forms, tables and evidence views, reconsider Option A.

## Action items

1. [ ] Service layer and registries (S1, S2).
2. [ ] App skeleton, design system and security middleware (S3, ADR-0004).
3. [ ] Pages: Targets and Playground, Runs, Run detail, Library, Checks, How it works, Compare,
       New run (S4–S7).
4. [ ] Browser tests with Page Objects in CI (S8).
