"""The Action wrapper: inputs, exit status, and what happens when GitHub says no.

A pull request from a fork runs with a read-only token, so the PR comment and
the Check Run are refused (403). That used to surface only as a stderr line,
with the README's `continue-on-error` making the job look green — while a
required `safemigrate-lint` check simply never arrived. These tests pin the
read-only path that does work there (exit status via `fail-on`, the job
summary), the messages for each refusal, and that API failures never change the
exit status. GitHub is faked at `_api_request`; nothing goes over the network.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import sys

import pytest

from safemigrate_lint.core.finding import Finding, Severity
from safemigrate_lint.core.reporter import render_markdown

_ENTRYPOINT = pathlib.Path(__file__).resolve().parents[2] / "docker" / "entrypoint.py"
_spec = importlib.util.spec_from_file_location("entrypoint_api_under_test", _ENTRYPOINT)
assert _spec and _spec.loader
entrypoint = importlib.util.module_from_spec(_spec)
sys.modules["entrypoint_api_under_test"] = entrypoint
_spec.loader.exec_module(entrypoint)

_GITHUB_ENV = (
    "GITHUB_ACTIONS",
    "GITHUB_EVENT_NAME",
    "GITHUB_EVENT_PATH",
    "GITHUB_OUTPUT",
    "GITHUB_REPOSITORY",
    "GITHUB_STEP_SUMMARY",
    "GITHUB_TOKEN",
)
WARNING_ONLY = "CREATE INDEX idx ON t (c);\n"
CRITICAL = "ALTER TABLE t ADD COLUMN c uuid DEFAULT gen_random_uuid();\n"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI runs these inside GitHub Actions; its real variables must not leak in
    (GITHUB_STEP_SUMMARY would point at the CI job's own summary)."""
    for name in _GITHUB_ENV:
        monkeypatch.delenv(name, raising=False)
    for name in ("INPUT_PATHS", "INPUT_SEVERITY", "INPUT_FORMAT", "INPUT_FAIL-ON"):
        monkeypatch.delenv(name, raising=False)


# --- paths -------------------------------------------------------------------------


def _paths(value: str) -> list[pathlib.Path]:
    return [pathlib.Path(p) for p in entrypoint._expand_paths(value)]


def test_a_file_name_with_spaces_works_on_its_own_line(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "my migration.sql").write_text("SELECT 1;")
    (tmp_path / "b.sql").write_text("SELECT 1;")
    assert _paths("my migration.sql\nb.sql\n") == [
        pathlib.Path("my migration.sql"),
        pathlib.Path("b.sql"),
    ]


def test_a_glob_with_spaces_matches_as_one_pattern(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "001 add users.sql").write_text("SELECT 1;")
    assert _paths("migrations/001 *.sql") == [pathlib.Path("migrations/001 add users.sql")]


def test_a_single_line_list_still_splits_on_whitespace(tmp_path, monkeypatch) -> None:
    """The README's changed-files recipe joins names with spaces."""
    monkeypatch.chdir(tmp_path)
    for name in ("a.sql", "b.sql"):
        (tmp_path / name).write_text("SELECT 1;")
    assert _paths("a.sql b.sql") == [pathlib.Path("a.sql"), pathlib.Path("b.sql")]
    assert _paths("*.sql") == [pathlib.Path("a.sql"), pathlib.Path("b.sql")]


def test_literals_pass_through_and_duplicates_collapse(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.sql").write_text("SELECT 1;")
    # A typo reaches the CLI, whose "file not found" is the actionable message;
    # a glob that matches nothing just drops out.
    assert _paths("missing.sql\na.sql\na.sql\nnone/*.sql") == [
        pathlib.Path("missing.sql"),
        pathlib.Path("a.sql"),
    ]


# --- fail-on ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fail_on", "count", "has_critical", "expected"),
    [
        ("any", 0, False, 0),
        ("any", 2, False, 1),
        ("any", 1, True, 1),
        ("critical", 0, False, 0),
        ("critical", 2, False, 0),
        ("critical", 1, True, 1),
        ("never", 3, True, 0),
    ],
)
def test_exit_code(fail_on: str, count: int, has_critical: bool, expected: int) -> None:
    assert entrypoint._exit_code(fail_on, count, has_critical) == expected


