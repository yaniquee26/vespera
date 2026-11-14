"""HTML template of the report (kept in Python so it ships with the package)."""

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vespera report: run {{ d.run_id }}</title>
<style>
:root {
  --bg: #f6f7f9; --card: #ffffff; --text: #1b1f27; --muted: #5d6675; --line: #dfe3ea;
  --bad: #b3261e; --bad-soft: #f9dedc; --ok: #1f7a4d; --ok-soft: #dcf2e6;
  --accent: #3a5bd9; --track: #e9ecf2; --warn: #8a5a00; --warn-soft: #fbefd5;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #12151b; --card: #1b2029; --text: #e8ebf1; --muted: #9aa3b2; --line: #2b323e;
    --bad: #ff8a80; --bad-soft: #3a1f1d; --ok: #6fd3a0; --ok-soft: #173326;
    --accent: #8ea4ff; --track: #2a303b; --warn: #f0c36a; --warn-soft: #34290f;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 16px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 980px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 1.7rem; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 { font-size: 1.2rem; margin: 36px 0 12px; }
p { margin: 0 0 12px; }
.sub { color: var(--muted); margin-bottom: 20px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
  padding: 16px 18px; margin-bottom: 14px; }
.guide { border-left: 4px solid var(--accent); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px;
  margin-bottom: 8px; }
.stat .num { font-size: 1.9rem; font-weight: 700; line-height: 1.1; }
.stat .lbl { color: var(--muted); font-size: 0.9rem; }
.scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.95rem; }
th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--line);
  vertical-align: middle; }
th { color: var(--muted); font-weight: 600; font-size: 0.82rem; text-transform: uppercase;
  letter-spacing: 0.04em; }
td.num, th.num { text-align: right; white-space: nowrap; }
.bar { position: relative; height: 14px; background: var(--track); border-radius: 7px;
  min-width: 160px; }
.bar .ci { position: absolute; top: 0; bottom: 0; background: var(--bad-soft);
  border-radius: 7px; }
.bar .pt { position: absolute; top: -2px; bottom: -2px; width: 3px; margin-left: -1px;
  background: var(--bad); border-radius: 2px; }
.bar.harmless .ci { background: var(--warn-soft); }
.bar.harmless .pt { background: var(--warn); }
.pill { display: inline-block; padding: 1px 9px; border-radius: 999px; font-size: 0.8rem; }
.pill.ok { background: var(--ok-soft); color: var(--ok); }
.pill.warn { background: var(--warn-soft); color: var(--warn); }
.muted { color: var(--muted); }
details { border-top: 1px solid var(--line); padding: 8px 0; }
details:first-of-type { border-top: 0; }
summary { cursor: pointer; }
details.proof { border-top: 0; padding-top: 0; }
.proof-toggle { color: var(--accent); font-weight: 600; padding: 4px 0 8px; }
summary .id { font-weight: 600; }
pre { white-space: pre-wrap; word-break: break-word; background: var(--bg);
  border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; margin: 6px 0 12px;
  font: 0.88rem/1.5 ui-monospace, "Cascadia Mono", Consolas, monospace; }
.excerpt { font-family: ui-monospace, "Cascadia Mono", Consolas, monospace; font-size: 0.88rem;
  background: var(--bad-soft); border-radius: 6px; padding: 1px 6px; }
.k { color: var(--muted); font-size: 0.82rem; text-transform: uppercase; letter-spacing: 0.04em; }
ul { margin: 0 0 8px; padding-left: 20px; }
li { margin-bottom: 6px; }
code { font: 0.9em ui-monospace, Consolas, monospace; background: var(--bg);
  padding: 1px 5px; border-radius: 5px; }
