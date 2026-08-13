"""Output reporters.

JSON is the v1 default — structured for CI integration and easy diffing against
squawk's `--reporter=json`. Markdown is used by the GitHub Action wrapper to
render PR comments.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from .finding import Finding, Severity

# Markdown characters that create structure or links. Table identifiers and
# literals reach the report straight out of the migration, so without this a PR
# author could inject headings, links, or images into a comment the Action posts
# under the repo bot's identity. `_` is deliberately absent: intraword
# underscores don't start emphasis, and escaping them would mangle every
# ordinary SQL identifier (event_type_id -> event\_type\_id).
_MD_SPECIAL = re.compile(r"[\\`\[\]<>|#!*]")
_BACKTICK_RUN = re.compile(r"`+")

_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.STYLE: 2}
# GitHub markdown emoji shortcodes — plain ASCII (Windows-terminal safe) and
# rendered as proper emoji in GitHub PR comments.
_SEVERITY_BADGE = {
    Severity.CRITICAL: ":red_circle: CRITICAL",
    Severity.WARNING: ":yellow_circle: WARNING",
    Severity.STYLE: ":large_blue_circle: STYLE",
}
# Postgres lock modes in strength order (weakest -> strongest), per the
# lock-conflict table in the Postgres docs. Used to pick the migration's worst
# lock; an unknown mode sorts below all of them.
_LOCK_RANK = {
    "ACCESS SHARE": 1,
    "ROW SHARE": 2,
    "ROW EXCLUSIVE": 3,
    "SHARE UPDATE EXCLUSIVE": 4,
    "SHARE": 5,
    "SHARE ROW EXCLUSIVE": 6,
    "EXCLUSIVE": 7,
    "ACCESS EXCLUSIVE": 8,
}


def _one_line(text: str) -> str:
    """Collapse whitespace so interpolated SQL can't break out of a construct.

    A newline inside a finding's text would otherwise end the blockquote (or the
    list item) and let everything after it render as top-level Markdown.
    """
    return " ".join(text.split())


def _md_escape(text: str) -> str:
    """Render SQL-derived text as literal prose: single line, no Markdown structure."""
    return _MD_SPECIAL.sub(lambda m: "\\" + m.group(0), _one_line(text))


def _fenced(content: str, lang: str = "sql") -> list[str]:
    """Fence a block with more backticks than the content's longest run.

    A migration line containing ``` would otherwise close the fence early and
    let the rest of the statement render as Markdown.
    """
    longest = max((len(m.group(0)) for m in _BACKTICK_RUN.finditer(content)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}{lang}", content, fence]


def render_json(findings: Iterable[Finding]) -> str:
    """Serialize findings as a JSON array, sorted for stable output."""
    items = sorted(
        (f.to_dict() for f in findings),
        key=lambda d: (d["file"], d["line"], d["column"], d["rule_id"]),
    )
    return json.dumps(items, indent=2, default=str)


def render_markdown(findings: Iterable[Finding], source_by_file: dict[str, str]) -> str:
    """Render findings as markdown suitable for a GitHub PR comment.

    `source_by_file` maps file paths to the SQL content; used to render the
    code line in a fenced block per finding. If a file's source isn't
    provided, the code line is omitted.
    """
    findings_list = sorted(
        findings,
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 99), f.file, f.line, f.column, f.rule_id),
    )

    if not findings_list:
        return "## :shield: SafeMigrate Lint\n\nNo findings. :white_check_mark:\n"

    counts = Counter(f.severity for f in findings_list)
    summary_parts = []
    for sev in (Severity.CRITICAL, Severity.WARNING, Severity.STYLE):
        if counts.get(sev):
            summary_parts.append(f"{counts[sev]} {sev.value}")
    summary = ", ".join(summary_parts)

    lines: list[str] = []
    lines.append("## :shield: SafeMigrate Lint")
    lines.append("")
    lines.append(f"**{len(findings_list)} findings** — {summary}.")
    lines.append("")
    impacts = [f.lock_impact for f in findings_list if f.lock_impact is not None]
    if impacts:
        heaviest = max(impacts, key=lambda i: _LOCK_RANK.get(i.lock, 0))
        lines.append(f":lock: Heaviest lock: **{heaviest.lock}** — blocks {heaviest.blocks}.")
        lines.append("")
    lines.append("---")
    lines.append("")

    for f in findings_list:
        badge = _SEVERITY_BADGE.get(f.severity, f.severity.value.upper())
        lines.append(f"### {badge} — `{f.rule_id}`")
        lines.append("")
        # Location header
        file_display = Path(f.file).name if "/" in f.file or "\\" in f.file else f.file
        lines.append(f"`{file_display}:{f.line}:{f.column}`")
        lines.append("")
        lines.append(f"> {_md_escape(f.message)}")
        lines.append("")
        if f.lock_impact:
            li = f.lock_impact
            lines.append(f":lock: Lock: **{li.lock}** | held: {li.held} | blocks: {li.blocks}")
            if li.note:
                lines.append(f"_{li.note}_")
            lines.append("")
        # Code line from source, if available
        code_line = _extract_line(source_by_file.get(f.file, ""), f.line)
        if code_line:
            lines.extend(_fenced(code_line))
            lines.append("")
        if f.help:
            # Rule-authored prose, so its own inline code stays intact; only the
            # newlines are collapsed, since a value interpolated into help could
            # otherwise carry one and break the layout.
            lines.append(_one_line(f.help))
            lines.append("")
        if f.suggested_fix:
            lines.append("**Suggested fix:**")
            lines.append("")
            lines.extend(_fenced(f.suggested_fix.strip()))
            lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _extract_line(sql: str, line_num: int) -> str:
    if not sql or line_num < 1:
        return ""
    all_lines = sql.splitlines()
    if line_num > len(all_lines):
        return ""
    return all_lines[line_num - 1].rstrip()
