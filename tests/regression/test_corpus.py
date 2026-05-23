"""Corpus regression test.

Runs the analyzer on every fixture in `fixtures/migrations/` and diffs the
default-mode output against committed golden JSON. Intentional analyzer
changes are blessed via `pytest --update-golden`; the resulting `git diff`
must be reviewed in PR.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Force registration of every rule. Importing this module triggers each rule's
# @register_rule decorator side-effect.
import safemigrate_lint.rules  # noqa: F401
from safemigrate_lint.core.engine import analyze
from safemigrate_lint.core.finding import DEFAULT_LEVELS
from safemigrate_lint.core.parser import parse_file
from safemigrate_lint.core.state import StateBuilder

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "fixtures" / "migrations"
GOLDEN_DIR = REPO_ROOT / "tests" / "regression" / "golden"

ALL_FIXTURES = sorted(FIXTURES_DIR.glob("*.sql"))


def _run_default_mode(fixture_path: Path) -> list[dict]:
    """Analyze one fixture; return findings in default-mode (CRITICAL+WARNING)
    severity tier, normalized so goldens are stable across platforms."""
    result = parse_file(fixture_path)
    state = StateBuilder.build(result.statements or [])
    findings = analyze(result, state)
    filtered = [f for f in findings if f.severity in DEFAULT_LEVELS]
    items = [f.to_dict() for f in filtered]
    # Normalize: replace the absolute file path with basename so goldens are
    # platform-stable (Windows backslash vs POSIX forward-slash).
    for it in items:
        it["file"] = fixture_path.name
    return sorted(
        items,
        key=lambda d: (d["file"], d["line"], d["column"], d["rule_id"]),
    )


@pytest.mark.parametrize("fixture_path", ALL_FIXTURES, ids=lambda p: p.name)
def test_corpus(fixture_path: Path, update_golden: bool) -> None:
    findings = _run_default_mode(fixture_path)
    golden_path = GOLDEN_DIR / f"{fixture_path.stem}.json"

    if update_golden:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
        return

    if not golden_path.exists():
        pytest.fail(
            f"missing golden file: {golden_path.relative_to(REPO_ROOT)}\n"
            f"run `pytest --update-golden` to bootstrap, then review the diff before commit"
        )

    expected = json.loads(golden_path.read_text(encoding="utf-8"))
    assert findings == expected, (
        f"\nregression in {fixture_path.name}:\n"
        f"expected (golden): {json.dumps(expected, indent=2)}\n"
        f"actual:            {json.dumps(findings, indent=2)}\n"
        f"if this change is intentional, run `pytest --update-golden` and commit the diff"
    )