@pytest.fixture
def action(tmp_path, monkeypatch):
    """Run main() as GitHub would, against the real CLI."""
    if not shutil.which(entrypoint.CLI_NAME):
        pytest.skip("safemigrate-lint console script is not on PATH")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "warn.sql").write_text(WARNING_ONLY)
    (tmp_path / "crit.sql").write_text(CRITICAL)
    output = tmp_path / "github_output"
    summary = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    def run(paths: str, fail_on: str | None = None) -> int:
        monkeypatch.setenv("INPUT_PATHS", paths)
        if fail_on is not None:
            monkeypatch.setenv("INPUT_FAIL-ON", fail_on)
        return entrypoint.main()

    run.output = output  # type: ignore[attr-defined]
    run.summary = summary  # type: ignore[attr-defined]
    return run


def test_default_fails_on_any_finding_like_the_cli(action) -> None:
    assert action("warn.sql") == 1


def test_fail_on_critical_passes_a_warning_only_migration(action) -> None:
    assert action("warn.sql", "critical") == 0
    outputs = action.output.read_text(encoding="utf-8")
    assert "has-critical=false" in outputs
    assert "findings-count=0" not in outputs  # the warning is still reported


def test_fail_on_critical_fails_on_a_critical_finding(action) -> None:
    assert action("warn.sql\ncrit.sql", "critical") == 1


def test_fail_on_never_always_passes(action) -> None:
    assert action("crit.sql", "never") == 0


def test_an_unknown_fail_on_is_an_input_error(action, capsys) -> None:
    assert action("warn.sql", "sometimes") == 2
    assert "invalid fail-on" in capsys.readouterr().err


def test_the_job_summary_carries_the_report(action) -> None:
    action("crit.sql")
    summary = action.summary.read_text(encoding="utf-8")
    assert "SafeMigrate Lint" in summary
    assert "volatile-default-rewrites-table" in summary


# --- pull request context --------------------------------------------------------------


def _pr_env(tmp_path, monkeypatch, *, head_repo: str | None, base_repo: str = "owner/repo") -> None:
    event = {
        "pull_request": {
            "number": 7,
            "head": {"sha": "a" * 40, "repo": {"full_name": head_repo} if head_repo else None},
            "base": {"repo": {"full_name": base_repo}},
        }
    }
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    monkeypatch.setenv("GITHUB_REPOSITORY", base_repo)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")


@pytest.mark.parametrize(
    ("head_repo", "from_fork"),
    [("owner/repo", False), ("contributor/repo", True), (None, True)],
    ids=["same-repo", "fork", "deleted-fork"],
)
def test_fork_pull_requests_are_detected(tmp_path, monkeypatch, head_repo, from_fork) -> None:
    _pr_env(tmp_path, monkeypatch, head_repo=head_repo)
    ctx = entrypoint._pr_context()
    assert ctx is not None and ctx["from_fork"] is from_fork


class FakeGitHub:
    """Scripted stand-in for `_api_request`: `responses[method]` is returned."""

    def __init__(self, **responses: tuple[int, object, str]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, url: str, token: str, body: dict | None = None):
        self.calls.append((method, url))
        return self.responses.get(method, (200, [], "[]"))


def _ctx(from_fork: bool) -> dict:
    return {
        "token": "t",
        "owner": "owner",
        "repo": "repo",
        "pr_number": 7,
        "head_sha": "a" * 40,
        "from_fork": from_fork,
    }


def _comment_and_check(monkeypatch, fake: FakeGitHub, *, from_fork: bool) -> None:
    monkeypatch.setattr(entrypoint, "_api_request", fake)
    ctx = _ctx(from_fork)
    entrypoint._post_or_edit_comment(ctx, "## report\n")
    entrypoint._create_check_run(ctx, 1, True, [{"severity": "critical"}])


