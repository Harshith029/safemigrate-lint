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
        (
            i
            for i, line in enumerate(lines)
            if line.lstrip().startswith("- name:") and "Lint migration SQL" in line
        ),
        None,
    )
    assert start is not None, "Expected 'Lint migration SQL' step was not found in workflow."

    indent_level = len(lines[start]) - len(lines[start].lstrip())
    step_lines: list[str] = []
    for line in lines[start + 1 :]:
        sibling_step = (
            line.lstrip().startswith("- name:")
            and (len(line) - len(line.lstrip()) == indent_level)
        )
        if sibling_step:
            break
        step_lines.append(line)

    def _is_truthy_continue_on_error(line: str) -> bool:
        stripped = line.strip()
        if not stripped.startswith("continue-on-error:"):
            return False
        value = stripped.split(":", 1)[1].strip().strip("'\"").lower()
        return value in {"true", "yes", "on"}

    assert any(_is_truthy_continue_on_error(line) for line in step_lines), (
        "The lint workflow must set continue-on-error for the migration lint step "
        "so findings do not fail the Actions job."
    )