.snap td:first-child { color: var(--muted); width: 38%; }
footer { color: var(--muted); font-size: 0.85rem; margin-top: 36px; }
</style>
</head>
<body>
<main>
<h1>Vespera report</h1>
<p class="sub">Run {{ d.run_id }} &middot; model <strong>{{ d.snapshot.model }}</strong>
&middot; {{ d.snapshot.created_at }} &middot; status: {{ d.status }}</p>

<div class="card guide">
<p><strong>How to read this page.</strong> Vespera asked the model trap questions (attempts to make
it break its rules) in English, Russian and Romanian. A <em>violation</em> means the model broke a
rule in that attempt. For the harmless question, a violation means a needless refusal.</p>
<p class="muted">Each rate comes with a 95% interval (the pale range on the bar): the true rate
probably lies inside it. A wide range means there is not enough data yet. Every finding below can
be opened to see the exact question and the full reply that proves it.</p>
</div>

<div class="stats">
  <div class="card stat">
    {% if d.attack_rate %}
    <div class="num">{{ d.attack_rate.p | pct }}</div>
    <div class="lbl">attack success: {{ d.attack_rate.k }} of {{ d.attack_rate.n }} attempts
      (95% interval {{ d.attack_rate.low | pct }} to {{ d.attack_rate.high | pct }})</div>
    {% else %}<div class="num">&ndash;</div><div class="lbl">no attack attempts judged</div>{% endif %}
  </div>
  <div class="card stat">
    <div class="num">{{ d.snapshot.repeats }}</div>
    <div class="lbl">repeats per question (temperature {{ d.snapshot.temperature }})</div>
  </div>
  <div class="card stat">
    <div class="num">{{ d.flaky_cases }} / {{ d.repeated_cases }}</div>
    <div class="lbl">questions whose verdict changed between repeats (unstable)</div>
  </div>
  <div class="card stat">
    <div class="num">{{ d.technical_errors }}</div>
    <div class="lbl">attempts lost to technical errors (not judged)</div>
  </div>
</div>

<h2>Results by category</h2>
<div class="card scroll">
<table>
<thead><tr><th>Category</th><th class="num">Violations</th><th>Rate and 95% interval</th>
<th>Stability</th></tr></thead>
<tbody>
{% for row in d.rows %}
<tr>
  <td>{{ row.label }}</td>
  <td class="num">{{ row.rate.k }} / {{ row.rate.n }}<br>
    <span class="muted">{{ row.rate.p | pct }}</span></td>
  <td><div class="bar{% if row.harmless %} harmless{% endif %}"
       role="img" aria-label="{{ row.rate.p | pct }}, interval {{ row.rate.low | pct }} to {{ row.rate.high | pct }}">
    <div class="ci" style="left: {{ row.rate.low | w }}%; width: {{ (row.rate.high - row.rate.low) | w }}%"></div>
    <div class="pt" style="left: {{ row.rate.p | w }}%"></div></div>
    <span class="muted">{{ row.rate.low | pct }} to {{ row.rate.high | pct }}</span></td>
  <td>{% if row.stability %}
    {% if row.stability.flaky_cases %}<span class="pill warn">{{ row.stability.flaky_cases }} of
      {{ row.stability.judged_cases }} unstable</span>
    {% else %}<span class="pill ok">consistent</span>{% endif %}
    {% else %}<span class="muted">single repeat</span>{% endif %}</td>
</tr>
{% endfor %}
</tbody>
</table>
</div>

<h2>By language</h2>
<div class="card scroll">
<table>
<thead><tr><th>Category</th>{% for lang in languages %}<th class="num">{{ lang | upper }}</th>{% endfor %}
<th>Is the gap conclusive?</th></tr></thead>
<tbody>
{% for row in d.rows %}
<tr><td>{{ row.label }}</td>
{% for lang in languages %}
  <td class="num">{% if lang in row.by_language %}{% set r = row.by_language[lang] %}
    {{ r.k }} / {{ r.n }}<br><span class="muted">{{ r.p | pct }}</span>{% else %}&ndash;{% endif %}</td>
{% endfor %}
<td>{% if row.language_gap is none %}<span class="muted">n/a</span>
  {% elif row.language_gap %}<span class="pill warn">yes, intervals do not overlap</span>
  {% else %}<span class="muted">no, intervals overlap: differences may be chance</span>{% endif %}</td>
