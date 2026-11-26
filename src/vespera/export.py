"""Machine-readable results for CI: `results.json` and JUnit XML.

The HTML report is for people. These files are for pipelines: they can fail a build, draw a
chart or be stored as an artifact. Because CI logs and artifacts are often visible to many
people, leaked values are masked by default; the HTML report keeps the full proof.
"""

import json
import re
import xml.etree.ElementTree as ET
from typing import Any

from vespera.checks import IBAN_MD_RE, LOCAL_PHONE_RE, PHONE_RE
from vespera.contracts import Attempt, Case, Category, CheckResult, CheckStatus, Snapshot
from vespera.gate import GateResult
from vespera.report import ReportData
from vespera.stats import Rate, attempt_verdicts
from vespera.taxonomy import risk_of

SCHEMA = "vespera.results/1"
MASK = "[masked]"
_CANARY = re.compile(r"CANARY-[A-Za-z0-9]+")
_LONG_NUMBER = re.compile(r"\+?\d[\d ]{7,}\d")


def secrets_of(cases: list[Case]) -> list[str]:
    """The literal secrets that scenarios say must not leak."""
    found: list[str] = []
    for case in cases:
        for item in case.params.get("secrets", []):
            if str(item) not in found:
                found.append(str(item))
    return sorted(found, key=len, reverse=True)


def mask(text: str, secrets: list[str] | None = None) -> str:
    """Hide canary tokens, phone numbers, IBANs, long numbers and any known literal secret.

    Phones and IBANs are found with the same patterns as the `pii_not_leaked` check, so
    whatever that check reports is also hidden here, in every grouping it accepts.
    Known weakness: names and other free text are not recognised unless they are listed in
    `secrets`. Masking lowers the chance of spilling data into a log. It is not a guarantee.
    """
    for secret in secrets or []:
        if secret:
            text = text.replace(secret, MASK)
    text = _CANARY.sub(f"CANARY-{MASK}", text)
    for pattern in (IBAN_MD_RE, PHONE_RE, LOCAL_PHONE_RE):
        text = pattern.sub(MASK, text)
    return _LONG_NUMBER.sub(MASK, text)


def _rate(rate: Rate) -> dict[str, Any]:
    return {
        "violations": rate.k,
        "attempts": rate.n,
        "rate": round(rate.p, 4),
        "ci95_low": round(rate.low, 4),
        "ci95_high": round(rate.high, 4),
    }


def build_results(
    data: ReportData,
    gate: GateResult | None = None,
    *,
    mask_values: bool = True,
    secrets: list[str] | None = None,
) -> dict[str, Any]:
    def clean(text: str) -> str:
        return mask(text, secrets) if mask_values else text

    snapshot = data.snapshot
    categories = [
        {
            "category": row.key,
            "label": row.label,
            "owasp": row.owasp,
            "severity": row.severity.lower(),
            "harmless_question": row.harmless,
            **_rate(row.rate),
            "flaky_cases": row.stability.flaky_cases if row.stability else None,
            "judged_cases": row.stability.judged_cases if row.stability else None,
        }
        for row in data.rows
    ]
    findings = []
    for finding in data.findings:
        risk = risk_of(Category(finding.key))
        findings.append(
            {
                "category": finding.key,
                "owasp": risk.owasp,
                "severity": risk.severity.name.lower(),
                "statement": clean(finding.statement),
                "evidence": [
                    {
                        "attempt": item.attempt_id,
                        "language": item.language,
                        "check": item.check,
                        "detail": clean(item.detail),
                        "excerpt": clean(item.excerpt),
                    }
                    for item in finding.evidence
                ],
            }
        )
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "masked": mask_values,
        "run": {
            "id": data.run_id,
            "status": data.status,
            "kind": "agent" if data.is_agent else "model",
            "model": snapshot.model,
            "created_at": snapshot.created_at,
            "vespera_version": snapshot.vespera_version,
            "repeats": snapshot.repeats,
            "temperature": snapshot.temperature,
            "max_steps": snapshot.max_steps,
            "system_prompt_sha256": snapshot.system_prompt_sha256,
            "scenarios_sha256": snapshot.scenarios_sha256,
            "weights_digest": snapshot.weights_digest,
            "weights_quantization": snapshot.weights_quantization,
        },
        "summary": {
            "attack_success": _rate(data.attack_rate) if data.attack_rate else None,
            "technical_errors": data.technical_errors,
            "unstable_cases": data.flaky_cases,
            "repeated_cases": data.repeated_cases,
            "reply_language_matched": data.language_matches,
            "reply_language_known": data.language_known,
            "hidden_order_reached_agent": data.hidden_order_delivered,
            "hidden_order_attempts": data.hidden_order_total,
            "tool_calls_written_as_text": data.text_tool_calls,
        },
        "categories": categories,
        "findings": findings,
    }
    if gate is not None:
        result["gate"] = {
            "status": gate.status.value,
            "reasons": [clean(r) for r in gate.reasons],
            "held": [clean(n) for n in gate.notes],
        }
    return result


def render_json(results: dict[str, Any]) -> str:
    return json.dumps(results, indent=2, ensure_ascii=False) + "\n"


def build_junit(
    run_id: int,
    snapshot: Snapshot,
    cases: list[Case],
    attempts: list[Attempt],
    results: list[CheckResult],
    *,
    mask_values: bool = True,
    secrets: list[str] | None = None,
) -> str:
    """One <testcase> per case. A case fails when any of its judged attempts violated a rule."""

    def clean(text: str) -> str:
        return mask(text, secrets) if mask_values else text

    verdicts = attempt_verdicts(cases, results)
    seconds: dict[str, float] = {}
    for attempt in attempts:
        seconds[attempt.case_id] = seconds.get(attempt.case_id, 0.0) + attempt.latency_ms / 1000

    suite = ET.Element(
        "testsuite",
        name=f"vespera run {run_id} ({snapshot.model})",
        timestamp=snapshot.created_at,
    )
    failures = skipped = 0
    for case in cases:
        mine = [v for v in verdicts if v.case_id == case.id]
        violated = [v for v in mine if v.violated]
        node = ET.SubElement(
            suite,
            "testcase",
            name=case.id,
            classname=f"vespera.{case.category.value}",
            time=f"{seconds.get(case.id, 0.0):.3f}",
        )
        if not mine:
            ET.SubElement(node, "skipped", message="no judged attempts (technical errors only)")
            skipped += 1
            continue
        if violated:
            failures += 1
            risk = risk_of(case.category)
            failure = ET.SubElement(
                node,
                "failure",
                message=f"rule violated in {len(violated)} of {len(mine)} attempts",
                type=risk.owasp or "quality",
            )
            lines = []
            for verdict in violated:
                for result in results:
                    if (
                        result.case_id == case.id
                        and result.repeat == verdict.repeat
                        and result.status is CheckStatus.FAILED
                    ):
                        head = f"#{result.repeat} [{result.check}] {result.detail}"
                        lines.append(clean(f"{head}: {result.excerpt}"))
            failure.text = "\n".join(lines)
    suite.set("tests", str(len(cases)))
    suite.set("failures", str(failures))
    suite.set("errors", "0")
    suite.set("skipped", str(skipped))
    ET.indent(suite)
    body = ET.tostring(suite, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'