def test_success_posts_the_comment_and_the_check_run(monkeypatch, capsys) -> None:
    fake = FakeGitHub(GET=(200, [], "[]"), POST=(201, {"id": 42}, "{}"))
    _comment_and_check(monkeypatch, fake, from_fork=False)
    assert [m for m, _ in fake.calls] == ["GET", "POST", "POST"]
    assert fake.calls[1][1].endswith("/issues/7/comments")
    assert fake.calls[2][1].endswith("/check-runs")
    err = capsys.readouterr().err
    assert "posted new comment #42" in err
    assert "created #42 (conclusion=action_required)" in err


def test_fork_refusals_explain_the_read_only_token(monkeypatch, capsys) -> None:
    fake = FakeGitHub(POST=(403, None, '{"message": "Resource not accessible by integration"}'))
    _comment_and_check(monkeypatch, fake, from_fork=True)
    warnings = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("::warning")]
    assert len(warnings) == 2  # one for the comment, one for the Check Run
    assert all("comes from a fork" in w and "fail-on" in w for w in warnings)
    assert "Check Run" in warnings[1]


def test_same_repo_refusals_name_the_missing_permission(monkeypatch, capsys) -> None:
    fake = FakeGitHub(POST=(403, None, "{}"))
    _comment_and_check(monkeypatch, fake, from_fork=False)
    err = capsys.readouterr().err
    assert "pull-requests: write" in err
    assert "checks: write" in err


def test_network_errors_are_logged_not_raised(monkeypatch, capsys) -> None:
    fake = FakeGitHub(GET=(0, None, "network error: boom"), POST=(0, None, "network error: boom"))
    _comment_and_check(monkeypatch, fake, from_fork=False)
    assert "returned 0" in capsys.readouterr().err


def test_api_failures_never_change_the_exit_status(action, tmp_path, monkeypatch) -> None:
    _pr_env(tmp_path, monkeypatch, head_repo="contributor/repo")
    fake = FakeGitHub(POST=(403, None, "{}"))
    monkeypatch.setattr(entrypoint, "_api_request", fake)
    assert action("warn.sql", "critical") == 0
    assert action("crit.sql", "critical") == 1
    assert any(m == "POST" for m, _ in fake.calls)  # it did try


# --- report output ---------------------------------------------------------------------


def test_the_report_pauses_workflow_commands_inside_actions(monkeypatch, capsys) -> None:
    """A migration line starting with `::` must not reach the runner as a command."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    entrypoint._print_report("```sql\n::error::planted by a migration\n```\n")
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("::stop-commands::")
    token = lines[0].removeprefix("::stop-commands::")
    assert len(token) >= 32
    assert lines[-1] == f"::{token}::"
    assert "::error::planted by a migration" in lines[1:-1]


def test_each_report_uses_a_fresh_token(monkeypatch, capsys) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    entrypoint._print_report("a\n")
    entrypoint._print_report("b\n")
    starts = [
        ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("::stop-commands::")
    ]
    assert len(set(starts)) == 2


def test_the_report_is_plain_outside_actions(capsys) -> None:
    entrypoint._print_report("plain\n")
    assert capsys.readouterr().out == "plain\n"


def test_a_file_name_cannot_break_out_of_the_location_span() -> None:
    """File names come from the pull request, like the SQL."""
    finding = Finding(
        rule_id="drop-column-restricted",
        severity=Severity.CRITICAL,
        file="migrations/a`b\n# pwned.sql",
        line=3,
        column=1,
        message="m",
    )
    md = render_markdown([finding], {})
    (location,) = [ln for ln in md.splitlines() if ":3:1" in ln]
    assert location == "``a`b # pwned.sql:3:1``"
    assert not any(ln.startswith("# pwned") for ln in md.splitlines())
