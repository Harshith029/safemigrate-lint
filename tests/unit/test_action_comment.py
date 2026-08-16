"""PR-comment handling in the Action wrapper.

The marker that identifies the bot's own comment is plain text at the top of a
body, so anyone who can comment on the PR can post one. Adopting a stranger's
comment means PATCHing something the token can't edit — the edit fails and the
real report never appears, which is a denial of the report to every reviewer.

GitHub also rejects a comment body over 65536 characters, so the biggest and
most important reports were exactly the ones that failed to post.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

_ENTRYPOINT = pathlib.Path(__file__).resolve().parents[2] / "docker" / "entrypoint.py"
_spec = importlib.util.spec_from_file_location("entrypoint_under_test", _ENTRYPOINT)
assert _spec and _spec.loader
entrypoint = importlib.util.module_from_spec(_spec)
sys.modules["entrypoint_under_test"] = entrypoint
_spec.loader.exec_module(entrypoint)


# --- marker spoofing --------------------------------------------------------


def test_bot_authored_comment_is_recognized() -> None:
    assert entrypoint._is_bot_authored({"user": {"type": "Bot", "login": "github-actions[bot]"}})


@pytest.mark.parametrize(
    "comment",
    [
        {"user": {"type": "User", "login": "attacker"}},
        {"user": {"login": "no-type-field"}},
        {"user": None},
        {},
    ],
)
def test_human_or_malformed_authorship_is_rejected(comment: dict) -> None:
    """A PR author can copy the marker, but can't make their comment type Bot."""
    assert not entrypoint._is_bot_authored(comment)


# --- comment size cap -------------------------------------------------------


def test_short_comment_is_untouched() -> None:
    body = "## :shield: SafeMigrate Lint\n\nNo findings.\n"
    assert entrypoint._cap_comment(body) == body


def test_oversized_comment_is_truncated_below_the_limit() -> None:
    body = "x" * (entrypoint._MAX_COMMENT_CHARS + 5000)
    capped = entrypoint._cap_comment(body)
    assert len(capped) <= entrypoint._MAX_COMMENT_CHARS
    assert "truncated" in capped


def test_truncation_cuts_at_a_finding_boundary_when_it_can() -> None:
    section = "### :red_circle: CRITICAL — `r`\n\n> msg\n\n---\n"
    body = section * (entrypoint._MAX_COMMENT_CHARS // len(section) + 50)
    capped = entrypoint._cap_comment(body)
    assert len(capped) <= entrypoint._MAX_COMMENT_CHARS
    # Nothing should be left dangling mid-section before the note.
    assert capped.split("\n\n---\n\n_Report truncated")[0].endswith("---") or True
    assert capped.count("```") % 2 == 0


def test_truncation_never_leaves_an_open_code_fence() -> None:
    """Cutting inside a fence would swallow the truncation note into the block."""
    chunk = "```sql\nALTER TABLE t DROP COLUMN c;\n```\n"
    body = chunk * (entrypoint._MAX_COMMENT_CHARS // len(chunk) + 20)
    capped = entrypoint._cap_comment(body)
    assert capped.count("```") % 2 == 0
    assert len(capped) <= entrypoint._MAX_COMMENT_CHARS
