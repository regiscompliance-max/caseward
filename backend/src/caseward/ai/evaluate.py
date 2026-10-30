"""Evaluation harness: score extraction against a labelled test set.

    python -m caseward.ai.evaluate path/to/labelled-set

Each case is a JSON file:
    {"certificate_type": "eicr", "pages": ["page 1 text", ...], "expected": {...fields...},
     "property_postcode": "SW1A 1AA"}

Robustness rule 3: no prompt or model change ships unless it scores at least
as well as the current version. The exit status is non-zero if any critical
field is below the 98% gate.
"""

import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from caseward.ai import extraction
from caseward.ai.provider import ModelProvider

GATE = 0.98


@dataclass
class Report:
    cases: int = 0
    correct: Counter = field(default_factory=Counter)
    failures: list[tuple[str, str, object, object]] = field(default_factory=list)

    def accuracy(self) -> dict[str, float]:
        return {f: self.correct[f] / self.cases for f in extraction.CRITICAL_FIELDS} if self.cases else {}

    def passes(self) -> bool:
        return bool(self.cases) and all(a >= GATE for a in self.accuracy().values())


def _norm(name: str, value):
    if value is None:
        return None
    if name == "defects":
        return sorted((d["code"], " ".join(d["description"].lower().split())) for d in value)
    if name == "postcode":
        return "".join(value.split()).upper()
    if isinstance(value, str):
        return " ".join(value.lower().split())
    return value


def evaluate(provider: ModelProvider, cases: list[tuple[str, dict]], today: date | None = None) -> Report:
    report = Report()
    for name, case in cases:
        result = extraction.extract(provider, case["certificate_type"], case["certificate_type"], case["pages"],
                                    today=today or date.today(), property_postcode=case.get("property_postcode"))
        report.cases += 1
        for f in extraction.CRITICAL_FIELDS:
            expected, actual = _norm(f, case["expected"].get(f)), _norm(f, result.result.get(f))
            if expected == actual:
                report.correct[f] += 1
            else:
                report.failures.append((name, f, case["expected"].get(f), result.result.get(f)))
    return report


def main(argv: list[str]) -> int:
    from caseward.services import ai_from_env
    provider = ai_from_env()
    if provider is None:
        print("No AI provider configured (see README).", file=sys.stderr)
        return 2
    cases = [(p.name, json.loads(p.read_text())) for p in sorted(Path(argv[0]).glob("*.json"))]
    report = evaluate(provider, cases)
    print(f"{report.cases} cases, prompt {extraction.PROMPT_ID} v{extraction.PROMPT_VERSION}")
    for f, a in report.accuracy().items():
        print(f"  {f:22} {a:7.2%} {'ok' if a >= GATE else 'BELOW GATE'}")
    for name, f, expected, actual in report.failures[:50]:
        print(f"  ✗ {name} {f}: expected {expected!r}, got {actual!r}")
    return 0 if report.passes() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
