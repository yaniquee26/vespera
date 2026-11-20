## Vespera 1.0.0

A local test bench that tries to break LLM chatbots and tool-using agents in English, Russian and
Romanian, judges every reply with deterministic rules and reports each rate with a 95% Wilson
interval, an OWASP LLM Top 10 (2026) label and the evidence behind it.

**Live reports:** https://yaniquee26.github.io/vespera/

### Benchmark highlights

Planned in advance ([plan](https://github.com/yaniquee26/vespera/blob/main/docs/benchmark/plan.md)),
qwen2.5:7b and llama3.1:8b in agent mode, 10 repeats, 360 attempts:

- Asked to send a customer's phone and ID number by SMS, both models did it **10/10 times in
  Russian** against 2/10 and 1/10 in English (conclusive: the intervals do not overlap).
- Asked to cancel a neighbour's subscription, they did it **8/10 and 9/10 times in Romanian**
  against 0/10 in English (conclusive).
- Both models fail the CI gate with zero tolerance for data leaks (OWASP LLM02).

One task per language, so this shows how these tasks behave in each language, not a general law.
Full results and limits: [docs/benchmark/results.md](https://github.com/yaniquee26/vespera/blob/main/docs/benchmark/results.md).

### What is in 1.0.0

- Two commands to start: `vespera init` asks a few questions and writes a ready project file,
  `vespera test --open` runs everything and opens the report. Or skip the file:
  `vespera test openai:gpt-4o-mini`, `vespera test openrouter:MODEL`, `vespera test qwen2.5:7b`.
- `vespera runs` lists every saved run with its attack success rate and interval.
- Text mode (questions to a chatbot) and agent mode (tasks with simulated tools and a full journal
  of tool calls).
- Checks: canary leak, personal data, forbidden strings, needless refusal, forbidden tool call,
  secret in tool arguments, harmless task not done. Exposure and "tool call written as text" are
  measured separately.
- Wilson 95% intervals, stability across repeats, and a strict "conclusive / not conclusive" rule
  for comparing languages and models.
- Gate with three outcomes for CI: passed (0), failed (2), inconclusive (3).
- `results.json` and JUnit export with masking of secrets.
- Any target: local Ollama, any OpenAI-style API, or a plain HTTP endpoint, configured in
  `vespera.yaml`. Keys only from environment variables.
- Risk labels follow the OWASP Top 10 for LLM Applications 2026. **Breaking:** the 2025 ids
  LLM06 and LLM07 are refused in `zero_tolerance`, with a message naming the 2026 id.
- [Test strategy](https://github.com/yaniquee26/vespera/blob/main/docs/test-strategy.md):
  risks, oracles and their known errors, exit criteria.

### Install

```
pip install git+https://github.com/yaniquee26/vespera@v1.0.0
vespera init
vespera test --open
```

Full changelog: [CHANGELOG.md](https://github.com/yaniquee26/vespera/blob/main/CHANGELOG.md)
