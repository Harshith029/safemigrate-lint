"""Output reporters.

JSON is the v1 default — structured for CI integration and easy diffing against
squawk's `--reporter=json`. Markdown is used by the GitHub Action wrapper (week
4 work) to render PR comments.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from .finding import Finding, Severity

_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.STYLE: 2}
# GitHub markdown emoji shortcodes — plain ASCII (Windows-terminal safe) and
# rendered as proper emoji in GitHub PR comments.
_SEVERITY_BADGE = {
    Severity.CRITICAL: ":red_circle: CRITICAL",
    Severity.WARNING: ":yellow_circle: WARNING",
    Severity.STYLE: ":large_blue_circle: STYLE",
}


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
        lines.append(f"> {f.message}")
        lines.append("")
        # Code line from source, if available
        code_line = _extract_line(source_by_file.get(f.file, ""), f.line)
        if code_line:
            lines.append("```sql")
            lines.append(code_line)
            lines.append("```")
            lines.append("")
        if f.help:
            lines.append(f.help)
            lines.append("")
        if f.suggested_fix:
            lines.append("**Suggested fix:**")
            lines.append("")
            lines.append("```sql")
            lines.append(f.suggested_fix.strip())
            lines.append("```")
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
