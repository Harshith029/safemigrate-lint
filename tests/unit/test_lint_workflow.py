from __future__ import annotations

from pathlib import Path


def test_lint_workflow_allows_findings_without_failing_job() -> None:
    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "lint-migrations.yml"
    )
    text = workflow.read_text(encoding="utf-8")

    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if "- name: Lint migration SQL" in line),
        None,
    )
    assert start is not None, "Expected 'Lint migration SQL' step was not found in workflow."

    indent_level = len(lines[start]) - len(lines[start].lstrip())
    step_prefix = " " * indent_level + "- name:"
    step_lines: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith(step_prefix):
            break
        step_lines.append(line)

    assert any(
        (len(line) - len(line.lstrip()) > indent_level)
        and line.lstrip() == "continue-on-error: true"
        for line in step_lines
    ), (
        "The lint workflow must set continue-on-error for the migration lint step "
        "so findings do not fail the Actions job."
    )
