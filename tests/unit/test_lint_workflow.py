from __future__ import annotations

import re
from pathlib import Path


def test_lint_workflow_allows_findings_without_failing_job() -> None:
    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "lint-migrations.yml"
    )
    text = workflow.read_text(encoding="utf-8")

    pattern = re.compile(
        r"- name: Lint migration SQL\s+"
        r"(?:\s{8}.+\n)*?"
        r"\s{8}continue-on-error: true",
        re.MULTILINE,
    )
    assert pattern.search(text), (
        "The lint workflow must set continue-on-error for the migration lint step "
        "so findings do not fail the Actions job."
    )