</tr>
{% endfor %}
</tbody>
</table>
<p class="muted" style="margin-top:10px">Language of the replies matched the language of the question in
{{ d.language_matches }} of {{ d.language_known }} replies where the language could be told
({{ d.language_unknown }} too short to tell). Information only, not a verdict.</p>
</div>

<h2>Findings and their proof</h2>
{% for f in d.findings %}
<div class="card">
<p><strong>{{ f.label }}.</strong> {{ f.statement }}</p>
<details class="proof"{% if f.evidence | length <= 4 %} open{% endif %}>
<summary class="proof-toggle">Show the proof ({{ f.evidence | length }}
  {{ "piece" if f.evidence | length == 1 else "pieces" }})</summary>
{% for e in f.evidence %}
<details>
<summary><span class="id">{{ e.attempt_id }}</span> &middot; {{ e.language }} &middot; {{ e.check }}
  &middot; <span class="excerpt">{{ e.excerpt }}</span></summary>
<p class="muted">{{ e.detail }}</p>
<div class="k">Question</div><pre>{{ e.prompt }}</pre>
<div class="k">Full reply of the model</div><pre>{{ e.reply }}</pre>
</details>
{% endfor %}
</details>
</div>
{% endfor %}
{% if not d.findings %}<div class="card"><p>No violations were found in this run.</p></div>{% endif %}

{% if d.clean %}
<div class="card">
<p><strong>Categories without violations.</strong></p>
<ul>
{% for label, rate in d.clean %}
<li>{{ label }}: no violations in {{ rate.n }} attempts. The true rate could still be up to
  {{ rate.high | pct }} (upper end of the 95% interval). This is not proof of safety.</li>
{% endfor %}
</ul>
</div>
{% endif %}

<h2>Limitations</h2>
<div class="card">
<ul>
<li>Checks are simple rules, not AI judges. They can miss a leak or raise a false alarm. That is why
every verdict keeps the proof, so a person can review it.</li>
<li>One model, one system prompt, synthetic data. Results describe this setup only.</li>
<li>The model answers a little differently each time, so repeat runs will not match exactly.
Wide intervals mean the data is too thin to conclude.</li>
<li>Prompts were drafted with AI assistance; the Romanian versions have not been reviewed by a
native speaker.</li>
</ul>
</div>

<h2>Experiment snapshot</h2>
<div class="card scroll">
<table class="snap">
<tr><td>Model</td><td>{{ d.snapshot.model }}</td></tr>
<tr><td>Temperature</td><td>{{ d.snapshot.temperature }}</td></tr>
<tr><td>Max reply length (tokens)</td><td>{{ d.snapshot.num_predict }}</td></tr>
<tr><td>Repeats</td><td>{{ d.snapshot.repeats }}</td></tr>
<tr><td>System prompt fingerprint</td><td><code>{{ d.snapshot.system_prompt_sha256[:16] }}</code></td></tr>
<tr><td>Scenario file fingerprint</td><td><code>{{ d.snapshot.scenarios_sha256[:16] }}</code></td></tr>
<tr><td>Vespera / Python</td><td>{{ d.snapshot.vespera_version }} / {{ d.snapshot.python_version }}</td></tr>
<tr><td>Created</td><td>{{ d.snapshot.created_at }}</td></tr>
</table>
</div>

<footer>Generated by Vespera {{ d.snapshot.vespera_version }}. To reproduce:
<code>vespera run --model {{ d.snapshot.model }} --repeats {{ d.snapshot.repeats }}</code> then
<code>vespera check</code> and <code>vespera report</code>.</footer>
</main>
</body>
</html>
"""