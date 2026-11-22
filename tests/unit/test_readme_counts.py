"""The README's rule counts must match the rule registry.

The README says how many rules ship in each tier. Those numbers are typed by
hand, so adding a rule, removing one or moving one to another tier left them
wrong without failing anything. This counts the registry by severity and
checks every count the README states against it.
"""

from __future__ import annotations

import pathlib
import re
from collections import Counter

import pytest

from safemigrate_lint.core.finding import Severity
from safemigrate_lint.rules import RULES

_README = pathlib.Path(__file__).resolve().parents[2] / "README.md"


@pytest.mark.parametrize(
    ("phrasing", "tiers"),
    [
        # "32 safety rules + 7 opt-in style rules", "32 safety + 7 opt-in style"
        (r"\b(\d+) safety\b", (Severity.CRITICAL, Severity.WARNING)),
        (r"\b(\d+) opt-in style\b", (Severity.STYLE,)),
        # "25 of the 39 rules carry a lock impact": a new total usually means the
        # lock-impact numbers in that sentence need checking too.
        (r"\bof the (\d+) rules\b", tuple(Severity)),
    ],
    ids=["safety", "style", "total"],
)
def test_readme_count_matches_the_registry(phrasing: str, tiers: tuple[Severity, ...]) -> None:
    by_tier = Counter(rule.severity for rule in RULES.values())
    registered = sum(by_tier[tier] for tier in tiers)
    stated = [int(n) for n in re.findall(phrasing, _README.read_text(encoding="utf-8"))]

    # A reworded README must not turn this into a test that checks nothing.
    assert stated, f"README no longer states a count matching {phrasing!r}; update this test"
    assert stated == [registered] * len(stated), (
        f"README states {stated} for {phrasing!r}, but the registry has {registered} "
        f"({by_tier[Severity.CRITICAL]} critical, {by_tier[Severity.WARNING]} warning, "
        f"{by_tier[Severity.STYLE]} style)"
    )
